"""Shared dark theme."""

from __future__ import annotations

import ctypes
import sys
import tkinter as tk
from tkinter import ttk

BG = "#0c0e14"
BG_ALT = "#10131b"
PANEL = "#151924"
PANEL_HOVER = "#1c2130"
BORDER = "#242b3b"
BORDER_HI = "#33405a"
TEXT = "#e9edf7"
MUTED = "#8d97ad"
DIM = "#616b80"
ACCENT = "#8f9dff"
ACCENT_HOVER = "#a9b6ff"
ACCENT_DOWN = "#7b89f0"
ACCENT_TEXT = "#0b0d14"
ACCENT_SOFT = "#1b2140"
OK = "#5fdba7"
ERROR = "#ff8b8b"
WARN = "#ffce6b"
FONT = "Segoe UI"
MONO = "Consolas"


def dark_titlebar(window: tk.Misc) -> None:
    """Ask DWM for a dark title bar (Windows 10 1809+); silently ignore failures."""
    if sys.platform != "win32":
        return
    try:
        window.update_idletasks()
        hwnd = int(window.winfo_id())
        parent = int(ctypes.windll.user32.GetParent(hwnd))
        if parent:
            hwnd = parent
        enabled = ctypes.c_int(1)
        for attribute in (20, 19):  # DWMWA_USE_IMMERSIVE_DARK_MODE, older builds
            result = ctypes.windll.dwmapi.DwmSetWindowAttribute(
                hwnd,
                attribute,
                ctypes.byref(enabled),
                ctypes.sizeof(enabled),
            )
            if result == 0:
                break
    except Exception:
        pass


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
        fieldbackground=PANEL_HOVER,
        background=PANEL_HOVER,
        foreground=TEXT,
        arrowcolor=MUTED,
        bordercolor=BORDER,
        lightcolor=BORDER,
        darkcolor=BORDER,
        padding=7,
        relief="flat",
        selectbackground=PANEL_HOVER,
        selectforeground=TEXT,
    )
    style.map(
        "TCombobox",
        fieldbackground=[("readonly", PANEL_HOVER), ("focus", PANEL_HOVER)],
        background=[("active", PANEL_HOVER)],
        foreground=[("readonly", TEXT)],
        selectbackground=[("readonly", PANEL_HOVER)],
        selectforeground=[("readonly", TEXT)],
        bordercolor=[("focus", ACCENT_HOVER)],
    )
    root.option_add("*TCombobox*Listbox.background", PANEL)
    root.option_add("*TCombobox*Listbox.foreground", TEXT)
    root.option_add("*TCombobox*Listbox.selectBackground", ACCENT)
    root.option_add("*TCombobox*Listbox.selectForeground", ACCENT_TEXT)
    root.option_add("*TCombobox*Listbox.borderWidth", 0)

    style.configure(
        "Treeview",
        background=PANEL,
        fieldbackground=PANEL,
        foreground=TEXT,
        bordercolor=BORDER,
        borderwidth=0,
        rowheight=30,
        font=(FONT, 10),
    )
    style.map(
        "Treeview",
        background=[("selected", ACCENT_SOFT)],
        foreground=[("selected", TEXT)],
    )
    style.configure(
        "Treeview.Heading",
        background=PANEL,
        foreground=MUTED,
        relief="flat",
        font=(FONT, 9),
        padding=(8, 8),
    )
    style.map("Treeview.Heading", background=[("active", PANEL_HOVER)])
    style.configure(
        "Vertical.TScrollbar",
        background=BORDER_HI,
        troughcolor=BG,
        bordercolor=BG,
        arrowcolor=BG,
        lightcolor=BORDER,
        darkcolor=BORDER,
        arrowsize=1,
        width=10,
        relief="flat",
        borderwidth=0,
    )
    style.map("Vertical.TScrollbar", background=[("active", BORDER_HI)])
    style.configure(
        "Log.Vertical.TScrollbar",
        background=BORDER_HI,
        troughcolor=PANEL,
        bordercolor=PANEL,
        arrowcolor=PANEL,
        lightcolor=BORDER,
        darkcolor=BORDER,
        arrowsize=1,
        width=8,
        relief="flat",
        borderwidth=0,
    )
    style.map("Log.Vertical.TScrollbar", background=[("active", BORDER_HI)])
    style.configure(
        "TSpinbox",
        fieldbackground=PANEL_HOVER,
        background=PANEL_HOVER,
        foreground=TEXT,
        arrowcolor=MUTED,
        bordercolor=BORDER,
        lightcolor=PANEL_HOVER,
        darkcolor=PANEL_HOVER,
        selectbackground=ACCENT_SOFT,
        selectforeground=TEXT,
        padding=(9, 5),
        relief="flat",
        borderwidth=0,
        arrowsize=11,
    )
    style.map(
        "TSpinbox",
        fieldbackground=[("readonly", PANEL_HOVER), ("focus", PANEL_HOVER)],
        foreground=[("disabled", DIM)],
        bordercolor=[("focus", ACCENT)],
        arrowcolor=[("active", ACCENT)],
    )
    style.configure("TSeparator", background=BORDER)
    return style
