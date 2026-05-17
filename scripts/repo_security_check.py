from __future__ import annotations

import re
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]

IGNORED_DIRS = {
    ".git",
    ".github",
    ".pytest_cache",
    ".pytest_basetemp_local",
    "__pycache__",
    "output",
    "reports",
    "tmp",
    "tests_tmp",
    "data",
    ".venv",
    "venv",
}

FORBIDDEN_FILE_PATTERNS = [
    ".env",
    ".env.local",
    ".env.production",
    "monitor.db",
]

TEXT_FILE_SUFFIXES = {
    ".py",
    ".md",
    ".txt",
    ".toml",
    ".json",
    ".yml",
    ".yaml",
    ".ini",
    ".cfg",
    ".env.example",
}

SECRET_PATTERNS = [
    re.compile(r"Authorization\s*[:=]\s*['\"]?(Bearer|Basic)\s+[A-Za-z0-9._~+/=-]{12,}", re.IGNORECASE),
    re.compile(r"GIGACHAT_AUTH_KEY\s*=\s*['\"]?[A-Za-z0-9._~+/=-]{20,}", re.IGNORECASE),
    re.compile(
        r"(API[_-]?KEY|AUTH[_-]?KEY|CLIENT[_-]?SECRET|BOT[_-]?TOKEN|PASSWORD|SECRET)\s*=\s*['\"]?[A-Za-z0-9._~+/=-]{16,}"
    ),
]


def iter_repo_files(root: Path):
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        if any(part in IGNORED_DIRS for part in path.relative_to(root).parts):
            continue
        yield path


def main() -> int:
    errors: list[str] = []

    for path in iter_repo_files(REPO_ROOT):
        if path.name in FORBIDDEN_FILE_PATTERNS or path.suffix == ".env":
            errors.append(f"Forbidden file in repo tree: {path.relative_to(REPO_ROOT)}")
            continue

        if path.suffix not in TEXT_FILE_SUFFIXES and path.name not in {".gitignore"}:
            continue

        try:
            content = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue

        for pattern in SECRET_PATTERNS:
            match = pattern.search(content)
            if match:
                errors.append(
                    f"Potential secret or authorization header in {path.relative_to(REPO_ROOT)}: {match.group(0)[:80]}"
                )

    if errors:
        print("Repository security check failed:")
        for item in errors:
            print(f"- {item}")
        return 1

    print("Repository security check passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
