"""Google Drive Authentication - OAuth 2.0 HTTP flow with automatic token refresh"""

import os
import logging
from pathlib import Path
from typing import Optional

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from src.config import Settings

# Allow OAuth over HTTP for localhost
os.environ['OAUTHLIB_INSECURE_TRANSPORT'] = '1'

logger = logging.getLogger(__name__)
_logging_configured = False


def _configure_logging(enable: bool) -> None:
    """Configure logging based on settings"""
    global _logging_configured
    if _logging_configured:
        return
    _logging_configured = True
    
    if not enable:
        # Disable all logging
        logging.getLogger(__name__).disabled = True
        return
    
    class FlushFileHandler(logging.FileHandler):
        """File handler that flushes after every log message"""
        def emit(self, record):
            super().emit(record)
            self.flush()
    
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - [%(levelname)s] - %(message)s',
        handlers=[
            FlushFileHandler('logs.txt', mode='a'),
            logging.StreamHandler()
        ],
        force=True
    )


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
        
        # Configure logging once based on settings
        _configure_logging(settings.enable_logging)
    
    def authenticate(self, interactive: bool = True) -> Credentials:
        """Authenticate with Google Drive API using HTTP OAuth flow
        
        Automatically refreshes expired tokens. Only prompts for user login
        if no valid credentials exist.
        
        Args:
            interactive: If True, will start local HTTP server for OAuth callback
            
        Returns:
            Valid Google OAuth credentials
            
        Raises:
            GoogleDriveAuthError: If authentication fails
        """
        try:
            token_file = self.settings.get_token_file_path()
            client_config = self.settings.get_client_config_path()
            
            # Load existing credentials if available
            if token_file.exists():
                logger.info(f"Found existing credentials at {token_file}")
                try:
                    self.credentials = Credentials.from_authorized_user_file(
                        str(token_file),
                        scopes=self.settings.google_drive_api_scopes
                    )
                    logger.info("Successfully loaded credentials from file")
                except Exception as e:
                    logger.warning(f"Failed to load credentials: {e}")
                    self.credentials = None
            else:
                logger.info(f"No existing credentials found at {token_file}")
            
            # Refresh expired tokens
            if self.credentials and self.credentials.expired and self.credentials.refresh_token:
                logger.info("Credentials expired, refreshing...")
                try:
                    self.credentials.refresh(Request())
                    self._save_credentials()
                    logger.info("✓ Credentials refreshed successfully")
                except Exception as e:
                    logger.warning(f"Failed to refresh credentials: {e}")
                    self.credentials = None
            
            # Return if credentials are valid
            if self.credentials and self.credentials.valid:
                logger.info("✓ Authentication successful")
                return self.credentials
            
            # Start interactive OAuth flow
            if not interactive:
                raise GoogleDriveAuthError(
                    "No valid credentials available and interactive mode is disabled"
                )
            
            if not client_config.exists():
                raise GoogleDriveAuthError(
                    f"Client secrets file not found: {client_config}\n"
                    f"Download OAuth credentials from Google Cloud Console and save to {client_config}"
                )
            
            logger.info("Starting OAuth HTTP flow...")
            logger.info(f"Local OAuth server listening on port {self.settings.google_oauth_port}")
            
            # Initialize OAuth flow
            flow = InstalledAppFlow.from_client_secrets_file(
                str(client_config),
                scopes=self.settings.google_drive_api_scopes
            )
            
            redirect_uri = f'http://localhost:{self.settings.google_oauth_port}/'
            flow.redirect_uri = redirect_uri
            
            # Generate authorization URL
            auth_url, _ = flow.authorization_url(
                access_type='offline',
                prompt='consent'
            )
            
            logger.info("=" * 80)
            logger.info("COPY AND PASTE THIS URL INTO YOUR BROWSER:")
            logger.info(auth_url)
            logger.info("=" * 80)
            logger.info("Waiting for authentication callback...")
            
            # Start local HTTP server for OAuth callback
            import wsgiref.simple_server
            
            class SilentHandler(wsgiref.simple_server.WSGIRequestHandler):
                """HTTP request handler that suppresses request logging"""
                def log_message(self, format, *args):
                    pass
            
            authorization_response = None
            
            def callback_app(environ, start_response):
                """WSGI app that handles OAuth callback"""
                nonlocal authorization_response
                query_string = environ.get('QUERY_STRING', '')
                authorization_response = f"{redirect_uri}?{query_string}"
                
                start_response('200 OK', [('Content-type', 'text/html; charset=utf-8')])
                return [b"<html><body><h1>Authentication successful!</h1><p>You can close this window.</p></body></html>"]
            
            try:
                server = wsgiref.simple_server.make_server(
                    'localhost',
                    self.settings.google_oauth_port,
                    callback_app,
                    handler_class=SilentHandler
                )
            except OSError as e:
                if e.errno == 98 or 'Address already in use' in str(e):
                    raise GoogleDriveAuthError(
                        f"Port {self.settings.google_oauth_port} is already in use. "
                        f"Kill the process using this port or wait and try again. "
                        f"Run: lsof -ti:{self.settings.google_oauth_port} | xargs kill -9"
                    )
                raise
            
            logger.info(f"Server started on http://localhost:{self.settings.google_oauth_port}")
            
            try:
                server.handle_request()
            finally:
                server.server_close()
            
            logger.info("Callback received, exchanging code for credentials...")
            
            # Exchange authorization code for credentials
            flow.fetch_token(authorization_response=authorization_response)
            self.credentials = flow.credentials
            
            logger.info("✓ OAuth flow completed successfully")
            
            # Save credentials for future use
            self._save_credentials()
            logger.info(f"✓ Credentials saved to {token_file}")
            
            return self.credentials
            
        except GoogleDriveAuthError:
            raise
        except Exception as e:
            raise GoogleDriveAuthError(f"Authentication failed: {e}") from e
    
    def _save_credentials(self) -> None:
        """Save credentials to disk for future use"""
        try:
            token_file = self.settings.get_token_file_path()
            token_file.parent.mkdir(parents=True, exist_ok=True)
            
            with open(token_file, 'w') as f:
                f.write(self.credentials.to_json())
        except Exception:
            pass  # Non-critical, user can re-auth next time
    
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
            service.files().list(pageSize=1).execute()
            return True
        except (HttpError, Exception):
            return False


def setup_google_drive_client(settings: Settings = None, interactive: bool = True) -> GoogleDriveClient:
    """Setup and authenticate Google Drive client with automatic token refresh
    
    Main entry point for authentication. Creates client, authenticates,
    and verifies connection.
    
    Args:
        settings: Application settings (if None, loads from environment)
        interactive: Whether to allow interactive OAuth flow
        
    Returns:
        Authenticated GoogleDriveClient instance
        
    Raises:
        GoogleDriveAuthError: If authentication fails
    """
    try:
        if settings is None:
            from src.config import get_settings
            settings = get_settings()
        
        logger.info("Initializing Google Drive client...")
        
        client = GoogleDriveClient(settings)
        client.authenticate(interactive=interactive)
        
        logger.info("Testing connection to Google Drive...")
        if not client.test_connection():
            raise GoogleDriveAuthError("Authentication succeeded but connection test failed")
        
        logger.info("✓ Connection test successful - Google Drive is ready!")
        
        return client
        
    except GoogleDriveAuthError:
        raise
    except Exception as e:
        raise GoogleDriveAuthError(f"Failed to setup Google Drive client: {e}") from e

