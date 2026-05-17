from datetime import datetime
from io import BytesIO
from zipfile import ZipFile

from bs4 import BeautifulSoup

from pirate_monitor.book_content import count_book_bytes
from pirate_monitor.models import SearchHit, TargetSite
from pirate_monitor.http import ResponseSnapshot
from pirate_monitor.targets import TargetPageParser
from pirate_monitor.parsing import count_text_characters


class DummyHttp:
    def __init__(self, snapshots):
        self.snapshots = snapshots

    def get(self, url, **kwargs):
        key = url
        if kwargs.get('params'):
            raise AssertionError('params are not expected in this test')
        return self.snapshots[key]


def make_epub_bytes(chapters: list[str]) -> bytes:
    buffer = BytesIO()
    with ZipFile(buffer, 'w') as archive:
        archive.writestr('mimetype', 'application/epub+zip')
        archive.writestr(
            'META-INF/container.xml',
            '<?xml version="1.0"?><container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">'
            '<rootfiles><rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/></rootfiles>'
            '</container>',
        )
        manifest_items = []
        spine_items = []
        for index, chapter in enumerate(chapters, start=1):
            href = f'text/chapter{index}.xhtml'
            manifest_items.append(
                f'<item id="c{index}" href="{href}" media-type="application/xhtml+xml"/>'
            )
            spine_items.append(f'<itemref idref="c{index}"/>')
            archive.writestr(
                f'OEBPS/{href}',
                f'<?xml version="1.0" encoding="utf-8"?>'
                f'<html xmlns="http://www.w3.org/1999/xhtml"><body><h2>{index}</h2><p>{chapter}</p></body></html>',
            )
        archive.writestr(
            'OEBPS/content.opf',
            '<?xml version="1.0" encoding="utf-8"?>'
            '<package xmlns="http://www.idpf.org/2007/opf" version="2.0">'
            '<manifest>' + ''.join(manifest_items) + '</manifest>'
            '<spine>' + ''.join(spine_items) + '</spine>'
            '</package>',
        )
    return buffer.getvalue()


def test_parser_prefers_downloaded_book_file_over_annotation_card() -> None:
    landing_url = 'https://litmir.club/bd/?b=959758'
    download_url = 'https://litmir.club/files/book.epub'
    html = '''
    <html>
      <head><title>Книга "Босс и мать-одиночка в разводе (СИ)" - Арина Арская</title></head>
      <body>
        <div class="lt26a">
          <p>Аннотация</p>
          <p>Короткий фрагмент текста, который не должен считаться полным объёмом книги.</p>
        </div>
        <a href="/files/book.epub">epub</a>
        <a href="/reader/959758">Читать онлайн</a>
      </body>
    </html>
    '''
    book_text = [
        'Первая глава ' + ('очень длинный текст ' * 120),
        'Вторая глава ' + ('ещё один длинный текст ' * 140),
    ]
    epub_bytes = make_epub_bytes(book_text)
    expected_count = count_book_bytes(epub_bytes, url=download_url, content_type='application/epub+zip').char_count

    parser = TargetPageParser(
        DummyHttp(
            {
                landing_url: ResponseSnapshot(
                    url=landing_url,
                    status_code=200,
                    text=html,
                    content=html.encode('utf-8'),
                    content_type='text/html; charset=utf-8',
                ),
                download_url: ResponseSnapshot(
                    url=download_url,
                    status_code=200,
                    text='',
                    content=epub_bytes,
                    content_type='application/epub+zip',
                ),
            }
        )
    )
    site = TargetSite(name='Литмир', base_url='https://litmir.club', complaint_format='', parser='litmir')
    hit = SearchHit(
        site_name=site.name,
        domain=site.domain,
        query='q',
        url=landing_url,
        title='Босс и мать-одиночка в разводе (СИ)',
        snippet='',
        provider='test',
        rank=1,
    )

    record = parser.parse(site, hit, 'Арина Арская', 'Босс и мать-одиночка в разводе', 'https://litnet.example/book')

    assert record.publication_confirmed is True
    assert record.publication_source == 'download'
    assert record.publication_format == 'epub'
    assert record.publication_url == download_url
    assert record.char_count == expected_count


