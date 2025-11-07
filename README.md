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

### `load_file`
Download and load a file from Google Drive (background operation).

**Parameters:**
- `file_id` (string): The Google Drive file ID to download

**Returns:** Dictionary with file_id, status, and message

**Example:**
```python
load_file("1KliqOOx9hdU6fOJ0oQznuctvF3AphcJU")
# → {file_id: "...", status: "downloading", message: "Download started..."}
```

### `info`
Get metadata about a loaded file's DataFrame structure.

**Parameters:**
- `file_id` (string): The Google Drive file ID

**Returns:** Shape, columns, data types, null counts, memory usage

**Example:**
```python
info("1KliqOOx9hdU6fOJ0oQznuctvF3AphcJU")
# Returns: {status, file_id, shape: {rows, columns}, columns: [{name, dtype, non_null_count, null_count}], memory_usage_bytes}
```

### `get_rows_csv`
Export rows from a loaded file as CSV format.

**Parameters:**
- `file_id` (string): The Google Drive file ID
- `start` (int, optional): Starting row index (0-based, default: 0)
- `end` (int, optional): Ending row index (exclusive, default: all rows)

**Returns:** CSV string with specified rows

**Examples:**
```python
# Get all rows
get_rows_csv("1KliqOOx9hdU6fOJ0oQznuctvF3AphcJU")

# Get first 100 rows
get_rows_csv("1KliqOOx9hdU6fOJ0oQznuctvF3AphcJU", 0, 100)

# Get rows 50-150
get_rows_csv("1KliqOOx9hdU6fOJ0oQznuctvF3AphcJU", 50, 150)
```

### `query_file`
Run SQL queries on loaded files using DuckDB.

**Parameters:**
- `file_id` (string): The Google Drive file ID
- `sql_query` (string): SQL query to execute (use 'data' as the table name)

**Returns:** Query results with columns and rows

**Examples:**
```python
# Select all rows
query_file("1KliqOOx9hdU6fOJ0oQznuctvF3AphcJU", "SELECT * FROM data LIMIT 10")

# Aggregation query
query_file("1KliqOOx9hdU6fOJ0oQznuctvF3AphcJU", "SELECT column1, COUNT(*) as count FROM data GROUP BY column1")

# Filter query
query_file("1KliqOOx9hdU6fOJ0oQznuctvF3AphcJU", "SELECT * FROM data WHERE column1 > 100 ORDER BY column2 DESC")
```

### `unload_file`
Remove a downloaded file from local storage and clear from memory.

**Parameters:**
- `file_id` (string): The Google Drive file ID to unload

**Returns:** Success message (idempotent - succeeds even if file doesn't exist)

**Example:**
```python
unload_file("1KliqOOx9hdU6fOJ0oQznuctvF3AphcJU")
# → "File 1KliqOOx9hdU6fOJ0oQznuctvF3AphcJU was unloaded successfully"
```
