"""Tables MCP Server - Main Entry Point"""

import shutil
import logging
from typing import Optional, Dict, Annotated
from pathlib import Path

import pandas as pd
import duckdb
from pydantic import Field

from mcp.server.fastmcp import FastMCP

from src.config import get_settings
from src.drive_client import DriveOperations
from src.auth import setup_google_drive_client, GoogleDriveAuthError


# Custom file handler that flushes immediately for real-time logging
class FlushFileHandler(logging.FileHandler):
    """Custom file handler that flushes after every log message"""
    def emit(self, record):
        super().emit(record)
        self.flush()


settings = get_settings()

# Setup logging based on configuration
if settings.enable_logging:
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        handlers=[
            FlushFileHandler('logs.txt', mode='a'),  # Append mode with auto-flush
        ],
        force=True  # Ensure this is the only logging configuration
    )
else:
    # Disable logging by setting to a high level
    logging.basicConfig(level=logging.CRITICAL, handlers=[logging.NullHandler()])

logger = logging.getLogger(__name__)

# Helper function to clear downloads folder
def clear_downloads_folder():
    """Clear all files in the downloads folder"""
    download_dir = settings.get_download_directory_path()
    logger.info(f"Clearing downloads folder: {download_dir}")
    if download_dir.exists():
        file_count = 0
        for item in download_dir.iterdir():
            try:
                if item.is_file():
                    item.unlink()
                    file_count += 1
                elif item.is_dir():
                    shutil.rmtree(item)
                    file_count += 1
            except Exception as e:
                logger.warning(f"Failed to delete {item}: {e}")
        logger.info(f"Cleared {file_count} items from downloads folder")
    else:
        download_dir.mkdir(parents=True, exist_ok=True)
        logger.info(f"Created downloads folder: {download_dir}")


# Clear downloads folder on module initialization
clear_downloads_folder()

# Create MCP server
mcp = FastMCP("tables-mcp-server")

# Global drive operations instance
drive_ops: Optional[DriveOperations] = None

# Global dictionary to store file data in memory
# Structure: file_id -> {"dataframes": {sheet_num: DataFrame}, "sheet_names": {sheet_num: name}, "empty_sheets": [name1, name2]}
loaded_files: Dict[str, Dict] = {}


def get_drive_operations() -> DriveOperations:
    """Get or initialize the Drive operations instance"""
    global drive_ops
    if drive_ops is None:
        try:
            logger.info("Initializing Google Drive client...")
            client = setup_google_drive_client(interactive=True)
            drive_ops = DriveOperations(client, settings)
            logger.info("Google Drive client initialized successfully")
        except GoogleDriveAuthError as e:
            logger.error(f"Google Drive authentication failed: {e}")
            raise
    return drive_ops


