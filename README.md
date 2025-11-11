# Tables MCP Server

A Model Context Protocol (MCP) server that enables AI assistants to load, query, and analyze tabular data files from Google Drive. Load spreadsheets and CSV files, run SQL queries with DuckDB, and work with data in-memory using pandas.

## Quick Setup

### 1. Install dependencies

Using uv (recommended):
```bash
uv sync
```

### 2. Set up Google Cloud OAuth

1. Go to [Google Cloud Console](https://console.cloud.google.com/)
2. Create a new project or select an existing one
3. Enable the **Google Drive API**
4. Go to **Credentials** → **Create Credentials** → **OAuth client ID**
5. Choose **Desktop app** as the application type
6. Download the JSON file and save it as `credentials/client_secrets.json`

### 3. Configure environment

```bash
cp env.example .env
```

Edit `.env` to customize settings (optional - defaults work for most cases).

### 4. Run the server

First-time setup (authenticates with Google):
```bash
uv run python main.py
```
- Browser opens for Google authentication
- Grants access to Google Drive
- Saves tokens for future use

Run with MCP Inspector for testing:
```bash
uv run mcp dev main.py
```

**Note:** Run `python main.py` once first to complete authentication before using `mcp dev`.

## Available MCP Tools

All tools automatically download files from Google Drive when needed. Files are cached locally and automatically cleaned up after 10 minutes of inactivity by a background thread.

### `info`
Get metadata about a file's DataFrame structure. Automatically downloads the file if not already cached.

**Parameters:**
- `file_id` (string): The Google Drive file ID

**Returns:** Shape, columns, data types, null counts, memory usage, sheet information

**Example:**
```python
info("1KliqOOx9hdU6fOJ0oQznuctvF3AphcJU")
# Returns: {status, file_id, num_sheets, sheets: [{sheet_number, sheet_name, shape: {rows, columns}, columns: [{name, dtype, non_null_count, null_count}], memory_usage_bytes}], total_memory_usage_bytes}
```

### `get_rows_csv`
Export rows from a file as CSV format. Automatically downloads the file if not already cached.

**Parameters:**
- `file_id` (string): The Google Drive file ID
- `start` (int, optional): Starting row index (0-based, default: 0)
- `end` (int, optional): Ending row index (exclusive, default: all rows)
- `sheet_number` (int, optional): Sheet number for Excel files (0-based, default: 0)

**Returns:** CSV string with specified rows (max 100 rows per call)

**Examples:**
```python
# Get first 10 rows from sheet 0
get_rows_csv("1KliqOOx9hdU6fOJ0oQznuctvF3AphcJU", 0, 10)

# Get rows 50-100 from sheet 1
get_rows_csv("1KliqOOx9hdU6fOJ0oQznuctvF3AphcJU", 50, 100, 1)
```

### `query_file`
Run SQL queries on files using DuckDB. Automatically downloads the file if not already cached.

**Parameters:**
- `file_id` (string): The Google Drive file ID
- `sql_query` (string): SQL query to execute (use 'data' or 'data_0' for single sheets, 'data_0', 'data_1', etc. for Excel files with multiple sheets)

**Returns:** Query results with columns and rows (max 100 rows per query)

**Examples:**
```python
# Select from single-sheet file or first sheet
query_file("1KliqOOx9hdU6fOJ0oQznuctvF3AphcJU", "SELECT * FROM data LIMIT 10")

# Query specific sheet in Excel file
query_file("1KliqOOx9hdU6fOJ0oQznuctvF3AphcJU", "SELECT * FROM data_1 WHERE column1 > 100")

# Join multiple sheets
query_file("1KliqOOx9hdU6fOJ0oQznuctvF3AphcJU", "SELECT * FROM data_0 JOIN data_1 ON data_0.id = data_1.id")

# Aggregation query
query_file("1KliqOOx9hdU6fOJ0oQznuctvF3AphcJU", "SELECT column1, COUNT(*) as count FROM data GROUP BY column1")
```

## Automatic File Management

- **Auto-download:** Files are automatically downloaded from Google Drive when you call `info`, `get_rows_csv`, or `query_file`
- **Caching:** Downloaded files are cached locally for performance
- **Auto-cleanup:** A background thread removes files that haven't been accessed for 10 minutes
- **Memory management:** DataFrames are loaded into memory on demand and tracked for cleanup
