"""Tables MCP Server - Main Entry Point"""

import logging
import shutil
import threading
import json
from typing import Optional, Dict
from pathlib import Path
from enum import Enum

import pandas as pd
import duckdb

from mcp.server.fastmcp import FastMCP

from src.config import get_settings
from src.drive_client import DriveOperations
from src.auth import setup_google_drive_client, GoogleDriveAuthError

# Setup logging
settings = get_settings()

# Create logger
logger = logging.getLogger()
logger.setLevel(getattr(logging, settings.logging_level.upper()))

# Create formatter
formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')

# Console handler
console_handler = logging.StreamHandler()
console_handler.setFormatter(formatter)
logger.addHandler(console_handler)

# File handler
file_handler = logging.FileHandler('logs.txt', mode='a')
file_handler.setFormatter(formatter)
logger.addHandler(file_handler)

logger = logging.getLogger(__name__)

# Helper function to clear downloads folder
def clear_downloads_folder():
    """Clear all files in the downloads folder"""
    download_dir = settings.get_download_directory_path()
    if download_dir.exists():
        logger.info(f"Clearing downloads folder: {download_dir}")
        for item in download_dir.iterdir():
            try:
                if item.is_file():
                    item.unlink()
                    logger.debug(f"Deleted file: {item.name}")
                elif item.is_dir():
                    shutil.rmtree(item)
                    logger.debug(f"Deleted directory: {item.name}")
            except Exception as e:
                logger.warning(f"Failed to delete {item}: {e}")
        logger.info("Downloads folder cleared")
    else:
        logger.info(f"Creating downloads folder: {download_dir}")
        download_dir.mkdir(parents=True, exist_ok=True)


# Clear downloads folder on module initialization
logger.info("Initializing Tables MCP Server...")
clear_downloads_folder()

# Create MCP server
mcp = FastMCP("tables-mcp-server")

# Global drive operations instance
drive_ops: Optional[DriveOperations] = None

# Download status tracking
class DownloadStatus(Enum):
    DOWNLOADING = "downloading"
    COMPLETED = "completed"
    FAILED = "failed"

# Global dictionary to track download status (file_id -> status)
download_status: Dict[str, DownloadStatus] = {}

# Global dictionary to track download errors (file_id -> error message)
download_errors: Dict[str, str] = {}

# Global dictionary to store DataFrames in memory (file_id -> DataFrame)
loaded_dataframes: Dict[str, pd.DataFrame] = {}


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
            logger.error(f"Failed to setup Google Drive client: {e}")
            raise
    return drive_ops


def _download_file_background(file_id: str):
    """Background thread function to download a file from Google Drive"""
    try:
        ops = get_drive_operations()
        logger.info(f"Starting background download for file {file_id}")
        ops.download_file_by_id(file_id)
        
        # Mark as completed
        download_status[file_id] = DownloadStatus.COMPLETED
        logger.info(f"Background download completed for file {file_id}")
        
    except Exception as e:
        # Mark as failed and store error
        download_status[file_id] = DownloadStatus.FAILED
        download_errors[file_id] = str(e)
        logger.error(f"Background download failed for file {file_id}: {e}")


# MCP Tool Definitions
@mcp.tool()
def load_file(file_id: str) -> dict:
    """Start downloading a file from Google Drive in the background.
    The download happens asynchronously and the function returns immediately.
    Supports xlsx, csv, and Google Sheets files.
    Use info() to check when the download is complete and see file information.
    
    Args:
        file_id: The Google Drive file ID to download
    
    Returns:
        Dictionary with file_id, status, and message
        
    Raises:
        Exception: If the download cannot be started
    """
    # Check if already downloading or downloaded
    if file_id in download_status:
        status = download_status[file_id]
        if status == DownloadStatus.DOWNLOADING:
            return {
                "file_id": file_id,
                "status": "already downloading",
                "message": "File is already downloading. Use info() to check when it's ready."
            }
        elif status == DownloadStatus.COMPLETED:
            return {
                "file_id": file_id,
                "status": "already loaded",
                "message": "File is already downloaded and ready. Use info() to see file details."
            }
        elif status == DownloadStatus.FAILED:
            # Clear failed status and retry
            error_msg = download_errors.get(file_id, "Unknown error")
            logger.info(f"Retrying failed download for file {file_id}. Previous error: {error_msg}")
            del download_status[file_id]
            if file_id in download_errors:
                del download_errors[file_id]
    
    # Validate file exists on Google Drive before starting download
    try:
        ops = get_drive_operations()
        exists, error_message = ops.check_file_exists(file_id)
        if not exists:
            logger.error(f"File {file_id} validation failed: {error_message}")
            raise Exception(f"{error_message}")
    except Exception as e:
        logger.error(f"Failed to validate file {file_id}: {e}")
        raise
    
    # Mark as downloading
    download_status[file_id] = DownloadStatus.DOWNLOADING
    
    # Start download in background thread
    thread = threading.Thread(target=_download_file_background, args=(file_id,), daemon=True)
    thread.start()
    
    logger.info(f"Started background download for file {file_id}")
    return {
        "file_id": file_id,
        "status": "downloading",
        "message": "Download started. Use info() to check when the download is complete and see file details."
    }


