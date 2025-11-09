"""Pydantic models for MCP tool return types"""

from typing import List, Dict, Any
from pydantic import BaseModel, Field


class LoadFileResponse(BaseModel):
    """Response model for load_file tool"""
    file_id: str = Field(description="The Google Drive file ID")
    status: str = Field(description="Status of the operation (e.g., 'success', 'already loaded')")
    message: str = Field(description="Human-readable message about the operation")


class ColumnInfo(BaseModel):
    """Information about a single column in a DataFrame"""
    name: str = Field(description="Column name")
    dtype: str = Field(description="Data type of the column")
    non_null_count: int = Field(description="Number of non-null values")
    null_count: int = Field(description="Number of null values")


class ShapeInfo(BaseModel):
    """Shape information for a sheet"""
    rows: int = Field(description="Number of rows")
    columns: int = Field(description="Number of columns")


class SheetInfo(BaseModel):
    """Information about a single sheet in a file"""
    sheet_number: int = Field(description="0-based index of the sheet")
    sheet_name: str = Field(description="Name of the sheet")
    shape: ShapeInfo = Field(description="Dimensions of the sheet")
    columns: List[ColumnInfo] = Field(description="Information about each column")
    memory_usage_bytes: int = Field(description="Memory usage of this sheet in bytes")


class InfoResponse(BaseModel):
    """Response model for info tool"""
    status: str = Field(description="Status of the operation")
    file_id: str = Field(description="The Google Drive file ID")
    num_sheets: int = Field(description="Number of non-empty sheets in the file")
    sheets: List[SheetInfo] = Field(description="Detailed information about each sheet")
    total_memory_usage_bytes: int = Field(description="Total memory usage of all sheets in bytes")


class GetRowsCsvResponse(BaseModel):
    """Response model for get_rows_csv tool"""
    status: str = Field(description="Status of the operation")
    file_id: str = Field(description="The Google Drive file ID")
    sheet_number: int = Field(description="Sheet number that was queried")
    start: int = Field(description="Starting row index (0-based, inclusive)")
    end: int = Field(description="Ending row index (exclusive)")
    rows_returned: int = Field(description="Number of rows returned")
    total_rows_in_sheet: int = Field(description="Total number of rows in the sheet")
    csv: str = Field(description="CSV-formatted text of the requested rows")


class QueryFileResponse(BaseModel):
    """Response model for query_file tool"""
    status: str = Field(description="Status of the operation")
    columns: List[str] = Field(description="Column names in the query result")
    rows: List[List[Any]] = Field(description="Query result rows as nested arrays")

