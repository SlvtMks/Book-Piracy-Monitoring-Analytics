from __future__ import annotations

from collections import defaultdict
from typing import Iterable

from .models import OfficialBook, PirateRecord


def _effective_volume(record: PirateRecord) -> int | None:
    return record.char_count or None


def _official_volume(book: OfficialBook) -> int | None:
    return book.char_count or None


def classify_record(book: OfficialBook, record: PirateRecord) -> str:
    if not record.available:
        return "removed"

    raw = (record.raw_status or "").casefold()
    if "removed" in raw:
        return "removed"
    if "fragment" in raw or "ongoing" in raw or "blocked" in raw:
        return "partial"

    official_volume = _official_volume(book)
    pirate_volume = _effective_volume(record)

    if record.publication_confirmed and record.publication_source == "download" and pirate_volume:
        if official_volume:
            ratio = pirate_volume / official_volume
            return "full" if ratio >= 0.9 else "partial"
        return "full"

    if official_volume and pirate_volume:
        ratio = pirate_volume / official_volume
        if ratio >= 0.9:
            return "full"
        return "partial"

    if record.publication_confirmed and pirate_volume:
        return "partial"

    if pirate_volume:
        return "partial"

    return "unknown"


def apply_reposted_status(records: Iterable[PirateRecord]) -> None:
    key_urls: dict[tuple[str, str], set[str]] = defaultdict(set)
    for record in records:
        key_urls[(record.domain, record.source_title.casefold())].add(record.book_url)

    for record in records:
        if len(key_urls[(record.domain, record.source_title.casefold())]) > 1:
            if record.assigned_status in {"full", "partial", "unknown"}:
                record.assigned_status = "reposted"