@mcp.tool()
def unload_file(file_id: str) -> str:
    """Delete a previously downloaded file from the local downloads folder and clear from memory.
    Idempotent - returns success even if file doesn't exist.
    
    Args:
        file_id: The Google Drive file ID (used as the filename)
    
    Returns:
        Success message
        
    Raises:
        Exception: If the file exists but cannot be deleted
    """
    download_dir = settings.get_download_directory_path()
    
    # Clear download status
    if file_id in download_status:
        del download_status[file_id]
        logger.info(f"Cleared download status for file {file_id}")
    
    # Clear download errors
    if file_id in download_errors:
        del download_errors[file_id]
    
    # Clear DataFrame from memory
    if file_id in loaded_dataframes:
        del loaded_dataframes[file_id]
        logger.info(f"Cleared DataFrame for file {file_id} from memory")
    
    # Look for file with this ID (could have various extensions)
    matching_files = list(download_dir.glob(f"{file_id}.*"))
    
    if not matching_files:
        logger.info(f"No file found with ID '{file_id}' (already unloaded)")
        return f"file {file_id} was unloaded."
    
    for file_path in matching_files:
        file_path.unlink()
        logger.info(f"Deleted file: {file_path}")
    
    return f"file {file_id} was unloaded."


@mcp.tool()
def info(file_id: str) -> dict:
    """Get information about a loaded file's DataFrame structure.
    Returns metadata about the file including shape, columns, data types, and statistics.
    If the file is still downloading, returns a status indicating it's pending.
    
    Args:
        file_id: The Google Drive file ID of the file
    
    Returns:
        Dictionary containing DataFrame info or download status
        
    Examples:
        - info("abc123")
    """
    # Check download status
    if file_id not in download_status:
        raise Exception(f"File {file_id} has not been loaded. Call load_file() first.")
    
    status = download_status[file_id]
    
    if status == DownloadStatus.DOWNLOADING:
        return {
            "status": "pending download",
            "file_id": file_id,
            "message": "File is still downloading. Please try again shortly."
        }
    
    if status == DownloadStatus.FAILED:
        error_msg = download_errors.get(file_id, "Unknown error")
        raise Exception(f"File {file_id} download failed: {error_msg}. Retry with load_file().")
    
    # Status is COMPLETED - load the file into DataFrame if not already loaded
    if file_id not in loaded_dataframes:
        logger.info(f"Loading file {file_id} into DataFrame...")
        download_dir = settings.get_download_directory_path()
        matching_files = list(download_dir.glob(f"{file_id}.*"))
        
        if not matching_files:
            raise Exception(f"File {file_id} was downloaded but not found locally. Try unload_file() then load_file().")
        
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
            
            # Store in memory
            loaded_dataframes[file_id] = df
            
            logger.info(f"Loaded DataFrame for file {file_id}: shape={df.shape}, columns={list(df.columns)}")
            
        except Exception as e:
            logger.error(f"Failed to load file {file_id} as DataFrame: {e}")
            raise Exception(f"Failed to load file {file_id} as DataFrame: {e}")
    
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
        
        logger.info(f"Retrieved info for file {file_id}: {df.shape[0]} rows, {df.shape[1]} columns")
        
        return info_dict
        
    except Exception as e:
        logger.error(f"Failed to get info for file {file_id}: {e}")
        raise Exception(f"Failed to retrieve info for file {file_id}: {e}")