def load_dataframe_from_file(file_id: str) -> Dict:
    """Load a downloaded file into pandas DataFrames (one per sheet for Excel files).
    
    Args:
        file_id: The Google Drive file ID of the downloaded file
        
    Returns:
        Dictionary with structure:
        {
            "dataframes": {sheet_num: DataFrame, ...},
            "sheet_names": {sheet_num: name, ...},
            "empty_sheets": [name1, name2, ...]
        }
        
    Raises:
        Exception: If the file is not found or cannot be loaded
    """
    download_dir = settings.get_download_directory_path()
    matching_files = list(download_dir.glob(f"{file_id}.*"))
    
    logger.info(f"Loading file {file_id} from {download_dir}")
    
    if not matching_files:
        logger.error(f"File {file_id} not found locally in {download_dir}")
        raise Exception(f"File {file_id} not found locally. Call load_file() first to download it.")
    
    file_path = matching_files[0]
    extension = file_path.suffix.lower()
    logger.info(f"Found file: {file_path} (extension: {extension})")
    
    # Load based on file type
    try:
        if extension in ['.xlsx', '.xls']:
            # Load all sheets from Excel file
            logger.info(f"Loading Excel file: {file_path}")
            excel_file = pd.ExcelFile(file_path)
            sheet_names = excel_file.sheet_names
            logger.info(f"Found {len(sheet_names)} sheets: {sheet_names}")
            
            sheets_dict = {}
            sheet_names_dict = {}
            empty_sheets = []
            new_index = 0  # Sequential index for non-empty sheets
            
            for idx, sheet_name in enumerate(sheet_names):
                logger.info(f"Loading original sheet {idx}: {sheet_name}")
                df = pd.read_excel(excel_file, sheet_name=sheet_name)
                
                # Skip empty sheets (0 columns)
                if df.shape[1] == 0:
                    logger.warning(f"Skipping empty sheet {idx} '{sheet_name}' - 0 columns")
                    empty_sheets.append(sheet_name)
                    continue
                
                sheets_dict[new_index] = df
                sheet_names_dict[new_index] = sheet_name
                logger.info(f"Sheet {new_index} (original {idx}, '{sheet_name}'): {df.shape[0]} rows, {df.shape[1]} columns")
                new_index += 1
            
            if len(sheets_dict) == 0:
                logger.error(f"All sheets in file {file_id} are empty (0 columns)")
                raise Exception(f"All sheets in file {file_id} are empty (0 columns). Cannot load file.")
            
            logger.info(f"Successfully loaded {len(sheets_dict)} non-empty sheets from {file_id}")
            if empty_sheets:
                logger.info(f"Skipped {len(empty_sheets)} empty sheets: {empty_sheets}")
            
            return {
                "dataframes": sheets_dict,
                "sheet_names": sheet_names_dict,
                "empty_sheets": empty_sheets
            }
        elif extension == '.csv':
            # CSV files only have one "sheet" (sheet 0)
            logger.info(f"Loading CSV file: {file_path}")
            df = pd.read_csv(file_path)
            logger.info(f"CSV loaded: {df.shape[0]} rows, {df.shape[1]} columns")
            return {
                "dataframes": {0: df},
                "sheet_names": {0: file_path.stem},  # Use filename as sheet name
                "empty_sheets": []
            }
        else:
            logger.error(f"Unsupported file type '{extension}' for file {file_id}")
            raise Exception(f"Unsupported file type '{extension}'. Supported types: .xlsx, .xls, .csv")
        
    except Exception as e:
        logger.error(f"Failed to load file {file_id} as DataFrame: {e}")
        raise Exception(f"Failed to load file {file_id} as DataFrame: {e}")


# MCP Tool Definitions
@mcp.tool()
def load_file(file_id: Annotated[str, Field(description="The Google Drive file ID to download and load into memory")]) -> dict:
    """Download a file from Google Drive and load it into memory for analysis.
    
    Supports Excel files (.xlsx, .xls), CSV files (.csv), and Google Sheets.
    Must be called before any other operations on the file.
    """
    logger.info(f"load_file called for file_id: {file_id}")
    
    # Check if already loaded
    if file_id in loaded_files:
        logger.info(f"File {file_id} is already loaded in memory")
        return {
            "file_id": file_id,
            "status": "already loaded",
            "message": "File is already loaded and ready to use."
        }
    
    # Validate file exists on Google Drive
    try:
        logger.info(f"Checking if file {file_id} exists on Google Drive...")
        ops = get_drive_operations()
        exists, error_message = ops.check_file_exists(file_id)
        if not exists:
            logger.error(f"File {file_id} does not exist on Google Drive: {error_message}")
            raise Exception(f"{error_message}")
        logger.info(f"File {file_id} exists on Google Drive")
    except Exception as e:
        logger.error(f"Error checking file existence: {e}")
        raise
    
    # Download the file synchronously
    try:
        logger.info(f"Starting download for file {file_id}...")
        ops.download_file_by_id(file_id)
        logger.info(f"File {file_id} downloaded successfully")
        
        # Verify the file was actually downloaded
        download_dir = settings.get_download_directory_path()
        matching_files = list(download_dir.glob(f"{file_id}.*"))
        if matching_files:
            logger.info(f"Download verified: {matching_files[0]} (size: {matching_files[0].stat().st_size} bytes)")
        else:
            logger.warning(f"Download completed but file not found in {download_dir}")
        
        return {
            "file_id": file_id,
            "status": "success",
            "message": "File downloaded successfully and ready to use."
        }
    except Exception as e:
        logger.error(f"Failed to download file {file_id}: {e}")
        raise Exception(f"Failed to download file {file_id}: {e}")


