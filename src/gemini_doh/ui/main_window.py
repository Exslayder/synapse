"""Main application window."""

from __future__ import annotations

import queue
import threading
import time
import tkinter as tk
import webbrowser
from tkinter import messagebox

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
    PANEL_HOVER,
    TEXT,
    dark_titlebar,
    setup_ttk,
)
from .widgets import Card, FlatButton, ScrollFrame, Switch, make_label, rounded_rect


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
        self._update_release: updater.Release | None = None
        self._update_checking = False
        self.status_var = tk.StringVar(value="")
        self.topmost_var = tk.BooleanVar(value=self.config.window_topmost)

        setup_ttk(root)
        self._configure_root()
        self._build()
        self._refresh_sites()
        self._update_meta()
        self._poll_log()
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
        header.grid(row=0, column=0, sticky="ew", padx=outer, pady=(20, 4))
        header.columnconfigure(1, weight=1)

        self._logo = self._load_logo(header, 44)
        self._logo.grid(row=0, column=0, rowspan=2, sticky="w", padx=(0, 12))

        title_row = tk.Frame(header, bg=BG)
        title_row.grid(row=0, column=1, sticky="sw")
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

        self.subtitle_var = tk.StringVar(value="")
        tk.Label(
            header,
            textvariable=self.subtitle_var,
            bg=BG,
            fg=MUTED,
            font=(FONT, 9),
            anchor="w",
        ).grid(row=1, column=1, sticky="nw", pady=(2, 0))

        right = tk.Frame(header, bg=BG)
        right.grid(row=0, column=2, rowspan=2, sticky="e")
        Switch(right, text="Поверх", variable=self.topmost_var, command=self._toggle_topmost).pack(
            side="left", padx=(0, 12)
        )
        self._view_button = FlatButton(right, "Вид", self._post_view_menu, small=True)
        self._view_button.pack(side="left")
        self._build_view_menu()

        sites_head = tk.Frame(root, bg=BG)
        sites_head.grid(row=1, column=0, sticky="ew", padx=outer, pady=(16, 6))
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

        doh_card = Card(root, padding=(16, 10), radius=16)
        doh_card.grid(row=3, column=0, sticky="ew", padx=outer, pady=(12, 0))
        doh_card.body.columnconfigure(1, weight=1)
        make_label(
            doh_card.body,
            "DoH",
            bg=PANEL,
            fg=ACCENT,
            font=(FONT, 8, "bold"),
        ).grid(row=0, column=0, sticky="w", padx=(0, 10))
        self.doh_label = tk.Label(
            doh_card.body,
            text="",
            bg=PANEL,
            fg=TEXT,
            font=(MONO, 9),
            anchor="w",
            justify="left",
        )
        self.doh_label.grid(row=0, column=1, sticky="ew")
        FlatButton(doh_card.body, "Изменить", self.open_settings, small=True, ghost=True).grid(
            row=0, column=2, sticky="e", padx=(10, 0)
        )

        log_head = tk.Frame(root, bg=BG)
        log_head.grid(row=4, column=0, sticky="new", padx=outer, pady=(16, 6))
        log_head.columnconfigure(0, weight=1)
        make_label(log_head, "ЖУРНАЛ", fg=MUTED, font=(FONT, 8, "bold")).grid(row=0, column=0, sticky="w")
        FlatButton(log_head, "Очистить", self._clear_log, small=True, ghost=True).grid(
            row=0, column=1, sticky="e"
        )

        log_card = Card(root, padding=(12, 10), radius=16, fill_height=True)
        log_card.grid(row=5, column=0, sticky="nsew", padx=outer)
        root.rowconfigure(5, weight=1)
        log_card.body.columnconfigure(0, weight=1)
        log_card.body.rowconfigure(0, weight=1)
        self.log_text = tk.Text(
            log_card.body,
            bg=PANEL,
            fg=MUTED,
            insertbackground=TEXT,
            selectbackground=ACCENT_SOFT,
            selectforeground=TEXT,
            relief="flat",
            bd=0,
            highlightthickness=0,
            font=(MONO, 9),
            height=8,
            wrap="word",
            padx=2,
            pady=2,
            state="disabled",
        )
        self.log_text.grid(row=0, column=0, sticky="nsew")
        scrollbar = tk.Scrollbar(
            log_card.body,
            orient="vertical",
            command=self.log_text.yview,
            bg=PANEL,
            troughcolor=PANEL,
            activebackground=BORDER,
            relief="flat",
            bd=0,
            width=10,
        )
        scrollbar.grid(row=0, column=1, sticky="ns")
        self.log_text.configure(yscrollcommand=scrollbar.set)

        buttons = tk.Frame(root, bg=BG)
        buttons.grid(row=6, column=0, sticky="ew", padx=outer, pady=(16, 6))
        FlatButton(buttons, "Открыть все", self._open_all, accent=True).pack(side="left")
        FlatButton(buttons, "Настройки", self.open_settings).pack(side="left", padx=(10, 0))
        FlatButton(buttons, "Закрыть", self.close, ghost=True).pack(side="right")

        footer = tk.Frame(root, bg=BG)
        footer.grid(row=7, column=0, sticky="ew", padx=outer, pady=(6, 16))
        footer.columnconfigure(1, weight=1)
        self._status_dot = tk.Canvas(footer, width=8, height=8, bg=BG, highlightthickness=0, bd=0)
        self._status_dot.grid(row=0, column=0, sticky="w", padx=(0, 7))
        rounded_rect(self._status_dot, 1, 1, 7, 7, 3, fill=OK, outline=OK)
        make_label(
            footer,
            textvariable=self.status_var,
            fg=DIM,
            font=(FONT, 8),
        ).grid(row=0, column=1, sticky="w")
        self.update_chip = FlatButton(footer, "", self._open_update_page, small=True, accent=True)
        self.config_label = make_label(footer, "", fg=DIM, font=(FONT, 8), anchor="e")
        self.config_label.grid(row=0, column=3, sticky="e")

    def _load_logo(self, parent: tk.Misc, size: int) -> tk.Canvas:
        canvas = tk.Canvas(parent, width=size, height=size, bg=BG, highlightthickness=0, bd=0)
        rounded_rect(canvas, 0, 0, size, size, 12, fill=ACCENT_SOFT, outline=BORDER)
        try:
            image = tk.PhotoImage(file=str(self.services.paths.icon_png))
            factor = max(1, image.width() // (size - 8))
            if factor > 1:
                image = image.subsample(factor, factor)
            canvas.image = image  # type: ignore[attr-defined]
            canvas.create_image(size // 2, size // 2, image=image)
        except tk.TclError:
            canvas.create_text(
                size // 2,
                size // 2,
                text="G",
                fill=ACCENT,
                font=(FONT, 18, "bold"),
            )
        return canvas

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
            text.grid(row=0, column=0, sticky="ew", padx=(10, 8), pady=8)
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
                row=0, column=2, padx=(0, 10), pady=8
            )

            for widget in (row, text, name, url, meta):
                widget.bind("<Double-1>", lambda _e, s=site: self._open_site(s))
                widget.bind("<Enter>", lambda _e, r=row: self._tint_row(r, True))
                widget.bind("<Leave>", lambda _e, r=row: self._tint_row(r, False))

        self.sites_scroll.fit_content(max_height=340, min_height=60)

    def _tint_row(self, row: tk.Frame, hover: bool) -> None:
        color = PANEL_HOVER if hover else PANEL
        for child in row.winfo_children():
            try:
                child.configure(bg=color)
            except tk.TclError:
                pass
            for sub in child.winfo_children():
                try:
                    sub.configure(bg=color)
                except tk.TclError:
                    pass

    def _update_meta(self) -> None:
        url = self.services.doh_url
        if len(url) > 62:
            url = url[:59] + "…"
        self.doh_label.configure(text=url)
        count = len([site for site in self.config.sites if site.enabled])
        browser = resolve_browser(self.config)
        browser_name = browser.name if browser is not None else "браузер не найден"
        self.subtitle_var.set(f"DNS-over-HTTPS · {count} сайтов · {browser_name}")
        self.status_var.set(f"127.0.0.1:{self.services.port} · профили сохранены")
        config_path = str(self.services.paths.config)
        if len(config_path) > 46:
            config_path = "…" + config_path[-45:]
        self.config_label.configure(text=f"config: {config_path}")

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
            if status is not None:
                status.configure(text="открыто", fg=OK)
        else:
            if status is not None:
                status.configure(text="ошибка", fg=ERROR)
            self.services.logger.warning(result.message)
            messagebox.showerror(APP_NAME, result.message, parent=self.root)

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
            self.status_var.set(f"Доступна версия {release.version}")
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
