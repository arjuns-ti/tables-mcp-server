# Tables MCP Server

A Python MCP server for Google Drive file management with automatic token refresh.

## Features

- ✅ List, search, and download files from Google Drive
- ✅ Support for Shared Drives (Team Drives)
- ✅ **Automatic token refresh** - no manual re-authentication needed
- ✅ File size limits and download management

## Quick Setup

1. **Install dependencies**
   ```bash
   pip install -e .
   ```

2. **Set up Google Cloud OAuth**
   - Go to [Google Cloud Console](https://console.cloud.google.com/)
   - Enable Google Drive API
   - Create OAuth Desktop App credentials
   - Download JSON and save as `credentials/client_secrets.json`

3. **Configure environment**
   ```bash
   cp env.example .env
   ```

4. **Run the server**
   ```bash
   python main.py
   ```
   - First run: Browser opens for Google authentication
   - Subsequent runs: Uses saved tokens (auto-refreshes when expired)

## Available MCP Tools

- `list_drive_files` - List files from Drive
- `search_drive_files` - Search by filename
- `download_drive_file` - Download a file
- `get_file_info` - Get file metadata
- `list_shared_drives` - List Shared Drives
- `ping` - Check connection

## Token Management

**Automatic refresh** - no user intervention required:
- Access tokens refresh automatically when expired (~1 hour)
- Refresh tokens valid for 6+ months
- Re-authentication only needed if refresh token expires or is revoked

## Configuration

Edit `.env` to customize:
```bash
GOOGLE_OAUTH_PORT=8765
DOWNLOAD_DIRECTORY=downloads
MAX_FILE_SIZE_MB=100
ENABLE_SHARED_DRIVES=true
LOGGING_LEVEL=INFO  # DEBUG, INFO, WARNING, ERROR, CRITICAL
```

Logs are automatically saved to `logs.txt`

## Troubleshooting

**Authentication issues:**
```bash
rm .gcp-saved-tokens.json
python main.py
```

**Port in use:** Change `GOOGLE_OAUTH_PORT` in `.env`

## Security

Never commit (already in `.gitignore`):
- `credentials/client_secrets.json`
- `.gcp-saved-tokens.json`
- `.env`
- `logs.txt`
