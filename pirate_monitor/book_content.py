from __future__ import annotations

from dataclasses import dataclass
from io import BytesIO
from pathlib import PurePosixPath
from zipfile import BadZipFile, ZipFile
from xml.etree import ElementTree as ET

from bs4 import BeautifulSoup

from .normalization import clean_spaces


SUPPORTED_BOOK_EXTENSIONS = {
    '.epub': 'epub',
    '.fb2': 'fb2',
    '.txt': 'txt',
    '.html': 'html',
    '.htm': 'html',
    '.xhtml': 'html',
    '.zip': 'zip',
}
EXCLUDED_EPUB_PATH_PARTS = (
    'cover',
    'title',
    'annotation',
    'about',
    'fb2info',
    'toc',
    'nav',
    'contents',
)
TEXT_MEDIA_TYPES = {
    'application/xhtml+xml',
    'text/html',
    'application/xml',
    'text/xml',
    'text/plain',
}
HTML_TEXT_BLOCK_TAGS = ('h1', 'h2', 'h3', 'h4', 'p', 'li', 'blockquote')


@dataclass(slots=True)
class BookFileParseResult:
    char_count: int | None
    file_format: str | None


def count_book_bytes(content: bytes, *, url: str = '', content_type: str = '') -> BookFileParseResult:
    file_format = detect_book_format(url=url, content_type=content_type, content=content)
    if not file_format:
        return BookFileParseResult(char_count=None, file_format=None)

    if file_format == 'epub':
        return BookFileParseResult(char_count=_count_epub_bytes(content), file_format=file_format)
    if file_format == 'fb2':
        return BookFileParseResult(char_count=_count_fb2_bytes(content), file_format=file_format)
    if file_format == 'txt':
        return BookFileParseResult(char_count=_count_txt_bytes(content), file_format=file_format)
    if file_format == 'html':
        return BookFileParseResult(char_count=_count_html_bytes(content), file_format=file_format)
    if file_format == 'zip':
        return BookFileParseResult(char_count=_count_zip_bytes(content), file_format=file_format)
    return BookFileParseResult(char_count=None, file_format=file_format)


def detect_book_format(*, url: str = '', content_type: str = '', content: bytes | None = None) -> str | None:
    suffix = PurePosixPath(url.split('?', 1)[0]).suffix.casefold()
    if suffix in SUPPORTED_BOOK_EXTENSIONS:
        return SUPPORTED_BOOK_EXTENSIONS[suffix]

    lowered_type = (content_type or '').casefold()
    if 'epub+zip' in lowered_type:
        return 'epub'
    if 'fictionbook' in lowered_type or 'fb2' in lowered_type or 'xml' in lowered_type:
        if content and b'<FictionBook' in content[:4096]:
            return 'fb2'
    if 'text/plain' in lowered_type:
        return 'txt'
    if 'text/html' in lowered_type or 'application/xhtml+xml' in lowered_type:
        return 'html'
    if 'zip' in lowered_type:
        return 'zip'

    if not content:
        return None
    head = content[:4096]
    if head.startswith(b'PK'):
        try:
            with ZipFile(BytesIO(content)) as archive:
                names = {name.casefold() for name in archive.namelist()}
                if 'mimetype' in names and any(name.endswith('.opf') for name in names):
                    return 'epub'
                if any(name.endswith('.fb2') for name in names):
                    return 'zip'
                if any(name.endswith(('.txt', '.html', '.htm', '.xhtml')) for name in names):
                    return 'zip'
        except BadZipFile:
            return None
    if b'<FictionBook' in head:
        return 'fb2'
    if b'<html' in head.lower() or b'<!doctype html' in head.lower():
        return 'html'
    return 'txt'


