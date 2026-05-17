from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import parse_qs, unquote, urljoin, urlparse

from bs4 import BeautifulSoup, Tag

from pirate_monitor.models import SearchHit, TargetSite
from pirate_monitor.normalization import clean_spaces, normalize_text, similarity


RU_TO_LAT = {
    "а": "a",
    "б": "b",
    "в": "v",
    "г": "g",
    "д": "d",
    "е": "e",
    "ё": "e",
    "ж": "zh",
    "з": "z",
    "и": "i",
    "й": "y",
    "к": "k",
    "л": "l",
    "м": "m",
    "н": "n",
    "о": "o",
    "п": "p",
    "р": "r",
    "с": "s",
    "т": "t",
    "у": "u",
    "ф": "f",
    "х": "h",
    "ц": "c",
    "ч": "ch",
    "ш": "sh",
    "щ": "sch",
    "ъ": "",
    "ы": "y",
    "ь": "",
    "э": "e",
    "ю": "yu",
    "я": "ya",
}

SEARCH_PARAM_CANDIDATES = ["q", "query", "s", "search", "story", "find", "name", "title", "keyword", "keywords", "ask", "text", "zap", "sq"]
GENERIC_SEARCH_PATHS = [
    ("/search", "q"),
    ("/search", "query"),
    ("/search/", "q"),
    ("/search/", "query"),
    ("/index.php", "story"),
    ("/", "s"),
    ("/", "q"),
]
GENERIC_AUTHOR_PATHS = [
    "/author/{slug}",
    "/author/{slug}/",
    "/authors/{slug}",
    "/authors/{slug}/",
    "/authors/{slug}/all",
    "/avtor/{slug}",
    "/avtor/{slug}/",
    "/avtory/{slug}",
    "/avtory/{slug}/",
    "/writer/{slug}",
    "/writer/{slug}/",
    "/writers/{slug}",
    "/writers/{slug}/",
    "/creator/{slug}",
    "/creator/{slug}/",
    "/user/{slug}",
    "/user/{slug}/",
]
SKIP_PATH_PREFIXES = (
    "author",
    "authors",
    "avtor",
    "category",
    "categories",
    "genre",
    "genres",
    "tag",
    "tags",
    "serie",
    "series",
    "blog",
    "blogs",
    "comment",
    "comments",
    "support",
    "page",
    "pages",
    "login",
    "register",
    "account",
    "user",
    "users",
    "profile",
    "forum",
    "market",
    "promo",
    "litra",
    "creator",
    "catalog",
)
SKIP_SUBSTRINGS = ("/search", "?page=", "#comments", "/comments", "/reviews")
BLOCKED_GENERIC_DOMAINS = {"vb.topbook.me"}
DNS_ERROR_MARKERS = ("Failed to resolve", "getaddrinfo failed", "NameResolutionError")


def _extract_redirect_url(raw_url: str) -> str:
    parsed = urlparse(raw_url)
    query = parse_qs(parsed.query)
    for key in ["url", "uddg", "u"]:
        if key in query and query[key]:
            return unquote(query[key][0])
    if raw_url.startswith("//"):
        return "https:" + raw_url
    return raw_url


def transliterate(value: str) -> str:
    return "".join(RU_TO_LAT.get(char, char) for char in value.casefold())


def slugify_latin(value: str) -> str:
    value = transliterate(value)
    value = re.sub(r"[^a-z0-9]+", "-", value.casefold())
    return value.strip("-")


def slug_to_text(slug: str) -> str:
    slug = slug.strip("/").split("/")[-1]
    slug = slug.split("#")[0].split("?")[0]
    slug = re.sub(r"-\d+$", "", slug)
    slug = re.sub(r"^part-\d+$", "", slug)
    return clean_spaces(slug.replace("-", " "))