def test_parser_marks_annotation_only_page_as_unconfirmed() -> None:
    landing_url = 'https://example.com/book-card'
    html = '''
    <html>
      <head><title>Босс и мать-одиночка в разводе</title></head>
      <body>
        <div class="book-details">
          <p>Аннотация</p>
          <p>Совсем короткий текст карточки книги без полноценного чтения и без файлов для скачивания.</p>
        </div>
      </body>
    </html>
    '''

    parser = TargetPageParser(
        DummyHttp(
            {
                landing_url: ResponseSnapshot(
                    url=landing_url,
                    status_code=200,
                    text=html,
                    content=html.encode('utf-8'),
                    content_type='text/html; charset=utf-8',
                ),
            }
        )
    )
    site = TargetSite(name='ТопЛиба', base_url='https://topliba.com', complaint_format='', parser='generic')
    hit = SearchHit(
        site_name=site.name,
        domain=site.domain,
        query='q',
        url=landing_url,
        title='Босс и мать-одиночка в разводе',
        snippet='',
        provider='test',
        rank=1,
    )

    record = parser.parse(site, hit, 'Арина Арская', 'Босс и мать-одиночка в разводе', 'https://litnet.example/book')

    assert record.publication_confirmed is False
    assert record.publication_source == 'card'
    assert record.char_count is None

def test_parser_resolves_litmir_qr_download_link_to_real_file() -> None:
    landing_url = 'https://litmir.club/bd/?b=959758'
    download_url = 'https://litmir.club/BookFileDownloadLink/?id=3022436'
    html = '''
    <html>
      <head><title>Book card</title></head>
      <body>
        <a href="/ShowQRCode/?Text=https%3A%2F%2Flitmir.club%2FBookFileDownloadLink%2F%3Fid%3D3022436">QR code</a>
      </body>
    </html>
    '''
    epub_bytes = make_epub_bytes(['Chapter one ' + ('text ' * 400)])
    expected_count = count_book_bytes(epub_bytes, url=download_url, content_type='application/epub+zip').char_count

    parser = TargetPageParser(
        DummyHttp(
            {
                landing_url: ResponseSnapshot(
                    url=landing_url,
                    status_code=200,
                    text=html,
                    content=html.encode('utf-8'),
                    content_type='text/html; charset=utf-8',
                ),
                download_url: ResponseSnapshot(
                    url=download_url,
                    status_code=200,
                    text='',
                    content=epub_bytes,
                    content_type='application/epub+zip',
                ),
            }
        )
    )
    site = TargetSite(name='Litmir', base_url='https://litmir.club', complaint_format='', parser='litmir')
    hit = SearchHit(
        site_name=site.name,
        domain=site.domain,
        query='q',
        url=landing_url,
        title='Boss and single mom',
        snippet='',
        provider='test',
        rank=1,
    )

    record = parser.parse(site, hit, 'Author', 'Boss and single mom', 'https://example.com/official')

    assert record.publication_confirmed is True
    assert record.publication_source == 'download'
    assert record.publication_format == 'epub'
    assert record.publication_url == download_url
    assert record.char_count == expected_count


def test_parser_does_not_treat_plain_html_page_as_download() -> None:
    landing_url = 'https://topliba.com/books/1020654'
    top_url = 'https://topliba.com/top.html'
    html = '''
    <html>
      <body>
        <div class="book-details">
          <p>Annotation only.</p>
          <p>This page has no real book text and no real downloadable file.</p>
        </div>
        <a href="/top.html">Скачать</a>
      </body>
    </html>
    '''
    top_html = '''
    <html>
      <body>
        <h1>Top books</h1>
        <p>Rating page, not a downloadable book.</p>
      </body>
    </html>
    '''

    parser = TargetPageParser(
        DummyHttp(
            {
                landing_url: ResponseSnapshot(
                    url=landing_url,
                    status_code=200,
                    text=html,
                    content=html.encode('utf-8'),
                    content_type='text/html; charset=utf-8',
                ),
                top_url: ResponseSnapshot(
                    url=top_url,
                    status_code=200,
                    text=top_html,
                    content=top_html.encode('utf-8'),
                    content_type='text/html; charset=utf-8',
                ),
            }
        )
    )
    site = TargetSite(name='Topliba', base_url='https://topliba.com', complaint_format='', parser='generic')
    hit = SearchHit(
        site_name=site.name,
        domain=site.domain,
        query='q',
        url=landing_url,
        title='Boss and single mom',
        snippet='',
        provider='test',
        rank=1,
    )

    record = parser.parse(site, hit, 'Author', 'Boss and single mom', 'https://example.com/official')

    assert record.publication_confirmed is False
    assert record.publication_source == 'card'
    assert record.publication_url == landing_url
    assert record.char_count is None