@mcp.tool()
def unload_file(file_id: Annotated[str, Field(description="The Google Drive file ID to remove from local storage and memory")]) -> str:
    """Remove a file from local storage and free up memory.
    
    Does not affect the original file in Google Drive.
    """
    logger.info(f"unload_file called for file_id: {file_id}")
    download_dir = settings.get_download_directory_path()
    
    # Clear file data from memory
    if file_id in loaded_files:
        num_sheets = len(loaded_files[file_id]["dataframes"])
        del loaded_files[file_id]
        logger.info(f"Removed {num_sheets} sheet(s) for file {file_id} from memory")
    
    # Look for file with this ID (could have various extensions)
    matching_files = list(download_dir.glob(f"{file_id}.*"))
    
    if not matching_files:
        logger.info(f"No files found to delete for {file_id}")
        return f"file {file_id} was unloaded."
    
    for file_path in matching_files:
        logger.info(f"Deleting file: {file_path}")
        file_path.unlink()
    
    logger.info(f"File {file_id} unloaded successfully")
    return f"file {file_id} was unloaded."


@mcp.tool()
def info(file_id: Annotated[str, Field(description="The Google Drive file ID to retrieve information about")]) -> dict:
    """Get metadata and statistics about a loaded file.
    
    Returns shape (rows and columns), column names with data types,
    and null/non-null counts for each column. For Excel files with multiple sheets,
    returns statistics for each sheet separately.
    
    Use this before writing SQL queries to know available columns.
    """
    logger.info(f"info called for file_id: {file_id}")
    
    # Load the file into DataFrames if not already loaded
    if file_id not in loaded_files:
        logger.info(f"File {file_id} not in memory, loading from disk...")
        loaded_files[file_id] = load_dataframe_from_file(file_id)
    
    file_data = loaded_files[file_id]
    sheets_dict = file_data["dataframes"]
    sheet_names = file_data["sheet_names"]
    empty_sheets = file_data["empty_sheets"]
    
    try:
        # Gather information for each sheet
        sheets_info = []
        total_memory = 0
        
        for sheet_num in sorted(sheets_dict.keys()):
            df = sheets_dict[sheet_num]
            sheet_memory = int(df.memory_usage(deep=True).sum())
            total_memory += sheet_memory
            
            sheet_info = {
                "sheet_number": sheet_num,
                "sheet_name": sheet_names[sheet_num],
                "shape": {
                    "rows": int(df.shape[0]),
                    "columns": int(df.shape[1])
                },
                "columns": [
                    {
                        "name": str(col),
                        "dtype": str(df[col].dtype),
                        "non_null_count": int(df[col].count()),
                        "null_count": int(df[col].isna().sum())
                    }
                    for col in df.columns
                ],
                "memory_usage_bytes": sheet_memory
            }
            sheets_info.append(sheet_info)
        
        info_dict = {
            "status": "ready",
            "file_id": file_id,
            "num_sheets": len(sheets_dict),
            # "empty_sheets": empty_sheets,
            "sheets": sheets_info,
            "total_memory_usage_bytes": total_memory
        }
        
        logger.info(f"Successfully retrieved info for file {file_id}: {len(sheets_dict)} sheet(s)")
        return info_dict
        
    except Exception as e:
        logger.error(f"Failed to retrieve info for file {file_id}: {e}")
        raise Exception(f"Failed to retrieve info for file {file_id}: {e}")


