from __future__ import annotations

import re
from typing import Optional

from .normalization import clean_spaces


PAGE_RE = re.compile(r"(\d[\d\s\xa0]{0,8})\s*(?:стр\.?|страниц(?:а|ы)?)", re.IGNORECASE)
CHAR_RE = re.compile(r"(\d[\d\s\xa0]{0,12})\s*(?:знак(?:ов|а)?|символ(?:ов|а)?|зн\.)", re.IGNORECASE)
FILE_SIZE_RE = re.compile(r"(\d[\d\s\xa0]{0,8})\s*(?:кб|kb|kb\.)", re.IGNORECASE)
AUTHOR_RE = re.compile(r"(?:автор|author)\s*[:\-]\s*([^\n|]+)", re.IGNORECASE)
DATE_RE = re.compile(
    r"(\d{1,2}[./]\d{1,2}[./]\d{2,4}(?:\s*,?\s*\d{1,2}:\d{2})?|\d{4}[.-]\d{2}[.-]\d{2}(?:\s+\d{2}:\d{2}(?::\d{2})?)?|\d{1,2}\s+(?:январ(?:я|ь)|феврал(?:я|ь)|март(?:а)?|апрел(?:я|ь)|ма(?:я|й)|июн(?:я|ь)|июл(?:я|ь)|август(?:а)?|сентябр(?:я|ь)|октябр(?:я|ь)|ноябр(?:я|ь)|декабр(?:я|ь))\s+\d{4}(?:\s*,?\s*\d{1,2}:\d{2})?)",
    re.IGNORECASE,
)
MONTH_CASES = {
    "январь": "января",
    "февраль": "февраля",
    "март": "марта",
    "апрель": "апреля",
    "май": "мая",
    "июнь": "июня",
    "июль": "июля",
    "август": "августа",
    "сентябрь": "сентября",
    "октябрь": "октября",
    "ноябрь": "ноября",
    "декабрь": "декабря",
}
BOT_BLOCK_MARKERS = [
    "подтвердите, пожалуйста, что вы не робот",
    "если капча не появилась",
    "captcha",
    "cloudflare",
    "access denied",
    "prove you are human",
]


def parse_number(raw: str | None) -> Optional[int]:
    if not raw:
        return None
    digits = re.sub(r"\D+", "", raw)
    if not digits:
        return None
    return int(digits)


def extract_page_count(text: str) -> Optional[int]:
    match = PAGE_RE.search(text or "")
    return parse_number(match.group(1)) if match else None


def extract_char_count(text: str) -> Optional[int]:
    match = CHAR_RE.search(text or "")
    return parse_number(match.group(1)) if match else None


def count_text_characters(text: str) -> Optional[int]:
    cleaned = clean_spaces(text or "")
    return len(cleaned) if cleaned else None


def extract_file_size_kb(text: str) -> Optional[int]:
    match = FILE_SIZE_RE.search(text or "")
    return parse_number(match.group(1)) if match else None


def extract_author(text: str) -> Optional[str]:
    match = AUTHOR_RE.search(text or "")
    return clean_spaces(match.group(1)) if match else None


def extract_date(text: str) -> Optional[str]:
    match = DATE_RE.search(text or "")
    if not match:
        return None
    value = clean_spaces(match.group(1))
    for nominative, genitive in MONTH_CASES.items():
        value = re.sub(rf"\b{re.escape(nominative)}\b", genitive, value, flags=re.IGNORECASE)
    return value


def infer_bot_blocked(text: str) -> bool:
    lower = (text or "").casefold()
    return any(marker in lower for marker in BOT_BLOCK_MARKERS)


def infer_availability(text: str) -> bool:
    lower = (text or "").casefold()
    markers = [
        "страница не найдена",
        "404",
        "книга удалена",
        "удалено по требованию",
        "произведение удалено",
        "материал недоступен",
    ]
    return not any(marker in lower for marker in markers)


def infer_raw_status(text: str) -> Optional[str]:
    lower = (text or "").casefold()
    if "ознакомительный фрагмент" in lower or "фрагмент" in lower:
        return "fragment-marker"
    if "книга заблокирована" in lower or re.search(r"\bблок\s+\d+\s*стр", lower):
        return "blocked-fragment-marker"
    if (
        "полный текст" in lower
        or "читать полностью" in lower
        or "весь текст" in lower
        or "полностью (целиком)" in lower
        or "читать онлайн бесплатно и без регистрации полностью" in lower
        or "скачать fb2" in lower
        or "скачать epub" in lower
        or "скачать pdf" in lower
        or "скачать txt" in lower
        or "скачать книгу" in lower
        or "в форматах fb2" in lower
    ):
        return "full-text-marker"
    if "завершен" in lower or "завершено" in lower or "закончена" in lower:
        return "completed-marker"
    if "в процессе" in lower:
        return "ongoing-marker"
    if (
        "книга удалена" in lower
        or "удалено по требованию" in lower
        or "произведение удалено" in lower
        or "материал недоступен" in lower
    ):
        return "removed-marker"
    return None

