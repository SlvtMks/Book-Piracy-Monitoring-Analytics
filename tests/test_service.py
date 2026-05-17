from pirate_monitor.models import OfficialBook
from pirate_monitor.service import MonitorService


AUTHOR = "\u0410\u0440\u0438\u043d\u0430 \u0410\u0440\u0441\u043a\u0430\u044f"
TITLE_EXACT = "\u041d\u0443 \u043a\u0430\u043a\u043e\u0439 \u0436\u0435 \u0442\u044b \u043c\u0435\u0440\u0437\u0430\u0432\u0435\u0446!"
TITLE_MAIN = "\u0411\u043e\u0441\u0441 \u0438 \u043c\u0430\u0442\u044c-\u043e\u0434\u0438\u043d\u043e\u0447\u043a\u0430 \u0432 \u0440\u0430\u0437\u0432\u043e\u0434\u0435"
TITLE_OTHER = "(\u043d\u0435)\u0432\u0430\u0448\u0430 \u0434\u0435\u0432\u043e\u0447\u043a\u0430"
TITLE_SUFFIX = TITLE_MAIN + " (\u0421\u0418)"
TITLE_READ = TITLE_MAIN + " \u0447\u0438\u0442\u0430\u0442\u044c \u043e\u043d\u043b\u0430\u0439\u043d"
CATALOG = "\u0410\u0432\u0442\u043e\u0440\u044b, \u0444\u0430\u043c\u0438\u043b\u0438\u044f \u043a\u043e\u0442\u043e\u0440\u044b\u0445 \u043d\u0430\u0447\u0438\u043d\u0430\u0435\u0442\u0441\u044f \u043d\u0430 \u0431\u0443\u043a\u0432\u0443 \u0427"


def test_filter_books_by_exact_title_only() -> None:
    books = [
        OfficialBook(author_name=AUTHOR, title=TITLE_OTHER, url="https://example.com/1"),
        OfficialBook(author_name=AUTHOR, title=TITLE_MAIN, url="https://example.com/2"),
        OfficialBook(author_name=AUTHOR, title=TITLE_EXACT, url="https://example.com/3"),
    ]
    service = MonitorService()

    exact = service._filter_books(books, TITLE_EXACT)
    assert [book.title for book in exact] == [TITLE_EXACT]

    normalized_exact = service._filter_books(books, TITLE_EXACT.rstrip("!"))
    assert [book.title for book in normalized_exact] == [TITLE_EXACT]

    contains = service._filter_books(books, "\u043c\u0430\u0442\u044c-\u043e\u0434\u0438\u043d\u043e\u0447\u043a\u0430")
    assert contains == []


def test_relevant_record_allows_safe_suffixes() -> None:
    service = MonitorService()

    assert service._is_relevant_record(TITLE_MAIN, TITLE_SUFFIX)
    assert service._is_relevant_record(TITLE_MAIN, TITLE_READ)


def test_relevant_record_rejects_catalog_pages() -> None:
    service = MonitorService()

    assert not service._is_relevant_record(TITLE_MAIN, CATALOG)
