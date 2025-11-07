"""Tables MCP Server - Main Entry Point"""

import logging
from typing import Optional

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

# Create MCP server
mcp = FastMCP("tables-mcp-server")

# Global drive operations instance
drive_ops: Optional[DriveOperations] = None


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


# MCP Tool Definitions
@mcp.tool()
def list_drive_files(
    max_results: int = 10,
    query: Optional[str] = None,
    folder_id: Optional[str] = None,
    shared_drive_id: Optional[str] = None
) -> str:
    """List files from Google Drive, including Shared Drives.
    
    Args:
        max_results: Maximum number of files to return (default: 10, max: 100)
        query: Optional search query (e.g., "name contains 'report'")
        folder_id: Optional folder ID to list files from a specific folder
        shared_drive_id: Optional Shared Drive ID to list files from a specific Shared Drive
    
    Returns:
        JSON string with list of files and their metadata
    """
    ops = get_drive_operations()
    return ops.list_files(max_results, query, folder_id, shared_drive_id)


@mcp.tool()
def download_drive_file(
    file_id: str,
    destination_name: Optional[str] = None
) -> str:
    """Download a file from Google Drive, including files in Shared Drives.
    
    Args:
        file_id: The Google Drive file ID to download
        destination_name: Optional custom name for the downloaded file
    
    Returns:
        Success message with file path or error message
    """
    ops = get_drive_operations()
    return ops.download_file(file_id, destination_name)


@mcp.tool()
def search_drive_files(
    search_term: str,
    max_results: int = 10,
    shared_drive_id: Optional[str] = None
) -> str:
    """Search for files in Google Drive by name, including Shared Drives.
    
    Args:
        search_term: The term to search for in file names
        max_results: Maximum number of results to return (default: 10, max: 50)
        shared_drive_id: Optional Shared Drive ID to search within a specific Shared Drive
    
    Returns:
        JSON string with matching files
    """
    ops = get_drive_operations()
    return ops.search_files(search_term, max_results, shared_drive_id)


@mcp.tool()
def get_file_info(file_id: str) -> str:
    """Get detailed information about a Google Drive file, including files in Shared Drives.
    
    Args:
        file_id: The Google Drive file ID
    
    Returns:
        JSON string with file metadata
    """
    ops = get_drive_operations()
    return ops.get_file_info(file_id)


@mcp.tool()
def list_shared_drives(max_results: int = 10) -> str:
    """List available Shared Drives (formerly Team Drives).
    
    Args:
        max_results: Maximum number of Shared Drives to return (default: 10, max: 100)
    
    Returns:
        JSON string with list of Shared Drives you have access to
    """
    ops = get_drive_operations()
    return ops.list_shared_drives(max_results)


@mcp.tool()
def ping() -> str:
    """Simple ping endpoint that returns 'pong' and checks Drive connection."""
    try:
        ops = get_drive_operations()
        if ops.test_connection():
            return "pong 🏓 - Google Drive connected ✅"
        else:
            return "pong 🏓 - Google Drive connection issues ⚠️"
    except Exception:
        return "pong 🏓 - Google Drive not initialized"


if __name__ == "__main__":
    try:
        # Initialize Google Drive client on startup
        logger.info("Starting Tables MCP Server...")
        get_drive_operations()
        logger.info("Server ready")
        
        # Run the MCP server
        mcp.run()
    except KeyboardInterrupt:
        logger.info("Server shutdown requested")
    except Exception as e:
        logger.error(f"Failed to start server: {e}")
        raise