def _count_epub_bytes(content: bytes) -> int | None:
    try:
        with ZipFile(BytesIO(content)) as archive:
            container = ET.fromstring(archive.read('META-INF/container.xml'))
            rootfile = container.find('.//{*}rootfile')
            if rootfile is None:
                return None
            rootfile_path = rootfile.attrib.get('full-path')
            if not rootfile_path:
                return None
            opf = ET.fromstring(archive.read(rootfile_path))
            namespace = {'opf': opf.tag.split('}')[0].strip('{')}
            manifest = {
                item.attrib['id']: (
                    item.attrib.get('href', ''),
                    item.attrib.get('media-type', ''),
                )
                for item in opf.findall('.//opf:manifest/opf:item', namespace)
            }
            root_dir = PurePosixPath(rootfile_path).parent
            documents: list[str] = []
            for itemref in opf.findall('.//opf:spine/opf:itemref', namespace):
                idref = itemref.attrib.get('idref', '')
                href, media_type = manifest.get(idref, ('', ''))
                if media_type not in TEXT_MEDIA_TYPES:
                    continue
                lowered_href = href.casefold()
                if any(marker in lowered_href for marker in EXCLUDED_EPUB_PATH_PARTS):
                    continue
                full_path = str(root_dir / href) if href else ''
                if not full_path:
                    continue
                text = _extract_html_document_text(archive.read(full_path))
                if not text:
                    continue
                if len(text) < 100:
                    continue
                documents.append(text)
    except (BadZipFile, ET.ParseError, KeyError, ValueError):
        return None

    if not documents:
        return None
    return len(clean_spaces(' '.join(documents)))


def _count_fb2_bytes(content: bytes) -> int | None:
    decoded = _decode_text_bytes(content)
    if not decoded:
        return None
    try:
        root = ET.fromstring(decoded)
    except ET.ParseError:
        return None

    paragraphs: list[str] = []
    for body in root.findall('.//{*}body'):
        body_name = body.attrib.get('name', '').casefold()
        if body_name in {'notes', 'comments'}:
            continue
        for node in body.iter():
            if node.tag.rsplit('}', 1)[-1] not in {'p', 'subtitle', 'title'}:
                continue
            text = clean_spaces(''.join(node.itertext()))
            if text:
                paragraphs.append(text)
    if not paragraphs:
        return None
    return len(clean_spaces(' '.join(paragraphs)))


def _count_txt_bytes(content: bytes) -> int | None:
    decoded = _decode_text_bytes(content)
    if not decoded:
        return None
    text = clean_spaces(decoded)
    return len(text) if text else None


def _count_html_bytes(content: bytes) -> int | None:
    text = _extract_html_document_text(content)
    return len(text) if text else None


def _count_zip_bytes(content: bytes) -> int | None:
    try:
        with ZipFile(BytesIO(content)) as archive:
            candidates: list[tuple[int, str]] = []
            for name in archive.namelist():
                suffix = PurePosixPath(name).suffix.casefold()
                if suffix not in SUPPORTED_BOOK_EXTENSIONS or suffix == '.zip':
                    continue
                lower_name = name.casefold()
                if any(marker in lower_name for marker in EXCLUDED_EPUB_PATH_PARTS):
                    continue
                info = archive.getinfo(name)
                candidates.append((info.file_size, name))
            if not candidates:
                return None
            candidates.sort(reverse=True)
            for _, name in candidates:
                inner = archive.read(name)
                result = count_book_bytes(inner, url=name)
                if result.char_count:
                    return result.char_count
    except BadZipFile:
        return None
    return None


def _extract_html_document_text(content: bytes) -> str | None:
    decoded = _decode_text_bytes(content)
    if not decoded:
        return None
    soup = BeautifulSoup(decoded, 'html.parser')
    body = soup.body or soup
    for node in body.select('script,style,noscript,svg,iframe'):
        node.decompose()

    blocks = _collect_html_blocks(body)
    if not blocks:
        text = clean_spaces(body.get_text('', strip=False))
        return text if text else None
    return clean_spaces(' '.join(blocks))


def _collect_html_blocks(body) -> list[str]:
    blocks: list[str] = []
    for node in body.find_all(HTML_TEXT_BLOCK_TAGS):
        text = clean_spaces(node.get_text('', strip=False))
        if text:
            blocks.append(text)
    return blocks


def _decode_text_bytes(content: bytes) -> str | None:
    for encoding in ('utf-8-sig', 'utf-8', 'cp1251'):
        try:
            return content.decode(encoding)
        except UnicodeDecodeError:
            continue
    return content.decode('utf-8', errors='ignore') if content else None
