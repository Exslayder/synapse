"""Settings window: DoH, browser, sites and updates."""

from __future__ import annotations

import copy
import os
import shutil
import subprocess
import sys
import threading
import time
import tkinter as tk
import webbrowser
from tkinter import filedialog, messagebox, ttk
from collections.abc import Callable

from .. import __version__
from .. import updater
from ..browser import browser_candidates, resolve_browser
from ..config import (
    AppConfig,
    ConfigError,
    DEFAULT_BOOTSTRAP,
    DEFAULT_DOH_URL,
    DohSettings,
    Site,
    normalize_site_url,
    slugify,
)
from ..doh import DohResolver
from .theme import (
    BG,
    BORDER,
    DIM,
    ERROR,
    FONT,
    MONO,
    MUTED,
    OK,
    PANEL,
    PANEL_HOVER,
    TEXT,
    dark_titlebar,
)
from .widgets import (
    Card,
    FlatButton,
    attach_entry_menu,
    make_checkbutton,
    make_entry,
    make_label,
)

BROWSER_CHOICES = [
    ("Авто — Chrome, затем Firefox", "auto"),
    ("Google Chrome", "chrome"),
    ("Firefox", "firefox"),
    ("Другой файл…", "custom"),
]


def open_path(path: str) -> None:
    try:
        if sys.platform == "win32":
            os.startfile(path)  # type: ignore[attr-defined]
        elif sys.platform == "darwin":
            subprocess.Popen(["open", path])
        else:
            subprocess.Popen(["xdg-open", path])
    except OSError:
        pass


def make_spinbox(
    parent: tk.Misc, variable: tk.Variable, low: int, high: int, step: int, width: int
) -> ttk.Spinbox:
    spinbox = ttk.Spinbox(
        parent,
        from_=low,
        to=high,
        increment=step,
        textvariable=variable,
        width=width,
        style="TSpinbox",
        font=(FONT, 10),
    )
    attach_entry_menu(spinbox)
    return spinbox


