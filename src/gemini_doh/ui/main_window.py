"""Main application window."""

from __future__ import annotations

import queue
import tkinter as tk
from tkinter import messagebox

from .. import APP_NAME, __version__
from ..config import AppConfig
from ..runtime import Services
from .settings import SettingsDialog
from .theme import (
    ACCENT,
    BG,
    BORDER,
    FONT,
    MUTED,
    OK,
    PANEL,
    PANEL_HOVER,
    TEXT,
    setup_ttk,
)
from .widgets import FlatButton, ScrollFrame, make_label


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
        self.status_var = tk.StringVar(value="")
        self.topmost_var = tk.BooleanVar(value=self.config.window_topmost)

        setup_ttk(root)
        self._configure_root()
        self._build()
        self._refresh_sites()
        self._update_meta()
        self._poll_log()

        root.protocol("WM_DELETE_WINDOW", self.close)
        root.report_callback_exception = self._report_exception

    def _configure_root(self) -> None:
        root = self.root
        root.title(f"{APP_NAME} {__version__}")
        root.configure(bg=BG)
        root.minsize(430, 600)
        geometry = self.config.window_geometry or "570x790"
        try:
            root.geometry(geometry)
        except tk.TclError:
            root.geometry("570x790")
        try:
            root.iconbitmap(default=str(self.services.paths.icon))
        except tk.TclError:
            pass
        root.columnconfigure(0, weight=1)
        root.rowconfigure(4, weight=1)
        if self.config.window_topmost:
            root.attributes("-topmost", True)
        root.update_idletasks()
        self._ensure_visible()

    def _build(self) -> None:
        root = self.root
        outer_pad = 22

        header = tk.Frame(root, bg=BG)
        header.grid(row=0, column=0, sticky="ew", padx=outer_pad, pady=(20, 6))
        header.columnconfigure(0, weight=1)

        left = tk.Frame(header, bg=BG)
        left.grid(row=0, column=0, sticky="w")
        make_label(left, "GEMINI", font=(FONT, 23), fg=TEXT).pack(anchor="w")
        make_label(
            left,
            "Окно браузера · DNS-over-HTTPS",
            fg=MUTED,
            font=(FONT, 10),
        ).pack(anchor="w", pady=(1, 0))

        right = tk.Frame(header, bg=BG)
        right.grid(row=0, column=1, sticky="e")
        tk.Checkbutton(
            right,
            text="Поверх всех",
            variable=self.topmost_var,
            command=self._toggle_topmost,
            bg=BG,
            fg=MUTED,
            activebackground=BG,
            activeforeground=TEXT,
            selectcolor=PANEL,
            highlightthickness=0,
            bd=0,
            font=(FONT, 9),
            cursor="hand2",
        ).pack(side="left", padx=(0, 8))
        self._build_view_menu(right)

        sites_head = tk.Frame(root, bg=BG)
        sites_head.grid(row=1, column=0, sticky="ew", padx=outer_pad, pady=(10, 4))
        make_label(sites_head, "САЙТЫ", fg=MUTED, font=(FONT, 8)).pack(side="left")
        make_label(
            sites_head,
            "отдельное окно · свой профиль",
            fg="#777b6b",
            font=(FONT, 8),
        ).pack(side="right")

        self.sites_scroll = ScrollFrame(root, height=312, bg=BG)
        self.sites_scroll.grid(row=2, column=0, sticky="ew", padx=outer_pad)
        self.sites_inner = self.sites_scroll.inner

        doh = tk.Frame(
            root,
            bg=PANEL,
            highlightbackground=BORDER,
            highlightthickness=1,
        )
        doh.grid(row=3, column=0, sticky="new", padx=outer_pad, pady=(12, 8))
        doh.columnconfigure(1, weight=1)
        make_label(doh, "DoH", bg=PANEL, fg=ACCENT, font=(FONT, 8)).grid(
            row=0, column=0, sticky="nw", padx=(12, 8), pady=(9, 9)
        )
        self.doh_label = tk.Label(
            doh,
            text="",
            bg=PANEL,
            fg=TEXT,
            font=("Consolas", 9),
            anchor="w",
            justify="left",
        )
        self.doh_label.grid(row=0, column=1, sticky="ew", pady=(9, 9))
        FlatButton(doh, "Настроить", self.open_settings, small=True).grid(
            row=0, column=2, sticky="e", padx=(8, 8), pady=6
        )

        log_wrap = tk.Frame(root, bg=BG)
        log_wrap.grid(row=4, column=0, sticky="nsew", padx=outer_pad, pady=(4, 8))
        log_wrap.columnconfigure(0, weight=1)
        log_wrap.rowconfigure(1, weight=1)

        log_head = tk.Frame(log_wrap, bg=BG)
        log_head.grid(row=0, column=0, sticky="ew", pady=(0, 4))
        make_label(log_head, "ЖУРНАЛ", fg=MUTED, font=(FONT, 8)).pack(side="left")
        FlatButton(log_head, "Очистить", self._clear_log, small=True).pack(side="right")

        text_frame = tk.Frame(log_wrap, bg=PANEL)
        text_frame.grid(row=1, column=0, sticky="nsew")
        text_frame.columnconfigure(0, weight=1)
        text_frame.rowconfigure(0, weight=1)
        self.log_text = tk.Text(
            text_frame,
            bg=PANEL,
            fg=TEXT,
            insertbackground=TEXT,
            selectbackground="#3a402e",
            relief="flat",
            bd=0,
            highlightthickness=0,
            font=("Consolas", 9),
            height=8,
            wrap="word",
            padx=10,
            pady=8,
            state="disabled",
        )
        self.log_text.grid(row=0, column=0, sticky="nsew")
        scrollbar = tk.Scrollbar(
            text_frame,
            orient="vertical",
            command=self.log_text.yview,
            bg=PANEL,
            troughcolor=BG,
            activebackground=PANEL_HOVER,
            relief="flat",
            bd=0,
            width=12,
        )
        scrollbar.grid(row=0, column=1, sticky="ns")
        self.log_text.configure(yscrollcommand=scrollbar.set)

        buttons = tk.Frame(root, bg=BG)
        buttons.grid(row=5, column=0, sticky="ew", padx=outer_pad, pady=(2, 6))
        FlatButton(buttons, "Открыть все", self._open_all, accent=True).pack(side="left")
        FlatButton(buttons, "Настройки", self.open_settings).pack(side="left", padx=(8, 0))
        FlatButton(buttons, "Закрыть", self.close).pack(side="right")

        footer = tk.Frame(root, bg=BG)
        footer.grid(row=6, column=0, sticky="ew", padx=outer_pad, pady=(0, 14))
        footer.columnconfigure(0, weight=1)
        make_label(
            footer,
            textvariable=self.status_var,
            fg=MUTED,
            font=(FONT, 8),
        ).grid(row=0, column=0, sticky="w")
        config_path = str(self.services.paths.config)
        if len(config_path) > 52:
            config_path = "…" + config_path[-51:]
        make_label(
            footer,
            f"config: {config_path}",
            fg="#777b6b",
            font=(FONT, 8),
        ).grid(row=0, column=1, sticky="e")

    def _build_view_menu(self, parent: tk.Misc) -> None:
        button = tk.Menubutton(
            parent,
            text="Вид",
            bg=PANEL,
            fg=TEXT,
            activebackground=PANEL_HOVER,
            activeforeground=TEXT,
            relief="flat",
            bd=0,
            padx=10,
            pady=5,
            cursor="hand2",
            font=(FONT, 9),
        )
        menu = tk.Menu(
            button,
            tearoff=0,
            bg=PANEL,
            fg=TEXT,
            activebackground=ACCENT,
            activeforeground=BG,
            bd=0,
            font=(FONT, 9),
        )
        menu.add_command(label="Компактный 430×620", command=lambda: self._set_size(430, 620))
        menu.add_command(label="Обычный 570×790", command=lambda: self._set_size(570, 790))
        menu.add_command(label="Широкий 760×820", command=lambda: self._set_size(760, 820))
        menu.add_separator()
        menu.add_command(label="Запомнить текущий размер", command=self._save_geometry)
        button.configure(menu=menu)
        button.pack(side="left")

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
                fg=MUTED,
                font=(FONT, 9),
            ).pack(anchor="w", pady=12)
            self.sites_scroll.fit_content(max_height=340, min_height=96)
            return

        for site in enabled:
            row = tk.Frame(
                self.sites_inner,
                bg=PANEL,
                highlightbackground=BORDER,
                highlightthickness=1,
            )
            row.pack(fill="x", pady=3)
            row.columnconfigure(0, weight=1)

            text = tk.Frame(row, bg=PANEL)
            text.grid(row=0, column=0, sticky="ew", padx=(12, 8), pady=7)
            name = make_label(text, site.name, bg=PANEL, fg=TEXT, font=(FONT, 11))
            name.pack(anchor="w")
            meta = tk.Frame(text, bg=PANEL)
            meta.pack(fill="x")
            url = make_label(
                meta,
                site.url.replace("https://", "").rstrip("/"),
                bg=PANEL,
                fg=MUTED,
                font=(FONT, 8),
            )
            url.pack(side="left")
            status = make_label(meta, "", bg=PANEL, fg="#777b6b", font=(FONT, 8))
            status.pack(side="right")
            self._site_status[site.id] = status

            for widget in (row, text, name, url, meta):
                widget.bind("<Double-1>", lambda _e, s=site: self._open_site(s))

            pin_var = tk.BooleanVar(value=site.pinned)
            self._pin_vars[site.id] = pin_var
            tk.Checkbutton(
                row,
                text="Поверх",
                variable=pin_var,
                command=lambda site_id=site.id: self._toggle_pin(site_id),
                bg=PANEL,
                fg=MUTED,
                activebackground=PANEL,
                activeforeground=TEXT,
                selectcolor=BG,
                highlightthickness=0,
                bd=0,
                font=(FONT, 8),
                cursor="hand2",
            ).grid(row=0, column=1, padx=(0, 6))

            FlatButton(row, "Открыть", lambda s=site: self._open_site(s), small=True).grid(
                row=0, column=2, padx=(0, 8), pady=7
            )

        self.sites_scroll.fit_content(max_height=340)

    def _update_meta(self) -> None:
        url = self.services.doh_url
        if len(url) > 64:
            url = url[:61] + "…"
        self.doh_label.configure(text=url)
        self._update_status()
        self.topmost_var.set(self.config.window_topmost)

    def _update_status(self) -> None:
        count = len([site for site in self.config.sites if site.enabled])
        self.status_var.set(
            f"127.0.0.1:{self.services.port}  ·  сайтов: {count}  ·  v{__version__}"
        )

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
            {
                item.id: item.match_tokens()
                for item in self.config.sites
                if item.pinned and item.enabled
            }
        )
        self._save_config()

    def _open_site(self, site) -> None:
        result = self.services.open_site(site)
        status = self._site_status.get(site.id)
        if result.ok:
            if status is not None:
                status.configure(text="окно открыто", fg=OK)
        else:
            if status is not None:
                status.configure(text="ошибка запуска", fg="#ff8f80")
            self.services.logger.warning(result.message)
            messagebox.showerror(APP_NAME, result.message, parent=self.root)

    def _open_all(self) -> None:
        sites = [site for site in self.config.sites if site.enabled]
        if not sites:
            messagebox.showinfo(APP_NAME, "В настройках нет включённых сайтов.", parent=self.root)
            return
        for site in sites:
            self._open_site(site)

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

    def _save_config(self) -> None:
        try:
            self.services.store.save(self.config)
        except OSError as exc:
            self.services.logger.error("Не удалось сохранить config.json", exc)
            messagebox.showerror(
                APP_NAME,
                f"Не удалось сохранить config.json:\n{exc}",
                parent=self.root,
            )

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
        self._save_config()
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
