from __future__ import annotations

from typing import Any

from bs4 import BeautifulSoup

from pirate_monitor.models import OfficialBook


class BaseAuthorSource:
    page_title = "source"

    def _finalize_books(self, books: list[OfficialBook]) -> list[OfficialBook]:
        books.sort(key=lambda book: book.title.casefold())
        return books

    def _build_open_page_error(self, status_code: int) -> str:
        return f"Could not open {self.page_title} author page: HTTP {status_code}"

    def _build_missing_author_error(self) -> str:
        return f"Could not determine author name on {self.page_title} page."

    def _build_missing_books_error(self) -> str:
        return f"Could not parse author books on {self.page_title} page."


class PagedAuthorSource(BaseAuthorSource):
    def fetch_author_books(self, author_url: str) -> tuple[str, list[OfficialBook]]:
        next_url, context = self._build_paging_context(author_url)
        visited_pages: set[str] = set()
        seen_books: set[str] = set()
        books: list[OfficialBook] = []
        author_name: str | None = None

        while next_url and next_url not in visited_pages:
            snapshot = self.http.get(next_url)
            if snapshot.status_code >= 400:
                raise RuntimeError(self._build_open_page_error(snapshot.status_code))

            visited_pages.add(snapshot.url)
            soup = BeautifulSoup(snapshot.text, "html.parser")
            author_name = author_name or self._extract_author_name(soup)

            for book in self._extract_page_books(soup, snapshot.url, author_name, context):
                if book.url in seen_books:
                    continue
                seen_books.add(book.url)
                books.append(book)

            next_url = self._find_next_page_url(soup, snapshot.url)

        if not author_name:
            raise RuntimeError(self._build_missing_author_error())
        if not books:
            raise RuntimeError(self._build_missing_books_error())
        return author_name, self._finalize_books(books)

    def _build_paging_context(self, author_url: str) -> tuple[str, dict[str, Any]]:
        return self._normalize_author_url(author_url), {}

    def _normalize_author_url(self, author_url: str) -> str:
        raise NotImplementedError

    def _extract_author_name(self, soup: BeautifulSoup) -> str:
        raise NotImplementedError

    def _extract_page_books(
        self,
        soup: BeautifulSoup,
        page_url: str,
        author_name: str,
        context: dict[str, Any],
    ) -> list[OfficialBook]:
        raise NotImplementedError

    def _find_next_page_url(self, soup: BeautifulSoup, current_url: str) -> str | None:
        raise NotImplementedError


class SinglePageAuthorSource(BaseAuthorSource):
    def fetch_author_books(self, author_url: str) -> tuple[str, list[OfficialBook]]:
        page_url, context = self._build_page_context(author_url)
        snapshot = self.http.get(page_url)
        if snapshot.status_code >= 400:
            raise RuntimeError(self._build_open_page_error(snapshot.status_code))

        soup = BeautifulSoup(snapshot.text, "html.parser")
        author_name = self._extract_author_name(soup)
        if not author_name:
            raise RuntimeError(self._build_missing_author_error())

        books = self._extract_books(soup, snapshot.url, author_name, context)
        if not books:
            raise RuntimeError(self._build_missing_books_error())
        return author_name, self._finalize_books(books)

    def _build_page_context(self, author_url: str) -> tuple[str, dict[str, Any]]:
        return self._normalize_author_url(author_url), {}

    def _normalize_author_url(self, author_url: str):
        raise NotImplementedError

    def _extract_author_name(self, soup: BeautifulSoup) -> str:
        raise NotImplementedError

    def _extract_books(
        self,
        soup: BeautifulSoup,
        base_url: str,
        author_name: str,
        context: dict[str, Any],
    ) -> list[OfficialBook]:
        raise NotImplementedError
