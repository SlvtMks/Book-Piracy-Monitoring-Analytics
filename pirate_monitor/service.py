from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable

from pirate_monitor.models import OfficialBook, PirateRecord, RunSummary
from pirate_monitor.normalization import looks_like_catalog_title, normalize_text, title_contains_book_name

from pirate_monitor.exporters import export_csv, export_xlsx
from pirate_monitor.http import HttpClient
from pirate_monitor.official_sources import resolve_book_reference, resolve_source_reference, source_title
from pirate_monitor.search import BingSearchProvider, SearchCoordinator, YandexSearchProvider
from pirate_monitor.status import apply_reposted_status, classify_record
from pirate_monitor.storage import Storage
from pirate_monitor.targets import TargetPageParser, load_target_sites


LogFn = Callable[[str], None] | None


def log_message(logger: LogFn, message: str) -> None:
    if logger is not None:
        logger(message)


@dataclass(slots=True)
class MonitorOptions:
    author_url: str | None = None
    author_name: str | None = None
    book_url: str | None = None
    official_source: str = "auto"
    config_path: str = "config/target_sites.json"
    output_dir: str = "output/exports"
    book_title: str | None = None
    max_books: int | None = None
    max_sites: int | None = None
    search_limit: int = 3
    interval_minutes: int | None = None
    min_delay_seconds: float = 1.0
    connect_timeout: int = 10
    read_timeout: int = 60
    max_retries: int = 3
    retry_backoff_seconds: float = 2.0
    site_workers: int = 5


