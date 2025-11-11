"""Tables MCP Server - Main Entry Point"""

import shutil
import logging
from typing import Optional, Dict, Annotated
from pathlib import Path
from datetime import datetime, timedelta
import threading

import pandas as pd
import duckdb
from pydantic import Field

from mcp.server.fastmcp import FastMCP

from src.config import get_settings
from src.drive_client import DriveOperations
from src.auth import setup_google_drive_client, GoogleDriveAuthError
from src.models import (
    InfoResponse,
    GetRowsCsvResponse,
    QueryFileResponse,
    ColumnInfo,
    ShapeInfo,
    SheetInfo
)


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

# Maximum rows to return from queries and CSV exports to prevent server overload
MAX_ROWS = 100

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
# Structure: file_id -> {"dataframes": {sheet_num: DataFrame}, "sheet_names": {sheet_num: name}, "last_accessed": datetime}
loaded_files: Dict[str, Dict] = {}

# Cleanup configuration
CLEANUP_INTERVAL_MINUTES = 10  # How often to run cleanup
INACTIVITY_THRESHOLD_MINUTES = 10  # How long before a file is considered inactive


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


def update_file_access_time(file_id: str) -> None:
    """Update the last accessed timestamp for a file"""
    if file_id in loaded_files:
        loaded_files[file_id]["last_accessed"] = datetime.now()
        logger.debug(f"Updated last access time for file {file_id}")


def cleanup_inactive_files() -> None:
    """Remove files and dataframes that haven't been accessed for over INACTIVITY_THRESHOLD_MINUTES"""
    try:
        cutoff_time = datetime.now() - timedelta(minutes=INACTIVITY_THRESHOLD_MINUTES)
        files_to_remove = []
        
        # Find files that haven't been accessed recently
        for file_id, file_data in loaded_files.items():
            last_accessed = file_data.get("last_accessed")
            if last_accessed and last_accessed < cutoff_time:
                files_to_remove.append(file_id)
        
        # Remove inactive files
        if files_to_remove:
            logger.info(f"Cleaning up {len(files_to_remove)} inactive file(s): {files_to_remove}")
            for file_id in files_to_remove:
                try:
                    # Remove from memory
                    num_sheets = len(loaded_files[file_id]["dataframes"])
                    del loaded_files[file_id]
                    logger.info(f"Removed {num_sheets} sheet(s) for file {file_id} from memory")
                    
                    # Remove from disk
                    download_dir = settings.get_download_directory_path()
                    matching_files = list(download_dir.glob(f"{file_id}.*"))
                    for file_path in matching_files:
                        file_path.unlink()
                        logger.info(f"Deleted file: {file_path}")
                    
                    logger.info(f"Successfully cleaned up inactive file: {file_id}")
                except Exception as e:
                    logger.error(f"Error cleaning up file {file_id}: {e}")
        else:
            logger.debug("No inactive files to clean up")
            
    except Exception as e:
        logger.error(f"Error during cleanup: {e}")


def periodic_cleanup() -> None:
    """Run cleanup periodically in the background"""
    cleanup_inactive_files()
    # Schedule next cleanup
    timer = threading.Timer(CLEANUP_INTERVAL_MINUTES * 60, periodic_cleanup)
    timer.daemon = True
    timer.start()
    logger.debug(f"Next cleanup scheduled in {CLEANUP_INTERVAL_MINUTES} minutes")


def ensure_file_downloaded(file_id: str) -> None:
    """Ensure a file is downloaded from Google Drive if not already present locally.
    
    Args:
        file_id: The Google Drive file ID to download if needed
        
    Raises:
        Exception: If the file cannot be downloaded
    """
    download_dir = settings.get_download_directory_path()
    matching_files = list(download_dir.glob(f"{file_id}.*"))
    
    # If file already exists locally, no need to download
    if matching_files:
        logger.info(f"File {file_id} already exists locally: {matching_files[0]}")
        return
    
    # File not found locally, download from Google Drive
    logger.info(f"File {file_id} not found locally, downloading from Google Drive...")
    
    # Validate file exists on Google Drive
    try:
        ops = get_drive_operations()
        exists, error_message = ops.check_file_exists(file_id)
        if not exists:
            logger.error(f"File {file_id} does not exist on Google Drive: {error_message}")
            raise Exception(f"{error_message}")
        logger.info(f"File {file_id} exists on Google Drive")
    except Exception as e:
        logger.error(f"Error checking file existence: {e}")
        raise
    
    # Download the file
    try:
        logger.info(f"Starting download for file {file_id}...")
        ops.download_file_by_id(file_id)
        logger.info(f"File {file_id} downloaded successfully")
        
        # Verify the file was actually downloaded
        matching_files = list(download_dir.glob(f"{file_id}.*"))
        if matching_files:
            logger.info(f"Download verified: {matching_files[0]} (size: {matching_files[0].stat().st_size} bytes)")
        else:
            logger.error(f"Download completed but file not found in {download_dir}")
            raise Exception(f"File {file_id} download failed: file not found after download")
    except Exception as e:
        logger.error(f"Failed to download file {file_id}: {e}")
        raise Exception(f"Failed to download file {file_id}: {e}")


