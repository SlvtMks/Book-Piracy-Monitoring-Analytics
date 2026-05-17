from __future__ import annotations

import argparse
import sys
from datetime import datetime

from pirate_monitor.analytics import AnalyticsService
from pirate_monitor.gui import launch_gui
from pirate_monitor.http import HttpClient
from pirate_monitor.official_sources import official_source_choices, resolve_book_reference, resolve_source_reference, source_title
from pirate_monitor.service import MonitorOptions, MonitorService


SOURCE_CHOICES = official_source_choices()
MANUAL_SOURCE_CHOICES = [choice for choice in SOURCE_CHOICES if choice != "auto"]


def safe_print(message: str) -> None:
    print(message.encode("cp1251", errors="backslashreplace").decode("cp1251", errors="replace"))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Book piracy monitoring CLI")
    subparsers = parser.add_subparsers(dest="command", required=True)

    run_parser = subparsers.add_parser("run", help="Run one monitoring pass")
    run_parser.add_argument("--author-url", default=None, help="Official author page URL")
    run_parser.add_argument("--author-name", default=None, help="Author name for official-source lookup")
    run_parser.add_argument("--book-url", default=None, help="Direct URL of the official book page")
    run_parser.add_argument("--official-source", choices=SOURCE_CHOICES, default="auto")
    run_parser.add_argument("--config-path", default="config/target_sites.json")
    run_parser.add_argument("--output-dir", default="output/exports")
    run_parser.add_argument("--book-title", default=None, help="Exact book title")
    run_parser.add_argument("--max-books", type=int, default=None)
    run_parser.add_argument("--max-sites", type=int, default=None)
    run_parser.add_argument("--search-limit", type=int, default=3)
    run_parser.add_argument("--interval-minutes", type=int, default=None)
    run_parser.add_argument("--connect-timeout", type=int, default=10)
    run_parser.add_argument("--read-timeout", type=int, default=60)
    run_parser.add_argument("--max-retries", type=int, default=3)
    run_parser.add_argument("--retry-backoff-seconds", type=float, default=2.0)
    run_parser.add_argument("--site-workers", type=int, default=5, help="Parallel site workers")

    subparsers.add_parser("gui", help="Open local GUI")
    subparsers.add_parser("run-status", help="Show currently active run")

    history_parser = subparsers.add_parser("export-history", help="Export monitoring history")
    history_parser.add_argument("--output-dir", default="reports/exports")
    history_parser.add_argument("--official-source", choices=MANUAL_SOURCE_CHOICES, default=None)
    history_parser.add_argument("--author-name", default=None)
    history_parser.add_argument("--book-title", default=None)

    analytics_parser = subparsers.add_parser("analytics-report", help="Build analytics report")
    analytics_parser.add_argument("--output-dir", default="reports/analytics")
    analytics_parser.add_argument("--official-source", choices=MANUAL_SOURCE_CHOICES, default=None)
    analytics_parser.add_argument("--author-name", default=None)
    analytics_parser.add_argument("--book-title", default=None)

    inspect_parser = subparsers.add_parser("inspect-source", help="Inspect official source")
    inspect_parser.add_argument("--author-url", default=None, help="Official author page URL")
    inspect_parser.add_argument("--author-name", default=None, help="Author name for official-source lookup")
    inspect_parser.add_argument("--book-url", default=None, help="Direct URL of the official book page")
    inspect_parser.add_argument("--official-source", choices=SOURCE_CHOICES, default="auto")
    inspect_parser.add_argument("--book-title", default=None, help="Exact book title")
    inspect_parser.add_argument("--connect-timeout", type=int, default=10)
    inspect_parser.add_argument("--read-timeout", type=int, default=60)
    inspect_parser.add_argument("--max-retries", type=int, default=3)

    inspect_litnet = subparsers.add_parser("inspect-litnet", help="Inspect Litnet source")
    inspect_litnet.add_argument("--author-url", default=None, help="Litnet author URL")
    inspect_litnet.add_argument("--author-name", default=None, help="Litnet author name")
    inspect_litnet.add_argument("--book-title", default=None, help="Exact book title")
    inspect_litnet.add_argument("--connect-timeout", type=int, default=10)
    inspect_litnet.add_argument("--read-timeout", type=int, default=60)
    inspect_litnet.add_argument("--max-retries", type=int, default=3)
    return parser


def _build_http(args) -> HttpClient:
    return HttpClient(connect_timeout=args.connect_timeout, read_timeout=args.read_timeout, max_retries=args.max_retries)


def _filter_inspect_books(books, book_title: str | None):
    if not book_title:
        return books
    from pirate_monitor.normalization import normalize_text

    normalized_requested = normalize_text(book_title)
    return [book for book in books if normalize_text(book.title) == normalized_requested]


