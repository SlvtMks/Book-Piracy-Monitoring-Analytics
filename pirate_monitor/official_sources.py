from __future__ import annotations

from dataclasses import dataclass
import re
from urllib.parse import urlparse

from pirate_monitor.models import TargetSite
from pirate_monitor.normalization import clean_spaces, normalize_text
from pirate_monitor.search import BingSearchProvider, SearchCoordinator, YandexSearchProvider
from pirate_monitor.sources.author_today import AuthorTodaySource
from pirate_monitor.sources.litgorod import LitgorodSource
from pirate_monitor.sources.litmarket import LitmarketSource
from pirate_monitor.sources.litnet import LitnetSource
from pirate_monitor.sources.litres import LitresSource


@dataclass(frozen=True, slots=True)
class OfficialSourceInfo:
    key: str
    title: str
    domains: tuple[str, ...]
    implemented: bool
    notes: str = ""


OFFICIAL_SOURCES: dict[str, OfficialSourceInfo] = {
    "litnet": OfficialSourceInfo("litnet", "Litnet", ("litnet.com",), True),
    "author_today": OfficialSourceInfo("author_today", "Author.Today", ("author.today",), True),
    "litres": OfficialSourceInfo("litres", "LitRes", ("litres.ru",), True),
    "litgorod": OfficialSourceInfo(
        "litgorod",
        "LitGorod",
        ("litgorod.ru",),
        True,
        "Supported via author profile URL /profile/<id>/books.",
    ),
    "litmarket": OfficialSourceInfo(
        "litmarket",
        "Litmarket",
        ("litmarket.ru",),
        True,
        "Supported via author URL /<slug>-p<id> or /<slug>-p<id>/books.",
    ),
}


SOURCE_FACTORIES = {
    "litnet": LitnetSource,
    "author_today": AuthorTodaySource,
    "litres": LitresSource,
    "litgorod": LitgorodSource,
    "litmarket": LitmarketSource,
}


AUTHOR_URL_PATTERNS: dict[str, tuple[re.Pattern[str], ...]] = {
    "litnet": (re.compile(r"/[^/]*-u\d+/?$", re.IGNORECASE),),
    "author_today": (re.compile(r"/u/[^/?#]+(?:/works)?/?$", re.IGNORECASE),),
    "litres": (re.compile(r"/author/[^/?#]+/?$", re.IGNORECASE),),
    "litgorod": (re.compile(r"/profile/\d+/books/?$", re.IGNORECASE),),
    "litmarket": (re.compile(r"/[^/?#]*-p\d+(?:/books)?/?$", re.IGNORECASE),),
}

BOOK_URL_PATTERNS: dict[str, tuple[re.Pattern[str], ...]] = {
    "litnet": (re.compile(r"/book/", re.IGNORECASE),),
    "author_today": (re.compile(r"/work/\d+", re.IGNORECASE),),
    "litres": (re.compile(r"/(?:book|audiobook)/", re.IGNORECASE),),
    "litgorod": (re.compile(r"/books/view/", re.IGNORECASE),),
    "litmarket": (re.compile(r"/books?/", re.IGNORECASE), re.compile(r"/[^/?#]*-b\d+", re.IGNORECASE)),
}


def official_source_choices(*, implemented_only: bool = False) -> list[str]:
    keys = [key for key, info in OFFICIAL_SOURCES.items() if not implemented_only or info.implemented]
    return ["auto", *keys]


def source_title(source_key: str) -> str:
    info = OFFICIAL_SOURCES.get(source_key)
    return info.title if info else source_key


def detect_official_source(author_url: str) -> str | None:
    domain = urlparse(author_url).netloc.lower().removeprefix("www.")
    if not domain:
        return None
    for key, info in OFFICIAL_SOURCES.items():
        if any(domain == candidate or domain.endswith(f".{candidate}") for candidate in info.domains):
            return key
    return None


def resolve_official_source(http, author_url: str, preferred_source: str = "auto"):
    source_key = preferred_source if preferred_source != "auto" else detect_official_source(author_url)
    if not source_key:
        raise ValueError(
            "Could not detect official source from URL. Use --official-source or pass a Litnet / Author.Today / LitRes / LitGorod / Litmarket author URL."
        )

    info = OFFICIAL_SOURCES.get(source_key)
    if not info:
        raise ValueError(f"Unknown official source: {source_key}")

    factory = SOURCE_FACTORIES.get(source_key)
    if factory is None:
        details = f" {info.notes}" if info.notes else ""
        raise NotImplementedError(f"Official source {info.title} is not implemented yet.{details}")
    return factory(http)


def resolve_source_reference(http, *, author_url: str | None = None, author_name: str | None = None, preferred_source: str = "auto"):
    normalized_url = clean_spaces(author_url or "")
    normalized_name = clean_spaces(author_name or "")

    if normalized_url:
        return resolve_official_source(http, normalized_url, preferred_source=preferred_source), normalized_url
    if not normalized_name:
        raise ValueError("Need either an author URL or an author name.")
    if preferred_source == "auto":
        raise ValueError("When searching by author name, specify --official-source.")

    source = resolve_official_source(http, f"https://{OFFICIAL_SOURCES[preferred_source].domains[0]}/", preferred_source=preferred_source)
    resolved_author_url = find_author_url(http, preferred_source, normalized_name)
    if not resolved_author_url:
        raise RuntimeError(f"Could not find author '{normalized_name}' on source {source_title(preferred_source)}.")
    return source, resolved_author_url


