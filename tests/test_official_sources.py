from pirate_monitor.models import OfficialBook
from pirate_monitor.official_sources import SOURCE_FACTORIES, detect_official_source, official_source_choices, resolve_book_reference, source_title
from pirate_monitor.sources.author_today import AuthorTodaySource
from pirate_monitor.sources.litgorod import LitgorodSource
from pirate_monitor.sources.litmarket import LitmarketSource
from pirate_monitor.sources.litres import LitresSource


class DummyHttp:
    pass


def test_detect_official_source_by_url() -> None:
    assert detect_official_source("https://litnet.com/ru/arina-arskaya-u9986314") == "litnet"
    assert detect_official_source("https://author.today/u/nssokol/works") == "author_today"
    assert detect_official_source("https://www.litres.ru/book/roald-dal/volshebnoe-lekarstvo-dzhordzha-50686139/") == "litres"


def test_official_source_choices_contains_supported_sources() -> None:
    choices = official_source_choices()
    assert "auto" in choices
    assert "litnet" in choices
    assert "author_today" in choices
    assert source_title("author_today") == "Author.Today"


def test_author_today_normalizes_author_url_to_works_page() -> None:
    source = AuthorTodaySource(DummyHttp())
    assert source._normalize_author_url("https://author.today/u/nssokol") == "https://author.today/u/nssokol/works"
    assert source._normalize_author_url("https://author.today/u/nssokol/posts") == "https://author.today/u/nssokol/works"


def test_litgorod_normalizes_author_url_to_books_page() -> None:
    source = LitgorodSource(DummyHttp())
    books_url, profile_url = source._normalize_author_url("https://litgorod.ru/profile/12345")
    assert books_url == "https://litgorod.ru/profile/12345/books"
    assert profile_url == "https://litgorod.ru/profile/12345/books"


def test_litres_normalizes_author_url_with_trailing_slash() -> None:
    source = LitresSource(DummyHttp())
    assert source._normalize_author_url("https://www.litres.ru/author/test-author") == "https://www.litres.ru/author/test-author/"


def test_litmarket_normalizes_author_url_and_extracts_id() -> None:
    source = LitmarketSource(DummyHttp())
    normalized_url, author_id = source._normalize_author_url("https://litmarket.ru/arina-arskaya-p12345/books")
    assert normalized_url == "https://litmarket.ru/arina-arskaya-p12345"
    assert author_id == 12345


def test_resolve_book_reference_accepts_direct_book_url(monkeypatch) -> None:
    class DummySource:
        key = "litres"

        def __init__(self, _http) -> None:
            pass

        def fetch_book_reference(self, book_url: str, *, expected_author: str | None = None):
            return (
                OfficialBook(
                    author_name=expected_author or "Роальд Даль",
                    title="Волшебное лекарство Джорджа",
                    url=book_url,
                    source="litres",
                    is_complete=True,
                ),
                "https://www.litres.ru/author/roald-dal/",
            )

    monkeypatch.setitem(SOURCE_FACTORIES, "litres", DummySource)

    source, author_url, book = resolve_book_reference(
        DummyHttp(),
        author_name="Роальд Даль",
        book_title="Волшебное лекарство Джорджа",
        preferred_source="auto",
        book_url="https://www.litres.ru/book/roald-dal/volshebnoe-lekarstvo-dzhordzha-50686139/",
    )

    assert source.key == "litres"
    assert author_url == "https://www.litres.ru/author/roald-dal/"
    assert book.title == "Волшебное лекарство Джорджа"
