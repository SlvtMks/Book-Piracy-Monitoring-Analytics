from __future__ import annotations

import re
import unicodedata
from difflib import SequenceMatcher


WHITESPACE_RE = re.compile("\\s+")
NON_ALNUM_RE = re.compile("[^a-zA-Z0-9\u0430-\u044f\u0410-\u042f\u0451\u0401]+")


def clean_spaces(value: str) -> str:
    return WHITESPACE_RE.sub(" ", value or "").strip()


def normalize_text(value: str) -> str:
    value = unicodedata.normalize("NFKC", value or "")
    value = value.casefold().replace("\u0451", "\u0435")
    value = NON_ALNUM_RE.sub(" ", value)
    return clean_spaces(value)


CATALOG_MARKERS = (
    "читать книги онлайн полностью бесплатно и без регистрации",
    "читать книги онлайн полностью",
    "скачать книгу бесплатно",
    "читать онлайн бесплатно",
    "читать онлайн",
    "скачать бесплатно",
    "скачать книгу",
    "все книги",
    "электронная библиотека",
    "книги по порядку",
)


def title_contains_book_name(official_title: str, candidate_title: str) -> bool:
    official_norm = normalize_text(official_title)
    candidate_norm = normalize_text(candidate_title)
    if not official_norm or not candidate_norm:
        return False
    return official_norm in candidate_norm


def looks_like_catalog_title(candidate_title: str) -> bool:
    candidate_norm = normalize_text(candidate_title)
    if not candidate_norm:
        return True
    return any(marker in candidate_norm for marker in CATALOG_MARKERS)


def similarity(left: str, right: str) -> float:
    left_norm = normalize_text(left)
    right_norm = normalize_text(right)
    if not left_norm or not right_norm:
        return 0.0
    if left_norm == right_norm:
        return 1.0
    return SequenceMatcher(None, left_norm, right_norm).ratio()


def looks_like_same_book(official_title: str, candidate_title: str, threshold: float = 0.73) -> bool:
    return similarity(official_title, candidate_title) >= threshold