def resolve_book_reference(
    http,
    *,
    author_name: str | None = None,
    book_title: str | None = None,
    preferred_source: str = "auto",
    book_url: str | None = None,
):
    normalized_author = clean_spaces(author_name or "")
    normalized_title = clean_spaces(book_title or "")
    normalized_book_url = clean_spaces(book_url or "")

    if normalized_book_url:
        source = resolve_official_source(http, normalized_book_url, preferred_source=preferred_source)
        if not _looks_like_book_url(source.key, normalized_book_url):
            raise ValueError(f"Direct URL does not look like a book page for source {source_title(source.key)}.")
    else:
        if not normalized_author or not normalized_title:
            raise ValueError("When searching by book, specify both author name and book title.")
        if preferred_source == "auto":
            raise ValueError("When searching by book title, specify --official-source.")

        source = resolve_official_source(http, f"https://{OFFICIAL_SOURCES[preferred_source].domains[0]}/", preferred_source=preferred_source)
        normalized_book_url = find_book_url(http, preferred_source, normalized_author, normalized_title) or ""
        if not normalized_book_url:
            raise RuntimeError(
                f"Could not find official page for book '{normalized_title}' by '{normalized_author}' on source {source_title(preferred_source)}."
            )

    fetch_book = getattr(source, "fetch_book_reference", None)
    if not callable(fetch_book):
        raise NotImplementedError(f"Direct book resolution is not implemented for source {source_title(source.key)}.")

    book, resolved_author_url = fetch_book(normalized_book_url, expected_author=normalized_author or None)
    if not book:
        if normalized_title and normalized_author:
            raise RuntimeError(
                f"Could not confirm official page for book '{normalized_title}' by '{normalized_author}' on source {source_title(source.key)}."
            )
        raise RuntimeError(f"Could not confirm official book page by URL: {normalized_book_url}.")
    return source, resolved_author_url, book


def find_author_url(http, source_key: str, author_name: str) -> str | None:
    info = OFFICIAL_SOURCES.get(source_key)
    if not info:
        raise ValueError(f"Unknown official source: {source_key}")

    site = TargetSite(
        name=info.title,
        base_url=f"https://{info.domains[0]}",
        complaint_format="official",
        parser="generic",
        enabled=True,
    )
    providers = [YandexSearchProvider(http), BingSearchProvider(http)]
    candidates: list[str] = []

    for provider in providers:
        for query in [f'"{author_name}"', author_name]:
            try:
                hits = provider.search(query, site, limit=10)
            except Exception:
                continue
            for hit in hits:
                if _looks_like_author_url(source_key, hit.url) and _looks_like_author_result(author_name, hit.title, hit.snippet, hit.url):
                    candidates.append(hit.url)

    seen: set[str] = set()
    for candidate in candidates:
        if candidate in seen:
            continue
        seen.add(candidate)
        return _normalize_found_author_url(source_key, candidate)
    return None


def find_book_url(http, source_key: str, author_name: str, book_title: str) -> str | None:
    info = OFFICIAL_SOURCES.get(source_key)
    if not info:
        raise ValueError(f"Unknown official source: {source_key}")

    site = TargetSite(
        name=info.title,
        base_url=f"https://{info.domains[0]}",
        complaint_format="official",
        parser="generic",
        enabled=True,
    )
    search = SearchCoordinator([YandexSearchProvider(http), BingSearchProvider(http)], http=http)
    hits, _attempts = search.search(author_name, book_title, site, limit=10)
    for hit in hits:
        if _looks_like_book_url(source_key, hit.url) and _looks_like_book_result(author_name, book_title, hit.title, hit.snippet, hit.url):
            return hit.url
    return None


def _looks_like_author_url(source_key: str, url: str) -> bool:
    parsed = urlparse(url)
    if not parsed.netloc:
        return False
    return any(pattern.search(parsed.path) for pattern in AUTHOR_URL_PATTERNS.get(source_key, ()))


def _looks_like_book_url(source_key: str, url: str) -> bool:
    parsed = urlparse(url)
    if not parsed.netloc:
        return False
    return any(pattern.search(parsed.path) for pattern in BOOK_URL_PATTERNS.get(source_key, ()))


def _looks_like_author_result(author_name: str, title: str, snippet: str, url: str) -> bool:
    author_tokens = {token for token in normalize_text(author_name).split() if len(token) >= 3}
    if not author_tokens:
        return False
    haystack_tokens = {
        token
        for token in normalize_text(" ".join([title or "", snippet or "", url or ""])).split()
        if len(token) >= 3
    }
    return author_tokens <= haystack_tokens or len(author_tokens & haystack_tokens) >= min(2, len(author_tokens))


def _looks_like_book_result(author_name: str, book_title: str, title: str, snippet: str, url: str) -> bool:
    author_tokens = {token for token in normalize_text(author_name).split() if len(token) >= 3}
    title_tokens = {token for token in normalize_text(book_title).split() if len(token) >= 3}
    haystack_tokens = {
        token
        for token in normalize_text(" ".join([title or "", snippet or "", url or ""])).split()
        if len(token) >= 3
    }
    return bool(title_tokens) and title_tokens <= haystack_tokens and (
        not author_tokens or author_tokens <= haystack_tokens or len(author_tokens & haystack_tokens) >= min(2, len(author_tokens))
    )


def _normalize_found_author_url(source_key: str, url: str) -> str:
    parsed = urlparse(url)
    path = parsed.path.rstrip("/")
    if source_key == "author_today":
        parts = [part for part in path.split("/") if part]
        if len(parts) >= 2 and parts[0] == "u":
            path = f"/u/{parts[1]}/works"
    elif source_key == "litgorod":
        match = re.search(r"/profile/(\d+)", path, re.IGNORECASE)
        if match:
            path = f"/profile/{match.group(1)}/books"
    elif source_key == "litmarket" and not path.endswith("/books"):
        path = path + "/books"
    return parsed._replace(path=path or "/", query="", fragment="").geturl()
