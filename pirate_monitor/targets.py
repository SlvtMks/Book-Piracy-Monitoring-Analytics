from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from urllib.parse import parse_qs, unquote, urljoin, urlparse

from bs4 import BeautifulSoup

from pirate_monitor.book_content import count_book_bytes
from pirate_monitor.browser_download import download_book_bytes_via_browser
from pirate_monitor.models import PirateRecord, SearchHit, TargetSite
from pirate_monitor.normalization import clean_spaces, looks_like_same_book, similarity
from pirate_monitor.parsing import (
    count_text_characters,
    extract_author,
    extract_date,
    extract_file_size_kb,
    extract_page_count,
    infer_availability,
    infer_bot_blocked,
    infer_raw_status,
)


LITMIR_AUTHOR_RE = re.compile(r"Автор:\s*([^\n\r]+?)\s+Жанр:", re.IGNORECASE)
LITMIR_PAGES_RE = re.compile(r"Количество страниц:\s*(\d+)", re.IGNORECASE)
LITMIR_SIZE_RE = re.compile(r"Размер:\s*([\d.,]+)\s*Кбайт", re.IGNORECASE)
LITMIR_DATE_RE = re.compile(r"Добавлено\s+(\d{1,2}\s+[^\s]+\s+\d{4},\s*\d{1,2}:\d{2})", re.IGNORECASE)
READLI_AUTHOR_TITLE_RE = re.compile(r"^(.*?)\s*\|\s*Ридли", re.IGNORECASE)
FB2_AUTHOR_RE = re.compile(r"^\s*(.*?)\s*-\s*все книги автора", re.IGNORECASE)
NOISY_AUTHOR_MARKERS = (
    "язык:",
    "добавил:",
    "проверил:",
    "формат:",
    "аннотация",
    "рейтинг:",
    "комментарии",
    "навигация",
)
TITLE_AUTHOR_DOMAINS = {"bookzip.top", "mir-knigi.net", "litlib.net", "www.litlib.net", "kniga-online.com", "siteknig.com", "knigkindom.ru"}
TITLE_PREFIX_AUTHOR_DOMAINS = {"litvek.com", "www.knigago.com", "knigago.com"}
GENERIC_CONTENT_SELECTORS = [
    "article",
    "article.full",
    "article.full.ignore-select",
    "div#dle-content",
    "div.fullstory",
    "div.entry-content",
    "div.post-content",
    "div.text",
    "div.book-text",
    "div.content",
    "main",
]
CONTENT_SELECTORS = {
    "bookzip.top": ["div#book_reader", "div.book-reader", "article.item-full", "div#dle-content"],
    "fb2.top": ["div.book-info-body"],
    "litmir.club": ["div.lt26a", "div.island"],
    "topliba.com": ["div.book-details"],
    "www.knigago.com": ["div.pbook"],
    "knigago.com": ["div.pbook"],
    "kniga-online.com": ["article.full.ignore-select", "div#dle-content"],
    "litvek.com": ["div.pbook"],
    "readli.net": ["div.page__left"],
    "www.rulit.me": ["article.single-blog", "div.kotha-default-content"],
    "rulit.me": ["article.single-blog", "div.kotha-default-content"],
    "www.litlib.net": ["div#content"],
    "litlib.net": ["div#content"],
    "mir-knigi.net": ["div.col-content"],
    "knigkindom.ru": ["div.fullstory", "div#dle-content"],
    "siteknig.com": ["article.full.ignore-select", "div#dle-content"],
}
START_MARKERS = {
    "bookzip.top": ["Краткое описание"],
    "fb2.top": ["Краткое описание", "Аннотация"],
    "litmir.club": ["Аннотация"],
    "topliba.com": ["Аннотация", "Описание"],
    "www.knigago.com": ["Краткое содержание книги"],
    "knigago.com": ["Краткое содержание книги"],
    "kniga-online.com": ["читать полностью бесплатно онлайн", "Читать онлайн"],
    "litvek.com": ["Скачать книгу Читать онлайн", "Доступен ознакомительный фрагмент книги!"],
    "readli.net": ["Аннотация"],
    "www.rulit.me": ["Аннотация"],
    "rulit.me": ["Аннотация"],
    "www.litlib.net": [],
    "litlib.net": [],
    "mir-knigi.net": ["описание и краткое содержание", "краткое содержание"],
    "knigkindom.ru": ["читаем онлайн бесплатно полную версию!"],
    "siteknig.com": ["читать полностью бесплатно онлайн", "Читать книгу бесплатно"],
}
TAIL_CUT_MARKERS = [
    "C этой книгой скачивают",
    "с этой книгой скачивают",
    "Похожие книги",
    "Другие книги:",
    "Отзывы",
]
NOISY_CONTENT_SELECTORS = [
    "script",
    "style",
    "noscript",
    "header",
    "footer",
    "nav",
    "aside",
    "form",
    "button",
    "svg",
    "iframe",
    ".advert",
    ".advertising",
    ".ads",
    ".banner",
    ".breadcrumbs",
    ".breadcrumb",
    ".pagination",
    ".pager",
    ".comments",
    ".comment",
    ".share",
    ".social",
    ".related",
    ".similar",
    ".book-info",
    ".book-meta",
    ".booknav",
    ".book-fontsize",
    ".footer-text",
    ".download",
    ".downloads",
    ".sidebar",
    ".tags",
    ".rating",
]
DOWNLOAD_TEXT_MARKERS = (
    "скачать",
    "download",
    "epub",
    "fb2",
    "txt",
    "фб2",
)
READ_TEXT_MARKERS = (
    "читать онлайн",
    "читать книгу",
    "читать полностью",
    "полный текст",
    "read online",
)
NEXT_TEXT_MARKERS = (
    "следующая глава",
    "следующая страница",
    "следующая",
    "читать дальше",
    "далее",
    "next",
)
SUPPORTED_DOWNLOAD_EXTENSIONS = (".epub", ".fb2", ".txt", ".zip", ".rtf", ".pdf")
DOWNLOAD_URL_MARKERS = ("download", "bookfiledownloadlink", "getfile", "filedownload", "\u0441\u043a\u0430\u0447")
IGNORED_DOWNLOAD_URL_MARKERS = ("top.html", "rating", "pravooblad", "privacy")
INLINE_CURRENT_MIN_CHARS = 12000
INLINE_CHAIN_MAX_PAGES = 120