class BrowserFallbackParser(TargetPageParser):
    def __init__(self, snapshots, browser_payload):
        super().__init__(DummyHttp(snapshots))
        self.browser_payload = browser_payload

    def _download_book_via_browser(self, page_url, download_url):
        return self.browser_payload


def test_parser_uses_browser_download_fallback_when_http_returns_html() -> None:
    landing_url = 'https://bookzip.top/137607-boss-i-mat-odinochka-v-razvode.html'
    download_url = 'https://bookzip.top/index.php?do=download&id=288835'
    html = """
    <html>
      <body>
        <div id="dle-content">
          <a href="/index.php?do=download&id=288835">Download epub</a>
        </div>
      </body>
    </html>
    """
    fake_download_html = """
    <html>
      <body>
        <h1>Book card</h1>
        <p>Server did not return the actual file over plain HTTP.</p>
      </body>
    </html>
    """
    epub_bytes = make_epub_bytes([
        'First chapter ' + ('very long text ' * 120),
        'Second chapter ' + ('another long text ' * 140),
    ])
    expected_count = count_book_bytes(epub_bytes, url='book.epub', content_type='application/epub+zip').char_count

    parser = BrowserFallbackParser(
        {
            landing_url: ResponseSnapshot(
                url=landing_url,
                status_code=200,
                text=html,
                content=html.encode('utf-8'),
                content_type='text/html; charset=utf-8',
            ),
            download_url: ResponseSnapshot(
                url=landing_url,
                status_code=200,
                text=fake_download_html,
                content=fake_download_html.encode('utf-8'),
                content_type='text/html; charset=utf-8',
            ),
        },
        {'content': epub_bytes, 'file_name': 'book.epub'},
    )
    site = TargetSite(name='BookZip', base_url='https://bookzip.top', complaint_format='', parser='generic')
    hit = SearchHit(
        site_name=site.name,
        domain=site.domain,
        query='q',
        url=landing_url,
        title='Boss and single mom',
        snippet='',
        provider='test',
        rank=1,
    )

    record = parser.parse(site, hit, 'Author', 'Boss and single mom', 'https://litnet.example/book')

    assert record.publication_confirmed is True
    assert record.publication_source == 'download'
    assert record.publication_format == 'epub'
    assert record.publication_url == download_url
    assert record.char_count == expected_count


def test_extract_node_reading_text_deduplicates_nested_wrappers() -> None:
    parser = TargetPageParser(DummyHttp({}))
    soup = BeautifulSoup(
        """
        <div id="book_reader">
          <div class="outer">
            <div class="inner">
              <p>First long part of the text with enough letters and symbols to count as a real book paragraph for this parser.</p>
              <p>Second long part of the text, also big enough to qualify as a real paragraph of the work.</p>
            </div>
          </div>
        </div>
        """,
        'html.parser',
    )

    text = parser._extract_node_reading_text(soup.select_one('#book_reader'))

    assert text is not None
    assert text.count('First long part of the text') == 1
    assert text.count('Second long part of the text') == 1



def test_extract_read_links_accepts_only_readli_reader_pages() -> None:
    parser = TargetPageParser(DummyHttp({}))
    soup = BeautifulSoup(
        '''
        <html><body>
          <a href="/boss-i-mat-odinochka-v-razvode/">Карточка книги</a>
          <a href="/chitat-online/?b=1362567&pg=1">Читать онлайн</a>
          <a href="/avtor/arina-arskaya/">Страница автора</a>
        </body></html>
        ''',
        'html.parser',
    )

    links = parser._extract_read_links(
        TargetSite(name='Readli', base_url='https://readli.net', complaint_format='', parser='generic'),
        soup,
        'https://readli.net/boss-i-mat-odinochka-v-razvode/',
    )

    assert links == ['https://readli.net/chitat-online/?b=1362567&pg=1']




def test_extract_read_links_accepts_only_matching_litmir_reader() -> None:
    parser = TargetPageParser(DummyHttp({}))
    soup = BeautifulSoup(
        '''
        <html><body>
          <a href="/reader/959758">Read online</a>
          <a href="/reader/123456">Read online</a>
          <a href="/a/?id=77">Read online</a>
        </body></html>
        ''',
        'html.parser',
    )

    links = parser._extract_read_links(
        TargetSite(name='Литмир', base_url='https://litmir.club', complaint_format='', parser='litmir'),
        soup,
        'https://litmir.club/bd/?b=959758',
    )

    assert links == ['https://litmir.club/reader/959758']



