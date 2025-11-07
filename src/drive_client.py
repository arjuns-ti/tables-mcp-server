"""Google Drive Client - File operations for loading and unloading files"""

import logging

from googleapiclient.errors import HttpError
from googleapiclient.http import MediaIoBaseDownload

from src.auth import GoogleDriveClient
from src.config import Settings

logger = logging.getLogger(__name__)


class DriveOperations:
    """Handles Google Drive API operations for file loading/unloading"""
    
    def __init__(self, client: GoogleDriveClient, settings: Settings):
        self.client = client
        self.settings = settings
        self.service = client.get_service()
    
    def test_connection(self) -> bool:
        """Test the Google Drive connection"""
        return self.client.test_connection()
    
    def check_file_exists(self, file_id: str) -> tuple[bool, str]:
        """Check if a file exists and is accessible on Google Drive.
        
        Args:
            file_id: The Google Drive file ID to check
            
        Returns:
            Tuple of (exists: bool, error_message: str or None)
        """
        try:
            # Build metadata request parameters
            metadata_params = {
                'fileId': file_id,
                'fields': 'name, mimeType'
            }
            if self.settings.enable_shared_drives:
                metadata_params['supportsAllDrives'] = True
            
            # Try to get file metadata
            self.service.files().get(**metadata_params).execute()
            return (True, None)
            
        except HttpError as e:
            error_code = e.resp.status
            if error_code == 404:
                return (False, "File not found. Please check the file ID.")
            elif error_code == 403:
                return (False, "Access denied. You don't have permission to access this file.")
            else:
                return (False, f"Error accessing file: {e}")
        except Exception as e:
            return (False, f"Unexpected error: {e}")
    
    def download_file_by_id(self, file_id: str) -> None:
        """Download a file from Google Drive and save it with the drive ID as filename.
        Supports xlsx, csv, and Google Sheets files. Google Sheets are exported as xlsx.
        
        Args:
            file_id: The Google Drive file ID to download
        
        Raises:
            Exception: If download fails for any reason
        """
        # Build metadata request parameters
        metadata_params = {
            'fileId': file_id,
            'fields': 'name, mimeType, size, driveId'
        }
        if self.settings.enable_shared_drives:
            metadata_params['supportsAllDrives'] = True
        
        # Get file metadata
        file_metadata = self.service.files().get(**metadata_params).execute()
        
        original_name = file_metadata['name']
        mime_type = file_metadata.get('mimeType', 'unknown')
        file_size = int(file_metadata.get('size', 0))
        
        logger.info(f"Downloading file: {original_name} (ID: {file_id}, MIME: {mime_type})")
        
        # Determine file extension based on MIME type
        extension = self._get_extension_from_name_or_mime(original_name, mime_type)
        
        # Create destination filename using file_id
        destination_name = f"{file_id}{extension}"
        
        # Get download directory
        download_dir = self.settings.get_download_directory_path()
        destination_path = download_dir / destination_name
        
        # Handle Google Sheets - export as xlsx
        if mime_type == 'application/vnd.google-apps.spreadsheet':
            self._export_google_sheet(file_id, destination_path, original_name)
            return
        
        # Check file size limit for regular files
        max_size_bytes = self.settings.max_file_size_mb * 1024 * 1024
        if self.settings.max_file_size_mb > 0 and file_size > max_size_bytes:
            raise Exception(f"File size ({file_size} bytes) exceeds maximum allowed size ({max_size_bytes} bytes)")
        
        # Build download request parameters
        download_params = {'fileId': file_id}
        if self.settings.enable_shared_drives:
            download_params['supportsAllDrives'] = True
        
        # Download file with larger chunk size for faster downloads
        request = self.service.files().get_media(**download_params)
        
        with open(destination_path, 'wb') as f:
            # Use 10MB chunk size for faster downloads (default is 256KB)
            downloader = MediaIoBaseDownload(f, request, chunksize=10*1024*1024)
            done = False
            while not done:
                status, done = downloader.next_chunk()
                if status:
                    progress = int(status.progress() * 100)
                    logger.debug(f"Download progress: {progress}%")
        
        logger.info(f"File downloaded successfully to: {destination_path}")
    
    def _get_extension_from_name_or_mime(self, name: str, mime_type: str) -> str:
        """Determine file extension from filename or MIME type"""
        # Try to get extension from filename first
        if '.' in name:
            return name[name.rfind('.'):]
        
        # Fallback to MIME type mapping
        mime_to_ext = {
            'application/vnd.google-apps.spreadsheet': '.xlsx',
            'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet': '.xlsx',
            'application/vnd.ms-excel': '.xls',
            'text/csv': '.csv',
            'text/plain': '.txt',
            'application/pdf': '.pdf',
            'application/json': '.json',
        }
        return mime_to_ext.get(mime_type, '.bin')
    
    def _export_google_sheet(self, file_id: str, destination_path, original_name: str) -> None:
        """Export a Google Sheet as xlsx format
        
        Raises:
            Exception: If export fails
        """
        logger.info(f"Exporting Google Sheet as xlsx: {original_name}")
        
        # Build export request parameters
        export_params = {
            'fileId': file_id,
            'mimeType': 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
        }
        if self.settings.enable_shared_drives:
            export_params['supportsAllDrives'] = True
        
        # Export the file with larger chunk size for faster downloads
        request = self.service.files().export_media(**export_params)
        
        with open(destination_path, 'wb') as f:
            # Use 10MB chunk size for faster downloads (default is 256KB)
            downloader = MediaIoBaseDownload(f, request, chunksize=10*1024*1024)
            done = False
            while not done:
                status, done = downloader.next_chunk()
                if status:
                    progress = int(status.progress() * 100)
                    logger.debug(f"Export progress: {progress}%")
        
        logger.info(f"Google Sheet exported successfully to: {destination_path}")

