from __future__ import annotations

from urllib.parse import urljoin, urlparse, urlunparse

from bs4 import BeautifulSoup, Tag

from pirate_monitor.models import OfficialBook
from pirate_monitor.normalization import clean_spaces
from pirate_monitor.parsing import extract_char_count, extract_date, extract_page_count
from pirate_monitor.source_base import SinglePageAuthorSource


class LitresSource(SinglePageAuthorSource):
    key = "litres"
    title = "LitRes"
    page_title = "LitRes"

    def __init__(self, http_client) -> None:
        self.http = http_client

    def _normalize_author_url(self, author_url: str) -> str:
        parsed = urlparse(author_url)
        domain = parsed.netloc.lower().removeprefix("www.")
        if domain != "litres.ru" or "/author/" not in parsed.path:
            raise ValueError("LitRes expects an author URL like https://www.litres.ru/author/<slug>/")
        normalized_path = parsed.path if parsed.path.endswith("/") else parsed.path + "/"
        return urlunparse((parsed.scheme or "https", parsed.netloc, normalized_path, "", parsed.query, ""))

    def fetch_book_reference(
        self,
        book_url: str,
        *,
        expected_author: str | None = None,
    ) -> tuple[OfficialBook | None, str | None]:
        snapshot = self.http.get(book_url)
        if snapshot.status_code >= 400:
            return None, None

        soup = BeautifulSoup(snapshot.text, "html.parser")
        title = self._extract_book_title(soup)
        if not title:
            return None, None

        author_name = self._extract_book_author(soup)
        if expected_author and author_name:
            if clean_spaces(expected_author).casefold() != clean_spaces(author_name).casefold():
                return None, None

        text = clean_spaces(soup.get_text(" ", strip=True))
        return (
            OfficialBook(
                author_name=author_name or expected_author or "",
                title=title,
                url=snapshot.url,
                source=self.key,
                page_count=extract_page_count(text),
                char_count=extract_char_count(text),
                last_update_raw=extract_date(text),
                is_complete=True,
                is_paid=self._infer_paid(text),
                raw_text=text,
            ),
            self._extract_author_url(snapshot.url),
        )

    def _extract_author_name(self, soup: BeautifulSoup) -> str:
        node = soup.select_one("h1")
        if node:
            text = clean_spaces(node.get_text(" ", strip=True))
            if text:
                return text
        title = clean_spaces(soup.title.get_text(" ", strip=True)) if soup.title else ""
        title = title.removeprefix("Все книги ")
        title = title.split(" — ")[0]
        return title or "Unknown author"

    def _extract_books(
        self,
        soup: BeautifulSoup,
        base_url: str,
        author_name: str,
        context: dict[str, object],
    ) -> list[OfficialBook]:
        books: list[OfficialBook] = []
        seen_urls: set[str] = set()
        for card in soup.select('a.art__title[href], a[href*="/audiobook/"][class], a[href*="/book/"][class]'):
            if not isinstance(card, Tag):
                continue
            href = (card.get("href") or "").strip()
            if not href:
                continue
            absolute = urljoin(base_url, href)
            path = urlparse(absolute).path
            if not ("/audiobook/" in path or "/book/" in path):
                continue
            if absolute in seen_urls:
                continue

            title = clean_spaces(card.get_text(" ", strip=True))
            if not title:
                continue

            card_root = card.find_parent(["div", "article", "li", "section"]) or card
            card_text = clean_spaces(card_root.get_text(" ", strip=True))

            books.append(
                OfficialBook(
                    author_name=author_name,
                    title=title,
                    url=absolute,
                    source=self.key,
                    page_count=None,
                    char_count=None,
                    last_update_raw=extract_date(card_text),
                    is_complete=True,
                    is_paid=self._infer_paid(card_text),
                    raw_text=card_text,
                )
            )
            seen_urls.add(absolute)
        return books

    def _extract_book_title(self, soup: BeautifulSoup) -> str:
        for selector in ["h1", "meta[property='og:title']", "meta[name='twitter:title']"]:
            node = soup.select_one(selector)
            if not node:
                continue
            if node.name == "meta":
                text = clean_spaces(node.get("content") or "")
            else:
                text = clean_spaces(node.get_text(" ", strip=True))
            if text:
                return text.split(" - ")[0].split(" | ")[0].strip()
        title = clean_spaces(soup.title.get_text(" ", strip=True)) if soup.title else ""
        return title.split(" - ")[0].split(" | ")[0].strip()

    def _extract_book_author(self, soup: BeautifulSoup) -> str | None:
        for selector in ["[itemprop='author']", "a[href*='/author/']", "meta[name='author']", "meta[property='book:author']"]:
            node = soup.select_one(selector)
            if not node:
                continue
            if node.name == "meta":
                text = clean_spaces(node.get("content") or "")
            else:
                text = clean_spaces(node.get_text(" ", strip=True))
            if text:
                return text
        return None

    def _extract_author_url(self, book_url: str) -> str | None:
        parsed = urlparse(book_url)
        segments = [segment for segment in parsed.path.split("/") if segment]
        if len(segments) >= 3 and segments[0] in {"book", "audiobook"}:
            return urlunparse((parsed.scheme or "https", parsed.netloc, f"/author/{segments[1]}/", "", "", ""))
        return None

    def _infer_paid(self, text: str) -> bool | None:
        lower = text.casefold()
        if any(marker in lower for marker in ["купить", "цена", "подписка", "руб", "₽"]):
            return True
        if "бесплатно" in lower:
            return False
        return None
