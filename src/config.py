"""Configuration for the Tables MCP Server - Google Drive Integration"""

import os
from pathlib import Path

from pydantic import Field, ConfigDict
from pydantic_settings import BaseSettings

from dotenv import load_dotenv
load_dotenv(Path(__file__).parent.parent / ".env")

class Settings(BaseSettings):
    """Settings for the Tables MCP Server - Google Drive Integration"""

    # Google Cloud Config
    google_client_config: str = Field(
        "credentials/client_secrets.json",
        description="The path to the Google OAuth client secrets JSON file",
    )

    google_token_file: str = Field(
        ".gcp-saved-tokens.json",
        description="The path to store OAuth access and refresh tokens",
    )

    google_oauth_port: int = Field(
        8765,
        description="The port to use for OAuth callback during authentication"
    )

    # Google Drive API scopes
    google_drive_api_scopes: list[str] = Field(
        [
            "https://www.googleapis.com/auth/drive.readonly",
            "https://www.googleapis.com/auth/drive.file",
        ],
        description="The scopes to use for the Google Drive API (includes readonly, file access, and shared drives)"
    )
    
    # Shared Drives (Team Drives) support
    enable_shared_drives: bool = Field(
        True,
        description="Enable access to Shared Drives (formerly Team Drives)"
    )

    # Download Configuration
    download_directory: str = Field(
        "downloads",
        description="The directory where downloaded files will be stored"
    )

    max_file_size_mb: int = Field(
        100,
        description="Maximum file size in MB to download (0 for no limit)"
    )

    model_config = ConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        env_ignore_empty=True,
    )

    def get_client_config_path(self) -> Path:
        """Get the client config path"""
        return Path(self.google_client_config)

    def get_token_file_path(self) -> Path:
        """Get the token file path"""
        return Path(self.google_token_file)

    def get_download_directory_path(self) -> Path:
        """Get the download directory path"""
        download_path = Path(self.download_directory)
        # Create the directory if it doesn't exist
        download_path.mkdir(parents=True, exist_ok=True)
        return download_path

def get_settings() -> Settings:
    """Get application settings"""
    try:
        return Settings()
    except Exception as e:
        raise ValueError(
            f"Configuration error: {e}. "
            f"Please ensure Google OAuth client config and token paths are valid. "
            f"Current working directory: {os.getcwd()}. "
            f"GOOGLE_CLIENT_CONFIG: {os.getenv('GOOGLE_CLIENT_CONFIG')}, "
            f"GOOGLE_TOKEN_FILE: {os.getenv('GOOGLE_TOKEN_FILE')}, "
            f"DOWNLOAD_DIRECTORY: {os.getenv('DOWNLOAD_DIRECTORY')}"
        ) from e