def test_extract_read_links_accepts_only_matching_bookzip_reader() -> None:
    parser = TargetPageParser(DummyHttp({}))
    soup = BeautifulSoup(
        '''
        <html><body>
          <a href="/reader/137607/1/">Читать онлайн</a>
          <a href="/reader/999999/1/">Читать онлайн</a>
          <a href="/137607-boss-i-mat-odinochka-v-razvode.html">Карточка книги</a>
        </body></html>
        ''',
        'html.parser',
    )

    links = parser._extract_read_links(
        TargetSite(name='BookZip', base_url='https://bookzip.top', complaint_format='', parser='generic'),
        soup,
        'https://bookzip.top/137607-boss-i-mat-odinochka-v-razvode.html',
    )

    assert links == ['https://bookzip.top/reader/137607/1/']


def test_extract_read_links_skips_generic_rulit_reader_index() -> None:
    parser = TargetPageParser(DummyHttp({}))
    soup = BeautifulSoup(
        '''
        <html><body>
          <a href="/reader/all/1/name">Читать онлайн</a>
          <a href="/reader/1066937/1">Читать онлайн</a>
        </body></html>
        ''',
        'html.parser',
    )

    links = parser._extract_read_links(
        TargetSite(name='Рулит', base_url='https://www.rulit.me', complaint_format='', parser='rulit'),
        soup,
        'https://www.rulit.me/books/boss-i-mat-odinochka-v-razvode-download-1066937.html',
    )

    assert links == ['https://www.rulit.me/reader/1066937/1']


def test_inline_chain_follows_forward_marker_and_counts_pages() -> None:
    page1 = 'https://readli.net/chitat-online/?b=1362567&pg=1'
    page2 = 'https://readli.net/chitat-online/?b=1362567&pg=2'
    first_text = 'First page fragment ' + ('long readable prose ' * 40)
    second_text = 'Second page fragment ' + ('another readable prose block ' * 40)
    parser = TargetPageParser(
        DummyHttp(
            {
                page1: ResponseSnapshot(
                    url=page1,
                    status_code=200,
                    text=f'''<html><body>
                      <div class="page__left">
                        <p>{first_text}</p>
                      </div>
                      <a class="pagination__next" href="/chitat-online/?b=1362567&pg=2">NEXT</a>
                    </body></html>''',
                    content=b'',
                    content_type='text/html; charset=utf-8',
                ),
                page2: ResponseSnapshot(
                    url=page2,
                    status_code=200,
                    text=f'''<html><body>
                      <div class="page__left">
                        <p>{second_text}</p>
                      </div>
                    </body></html>''',
                    content=b'',
                    content_type='text/html; charset=utf-8',
                ),
            }
        )
    )

    inline = parser._extract_inline_chain(
        TargetSite(name='Readli', base_url='https://readli.net', complaint_format='', parser='generic'),
        page1,
        'Boss and single mom',
        'Arina Arskaya',
    )

    assert inline is not None
    assert inline['pages'] == 2
    assert inline['url'] == page2
    assert inline['char_count'] and inline['char_count'] > 1000




def test_inline_chain_does_not_double_count_cumulative_reader_pages() -> None:
    page1 = 'https://bookzip.top/reader/137607/1/'
    page2 = 'https://bookzip.top/reader/137607/2/'
    first_text = 'First page fragment ' + ('long readable prose ' * 40)
    second_delta = 'Second page only ' + ('another readable prose block ' * 20)
    second_text = first_text + ' ' + second_delta
    parser = TargetPageParser(
        DummyHttp(
            {
                page1: ResponseSnapshot(
                    url=page1,
                    status_code=200,
                    text=f'''<html><body>
                      <div id="book_reader">
                        <p>{first_text}</p>
                      </div>
                      <a class="pagination__next" href="/reader/137607/2/">Вперед</a>
                    </body></html>''',
                    content=b'',
                    content_type='text/html; charset=utf-8',
                ),
                page2: ResponseSnapshot(
                    url=page2,
                    status_code=200,
                    text=f'''<html><body>
                      <div id="book_reader">
                        <p>{second_text}</p>
                      </div>
                    </body></html>''',
                    content=b'',
                    content_type='text/html; charset=utf-8',
                ),
            }
        )
    )

    inline = parser._extract_inline_chain(
        TargetSite(name='BookZip', base_url='https://bookzip.top', complaint_format='', parser='generic'),
        page1,
        'Boss and single mom',
        'Arina Arskaya',
    )

    assert inline is not None
    assert inline['pages'] == 1
    assert inline['url'] == page2
    assert inline['char_count'] == count_text_characters(second_text)


