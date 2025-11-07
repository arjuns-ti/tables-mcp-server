"""Tables MCP Server - Main Entry Point"""

import shutil
from typing import Optional, Dict, Annotated
from pathlib import Path

import pandas as pd
import duckdb
from pydantic import Field

from mcp.server.fastmcp import FastMCP

from src.config import get_settings
from src.drive_client import DriveOperations
from src.auth import setup_google_drive_client, GoogleDriveAuthError

settings = get_settings()

# Helper function to clear downloads folder
def clear_downloads_folder():
    """Clear all files in the downloads folder"""
    download_dir = settings.get_download_directory_path()
    if download_dir.exists():
        for item in download_dir.iterdir():
            try:
                if item.is_file():
                    item.unlink()
                elif item.is_dir():
                    shutil.rmtree(item)
            except Exception:
                pass
    else:
        download_dir.mkdir(parents=True, exist_ok=True)


# Clear downloads folder on module initialization
clear_downloads_folder()

# Create MCP server
mcp = FastMCP("tables-mcp-server")

# Global drive operations instance
drive_ops: Optional[DriveOperations] = None

# Global dictionary to store DataFrames in memory (file_id -> DataFrame)
loaded_dataframes: Dict[str, pd.DataFrame] = {}


def get_drive_operations() -> DriveOperations:
    """Get or initialize the Drive operations instance"""
    global drive_ops
    if drive_ops is None:
        try:
            client = setup_google_drive_client(interactive=True)
            drive_ops = DriveOperations(client, settings)
        except GoogleDriveAuthError as e:
            raise
    return drive_ops


def load_dataframe_from_file(file_id: str) -> pd.DataFrame:
    """Load a downloaded file into a pandas DataFrame.
    
    Args:
        file_id: The Google Drive file ID of the downloaded file
        
    Returns:
        pandas DataFrame containing the file data
        
    Raises:
        Exception: If the file is not found or cannot be loaded
    """
    download_dir = settings.get_download_directory_path()
    matching_files = list(download_dir.glob(f"{file_id}.*"))
    
    if not matching_files:
        raise Exception(f"File {file_id} not found locally. Call load_file() first to download it.")
    
    file_path = matching_files[0]
    extension = file_path.suffix.lower()
    
    # Load based on file type
    try:
        if extension in ['.xlsx', '.xls']:
            df = pd.read_excel(file_path)
        elif extension == '.csv':
            df = pd.read_csv(file_path)
        else:
            raise Exception(f"Unsupported file type '{extension}'. Supported types: .xlsx, .xls, .csv")
        
        return df
        
    except Exception as e:
        raise Exception(f"Failed to load file {file_id} as DataFrame: {e}")


# MCP Tool Definitions
@mcp.tool()
def load_file(file_id: Annotated[str, Field(description="The Google Drive file ID to download and load into memory")]) -> dict:
    """Download a file from Google Drive and load it into memory for analysis.
    
    Supports Excel files (.xlsx, .xls), CSV files (.csv), and Google Sheets.
    Must be called before any other operations on the file.
    """
    # Check if already loaded
    if file_id in loaded_dataframes:
        return {
            "file_id": file_id,
            "status": "already loaded",
            "message": "File is already loaded and ready to use."
        }
    
    # Validate file exists on Google Drive
    try:
        ops = get_drive_operations()
        exists, error_message = ops.check_file_exists(file_id)
        if not exists:
            raise Exception(f"{error_message}")
    except Exception as e:
        raise
    
    # Download the file synchronously
    try:
        ops.download_file_by_id(file_id)
        
        return {
            "file_id": file_id,
            "status": "success",
            "message": "File downloaded successfully and ready to use."
        }
    except Exception as e:
        raise Exception(f"Failed to download file {file_id}: {e}")


@mcp.tool()
def unload_file(file_id: Annotated[str, Field(description="The Google Drive file ID to remove from local storage and memory")]) -> str:
    """Remove a file from local storage and free up memory.
    
    Does not affect the original file in Google Drive.
    """
    download_dir = settings.get_download_directory_path()
    
    # Clear DataFrame from memory
    if file_id in loaded_dataframes:
        del loaded_dataframes[file_id]
    
    # Look for file with this ID (could have various extensions)
    matching_files = list(download_dir.glob(f"{file_id}.*"))
    
    if not matching_files:
        return f"file {file_id} was unloaded."
    
    for file_path in matching_files:
        file_path.unlink()
    
    return f"file {file_id} was unloaded."


@mcp.tool()
def info(file_id: Annotated[str, Field(description="The Google Drive file ID to retrieve information about")]) -> dict:
    """Get metadata and statistics about a loaded file.
    
    Returns shape (rows and columns), column names with data types,
    and null/non-null counts for each column.
    
    Use this before writing SQL queries to know available columns.
    """
    # Load the file into DataFrame if not already loaded
    if file_id not in loaded_dataframes:
        loaded_dataframes[file_id] = load_dataframe_from_file(file_id)
    
    df = loaded_dataframes[file_id]
    
    try:
        # Gather DataFrame information
        info_dict = {
            "status": "ready",
            "file_id": file_id,
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
            "memory_usage_bytes": int(df.memory_usage(deep=True).sum())
        }
        
        return info_dict
        
    except Exception as e:
        raise Exception(f"Failed to retrieve info for file {file_id}: {e}")


@mcp.tool()
def get_rows_csv(
    file_id: Annotated[str, Field(description="The Google Drive file ID to retrieve rows from")],
    start: Annotated[int | None, Field(description="Starting row index (0-based, inclusive)")] = None,
    end: Annotated[int | None, Field(description="Ending row index (exclusive). If not specified, returns all rows from start")] = None
) -> dict:
    """Retrieve a range of rows from a loaded file as CSV-formatted text.
    
    Returns rows as CSV with column headers. Uses zero-based indexing where
    end is exclusive (like Python slicing). If start or end is not specified, returns
    all rows from the start of the file or to the end of the file.
    
    Example: start=0, end=10 returns the first 10 rows.
    """
    # Load the file into DataFrame if not already loaded
    if file_id not in loaded_dataframes:
        loaded_dataframes[file_id] = load_dataframe_from_file(file_id)
    
    df = loaded_dataframes[file_id]
    
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
            "start": start,
            "end": end,
            "rows_returned": len(df_slice),
            "csv": csv_output
        }
        
    except Exception as e:
        raise Exception(f"Failed to export rows from file {file_id} as CSV: {e}")


@mcp.tool()
def query_file(
    file_id: Annotated[str, Field(description="The Google Drive file ID to query")],
    sql_query: Annotated[str, Field(description="SQL query to execute (use 'data' as the table name)")]
) -> dict:
    """Execute SQL queries on a loaded file.
    
    Supports full SQL syntax (SELECT, WHERE, JOIN, GROUP BY, ORDER BY, etc.).
    Results are returned as structured data with columns and rows arrays.
    Only read operations are supported.

    Always reference the table as 'data' (e.g., "SELECT * FROM data").
    Column names are case-sensitive and match the file's column headers.
    Use LIMIT clause to control the number of returned rows.
    """
    # Load the file into DataFrame if not already loaded
    if file_id not in loaded_dataframes:
        loaded_dataframes[file_id] = load_dataframe_from_file(file_id)
    
    df = loaded_dataframes[file_id]
    
    try:
        # Use DuckDB to query the DataFrame
        # Register the DataFrame as a table named 'data'
        conn = duckdb.connect(":memory:")
        conn.register("data", df)
        
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