def build_search_queries(author_name: str, book_title: str) -> list[str]:
    variants = [
        f'"{author_name}" "{book_title}"',
        f"{author_name} {book_title}",
        f'"{book_title}" {author_name}',
        f"{book_title} {author_name}",
        book_title,
    ]
    seen: set[str] = set()
    result: list[str] = []
    for item in variants:
        normalized = clean_spaces(item)
        if normalized and normalized not in seen:
            seen.add(normalized)
            result.append(normalized)
    return result


def build_author_slugs(author_name: str) -> list[str]:
    parts = [part for part in clean_spaces(author_name).split(" ") if part]
    if len(parts) < 2:
        single = slugify_latin(author_name)
        return [single] if single else []
    first_last = slugify_latin(f"{parts[0]} {' '.join(parts[1:])}")
    last_first = slugify_latin(f"{parts[-1]} {' '.join(parts[:-1])}")
    variants = [last_first, first_last]
    result: list[str] = []
    seen: set[str] = set()
    for item in variants:
        if item and item not in seen:
            seen.add(item)
            result.append(item)
    return result


def _make_hit(site: TargetSite, query: str, url: str, title: str, snippet: str, provider: str, rank: int) -> SearchHit:
    return SearchHit(
        site_name=site.name,
        domain=site.domain,
        query=query,
        url=url,
        title=clean_spaces(title),
        snippet=clean_spaces(snippet),
        provider=provider,
        rank=rank,
    )


def _book_match_score(book_title: str, candidate_title: str, url: str) -> float:
    title_score = similarity(book_title, candidate_title)
    slug_score = similarity(slugify_latin(book_title), slug_to_text(url))
    return max(title_score, slug_score)


def _token_overlap(book_title: str, candidate_title: str, url: str) -> int:
    left = {token for token in normalize_text(book_title).split() if len(token) >= 4}
    right = {token for token in normalize_text(candidate_title).split() if len(token) >= 4}
    if not right:
        right = {token for token in normalize_text(slug_to_text(url)).split() if len(token) >= 4}
    return len(left & right)


def _is_probable_book_match(book_title: str, candidate_title: str, url: str) -> bool:
    score = _book_match_score(book_title, candidate_title, url)
    if score >= 0.82:
        return True
    normalized_left = normalize_text(book_title)
    normalized_right = normalize_text(candidate_title)
    if normalized_left and normalized_right and (
        normalized_left in normalized_right or normalized_right in normalized_left
    ):
        return True
    overlap = _token_overlap(book_title, candidate_title, url)
    significant_tokens = [token for token in normalized_left.split() if len(token) >= 4]
    min_overlap = min(3, max(1, len(significant_tokens)))
    return score >= 0.60 and overlap >= min_overlap


def _is_same_author_name(left: str, right: str) -> bool:
    left_tokens = {token for token in normalize_text(left).split() if len(token) >= 3}
    right_tokens = {token for token in normalize_text(right).split() if len(token) >= 3}
    if not left_tokens or not right_tokens:
        return False
    return left_tokens <= right_tokens or right_tokens <= left_tokens or len(left_tokens & right_tokens) >= 2


def _looks_like_generic_book_path(url: str, site_domain: str) -> bool:
    parsed = urlparse(url)
    domain = parsed.netloc.lower().removeprefix("www.")
    if domain and site_domain not in domain:
        return False

    path = parsed.path.strip("/")
    if not path:
        return False
    if any(fragment in url for fragment in SKIP_SUBSTRINGS):
        return False

    first_segment = path.split("/")[0].casefold()
    if first_segment in SKIP_PATH_PREFIXES:
        return False

    return len(path) > 3


@dataclass(slots=True)
class SearchProvider:
    name: str
    http: object

    def search(self, query: str, site: TargetSite, limit: int = 5) -> list[SearchHit]:
        raise NotImplementedError