def load_dataframe_from_file(file_id: str) -> Dict:
    """Load a downloaded file into pandas DataFrames (one per sheet for Excel files).
    
    Args:
        file_id: The Google Drive file ID of the downloaded file
        
    Returns:
        Dictionary with structure:
        {
            "dataframes": {sheet_num: DataFrame, ...},
            "sheet_names": {sheet_num: name, ...}
        }
        
    Raises:
        Exception: If the file is not found or cannot be loaded
    """
    download_dir = settings.get_download_directory_path()
    matching_files = list(download_dir.glob(f"{file_id}.*"))
    
    logger.info(f"Loading file {file_id} from {download_dir}")
    
    if not matching_files:
        logger.error(f"File {file_id} not found locally in {download_dir}")
        raise Exception(f"File {file_id} not found locally after download attempt")
    
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
            new_index = 0  # Sequential index for non-empty sheets
            
            for idx, sheet_name in enumerate(sheet_names):
                logger.info(f"Loading original sheet {idx}: {sheet_name}")
                df = pd.read_excel(excel_file, sheet_name=sheet_name)
                
                # Skip empty sheets (0 columns)
                if df.shape[1] == 0:
                    logger.warning(f"Skipping empty sheet {idx} '{sheet_name}' - 0 columns")
                    continue
                
                sheets_dict[new_index] = df
                sheet_names_dict[new_index] = sheet_name
                logger.info(f"Sheet {new_index} (original {idx}, '{sheet_name}'): {df.shape[0]} rows, {df.shape[1]} columns")
                new_index += 1
            
            if len(sheets_dict) == 0:
                logger.error(f"All sheets in file {file_id} are empty (0 columns)")
                raise Exception(f"All sheets in file {file_id} are empty (0 columns). Cannot load file.")
            
            logger.info(f"Successfully loaded {len(sheets_dict)} non-empty sheets from {file_id}")
            
            return {
                "dataframes": sheets_dict,
                "sheet_names": sheet_names_dict,
                "last_accessed": datetime.now()
            }
        elif extension == '.csv':
            # CSV files only have one "sheet" (sheet 0)
            logger.info(f"Loading CSV file: {file_path}")
            df = pd.read_csv(file_path)
            logger.info(f"CSV loaded: {df.shape[0]} rows, {df.shape[1]} columns")
            return {
                "dataframes": {0: df},
                "sheet_names": {0: file_path.stem},  # Use filename as sheet name
                "last_accessed": datetime.now()
            }
        else:
            logger.error(f"Unsupported file type '{extension}' for file {file_id}")
            raise Exception(f"Unsupported file type '{extension}'. Supported types: .xlsx, .xls, .csv")
        
    except Exception as e:
        logger.error(f"Failed to load file {file_id} as DataFrame: {e}")
        raise Exception(f"Failed to load file {file_id} as DataFrame: {e}")


