"""Shared dark theme."""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk

BG = "#12140f"
PANEL = "#1c1f17"
PANEL_HOVER = "#262a1f"
BORDER = "#32362b"
TEXT = "#eceadf"
MUTED = "#b2af9f"
ACCENT = "#dff25a"
ACCENT_HOVER = "#eafb84"
ERROR = "#ff8f80"
OK = "#9ee37d"
FONT = "Segoe UI"
MONO = "Consolas"


def setup_ttk(root: tk.Misc) -> ttk.Style:
    style = ttk.Style(root)
    try:
        style.theme_use("clam")
    except tk.TclError:
        pass

    style.configure("TNotebook", background=BG, borderwidth=0, tabmargins=(0, 0, 0, 0))
    style.configure(
        "TNotebook.Tab",
        background=PANEL,
        foreground=MUTED,
        padding=(16, 8),
        borderwidth=0,
        font=(FONT, 10),
    )
    style.map(
        "TNotebook.Tab",
        background=[("selected", BG)],
        foreground=[("selected", ACCENT)],
        expand=[("selected", (0, 0, 0, 0))],
    )
    style.configure(
        "TCombobox",
        fieldbackground=PANEL,
        background=PANEL,
        foreground=TEXT,
        arrowcolor=ACCENT,
        bordercolor=BORDER,
        lightcolor=BORDER,
        darkcolor=BORDER,
        padding=6,
    )
    style.map(
        "TCombobox",
        fieldbackground=[("readonly", PANEL)],
        foreground=[("readonly", TEXT)],
        selectbackground=[("readonly", PANEL)],
        selectforeground=[("readonly", TEXT)],
    )
    root.option_add("*TCombobox*Listbox.background", PANEL)
    root.option_add("*TCombobox*Listbox.foreground", TEXT)
    root.option_add("*TCombobox*Listbox.selectBackground", ACCENT)
    root.option_add("*TCombobox*Listbox.selectForeground", BG)

    style.configure(
        "Treeview",
        background=PANEL,
        fieldbackground=PANEL,
        foreground=TEXT,
        bordercolor=BORDER,
        rowheight=28,
        font=(FONT, 10),
    )
    style.map(
        "Treeview",
        background=[("selected", "#3a402e")],
        foreground=[("selected", TEXT)],
    )
    style.configure(
        "Treeview.Heading",
        background=PANEL_HOVER,
        foreground=MUTED,
        relief="flat",
        font=(FONT, 9),
        padding=(6, 6),
    )
    style.map("Treeview.Heading", background=[("active", PANEL_HOVER)])
    style.configure(
        "Vertical.TScrollbar",
        background=PANEL_HOVER,
        troughcolor=BG,
        bordercolor=BG,
        arrowcolor=MUTED,
        relief="flat",
    )
    style.configure("TSeparator", background=BORDER)
    return style
