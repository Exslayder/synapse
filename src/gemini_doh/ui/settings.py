"""Settings window: DoH, browser, sites."""

from __future__ import annotations

import copy
import os
import shutil
import subprocess
import sys
import threading
import time
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from collections.abc import Callable

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
from .theme import ACCENT, BG, BORDER, ERROR, FONT, MUTED, OK, PANEL, PANEL_HOVER, TEXT
from .widgets import FlatButton, make_checkbutton, make_entry, make_label

BROWSER_CHOICES = [
    ("Авто (Edge / Chrome)", "auto"),
    ("Microsoft Edge", "edge"),
    ("Google Chrome", "chrome"),
    ("Brave", "brave"),
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

        self.title("Настройки — Gemini DoH")
        self.configure(bg=BG)
        self.geometry("780x670")
        self.minsize(700, 600)
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
        self.test_var = tk.StringVar(value="")
        self.browser_var = tk.StringVar()
        self.path_var = tk.StringVar()
        self.width_var = tk.StringVar()
        self.height_var = tk.StringVar()
        self.port_var = tk.StringVar()
        self.close_browsers_var = tk.BooleanVar(value=True)

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

    def _build(self) -> None:
        outer = tk.Frame(self, bg=BG)
        outer.pack(fill="both", expand=True, padx=20, pady=16)

        make_label(outer, "Настройки", font=(FONT, 17), fg=TEXT).pack(anchor="w")
        make_label(
            outer,
            "Изменения DNS применяются сразу. Порт прокси — после перезапуска.",
            fg=MUTED,
            font=(FONT, 9),
        ).pack(anchor="w", pady=(2, 12))

        notebook = ttk.Notebook(outer)
        notebook.pack(fill="both", expand=True)

        dns_tab = tk.Frame(notebook, bg=BG)
        browser_tab = tk.Frame(notebook, bg=BG)
        sites_tab = tk.Frame(notebook, bg=BG)
        notebook.add(dns_tab, text="  DNS  ")
        notebook.add(browser_tab, text="  Браузер  ")
        notebook.add(sites_tab, text="  Сайты  ")

        self._build_dns(dns_tab)
        self._build_browser(browser_tab)
        self._build_sites(sites_tab)

        bottom = tk.Frame(outer, bg=BG)
        bottom.pack(fill="x", pady=(14, 0))
        FlatButton(bottom, "Сохранить", self._save, accent=True).pack(side="right")
        FlatButton(bottom, "Отмена", self.destroy).pack(side="right", padx=(0, 8))

    def _build_dns(self, tab: tk.Frame) -> None:
        tab.columnconfigure(1, weight=1)

        make_label(tab, "DoH-сервер").grid(row=0, column=0, sticky="w", pady=(18, 4))
        url_entry = make_entry(tab, self.url_var)
        url_entry.grid(row=0, column=1, sticky="ew", padx=(14, 0), pady=(18, 4))
        make_label(
            tab,
            "Например: https://dns.dagami.ru/dns-query",
            fg=MUTED,
            font=(FONT, 9),
        ).grid(row=1, column=1, sticky="w", padx=(14, 0))

        make_label(tab, "Bootstrap IP").grid(row=2, column=0, sticky="w", pady=(16, 4))
        make_entry(tab, self.boot_var).grid(row=2, column=1, sticky="ew", padx=(14, 0), pady=(16, 4))
        make_label(
            tab,
            "IP через запятую. Запрос идёт сразу на этот адрес, а сертификат "
            "проверяется по имени DoH-сервера.",
            fg=MUTED,
            font=(FONT, 9),
            wraplength=520,
        ).grid(row=3, column=1, sticky="w", padx=(14, 0))

        make_checkbutton(
            tab,
            "Разрешить резервный DNS/53, если DoH недоступен",
            self.fallback_var,
        ).grid(row=4, column=1, sticky="w", padx=(14, 0), pady=(16, 4))

        make_label(tab, "Таймаут, сек").grid(row=5, column=0, sticky="w", pady=(8, 4))
        timeout = tk.Spinbox(
            tab,
            from_=2,
            to=30,
            increment=1,
            textvariable=self.timeout_var,
            width=6,
            bg=PANEL,
            fg=TEXT,
            buttonbackground=PANEL_HOVER,
            insertbackground=ACCENT,
            relief="flat",
            highlightthickness=1,
            highlightbackground=BORDER,
            highlightcolor=ACCENT,
            font=(FONT, 10),
        )
        timeout.grid(row=5, column=1, sticky="w", padx=(14, 0), pady=(8, 4))

        actions = tk.Frame(tab, bg=BG)
        actions.grid(row=6, column=0, columnspan=2, sticky="w", pady=(18, 6))
        self.test_btn = FlatButton(actions, "Проверить DNS", self._test_dns, small=True)
        self.test_btn.pack(side="left")
        FlatButton(actions, "Сбросить на Dagami", self._reset_dns, small=True).pack(
            side="left", padx=(8, 0)
        )

        self.test_label = make_label(
            tab,
            "",
            fg=MUTED,
            font=(FONT, 9),
            wraplength=620,
            justify="left",
        )
        self.test_label.grid(row=7, column=0, columnspan=2, sticky="w", pady=(2, 8))

        make_label(
            tab,
            "Проверка запрашивает gemini.google.com через указанный DoH. "
            "Сертификат DoH-сервера всегда проверяется.",
            fg=MUTED,
            font=(FONT, 9),
            wraplength=620,
            justify="left",
        ).grid(row=8, column=0, columnspan=2, sticky="w", pady=(4, 0))

    def _build_browser(self, tab: tk.Frame) -> None:
        tab.columnconfigure(1, weight=1)

        make_label(tab, "Браузер").grid(row=0, column=0, sticky="w", pady=(18, 4))
        self.browser_combo = ttk.Combobox(
            tab,
            textvariable=self.browser_var,
            values=[label for label, _key in BROWSER_CHOICES],
            state="readonly",
        )
        self.browser_combo.grid(row=0, column=1, sticky="ew", padx=(14, 0), pady=(18, 4))

        make_label(tab, "Путь к .exe").grid(row=1, column=0, sticky="w", pady=(12, 4))
        path_row = tk.Frame(tab, bg=BG)
        path_row.grid(row=1, column=1, sticky="ew", padx=(14, 0), pady=(12, 4))
        path_row.columnconfigure(0, weight=1)
        make_entry(path_row, self.path_var).grid(row=0, column=0, sticky="ew")
        FlatButton(path_row, "Обзор…", self._browse_browser, small=True).grid(
            row=0, column=1, padx=(8, 0)
        )
        make_label(
            tab,
            "Можно оставить пустым: браузер определится автоматически.",
            fg=MUTED,
            font=(FONT, 9),
        ).grid(row=2, column=1, sticky="w", padx=(14, 0))

        make_label(tab, "Размер окна сайта").grid(row=3, column=0, sticky="w", pady=(18, 4))
        size_row = tk.Frame(tab, bg=BG)
        size_row.grid(row=3, column=1, sticky="w", padx=(14, 0), pady=(18, 4))
        width = tk.Spinbox(
            size_row,
            from_=640,
            to=3840,
            increment=20,
            textvariable=self.width_var,
            width=7,
            bg=PANEL,
            fg=TEXT,
            buttonbackground=PANEL_HOVER,
            insertbackground=ACCENT,
            relief="flat",
            highlightthickness=1,
            highlightbackground=BORDER,
            highlightcolor=ACCENT,
            font=(FONT, 10),
        )
        width.pack(side="left")
        make_label(size_row, " × ", fg=MUTED).pack(side="left")
        height = tk.Spinbox(
            size_row,
            from_=480,
            to=2160,
            increment=20,
            textvariable=self.height_var,
            width=7,
            bg=PANEL,
            fg=TEXT,
            buttonbackground=PANEL_HOVER,
            insertbackground=ACCENT,
            relief="flat",
            highlightthickness=1,
            highlightbackground=BORDER,
            highlightcolor=ACCENT,
            font=(FONT, 10),
        )
        height.pack(side="left")

        make_label(tab, "Порт прокси").grid(row=4, column=0, sticky="w", pady=(12, 4))
        port = tk.Spinbox(
            tab,
            from_=1024,
            to=65535,
            increment=1,
            textvariable=self.port_var,
            width=8,
            bg=PANEL,
            fg=TEXT,
            buttonbackground=PANEL_HOVER,
            insertbackground=ACCENT,
            relief="flat",
            highlightthickness=1,
            highlightbackground=BORDER,
            highlightcolor=ACCENT,
            font=(FONT, 10),
        )
        port.grid(row=4, column=1, sticky="w", padx=(14, 0), pady=(12, 4))

        make_checkbutton(
            tab,
            "Закрывать окна сайтов при выходе из программы",
            self.close_browsers_var,
        ).grid(row=5, column=1, sticky="w", padx=(14, 0), pady=(18, 4))

        make_label(
            tab,
            "Логины сохраняются в profiles/<id>. У каждого сайта отдельный профиль.",
            fg=MUTED,
            font=(FONT, 9),
            wraplength=560,
            justify="left",
        ).grid(row=6, column=0, columnspan=2, sticky="w", pady=(18, 2))

        path = str(self.services.paths.profiles)
        row = tk.Frame(tab, bg=BG)
        row.grid(row=7, column=0, columnspan=2, sticky="w", pady=(2, 0))
        make_label(row, path, fg=MUTED, font=("Consolas", 9)).pack(side="left")
        FlatButton(row, "Открыть", lambda: open_path(path), small=True).pack(
            side="left", padx=(8, 0)
        )

    def _build_sites(self, tab: tk.Frame) -> None:
        tab.columnconfigure(0, weight=1)
        tab.rowconfigure(1, weight=1)

        make_label(
            tab,
            "Сайты открываются в отдельных окнах браузера. «Поверх» закрепляет окно.",
            fg=MUTED,
            font=(FONT, 9),
            wraplength=660,
            justify="left",
        ).grid(row=0, column=0, sticky="w", pady=(14, 8))

        tree_frame = tk.Frame(tab, bg=BG)
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
        self.tree.column("name", width=140, stretch=False)
        self.tree.column("url", width=360, stretch=True)
        self.tree.column("pinned", width=70, stretch=False, anchor="center")
        self.tree.column("enabled", width=50, stretch=False, anchor="center")
        self.tree.grid(row=0, column=0, sticky="nsew")
        self.tree.bind("<Double-1>", lambda _e: self._edit_site())

        scrollbar = ttk.Scrollbar(
            tree_frame,
            orient="vertical",
            command=self.tree.yview,
            style="Vertical.TScrollbar",
        )
        scrollbar.grid(row=0, column=1, sticky="ns")
        self.tree.configure(yscrollcommand=scrollbar.set)

        buttons = tk.Frame(tab, bg=BG)
        buttons.grid(row=2, column=0, sticky="w", pady=(10, 0))
        for text, command in (
            ("Добавить", self._add_site),
            ("Изменить", self._edit_site),
            ("Удалить", self._delete_site),
            ("Выше", lambda: self._move_site(-1)),
            ("Ниже", lambda: self._move_site(1)),
        ):
            FlatButton(buttons, text, command, small=True).pack(side="left", padx=(0, 6))
        FlatButton(buttons, "Очистить профиль", self._clear_profile, small=True).pack(
            side="left", padx=(12, 0)
        )

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
        self._render_sites()

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
        SiteDialog(
            self,
            None,
            {site.id for site in self.sites},
            self._site_created,
        )

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
        messagebox.showinfo("Профиль", "Профиль удалён. При следующем открытии вход потребуется снова.", parent=self)

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

    def _reset_dns(self) -> None:
        self.url_var.set(DEFAULT_DOH_URL)
        self.boot_var.set(", ".join(DEFAULT_BOOTSTRAP))
        self.fallback_var.set(True)
        self.timeout_var.set("8")
        self.test_var.set("")
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
                text = (
                    f"OK · {via} · gemini.google.com → {', '.join(ips)} "
                    f"({elapsed:.2f} с)"
                )
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
        self.geometry("540x350")
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
        outer.pack(fill="both", expand=True, padx=22, pady=18)
        outer.columnconfigure(1, weight=1)

        make_label(outer, "Название").grid(row=0, column=0, sticky="w", pady=(4, 6))
        make_entry(outer, self.name_var).grid(row=0, column=1, sticky="ew", padx=(14, 0), pady=(4, 6))

        make_label(outer, "Адрес").grid(row=1, column=0, sticky="w", pady=6)
        make_entry(outer, self.url_var).grid(row=1, column=1, sticky="ew", padx=(14, 0), pady=6)
        make_label(
            outer,
            "Например: https://chatgpt.com",
            fg=MUTED,
            font=(FONT, 9),
        ).grid(row=2, column=1, sticky="w", padx=(14, 0))

        make_checkbutton(outer, "Закреплять окно поверх всех", self.pinned_var).grid(
            row=3, column=1, sticky="w", padx=(14, 0), pady=(18, 4)
        )
        make_checkbutton(outer, "Показывать в списке", self.enabled_var).grid(
            row=4, column=1, sticky="w", padx=(14, 0), pady=2
        )

        if site:
            make_label(
                outer,
                f"Профиль: profiles/{site.id}",
                fg=MUTED,
                font=("Consolas", 9),
            ).grid(row=5, column=0, columnspan=2, sticky="w", pady=(16, 0))

        buttons = tk.Frame(outer, bg=BG)
        buttons.grid(row=6, column=0, columnspan=2, sticky="e", pady=(24, 0))
        FlatButton(buttons, "Сохранить", self._save, accent=True).pack(side="right")
        FlatButton(buttons, "Отмена", self.destroy).pack(side="right", padx=(0, 8))

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
