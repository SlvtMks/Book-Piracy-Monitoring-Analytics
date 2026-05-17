from __future__ import annotations

import shutil
from datetime import datetime
from pathlib import Path

from pirate_monitor.models import OfficialBook, PirateRecord
from pirate_monitor.analytics import AnalyticsService
from pirate_monitor.storage import Storage


def _record(
    *,
    site_name: str,
    title: str,
    status: str,
    confirmed: bool = True,
    author_name: str = "Арина Арская",
) -> PirateRecord:
    return PirateRecord(
        site_name=site_name,
        domain=site_name.casefold(),
        source_author=author_name,
        source_title=title,
        official_url=f"https://official/{title}",
        book_url=f"https://pirate/{site_name}/{title}",
        discovered_at=datetime(2026, 4, 2, 10, 0, 0),
        page_title=title,
        assigned_status=status,
        publication_confirmed=confirmed,
        available=status != "удален",
    )


def _make_test_dir(name: str) -> Path:
    base_dir = Path("tests_tmp") / name
    if base_dir.exists():
        shutil.rmtree(base_dir)
    base_dir.mkdir(parents=True, exist_ok=True)
    return base_dir


def test_build_summary_aggregates_key_metrics():
    tmp_dir = _make_test_dir("analytics_summary")
    storage = Storage(tmp_dir / "monitor.db")
    run_id = storage.create_run(
        author_name="Арина Арская",
        author_url="https://litnet/arina",
        started_at=datetime(2026, 4, 2, 9, 0, 0),
        official_source="litnet",
        total_books=2,
    )
    storage.save_official_books(
        run_id,
        [
            OfficialBook(author_name="Арина Арская", title="Книга 1", url="https://official/1", source="litnet", char_count=1000),
            OfficialBook(author_name="Арина Арская", title="Книга 2", url="https://official/2", source="litnet", char_count=2000),
        ],
    )
    storage.save_pirate_records(
        run_id,
        [
            _record(site_name="Литмир", title="Книга 1", status="опубликован полностью"),
            _record(site_name="Ридли", title="Книга 1", status="опубликован частично"),
            _record(site_name="Рулит", title="Книга 2", status="удален", confirmed=False),
            _record(site_name="Литмир", title="Книга 2", status="опубликован повторно"),
        ],
    )
    storage.finish_run(run_id, datetime(2026, 4, 2, 9, 30, 0))

    service = AnalyticsService(storage)
    summary = service.build_summary(author_name="Арина Арская")

    assert summary.authors_count == 1
    assert summary.books_count == 2
    assert summary.active_sites_count == 3
    assert summary.findings_count == 4
    assert summary.confirmed_findings_count == 3
    assert summary.full_publication_count == 1
    assert summary.partial_publication_count == 1
    assert summary.removed_count == 1
    assert summary.reposted_count == 1
    assert summary.top_sites[0]["site_name"] == "Литмир"


def test_export_summary_rows_returns_named_tables():
    tmp_dir = _make_test_dir("analytics_export")
    storage = Storage(tmp_dir / "monitor.db")
    run_id = storage.create_run(
        author_name="Арина Арская",
        author_url="https://litnet/arina",
        started_at=datetime(2026, 4, 2, 9, 0, 0),
        official_source="litnet",
        total_books=1,
    )
    storage.save_official_books(
        run_id,
        [OfficialBook(author_name="Арина Арская", title="Книга 1", url="https://official/1", source="litnet", char_count=1000)],
    )
    storage.save_pirate_records(run_id, [_record(site_name="Литмир", title="Книга 1", status="опубликован частично")])
    storage.finish_run(run_id, datetime(2026, 4, 2, 9, 30, 0))

    service = AnalyticsService(storage)
    tables = service.export_summary_rows(author_name="Арина Арская")

    assert set(tables) == {"overview", "top_sites", "top_authors", "top_books"}
    assert tables["overview"][0]["metric"] == "authors_count"
