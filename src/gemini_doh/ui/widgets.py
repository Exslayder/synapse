"""Small Tk helpers used by the main window and settings dialog."""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk

from .theme import ACCENT, ACCENT_HOVER, BG, BORDER, FONT, MUTED, PANEL, PANEL_HOVER, TEXT


class FlatButton(tk.Button):
    def __init__(
        self,
        parent: tk.Misc,
        text: str,
        command,
        accent: bool = False,
        small: bool = False,
        **kwargs,
    ):
        base_bg = ACCENT if accent else PANEL
        hover_bg = ACCENT_HOVER if accent else PANEL_HOVER
        fg = BG if accent else TEXT
        super().__init__(
            parent,
            text=text,
            command=command,
            bg=base_bg,
            fg=fg,
            activebackground=hover_bg,
            activeforeground=fg,
            relief="flat",
            bd=0,
            highlightthickness=0,
            cursor="hand2",
            font=(FONT, 9 if small else 10),
            padx=10 if small else 14,
            pady=4 if small else 7,
            **kwargs,
        )
        self._base_bg = base_bg
        self._hover_bg = hover_bg
        self.bind("<Enter>", lambda _e: self.configure(bg=self._hover_bg))
        self.bind("<Leave>", lambda _e: self.configure(bg=self._base_bg))


def make_entry(parent: tk.Misc, textvariable: tk.Variable, width: int = 40) -> tk.Entry:
    return tk.Entry(
        parent,
        textvariable=textvariable,
        width=width,
        bg=PANEL,
        fg=TEXT,
        insertbackground=ACCENT,
        disabledbackground=PANEL,
        disabledforeground=MUTED,
        relief="flat",
        highlightthickness=1,
        highlightbackground=BORDER,
        highlightcolor=ACCENT,
        font=(FONT, 10),
    )


def make_label(parent: tk.Misc, text: str = "", **kwargs) -> tk.Label:
    options = {
        "bg": BG,
        "fg": TEXT,
        "font": (FONT, 10),
        "anchor": "w",
        "justify": "left",
    }
    options.update(kwargs)
    return tk.Label(parent, text=text, **options)


def make_checkbutton(parent: tk.Misc, text: str, variable: tk.Variable, command=None) -> tk.Checkbutton:
    return tk.Checkbutton(
        parent,
        text=text,
        variable=variable,
        command=command,
        bg=BG,
        fg=MUTED,
        activebackground=BG,
        activeforeground=TEXT,
        selectcolor=PANEL,
        highlightthickness=0,
        bd=0,
        font=(FONT, 9),
        cursor="hand2",
    )


class ScrollFrame(tk.Frame):
    """Vertically scrollable frame; wheel is bound only while the pointer is inside."""

    def __init__(self, parent: tk.Misc, height: int = 210, bg: str = BG):
        super().__init__(parent, bg=bg)
        self.canvas = tk.Canvas(
            self,
            bg=bg,
            height=height,
            highlightthickness=0,
            bd=0,
        )
        self.scrollbar = ttk.Scrollbar(
            self,
            orient="vertical",
            command=self.canvas.yview,
            style="Vertical.TScrollbar",
        )
        self.canvas.configure(yscrollcommand=self.scrollbar.set)
        self.canvas.pack(side="left", fill="both", expand=True)
        self._scrollbar_visible = False

        self.inner = tk.Frame(self.canvas, bg=bg)
        self._window = self.canvas.create_window((0, 0), window=self.inner, anchor="nw")
        self.inner.bind("<Configure>", self._on_inner_configure)
        self.canvas.bind("<Configure>", self._on_canvas_configure)
        self.canvas.bind("<Enter>", self._bind_wheel)
        self.canvas.bind("<Leave>", self._unbind_wheel)

    def _on_inner_configure(self, _event=None) -> None:
        bbox = self.canvas.bbox("all")
        self.canvas.configure(scrollregion=bbox)
        self._sync_scrollbar(bbox)

    def _on_canvas_configure(self, event) -> None:
        self.canvas.itemconfigure(self._window, width=event.width)
        self._sync_scrollbar(self.canvas.bbox("all"))

    def _sync_scrollbar(self, bbox) -> None:
        """Show the scrollbar only when the content is taller than the viewport."""
        need = bool(bbox) and bbox[3] > self.canvas.winfo_height() + 1
        if need and not self._scrollbar_visible:
            self.scrollbar.pack(side="right", fill="y")
            self._scrollbar_visible = True
        elif not need and self._scrollbar_visible:
            self.scrollbar.pack_forget()
            self._scrollbar_visible = False

    def fit_content(self, max_height: int, min_height: int = 120) -> None:
        """Shrink the viewport to the content, never growing past ``max_height``."""
        self.inner.update_idletasks()
        height = self.inner.winfo_reqheight()
        height = max(min_height, min(height, max_height))
        if int(self.canvas.cget("height")) != height:
            self.canvas.configure(height=height)
        self._sync_scrollbar(self.canvas.bbox("all"))

    def _bind_wheel(self, _event=None) -> None:
        self.canvas.bind_all("<MouseWheel>", self._on_wheel)

    def _unbind_wheel(self, _event=None) -> None:
        self.canvas.unbind_all("<MouseWheel>")

    def _on_wheel(self, event) -> None:
        delta = int(-event.delta / 120 * 3) if event.delta else 0
        if delta:
            self.canvas.yview_scroll(delta, "units")