@mcp.tool()
def get_rows_csv(file_id: str, start: int = 0, end: Optional[int] = None) -> dict:
    """Get rows from a loaded file in CSV format.
    Returns a specified range of rows from the DataFrame as CSV.
    If the file is still downloading, returns a status indicating it's pending.
    
    Args:
        file_id: The Google Drive file ID of the file
        start: Starting row index (0-based, default 0)
        end: Ending row index (exclusive, default is all rows)
    
    Returns:
        Dictionary with status and CSV data, or status object if not ready
        
    Examples:
        - get_rows_csv("abc123") - all rows
        - get_rows_csv("abc123", 0, 100) - first 100 rows
        - get_rows_csv("abc123", 50, 150) - rows 50-149
    """
    # Check download status
    if file_id not in download_status:
        raise Exception(f"File {file_id} has not been loaded. Call load_file() first.")
    
    status = download_status[file_id]
    
    if status == DownloadStatus.DOWNLOADING:
        return {
            "status": "pending download",
            "file_id": file_id,
            "message": "File is still downloading. Please try again shortly."
        }
    
    if status == DownloadStatus.FAILED:
        error_msg = download_errors.get(file_id, "Unknown error")
        raise Exception(f"File {file_id} download failed: {error_msg}. Retry with load_file().")
    
    # Status is COMPLETED - load the file into DataFrame if not already loaded
    if file_id not in loaded_dataframes:
        logger.info(f"Loading file {file_id} into DataFrame...")
        download_dir = settings.get_download_directory_path()
        matching_files = list(download_dir.glob(f"{file_id}.*"))
        
        if not matching_files:
            raise Exception(f"File {file_id} was downloaded but not found locally. Try unload_file() then load_file().")
        
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
            
            # Store in memory
            loaded_dataframes[file_id] = df
            
            logger.info(f"Loaded DataFrame for file {file_id}: shape={df.shape}, columns={list(df.columns)}")
            
        except Exception as e:
            logger.error(f"Failed to load file {file_id} as DataFrame: {e}")
            raise Exception(f"Failed to load file {file_id} as DataFrame: {e}")
    
    df = loaded_dataframes[file_id]
    
    try:
        # Validate start index
        if start < 0:
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
        
        logger.info(f"Exported rows {start}-{end} from file {file_id} as CSV ({len(df_slice)} rows)")
        
        return {
            "status": "success",
            "file_id": file_id,
            "start": start,
            "end": end,
            "rows_returned": len(df_slice),
            "csv": csv_output
        }
        
    except Exception as e:
        logger.error(f"Failed to export rows from file {file_id}: {e}")
        raise Exception(f"Failed to export rows from file {file_id} as CSV: {e}")


@mcp.tool()
def query_file(file_id: str, sql_query: str) -> dict:
    """Run a SQL query on a downloaded file using DuckDB.
    The file must be fully downloaded first using load_file().
    The table name in the SQL query should be 'data'.
    If the file is still downloading, returns a status indicating it's pending.
    
    Args:
        file_id: The Google Drive file ID of the downloaded file
        sql_query: SQL query to execute (use 'data' as the table name)
    
    Returns:
        Query results as a dictionary with 'columns' and 'rows' keys, or status object if not ready
        
    Raises:
        Exception: If the query execution fails or file cannot be loaded
    
    Examples:
        - query_file("abc123", "SELECT * FROM data LIMIT 10")
        - query_file("abc123", "SELECT column1, COUNT(*) FROM data GROUP BY column1")
        - query_file("abc123", "SELECT * FROM data WHERE column1 > 100")
    """
    # Check download status
    if file_id not in download_status:
        raise Exception(f"File {file_id} has not been loaded. Call load_file() first.")
    
    status = download_status[file_id]
    
    if status == DownloadStatus.DOWNLOADING:
        return {
            "status": "pending download",
            "file_id": file_id,
            "message": "File is still downloading. Please try again shortly."
        }
    
    if status == DownloadStatus.FAILED:
        error_msg = download_errors.get(file_id, "Unknown error")
        raise Exception(f"File {file_id} download failed: {error_msg}. Retry with load_file().")
    
    # Status is COMPLETED - load the file into DataFrame if not already loaded
    if file_id not in loaded_dataframes:
        logger.info(f"Loading file {file_id} into DataFrame...")
        download_dir = settings.get_download_directory_path()
        matching_files = list(download_dir.glob(f"{file_id}.*"))
        
        if not matching_files:
            raise Exception(f"File {file_id} was downloaded but not found locally. Try unload_file() then load_file().")
        
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
            
            # Store in memory
            loaded_dataframes[file_id] = df
            
            logger.info(f"Loaded DataFrame for file {file_id}: shape={df.shape}, columns={list(df.columns)}")
            
        except Exception as e:
            logger.error(f"Failed to load file {file_id} as DataFrame: {e}")
            raise Exception(f"Failed to load file {file_id} as DataFrame: {e}")
    
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
        
        logger.info(f"Query executed successfully on file {file_id}. Returned {len(result)} rows and {len(result.columns)} columns.")
        
        return output
        
    except Exception as e:
        logger.error(f"Failed to execute query on file {file_id}: {e}")
        raise Exception(f"Query execution failed on file {file_id}: {e}")


if __name__ == "__main__":
    try:
        # Run the MCP server
        mcp.run()
    except KeyboardInterrupt:
        logger.info("Server shutdown requested")
    except Exception as e:
        logger.error(f"Failed to start server: {e}")
        raise
