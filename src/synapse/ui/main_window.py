"""Main application window."""

from __future__ import annotations

import queue
import threading
import time
import tkinter as tk
import webbrowser
from tkinter import messagebox, ttk

from .. import APP_NAME, __version__, updater
from ..browser import resolve_browser
from ..config import AppConfig
from ..runtime import Services
from .settings import SettingsDialog
from .theme import (
    ACCENT,
    ACCENT_SOFT,
    BG,
    BORDER,
    DIM,
    ERROR,
    FONT,
    MONO,
    MUTED,
    OK,
    PANEL,
    SELECT_BG,
    SELECT_FG,
    TEXT,
    dark_titlebar,
    setup_ttk,
)
from .widgets import (
    Card,
    Field,
    FlatButton,
    ScrollFrame,
    Switch,
    attach_text_menu,
    make_label,
)


def plural_sites(count: int) -> str:
    """Russian plural for «сайт» (1 сайт, 2 сайта, 5 сайтов)."""
    if count % 10 == 1 and count % 100 != 11:
        return "сайт"
    if 2 <= count % 10 <= 4 and not 12 <= count % 100 <= 14:
        return "сайта"
    return "сайтов"


class MainWindow:
    def __init__(self, root: tk.Tk, services: Services, log_queue: queue.Queue[str]):
        self.root = root
        self.services = services
        self.config: AppConfig = services.config
        self.log_queue = log_queue
        self._closed = False
        self._settings: SettingsDialog | None = None
        self._pin_vars: dict[str, tk.BooleanVar] = {}
        self._site_status: dict[str, tk.Label] = {}
        self._site_grace: dict[str, float] = {}
        self._site_errors: dict[str, float] = {}
        self._status_stop = threading.Event()
        self._update_release: updater.Release | None = None
        self._update_checking = False
        self._status_job: str | None = None
        self.status_var = tk.StringVar(value="")
        self.topmost_var = tk.BooleanVar(value=self.config.window_topmost)

        setup_ttk(root)
        self._configure_root()
        self._build()
        self._refresh_sites()
        self._update_meta()
        self._poll_log()
        self._start_status_watch()
        self.root.after(1800, self._poll_updates)

        root.protocol("WM_DELETE_WINDOW", self.close)
        root.report_callback_exception = self._report_exception

    # ------------------------------------------------------------------ setup

    def _configure_root(self) -> None:
        root = self.root
        root.title(f"{APP_NAME} {__version__}")
        root.configure(bg=BG)
        root.minsize(470, 640)
        geometry = self.config.window_geometry or "600x830"
        try:
            root.geometry(geometry)
        except tk.TclError:
            root.geometry("600x830")
        try:
            root.iconbitmap(default=str(self.services.paths.icon))
        except tk.TclError:
            pass
        root.columnconfigure(0, weight=1)
        if self.config.window_topmost:
            root.attributes("-topmost", True)
        root.update_idletasks()
        self._ensure_visible()
        self.root.after(60, lambda: dark_titlebar(self.root))

    def _build(self) -> None:
        root = self.root
        outer = 22

        header = tk.Frame(root, bg=BG)
        header.grid(row=0, column=0, sticky="ew", padx=outer, pady=(16, 2))
        header.columnconfigure(1, weight=1)

        self._logo = self._load_logo(header, 44)
        self._logo.grid(row=0, column=0, rowspan=2, sticky="nw", padx=(0, 12))

        title_row = tk.Frame(header, bg=BG)
        title_row.grid(row=0, column=1, sticky="w")
        tk.Label(
            title_row,
            text=APP_NAME,
            bg=BG,
            fg=TEXT,
            font=(FONT, 17, "bold"),
        ).pack(side="left")
        tk.Label(
            title_row,
            text=f"v{__version__}",
            bg=PANEL,
            fg=MUTED,
            font=(FONT, 8),
            padx=7,
            pady=1,
        ).pack(side="left", padx=(9, 0), pady=(3, 0))

        right = tk.Frame(header, bg=BG)
        right.grid(row=0, column=2, sticky="e")
        Switch(right, text="Поверх", variable=self.topmost_var, command=self._toggle_topmost).pack(
            side="left", padx=(0, 10)
        )
        self._view_button = FlatButton(right, "Вид", self._post_view_menu, small=True)
        self._view_button.pack(side="left")
        self._build_view_menu()

        # The subtitle sits on its own row and spans the whole width, so it can
        # never be squeezed under the buttons at the minimum window size.
        self.subtitle_var = tk.StringVar(value="")
        tk.Label(
            header,
            textvariable=self.subtitle_var,
            bg=BG,
            fg=MUTED,
            font=(FONT, 9),
            anchor="w",
        ).grid(row=1, column=1, columnspan=2, sticky="ew", pady=(2, 0))

        sites_head = tk.Frame(root, bg=BG)
        sites_head.grid(row=1, column=0, sticky="ew", padx=outer, pady=(12, 4))
        make_label(sites_head, "САЙТЫ", fg=MUTED, font=(FONT, 8, "bold")).pack(side="left")
        make_label(
            sites_head,
            "отдельное окно · свой профиль",
            fg=DIM,
            font=(FONT, 8),
        ).pack(side="right")

        sites_card = Card(root, padding=(8, 8), radius=16)
        sites_card.grid(row=2, column=0, sticky="ew", padx=outer)
        self.sites_scroll = ScrollFrame(sites_card.body, height=60, bg=PANEL)
        self.sites_scroll.pack(fill="x")
        self.sites_inner = self.sites_scroll.inner

        conn_head = tk.Frame(root, bg=BG)
        conn_head.grid(row=3, column=0, sticky="ew", padx=outer, pady=(12, 4))
        make_label(conn_head, "ПОДКЛЮЧЕНИЕ", fg=MUTED, font=(FONT, 8, "bold")).pack(side="left")
        FlatButton(conn_head, "Изменить", self.open_settings, small=True, ghost=True).pack(
            side="right"
        )

        doh_card = Card(root, padding=(12, 6), radius=16)
        doh_card.grid(row=4, column=0, sticky="ew", padx=outer)
        doh_card.body.columnconfigure(0, weight=1)
        self.doh_var = tk.StringVar(value="")
        self.doh_field = Field(doh_card.body, self.doh_var, width=30, readonly=True)
        self.doh_field.entry.configure(font=(MONO, 9))
        self.doh_field.grid(row=0, column=0, sticky="ew")

        log_head = tk.Frame(root, bg=BG)
        log_head.grid(row=5, column=0, sticky="new", padx=outer, pady=(12, 4))
        log_head.columnconfigure(0, weight=1)
        make_label(log_head, "ЖУРНАЛ", fg=MUTED, font=(FONT, 8, "bold")).grid(row=0, column=0, sticky="w")
        FlatButton(log_head, "Копировать", self._copy_log, small=True, ghost=True).grid(
            row=0, column=1, sticky="e", padx=(0, 8)
        )
        FlatButton(log_head, "Очистить", self._clear_log, small=True, ghost=True).grid(
            row=0, column=2, sticky="e"
        )

        log_card = Card(root, padding=(12, 10), radius=16, fill_height=True)
        log_card.grid(row=6, column=0, sticky="nsew", padx=outer)
        root.rowconfigure(6, weight=1)
        log_card.body.columnconfigure(0, weight=1)
        log_card.body.rowconfigure(0, weight=1)
        self.log_text = tk.Text(
            log_card.body,
            bg=PANEL,
            fg=MUTED,
            insertbackground=TEXT,
            selectbackground=SELECT_BG,
            selectforeground=SELECT_FG,
            relief="flat",
            bd=0,
            highlightthickness=0,
            font=(MONO, 9),
            height=6,
            wrap="word",
            padx=2,
            pady=2,
            state="disabled",
        )
        self.log_text.grid(row=0, column=0, sticky="nsew")
        attach_text_menu(self.log_text, on_clear=self._clear_log)
        self.log_scrollbar = ttk.Scrollbar(
            log_card.body,
            orient="vertical",
            command=self.log_text.yview,
            style="Log.Vertical.TScrollbar",
        )
        self.log_scrollbar.grid(row=0, column=1, sticky="ns")

        def on_log_scroll(first: str, last: str) -> None:
            self.log_scrollbar.set(first, last)
            if float(first) <= 0.0 and float(last) >= 1.0:
                self.log_scrollbar.grid_remove()
            else:
                self.log_scrollbar.grid()

        self.log_text.configure(yscrollcommand=on_log_scroll)

        buttons = tk.Frame(root, bg=BG)
        buttons.grid(row=7, column=0, sticky="ew", padx=outer, pady=(12, 4))
        FlatButton(buttons, "Открыть все", self._open_all, accent=True).pack(side="left")
        FlatButton(buttons, "Настройки", self.open_settings).pack(side="left", padx=(10, 0))
        FlatButton(buttons, "Закрыть", self.close, ghost=True).pack(side="right")

        footer = tk.Frame(root, bg=BG)
        footer.grid(row=8, column=0, sticky="ew", padx=outer, pady=(4, 12))
        footer.columnconfigure(1, weight=1)
        tk.Label(
            footer,
            text="●",
            bg=BG,
            fg=OK,
            font=(FONT, 8),
        ).grid(row=0, column=0, sticky="w", padx=(0, 7))
        make_label(
            footer,
            textvariable=self.status_var,
            fg=DIM,
            font=(FONT, 8),
        ).grid(row=0, column=1, sticky="w")
        self.update_chip = FlatButton(footer, "", self._open_update_page, small=True, accent=True)

    def _load_logo(self, parent: tk.Misc, size: int) -> tk.Label:
        try:
            image = tk.PhotoImage(file=str(self.services.paths.icon_png))
            factor = max(1, round(image.width() / size))
            if factor > 1:
                image = image.subsample(factor, factor)
            label = tk.Label(parent, image=image, bg=BG, bd=0, highlightthickness=0)
            label.image = image  # type: ignore[attr-defined]
            return label
        except tk.TclError:
            return tk.Label(
                parent,
                text="G",
                bg=ACCENT_SOFT,
                fg=ACCENT,
                font=(FONT, 18, "bold"),
                padx=8,
                pady=2,
            )

    def _build_view_menu(self) -> None:
        self._view_menu = tk.Menu(
            self.root,
            tearoff=0,
            bg=PANEL,
            fg=TEXT,
            activebackground=ACCENT_SOFT,
            activeforeground=TEXT,
            bd=0,
            relief="flat",
            font=(FONT, 9),
        )
        self._view_menu.add_command(label="Компактный  470 × 660", command=lambda: self._set_size(470, 660))
        self._view_menu.add_command(label="Обычный  600 × 830", command=lambda: self._set_size(600, 830))
        self._view_menu.add_command(label="Широкий  800 × 860", command=lambda: self._set_size(800, 860))
        self._view_menu.add_separator()
        self._view_menu.add_command(label="Запомнить текущий размер", command=self._save_geometry)

    def _post_view_menu(self) -> None:
        button = self._view_button
        x = button.winfo_rootx()
        y = button.winfo_rooty() + button.winfo_height() + 4
        try:
            self._view_menu.tk_popup(x, y)
        finally:
            try:
                self._view_menu.grab_release()
            except tk.TclError:
                pass

    def _ensure_visible(self) -> None:
        try:
            self.root.update_idletasks()
            width = self.root.winfo_width()
            height = self.root.winfo_height()
            x = self.root.winfo_x()
            y = self.root.winfo_y()
            screen_w = self.root.winfo_screenwidth()
            screen_h = self.root.winfo_screenheight()
            if x < -width + 100 or x > screen_w - 100 or y < -40 or y > screen_h - 100:
                x = max(20, (screen_w - width) // 2)
                y = max(20, (screen_h - height) // 3)
                self.root.geometry(f"+{x}+{y}")
        except tk.TclError:
            pass

    def _set_size(self, width: int, height: int) -> None:
        self.root.geometry(f"{width}x{height}")
        self._save_geometry()

    # ------------------------------------------------------------------- sites

    def _refresh_sites(self) -> None:
        for child in self.sites_inner.winfo_children():
            child.destroy()
        self._pin_vars.clear()
        self._site_status.clear()
        self._site_grace.clear()
        self._site_errors.clear()

        enabled = [site for site in self.config.sites if site.enabled]
        if not enabled:
            make_label(
                self.sites_inner,
                "Нет включённых сайтов. Добавьте их в настройках.",
                bg=PANEL,
                fg=MUTED,
                font=(FONT, 9),
            ).pack(anchor="w", padx=10, pady=14)
            self.sites_scroll.fit_content(max_height=340, min_height=60)
            return

        for index, site in enumerate(enabled):
            if index:
                tk.Frame(self.sites_inner, bg=BORDER, height=1).pack(fill="x", padx=10)
            row = tk.Frame(self.sites_inner, bg=PANEL)
            row.pack(fill="x")
            row.columnconfigure(0, weight=1)

            text = tk.Frame(row, bg=PANEL)
            text.grid(row=0, column=0, sticky="ew", padx=(10, 8), pady=6)
            name = tk.Label(
                text,
                text=site.name,
                bg=PANEL,
                fg=TEXT,
                font=(FONT, 11),
                anchor="w",
            )
            name.pack(anchor="w")
            meta = tk.Frame(text, bg=PANEL)
            meta.pack(fill="x")
            url = tk.Label(
                meta,
                text=site.url.replace("https://", "").rstrip("/"),
                bg=PANEL,
                fg=MUTED,
                font=(FONT, 8),
                anchor="w",
            )
            url.pack(side="left")
            status = tk.Label(meta, text="", bg=PANEL, fg=DIM, font=(FONT, 8))
            status.pack(side="right")
            self._site_status[site.id] = status

            pin_var = tk.BooleanVar(value=site.pinned)
            self._pin_vars[site.id] = pin_var
            pin = Switch(row, text="Поверх", variable=pin_var, command=lambda sid=site.id: self._toggle_pin(sid))
            pin.grid(row=0, column=1, padx=(0, 6))
            FlatButton(row, "Открыть", lambda s=site: self._open_site(s), small=True).grid(
                row=0, column=2, padx=(0, 10), pady=6
            )

            for widget in (row, text, name, url, meta):
                widget.bind("<Double-1>", lambda _e, s=site: self._open_site(s))

        self.sites_scroll.fit_content(max_height=290, min_height=60)

    def _update_meta(self) -> None:
        url = self.services.doh_url
        self.doh_var.set(url or "DoH не настроен · используется системный DNS")
        count = len([site for site in self.config.sites if site.enabled])
        browser = resolve_browser(self.config)
        browser_name = browser.name if browser is not None else "браузер не найден"
        doh_label = "DoH" if url else "DoH не настроен"
        self.subtitle_var.set(f"{doh_label} · {count} {plural_sites(count)} · {browser_name}")
        self._refresh_status()

    def _toggle_topmost(self) -> None:
        value = bool(self.topmost_var.get())
        try:
            self.root.attributes("-topmost", value)
        except tk.TclError:
            return
        self.config.window_topmost = value
        self._save_config()

    def _toggle_pin(self, site_id: str) -> None:
        site = next((item for item in self.config.sites if item.id == site_id), None)
        variable = self._pin_vars.get(site_id)
        if site is None or variable is None:
            return
        site.pinned = bool(variable.get())
        self.services.pins.set_pinned(
            {item.id for item in self.config.sites if item.pinned and item.enabled}
        )
        self._save_config()

    def _open_site(self, site) -> None:
        result = self.services.open_site(site)
        status = self._site_status.get(site.id)
        if result.ok:
            self._site_errors.pop(site.id, None)
            # Give the browser a moment to create its window before the
            # watcher is allowed to hide the indicator again.
            self._site_grace[site.id] = time.monotonic() + 8.0
            if status is not None:
                status.configure(text="● открыто", fg=OK)
        else:
            self._site_grace.pop(site.id, None)
            self._site_errors[site.id] = time.monotonic()
            if status is not None:
                status.configure(text="ошибка", fg=ERROR)
            self.services.logger.warning(result.message)
            messagebox.showerror(APP_NAME, result.message, parent=self.root)

    def _start_status_watch(self) -> None:
        """Keep the green «открыто» badge in sync with real browser windows."""

        def worker() -> None:
            while not self._status_stop.wait(1.5):
                if self._closed:
                    return
                try:
                    active = self.services.launcher.active_sites()
                except Exception as exc:  # pragma: no cover - defensive
                    self.services.logger.warning(
                        f"Статус окон: {type(exc).__name__}: {exc}"
                    )
                    continue
                try:
                    self.root.after(0, lambda sites=active: self._sync_site_status(sites))
                except (tk.TclError, RuntimeError):
                    return

        threading.Thread(target=worker, name="site-status", daemon=True).start()

    def _sync_site_status(self, active: set[str]) -> None:
        if self._closed:
            return
        now = time.monotonic()
        for site_id, label in self._site_status.items():
            try:
                current = str(label.cget("text"))
            except tk.TclError:
                continue
            if site_id in active:
                self._site_errors.pop(site_id, None)
                self._site_grace.pop(site_id, None)
                if current != "● открыто":
                    label.configure(text="● открыто", fg=OK)
                continue
            if self._site_grace.get(site_id, 0.0) > now:
                continue
            self._site_grace.pop(site_id, None)
            error_at = self._site_errors.get(site_id)
            if error_at is not None and now - error_at < 6.0:
                continue
            self._site_errors.pop(site_id, None)
            if current:
                label.configure(text="")

    def _open_all(self) -> None:
        sites = [site for site in self.config.sites if site.enabled]
        if not sites:
            messagebox.showinfo(APP_NAME, "В настройках нет включённых сайтов.", parent=self.root)
            return
        for site in sites:
            self._open_site(site)

    # --------------------------------------------------------------- updates

    def _poll_updates(self) -> None:
        if self._closed:
            return
        if self.config.auto_check_updates and self._due_for_check():
            self._start_update_check(silent=True)

    def _due_for_check(self) -> bool:
        stamp = (self.config.last_update_check or "").strip()
        if not stamp:
            return True
        try:
            last = float(stamp)
        except ValueError:
            return True
        return (time.time() - last) > 86400

    def _start_update_check(self, silent: bool) -> None:
        if self._update_checking:
            return
        self._update_checking = True
        if not silent:
            self.status_var.set("Проверяю обновления…")

        def worker() -> None:
            release = None
            error = ""
            try:
                release = updater.check()
            except updater.UpdateError as exc:
                error = str(exc)
            except Exception as exc:  # pragma: no cover - defensive
                error = f"{type(exc).__name__}: {exc}"
            self.root.after(0, lambda: self._on_update_checked(release, error, silent))

        threading.Thread(target=worker, name="update-check", daemon=True).start()

    def _on_update_checked(self, release, error: str, silent: bool) -> None:
        self._update_checking = False
        self.config.last_update_check = f"{time.time():.0f}"
        self._save_config(quiet=True)
        if release is not None:
            self._update_release = release
            self.update_chip.configure(text=f"Обновление {release.version}")
            self.update_chip.grid(row=0, column=2, sticky="e", padx=(0, 12))
            self._refresh_status()
            self.services.logger.info(f"Доступно обновление {release.version}")
            return
        if error and not silent:
            self.status_var.set(f"Обновления: {error}")
        elif not error and not silent:
            self.status_var.set(f"Установлена последняя версия {__version__}")

    def _open_update_page(self) -> None:
        release = self._update_release
        if release is None or not release.url:
            return
        webbrowser.open(release.url)

    # -------------------------------------------------------------- settings

    def open_settings(self) -> None:
        if self._settings is not None:
            try:
                exists = self._settings.winfo_exists()
            except tk.TclError:
                exists = False
            if exists:
                self._settings.lift()
                return
            self._settings = None
        self._settings = SettingsDialog(self.root, self.services, self._apply_settings)

    def _apply_settings(self, new_config: AppConfig) -> list[str]:
        new_config.window_geometry = self.config.window_geometry
        new_config.window_topmost = bool(self.topmost_var.get())
        new_config.last_update_check = self.config.last_update_check
        warnings = self.services.apply_config(new_config)
        self.config = new_config
        self._save_config()
        self._refresh_sites()
        self._update_meta()
        self.services.logger.info("Настройки сохранены.")
        return warnings

    def _save_geometry(self) -> None:
        try:
            geometry = self.root.geometry()
        except tk.TclError:
            return
        if geometry:
            self.config.window_geometry = geometry
            self._save_config()

    def _save_config(self, quiet: bool = False) -> None:
        try:
            self.services.store.save(self.config)
        except OSError as exc:
            self.services.logger.error("Не удалось сохранить config.json", exc)
            if not quiet:
                messagebox.showerror(
                    APP_NAME,
                    f"Не удалось сохранить config.json:\n{exc}",
                    parent=self.root,
                )

    # ------------------------------------------------------------------- misc

    def _refresh_status(self) -> None:
        if self._update_release is not None:
            self.status_var.set(f"Доступна версия {self._update_release.version}")
        else:
            self.status_var.set(f"прокси 127.0.0.1:{self.services.port}")

    def _flash_status(self, text: str, delay: int = 2600) -> None:
        self.status_var.set(text)
        if self._status_job is not None:
            try:
                self.root.after_cancel(self._status_job)
            except tk.TclError:
                pass
        self._status_job = self.root.after(delay, self._refresh_status)

    def _copy_log(self) -> None:
        text = self.log_text
        if not text.get("1.0", "end-1c").strip():
            self._flash_status("Журнал пока пуст")
            return
        if not text.tag_ranges("sel"):
            text.tag_add("sel", "1.0", "end-1c")
        try:
            text.event_generate("<<Copy>>")
        except tk.TclError:
            pass
        text.tag_remove("sel", "1.0", "end")
        self._flash_status("Журнал скопирован")

    def _append_log(self, line: str) -> None:
        self.log_text.configure(state="normal")
        self.log_text.insert("end", line + "\n")
        lines = int(self.log_text.index("end-1c").split(".")[0])
        if lines > 600:
            self.log_text.delete("1.0", f"{lines - 600}.0")
        self.log_text.see("end")
        self.log_text.configure(state="disabled")

    def _clear_log(self) -> None:
        self.log_text.configure(state="normal")
        self.log_text.delete("1.0", "end")
        self.log_text.configure(state="disabled")

    def _poll_log(self) -> None:
        if self._closed:
            return
        try:
            for _ in range(100):
                self._append_log(self.log_queue.get_nowait())
        except queue.Empty:
            pass
        self.root.after(200, self._poll_log)

    def _report_exception(self, exc_type, exc, _tb) -> None:
        self.services.logger.error("Ошибка интерфейса", exc)
        try:
            messagebox.showerror(
                APP_NAME,
                f"{exc_type.__name__}: {exc}\n\nПодробности в журнале.",
                parent=self.root,
            )
        except tk.TclError:
            pass

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._status_stop.set()
        self._save_geometry()
        self._save_config(quiet=True)
        self.status_var.set("Останавливаю…")
        try:
            self.root.update_idletasks()
        except tk.TclError:
            pass
        try:
            self.services.shutdown(self.config.close_browsers_on_exit)
        except Exception as exc:
            self.services.logger.error("Ошибка при остановке", exc)
        try:
            self.root.destroy()
        except tk.TclError:
            pass

    def run(self) -> None:
        self.root.mainloop()