class YandexSearchProvider(SearchProvider):
    def __init__(self, http) -> None:
        super().__init__(name="yandex", http=http)

    def search(self, query: str, site: TargetSite, limit: int = 5) -> list[SearchHit]:
        queries = [
            {"text": f"site:{site.domain} {query}", "lr": "213"},
            {"text": f"{query} site:{site.domain}", "lr": "213"},
        ]
        for params in queries:
            hits = self._search_once(site, query, limit, params)
            if hits:
                return hits
        return []

    def _search_once(self, site: TargetSite, original_query: str, limit: int, params: dict[str, str]) -> list[SearchHit]:
        snapshot = self.http.get("https://yandex.ru/search/", params=params)
        if snapshot.status_code >= 400:
            return []

        soup = BeautifulSoup(snapshot.text, "html.parser")
        results: list[SearchHit] = []
        seen_urls: set[str] = set()
        for anchor in soup.select("a[href]"):
            href = (anchor.get("href") or "").strip()
            title = clean_spaces(anchor.get_text(" ", strip=True))
            if not href or not title:
                continue
            resolved = _extract_redirect_url(href)
            domain = urlparse(resolved).netloc.lower().removeprefix("www.")
            if not domain or site.domain not in domain or resolved in seen_urls:
                continue
            container = anchor.find_parent(["li", "div", "article"])
            snippet = clean_spaces(container.get_text(" ", strip=True)) if container else ""
            seen_urls.add(resolved)
            results.append(_make_hit(site, original_query, resolved, title, snippet, self.name, len(results) + 1))
            if len(results) >= limit:
                break
        return results


class BingSearchProvider(SearchProvider):
    def __init__(self, http) -> None:
        super().__init__(name="bing", http=http)

    def search(self, query: str, site: TargetSite, limit: int = 5) -> list[SearchHit]:
        snapshot = self.http.get("https://www.bing.com/search", params={"q": f"site:{site.domain} {query}"})
        if snapshot.status_code >= 400:
            return []

        soup = BeautifulSoup(snapshot.text, "html.parser")
        results: list[SearchHit] = []
        seen_urls: set[str] = set()
        for anchor in soup.select("li.b_algo h2 a, a[href]"):
            href = (anchor.get("href") or "").strip()
            title = clean_spaces(anchor.get_text(" ", strip=True))
            if not href or not title:
                continue
            resolved = _extract_redirect_url(href)
            domain = urlparse(resolved).netloc.lower().removeprefix("www.")
            if not domain or site.domain not in domain or resolved in seen_urls:
                continue
            container = anchor.find_parent(["li", "div", "article"])
            snippet = clean_spaces(container.get_text(" ", strip=True)) if container else ""
            seen_urls.add(resolved)
            results.append(_make_hit(site, query, resolved, title, snippet, self.name, len(results) + 1))
            if len(results) >= limit:
                break
        return results