@mcp.tool()
def get_rows_csv(
    file_id: Annotated[str, Field(description="The Google Drive file ID to retrieve rows from")],
    start: Annotated[int | None, Field(description="Starting row index (0-based, inclusive)")] = None,
    end: Annotated[int | None, Field(description="Ending row index (exclusive). If not specified, returns all rows from start")] = None,
    sheet_number: Annotated[int, Field(description="Sheet number to retrieve rows from (0-based index). Defaults to 0")] = 0
) -> dict:
    """Retrieve a range of rows from a loaded file as CSV-formatted text.
    
    Returns rows as CSV with column headers. Uses zero-based indexing where
    end is exclusive (like Python slicing). If start or end is not specified, returns
    all rows from the start of the file or to the end of the file.
    
    For Excel files with multiple sheets, specify sheet_number (0-based index).
    
    Example: start=0, end=10, sheet_number=0 returns the first 10 rows from sheet 0.
    """
    # Load the file into DataFrames if not already loaded
    if file_id not in loaded_files:
        loaded_files[file_id] = load_dataframe_from_file(file_id)
    
    sheets_dict = loaded_files[file_id]["dataframes"]
    
    # Validate sheet_number
    if sheet_number not in sheets_dict:
        available_sheets = sorted(sheets_dict.keys())
        raise Exception(f"Sheet {sheet_number} not found. Available sheets: {available_sheets}")
    
    df = sheets_dict[sheet_number]
    
    try:
        # Validate start index
        if start < 0 or start is None:
            start = 0
        
        # Default end to number of rows if not specified
        if end is None:
            end = len(df)
        
        # Ensure end is not beyond the dataframe length
        if end > len(df):
            end = len(df)
        
        # Ensure start is not after end
        if start >= end:
            return {
                "status": "success",
                "file_id": file_id,
                "sheet_number": sheet_number,
                "start": start,
                "end": end,
                "rows_returned": 0,
                "csv": ""
            }
        
        # Slice the DataFrame
        df_slice = df.iloc[start:end]
        
        # Convert to CSV
        csv_output = df_slice.to_csv(index=False)
        
        return {
            "status": "success",
            "file_id": file_id,
            "sheet_number": sheet_number,
            "start": start,
            "end": end,
            "rows_returned": len(df_slice),
            "csv": csv_output
        }
        
    except Exception as e:
        raise Exception(f"Failed to export rows from file {file_id}, sheet {sheet_number} as CSV: {e}")


@mcp.tool()
def query_file(
    file_id: Annotated[str, Field(description="The Google Drive file ID to query")],
    sql_query: Annotated[str, Field(description="SQL query to execute. For Excel files with multiple sheets, use 'data_0', 'data_1', etc. as table names where the number is the sheet index. For single-sheet files (CSV or single-sheet Excel), use 'data' or 'data_0'")]
) -> dict:
    """Execute SQL queries on a loaded file.
    
    Supports full SQL syntax (SELECT, WHERE, JOIN, GROUP BY, ORDER BY, etc.).
    Results are returned as structured data with columns and rows arrays.
    Only read operations are supported.

    For Excel files with multiple sheets, reference sheets as 'data_0', 'data_1', etc.
    where the number is the 0-based sheet index (e.g., "SELECT * FROM data_0" for first sheet).
    For single-sheet files (CSV or single-sheet Excel), use 'data' or 'data_0'.
    
    You can join multiple sheets: "SELECT * FROM data_0 JOIN data_1 ON data_0.id = data_1.id"
    
    Column names are case-sensitive and match the file's column headers.
    Use LIMIT clause to control the number of returned rows.
    """
    # Load the file into DataFrames if not already loaded
    if file_id not in loaded_files:
        loaded_files[file_id] = load_dataframe_from_file(file_id)
    
    sheets_dict = loaded_files[file_id]["dataframes"]
    
    try:
        # Use DuckDB to query the DataFrames
        # Register each sheet as a table named 'data.N' where N is the sheet number
        conn = duckdb.connect(":memory:")
        
        for sheet_num, df in sheets_dict.items():
            table_name = f"data_{sheet_num}"
            conn.register(table_name, df)
            logger.info(f"Registered sheet {sheet_num} as '{table_name}' with {df.shape[0]} rows, {df.shape[1]} columns")
            
            # Also register the first sheet as 'data' for backward compatibility
            if sheet_num == 0:
                conn.register("data", df)
                logger.info(f"Also registered sheet 0 as 'data' for backward compatibility")
        
        # Execute the query
        result = conn.execute(sql_query).fetchdf()
        
        # Close the connection
        conn.close()
        
        # Return the result as a dictionary with specific structure
        if result.empty:
            return {"status": "success", "columns": [], "rows": []}
        
        # Create the output structure: {status, columns: [...], rows: [[...], ...]}
        output = {
            "status": "success",
            "columns": result.columns.tolist(),
            "rows": result.values.tolist()
        }
        
        return output
        
    except Exception as e:
        raise Exception(f"Query execution failed on file {file_id}: {e}")


if __name__ == "__main__":
    try:
        # Run the MCP server
        mcp.run()
    except KeyboardInterrupt:
        pass
    except Exception as e:
        raise
