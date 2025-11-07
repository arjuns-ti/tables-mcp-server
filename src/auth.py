"""Google Drive Authentication - OAuth 2.0 flow with automatic token refresh"""

import os
from pathlib import Path
from typing import Optional

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from src.config import Settings


class GoogleDriveAuthError(Exception):
    """Custom exception for Google Drive authentication errors"""
    pass


class GoogleDriveClient:
    """Handles Google Drive authentication and API service creation with automatic token refresh"""
    
    def __init__(self, settings: Settings):
        """Initialize the Google Drive client
        
        Args:
            settings: Application settings containing OAuth configuration
        """
        self.settings = settings
        self.credentials: Optional[Credentials] = None
        self.service = None
    
    def authenticate(self, interactive: bool = True) -> Credentials:
        """Authenticate with Google Drive API and automatically refresh tokens
        
        This method:
        1. Checks for existing saved credentials
        2. Automatically refreshes expired tokens (no user intervention needed)
        3. Only prompts for user login if no valid credentials exist
        
        Args:
            interactive: If True, will open browser for OAuth if needed
            
        Returns:
            Valid Google OAuth credentials
            
        Raises:
            GoogleDriveAuthError: If authentication fails
        """
        try:
            token_file = self.settings.get_token_file_path()
            client_config = self.settings.get_client_config_path()
            
            # Check if we have saved credentials
            if token_file.exists():
                try:
                    self.credentials = Credentials.from_authorized_user_file(
                        str(token_file),
                        scopes=self.settings.google_drive_api_scopes
                    )
                except Exception:
                    self.credentials = None
            
            # Automatically refresh expired tokens
            if self.credentials and self.credentials.expired and self.credentials.refresh_token:
                try:
                    self.credentials.refresh(Request())
                    # Save the refreshed credentials
                    self._save_credentials()
                except Exception:
                    self.credentials = None
            
            # If we have valid credentials, we're done
            if self.credentials and self.credentials.valid:
                return self.credentials
            
            # Need to authenticate - only happens on first run or if refresh failed
            if not interactive:
                raise GoogleDriveAuthError(
                    "No valid credentials available and interactive mode is disabled"
                )
            
            # Check if client config exists
            if not client_config.exists():
                raise GoogleDriveAuthError(
                    f"Client secrets file not found: {client_config}\n"
                    f"Please download OAuth credentials from Google Cloud Console and save to {client_config}\n"
                    f"See README.md for setup instructions"
                )
            
            # Run OAuth flow
            flow = InstalledAppFlow.from_client_secrets_file(
                str(client_config),
                scopes=self.settings.google_drive_api_scopes
            )
            
            # Run local server for OAuth callback
            self.credentials = flow.run_local_server(
                port=self.settings.google_oauth_port,
                success_message="Authentication successful! You can close this window.",
                open_browser=True
            )
            
            # Save credentials for future use
            self._save_credentials()
            
            return self.credentials
            
        except GoogleDriveAuthError:
            raise
        except Exception as e:
            raise GoogleDriveAuthError(f"Authentication failed: {e}") from e
    
    def _save_credentials(self) -> None:
        """Save credentials to disk for future use"""
        try:
            token_file = self.settings.get_token_file_path()
            
            # Ensure parent directory exists
            token_file.parent.mkdir(parents=True, exist_ok=True)
            
            # Save credentials as JSON
            with open(token_file, 'w') as token:
                token.write(self.credentials.to_json())
        except Exception:
            # Don't raise - this is not critical, user can re-auth next time
            pass
    
    def get_service(self):
        """Get or create the Google Drive API service
        
        Returns:
            Authenticated Google Drive API service
            
        Raises:
            GoogleDriveAuthError: If service creation fails
        """
        try:
            if self.service is None:
                if self.credentials is None:
                    self.authenticate()
                
                self.service = build('drive', 'v3', credentials=self.credentials)
            
            return self.service
            
        except Exception as e:
            raise GoogleDriveAuthError(f"Failed to create Drive service: {e}") from e
    
    def test_connection(self) -> bool:
        """Test the Google Drive API connection
        
        Returns:
            True if connection is working, False otherwise
        """
        try:
            service = self.get_service()
            # Try to list 1 file as a connection test
            service.files().list(pageSize=1).execute()
            return True
        except HttpError:
            return False
        except Exception:
            return False


def setup_google_drive_client(settings: Settings = None, interactive: bool = True) -> GoogleDriveClient:
    """Setup and authenticate Google Drive client with automatic token refresh
    
    This is the main entry point for authentication. It:
    1. Creates a GoogleDriveClient instance
    2. Runs authentication (with automatic refresh if tokens exist)
    3. Returns a ready-to-use client
    
    Args:
        settings: Application settings (if None, will load from environment)
        interactive: Whether to allow interactive OAuth flow
        
    Returns:
        Authenticated GoogleDriveClient instance
        
    Raises:
        GoogleDriveAuthError: If authentication fails
    """
    try:
        # Load settings if not provided
        if settings is None:
            from src.config import get_settings
            settings = get_settings()
        
        # Create client
        client = GoogleDriveClient(settings)
        
        # Authenticate (will auto-refresh if possible)
        client.authenticate(interactive=interactive)
        
        # Test connection
        if not client.test_connection():
            raise GoogleDriveAuthError("Authentication succeeded but connection test failed")
        
        return client
        
    except GoogleDriveAuthError:
        raise
    except Exception as e:
        raise GoogleDriveAuthError(f"Failed to setup Google Drive client: {e}") from e

