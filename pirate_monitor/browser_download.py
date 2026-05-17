from __future__ import annotations

import shutil
import subprocess
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit


DOWNLOADABLE_SUFFIXES = {".epub", ".fb2", ".txt", ".zip", ".rtf", ".pdf"}
PLAYWRIGHT_CLI_PACKAGE = "@playwright/cli"
PLAYWRIGHT_ARTIFACT_DIRNAME = ".playwright-cli"
SAFE_URL_SCHEMES = {"http", "https"}


@dataclass(slots=True)
class BrowserDownloadResult:
    content: bytes
    file_name: str


def download_book_bytes_via_browser(
    page_url: str,
    target_url: str,
    *,
    base_temp_dir: str | Path | None = None,
    timeout_seconds: int = 45,
    max_attempts: int = 3,
) -> BrowserDownloadResult | None:
    if not _is_safe_web_url(page_url) or not _is_safe_web_url(target_url):
        return None

    npx_path = shutil.which("npx")
    if not npx_path:
        return None

    root_dir = Path(base_temp_dir) if base_temp_dir else Path.cwd() / "output" / "playwright"
    root_dir.mkdir(parents=True, exist_ok=True)

    for _ in range(max_attempts):
        result = _download_once(npx_path, page_url, target_url, root_dir=root_dir, timeout_seconds=timeout_seconds)
        if result:
            return result
    return None


def _download_once(
    npx_path: str,
    page_url: str,
    target_url: str,
    *,
    root_dir: Path,
    timeout_seconds: int,
) -> BrowserDownloadResult | None:
    workdir = Path(tempfile.mkdtemp(prefix="pm-browser-", dir=root_dir))
    downloaded_path: Path | None = None

    try:
        _run_cli(_playwright_command(npx_path, "open", page_url), cwd=workdir, timeout_seconds=timeout_seconds)
        _run_cli(
            _playwright_command(npx_path, "run-code", _build_download_script(target_url)),
            cwd=workdir,
            timeout_seconds=timeout_seconds,
        )
        downloaded_path = _wait_for_downloaded_file(workdir / PLAYWRIGHT_ARTIFACT_DIRNAME)
    except Exception:
        return None
    finally:
        try:
            _run_cli(_playwright_command(npx_path, "close"), cwd=workdir, timeout_seconds=10)
        except Exception:
            pass

    try:
        if not downloaded_path:
            return None
        return BrowserDownloadResult(content=_read_bytes_with_retry(downloaded_path), file_name=downloaded_path.name)
    except Exception:
        return None
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


def _playwright_command(npx_path: str, *args: str) -> list[str]:
    return [npx_path, "--yes", PLAYWRIGHT_CLI_PACKAGE, *args]


def _run_cli(command: list[str], *, cwd: Path, timeout_seconds: int) -> None:
    subprocess.run(
        command,
        cwd=str(cwd),
        check=True,
        timeout=timeout_seconds,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="ignore",
    )


def _wait_for_downloaded_file(artifact_dir: Path, *, wait_seconds: float = 5.0) -> Path | None:
    deadline = time.time() + wait_seconds
    while time.time() < deadline:
        candidate = _pick_downloaded_file(artifact_dir)
        if candidate:
            return candidate
        time.sleep(0.25)
    return _pick_downloaded_file(artifact_dir)


def _read_bytes_with_retry(path: Path, *, wait_seconds: float = 5.0) -> bytes:
    deadline = time.time() + wait_seconds
    last_error: Exception | None = None
    while time.time() < deadline:
        try:
            return path.read_bytes()
        except OSError as exc:
            last_error = exc
            time.sleep(0.25)
    if last_error is not None:
        raise last_error
    return path.read_bytes()


def _pick_downloaded_file(artifact_dir: Path) -> Path | None:
    if not artifact_dir.exists():
        return None
    candidates = [path for path in artifact_dir.iterdir() if path.is_file() and path.suffix.casefold() in DOWNLOADABLE_SUFFIXES]
    if not candidates:
        return None
    candidates.sort(key=lambda path: (path.stat().st_mtime, path.stat().st_size), reverse=True)
    return candidates[0]


def _build_download_script(target_url: str) -> str:
    target_absolute = _js_escape(target_url.split('#', 1)[0])
    parts = urlsplit(target_url)
    target_relative = _js_escape(parts.path + (("?" + parts.query) if parts.query else ""))
    return (
        "async page => {"
        f"const targetAbsolute = '{target_absolute}';"
        f"const targetRelative = '{target_relative}';"
        "const links = page.locator('a[href]');"
        "const count = await links.count();"
        "let target = null;"
        "for (let i = 0; i < count; i += 1) {"
        "  const rawHref = ((await links.nth(i).getAttribute('href')) || '').split('#')[0];"
        "  if (!rawHref) continue;"
        "  if (rawHref === targetAbsolute || rawHref === targetRelative || rawHref.endsWith(targetRelative)) {"
        "    target = links.nth(i);"
        "    break;"
        "  }"
        "}"
        "if (!target) throw new Error('download link not found');"
        "await Promise.all([page.waitForEvent('download', { timeout: 30000 }), target.evaluate(el => el.click())]);"
        "}"
    )


def _js_escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace("'", "\\'")


def _is_safe_web_url(value: str) -> bool:
    parts = urlsplit(value)
    return parts.scheme.casefold() in SAFE_URL_SCHEMES and bool(parts.netloc)
