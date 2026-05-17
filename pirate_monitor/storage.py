from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path

from pirate_monitor.models import OfficialBook, PirateRecord
from pirate_monitor.normalization import clean_spaces, normalize_text
from pirate_monitor.exporters import export_history_csv, export_history_xlsx


class Storage:
    def __init__(self, db_path: str | Path = "data/monitor.db") -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.db_path)

    def _init_db(self) -> None:
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS runs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    official_source TEXT NOT NULL DEFAULT 'litnet',
                    author_name TEXT NOT NULL,
                    author_url TEXT NOT NULL,
                    started_at TEXT NOT NULL,
                    finished_at TEXT,
                    total_books INTEGER NOT NULL DEFAULT 0,
                    processed_books INTEGER NOT NULL DEFAULT 0,
                    current_book_title TEXT
                );

                CREATE TABLE IF NOT EXISTS official_books (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id INTEGER NOT NULL,
                    source TEXT NOT NULL DEFAULT 'litnet',
                    author_name TEXT NOT NULL,
                    title TEXT NOT NULL,
                    url TEXT NOT NULL,
                    page_count INTEGER,
                    char_count INTEGER,
                    last_update_raw TEXT,
                    is_complete INTEGER,
                    is_paid INTEGER,
                    raw_text TEXT,
                    FOREIGN KEY(run_id) REFERENCES runs(id)
                );

                CREATE TABLE IF NOT EXISTS pirate_copies (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id INTEGER NOT NULL,
                    site_name TEXT NOT NULL,
                    domain TEXT NOT NULL,
                    source_author TEXT NOT NULL,
                    source_title TEXT NOT NULL,
                    official_url TEXT NOT NULL,
                    book_url TEXT NOT NULL,
                    discovered_at TEXT NOT NULL,
                    page_title TEXT NOT NULL,
                    page_author TEXT,
                    page_count INTEGER,
                    char_count INTEGER,
                    publication_confirmed INTEGER,
                    publication_source TEXT,
                    publication_format TEXT,
                    publication_url TEXT,
                    file_size_kb INTEGER,
                    last_update_raw TEXT,
                    raw_status TEXT,
                    snippet TEXT,
                    search_query TEXT,
                    provider TEXT,
                    match_score REAL,
                    assigned_status TEXT,
                    available INTEGER NOT NULL,
                    notes TEXT,
                    FOREIGN KEY(run_id) REFERENCES runs(id)
                );
                """
            )
            self._ensure_column(connection, "runs", "official_source", "TEXT NOT NULL DEFAULT 'litnet'")
            self._ensure_column(connection, "runs", "total_books", "INTEGER NOT NULL DEFAULT 0")
            self._ensure_column(connection, "runs", "processed_books", "INTEGER NOT NULL DEFAULT 0")
            self._ensure_column(connection, "runs", "current_book_title", "TEXT")
            self._ensure_column(connection, "official_books", "source", "TEXT NOT NULL DEFAULT 'litnet'")
            self._ensure_column(connection, "pirate_copies", "publication_confirmed", "INTEGER NOT NULL DEFAULT 0")
            self._ensure_column(connection, "pirate_copies", "publication_source", "TEXT")
            self._ensure_column(connection, "pirate_copies", "publication_format", "TEXT")
            self._ensure_column(connection, "pirate_copies", "publication_url", "TEXT")

    def _ensure_column(self, connection: sqlite3.Connection, table_name: str, column_name: str, ddl: str) -> None:
        columns = {row[1] for row in connection.execute(f"PRAGMA table_info({table_name})")}
        if column_name not in columns:
            connection.execute(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {ddl}")

    def create_run(
        self,
        author_name: str,
        author_url: str,
        started_at: datetime,
        official_source: str = "litnet",
        total_books: int = 0,
    ) -> int:
        with self._connect() as connection:
            cursor = connection.execute(
                """
                INSERT INTO runs (official_source, author_name, author_url, started_at, total_books, processed_books, current_book_title)
                VALUES (?, ?, ?, ?, ?, 0, NULL)
                """,
                (official_source, author_name, author_url, started_at.isoformat(), total_books),
            )
            return int(cursor.lastrowid)

    def update_run_progress(self, run_id: int, processed_books: int, current_book_title: str | None) -> None:
        with self._connect() as connection:
            connection.execute(
                "UPDATE runs SET processed_books = ?, current_book_title = ? WHERE id = ?",
                (processed_books, current_book_title, run_id),
            )

    def finish_run(self, run_id: int, finished_at: datetime) -> None:
        with self._connect() as connection:
            connection.execute(
                "UPDATE runs SET finished_at = ?, current_book_title = NULL WHERE id = ?",
                (finished_at.isoformat(), run_id),
            )

    def save_official_books(self, run_id: int, books: list[OfficialBook]) -> None:
        with self._connect() as connection:
            connection.executemany(
                """
                INSERT INTO official_books (
                    run_id, source, author_name, title, url, page_count, char_count,
                    last_update_raw, is_complete, is_paid, raw_text
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        run_id,
                        book.source,
                        book.author_name,
                        book.title,
                        book.url,
                        book.page_count,
                        book.char_count,
                        book.last_update_raw,
                        int(bool(book.is_complete)) if book.is_complete is not None else None,
                        int(bool(book.is_paid)) if book.is_paid is not None else None,
                        book.raw_text,
                    )
                    for book in books
                ],
            )

    def save_pirate_records(self, run_id: int, records: list[PirateRecord]) -> None:
        with self._connect() as connection:
            connection.executemany(
                """
                INSERT INTO pirate_copies (
                    run_id, site_name, domain, source_author, source_title, official_url,
                    book_url, discovered_at, page_title, page_author, page_count, char_count,
                    publication_confirmed, publication_source, publication_format, publication_url,
                    file_size_kb, last_update_raw, raw_status, snippet, search_query, provider,
                    match_score, assigned_status, available, notes
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        run_id,
                        record.site_name,
                        record.domain,
                        record.source_author,
                        record.source_title,
                        record.official_url,
                        record.book_url,
                        record.discovered_at.isoformat(),
                        record.page_title,
                        record.page_author,
                        record.page_count,
                        record.char_count,
                        int(bool(record.publication_confirmed)),
                        record.publication_source,
                        record.publication_format,
                        record.publication_url,
                        record.file_size_kb,
                        record.last_update_raw,
                        record.raw_status,
                        record.snippet,
                        record.search_query,
                        record.provider,
                        record.match_score,
                        record.assigned_status,
                        int(record.available),
                        " | ".join(record.notes),
                    )
                    for record in records
                ],
            )

    def get_active_run(self) -> dict[str, object] | None:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT id, official_source, author_name, author_url, started_at, total_books, processed_books, current_book_title
                FROM runs
                WHERE finished_at IS NULL AND (total_books > 0 OR processed_books > 0 OR current_book_title IS NOT NULL)
                ORDER BY id DESC
                LIMIT 1
                """
            ).fetchone()
        if not row:
            return None
        return {
            "run_id": row[0],
            "official_source": row[1],
            "author_name": row[2],
            "author_url": row[3],
            "started_at": row[4],
            "total_books": row[5],
            "processed_books": row[6],
            "current_book_title": row[7],
        }

    def load_history_rows(
        self,
        *,
        official_source: str | None = None,
        author_name: str | None = None,
        book_title: str | None = None,
    ) -> list[dict[str, object]]:
        clauses: list[str] = []
        params: list[object] = []

        if official_source:
            clauses.append("r.official_source = ?")
            params.append(official_source)

        query = """
            SELECT
                pc.discovered_at,
                pc.source_author,
                pc.source_title,
                r.official_source,
                pc.official_url,
                ob.page_count,
                ob.char_count,
                pc.char_count,
                pc.publication_confirmed,
                pc.publication_source,
                pc.publication_format,
                pc.publication_url,
                pc.site_name,
                pc.page_title,
                pc.book_url,
                pc.last_update_raw,
                pc.assigned_status
            FROM pirate_copies pc
            JOIN runs r ON r.id = pc.run_id
            LEFT JOIN official_books ob
                ON ob.run_id = pc.run_id
               AND ob.url = pc.official_url
               AND normalize(ob.title) = normalize(pc.source_title)
        """
        if clauses:
            query += " WHERE " + " AND ".join(clauses)
        query += " ORDER BY pc.discovered_at DESC, pc.source_author, pc.source_title, pc.site_name"

        with self._connect() as connection:
            connection.create_function("normalize", 1, lambda value: normalize_text(str(value or "")))
            rows = connection.execute(query, params).fetchall()

        normalized_author = normalize_text(author_name or "")
        normalized_title = normalize_text(book_title or "")
        result: list[dict[str, object]] = []
        for row in rows:
            source_author = clean_spaces(str(row[1] or ""))
            source_title = clean_spaces(str(row[2] or ""))
            if normalized_author and normalize_text(source_author) != normalized_author:
                continue
            if normalized_title and normalize_text(source_title) != normalized_title:
                continue
            result.append(
                {
                    "checked_at": str(row[0] or ""),
                    "source_author": source_author,
                    "source_title": source_title,
                    "official_source": row[3] or "",
                    "official_url": row[4] or "",
                    "official_page_count": row[5],
                    "official_char_count": row[6],
                    "char_count": row[7],
                    "publication_confirmed": row[8],
                    "publication_source": row[9] or "",
                    "publication_format": row[10] or "",
                    "publication_url": row[11] or "",
                    "site_name": row[12] or "",
                    "page_title": row[13] or "",
                    "book_url": row[14] or "",
                    "last_update_raw": row[15] or "",
                    "assigned_status": row[16] or "unknown",
                }
            )
        return result

    def export_history_report(
        self,
        *,
        output_dir: str | Path,
        official_source: str | None = None,
        author_name: str | None = None,
        book_title: str | None = None,
        stamp: str | None = None,
    ) -> dict[str, object]:
        rows = self.load_history_rows(official_source=official_source, author_name=author_name, book_title=book_title)
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        slug_parts = [
            clean_spaces(author_name or ""),
            clean_spaces(book_title or ""),
            clean_spaces(official_source or ""),
            stamp or datetime.now().strftime("%Y%m%d_%H%M%S"),
        ]
        slug = "_".join(part.casefold().replace(" ", "_") for part in slug_parts if part) or "history"
        csv_path = export_history_csv(rows, output_dir / f"history_{slug}.csv")
        xlsx_path = export_history_xlsx(rows, output_dir / f"history_{slug}.xlsx")
        return {"records_count": len(rows), "csv_path": csv_path, "xlsx_path": xlsx_path}

    def find_recent_author_url(self, official_source: str, author_name: str) -> str | None:
        normalized_author = normalize_text(author_name)
        if not normalized_author:
            return None
        query = """
            SELECT author_url
            FROM runs
            WHERE official_source = ?
              AND normalize(author_name) = ?
              AND author_url IS NOT NULL
              AND author_url <> ''
            ORDER BY id DESC
            LIMIT 1
        """
        with self._connect() as connection:
            connection.create_function("normalize", 1, lambda value: normalize_text(str(value or "")))
            row = connection.execute(query, (official_source, normalized_author)).fetchone()
        return str(row[0]) if row and row[0] else None

    def find_recent_book_reference(self, official_source: str, author_name: str, book_title: str) -> tuple[str | None, OfficialBook | None]:
        normalized_author = normalize_text(author_name)
        normalized_title = normalize_text(book_title)
        if not normalized_author or not normalized_title:
            return None, None

        query = """
            SELECT
                r.author_url,
                ob.author_name,
                ob.title,
                ob.url,
                ob.source,
                ob.page_count,
                ob.char_count,
                ob.last_update_raw,
                ob.is_complete,
                ob.is_paid,
                ob.raw_text
            FROM official_books ob
            JOIN runs r ON r.id = ob.run_id
            WHERE r.official_source = ?
              AND normalize(ob.author_name) = ?
              AND normalize(ob.title) = ?
            ORDER BY ob.id DESC
            LIMIT 1
        """
        with self._connect() as connection:
            connection.create_function("normalize", 1, lambda value: normalize_text(str(value or "")))
            row = connection.execute(query, (official_source, normalized_author, normalized_title)).fetchone()
        if not row:
            return None, None

        book = OfficialBook(
            author_name=str(row[1] or ""),
            title=str(row[2] or ""),
            url=str(row[3] or ""),
            source=str(row[4] or official_source),
            page_count=row[5],
            char_count=row[6],
            last_update_raw=row[7],
            is_complete=None if row[8] is None else bool(row[8]),
            is_paid=None if row[9] is None else bool(row[9]),
            raw_text=str(row[10] or ""),
        )
        return (str(row[0]) if row[0] else None), book

