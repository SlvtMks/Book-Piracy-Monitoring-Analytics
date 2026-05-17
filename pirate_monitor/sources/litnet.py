from __future__ import annotations

import re
from urllib.parse import urljoin

from bs4 import BeautifulSoup, Tag

from pirate_monitor.models import OfficialBook
from pirate_monitor.normalization import clean_spaces, similarity
from pirate_monitor.parsing import extract_char_count, extract_date


BAD_TITLES = {"подробнее", "читать", "читать онлайн", "перейти"}
STATUS_RE = re.compile(
    r"(полный\s+текст|ознакомительный\s+фрагмент|фрагмент)\s+(\d+)\s*стр",
    re.IGNORECASE,
)
PUBLICATION_RE = re.compile(r"Публикация:\s*([^<\n\r]+)", re.IGNORECASE)


class LitnetSource:
    key = "litnet"
    title = "Litnet"

    def __init__(self, http_client) -> None:
        self.http = http_client

    def fetch_author_books(self, author_url: str, max_books: int | None = None) -> tuple[str, list[OfficialBook]]:
        snapshot = self.http.get(author_url)
        if snapshot.status_code >= 400:
            raise RuntimeError(f"Не удалось открыть страницу автора Litnet: HTTP {snapshot.status_code}")

        soup = BeautifulSoup(snapshot.text, "html.parser")
        author_name = self._extract_author_name(soup)
        candidates = self._collect_candidate_books(soup, snapshot.url)
        books = self._enrich_and_filter_books(author_name, candidates, max_books=max_books)
        if not books:
            raise RuntimeError("На странице Litnet не удалось распознать книги автора.")
        return author_name, books

    def fetch_book_reference(
        self,
        book_url: str,
        *,
        expected_author: str | None = None,
    ) -> tuple[OfficialBook | None, str | None]:
        candidate = OfficialBook(author_name=expected_author or "", title="", url=book_url, source=self.key)
        book = self._fetch_book_page(expected_author or "", candidate, require_author_match=bool(expected_author))
        if not book:
            return None, None

        snapshot = self.http.get(book_url)
        if snapshot.status_code >= 400:
            return book, None
        soup = BeautifulSoup(snapshot.text, "html.parser")
        return book, self._extract_author_url(soup, snapshot.url)

    def _extract_author_name(self, soup: BeautifulSoup) -> str:
        for selector in ["h1", '[itemprop="name"]', ".author-name", ".page-caption h1"]:
            node = soup.select_one(selector)
            if node:
                text = clean_spaces(node.get_text(" ", strip=True))
                if text and "все книги" not in text.casefold():
                    return text

        title = clean_spaces(soup.title.get_text(" ", strip=True)) if soup.title else ""
        for separator in ["|", "-", "—", ":"]:
            if separator in title:
                left = clean_spaces(title.split(separator)[0])
                if left:
                    return left
        return title or "Неизвестный автор"

    def _collect_candidate_books(self, soup: BeautifulSoup, base_url: str) -> list[OfficialBook]:
        seen_urls: set[str] = set()
        books: list[OfficialBook] = []

        for link in soup.select('a[href*="/book/"]'):
            if not isinstance(link, Tag):
                continue

            href = (link.get("href") or "").strip()
            if "/book/" not in href:
                continue

            title = clean_spaces(link.get("title") or link.get_text(" ", strip=True))
            if not title or title.casefold() in BAD_TITLES:
                continue

            absolute_url = urljoin(base_url, href)
            if absolute_url in seen_urls:
                continue
            seen_urls.add(absolute_url)
            books.append(OfficialBook(author_name="", title=title, url=absolute_url, source=self.key, raw_text=title))

        return books

    def _enrich_and_filter_books(
        self,
        author_name: str,
        candidates: list[OfficialBook],
        *,
        max_books: int | None = None,
    ) -> list[OfficialBook]:
        result: list[OfficialBook] = []
        seen_urls: set[str] = set()

        for candidate in candidates:
            if max_books is not None and len(result) >= max_books:
                break
            if candidate.url in seen_urls:
                continue
            enriched = self._fetch_book_page(author_name, candidate)
            if not enriched:
                continue
            seen_urls.add(enriched.url)
            result.append(enriched)

        result.sort(key=lambda book: book.title.casefold())
        return result

    def _fetch_book_page(
        self,
        author_name: str,
        candidate: OfficialBook,
        *,
        require_author_match: bool = True,
    ) -> OfficialBook | None:
        snapshot = self.http.get(candidate.url)
        if snapshot.status_code >= 400:
            return None

        soup = BeautifulSoup(snapshot.text, "html.parser")
        container = soup.select_one(".book-view-info") or soup.select_one(".book-view-info-coll") or soup
        text = clean_spaces(container.get_text(" ", strip=True))

        page_author = self._extract_book_author(soup, text)
        if require_author_match and page_author and author_name and similarity(author_name, page_author) < 0.72:
            return None

        title = self._extract_book_title(soup, candidate.title)
        if title.casefold() in BAD_TITLES:
            return None

        page_count = self._extract_page_count(text)
        status_text = self._extract_status_text(text)
        publication = self._extract_publication(text)

        return OfficialBook(
            author_name=page_author or author_name,
            title=title,
            url=snapshot.url,
            source=self.key,
            page_count=page_count,
            char_count=extract_char_count(text),
            last_update_raw=publication or extract_date(text),
            is_complete=status_text == "full",
            is_paid=self._contains_any(text, ["купить", "подписка", "прокат", "rub"]),
            raw_text=text,
        )

    def _extract_author_url(self, soup: BeautifulSoup, base_url: str) -> str | None:
        selectors = [
            '[itemprop="author"] a[href*="-u"]',
            ".book-author a[href*='-u']",
            ".author a[href*='-u']",
            "a[href*='-u']",
        ]
        for selector in selectors:
            node = soup.select_one(selector)
            if not isinstance(node, Tag):
                continue
            href = clean_spaces(node.get("href") or "")
            if href and "-u" in href:
                return urljoin(base_url, href)
        return None

    def _extract_book_title(self, soup: BeautifulSoup, fallback: str) -> str:
        for selector in ["h1", ".book-view-info h1", ".book-title"]:
            node = soup.select_one(selector)
            if node:
                text = clean_spaces(node.get_text(" ", strip=True))
                if text:
                    return text
        return fallback

    def _extract_book_author(self, soup: BeautifulSoup, text: str) -> str | None:
        for selector in ['[itemprop="author"]', ".author", ".book-author a"]:
            node = soup.select_one(selector)
            if node:
                text_value = clean_spaces(node.get_text(" ", strip=True))
                if text_value:
                    return text_value

        title = clean_spaces(soup.title.get_text(" ", strip=True)) if soup.title else ""
        if ":" in title:
            left = clean_spaces(title.split(":")[0])
            if left:
                return left

        segments = text.split(" Ограничение ")
        if segments:
            tokens = segments[0].split()
            if len(tokens) >= 4:
                return " ".join(tokens[-2:])
        return None

    def _extract_page_count(self, text: str) -> int | None:
        match = STATUS_RE.search(text)
        if match:
            return int(match.group(2))
        fallback = re.search(r"(\d+)\s*стр\.", text, re.IGNORECASE)
        return int(fallback.group(1)) if fallback else None

    def _extract_status_text(self, text: str) -> str | None:
        lower = text.casefold()
        if "полный текст" in lower:
            return "full"
        if "ознакомительный фрагмент" in lower or "фрагмент" in lower:
            return "fragment"
        return None

    def _extract_publication(self, text: str) -> str | None:
        match = PUBLICATION_RE.search(text)
        return clean_spaces(match.group(1)) if match else None

    def _contains_any(self, text: str, markers: list[str]) -> bool:
        lower = text.casefold()
        return any(marker in lower for marker in markers)