class SearchCoordinator:
    def __init__(self, providers: list[SearchProvider], http) -> None:
        self.providers = providers
        self.http = http
        self._homepage_cache: dict[str, tuple[BeautifulSoup, str] | None] = {}

    def search(self, author_name: str, book_title: str, site: TargetSite, limit: int = 5) -> tuple[list[SearchHit], list[str]]:
        attempts: list[str] = []

        direct_hits = self._search_direct(author_name, book_title, site, limit, attempts)
        if direct_hits:
            return direct_hits, attempts

        generic_hits = self._search_generic_internal(author_name, book_title, site, limit, attempts)
        if generic_hits:
            return generic_hits, attempts

        for query in build_search_queries(author_name, book_title):
            attempts.append(f"web:{query}")
            for provider in self.providers:
                try:
                    hits = provider.search(query, site, limit=limit)
                except Exception as exc:  # noqa: BLE001
                    attempts.append(f"web-error:{provider.name}:{exc}")
                    continue
                if hits:
                    return hits, attempts
        return [], attempts

    def _search_direct(self, author_name: str, book_title: str, site: TargetSite, limit: int, attempts: list[str]) -> list[SearchHit]:
        domain = site.domain
        if domain == "litmir.club":
            hits = self._search_litmir(author_name, book_title, site, limit, attempts)
            if hits:
                return hits
        elif domain == "readli.net":
            hits = self._search_readli(author_name, book_title, site, limit, attempts)
            if hits:
                return hits
        elif domain == "rulit.me":
            hits = self._search_rulit(author_name, book_title, site, limit, attempts)
            if hits:
                return hits
        elif domain == "fb2.top":
            hits = self._search_fb2_top(author_name, book_title, site, limit, attempts)
            if hits:
                return hits
        elif domain == "avidreaders.ru":
            hits = self._search_avidreaders(author_name, book_title, site, limit, attempts)
            if hits:
                return hits
        hits = self._search_generic_author_pages(author_name, book_title, site, limit, attempts)
        if hits:
            return hits
        return []

    def _search_generic_internal(self, author_name: str, book_title: str, site: TargetSite, limit: int, attempts: list[str]) -> list[SearchHit]:
        homepage_cache = self._homepage_cache.get(site.domain)
        homepage_blocked = False
        if homepage_cache is None and site.domain not in self._homepage_cache:
            try:
                homepage = self.http.get(site.base_url)
            except Exception as exc:  # noqa: BLE001
                attempts.append(f"direct:homepage-error:{exc}")
                self._homepage_cache[site.domain] = None
                if any(marker in str(exc) for marker in DNS_ERROR_MARKERS):
                    return []
                homepage = None
            if homepage and homepage.status_code < 400:
                homepage_cache = (BeautifulSoup(homepage.text, "html.parser"), homepage.url)
                self._homepage_cache[site.domain] = homepage_cache
            else:
                self._homepage_cache[site.domain] = None
                homepage_blocked = bool(homepage and homepage.status_code == 403)

        if homepage_blocked and site.domain in BLOCKED_GENERIC_DOMAINS:
            return []

        if homepage_cache:
            soup, homepage_url = homepage_cache
            hits = self._search_discovered_forms(soup, homepage_url, site, author_name, book_title, limit, attempts)
            if hits:
                return hits

        for path, param_name in GENERIC_SEARCH_PATHS:
            for query in [f"{author_name} {book_title}", book_title]:
                search_url = urljoin(site.base_url, path)
                attempts.append(f"direct:generic-search:{search_url}?{param_name}={query}")
                try:
                    snapshot = self.http.get(search_url, params={param_name: query})
                except Exception as exc:  # noqa: BLE001
                    attempts.append(f"direct:generic-search-error:{param_name}:{exc}")
                    continue
                if snapshot.status_code >= 400:
                    continue
                soup = BeautifulSoup(snapshot.text, "html.parser")
                hits = self._extract_hits_from_result_soup(
                    soup=soup,
                    site=site,
                    source_query=query,
                    provider=f"direct-generic-{param_name}",
                    limit=limit,
                    href_markers=[],
                    book_title=book_title,
                    custom_filter=lambda url, domain=site.domain: _looks_like_generic_book_path(url, domain),
                    base_url=snapshot.url,
                )
                if hits:
                    return hits

        for base_path in ["/index.php", "/"]:
            for query in [f"{author_name} {book_title}", book_title]:
                attempts.append(f"direct:dle-search:{base_path}?story={query}")
                try:
                    snapshot = self.http.get(
                        urljoin(site.base_url, base_path),
                        params={"do": "search", "subaction": "search", "story": query},
                    )
                except Exception as exc:  # noqa: BLE001
                    attempts.append(f"direct:dle-search-error:{exc}")
                    continue
                if snapshot.status_code >= 400:
                    continue
                soup = BeautifulSoup(snapshot.text, "html.parser")
                hits = self._extract_hits_from_result_soup(
                    soup=soup,
                    site=site,
                    source_query=query,
                    provider="direct-dle-search",
                    limit=limit,
                    href_markers=[],
                    book_title=book_title,
                    custom_filter=lambda url, domain=site.domain: _looks_like_generic_book_path(url, domain),
                    base_url=snapshot.url,
                )
                if hits:
                    return hits
        return []

    def _search_generic_author_pages(self, author_name: str, book_title: str, site: TargetSite, limit: int, attempts: list[str]) -> list[SearchHit]:
        for slug in build_author_slugs(author_name):
            for pattern in GENERIC_AUTHOR_PATHS:
                author_url = urljoin(site.base_url, pattern.format(slug=slug).lstrip("/"))
                attempts.append(f"direct:generic-author:{author_url}")
                try:
                    hits = self._extract_hits_from_author_page(
                        site,
                        author_name,
                        "direct-generic-author",
                        author_url,
                        [],
                        book_title,
                        limit,
                        custom_filter=lambda url, domain=site.domain: _looks_like_generic_book_path(url, domain),
                    )
                except Exception as exc:  # noqa: BLE001
                    attempts.append(f"direct:generic-author-error:{exc}")
                    continue
                if hits:
                    return hits
        return []

    def _search_discovered_forms(self, soup: BeautifulSoup, base_url: str, site: TargetSite, author_name: str, book_title: str, limit: int, attempts: list[str]) -> list[SearchHit]:
        for form in soup.select("form"):
            action = (form.get("action") or "").strip()
            method = (form.get("method") or "get").casefold()
            if method not in {"get", "", "post"}:
                continue
            if form.select('input[type="password"]'):
                continue

            text_inputs = [
                node
                for node in form.select('input[name], input[type="search"], input[type="text"]')
                if isinstance(node, Tag)
            ]
            param_name = None
            for node in text_inputs:
                name = (node.get("name") or "").strip()
                input_type = (node.get("type") or "").casefold()
                if input_type in {"search", "text", ""} and name in SEARCH_PARAM_CANDIDATES:
                    param_name = name
                    break
            if not param_name:
                continue

            params = {
                (node.get("name") or "").strip(): (node.get("value") or "").strip()
                for node in form.select('input[type="hidden"][name]')
                if (node.get("name") or "").strip()
            }
            form_url = urljoin(base_url, action or base_url)
            for query in [f"{author_name} {book_title}", book_title]:
                request_params = dict(params)
                request_params[param_name] = query
                attempts.append(f"direct:form-{method}:{form_url}?{param_name}={query}")
                try:
                    if method == "post":
                        snapshot = self.http.post(form_url, data=request_params)
                    else:
                        snapshot = self.http.get(form_url, params=request_params)
                except Exception as exc:  # noqa: BLE001
                    attempts.append(f"direct:form-{method}-error:{param_name}:{exc}")
                    continue
                if snapshot.status_code >= 400:
                    continue
                result_soup = BeautifulSoup(snapshot.text, "html.parser")
                hits = self._extract_hits_from_result_soup(
                    soup=result_soup,
                    site=site,
                    source_query=query,
                    provider=f"direct-form-{method}-{param_name}",
                    limit=limit,
                    href_markers=[],
                    book_title=book_title,
                    custom_filter=lambda url, domain=site.domain: _looks_like_generic_book_path(url, domain),
                    base_url=snapshot.url,
                )
                if hits:
                    return hits
        return []

    def _search_litmir(self, author_name: str, book_title: str, site: TargetSite, limit: int, attempts: list[str]) -> list[SearchHit]:
        attempts.append("direct:litmir-author-search")
        snapshot = self.http.get("https://litmir.club/SphinxSearch", params={"name": author_name})
        if snapshot.status_code >= 400:
            return []
        soup = BeautifulSoup(snapshot.text, "html.parser")
        author_url = None
        for anchor in soup.select('a[href*="/a/?id="]'):
            href = (anchor.get("href") or "").strip()
            text = clean_spaces(anchor.get_text(" ", strip=True))
            if _is_same_author_name(author_name, text):
                author_url = urljoin("https://litmir.club", href)
                break
        if not author_url:
            return []
        return self._extract_hits_from_author_page(site, author_name, "direct-litmir-author", author_url, ["/bd/?b="], book_title, limit)

    def _search_readli(self, author_name: str, book_title: str, site: TargetSite, limit: int, attempts: list[str]) -> list[SearchHit]:
        for slug in build_author_slugs(author_name):
            author_url = f"https://readli.net/avtor/{slug}/"
            attempts.append(f"direct:readli-author:{author_url}")
            try:
                hits = self._extract_hits_from_author_page(
                    site,
                    author_name,
                    "direct-readli-author",
                    author_url,
                    ["https://readli.net/", "/"],
                    book_title,
                    limit,
                    custom_filter=self._is_readli_book_link,
                )
                if hits:
                    return hits
            except Exception:
                continue

        attempts.append("direct:readli-internal-search")
        snapshot = self.http.get("https://readli.net/index.php", params={"story": f"{author_name} {book_title}"})
        if snapshot.status_code >= 400:
            return []
        soup = BeautifulSoup(snapshot.text, "html.parser")
        return self._extract_hits_from_result_soup(
            soup=soup,
            site=site,
            source_query=f"{author_name} {book_title}",
            provider="direct-readli-search",
            limit=limit,
            href_markers=["https://readli.net/"],
            book_title=book_title,
            custom_filter=self._is_readli_book_link,
        )

    def _search_rulit(self, author_name: str, book_title: str, site: TargetSite, limit: int, attempts: list[str]) -> list[SearchHit]:
        attempts.append("direct:rulit-author-search")
        snapshot = self.http.get("https://www.rulit.me/author/all/1/surname", params={"search": author_name})
        if snapshot.status_code >= 400:
            return []
        soup = BeautifulSoup(snapshot.text, "html.parser")
        author_url = None
        for anchor in soup.select('a[href*="/author/"]'):
            href = (anchor.get("href") or "").strip()
            text = clean_spaces(anchor.get_text(" ", strip=True))
            if href.endswith("/author/all/1/surname"):
                continue
            if _is_same_author_name(author_name, text):
                author_url = href
                break
        if not author_url:
            for slug in build_author_slugs(author_name):
                author_url = f"https://www.rulit.me/author/{slug}"
                break
        if not author_url:
            return []
        return self._extract_hits_from_author_page(site, author_name, "direct-rulit-author", urljoin("https://www.rulit.me", author_url), ["/books/"], book_title, limit)

    def _search_fb2_top(self, author_name: str, book_title: str, site: TargetSite, limit: int, attempts: list[str]) -> list[SearchHit]:
        attempts.append("direct:fb2-top-authors-letter")
        snapshot = self.http.get("https://fb2.top/authors/" + author_name[:1])
        if snapshot.status_code >= 400:
            return []
        soup = BeautifulSoup(snapshot.text, "html.parser")
        author_url = None
        for anchor in soup.select("a[href]"):
            href = (anchor.get("href") or "").strip()
            text = clean_spaces(anchor.get_text(" ", strip=True))
            if "/authors/" in href and _is_same_author_name(author_name, text):
                author_url = href
                break
        if not author_url:
            attempts.append("direct:fb2-top-yandex-site-search-book")
            return self._search_fb2_book(author_name, book_title, site, limit)
        return self._extract_hits_from_author_page(site, author_name, "direct-fb2-author", urljoin("https://fb2.top", author_url), ["/"], book_title, limit, custom_filter=self._is_fb2_book_link)

    def _search_fb2_book(self, author_name: str, book_title: str, site: TargetSite, limit: int) -> list[SearchHit]:
        snapshot = self.http.get(
            "https://yandex.ru/search/site/",
            params={"searchid": "2436731", "l10n": "ru", "reqenc": "utf-8", "text": f"{author_name} {book_title}"},
        )
        if snapshot.status_code >= 400:
            return []
        soup = BeautifulSoup(snapshot.text, "html.parser")
        return self._extract_hits_from_result_soup(soup, site, f"{author_name} {book_title}", "direct-fb2-yandex-search", limit, ["fb2.top/"], book_title, custom_filter=self._is_fb2_search_result_link)

    def _search_avidreaders(self, author_name: str, book_title: str, site: TargetSite, limit: int, attempts: list[str]) -> list[SearchHit]:
        for slug in build_author_slugs(author_name):
            for page in range(1, 6):
                author_url = f"https://avidreaders.ru/author/{slug}/"
                if page > 1:
                    author_url += f"{page}"
                attempts.append(f"direct:avidreaders-author:{author_url}")
                try:
                    hits = self._extract_hits_from_author_page(
                        site,
                        author_name,
                        "direct-avidreaders-author",
                        author_url,
                        ["/book/"],
                        book_title,
                        limit,
                    )
                except Exception:
                    hits = []
                if hits:
                    return hits

        letter = author_name[:1].casefold()
        if not letter:
            return []
        attempts.append(f"direct:avidreaders-authors-letter:{letter}")
        snapshot = self.http.get("https://avidreaders.ru/authors/", params={"letter": letter})
        if snapshot.status_code >= 400:
            return []
        soup = BeautifulSoup(snapshot.text, "html.parser")
        author_url = None
        for anchor in soup.select('a[href*="/author/"]'):
            href = (anchor.get("href") or "").strip()
            text = clean_spaces(anchor.get_text(" ", strip=True))
            if _is_same_author_name(author_name, text):
                author_url = href
                break
        if not author_url:
            return []
        return self._extract_hits_from_author_page(site, author_name, "direct-avidreaders-author", author_url, ["/book/"], book_title, limit)

    def _extract_hits_from_author_page(self, site: TargetSite, source_query: str, provider: str, author_page_url: str, href_markers: list[str], book_title: str, limit: int, custom_filter=None) -> list[SearchHit]:
        snapshot = self.http.get(author_page_url)
        if snapshot.status_code >= 400:
            return []
        soup = BeautifulSoup(snapshot.text, "html.parser")
        return self._extract_hits_from_result_soup(soup, site, source_query, provider, limit, href_markers, book_title, custom_filter=custom_filter, base_url=snapshot.url)

    def _extract_hits_from_result_soup(self, soup: BeautifulSoup, site: TargetSite, source_query: str, provider: str, limit: int, href_markers: list[str], book_title: str, custom_filter=None, base_url: str | None = None) -> list[SearchHit]:
        results: list[SearchHit] = []
        seen_urls: set[str] = set()
        for anchor in soup.select("a[href]"):
            href = (anchor.get("href") or "").strip()
            if not href:
                continue
            absolute = urljoin(base_url or site.base_url, href)
            if absolute in seen_urls:
                continue
            if custom_filter and not custom_filter(absolute):
                continue
            if not custom_filter and href_markers and not any(marker in absolute for marker in href_markers):
                continue

            text = clean_spaces(anchor.get_text(" ", strip=True))
            container = anchor.find_parent(["li", "div", "article", "tr", "section"])
            snippet = clean_spaces(container.get_text(" ", strip=True)) if container else ""
            derived_title = text or self._derive_title_from_url(absolute)
            if not _is_probable_book_match(book_title, derived_title, absolute):
                continue
            seen_urls.add(absolute)
            results.append(_make_hit(site, source_query, absolute, derived_title, snippet, provider, len(results) + 1))
            if len(results) >= limit:
                break
        return results

    def _derive_title_from_url(self, url: str) -> str:
        path = urlparse(url).path.strip("/")
        if path.endswith("/read"):
            path = path[:-5]
        return slug_to_text(path.split("/")[-1])

    def _is_readli_book_link(self, url: str) -> bool:
        parsed = urlparse(url)
        if parsed.netloc and "readli.net" not in parsed.netloc:
            return False
        path = parsed.path.strip("/")
        if not path or path.startswith(("avtor", "cat", "serie", "zhanryi")):
            return False
        if path.endswith("#comments") or "/#" in url:
            return False
        return path.count("/") == 0 and not path.startswith("login")

    def _is_fb2_book_link(self, url: str) -> bool:
        parsed = urlparse(url)
        path = parsed.path.strip("/")
        if path.startswith(("authors", "search", "genres", "new-books", "popular-books", "login", "l/genres")):
            return False
        if path.endswith("read"):
            return False
        return bool(path)

    def _is_fb2_search_result_link(self, url: str) -> bool:
        return "fb2.top" in url and ("/l/" in url or "/authors/" in url or "/read/" in url)


