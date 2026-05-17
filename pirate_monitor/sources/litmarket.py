from __future__ import annotations

import json
import re
from urllib.parse import urlparse, urlunparse

from bs4 import BeautifulSoup

from pirate_monitor.models import OfficialBook
from pirate_monitor.normalization import clean_spaces
from pirate_monitor.source_base import SinglePageAuthorSource


AUTHOR_ID_RE = re.compile(r"-p(\d+)(?:/|$)")
AUTHOR_TITLE_PARTS = (
    " читать книги автора",
    " - читать книги автора на Литмаркете",
    ". книги автора на Литмаркете",
)


class LitmarketSource(SinglePageAuthorSource):
    key = "litmarket"
    title = "Litmarket"
    page_title = "Litmarket"

    def __init__(self, http_client) -> None:
        self.http = http_client

    def _build_page_context(self, author_url: str) -> tuple[str, dict[str, int | str]]:
        author_root_url, author_id = self._normalize_author_url(author_url)
        return author_root_url, {"author_id": author_id}

    def _normalize_author_url(self, author_url: str) -> tuple[str, int]:
        parsed = urlparse(author_url)
        domain = parsed.netloc.lower().removeprefix("www.")
        if domain != "litmarket.ru":
            raise ValueError("Litmarket expects an author URL like https://litmarket.ru/<slug>-p12345 or .../books")

        match = AUTHOR_ID_RE.search(parsed.path)
        if not match:
            raise ValueError("Could not extract Litmarket author id from URL.")

        path = parsed.path.rstrip("/")
        if path.endswith("/books"):
            path = path[: -len("/books")]
        normalized_url = urlunparse((parsed.scheme or "https", parsed.netloc, path, "", "", ""))
        return normalized_url, int(match.group(1))

    def _extract_author_name(self, soup: BeautifulSoup) -> str:
        for selector in ["h1", "h2", 'meta[property="og:title"]']:
            node = soup.select_one(selector)
            if node is None:
                continue
            text = clean_spaces(node.get("content") or "") if node.name == "meta" else clean_spaces(node.get_text(" ", strip=True))
            if text:
                return self._strip_author_title_noise(text)
        title = clean_spaces(soup.title.get_text(" ", strip=True)) if soup.title else ""
        return self._strip_author_title_noise(title) or "Unknown author"

    def _strip_author_title_noise(self, value: str) -> str:
        cleaned = value
        for part in AUTHOR_TITLE_PARTS:
            cleaned = cleaned.split(part)[0]
        return cleaned.strip()

    def _extract_books(
        self,
        soup: BeautifulSoup,
        base_url: str,
        author_name: str,
        context: dict[str, int | str],
    ) -> list[OfficialBook]:
        author_id = int(context["author_id"])
        return self._fetch_books_from_api(author_id, fallback_author=author_name)

    def _fetch_books_from_api(self, author_id: int, fallback_author: str) -> list[OfficialBook]:
        snapshot = self.http.get(f"https://litmarket.ru/api/author/{author_id}/books", params={"size": "small"})
        if snapshot.status_code >= 400:
            raise RuntimeError(f"Could not fetch Litmarket author books JSON: HTTP {snapshot.status_code}")

        payload = json.loads(snapshot.text)
        books_payload = payload.get("books") or []
        books: list[OfficialBook] = []
        seen_urls: set[str] = set()

        for item in books_payload:
            url = clean_spaces(item.get("url") or "")
            title = clean_spaces(item.get("name") or "")
            if not url or not title or url in seen_urls:
                continue

            authors = item.get("authors") or []
            author_name = clean_spaces(authors[0].get("name") or "") if authors else fallback_author
            ebook = item.get("ebook") or {}
            audio = item.get("audio") or {}
            status_text = clean_spaces(ebook.get("status") or audio.get("status") or "")
            lower_status = status_text.casefold()
            price = ebook.get("price")
            is_paid = None if price is None else float(price) > 0
            is_complete = True if "завершена" in lower_status else False if "в процессе" in lower_status else None

            books.append(
                OfficialBook(
                    author_name=author_name or fallback_author,
                    title=title,
                    url=url,
                    source=self.key,
                    page_count=None,
                    char_count=None,
                    last_update_raw=None,
                    is_complete=is_complete,
                    is_paid=is_paid,
                    raw_text=" ".join(part for part in [title, status_text] if part),
                )
            )
            seen_urls.add(url)

        return books
