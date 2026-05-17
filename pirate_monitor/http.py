from __future__ import annotations

import json
import os
import subprocess
import tempfile
import time
from dataclasses import dataclass
from typing import Optional
from urllib.parse import urlparse, urlunparse

import requests
import urllib3


urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0 Safari/537.36"
    ),
    "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8",
}
INSECURE_SSL_DOMAINS = {"vb.topbook.me", "novkniga.ru"}
NOVKNIGA_IP = "95.214.61.229"
TOPBOOK_NODE = r"F:\nodejs\node.exe"
TOPBOOK_CHALLENGE_MARKERS = ("document.cookie", "_gtyu", "location.reload")


@dataclass(slots=True)
class ResponseSnapshot:
    url: str
    status_code: int
    text: str
    content: bytes
    content_type: str


class HttpClient:
    def __init__(
        self,
        *,
        connect_timeout: int = 10,
        read_timeout: int = 60,
        min_delay_seconds: float = 1.0,
        max_retries: int = 3,
        retry_backoff_seconds: float = 2.0,
    ) -> None:
        self.connect_timeout = connect_timeout
        self.read_timeout = read_timeout
        self.min_delay_seconds = min_delay_seconds
        self.max_retries = max_retries
        self.retry_backoff_seconds = retry_backoff_seconds
        self._session = requests.Session()
        self._session.trust_env = False
        self._session.headers.update(DEFAULT_HEADERS)
        self._last_request_at = 0.0

    def clone(self) -> "HttpClient":
        return HttpClient(
            connect_timeout=self.connect_timeout,
            read_timeout=self.read_timeout,
            min_delay_seconds=self.min_delay_seconds,
            max_retries=self.max_retries,
            retry_backoff_seconds=self.retry_backoff_seconds,
        )

    def get(self, url: str, *, params: Optional[dict[str, str]] = None) -> ResponseSnapshot:
        return self._request("get", url, params=params)

    def post(self, url: str, *, data: Optional[dict[str, str]] = None) -> ResponseSnapshot:
        return self._request("post", url, data=data)

    def _request(
        self,
        method: str,
        url: str,
        *,
        params: Optional[dict[str, str]] = None,
        data: Optional[dict[str, str]] = None,
    ) -> ResponseSnapshot:
        last_error: Exception | None = None
        host = urlparse(url).netloc.lower().removeprefix("www.")

        for attempt in range(1, self.max_retries + 1):
            elapsed = time.time() - self._last_request_at
            if elapsed < self.min_delay_seconds:
                time.sleep(self.min_delay_seconds - elapsed)

            prepared_url, host_override, extra_headers, verify = self._prepare_request(url)
            try:
                if host == "coollib.xyz":
                    response = self._request_without_session(
                        method=method,
                        url=prepared_url,
                        params=params,
                        data=data,
                        headers=extra_headers,
                        verify=False,
                    )
                else:
                    response = self._session.request(
                        method.upper(),
                        prepared_url,
                        params=params,
                        data=data,
                        headers=extra_headers,
                        timeout=(self.connect_timeout, self.read_timeout),
                        verify=verify,
                        allow_redirects=True,
                    )
                self._last_request_at = time.time()
                if self._needs_topbook_cookie_retry(url, response):
                    solved = self._solve_topbook_challenge(response.text)
                    if solved:
                        name, value = solved.split("=", 1)
                        self._session.cookies.set(name, value, domain="vb.topbook.me")
                        response = self._session.request(
                            method.upper(),
                            prepared_url,
                            params=params,
                            data=data,
                            headers=extra_headers,
                            timeout=(self.connect_timeout, self.read_timeout),
                            verify=verify,
                            allow_redirects=True,
                        )
                        self._last_request_at = time.time()
                content_type = response.headers.get("content-type", "")
                response.encoding = response.encoding or "utf-8"
                final_url = self._normalize_response_url(original_url=url, response_url=response.url, host_override=host_override)
                return ResponseSnapshot(
                    url=final_url,
                    status_code=response.status_code,
                    text=response.text,
                    content=response.content,
                    content_type=content_type,
                )
            except (requests.exceptions.Timeout, requests.exceptions.ConnectionError, requests.exceptions.SSLError) as exc:
                last_error = exc
                if attempt >= self.max_retries:
                    break
                time.sleep(self.retry_backoff_seconds * attempt)

        assert last_error is not None
        raise RuntimeError(
            f"HTTP request failed for {url}: {last_error}. "
            f"connect_timeout={self.connect_timeout}, read_timeout={self.read_timeout}, retries={self.max_retries}"
        ) from last_error

    def _request_without_session(
        self,
        *,
        method: str,
        url: str,
        params: Optional[dict[str, str]],
        data: Optional[dict[str, str]],
        headers: Optional[dict[str, str]],
        verify: bool,
    ) -> requests.Response:
        merged_headers = dict(DEFAULT_HEADERS)
        if headers:
            merged_headers.update(headers)
        return requests.request(
            method.upper(),
            url,
            params=params,
            data=data,
            headers=merged_headers,
            timeout=(self.connect_timeout, self.read_timeout),
            verify=verify,
            allow_redirects=True,
        )

    def _prepare_request(self, url: str) -> tuple[str, str | None, dict[str, str] | None, bool]:
        parsed = urlparse(url)
        host = parsed.netloc.lower().removeprefix("www.")
        if host == "coollib.xyz":
            return self._force_http(url), None, None, False
        if host == "novkniga.ru":
            rewritten = parsed._replace(netloc=NOVKNIGA_IP)
            return (
                urlunparse(rewritten),
                "novkniga.ru",
                {"Host": "novkniga.ru", "Cookie": "beget=begetok"},
                False,
            )
        verify = host not in INSECURE_SSL_DOMAINS
        return url, None, None, verify

    def _normalize_response_url(self, *, original_url: str, response_url: str, host_override: str | None) -> str:
        if not host_override:
            return response_url
        parsed = urlparse(response_url)
        rewritten = parsed._replace(netloc=host_override)
        original = urlparse(original_url)
        if original.scheme:
            rewritten = rewritten._replace(scheme=original.scheme)
        return urlunparse(rewritten)

    def _force_http(self, url: str) -> str:
        parsed = urlparse(url)
        if parsed.scheme == "http":
            return url
        return urlunparse(parsed._replace(scheme="http"))

    def _needs_topbook_cookie_retry(self, original_url: str, response: requests.Response) -> bool:
        host = urlparse(original_url).netloc.lower().removeprefix("www.")
        if host != "vb.topbook.me":
            return False
        if response.status_code != 403:
            return False
        text = response.text or ""
        return all(marker in text for marker in TOPBOOK_CHALLENGE_MARKERS)

    def _solve_topbook_challenge(self, html: str) -> str | None:
        if not os.path.exists(TOPBOOK_NODE):
            return None
        node_code = self._build_topbook_solver_script(html)
        with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False, encoding="utf-8") as fh:
            fh.write(node_code)
            script_path = fh.name
        try:
            output = subprocess.check_output([TOPBOOK_NODE, script_path], text=True, timeout=20)
        except Exception:
            return None
        finally:
            try:
                os.unlink(script_path)
            except OSError:
                pass

        cookie_line = output.strip().splitlines()[-1].strip() if output.strip() else ""
        if not cookie_line or cookie_line.startswith("ERR:") or "=" not in cookie_line:
            return None
        return cookie_line.split(";", 1)[0]

    def _build_topbook_solver_script(self, html: str) -> str:
        return f'''
const html = {json.dumps(html)};
const match = html.match(/<script>([\\s\\S]+)<\\/script><\\/body>/i);
if (!match) {{ console.log('ERR:NO_SCRIPT'); process.exit(0); }}
const script = match[1];
let cookie = '';
const sandboxLocation = {{ reload(){{}}, href: 'https://vb.topbook.me/' }};
const sandboxDocument = {{ location: sandboxLocation }};
Object.defineProperty(sandboxDocument, 'cookie', {{
  set(v) {{ cookie = v; }},
  get() {{ return cookie; }},
}});
const sandboxWindow = {{ location: sandboxLocation, document: sandboxDocument }};
const sandboxNavigator = {{ userAgent: 'Mozilla/5.0' }};
const setTimeout = function(cb) {{ if (typeof cb === 'function') cb(); return 0; }};
const clearTimeout = function() {{}};
try {{
  const fn = new Function('document','location','window','navigator','setTimeout','clearTimeout', script + '; return document.cookie;');
  const result = fn(sandboxDocument, sandboxLocation, sandboxWindow, sandboxNavigator, setTimeout, clearTimeout);
  console.log(result || cookie || '');
}} catch (e) {{
  console.log('ERR:' + (e && e.stack || String(e)));
}}
'''