class MonitorService:
    def __init__(self, storage: Storage | None = None) -> None:
        self.storage = storage or Storage()

    def run_once(self, options: MonitorOptions, logger: LogFn = None) -> RunSummary:
        started_at = datetime.now()
        http = self._build_http(options)
        sites = [site for site in load_target_sites(options.config_path) if site.enabled]
        if options.max_sites:
            sites = sites[: options.max_sites]

        resolved_book: OfficialBook | None = None
        if options.book_url:
            source, resolved_author_url, resolved_book = resolve_book_reference(
                http,
                author_name=options.author_name,
                book_title=options.book_title,
                preferred_source=options.official_source,
                book_url=options.book_url,
            )
            author_name = resolved_book.author_name or options.author_name or ""
            books = [resolved_book]
            if not resolved_author_url:
                resolved_author_url = resolved_book.url
        elif not options.author_url and options.author_name and options.book_title:
            cached_author_url, cached_book = self.storage.find_recent_book_reference(
                options.official_source,
                options.author_name,
                options.book_title,
            )
            if cached_book:
                source, resolved_author_url = resolve_source_reference(
                    http,
                    author_url=cached_author_url or cached_book.url,
                    preferred_source=options.official_source,
                )
                author_name = cached_book.author_name or options.author_name
                books = [cached_book]
                if not resolved_author_url:
                    resolved_author_url = cached_book.url
            else:
                source, resolved_author_url, resolved_book = resolve_book_reference(
                    http,
                    author_name=options.author_name,
                    book_title=options.book_title,
                    preferred_source=options.official_source,
                )
                author_name = resolved_book.author_name or options.author_name
                books = [resolved_book]
                if not resolved_author_url:
                    resolved_author_url = resolved_book.url
        else:
            cached_author_url = None
            if not options.author_url and options.author_name and options.official_source != "auto":
                cached_author_url = self.storage.find_recent_author_url(options.official_source, options.author_name)
            source, resolved_author_url = resolve_source_reference(
                http,
                author_url=options.author_url or cached_author_url,
                author_name=options.author_name,
                preferred_source=options.official_source,
            )
            fetch_limit = None if options.book_title else options.max_books
            try:
                author_name, books = source.fetch_author_books(resolved_author_url, max_books=fetch_limit)
            except TypeError:
                author_name, books = source.fetch_author_books(resolved_author_url)
                if fetch_limit:
                    books = books[: fetch_limit]

            books = self._filter_books(books, options.book_title)
        if options.max_books and books:
            books = books[: options.max_books]
        if not books:
            raise RuntimeError("По указанному автору и книге не удалось получить список произведений для проверки.")

        run_id = self.storage.create_run(
            author_name,
            resolved_author_url,
            started_at,
            official_source=source.key,
            total_books=len(books),
        )
        self.storage.save_official_books(run_id, books)
        log_message(
            logger,
            f"Запуск #{run_id}. Источник: {source_title(source.key)}. Автор: {author_name}. Книг: {len(books)}. Сайтов: {len(sites)}.",
        )

        records: list[PirateRecord] = []
        site_workers = max(1, min(options.site_workers or 1, len(sites) or 1))
        for index, book in enumerate(books, start=1):
            self.storage.update_run_progress(run_id, index - 1, book.title)
            log_message(logger, f"Проверяю книгу {index}/{len(books)}: {book.title}")

            if site_workers <= 1 or len(sites) <= 1:
                for site in sites:
                    site_records, site_logs = self._process_site_with_fresh_worker(
                        base_http=http,
                        site=site,
                        book=book,
                        author_name=author_name,
                        search_limit=options.search_limit,
                    )
                    for message in site_logs:
                        log_message(logger, message)
                    records.extend(site_records)
            else:
                with ThreadPoolExecutor(max_workers=site_workers) as executor:
                    future_map = {
                        executor.submit(
                            self._process_site_with_fresh_worker,
                            base_http=http,
                            site=site,
                            book=book,
                            author_name=author_name,
                            search_limit=options.search_limit,
                        ): site
                        for site in sites
                    }
                    for future in as_completed(future_map):
                        site_records, site_logs = future.result()
                        for message in site_logs:
                            log_message(logger, message)
                        records.extend(site_records)

            self.storage.update_run_progress(run_id, index, book.title)

        apply_reposted_status(records)
        self.storage.save_pirate_records(run_id, records)

        finished_at = datetime.now()
        self.storage.finish_run(run_id, finished_at)

        stamp = finished_at.strftime("%Y%m%d_%H%M%S")
        base_name = f"{self._slug(author_name)}_{source.key}_{stamp}"
        output_dir = Path(options.output_dir)
        csv_path = export_csv(records, output_dir / f"{base_name}.csv")
        xlsx_path = export_xlsx(records, output_dir / f"{base_name}.xlsx")
        log_message(logger, f"Готово. Найдено записей: {len(records)}")

        return RunSummary(
            run_id=run_id,
            official_source=source.key,
            author_name=author_name,
            author_url=resolved_author_url,
            started_at=started_at,
            finished_at=finished_at,
            official_books_count=len(books),
            findings_count=len(records),
            csv_path=csv_path,
            xlsx_path=xlsx_path,
        )

    def run_forever(self, options: MonitorOptions, logger: LogFn = None) -> None:
        if not options.interval_minutes or options.interval_minutes <= 0:
            raise ValueError("Для запуска по расписанию interval_minutes должен быть больше 0.")

        while True:
            self.run_once(options, logger=logger)
            log_message(logger, f"Следующий запуск через {options.interval_minutes} мин.")
            time.sleep(options.interval_minutes * 60)

    def _process_site_with_fresh_worker(
        self,
        *,
        base_http: HttpClient,
        site,
        book: OfficialBook,
        author_name: str,
        search_limit: int,
    ) -> tuple[list[PirateRecord], list[str]]:
        logs: list[str] = []
        records: list[PirateRecord] = []
        http = base_http.clone()
        search = SearchCoordinator(providers=[YandexSearchProvider(http), BingSearchProvider(http)], http=http)
        target_parser = TargetPageParser(http)

        try:
            hits, attempts = search.search(author_name, book.title, site, limit=search_limit)
            if not hits:
                logs.append(f"[{site.name}] не найдено. Запросы: {' | '.join(attempts)}")
                return records, logs

            logs.append(f"[{site.name}] найдено ссылок: {len(hits)}")
            for hit in hits:
                record = target_parser.parse(site, hit, author_name, book.title, book.url)
                if not self._is_relevant_record(book.title, record.page_title):
                    logs.append(f"[{site.name}] отфильтровано нерелевантное совпадение: {record.page_title}")
                    continue
                record.official_source = book.source
                record.official_page_count = book.page_count
                record.official_char_count = book.char_count
                record.official_last_update_raw = book.last_update_raw
                record.official_is_complete = book.is_complete
                record.official_is_paid = book.is_paid
                record.assigned_status = classify_record(book, record)
                if attempts:
                    record.notes.append(f"Поисковые запросы: {' | '.join(attempts)}")
                records.append(record)
        except Exception as exc:  # noqa: BLE001
            logs.append(f"[{site.name}] ошибка: {exc}")
        return records, logs

    def _is_relevant_record(self, source_title: str, candidate_title: str) -> bool:
        # Prefer an explicit title match before applying broad catalog heuristics.
        # This keeps safe suffixes like "(СИ)" or "читать онлайн" relevant.
        if title_contains_book_name(source_title, candidate_title):
            return True
        if looks_like_catalog_title(candidate_title):
            return False
        return False

    def _build_http(self, options: MonitorOptions) -> HttpClient:
        return HttpClient(
            connect_timeout=options.connect_timeout,
            read_timeout=options.read_timeout,
            min_delay_seconds=options.min_delay_seconds,
            max_retries=options.max_retries,
            retry_backoff_seconds=options.retry_backoff_seconds,
        )

    def _filter_books(self, books: list[OfficialBook], requested_title: str | None) -> list[OfficialBook]:
        if not requested_title:
            return books

        normalized_requested = normalize_text(requested_title)
        return [book for book in books if normalize_text(book.title) == normalized_requested]

    def _slug(self, value: str) -> str:
        chars: list[str] = []
        for char in value.casefold():
            if char.isalnum():
                chars.append(char)
            elif chars and chars[-1] != "_":
                chars.append("_")
        return "".join(chars).strip("_") or "author"