# MCP Tool Definitions
@mcp.tool()
def info(file_id: Annotated[str, Field(description="The Google Drive file ID to retrieve information about")]) -> InfoResponse | dict:
    """Get metadata and statistics about a file.
    
    Returns shape (rows and columns), column names with data types,
    and null/non-null counts for each column. For Excel files with multiple sheets,
    returns statistics for each sheet separately.
    
    Use this before writing SQL queries to know available columns.
    """
    logger.info(f"info called for file_id: {file_id}")
    
    # Hidden debug feature: Get all loaded files
    if file_id == "GETALLFILES":
        logger.info("Debug command: GETALLFILES")
        
        debug_data = []
        for fid, fdata in loaded_files.items():
            file_info = {
                "file_id": fid,
                "sheet_names": fdata["sheet_names"],
                "last_accessed": fdata["last_accessed"].isoformat()
            }
            debug_data.append(file_info)
            logger.info(f"Debug: {file_info}")
        
        # Return as dict (not following schema)
        return {"status": "debug", "files": debug_data, "total_files": len(loaded_files)}
    
    # Hidden debug feature: Master refresh - clear all files and dataframes
    if file_id == "MASTERREFRESH":
        logger.info("Debug command: MASTERREFRESH - clearing all files and dataframes")
        files_cleared = len(loaded_files)
        loaded_files.clear()
        clear_downloads_folder()
        logger.info(f"Cleared {files_cleared} files from memory and downloads folder")
        
        # Return as dict (not following schema)
        return {"status": "refreshed", "files_cleared": files_cleared, "message": "All files and dataframes cleared"}
    
    # Ensure file is downloaded from Google Drive
    ensure_file_downloaded(file_id)
    
    # Load the file into DataFrames if not already loaded
    if file_id not in loaded_files:
        logger.info(f"File {file_id} not in memory, loading from disk...")
        loaded_files[file_id] = load_dataframe_from_file(file_id)
    
    # Update access time
    update_file_access_time(file_id)
    
    file_data = loaded_files[file_id]
    sheets_dict = file_data["dataframes"]
    sheet_names = file_data["sheet_names"]
    
    try:
        # Gather information for each sheet
        sheets_info = []
        total_memory = 0
        
        for sheet_num in sorted(sheets_dict.keys()):
            df = sheets_dict[sheet_num]
            sheet_memory = int(df.memory_usage(deep=True).sum())
            total_memory += sheet_memory
            
            columns_info = [
                ColumnInfo(
                    name=str(col),
                    dtype=str(df[col].dtype),
                    non_null_count=int(df[col].count()),
                    null_count=int(df[col].isna().sum())
                )
                for col in df.columns
            ]
            
            sheet_info = SheetInfo(
                sheet_number=sheet_num,
                sheet_name=sheet_names[sheet_num],
                shape=ShapeInfo(
                    rows=int(df.shape[0]),
                    columns=int(df.shape[1])
                ),
                columns=columns_info,
                memory_usage_bytes=sheet_memory
            )
            sheets_info.append(sheet_info)
        
        info_response = InfoResponse(
            status="ready",
            file_id=file_id,
            num_sheets=len(sheets_dict),
            sheets=sheets_info,
            total_memory_usage_bytes=total_memory
        )
        
        logger.info(f"Successfully retrieved info for file {file_id}: {len(sheets_dict)} sheet(s)")
        return info_response
        
    except Exception as e:
        logger.error(f"Failed to retrieve info for file {file_id}: {e}")
        raise Exception(f"Failed to retrieve info for file {file_id}: {e}")


@mcp.tool()
def get_rows_csv(
    file_id: Annotated[str, Field(description="The Google Drive file ID to retrieve rows from")],
    start: Annotated[int | None, Field(description="Starting row index (0-based, inclusive)")] = None,
    end: Annotated[int | None, Field(description="Ending row index (exclusive). If not specified, returns all rows from start")] = None,
    sheet_number: Annotated[int, Field(description="Sheet number to retrieve rows from (0-based index). Defaults to 0")] = 0
) -> GetRowsCsvResponse:
    """Retrieve a range of rows from a file as CSV-formatted text.
    
    Returns rows as CSV with column headers. Uses zero-based indexing where
    end is exclusive (like Python slicing). If start or end is not specified, returns
    all rows from the start of the file or to the end of the file.
    
    For Excel files with multiple sheets, specify sheet_number (0-based index).
    
    IMPORTANT: Maximum rows per CSV export is limited to 100 rows to prevent
    server overload and context pollution. For larger datasets, make multiple calls
    with different start/end ranges.
    
    Example: start=0, end=10, sheet_number=0 returns the first 10 rows from sheet 0.
    """
    # Ensure file is downloaded from Google Drive
    ensure_file_downloaded(file_id)
    
    # Load the file into DataFrames if not already loaded
    if file_id not in loaded_files:
        loaded_files[file_id] = load_dataframe_from_file(file_id)
    
    # Update access time
    update_file_access_time(file_id)
    
    sheets_dict = loaded_files[file_id]["dataframes"]
    
    # Validate sheet_number
    if sheet_number not in sheets_dict:
        available_sheets = sorted(sheets_dict.keys())
        raise Exception(f"Sheet {sheet_number} not found. Available sheets: {available_sheets}")
    
    df = sheets_dict[sheet_number]
    
    try:
        # Validate start index
        if start is None or start < 0:
            start = 0
        
        # Default end to number of rows if not specified
        if end is None:
            end = len(df)
        
        # Ensure end is not beyond the dataframe length
        if end > len(df):
            end = len(df)
        
        # Calculate requested rows
        requested_rows = end - start
        
        # Enforce maximum CSV export limit
        if requested_rows > MAX_ROWS:
            raise Exception(
                f"Requested {requested_rows} rows exceeds maximum CSV export limit of {MAX_ROWS} rows. "
                f"Please reduce the range or make multiple calls with smaller ranges. "
                f"For example, use start={start}, end={start + 10} to get the first {10} rows."
            )
        
        # Ensure start is not after end
        if start >= end:
            return GetRowsCsvResponse(
                status="success",
                file_id=file_id,
                sheet_number=sheet_number,
                start=start,
                end=end,
                rows_returned=0,
                total_rows_in_sheet=len(df),
                csv=""
            )
        
        # Slice the DataFrame
        df_slice = df.iloc[start:end]
        
        # Convert to CSV
        csv_output = df_slice.to_csv(index=False)
        
        logger.info(f"CSV export: {len(df_slice)} rows from sheet {sheet_number} of file {file_id}")
        
        return GetRowsCsvResponse(
            status="success",
            file_id=file_id,
            sheet_number=sheet_number,
            start=start,
            end=end,
            rows_returned=len(df_slice),
            total_rows_in_sheet=len(df),
            csv=csv_output
        )
        
    except Exception as e:
        logger.error(f"Failed to export CSV: {e}")
        raise Exception(f"Failed to export rows from file {file_id}, sheet {sheet_number} as CSV: {e}")


