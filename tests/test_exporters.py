import csv
import shutil
from datetime import datetime
from pathlib import Path

from openpyxl import load_workbook

from pirate_monitor.models import PirateRecord
from pirate_monitor.exporters import export_csv, export_xlsx


AUTHOR = "\u0410\u0440\u0438\u043d\u0430 \u0410\u0440\u0441\u043a\u0430\u044f"
SITE_NAME = "\u041b\u0438\u0442\u043c\u0438\u0440"
TITLE = "(\u043d\u0435)\u0432\u0430\u0448\u0430 \u0434\u0435\u0432\u043e\u0447\u043a\u0430"
PAGE_AUTHOR = "\u0410\u0440\u0441\u043a\u0430\u044f \u0410\u0440\u0438\u043d\u0430"
RESULTS = "\u0420\u0435\u0437\u0443\u043b\u044c\u0442\u0430\u0442\u044b"
LITNET = "\u041b\u0438\u0442\u043d\u0435\u0442"
SIGNS = "\u0437\u043d\u0430\u043a\u043e\u0432"
FULL = "\u043e\u043f\u0443\u0431\u043b\u0438\u043a\u043e\u0432\u0430\u043d \u043f\u043e\u043b\u043d\u043e\u0441\u0442\u044c\u044e"
CONFIRMED_DOWNLOAD = "\u043f\u043e\u0434\u0442\u0432\u0435\u0440\u0436\u0434\u0435\u043d\u043e: \u0441\u043a\u0430\u0447\u0438\u0432\u0430\u0435\u043c\u044b\u0439 \u0444\u0430\u0439\u043b \u043a\u043d\u0438\u0433\u0438 (epub)"
HEADERS = [
    "\u0410\u0432\u0442\u043e\u0440",
    "\u041f\u0440\u043e\u0438\u0437\u0432\u0435\u0434\u0435\u043d\u0438\u0435",
    "\u041e\u0444\u0438\u0446\u0438\u0430\u043b\u044c\u043d\u044b\u0439 \u0438\u0441\u0442\u043e\u0447\u043d\u0438\u043a",
    "\u0421\u0441\u044b\u043b\u043a\u0430 \u043d\u0430 \u043e\u0444\u0438\u0446\u0438\u0430\u043b\u044c\u043d\u043e\u0435 \u043f\u0440\u043e\u0438\u0437\u0432\u0435\u0434\u0435\u043d\u0438\u0435",
    "\u041e\u0431\u044a\u0451\u043c \u0442\u0435\u043a\u0441\u0442\u0430 \u043d\u0430 \u043e\u0444\u0438\u0446\u0438\u0430\u043b\u044c\u043d\u043e\u043c \u0441\u0430\u0439\u0442\u0435",
    "\u041e\u0431\u044a\u0451\u043c \u0442\u0435\u043a\u0441\u0442\u0430 \u043d\u0430 \u0441\u0430\u0439\u0442\u0435",
    "\u041f\u043e\u0434\u0442\u0432\u0435\u0440\u0436\u0434\u0435\u043d\u0438\u0435 \u043f\u0443\u0431\u043b\u0438\u043a\u0430\u0446\u0438\u0438",
    "\u0421\u0441\u044b\u043b\u043a\u0430 \u043d\u0430 \u043f\u043e\u0434\u0442\u0432\u0435\u0440\u0436\u0434\u0435\u043d\u043d\u044b\u0439 \u0442\u0435\u043a\u0441\u0442/\u0444\u0430\u0439\u043b",
    "\u041f\u0438\u0440\u0430\u0442\u0441\u043a\u0438\u0439 \u0441\u0430\u0439\u0442",
    "\u041d\u0430\u0437\u0432\u0430\u043d\u0438\u0435 \u043d\u0430 \u0441\u0430\u0439\u0442\u0435",
    "\u0421\u0441\u044b\u043b\u043a\u0430 \u043d\u0430 \u043d\u0430\u0439\u0434\u0435\u043d\u043d\u0443\u044e \u043a\u043e\u043f\u0438\u044e",
    "\u041f\u043e\u0441\u043b\u0435\u0434\u043d\u0435\u0435 \u043e\u0431\u043d\u043e\u0432\u043b\u0435\u043d\u0438\u0435 \u043d\u0430 \u0441\u0430\u0439\u0442\u0435",
    "\u0421\u0442\u0430\u0442\u0443\u0441",
]


def test_export_uses_client_russian_headers_and_values() -> None:
    record = PirateRecord(
        site_name=SITE_NAME,
        domain="litmir.club",
        source_author=AUTHOR,
        source_title=TITLE,
        official_url="https://litnet.com/book/1",
        book_url="https://litmir.club/bd/?b=1",
        discovered_at=datetime.now(),
        page_title=TITLE,
        official_source="litnet",
        official_char_count=214000,
        page_author=PAGE_AUTHOR,
        char_count=173000,
        publication_confirmed=True,
        publication_source="download",
        publication_format="epub",
        publication_url="https://litmir.club/files/book.epub",
        last_update_raw="13.03.2024",
        assigned_status="full",
    )

    tmp_path = Path("tests_tmp/exporters")
    if tmp_path.exists():
        shutil.rmtree(tmp_path)
    tmp_path.mkdir(parents=True, exist_ok=True)

    try:
        csv_path = Path(export_csv([record], tmp_path / "result.csv"))
        xlsx_path = Path(export_xlsx([record], tmp_path / "result.xlsx"))

        with csv_path.open(encoding="utf-8-sig", newline="") as fh:
            rows = list(csv.reader(fh))

        assert rows[0] == HEADERS
        assert rows[1][0] == AUTHOR
        assert rows[1][2] == LITNET
        assert rows[1][4] == f"214000 {SIGNS}"
        assert rows[1][5] == f"173000 {SIGNS}"
        assert rows[1][6] == CONFIRMED_DOWNLOAD
        assert rows[1][7] == "https://litmir.club/files/book.epub"
        assert rows[1][12] == FULL

        workbook = load_workbook(xlsx_path, data_only=True)
        sheet = workbook.active
        assert sheet.title == RESULTS
        assert [cell.value for cell in sheet[1]] == rows[0]
        assert sheet["A2"].value == AUTHOR
        assert sheet["E2"].value == f"214000 {SIGNS}"
        assert sheet["F2"].value == f"173000 {SIGNS}"
        assert sheet["G2"].value == CONFIRMED_DOWNLOAD
        assert sheet["H2"].value == "https://litmir.club/files/book.epub"
        assert sheet["M2"].value == FULL
    finally:
        if tmp_path.exists():
            shutil.rmtree(tmp_path)
