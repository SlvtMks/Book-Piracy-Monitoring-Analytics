from datetime import datetime

from pirate_monitor.models import OfficialBook, PirateRecord
from pirate_monitor.status import apply_reposted_status, classify_record


def test_full_status_by_char_count_ratio() -> None:
    book = OfficialBook(author_name="Автор", title="Книга", url="https://example.com", char_count=10000)
    record = PirateRecord(
        site_name="site",
        domain="site.test",
        source_author="Автор",
        source_title="Книга",
        official_url=book.url,
        book_url="https://site.test/book",
        discovered_at=datetime.now(),
        page_title="Книга",
        char_count=9500,
    )
    assert classify_record(book, record) == "full"


def test_removed_status() -> None:
    book = OfficialBook(author_name="Автор", title="Книга", url="https://example.com", char_count=10000)
    record = PirateRecord(
        site_name="site",
        domain="site.test",
        source_author="Автор",
        source_title="Книга",
        official_url=book.url,
        book_url="https://site.test/book",
        discovered_at=datetime.now(),
        page_title="Книга",
        available=False,
    )
    assert classify_record(book, record) == "removed"


def test_partial_status_by_fragment_marker_without_official_char_count() -> None:
    book = OfficialBook(author_name="Автор", title="Книга", url="https://example.com")
    record = PirateRecord(
        site_name="site",
        domain="site.test",
        source_author="Автор",
        source_title="Книга",
        official_url=book.url,
        book_url="https://site.test/book",
        discovered_at=datetime.now(),
        page_title="Книга",
        raw_status="fragment-marker",
        char_count=1200,
    )
    assert classify_record(book, record) == "partial"


def test_reposted_status_requires_distinct_urls() -> None:
    first = PirateRecord(
        site_name="site",
        domain="site.test",
        source_author="Автор",
        source_title="Книга",
        official_url="https://example.com/book",
        book_url="https://site.test/book-1",
        discovered_at=datetime.now(),
        page_title="Книга",
        assigned_status="full",
    )
    second = PirateRecord(
        site_name="site",
        domain="site.test",
        source_author="Автор",
        source_title="Книга",
        official_url="https://example.com/book",
        book_url="https://site.test/book-2",
        discovered_at=datetime.now(),
        page_title="Книга",
        assigned_status="partial",
    )
    same_url_first = PirateRecord(
        site_name="site",
        domain="site.test",
        source_author="Автор",
        source_title="Другая книга",
        official_url="https://example.com/book-2",
        book_url="https://site.test/book-3",
        discovered_at=datetime.now(),
        page_title="Другая книга",
        assigned_status="full",
    )
    same_url_second = PirateRecord(
        site_name="site",
        domain="site.test",
        source_author="Автор",
        source_title="Другая книга",
        official_url="https://example.com/book-2",
        book_url="https://site.test/book-3",
        discovered_at=datetime.now(),
        page_title="Другая книга",
        assigned_status="full",
    )

    records = [first, second, same_url_first, same_url_second]
    apply_reposted_status(records)

    assert first.assigned_status == "reposted"
    assert second.assigned_status == "reposted"
    assert same_url_first.assigned_status == "full"
    assert same_url_second.assigned_status == "full"
