from __future__ import annotations

import threading
import tkinter as tk
from tkinter import messagebox, ttk

from pirate_monitor.official_sources import official_source_choices
from pirate_monitor.service import MonitorOptions, MonitorService


class MonitorApp(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("Мониторинг пиратских копий книг")
        self.geometry("1080x760")
        self.service = MonitorService()
        self._build()

    def _build(self) -> None:
        frame = ttk.Frame(self, padding=12)
        frame.pack(fill="both", expand=True)

        self.official_source = tk.StringVar(value="litnet")
        self.author_url = tk.StringVar(value="https://litnet.com/ru/arina-arskaya-u9986314")
        self.author_name = tk.StringVar(value="")
        self.book_title = tk.StringVar(value="")
        self.config_path = tk.StringVar(value="config/target_sites.json")
        self.max_books = tk.StringVar(value="")
        self.max_sites = tk.StringVar(value="")
        self.interval_minutes = tk.StringVar(value="0")
        self.output_dir = tk.StringVar(value="output/exports")
        self.connect_timeout = tk.StringVar(value="10")
        self.read_timeout = tk.StringVar(value="60")
        self.max_retries = tk.StringVar(value="3")
        self.site_workers = tk.StringVar(value="5")

        fields = [
            ("Официальный источник", self.official_source, "combobox"),
            ("URL автора официального источника", self.author_url, "entry"),
            ("Имя автора (если URL не указан)", self.author_name, "entry"),
            ("Название книги (необязательно)", self.book_title, "entry"),
            ("Файл со списком сайтов", self.config_path, "entry"),
            ("Макс. книг (пусто = все)", self.max_books, "entry"),
            ("Макс. сайтов (пусто = все)", self.max_sites, "entry"),
            ("Интервал, минут (0 = один запуск)", self.interval_minutes, "entry"),
            ("Папка выгрузки", self.output_dir, "entry"),
            ("Connect timeout, сек", self.connect_timeout, "entry"),
            ("Read timeout, сек", self.read_timeout, "entry"),
            ("Повторы при сбое", self.max_retries, "entry"),
            ("Параллельных сайтов", self.site_workers, "entry"),
        ]

        for row, (label, variable, field_type) in enumerate(fields):
            ttk.Label(frame, text=label).grid(row=row, column=0, sticky="w", pady=4)
            if field_type == "combobox":
                widget = ttk.Combobox(
                    frame,
                    textvariable=variable,
                    values=official_source_choices(),
                    state="readonly",
                    width=88,
                )
            else:
                widget = ttk.Entry(frame, textvariable=variable, width=90)
            widget.grid(row=row, column=1, sticky="ew", pady=4)

        frame.columnconfigure(1, weight=1)

        buttons = ttk.Frame(frame)
        buttons.grid(row=len(fields), column=0, columnspan=2, sticky="w", pady=(10, 6))
        ttk.Button(buttons, text="Запустить один раз", command=self.run_once).pack(side="left", padx=(0, 8))
        ttk.Button(buttons, text="Запустить по расписанию", command=self.run_forever).pack(side="left", padx=(0, 8))
        ttk.Button(buttons, text="Показать прогресс", command=self.show_progress).pack(side="left", padx=(0, 8))
        ttk.Button(buttons, text="Выгрузить историю", command=self.export_history).pack(side="left")

        self.log = tk.Text(frame, wrap="word", height=26)
        self.log.grid(row=len(fields) + 1, column=0, columnspan=2, sticky="nsew", pady=(10, 0))
        frame.rowconfigure(len(fields) + 1, weight=1)

    def run_once(self) -> None:
        self._start_worker(loop=False)

    def run_forever(self) -> None:
        self._start_worker(loop=True)

    def show_progress(self) -> None:
        active_run = self.service.storage.get_active_run()
        if not active_run:
            self._append_log("Активных запусков нет.")
            return
        total_books = int(active_run["total_books"] or 0)
        processed_books = int(active_run["processed_books"] or 0)
        current_book = active_run.get("current_book_title") or "-"
        self._append_log(
            f"Активный запуск #{active_run['run_id']}: обработано {processed_books} из {total_books}. Текущая книга: {current_book}"
        )

    def export_history(self) -> None:
        try:
            summary = self.service.storage.export_history_report(
                output_dir=self.output_dir.get().strip() or "output/exports",
                official_source=(self.official_source.get().strip() or None) if self.official_source.get().strip() != "auto" else None,
                author_name=self.author_name.get().strip() or None,
                book_title=self.book_title.get().strip() or None,
            )
        except Exception as exc:  # noqa: BLE001
            self._append_log(f"Ошибка выгрузки истории: {exc}")
            return
        self._append_log(f"История CSV: {summary['csv_path']}")
        self._append_log(f"История XLSX: {summary['xlsx_path']}")
        self._append_log(f"Строк в истории: {summary['records_count']}")

    def _start_worker(self, loop: bool) -> None:
        try:
            options = self._collect_options()
        except ValueError as exc:
            messagebox.showerror("Ошибка параметров", str(exc))
            return

        def worker() -> None:
            try:
                if loop and options.interval_minutes and options.interval_minutes > 0:
                    self.service.run_forever(options, logger=self._append_log)
                else:
                    summary = self.service.run_once(options, logger=self._append_log)
                    self._append_log(f"CSV: {summary.csv_path}")
                    self._append_log(f"XLSX: {summary.xlsx_path}")
            except Exception as exc:  # noqa: BLE001
                self._append_log(f"Ошибка: {exc}")

        threading.Thread(target=worker, daemon=True).start()

    def _collect_options(self) -> MonitorOptions:
        author_url = self.author_url.get().strip()
        author_name = self.author_name.get().strip()
        if not author_url and not author_name:
            raise ValueError("Нужно указать URL автора или имя автора.")
        if author_name and not author_url and self.official_source.get().strip() == "auto":
            raise ValueError("Для поиска по имени автора нужно явно выбрать официальный источник.")

        max_books = self._parse_optional_int(self.max_books.get())
        max_sites = self._parse_optional_int(self.max_sites.get())
        interval = self._parse_optional_int(self.interval_minutes.get())
        connect_timeout = self._parse_required_int(self.connect_timeout.get(), default=10)
        read_timeout = self._parse_required_int(self.read_timeout.get(), default=60)
        max_retries = self._parse_required_int(self.max_retries.get(), default=3)
        site_workers = self._parse_required_int(self.site_workers.get(), default=5)

        return MonitorOptions(
            author_url=author_url or None,
            author_name=author_name or None,
            official_source=self.official_source.get().strip() or "auto",
            config_path=self.config_path.get().strip() or "config/target_sites.json",
            output_dir=self.output_dir.get().strip() or "output/exports",
            book_title=self.book_title.get().strip() or None,
            max_books=max_books,
            max_sites=max_sites,
            interval_minutes=interval,
            connect_timeout=connect_timeout,
            read_timeout=read_timeout,
            max_retries=max_retries,
            site_workers=site_workers,
        )

    def _parse_optional_int(self, raw_value: str) -> int | None:
        value = raw_value.strip()
        if not value or value == "0":
            return None
        return int(value)

    def _parse_required_int(self, raw_value: str, *, default: int) -> int:
        value = raw_value.strip()
        return int(value or str(default))

    def _append_log(self, message: str) -> None:
        self.after(0, lambda: self._write(message))

    def _write(self, message: str) -> None:
        self.log.insert("end", message + "\n")
        self.log.see("end")



def launch_gui() -> int:
    app = MonitorApp()
    app.mainloop()
    return 0
