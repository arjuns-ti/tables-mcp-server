"""Google Drive Client - All drive operations and business logic"""

import json
import logging
from typing import Optional

from googleapiclient.errors import HttpError
from googleapiclient.http import MediaIoBaseDownload

from src.auth import GoogleDriveClient
from src.config import Settings

logger = logging.getLogger(__name__)


class DriveOperations:
    """Handles all Google Drive API operations"""
    
    def __init__(self, client: GoogleDriveClient, settings: Settings):
        self.client = client
        self.settings = settings
        self.service = client.get_service()
    
    def list_files(
        self,
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
        try:
            # Limit max_results
            max_results = min(max_results, 100)
            
            # Build query
            search_query = query or ""
            if folder_id:
                folder_query = f"'{folder_id}' in parents"
                search_query = f"{folder_query} and ({search_query})" if search_query else folder_query
            
            # Add trashed filter
            if search_query:
                search_query = f"({search_query}) and trashed=false"
            else:
                search_query = "trashed=false"
            
            logger.info(f"Listing files with query: {search_query}")
            
            # Build API call parameters
            list_params = {
                'pageSize': max_results,
                'q': search_query,
                'fields': 'files(id, name, mimeType, size, createdTime, modifiedTime, webViewLink, parents, driveId)',
            }
            
            # Add Shared Drives support if enabled
            if self.settings.enable_shared_drives:
                list_params['supportsAllDrives'] = True
                list_params['includeItemsFromAllDrives'] = True
                if shared_drive_id:
                    list_params['driveId'] = shared_drive_id
                    list_params['corpora'] = 'drive'
                else:
                    list_params['corpora'] = 'allDrives'
            
            results = self.service.files().list(**list_params).execute()
            
            files = results.get('files', [])
            
            if not files:
                return "No files found."
            
            return json.dumps({
                "count": len(files),
                "files": files
            }, indent=2)
            
        except HttpError as e:
            error_msg = f"Google Drive API error: {e}"
            logger.error(error_msg)
            return f"Error: {error_msg}"
        except Exception as e:
            error_msg = f"Unexpected error: {e}"
            logger.error(error_msg)
            return f"Error: {error_msg}"
    
    def download_file(
        self,
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
        try:
            # Build metadata request parameters
            metadata_params = {
                'fileId': file_id,
                'fields': 'name, mimeType, size, driveId'
            }
            if self.settings.enable_shared_drives:
                metadata_params['supportsAllDrives'] = True
            
            # Get file metadata
            file_metadata = self.service.files().get(**metadata_params).execute()
            
            file_name = destination_name or file_metadata['name']
            file_size = int(file_metadata.get('size', 0))
            mime_type = file_metadata.get('mimeType', 'unknown')
            
            logger.info(f"Downloading file: {file_name} (ID: {file_id}, Size: {file_size} bytes)")
            
            # Check file size limit
            max_size_bytes = self.settings.max_file_size_mb * 1024 * 1024
            if self.settings.max_file_size_mb > 0 and file_size > max_size_bytes:
                return f"Error: File size ({file_size} bytes) exceeds maximum allowed size ({max_size_bytes} bytes)"
            
            # Get download directory
            download_dir = self.settings.get_download_directory_path()
            destination_path = download_dir / file_name
            
            # Build download request parameters
            download_params = {'fileId': file_id}
            if self.settings.enable_shared_drives:
                download_params['supportsAllDrives'] = True
            
            # Download file
            request = self.service.files().get_media(**download_params)
            
            with open(destination_path, 'wb') as f:
                downloader = MediaIoBaseDownload(f, request)
                done = False
                while not done:
                    status, done = downloader.next_chunk()
                    if status:
                        progress = int(status.progress() * 100)
                        logger.info(f"Download progress: {progress}%")
            
            logger.info(f"File downloaded successfully to: {destination_path}")
            
            return f"Successfully downloaded '{file_name}' to {destination_path.absolute()}"
            
        except HttpError as e:
            if e.resp.status == 404:
                error_msg = f"File not found: {file_id}"
            else:
                error_msg = f"Google Drive API error: {e}"
            logger.error(error_msg)
            return f"Error: {error_msg}"
        except Exception as e:
            error_msg = f"Unexpected error: {e}"
            logger.error(error_msg)
            return f"Error: {error_msg}"
    
    def search_files(
        self,
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
        query = f"name contains '{search_term}'"
        return self.list_files(max_results=min(max_results, 50), query=query, shared_drive_id=shared_drive_id)
    
    def get_file_info(self, file_id: str) -> str:
        """Get detailed information about a Google Drive file, including files in Shared Drives.
        
        Args:
            file_id: The Google Drive file ID
        
        Returns:
            JSON string with file metadata
        """
        try:
            # Build metadata request parameters
            metadata_params = {
                'fileId': file_id,
                'fields': 'id, name, mimeType, size, createdTime, modifiedTime, webViewLink, owners, permissions, parents, description, driveId'
            }
            if self.settings.enable_shared_drives:
                metadata_params['supportsAllDrives'] = True
            
            file_metadata = self.service.files().get(**metadata_params).execute()
            
            return json.dumps(file_metadata, indent=2)
            
        except HttpError as e:
            if e.resp.status == 404:
                error_msg = f"File not found: {file_id}"
            else:
                error_msg = f"Google Drive API error: {e}"
            logger.error(error_msg)
            return f"Error: {error_msg}"
        except Exception as e:
            error_msg = f"Unexpected error: {e}"
            logger.error(error_msg)
            return f"Error: {error_msg}"
    
    def list_shared_drives(self, max_results: int = 10) -> str:
        """List available Shared Drives (formerly Team Drives).
        
        Args:
            max_results: Maximum number of Shared Drives to return (default: 10, max: 100)
        
        Returns:
            JSON string with list of Shared Drives
        """
        try:
            if not self.settings.enable_shared_drives:
                return "Error: Shared Drives support is disabled. Enable it in config."
            
            # Limit max_results
            max_results = min(max_results, 100)
            
            logger.info(f"Listing Shared Drives (max: {max_results})")
            
            results = self.service.drives().list(
                pageSize=max_results,
                fields="drives(id, name, createdTime)"
            ).execute()
            
            drives = results.get('drives', [])
            
            if not drives:
                return "No Shared Drives found. You may not have access to any Shared Drives."
            
            return json.dumps({
                "count": len(drives),
                "shared_drives": drives
            }, indent=2)
            
        except HttpError as e:
            error_msg = f"Google Drive API error: {e}"
            logger.error(error_msg)
            return f"Error: {error_msg}"
        except Exception as e:
            error_msg = f"Unexpected error: {e}"
            logger.error(error_msg)
            return f"Error: {error_msg}"
    
    def test_connection(self) -> bool:
        """Test the Google Drive connection"""
        return self.client.test_connection()

