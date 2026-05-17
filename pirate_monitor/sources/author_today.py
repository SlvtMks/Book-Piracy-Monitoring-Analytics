from __future__ import annotations

from urllib.parse import parse_qs, urljoin, urlparse, urlunparse

from bs4 import BeautifulSoup, Tag

from pirate_monitor.models import OfficialBook
from pirate_monitor.normalization import clean_spaces
from pirate_monitor.parsing import extract_char_count, extract_date, infer_raw_status
from pirate_monitor.source_base import PagedAuthorSource


class AuthorTodaySource(PagedAuthorSource):
    key = "author_today"
    title = "Author.Today"
    page_title = "Author.Today"

    def __init__(self, http_client) -> None:
        self.http = http_client

    def _normalize_author_url(self, author_url: str) -> str:
        parsed = urlparse(author_url)
        domain = parsed.netloc.lower().removeprefix("www.")
        if domain != "author.today":
            raise ValueError("Author.Today expects an author URL like https://author.today/u/<author>/works")

        parts = [part for part in parsed.path.split("/") if part]
        if len(parts) < 2 or parts[0] != "u":
            raise ValueError("Author.Today author URL must match https://author.today/u/<author>/works")

        user_slug = parts[1]
        query = parsed.query if "page=" in parsed.query else ""
        return urlunparse((parsed.scheme or "https", parsed.netloc, f"/u/{user_slug}/works", "", query, ""))

    def _extract_author_name(self, soup: BeautifulSoup) -> str:
        node = soup.select_one("h1 a") or soup.select_one("h1")
        if node:
            text = clean_spaces(node.get_text(" ", strip=True))
            if text:
                return text

        title = clean_spaces(soup.title.get_text(" ", strip=True)) if soup.title else ""
        if "@" in title:
            return clean_spaces(title.split("@")[0])
        if "читать книги автора" in title.casefold():
            return clean_spaces(title.split("читать книги автора")[0])
        return title or "Unknown author"

    def _extract_page_books(
        self,
        soup: BeautifulSoup,
        page_url: str,
        author_name: str,
        context: dict[str, object],
    ) -> list[OfficialBook]:
        books: list[OfficialBook] = []
        for row in soup.select(".book-row"):
            if not isinstance(row, Tag):
                continue
            book = self._parse_book_row(row, page_url, author_name)
            if book is not None:
                books.append(book)
        return books

    def _parse_book_row(self, row: Tag, page_url: str, author_name: str) -> OfficialBook | None:
        title_link = row.select_one('.book-title a[href^="/work/"]')
        if not isinstance(title_link, Tag):
            return None

        href = (title_link.get("href") or "").strip()
        title = clean_spaces(title_link.get_text(" ", strip=True))
        if not href or not title:
            return None

        row_text = clean_spaces(row.get_text(" ", strip=True))
        lower = row_text.casefold()
        char_count = extract_char_count(row_text)
        raw_status = infer_raw_status(row_text)
        last_update_raw = self._extract_last_update(row, row_text)

        is_complete: bool | None = None
        if raw_status in {"full-text-marker", "completed-marker"} or "полный текст" in lower:
            is_complete = True
        elif raw_status == "ongoing-marker" or "в процессе" in lower:
            is_complete = False

        is_paid: bool | None = None
        if "бесплатные книги" in lower or "бесплатно" in lower:
            is_paid = False
        elif any(marker in lower for marker in ["подписка", "цена", "руб"]):
            is_paid = True

        return OfficialBook(
            author_name=author_name,
            title=title,
            url=urljoin(page_url, href),
            source=self.key,
            page_count=None,
            char_count=char_count,
            last_update_raw=last_update_raw,
            is_complete=is_complete,
            is_paid=is_paid,
            raw_text=row_text,
        )

    def _extract_last_update(self, row: Tag, row_text: str) -> str | None:
        time_node = row.select_one("span[data-time]")
        if isinstance(time_node, Tag):
            timestamp = clean_spaces(time_node.get("data-time") or "")
            if timestamp:
                return timestamp

        hint_node = row.select_one("span[data-hint]")
        if isinstance(hint_node, Tag):
            hint_text = clean_spaces(hint_node.get("data-hint") or "")
            if hint_text:
                return hint_text

        return extract_date(row_text)

    def _find_next_page_url(self, soup: BeautifulSoup, current_url: str) -> str | None:
        current_query = parse_qs(urlparse(current_url).query)
        current_page = int(current_query.get("page", ["1"])[0])
        candidates: dict[int, str] = {}

        for link in soup.select('.pagination a[href*="/works?page="]'):
            if not isinstance(link, Tag):
                continue
            href = (link.get("href") or "").strip()
            if not href:
                continue
            absolute = urljoin(current_url, href)
            page_query = parse_qs(urlparse(absolute).query)
            if "page" not in page_query:
                continue
            try:
                page_number = int(page_query["page"][0])
            except (TypeError, ValueError):
                continue
            if page_number > current_page:
                candidates[page_number] = absolute

        return candidates.get(current_page + 1) or (candidates[min(candidates)] if candidates else None)
