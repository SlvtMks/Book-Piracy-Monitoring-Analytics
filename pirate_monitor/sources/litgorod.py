from __future__ import annotations

from urllib.parse import parse_qs, urljoin, urlparse, urlunparse

from bs4 import BeautifulSoup, Tag

from pirate_monitor.models import OfficialBook
from pirate_monitor.normalization import clean_spaces
from pirate_monitor.parsing import extract_char_count, extract_date, extract_page_count, infer_raw_status
from pirate_monitor.source_base import PagedAuthorSource


class LitgorodSource(PagedAuthorSource):
    key = "litgorod"
    title = "LitGorod"
    page_title = "LitGorod"

    def __init__(self, http_client) -> None:
        self.http = http_client

    def _build_paging_context(self, author_url: str) -> tuple[str, dict[str, str]]:
        next_url, author_profile_url = self._normalize_author_url(author_url)
        return next_url, {"author_profile_url": author_profile_url}

    def _normalize_author_url(self, author_url: str) -> tuple[str, str]:
        parsed = urlparse(author_url)
        domain = parsed.netloc.lower().removeprefix("www.")
        if domain != "litgorod.ru":
            raise ValueError("LitGorod expects an author URL like https://litgorod.ru/profile/<id>/books")

        parts = [part for part in parsed.path.split("/") if part]
        if len(parts) < 2 or parts[0] != "profile":
            raise ValueError("LitGorod author URL must match https://litgorod.ru/profile/<id>/books")

        author_id = parts[1]
        profile_url = urlunparse((parsed.scheme or "https", parsed.netloc, f"/profile/{author_id}/books", "", "", ""))
        books_url = urlunparse((parsed.scheme or "https", parsed.netloc, f"/profile/{author_id}/books", "", parsed.query, ""))
        return books_url, profile_url

    def _extract_author_name(self, soup: BeautifulSoup) -> str:
        for selector in ["h2", "h1"]:
            node = soup.select_one(selector)
            if not node:
                continue
            text = clean_spaces(node.get_text(" ", strip=True))
            if text and not text.startswith("Книги автора"):
                return text
        title = clean_spaces(soup.title.get_text(" ", strip=True)) if soup.title else ""
        return title.removeprefix("Книги ").split(":")[0].strip() or "Unknown author"

    def _extract_page_books(
        self,
        soup: BeautifulSoup,
        page_url: str,
        author_name: str,
        context: dict[str, str],
    ) -> list[OfficialBook]:
        books: list[OfficialBook] = []
        author_profile_url = context["author_profile_url"]
        for container in soup.select("div.b-book_item__container"):
            if not isinstance(container, Tag) or not self._container_matches_author(container, author_profile_url):
                continue
            title_link = container.select_one('a._link[href*="/books/view/"]')
            if not isinstance(title_link, Tag):
                continue
            title = clean_spaces(title_link.get_text(" ", strip=True))
            href = (title_link.get("href") or "").strip()
            if not title or not href:
                continue

            card_text = clean_spaces(container.get_text(" ", strip=True))
            raw_status = infer_raw_status(card_text)
            is_complete = True if raw_status in {"full-text-marker", "completed-marker"} else False if raw_status == "ongoing-marker" else None
            is_paid = None
            container_html = str(container)
            if '"price":"0.00"' in container_html:
                is_paid = False
            elif '"price":"' in container_html:
                is_paid = True

            books.append(
                OfficialBook(
                    author_name=author_name,
                    title=title,
                    url=urljoin(page_url, href),
                    source=self.key,
                    page_count=extract_page_count(card_text),
                    char_count=extract_char_count(card_text),
                    last_update_raw=extract_date(card_text),
                    is_complete=is_complete,
                    is_paid=is_paid,
                    raw_text=card_text,
                )
            )
        return books

    def _container_matches_author(self, container: Tag, author_profile_url: str) -> bool:
        author_links = {
            clean_spaces(urljoin(author_profile_url, (node.get("href") or "").strip()))
            for node in container.select('a[href*="/profile/"]')
            if isinstance(node, Tag)
        }
        return author_profile_url in author_links

    def _find_next_page_url(self, soup: BeautifulSoup, current_url: str) -> str | None:
        current_query = parse_qs(urlparse(current_url).query)
        current_page = int(current_query.get("page", ["1"])[0])
        candidates: dict[int, str] = {}
        for link in soup.select('a[href*="/profile/"]'):
            if not isinstance(link, Tag):
                continue
            href = (link.get("href") or "").strip()
            if "/books?page=" not in href:
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
