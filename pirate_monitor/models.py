from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional


@dataclass(slots=True)
class TargetSite:
    name: str
    base_url: str
    complaint_format: str
    parser: str = "generic"
    enabled: bool = True

    @property
    def domain(self) -> str:
        from urllib.parse import urlparse

        return urlparse(self.base_url).netloc.lower().removeprefix("www.")


@dataclass(slots=True)
class OfficialBook:
    author_name: str
    title: str
    url: str
    source: str = "litnet"
    page_count: Optional[int] = None
    char_count: Optional[int] = None
    last_update_raw: Optional[str] = None
    is_complete: Optional[bool] = None
    is_paid: Optional[bool] = None
    raw_text: str = ""


@dataclass(slots=True)
class SearchHit:
    site_name: str
    domain: str
    query: str
    url: str
    title: str
    snippet: str
    provider: str
    rank: int


@dataclass(slots=True)
class PirateRecord:
    site_name: str
    domain: str
    source_author: str
    source_title: str
    official_url: str
    book_url: str
    discovered_at: datetime
    page_title: str
    official_source: str = "litnet"
    official_page_count: Optional[int] = None
    official_char_count: Optional[int] = None
    official_last_update_raw: Optional[str] = None
    official_is_complete: Optional[bool] = None
    official_is_paid: Optional[bool] = None
    page_author: Optional[str] = None
    page_count: Optional[int] = None
    char_count: Optional[int] = None
    publication_confirmed: bool = False
    publication_source: Optional[str] = None
    publication_format: Optional[str] = None
    publication_url: Optional[str] = None
    file_size_kb: Optional[int] = None
    last_update_raw: Optional[str] = None
    raw_status: Optional[str] = None
    snippet: str = ""
    search_query: str = ""
    provider: str = ""
    match_score: float = 0.0
    assigned_status: str = "unknown"
    available: bool = True
    notes: list[str] = field(default_factory=list)


@dataclass(slots=True)
class RunSummary:
    run_id: int
    official_source: str
    author_name: str
    author_url: str
    started_at: datetime
    finished_at: datetime
    official_books_count: int
    findings_count: int
    csv_path: str
    xlsx_path: str