@mcp.tool()
def query_file(
    file_id: Annotated[str, Field(description="The Google Drive file ID to query")],
    sql_query: Annotated[str, Field(description="SQL query to execute. For Excel files with multiple sheets, use 'data_0', 'data_1', etc. as table names where the number is the sheet index. For single-sheet files (CSV or single-sheet Excel), use 'data' or 'data_0'")]
) -> QueryFileResponse:
    """Execute SQL queries on a file.
    
    Supports full SQL syntax (SELECT, WHERE, JOIN, GROUP BY, ORDER BY, LIMIT, etc.).
    Results are returned as structured data with columns and rows arrays.
    Only read operations are supported.

    For Excel files with multiple sheets, reference sheets as 'data_0', 'data_1', etc.
    where the number is the 0-based sheet index (e.g., "SELECT * FROM data_0" for first sheet).
    For single-sheet files (CSV or single-sheet Excel), use 'data' or 'data_0'.
    
    You can join multiple sheets: "SELECT * FROM data_0 JOIN data_1 ON data_0.id = data_1.id"
    
    Column names are case-sensitive and match the file's column headers.
    
    IMPORTANT: Queries that return more than 100 rows will be rejected with an error message.
    Use WHERE clauses and LIMIT to narrow your results.
    """
    # Ensure file is downloaded from Google Drive
    ensure_file_downloaded(file_id)
    
    # Load the file into DataFrames if not already loaded
    if file_id not in loaded_files:
        loaded_files[file_id] = load_dataframe_from_file(file_id)
    
    # Update access time
    update_file_access_time(file_id)
    
    sheets_dict = loaded_files[file_id]["dataframes"]
    
    try:
        # Use DuckDB to query the DataFrames
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
        logger.info(f"Executing query: {sql_query}")
        result = conn.execute(sql_query).fetchdf()
        
        # Close the connection
        conn.close()
        
        # Check if result exceeds MAX_ROWS
        rows_returned = len(result)
        if rows_returned > MAX_ROWS:
            raise Exception(
                f"Query returned {rows_returned} rows, which exceeds the maximum of {MAX_ROWS} rows. "
                f"Please narrow your search using WHERE clauses or add a LIMIT clause."
            )
        
        # Return the result as a dictionary with specific structure
        if result.empty:
            return QueryFileResponse(status="success", columns=[], rows=[])
        
        output = QueryFileResponse(
            status="success",
            columns=result.columns.tolist(),
            rows=result.values.tolist()
        )
        
        logger.info(f"Query completed successfully: {rows_returned} rows returned")
        return output
        
    except Exception as e:
        logger.error(f"Query execution failed: {e}")
        raise Exception(f"Query execution failed on file {file_id}: {e}")


if __name__ == "__main__":
    try:
        # Start periodic cleanup in background
        logger.info(f"Starting periodic cleanup (every {CLEANUP_INTERVAL_MINUTES} minutes, files inactive for {INACTIVITY_THRESHOLD_MINUTES} minutes will be removed)")
        periodic_cleanup()
        
        # Run the MCP server
        mcp.run()
    except KeyboardInterrupt:
        pass
    except Exception as e:
        raise