def _inspect_source(author_url: str | None, author_name: str | None, preferred_source: str, args) -> int:
    http = _build_http(args)
    if getattr(args, "book_url", None):
        source, resolved_author_url, resolved_book = resolve_book_reference(
            http,
            author_name=author_name,
            book_title=getattr(args, "book_title", None),
            preferred_source=preferred_source,
            book_url=args.book_url,
        )
        author_name = resolved_book.author_name or author_name
        books = [resolved_book]
        resolved_author_url = resolved_author_url or resolved_book.url
    elif not author_url and author_name and getattr(args, "book_title", None):
        storage = MonitorService().storage
        cached_author_url, cached_book = storage.find_recent_book_reference(preferred_source, author_name, args.book_title)
        if cached_book:
            source, resolved_author_url = resolve_source_reference(
                http,
                author_url=cached_author_url or cached_book.url,
                preferred_source=preferred_source,
            )
            author_name = cached_book.author_name or author_name
            books = [cached_book]
            resolved_author_url = resolved_author_url or cached_book.url
        else:
            source, resolved_author_url, resolved_book = resolve_book_reference(
                http,
                author_name=author_name,
                book_title=args.book_title,
                preferred_source=preferred_source,
            )
            author_name = resolved_book.author_name or author_name
            books = [resolved_book]
            resolved_author_url = resolved_author_url or resolved_book.url
    else:
        cached_author_url = None
        if not author_url and author_name and preferred_source != "auto":
            cached_author_url = MonitorService().storage.find_recent_author_url(preferred_source, author_name)
        source, resolved_author_url = resolve_source_reference(
            http,
            author_url=author_url or cached_author_url,
            author_name=author_name,
            preferred_source=preferred_source,
        )
        author_name, books = source.fetch_author_books(resolved_author_url)
    books = _filter_inspect_books(books, getattr(args, "book_title", None))
    safe_print(f"Source: {source_title(source.key)}")
    safe_print(f"Author: {author_name}")
    safe_print(f"Author page: {resolved_author_url}")
    safe_print(f"Books found: {len(books)}")
    for index, book in enumerate(books, start=1):
        safe_print(
            f"{index}. {book.title} | pages={book.page_count} | chars={book.char_count} | complete={book.is_complete} | paid={book.is_paid} | {book.url}"
        )
    return 0


def _show_run_status() -> int:
    active_run = MonitorService().storage.get_active_run()
    if not active_run:
        safe_print("No active runs.")
        return 0

    total_books = int(active_run["total_books"] or 0)
    processed_books = int(active_run["processed_books"] or 0)
    remaining_books = max(total_books - processed_books, 0)
    safe_print(
        f"Active run #{active_run['run_id']}. Source: {source_title(str(active_run['official_source']))}. Author: {active_run['author_name']}"
    )
    safe_print(f"Started: {active_run['started_at']}")
    safe_print(f"Processed books: {processed_books} of {total_books}")
    safe_print(f"Remaining books: {remaining_books}")
    if active_run.get("current_book_title"):
        safe_print(f"Current book: {active_run['current_book_title']}")
    return 0


def _validate_author_reference(args) -> None:
    author_url = (getattr(args, "author_url", None) or "").strip()
    author_name = (getattr(args, "author_name", None) or "").strip()
    book_url = (getattr(args, "book_url", None) or "").strip()
    if not author_url and not author_name and not book_url:
        raise SystemExit("Need --author-url, --author-name, or --book-url.")
    if author_name and not author_url and not book_url and getattr(args, "official_source", "auto") == "auto":
        raise SystemExit("When searching by author name, specify --official-source.")


def main() -> int:
    try:
        sys.stdout.reconfigure(errors="backslashreplace")
    except Exception:
        pass

    parser = build_parser()
    args = parser.parse_args()

    if args.command == "gui":
        return launch_gui()
    if args.command == "run-status":
        return _show_run_status()
    if args.command == "export-history":
        storage = MonitorService().storage
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        summary = storage.export_history_report(
            output_dir=args.output_dir,
            official_source=args.official_source,
            author_name=args.author_name,
            book_title=args.book_title,
            stamp=stamp,
        )
        safe_print(f"CSV: {summary['csv_path']}")
        safe_print(f"XLSX: {summary['xlsx_path']}")
        safe_print(f"Records: {summary['records_count']}")
        return 0
    if args.command == "analytics-report":
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        summary = AnalyticsService(MonitorService().storage).export_report(
            output_dir=args.output_dir,
            official_source=args.official_source,
            author_name=args.author_name,
            book_title=args.book_title,
            stamp=stamp,
        )
        for key, value in summary.items():
            if key == "tables":
                continue
            safe_print(f"{key}: {value}")
        safe_print("Report sections: " + ", ".join(summary["tables"].keys()))
        return 0
    if args.command == "inspect-source":
        _validate_author_reference(args)
        return _inspect_source(args.author_url, args.author_name, args.official_source, args)
    if args.command == "inspect-litnet":
        _validate_author_reference(args)
        return _inspect_source(args.author_url, args.author_name, "litnet", args)

    _validate_author_reference(args)
    options = MonitorOptions(
        author_url=args.author_url,
        author_name=args.author_name,
        book_url=getattr(args, "book_url", None),
        official_source=args.official_source,
        config_path=args.config_path,
        output_dir=args.output_dir,
        book_title=args.book_title,
        max_books=args.max_books,
        max_sites=args.max_sites,
        search_limit=args.search_limit,
        interval_minutes=args.interval_minutes,
        connect_timeout=args.connect_timeout,
        read_timeout=args.read_timeout,
        max_retries=args.max_retries,
        retry_backoff_seconds=args.retry_backoff_seconds,
        site_workers=args.site_workers,
    )

    service = MonitorService()
    if options.interval_minutes:
        service.run_forever(options, logger=safe_print)
        return 0

    summary = service.run_once(options, logger=safe_print)
    safe_print(f"CSV: {summary.csv_path}")
    safe_print(f"XLSX: {summary.xlsx_path}")
    return 0