def load_target_sites(config_path: str | Path) -> list[TargetSite]:
    payload = json.loads(Path(config_path).read_text(encoding="utf-8"))
    return [TargetSite(**item) for item in payload]


class TargetPageParser:
    def __init__(self, http_client) -> None:
        self.http = http_client

    def parse(self, site: TargetSite, hit: SearchHit, source_author: str, source_title: str, official_url: str) -> PirateRecord:
        snapshot = self.http.get(hit.url)
        soup = BeautifulSoup(snapshot.text, "html.parser")

        page_text = clean_spaces(soup.get_text(" ", strip=True))
        meta_text = self._extract_meta_text(soup)
        title = self._extract_title(site, soup, hit)
        text = clean_spaces(" ".join(part for part in [title, meta_text, page_text] if part))
        author = self._extract_author(site, soup, text, source_author)
        page_count = self._extract_page_count(site, text)
        publication = self._probe_publication(site, snapshot.url, soup, title, author or source_author, text)
        char_count = publication["char_count"]
        file_size_kb = self._extract_file_size_kb(site, text)
        last_update_raw = self._extract_last_update(site, soup, text)
        raw_status = self._extract_raw_status(site, publication.get("status_text") or text)
        bot_blocked = infer_bot_blocked(text)
        if bot_blocked:
            char_count = None
            last_update_raw = None
            raw_status = None
            publication = {
                "confirmed": False,
                "source": None,
                "format": None,
                "url": None,
                "char_count": None,
                "note": "Доступ ограничен антибот-защитой",
                "status_text": text,
            }
        available = snapshot.status_code < 400 and infer_availability(text)

        record = PirateRecord(
            site_name=site.name,
            domain=site.domain,
            source_author=source_author,
            source_title=source_title,
            official_url=official_url,
            book_url=snapshot.url,
            discovered_at=datetime.now(),
            page_title=title,
            official_source="litnet",
            page_author=author,
            page_count=page_count,
            char_count=char_count,
            publication_confirmed=bool(publication.get("confirmed")),
            publication_source=publication.get("source"),
            publication_format=publication.get("format"),
            publication_url=publication.get("url"),
            file_size_kb=file_size_kb,
            last_update_raw=last_update_raw,
            raw_status=raw_status,
            snippet=hit.snippet,
            search_query=hit.query,
            provider=hit.provider,
            match_score=max(similarity(source_title, title), similarity(source_author, author or "")),
            available=available,
        )

        if bot_blocked:
            record.notes.append("Доступ ограничен антибот-защитой")
        if publication.get("note"):
            record.notes.append(str(publication["note"]))
        if not looks_like_same_book(source_title, title) and record.match_score < 0.35:
            record.notes.append("Слабое совпадение по названию")
        if site.parser != "generic":
            record.notes.append(f"Парсер-сайт: {site.parser}")
        return record

    def _probe_publication(self, site: TargetSite, current_url: str, soup: BeautifulSoup, title: str, author: str, text: str) -> dict[str, object]:
        for link in self._extract_download_links(soup, current_url):
            confirmed_download = self._confirm_download_link(current_url, link)
            if confirmed_download:
                return self._publication_result(
                    confirmed=True,
                    source="download",
                    file_format=confirmed_download["format"],
                    url=confirmed_download["url"],
                    char_count=confirmed_download["char_count"],
                    note="Подтверждено скачиваемым файлом книги ({})".format(confirmed_download["format"]),
                    status_text=text,
                )

        for link in self._extract_read_links(site, soup, current_url):
            inline = self._extract_inline_chain(site, link, title, author)
            if inline and inline.get("char_count"):
                return self._publication_result(
                    confirmed=True,
                    source="inline",
                    file_format="html",
                    url=inline.get("url") or link,
                    char_count=inline["char_count"],
                    note="Подтверждено текстом книги на странице чтения",
                    status_text=inline.get("status_text") or text,
                )

        inline_current = self._extract_inline_chain(site, current_url, title, author, initial_soup=soup, initial_text=text)
        if inline_current and inline_current.get("char_count"):
            inline_pages = int(inline_current.get("pages") or 0)
            inline_chars = int(inline_current["char_count"])
            if inline_pages > 1 or inline_chars >= INLINE_CURRENT_MIN_CHARS:
                return self._publication_result(
                    confirmed=True,
                    source="inline",
                    file_format="html",
                    url=inline_current.get("url") or current_url,
                    char_count=inline_chars,
                    note=self._inline_site_note(inline_pages),
                    status_text=inline_current.get("status_text") or text,
                )

        card_content = self._extract_any_content_text(site, soup, title, author)
        if card_content:
            return self._publication_result(
                confirmed=False,
                source="card",
                file_format="html",
                url=current_url,
                char_count=None,
                note="Не подтверждено: доступна только карточка книги или аннотация",
                status_text=text,
            )

        return self._publication_result(
            confirmed=False,
            source=None,
            file_format=None,
            url=None,
            char_count=None,
            note="Не найден ни подтвержденный текст, ни файл книги, ни аннотация",
            status_text=text,
        )

    def _publication_result(
        self,
        *,
        confirmed: bool,
        source: str | None,
        file_format: str | None,
        url: str | None,
        char_count: int | None,
        note: str,
        status_text: str,
    ) -> dict[str, object]:
        return {
            "confirmed": confirmed,
            "source": source,
            "format": file_format,
            "url": url,
            "char_count": char_count,
            "note": note,
            "status_text": status_text,
        }

    def _inline_site_note(self, page_count: int) -> str:
        if page_count > 1:
            return "Подтверждено текстом книги на {} страницах сайта".format(page_count)
        return "Подтверждено текстом книги на странице сайта"

    def _confirm_download_link(self, page_url: str, download_url: str) -> dict[str, object] | None:
        try:
            file_snapshot = self.http.get(download_url)
        except Exception:
            file_snapshot = None

        if file_snapshot is not None:
            parsed = count_book_bytes(file_snapshot.content, url=file_snapshot.url, content_type=file_snapshot.content_type)
            if parsed.char_count and parsed.file_format and parsed.file_format != "html":
                return {
                    "url": file_snapshot.url,
                    "format": parsed.file_format,
                    "char_count": parsed.char_count,
                }

        browser_download = self._download_book_via_browser(page_url, download_url)
        if not browser_download:
            return None
        parsed = count_book_bytes(browser_download["content"], url=browser_download["file_name"])
        if parsed.char_count and parsed.file_format and parsed.file_format != "html":
            return {
                "url": download_url,
                "format": parsed.file_format,
                "char_count": parsed.char_count,
            }
        return None

    def _download_book_via_browser(self, page_url: str, download_url: str) -> dict[str, object] | None:
        result = download_book_bytes_via_browser(page_url, download_url)
        if not result:
            return None
        return {"content": result.content, "file_name": result.file_name}

    def _extract_download_links(self, soup: BeautifulSoup, current_url: str) -> list[str]:
        candidates: list[tuple[int, str]] = []
        seen: set[str] = set()
        for anchor in soup.select("a[href]"):
            href = clean_spaces(anchor.get("href") or "")
            if not href or href.startswith(("#", "javascript:", "mailto:")):
                continue
            resolved_url = self._resolve_special_link(current_url, href)
            if not resolved_url or resolved_url in seen:
                continue
            text_value = clean_spaces(anchor.get_text(" ", strip=True))
            lower = f"{href} {text_value} {resolved_url}".casefold()
            path_lower = urlparse(resolved_url).path.casefold()
            if any(marker in lower for marker in IGNORED_DOWNLOAD_URL_MARKERS):
                continue
            score = 0
            if any(path_lower.endswith(ext) for ext in SUPPORTED_DOWNLOAD_EXTENSIONS):
                score += 100
            if any(marker in lower for marker in DOWNLOAD_TEXT_MARKERS):
                score += 30
            if any(marker in lower for marker in DOWNLOAD_URL_MARKERS):
                score += 40
            if any(marker in lower for marker in READ_TEXT_MARKERS):
                score -= 20
            if score <= 0:
                continue
            seen.add(resolved_url)
            candidates.append((score, resolved_url))
        candidates.sort(key=lambda item: item[0], reverse=True)
        return [url for _, url in candidates]

    def _extract_read_links(self, site: TargetSite, soup: BeautifulSoup, current_url: str) -> list[str]:
        current_clean = current_url.split("#", 1)[0]
        candidates: list[tuple[int, str]] = []
        seen: set[str] = set()

        def add_candidate(raw_url: str, text_value: str, extra: str = "") -> None:
            if not raw_url or raw_url.startswith(("#", "javascript:", "mailto:")):
                return
            full_url = self._resolve_special_link(current_url, raw_url)
            if not full_url or full_url in seen:
                return
            if full_url.split("#", 1)[0] == current_clean:
                return
            if not self._is_valid_read_link(site, current_url, full_url):
                return
            lower = f"{raw_url} {text_value} {extra} {full_url}".casefold()
            score = 0
            if any(marker in lower for marker in READ_TEXT_MARKERS):
                score += 60
            if any(marker in lower for marker in ("/read", "/online", "reader", "sample", "fragment")):
                score += 25
            if any(marker in lower for marker in DOWNLOAD_URL_MARKERS):
                score -= 40
            if score <= 0:
                return
            seen.add(full_url)
            candidates.append((score, full_url))

        for anchor in soup.select("a[href]"):
            href = clean_spaces(anchor.get("href") or "")
            text_value = clean_spaces(anchor.get_text(" ", strip=True))
            rel_values = " ".join(anchor.get("rel", []))
            add_candidate(href, text_value, rel_values)

        for node in soup.select("[data-u], [data-url], [data-href]"):
            raw_url = clean_spaces(node.get("data-u") or node.get("data-url") or node.get("data-href") or "")
            text_value = clean_spaces(node.get_text(" ", strip=True))
            extra = " ".join(
                part for part in [
                    node.get("id") or "",
                    " ".join(node.get("class", [])),
                    node.get("title") or "",
                ]
                if part
            )
            add_candidate(raw_url, text_value, extra)

        candidates.sort(key=lambda item: item[0], reverse=True)
        return [url for _, url in candidates]

    def _is_valid_read_link(self, site: TargetSite, current_url: str, full_url: str) -> bool:
        parsed = urlparse(full_url)
        path_lower = parsed.path.casefold()
        if any(marker in path_lower for marker in ("/reader/all/", "/author/", "/authors/", "/search", "/login")):
            return False

        current_identity = self._extract_book_identity(current_url)
        candidate_identity = self._extract_book_identity(full_url)

        if site.domain == "readli.net":
            return "/chitat-online/" in path_lower

        if site.domain == "bookzip.top":
            if "/reader/" not in path_lower:
                return False
            if current_identity and candidate_identity:
                return current_identity == candidate_identity
            return True

        if site.domain == "litmir.club":
            if "/reader/" not in path_lower:
                return False
            if current_identity and candidate_identity:
                return current_identity == candidate_identity
            return True

        if site.domain in {"rulit.me", "www.rulit.me"}:
            if not any(marker in path_lower for marker in ("/reader/", "/read/")):
                return False
            if current_identity and candidate_identity:
                return current_identity == candidate_identity

        return True

    def _extract_book_identity(self, url: str) -> str | None:
        parsed = urlparse(url)
        query = parse_qs(parsed.query)
        for key in ("b", "id"):
            values = query.get(key)
            if values and re.fullmatch(r"\d{4,}", values[0]):
                return values[0]

        path_matches = re.findall(r"\d{4,}", parsed.path)
        if path_matches:
            return path_matches[-1]
        return None

    def _extract_inline_chain(
        self,
        site: TargetSite,
        start_url: str,
        title: str,
        author: str,
        *,
        initial_soup: BeautifulSoup | None = None,
        initial_text: str | None = None,
    ) -> dict[str, object] | None:
        visited: set[str] = set()
        content_hashes: set[str] = set()
        parts: list[str] = []
        status_parts: list[str] = []
        current_url = start_url
        soup = initial_soup
        page_text = initial_text
        final_url = start_url

        for _ in range(INLINE_CHAIN_MAX_PAGES):
            clean_url = current_url.split("#", 1)[0]
            if clean_url in visited:
                break
            visited.add(clean_url)

            if soup is None:
                try:
                    snapshot = self.http.get(current_url)
                except Exception:
                    break
                final_url = snapshot.url
                soup = BeautifulSoup(snapshot.text, "html.parser")
                page_text = clean_spaces(soup.get_text(" ", strip=True))
            else:
                final_url = current_url
                page_text = page_text or clean_spaces(soup.get_text(" ", strip=True))

            content = self._extract_content_text(site, soup, title, author)
            if not content:
                break

            normalized = clean_spaces(content)
            if normalized:
                if parts:
                    previous = parts[-1]
                    if normalized == previous:
                        pass
                    elif normalized.startswith(previous) and len(normalized) > len(previous):
                        content_hashes.discard(previous)
                        content_hashes.add(normalized)
                        parts[-1] = normalized
                    elif previous.startswith(normalized):
                        pass
                    elif normalized not in content_hashes:
                        content_hashes.add(normalized)
                        parts.append(normalized)
                elif normalized not in content_hashes:
                    content_hashes.add(normalized)
                    parts.append(normalized)
            if page_text:
                status_parts.append(page_text)

            next_url = self._find_next_page_url(soup, final_url, visited)
            if not next_url:
                break
            current_url = next_url
            soup = None
            page_text = None

        if not parts:
            return None
        full_text = clean_spaces(" ".join(parts))
        return {
            "url": final_url,
            "char_count": count_text_characters(full_text),
            "pages": len(parts),
            "status_text": clean_spaces(" ".join(status_parts)),
        }

    def _resolve_special_link(self, current_url: str, raw_url: str) -> str | None:
        full_url = urljoin(current_url, raw_url)
        parsed = urlparse(full_url)
        if "showqrcode" in parsed.path.casefold():
            params = parse_qs(parsed.query)
            encoded = params.get("Text") or params.get("text")
            if encoded and encoded[0]:
                return urljoin(current_url, unquote(encoded[0]))
        return full_url

    def _find_next_page_url(self, soup: BeautifulSoup, current_url: str, visited: set[str]) -> str | None:
        candidates: list[tuple[int, str]] = []
        seen: set[str] = set()
        current_parsed = urlparse(current_url)
        current_host = current_parsed.netloc.lower().removeprefix("www.")
        current_path = current_parsed.path
        current_page_number = self._extract_page_number(current_url)

        for anchor in soup.select("a[href]"):
            href = clean_spaces(anchor.get("href") or "")
            if not href or href.startswith(("#", "javascript:", "mailto:")):
                continue
            full_url = urljoin(current_url, href)
            clean_full = full_url.split("#", 1)[0]
            if clean_full in visited or clean_full in seen:
                continue

            next_parsed = urlparse(full_url)
            next_host = next_parsed.netloc.lower().removeprefix("www.")
            if next_host and next_host != current_host:
                continue
            if not self._same_pagination_context(current_parsed, next_parsed):
                continue

            text_value = clean_spaces(anchor.get_text(" ", strip=True))
            rel_values = " ".join(anchor.get("rel", [])).casefold()
            lower = f"{href} {text_value} {rel_values}".casefold()
            classes = " ".join(anchor.get("class", [])).casefold()
            next_page_number = self._extract_page_number(full_url)
            score = 0

            if current_page_number is not None and next_page_number is not None:
                if next_page_number == current_page_number + 1:
                    score += 140
                elif next_page_number > current_page_number:
                    score += max(10, 60 - min(next_page_number - current_page_number, 50))
                else:
                    score -= 30

            if text_value.isdigit() and current_page_number is not None:
                numeric_text = int(text_value)
                if numeric_text == current_page_number + 1:
                    score += 80
                elif numeric_text > current_page_number:
                    score += 15

            if next_parsed.path == current_path:
                score += 15
            if "next" in rel_values:
                score += 100
            if any(marker in lower for marker in NEXT_TEXT_MARKERS):
                score += 50
            if "next" in classes or "pager" in classes or "pagination" in classes:
                score += 20
            if any(marker in lower for marker in DOWNLOAD_TEXT_MARKERS):
                score -= 50
            if score <= 0:
                continue
            seen.add(clean_full)
            candidates.append((score, full_url))
        if not candidates:
            return None
        candidates.sort(key=lambda item: item[0], reverse=True)
        return candidates[0][1]

    def _same_pagination_context(self, current_parsed, next_parsed) -> bool:
        current_query = parse_qs(current_parsed.query)
        next_query = parse_qs(next_parsed.query)
        paging_keys = {"page", "pg", "p", "str"}
        current_keys = [key for key in current_query if key not in paging_keys]
        if not current_keys:
            return True
        for key in current_keys:
            current_values = tuple(current_query.get(key) or [])
            next_values = tuple(next_query.get(key) or [])
            if not next_values:
                return False
            if current_values != next_values:
                return False
        return True

    def _extract_page_number(self, url: str) -> int | None:
        parsed = urlparse(url)
        query = parse_qs(parsed.query)
        for key in ("page", "pg", "p"):
            values = query.get(key)
            if values and values[0].isdigit():
                return int(values[0])

        path = parsed.path.casefold()
        match = re.search(r"(?:page|pg|str|p)[=/\-](\d{1,4})(?:/|$)", path)
        if match:
            return int(match.group(1))
        return None

    def _extract_title(self, site: TargetSite, soup: BeautifulSoup, hit: SearchHit) -> str:
        if site.domain == "litmir.club":
            title_tag = soup.find("title")
            if title_tag:
                title = clean_spaces(title_tag.get_text(" ", strip=True))
                return title.removeprefix('Книга "').split('" - ')[0]

        title = ""
        for candidate in [soup.find("h1"), soup.find("title")]:
            if candidate and candidate.get_text(strip=True):
                title = clean_spaces(candidate.get_text(" ", strip=True))
                break
        if not title:
            title = hit.title

        if site.domain in TITLE_AUTHOR_DOMAINS and " - " in title:
            title = title.rsplit(" - ", 1)[0]
        if site.domain in TITLE_PREFIX_AUTHOR_DOMAINS and " - " in title:
            title = title.split(" - ", 1)[1]
        return self._clean_title(title)

    def _extract_author(self, site: TargetSite, soup: BeautifulSoup, text: str, fallback: str) -> str:
        title_tag = soup.find("title")
        title_text = clean_spaces(title_tag.get_text(" ", strip=True)) if title_tag else ""

        if site.domain == "litmir.club":
            if " - " in title_text:
                parts = title_text.split(" - ")
                if len(parts) >= 2:
                    return clean_spaces(parts[1])
            match = LITMIR_AUTHOR_RE.search(text)
            if match:
                return clean_spaces(match.group(1))

        if site.domain == "readli.net":
            match = READLI_AUTHOR_TITLE_RE.search(title_text)
            if match:
                return clean_spaces(match.group(1))

        if site.domain == "fb2.top":
            match = FB2_AUTHOR_RE.search(title_text)
            if match:
                return clean_spaces(match.group(1))

        meta_author = soup.find(attrs={"itemprop": "author"})
        if meta_author and meta_author.get_text(strip=True):
            candidate = clean_spaces(meta_author.get_text(" ", strip=True))
            if self._looks_like_author(candidate):
                return candidate

        detected = extract_author(text)
        if detected and self._looks_like_author(detected):
            return detected
        return fallback

    def _extract_page_count(self, site: TargetSite, text: str) -> int | None:
        if site.domain == "litmir.club":
            match = LITMIR_PAGES_RE.search(text)
            if match:
                return int(match.group(1))
        if site.domain == "readli.net":
            return None
        return extract_page_count(text)

    def _extract_file_size_kb(self, site: TargetSite, text: str) -> int | None:
        if site.domain == "litmir.club":
            match = LITMIR_SIZE_RE.search(text)
            if match:
                return int(float(match.group(1).replace(",", ".")))
        return extract_file_size_kb(text)

    def _extract_last_update(self, site: TargetSite, soup: BeautifulSoup, text: str) -> str | None:
        meta_value = self._extract_meta_last_update(soup)
        if meta_value:
            return meta_value
        if site.domain == "litmir.club":
            match = LITMIR_DATE_RE.search(text)
            if match:
                return clean_spaces(match.group(1))
        return extract_date(text)

    def _extract_meta_last_update(self, soup: BeautifulSoup) -> str | None:
        meta_keys = {"article:modified_time", "og:updated_time", "og:published_time", "lastmod", "date", "pubdate"}
        for meta in soup.find_all("meta"):
            key = (meta.get("property") or meta.get("name") or "").casefold()
            if key not in meta_keys:
                continue
            content = clean_spaces(meta.get("content") or "")
            if content:
                return content
        time_node = soup.find("time")
        if time_node:
            datetime_value = clean_spaces(time_node.get("datetime") or "")
            if datetime_value:
                return datetime_value
            text_value = clean_spaces(time_node.get_text(" ", strip=True))
            if text_value:
                return text_value
        return None

    def _extract_raw_status(self, site: TargetSite, text: str) -> str | None:
        lower = text.casefold()
        if site.domain == "litmir.club" and "книга закончена" in lower:
            return "completed-marker"
        if site.domain == "readli.net" and "читать онлайн" in lower:
            return "full-text-marker"
        return infer_raw_status(text)

    def _extract_meta_text(self, soup: BeautifulSoup) -> str:
        parts: list[str] = []
        for meta in soup.find_all("meta"):
            key = (meta.get("name") or meta.get("property") or "").casefold()
            if key not in {"description", "og:description", "twitter:description"}:
                continue
            content = clean_spaces(meta.get("content") or "")
            if content:
                parts.append(content)
        return clean_spaces(" ".join(parts))

    def _extract_content_text(self, site: TargetSite, soup: BeautifulSoup, title: str, author: str) -> str | None:
        selectors = CONTENT_SELECTORS.get(site.domain, []) or []
        selectors = [*selectors, *[selector for selector in GENERIC_CONTENT_SELECTORS if selector not in selectors]]

        candidates: list[str] = []
        for selector in selectors:
            node = soup.select_one(selector)
            if not node:
                continue
            text = self._extract_node_reading_text(node)
            if text:
                candidates.append(text)
        if not candidates:
            return None

        ranked = sorted(candidates, key=len, reverse=True)
        for raw_content in ranked:
            content = self._trim_to_content_start(site.domain, raw_content)
            content = self._trim_content_tail(content)
            content = self._drop_repeated_title_author(content, title, author)
            content = clean_spaces(content)
            if self._looks_like_content(content):
                return content
        return None

    def _extract_any_content_text(self, site: TargetSite, soup: BeautifulSoup, title: str, author: str) -> str | None:
        selectors = CONTENT_SELECTORS.get(site.domain, []) or []
        selectors = [*selectors, *[selector for selector in GENERIC_CONTENT_SELECTORS if selector not in selectors]]

        candidates: list[str] = []
        for selector in selectors:
            node = soup.select_one(selector)
            if not node:
                continue
            text = self._extract_node_reading_text(node)
            if not text:
                continue
            text = self._trim_to_content_start(site.domain, text)
            text = self._trim_content_tail(text)
            text = self._drop_repeated_title_author(text, title, author)
            text = clean_spaces(text)
            if text:
                candidates.append(text)

        if not candidates:
            return None
        return sorted(candidates, key=len, reverse=True)[0]

    def _extract_node_reading_text(self, node) -> str | None:
        fragment = BeautifulSoup(str(node), "html.parser")
        for selector in NOISY_CONTENT_SELECTORS:
            for bad in fragment.select(selector):
                bad.decompose()

        paragraphs: list[str] = []
        for block in fragment.find_all(["p", "li", "blockquote", "h1", "h2", "h3", "h4"]):
            text = clean_spaces(block.get_text("", strip=False))
            if self._looks_like_paragraph(text):
                paragraphs.append(text)

        if not paragraphs:
            for block in fragment.find_all(["div", "section", "article"]):
                if block.find(["div", "section", "article", "p", "li", "blockquote"]):
                    continue
                text = clean_spaces(block.get_text("", strip=False))
                if self._looks_like_paragraph(text):
                    paragraphs.append(text)

        if not paragraphs:
            text = clean_spaces(fragment.get_text("", strip=False))
            return text if text else None
        return clean_spaces(" ".join(dict.fromkeys(paragraphs)))

    def _trim_to_content_start(self, domain: str, text: str) -> str:
        markers = START_MARKERS.get(domain, [])
        if not markers:
            return text

        lowered = text.casefold()
        best_idx = None
        best_marker = None
        for marker in markers:
            idx = lowered.rfind(marker.casefold())
            if idx != -1 and (best_idx is None or idx > best_idx):
                best_idx = idx
                best_marker = marker
        if best_idx is None or best_marker is None:
            return text
        start = best_idx + len(best_marker)
        return text[start:].lstrip(" :-—")

    def _trim_content_tail(self, text: str) -> str:
        result = text
        lowered = result.casefold()
        for marker in TAIL_CUT_MARKERS:
            idx = lowered.find(marker.casefold())
            if idx != -1:
                result = result[:idx]
                lowered = result.casefold()
        return result

    def _drop_repeated_title_author(self, text: str, title: str, author: str) -> str:
        result = clean_spaces(text)
        for fragment in [title, author, f"{title} - {author}", f"{author} - {title}"]:
            fragment = clean_spaces(fragment)
            if fragment and result.startswith(fragment):
                result = result[len(fragment):].lstrip(" :-—")
        result = re.sub(r"^(Название:\s*[^А-Яа-яA-Za-z0-9]+)?", "", result)
        return clean_spaces(result)

    def _clean_title(self, title: str) -> str:
        cleaned = clean_spaces(title)
        cleaned = re.sub(r"\s*-\s*Скачать книгу.*$", "", cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r"\s*[–-]\s*читать онлайн.*$", "", cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r"\s*[–-]\s*скачать бесплатно.*$", "", cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r"\s+[–-]\s+скачать.*$", "", cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r"\s*:\s*$", "", cleaned)
        return clean_spaces(cleaned)

    def _looks_like_author(self, value: str) -> bool:
        candidate = clean_spaces(value)
        lower = candidate.casefold()
        if not candidate:
            return False
        if len(candidate) > 80:
            return False
        if any(marker in lower for marker in NOISY_AUTHOR_MARKERS):
            return False
        if len(candidate.split()) > 6:
            return False
        return True

    def _looks_like_paragraph(self, value: str) -> bool:
        candidate = clean_spaces(value)
        if len(candidate) < 60:
            return False
        letters = sum(ch.isalpha() for ch in candidate)
        return letters >= 40

    def _looks_like_content(self, value: str) -> bool:
        candidate = clean_spaces(value)
        if len(candidate) < 250:
            return False
        letters = sum(ch.isalpha() for ch in candidate)
        return letters >= 180