def test_inline_chain_keeps_same_reader_identity_when_paging() -> None:
    page1 = 'https://readli.net/chitat-online/?b=1362567&pg=1'
    wrong_page = 'https://readli.net/chitat-online/?b=1370545&pg=24'
    page2 = 'https://readli.net/chitat-online/?b=1362567&pg=2'
    first_text = 'First page fragment ' + ('long readable prose ' * 40)
    second_text = 'Second page fragment ' + ('another readable prose block ' * 40)
    parser = TargetPageParser(
        DummyHttp(
            {
                page1: ResponseSnapshot(
                    url=page1,
                    status_code=200,
                    text=f'''<html><body>
                      <div class="page__left">
                        <p>{first_text}</p>
                      </div>
                      <a class="pagination__next" href="/chitat-online/?b=1370545&pg=24">Вперед</a>
                      <a class="pagination__next" href="/chitat-online/?b=1362567&pg=2">NEXT</a>
                    </body></html>''',
                    content=b'',
                    content_type='text/html; charset=utf-8',
                ),
                page2: ResponseSnapshot(
                    url=page2,
                    status_code=200,
                    text=f'''<html><body>
                      <div class="page__left">
                        <p>{second_text}</p>
                      </div>
                    </body></html>''',
                    content=b'',
                    content_type='text/html; charset=utf-8',
                ),
            }
        )
    )

    inline = parser._extract_inline_chain(
        TargetSite(name='Readli', base_url='https://readli.net', complaint_format='', parser='generic'),
        page1,
        'Boss and single mom',
        'Arina Arskaya',
    )

    assert inline is not None
    assert inline['pages'] == 2
    assert inline['url'] == page2
    assert inline['char_count'] and inline['char_count'] > 1000


def test_parser_marks_blocked_paginated_fragment_as_inline_partial() -> None:
    landing_url = 'https://readli.net/boss-i-mat-odinochka-v-razvode/'
    page1 = 'https://readli.net/chitat-online/?b=1362567&pg=1'
    page2 = 'https://readli.net/chitat-online/?b=1362567&pg=2'
    landing_html = '''<html><body>
      <div class="page__left"><p>Book card with short teaser.</p></div>
      <a href="/chitat-online/?b=1362567&pg=1">Read online</a>
    </body></html>'''
    first_text = 'First accessible fragment page ' + ('story text ' * 35)
    second_text = 'Second accessible fragment page ' + ('story text ' * 35)
    blocked_marker = 'книга заблокирована'
    page1_html = f'''<html><body>
      <div class="page__left">
        <p>{first_text}</p>
      </div>
      <a class="pagination__next" href="/chitat-online/?b=1362567&pg=2">NEXT</a>
    </body></html>'''
    page2_html = f'''<html><body>
      <div class="page__left">
        <p>{second_text}</p>
        <div>{blocked_marker}</div>
      </div>
    </body></html>'''

    parser = TargetPageParser(
        DummyHttp(
            {
                landing_url: ResponseSnapshot(
                    url=landing_url,
                    status_code=200,
                    text=landing_html,
                    content=landing_html.encode('utf-8'),
                    content_type='text/html; charset=utf-8',
                ),
                page1: ResponseSnapshot(
                    url=page1,
                    status_code=200,
                    text=page1_html,
                    content=page1_html.encode('utf-8'),
                    content_type='text/html; charset=utf-8',
                ),
                page2: ResponseSnapshot(
                    url=page2,
                    status_code=200,
                    text=page2_html,
                    content=page2_html.encode('utf-8'),
                    content_type='text/html; charset=utf-8',
                ),
            }
        )
    )
    site = TargetSite(name='Readli', base_url='https://readli.net', complaint_format='', parser='generic')
    hit = SearchHit(
        site_name=site.name,
        domain=site.domain,
        query='q',
        url=landing_url,
        title='Boss and single mom',
        snippet='',
        provider='test',
        rank=1,
    )

    record = parser.parse(site, hit, 'Arina Arskaya', 'Boss and single mom', 'https://litnet.example/book')

    assert record.publication_confirmed is True
    assert record.publication_source == 'inline'
    assert record.publication_url == page2
    assert record.raw_status == 'blocked-fragment-marker'
    assert record.char_count and record.char_count > 700