class SettingsDialog(tk.Toplevel):
    def __init__(
        self,
        parent: tk.Misc,
        services,
        on_save: Callable[[AppConfig], list[str]],
    ):
        super().__init__(parent)
        self.parent = parent
        self.services = services
        self.on_save = on_save
        self.config = copy.deepcopy(services.config)
        self.sites: list[Site] = [copy.copy(site) for site in self.config.sites]
        self._test_running = False
        self._update_checking = False
        self._release: updater.Release | None = None
        self._page = 0

        self.title("Настройки — Synapse")
        self.configure(bg=BG)
        self.geometry("860x760")
        self.minsize(780, 680)
        self.transient(parent)
        self.grab_set()
        self.protocol("WM_DELETE_WINDOW", self.destroy)
        self.bind("<Escape>", lambda _e: self.destroy())
        try:
            self.attributes("-topmost", bool(int(parent.attributes("-topmost"))))
        except (tk.TclError, TypeError, ValueError):
            pass

        self.url_var = tk.StringVar()
        self.boot_var = tk.StringVar()
        self.fallback_var = tk.BooleanVar(value=True)
        self.timeout_var = tk.StringVar()
        self.browser_var = tk.StringVar()
        self.path_var = tk.StringVar()
        self.width_var = tk.StringVar()
        self.height_var = tk.StringVar()
        self.port_var = tk.StringVar()
        self.close_browsers_var = tk.BooleanVar(value=True)
        self.auto_update_var = tk.BooleanVar(value=self.config.auto_check_updates)

        self._build()
        self._load()
        self._center()
        self.after(60, self._focus_first)

    def _center(self) -> None:
        self.update_idletasks()
        width = self.winfo_width()
        height = self.winfo_height()
        parent = self.parent
        x = parent.winfo_rootx() + max(0, (parent.winfo_width() - width) // 2)
        y = parent.winfo_rooty() + max(0, (parent.winfo_height() - height) // 2)
        self.geometry(f"+{x}+{y}")

    def _focus_first(self) -> None:
        try:
            self.lift()
            self.focus_force()
        except tk.TclError:
            pass
        dark_titlebar(self)

    # ------------------------------------------------------------------ build

    def _build(self) -> None:
        outer = tk.Frame(self, bg=BG)
        outer.pack(fill="both", expand=True, padx=24, pady=20)

        make_label(outer, "Настройки", font=(FONT, 18, "bold"), fg=TEXT).pack(anchor="w")
        make_label(
            outer,
            "DNS применяется сразу. Порт прокси и браузер — при следующем открытии окна.",
            fg=MUTED,
            font=(FONT, 9),
        ).pack(anchor="w", pady=(2, 14))

        segments = tk.Frame(outer, bg=BG)
        segments.pack(fill="x", pady=(0, 12))
        self._segments: list[FlatButton] = []
        for index, title in enumerate(("DNS", "Браузер", "Сайты", "Обновления")):
            button = FlatButton(
                segments,
                title,
                lambda i=index: self._select(i),
                small=True,
                ghost=True,
            )
            button.pack(side="left", padx=(0, 8))
            self._segments.append(button)

        self.pages = tk.Frame(outer, bg=BG)
        self.pages.pack(fill="both", expand=True)
        self.pages.columnconfigure(0, weight=1)
        self.pages.rowconfigure(0, weight=1)

        self.page_dns = tk.Frame(self.pages, bg=BG)
        self.page_browser = tk.Frame(self.pages, bg=BG)
        self.page_sites = tk.Frame(self.pages, bg=BG)
        self.page_updates = tk.Frame(self.pages, bg=BG)
        for page in (self.page_dns, self.page_browser, self.page_sites, self.page_updates):
            page.grid(row=0, column=0, sticky="nsew")

        self._build_dns(self.page_dns)
        self._build_browser(self.page_browser)
        self._build_sites(self.page_sites)
        self._build_updates(self.page_updates)

        bottom = tk.Frame(outer, bg=BG)
        bottom.pack(fill="x", pady=(14, 0))
        FlatButton(bottom, "Сохранить", self._save, accent=True).pack(side="right")
        FlatButton(bottom, "Отмена", self.destroy, ghost=True).pack(side="right", padx=(0, 10))

        self._select(0)

    def _select(self, index: int) -> None:
        self._page = index
        pages = (self.page_dns, self.page_browser, self.page_sites, self.page_updates)
        pages[index].tkraise()
        for position, button in enumerate(self._segments):
            button.set_active(position == index)

    def _build_dns(self, page: tk.Frame) -> None:
        card = Card(page, padding=(24, 20))
        card.pack(fill="x")
        body = card.body
        body.columnconfigure(1, weight=1)

        make_label(body, "DoH-сервер", bg=PANEL, font=(FONT, 10)).grid(
            row=0, column=0, sticky="w", pady=(0, 8)
        )
        make_entry(body, self.url_var, width=46).grid(
            row=0, column=1, sticky="ew", padx=(16, 0), pady=(0, 8)
        )
        make_label(
            body,
            "Например: https://dns.dagami.ru/dns-query",
            bg=PANEL,
            fg=DIM,
            font=(FONT, 8),
        ).grid(row=1, column=1, sticky="w", padx=(16, 0))

        make_label(body, "Bootstrap IP", bg=PANEL, font=(FONT, 10)).grid(
            row=2, column=0, sticky="w", pady=(18, 8)
        )
        make_entry(body, self.boot_var, width=46).grid(
            row=2, column=1, sticky="ew", padx=(16, 0), pady=(18, 8)
        )
        make_label(
            body,
            "IP через запятую: запрос идёт сразу на этот адрес, сертификат проверяется по имени DoH.",
            bg=PANEL,
            fg=DIM,
            font=(FONT, 8),
            wraplength=520,
        ).grid(row=3, column=1, sticky="w", padx=(16, 0))

        make_checkbutton(
            body,
            "Резервный DNS/53, если DoH недоступен",
            self.fallback_var,
        ).grid(row=4, column=1, sticky="w", padx=(16, 0), pady=(12, 0))

        make_label(body, "Таймаут, сек", bg=PANEL, font=(FONT, 10)).grid(
            row=5, column=0, sticky="w", pady=(18, 0)
        )
        make_spinbox(body, self.timeout_var, 2, 30, 1, 5).grid(
            row=5, column=1, sticky="w", padx=(16, 0), pady=(18, 0)
        )

        actions = tk.Frame(body, bg=PANEL)
        actions.grid(row=6, column=0, columnspan=2, sticky="w", pady=(22, 0))
        self.test_btn = FlatButton(actions, "Проверить DNS", self._test_dns)
        self.test_btn.pack(side="left")
        FlatButton(actions, "Сбросить на Dagami", self._reset_dns, ghost=True).pack(
            side="left", padx=(10, 0)
        )

        self.test_label = make_label(
            body,
            "",
            bg=PANEL,
            fg=MUTED,
            font=(FONT, 9),
            wraplength=640,
            justify="left",
        )
        self.test_label.grid(row=7, column=0, columnspan=2, sticky="w", pady=(14, 0))

        make_label(
            body,
            "Проверка запрашивает gemini.google.com через указанный DoH. "
            "Сертификат DoH-сервера проверяется всегда.",
            bg=PANEL,
            fg=DIM,
            font=(FONT, 8),
            wraplength=640,
            justify="left",
        ).grid(row=8, column=0, columnspan=2, sticky="w", pady=(12, 0))

    def _build_browser(self, page: tk.Frame) -> None:
        card = Card(page, padding=(24, 20))
        card.pack(fill="x")
        body = card.body
        body.columnconfigure(1, weight=1)

        make_label(body, "Браузер", bg=PANEL, font=(FONT, 10)).grid(
            row=0, column=0, sticky="w", pady=(0, 8)
        )
        self.browser_combo = ttk.Combobox(
            body,
            textvariable=self.browser_var,
            values=[label for label, _key in BROWSER_CHOICES],
            state="readonly",
            font=(FONT, 10),
        )
        self.browser_combo.grid(row=0, column=1, sticky="ew", padx=(16, 0), pady=(0, 8))

        self.detected_label = make_label(body, "", bg=PANEL, fg=DIM, font=(FONT, 8), wraplength=560)
        self.detected_label.grid(row=1, column=1, sticky="w", padx=(16, 0))

        make_label(body, "Путь к .exe", bg=PANEL, font=(FONT, 10)).grid(
            row=2, column=0, sticky="w", pady=(18, 8)
        )
        path_row = tk.Frame(body, bg=PANEL)
        path_row.grid(row=2, column=1, sticky="ew", padx=(16, 0), pady=(18, 8))
        path_row.columnconfigure(0, weight=1)
        make_entry(path_row, self.path_var, width=40).grid(row=0, column=0, sticky="ew")
        FlatButton(path_row, "Обзор…", self._browse_browser, small=True, ghost=True).grid(
            row=0, column=1, padx=(10, 0)
        )
        make_label(
            body,
            "Можно оставить пустым: браузер определится автоматически.",
            bg=PANEL,
            fg=DIM,
            font=(FONT, 8),
        ).grid(row=3, column=1, sticky="w", padx=(16, 0))

        make_label(body, "Размер окна", bg=PANEL, font=(FONT, 10)).grid(
            row=4, column=0, sticky="w", pady=(18, 0)
        )
        size_row = tk.Frame(body, bg=PANEL)
        size_row.grid(row=4, column=1, sticky="w", padx=(16, 0), pady=(18, 0))
        make_spinbox(size_row, self.width_var, 640, 3840, 20, 6).pack(side="left")
        make_label(size_row, "×", bg=PANEL, fg=MUTED).pack(side="left", padx=8)
        make_spinbox(size_row, self.height_var, 480, 2160, 20, 6).pack(side="left")

        make_label(body, "Порт прокси", bg=PANEL, font=(FONT, 10)).grid(
            row=5, column=0, sticky="w", pady=(16, 0)
        )
        make_spinbox(body, self.port_var, 1024, 65535, 1, 7).grid(
            row=5, column=1, sticky="w", padx=(16, 0), pady=(16, 0)
        )

        make_checkbutton(
            body,
            "Закрывать окна сайтов при выходе",
            self.close_browsers_var,
        ).grid(row=6, column=1, sticky="w", padx=(16, 0), pady=(12, 0))

        make_label(
            body,
            "Chrome открывает настоящее окно приложения без адресной строки. "
            "Firefox такого режима не имеет и открывает обычное окно; прокси "
            "прописывается в user.js отдельного профиля.",
            bg=PANEL,
            fg=DIM,
            font=(FONT, 8),
            wraplength=600,
            justify="left",
        ).grid(row=7, column=0, columnspan=2, sticky="w", pady=(20, 4))

        path = str(self.services.paths.profiles)
        row = tk.Frame(body, bg=PANEL)
        row.grid(row=8, column=0, columnspan=2, sticky="w", pady=(6, 0))
        make_label(row, path, bg=PANEL, fg=MUTED, font=(MONO, 8)).pack(side="left")
        FlatButton(row, "Открыть", lambda: open_path(path), small=True, ghost=True).pack(
            side="left", padx=(10, 0)
        )

    def _build_sites(self, page: tk.Frame) -> None:
        page.columnconfigure(0, weight=1)
        page.rowconfigure(0, weight=1)

        card = Card(page, padding=(16, 14), fill_height=True)
        card.grid(row=0, column=0, sticky="nsew")
        body = card.body
        body.columnconfigure(0, weight=1)
        body.rowconfigure(1, weight=1)

        make_label(
            body,
            "Каждый сайт открывается в отдельном окне и хранит свой вход.",
            bg=PANEL,
            fg=MUTED,
            font=(FONT, 9),
        ).grid(row=0, column=0, sticky="w", pady=(0, 10))

        tree_frame = tk.Frame(body, bg=PANEL)
        tree_frame.grid(row=1, column=0, sticky="nsew")
        tree_frame.columnconfigure(0, weight=1)
        tree_frame.rowconfigure(0, weight=1)

        self.tree = ttk.Treeview(
            tree_frame,
            columns=("name", "url", "pinned", "enabled"),
            show="headings",
            selectmode="browse",
            height=10,
        )
        self.tree.heading("name", text="Название")
        self.tree.heading("url", text="Адрес")
        self.tree.heading("pinned", text="Поверх")
        self.tree.heading("enabled", text="Вкл.")
        self.tree.column("name", width=150, stretch=False)
        self.tree.column("url", width=380, stretch=True)
        self.tree.column("pinned", width=80, stretch=False, anchor="center")
        self.tree.column("enabled", width=60, stretch=False, anchor="center")
        self.tree.grid(row=0, column=0, sticky="nsew")
        self.tree.bind("<Double-1>", lambda _e: self._edit_site())
        self.tree.bind("<<TreeviewSelect>>", lambda _e: None)

        scrollbar = ttk.Scrollbar(
            tree_frame,
            orient="vertical",
            command=self.tree.yview,
            style="Vertical.TScrollbar",
        )
        scrollbar.grid(row=0, column=1, sticky="ns")

        def on_tree_scroll(first: str, last: str) -> None:
            scrollbar.set(first, last)
            if float(first) <= 0.0 and float(last) >= 1.0:
                scrollbar.grid_remove()
            else:
                scrollbar.grid()

        self.tree.configure(yscrollcommand=on_tree_scroll)

        buttons = tk.Frame(body, bg=PANEL)
        buttons.grid(row=2, column=0, sticky="w", pady=(12, 0))
        for text, command in (
            ("Добавить", self._add_site),
            ("Изменить", self._edit_site),
            ("Удалить", self._delete_site),
            ("Выше", lambda: self._move_site(-1)),
            ("Ниже", lambda: self._move_site(1)),
        ):
            FlatButton(buttons, text, command, small=True, ghost=True).pack(
                side="left", padx=(0, 8)
            )
        FlatButton(buttons, "Очистить профиль", self._clear_profile, small=True, ghost=True).pack(
            side="left", padx=(12, 0)
        )

    def _build_updates(self, page: tk.Frame) -> None:
        card = Card(page, padding=(24, 20))
        card.pack(fill="x")
        body = card.body
        body.columnconfigure(1, weight=1)

        make_label(body, "Текущая версия", bg=PANEL, font=(FONT, 10)).grid(
            row=0, column=0, sticky="w", pady=(0, 8)
        )
        make_label(body, f"{__version__}", bg=PANEL, fg=TEXT, font=(MONO, 10)).grid(
            row=0, column=1, sticky="w", padx=(16, 0), pady=(0, 8)
        )

        make_label(body, "Релизы", bg=PANEL, font=(FONT, 10)).grid(
            row=1, column=0, sticky="w", pady=(8, 0)
        )
        make_label(body, updater.repo_slug(), bg=PANEL, fg=MUTED, font=(MONO, 9)).grid(
            row=1, column=1, sticky="w", padx=(16, 0), pady=(8, 0)
        )

        make_checkbutton(
            body,
            "Проверять обновления при запуске",
            self.auto_update_var,
        ).grid(row=2, column=1, sticky="w", padx=(16, 0), pady=(12, 0))

        actions = tk.Frame(body, bg=PANEL)
        actions.grid(row=3, column=0, columnspan=2, sticky="w", pady=(18, 0))
        self.update_btn = FlatButton(actions, "Проверить сейчас", self._check_updates)
        self.update_btn.pack(side="left")
        self.download_btn = FlatButton(
            actions, "Скачать .exe", self._download_update, ghost=True
        )
        self.download_btn.pack(side="left", padx=(10, 0))
        self.download_btn.configure(state="disabled")
        FlatButton(actions, "Страница релизов", self._open_releases, ghost=True).pack(
            side="left", padx=(10, 0)
        )

        self.update_status = make_label(
            body,
            "",
            bg=PANEL,
            fg=MUTED,
            font=(FONT, 9),
            wraplength=640,
            justify="left",
        )
        self.update_status.grid(row=4, column=0, columnspan=2, sticky="w", pady=(14, 0))

        note = Card(body, padding=(16, 12), radius=12, fill=PANEL_HOVER, border=BORDER)
        note.grid(row=5, column=0, columnspan=2, sticky="ew", pady=(20, 0))
        make_label(
            note.body,
            "Обновление программы",
            bg=PANEL_HOVER,
            fg=TEXT,
            font=(FONT, 9, "bold"),
        ).pack(anchor="w")
        make_label(
            note.body,
            "Внутри .exe нет сторонних pip-пакетов — только стандартная библиотека Python, "
            "поэтому отдельные модули обновлять не нужно. Обновляется сама программа: "
            "скачайте новый .exe и замените старый файл. Профили сайтов и config.json "
            "останутся на месте.",
            bg=PANEL_HOVER,
            fg=MUTED,
            font=(FONT, 9),
            wraplength=600,
            justify="left",
        ).pack(anchor="w", pady=(4, 0))
        make_label(
            note.body,
            "Еженедельная сборка в GitHub Actions всегда использует свежие Python и PyInstaller.",
            bg=PANEL_HOVER,
            fg=DIM,
            font=(FONT, 8),
            wraplength=600,
            justify="left",
        ).pack(anchor="w", pady=(6, 0))

        make_label(
            body,
            f"config: {self.services.paths.config}",
            bg=PANEL,
            fg=DIM,
            font=(MONO, 8),
        ).grid(row=6, column=0, columnspan=2, sticky="w", pady=(14, 0))

    # ------------------------------------------------------------------- load

    def _load(self) -> None:
        doh = self.config.doh
        self.url_var.set(doh.url)
        self.boot_var.set(", ".join(doh.bootstrap_ips))
        self.fallback_var.set(doh.fallback_tcp)
        self.timeout_var.set(str(int(round(doh.timeout))))

        labels = {key: label for label, key in BROWSER_CHOICES}
        self.browser_var.set(labels.get(self.config.browser, BROWSER_CHOICES[0][0]))
        self.path_var.set(self.config.browser_path)
        self.width_var.set(str(self.config.browser_width))
        self.height_var.set(str(self.config.browser_height))
        self.port_var.set(str(self.config.proxy_port))
        self.close_browsers_var.set(self.config.close_browsers_on_exit)
        self.auto_update_var.set(self.config.auto_check_updates)
        self._update_detected()
        self._render_sites()

    def _update_detected(self) -> None:
        found = browser_candidates()
        if not found:
            text = "Браузеры не найдены: укажите путь вручную."
        else:
            names = ", ".join(browser.name for browser in found)
            text = f"Найдено: {names}"
        current = resolve_browser(self.config)
        if current is not None and self.config.browser == "auto":
            text += f". Сейчас будет использован: {current.name}"
        self.detected_label.configure(text=text)

    def _render_sites(self) -> None:
        selected = self.tree.selection()
        selected_id = selected[0] if selected else (self.sites[0].id if self.sites else "")
        for item in self.tree.get_children():
            self.tree.delete(item)
        for site in self.sites:
            self.tree.insert(
                "",
                "end",
                iid=site.id,
                values=(
                    site.name,
                    site.url,
                    "да" if site.pinned else "—",
                    "да" if site.enabled else "нет",
                ),
            )
        if selected_id and self.tree.exists(selected_id):
            self.tree.selection_set(selected_id)
            self.tree.focus(selected_id)

    def _selected_site(self) -> Site | None:
        selection = self.tree.selection()
        if not selection:
            return None
        site_id = selection[0]
        for site in self.sites:
            if site.id == site_id:
                return site
        return None

    def _add_site(self) -> None:
        SiteDialog(self, None, {site.id for site in self.sites}, self._site_created)

    def _site_created(self, site: Site) -> None:
        self.sites.append(site)
        self._render_sites()
        self.tree.selection_set(site.id)

    def _edit_site(self) -> None:
        site = self._selected_site()
        if site is None:
            messagebox.showinfo("Сайты", "Выберите сайт в списке.", parent=self)
            return
        SiteDialog(
            self,
            site,
            {item.id for item in self.sites if item.id != site.id},
            self._site_updated,
        )

    def _site_updated(self, updated: Site) -> None:
        for index, site in enumerate(self.sites):
            if site.id == updated.id:
                self.sites[index] = updated
                break
        self._render_sites()
        self.tree.selection_set(updated.id)

    def _delete_site(self) -> None:
        site = self._selected_site()
        if site is None:
            messagebox.showinfo("Сайты", "Выберите сайт в списке.", parent=self)
            return
        if not messagebox.askyesno(
            "Удалить сайт",
            f"Убрать «{site.name}» из списка?\nПрофиль и сохранённый вход останутся на диске.",
            parent=self,
        ):
            return
        self.sites = [item for item in self.sites if item.id != site.id]
        self._render_sites()

    def _move_site(self, direction: int) -> None:
        site = self._selected_site()
        if site is None:
            return
        index = self.sites.index(site)
        target = index + direction
        if 0 <= target < len(self.sites):
            self.sites[index], self.sites[target] = self.sites[target], self.sites[index]
            self._render_sites()
            self.tree.selection_set(site.id)

    def _clear_profile(self) -> None:
        site = self._selected_site()
        if site is None:
            messagebox.showinfo("Профиль", "Выберите сайт в списке.", parent=self)
            return
        path = self.services.launcher.profile_dir(site)
        if not path.exists():
            messagebox.showinfo("Профиль", f"Профиль ещё не создан:\n{path}", parent=self)
            return
        if not messagebox.askyesno(
            "Очистить профиль",
            f"Удалить профиль «{site.name}»?\n{path}\n\n"
            "Будет удалён сохранённый вход и cookies. Окно сайта лучше закрыть заранее.",
            parent=self,
        ):
            return
        try:
            shutil.rmtree(path)
        except OSError as exc:
            messagebox.showerror("Профиль", f"Не удалось удалить профиль:\n{exc}", parent=self)
            return
        messagebox.showinfo(
            "Профиль",
            "Профиль удалён. При следующем открытии вход потребуется снова.",
            parent=self,
        )

    def _browse_browser(self) -> None:
        path = filedialog.askopenfilename(
            parent=self,
            title="Выберите браузер",
            filetypes=[("Исполняемые файлы", "*.exe"), ("Все файлы", "*.*")]
            if sys.platform == "win32"
            else [("Все файлы", "*.*")],
        )
        if path:
            self.path_var.set(path)

    # -------------------------------------------------------------------- dns

    def _reset_dns(self) -> None:
        self.url_var.set(DEFAULT_DOH_URL)
        self.boot_var.set(", ".join(DEFAULT_BOOTSTRAP))
        self.fallback_var.set(True)
        self.timeout_var.set("8")
        self.test_label.configure(text="", fg=MUTED)

    def _collect_doh(self) -> DohSettings:
        return DohSettings.from_dict(
            {
                "url": self.url_var.get(),
                "bootstrap_ips": self.boot_var.get(),
                "fallback_tcp": self.fallback_var.get(),
                "timeout": self.timeout_var.get() or "8",
            }
        )

    def _test_dns(self) -> None:
        if self._test_running:
            return
        try:
            doh = self._collect_doh()
        except (ConfigError, ValueError) as exc:
            self.test_label.configure(text=str(exc), fg=ERROR)
            return
        self._test_running = True
        self.test_btn.configure(state="disabled")
        self.test_label.configure(text="Проверяю DoH…", fg=MUTED)

        def worker() -> None:
            started = time.monotonic()
            try:
                resolver = DohResolver(doh, None)
                ips, via = resolver.resolve_detailed("gemini.google.com", use_cache=False)
                elapsed = time.monotonic() - started
                text = f"OK · {via} · gemini.google.com → {', '.join(ips)} ({elapsed:.2f} с)"
                color = OK
            except Exception as exc:
                elapsed = time.monotonic() - started
                text = f"Ошибка за {elapsed:.2f} с: {exc}"
                color = ERROR

            def finish() -> None:
                if not self.winfo_exists():
                    return
                self._test_running = False
                self.test_btn.configure(state="normal")
                self.test_label.configure(text=text, fg=color)

            try:
                self.after(0, finish)
            except tk.TclError:
                pass

        threading.Thread(target=worker, name="doh-test", daemon=True).start()

    # ---------------------------------------------------------------- updates

    def _check_updates(self) -> None:
        if self._update_checking:
            return
        self._update_checking = True
        self.update_btn.configure(state="disabled")
        self.update_status.configure(text="Проверяю GitHub…", fg=MUTED)

        def worker() -> None:
            release = None
            error = ""
            try:
                release = updater.check()
            except updater.UpdateError as exc:
                error = str(exc)
            except Exception as exc:  # pragma: no cover - defensive
                error = f"{type(exc).__name__}: {exc}"

            def finish() -> None:
                if not self.winfo_exists():
                    return
                self._update_checking = False
                self.update_btn.configure(state="normal")
                if error:
                    self.update_status.configure(text=f"Не удалось проверить: {error}", fg=ERROR)
                    return
                if release is None:
                    self.update_status.configure(
                        text=f"Установлена последняя версия {__version__}.", fg=OK
                    )
                    return
                self._release = release
                note = f" (есть файл {release.exe_name})" if release.exe_url else ""
                self.update_status.configure(
                    text=f"Доступна версия {release.version}{note}.", fg=OK
                )
                if release.exe_url:
                    self.download_btn.set_active(True)
                    self.download_btn.configure(state="normal")

            try:
                self.after(0, finish)
            except tk.TclError:
                pass

        threading.Thread(target=worker, name="update-check", daemon=True).start()

    def _download_update(self) -> None:
        release = self._release
        if release is None or not release.exe_url:
            return
        self.download_btn.configure(state="disabled")
        self.update_status.configure(text="Скачиваю обновление…", fg=MUTED)
        target = self.services.paths.root / "updates"

        def worker() -> None:
            path = None
            error = ""
            try:
                path = updater.download(release, target)
            except (updater.UpdateError, OSError) as exc:
                error = str(exc)

            def finish() -> None:
                if not self.winfo_exists():
                    return
                self.download_btn.configure(state="normal")
                if error:
                    self.update_status.configure(text=f"Ошибка загрузки: {error}", fg=ERROR)
                    return
                self.update_status.configure(text=f"Скачано: {path}", fg=OK)
                if messagebox.askyesno(
                    "Обновление",
                    f"Файл скачан:\n{path}\n\nОткрыть папку? Замените старый Synapse.exe "
                    "новым и запустите его снова.",
                    parent=self,
                ):
                    open_path(str(path.parent))

            try:
                self.after(0, finish)
            except tk.TclError:
                pass

        threading.Thread(target=worker, name="update-download", daemon=True).start()

    def _open_releases(self) -> None:
        webbrowser.open(f"https://github.com/{updater.repo_slug()}/releases")

    # ------------------------------------------------------------------- save

    def _get_int(self, variable: tk.StringVar, title: str, low: int, high: int) -> int:
        try:
            value = int(float(variable.get().strip().replace(",", ".")))
        except ValueError as exc:
            raise ConfigError(f"{title}: введите целое число.") from exc
        if not low <= value <= high:
            raise ConfigError(f"{title}: допустимо от {low} до {high}.")
        return value

    def _collect(self) -> AppConfig:
        doh = self._collect_doh()
        browser_label = self.browser_var.get()
        browser = next(
            (key for label, key in BROWSER_CHOICES if label == browser_label),
            "auto",
        )
        path = self.path_var.get().strip()
        if browser == "custom" and (not path or not os.path.isfile(path)):
            raise ConfigError("Укажите существующий путь к браузеру.")
        if path and not os.path.isfile(path):
            raise ConfigError("Файл браузера по указанному пути не найден.")

        width = self._get_int(self.width_var, "Ширина окна", 640, 3840)
        height = self._get_int(self.height_var, "Высота окна", 480, 2160)
        port = self._get_int(self.port_var, "Порт прокси", 1024, 65535)

        return AppConfig(
            doh=doh,
            proxy_port=port,
            allowed_ports=list(self.config.allowed_ports),
            sites=[copy.copy(site) for site in self.sites],
            browser=browser,
            browser_path=path,
            browser_width=width,
            browser_height=height,
            close_browsers_on_exit=self.close_browsers_var.get(),
            window_geometry=self.config.window_geometry,
            window_topmost=self.config.window_topmost,
            auto_check_updates=self.auto_update_var.get(),
            last_update_check=self.config.last_update_check,
        )

    def _save(self) -> None:
        try:
            config = self._collect()
            if not self.sites:
                if not messagebox.askyesno(
                    "Сайты",
                    "Список сайтов пуст. Сохранить так?",
                    parent=self,
                ):
                    return
        except (ConfigError, ValueError) as exc:
            messagebox.showerror("Настройки", str(exc), parent=self)
            return

        try:
            warnings = self.on_save(config)
        except Exception as exc:
            messagebox.showerror("Настройки", f"Не удалось применить настройки:\n{exc}", parent=self)
            return
        self.destroy()
        if warnings:
            messagebox.showinfo("Настройки", "\n\n".join(warnings), parent=self.parent)


class SiteDialog(tk.Toplevel):
    def __init__(
        self,
        parent: tk.Misc,
        site: Site | None,
        known_ids: set[str],
        on_ok: Callable[[Site], None],
    ):
        super().__init__(parent)
        self.site = site
        self.known_ids = known_ids
        self.on_ok = on_ok
        self.title("Изменить сайт" if site else "Новый сайт")
        self.configure(bg=BG)
        self.geometry("560x400")
        self.resizable(False, False)
        self.transient(parent)
        self.grab_set()
        self.bind("<Escape>", lambda _e: self.destroy())
        self.bind("<Return>", lambda _e: self._save())
        try:
            self.attributes("-topmost", bool(int(parent.attributes("-topmost"))))
        except (tk.TclError, TypeError, ValueError):
            pass

        self.name_var = tk.StringVar(value=site.name if site else "")
        self.url_var = tk.StringVar(value=site.url if site else "https://")
        self.pinned_var = tk.BooleanVar(value=site.pinned if site else False)
        self.enabled_var = tk.BooleanVar(value=site.enabled if site else True)

        outer = tk.Frame(self, bg=BG)
        outer.pack(fill="both", expand=True, padx=24, pady=20)

        make_label(outer, "Сайт", font=(FONT, 15, "bold"), fg=TEXT).pack(anchor="w", pady=(0, 12))

        card = Card(outer, padding=(20, 16))
        card.pack(fill="x")
        body = card.body
        body.columnconfigure(1, weight=1)

        make_label(body, "Название", bg=PANEL, font=(FONT, 10)).grid(
            row=0, column=0, sticky="w", pady=(0, 8)
        )
        make_entry(body, self.name_var, width=34).grid(
            row=0, column=1, sticky="ew", padx=(14, 0), pady=(0, 8)
        )

        make_label(body, "Адрес", bg=PANEL, font=(FONT, 10)).grid(
            row=1, column=0, sticky="w", pady=(10, 8)
        )
        make_entry(body, self.url_var, width=34).grid(
            row=1, column=1, sticky="ew", padx=(14, 0), pady=(10, 8)
        )
        make_label(
            body,
            "Например: https://chatgpt.com",
            bg=PANEL,
            fg=DIM,
            font=(FONT, 8),
        ).grid(row=2, column=1, sticky="w", padx=(14, 0))

        make_checkbutton(body, "Закреплять окно поверх всех", self.pinned_var).grid(
            row=3, column=1, sticky="w", padx=(14, 0), pady=(12, 0)
        )
        make_checkbutton(body, "Показывать в списке", self.enabled_var).grid(
            row=4, column=1, sticky="w", padx=(14, 0), pady=(4, 0)
        )

        if site:
            make_label(
                body,
                f"Профиль: profiles/{site.id}",
                bg=PANEL,
                fg=DIM,
                font=(MONO, 8),
            ).grid(row=5, column=0, columnspan=2, sticky="w", pady=(16, 0))

        buttons = tk.Frame(outer, bg=BG)
        buttons.pack(fill="x", pady=(18, 0))
        FlatButton(buttons, "Сохранить", self._save, accent=True).pack(side="right")
        FlatButton(buttons, "Отмена", self.destroy, ghost=True).pack(side="right", padx=(0, 10))

        self.update_idletasks()
        x = parent.winfo_rootx() + max(0, (parent.winfo_width() - self.winfo_width()) // 2)
        y = parent.winfo_rooty() + max(0, (parent.winfo_height() - self.winfo_height()) // 2)
        self.geometry(f"+{x}+{y}")
        self.after(60, self._focus_name)

    def _focus_name(self) -> None:
        try:
            self.lift()
            self.focus_force()
        except tk.TclError:
            pass
        dark_titlebar(self)

    def _save(self) -> None:
        name = self.name_var.get().strip()
        try:
            url = normalize_site_url(self.url_var.get())
        except ConfigError as exc:
            messagebox.showerror("Сайт", str(exc), parent=self)
            return
        if not name:
            messagebox.showerror("Сайт", "Укажите название.", parent=self)
            return

        if self.site is not None:
            site_id = self.site.id
        else:
            base = slugify(name, "site")
            site_id = base
            index = 2
            while site_id in self.known_ids:
                site_id = f"{base}-{index}"
                index += 1

        site = Site(
            id=site_id,
            name=name[:80],
            url=url,
            pinned=self.pinned_var.get(),
            enabled=self.enabled_var.get(),
        )
        self.on_ok(site)
        self.destroy()
