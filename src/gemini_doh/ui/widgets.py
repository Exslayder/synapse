"""Small Tk helpers used by the main window and settings dialog."""

from __future__ import annotations

import tkinter as tk
import tkinter.font as tkfont
from tkinter import ttk

from .theme import (
    ACCENT,
    ACCENT_DOWN,
    ACCENT_HOVER,
    ACCENT_TEXT,
    BG,
    BORDER,
    BORDER_HI,
    DIM,
    FONT,
    MUTED,
    PANEL,
    PANEL_HOVER,
    TEXT,
)


def parent_bg(widget: tk.Misc) -> str:
    for option in ("bg", "background"):
        try:
            value = widget.cget(option)
        except (tk.TclError, AttributeError):
            continue
        if isinstance(value, str) and value:
            return value
    return BG


def rounded_rect(
    canvas: tk.Canvas,
    x1: float,
    y1: float,
    x2: float,
    y2: float,
    radius: float,
    **kwargs,
) -> int:
    """Draw a rounded rectangle as a smoothed polygon."""
    radius = max(0.0, min(radius, (x2 - x1) / 2, (y2 - y1) / 2))
    points = [
        x1 + radius, y1,
        x2 - radius, y1,
        x2, y1,
        x2, y1 + radius,
        x2, y2 - radius,
        x2, y2,
        x2 - radius, y2,
        x1 + radius, y2,
        x1, y2,
        x1, y2 - radius,
        x1, y1 + radius,
        x1, y1,
    ]
    return canvas.create_polygon(points, smooth=True, splinesteps=24, **kwargs)


class FlatButton(tk.Canvas):
    """Rounded button drawn on a canvas (keeps the app dependency-free)."""

    def __init__(
        self,
        parent: tk.Misc,
        text: str,
        command,
        accent: bool = False,
        small: bool = False,
        ghost: bool = False,
        **kwargs,
    ):
        self._text = text
        self._command = command
        self._accent = accent
        self._ghost = ghost
        self._small = small
        self._enabled = True
        self._hover = False
        self._surface = parent_bg(parent)
        height = 30 if small else 38
        self._font = (FONT, 9, "bold") if small else (FONT, 10, "bold" if accent else "normal")
        super().__init__(
            parent,
            height=height,
            bg=self._surface,
            highlightthickness=0,
            bd=0,
            cursor="hand2",
            **{k: v for k, v in kwargs.items() if k not in {"state", "font"}},
        )
        self._resize()
        self.bind("<Enter>", self._on_enter)
        self.bind("<Leave>", self._on_leave)
        self.bind("<ButtonPress-1>", self._on_press)
        self.bind("<ButtonRelease-1>", self._on_release)
        self.bind("<Configure>", lambda _e: self._draw())

    def _palette(self) -> tuple[str, str, str]:
        if not self._enabled:
            return (PANEL_HOVER, DIM, BORDER)
        if self._accent:
            fill = ACCENT_DOWN if self._pressed else (ACCENT_HOVER if self._hover else ACCENT)
            return (fill, ACCENT_TEXT, fill)
        if self._ghost:
            if self._hover:
                return (PANEL_HOVER, TEXT, BORDER_HI)
            return (self._surface, MUTED, BORDER)
        fill = BORDER_HI if self._hover else PANEL_HOVER
        return (fill, TEXT, BORDER)

    @property
    def _pressed(self) -> bool:
        return getattr(self, "_is_pressed", False)

    def _measure(self) -> int:
        font = tkfont.Font(family=self._font[0], size=self._font[1], weight=self._font[2])
        return font.measure(self._text) + (24 if self._small else 36)

    def _resize(self) -> None:
        self.configure(width=self._measure())
        self.after_idle(self._redraw_if_ready)

    def _draw(self) -> None:
        self.delete("all")
        width = max(int(self.winfo_width()), self._measure())
        height = int(self.winfo_height())
        fill, foreground, outline = self._palette()
        radius = 10 if self._small else 12
        rounded_rect(
            self,
            1,
            1,
            width - 1,
            height - 1,
            radius,
            fill=fill,
            outline=outline or fill,
            width=1,
        )
        self.create_text(
            width / 2,
            height / 2 + 1,
            text=self._text,
            fill=foreground,
            font=self._font,
        )

    def _redraw_if_ready(self) -> None:
        if self.winfo_width() > 1:
            self._draw()

    def _on_enter(self, _event=None) -> None:
        if not self._enabled:
            return
        self._hover = True
        self._draw()

    def _on_leave(self, _event=None) -> None:
        self._hover = False
        self._is_pressed = False
        self._draw()

    def _on_press(self, _event=None) -> None:
        if not self._enabled:
            return
        self._is_pressed = True
        self._draw()

    def _on_release(self, event=None) -> None:
        was_pressed = self._pressed
        self._is_pressed = False
        self._draw()
        if not self._enabled or not was_pressed:
            return
        if event is not None and not (0 <= event.x <= self.winfo_width() and 0 <= event.y <= self.winfo_height()):
            return
        if callable(self._command):
            self._command()

    def set_active(self, active: bool) -> None:
        """Switch between the accent (selected) and ghost (idle) look."""
        self._accent = active
        self._ghost = not active
        self._draw()

    def configure(self, cnf=None, **kwargs):
        if cnf:
            kwargs.update(cnf)
        redraw = False
        if "state" in kwargs:
            self._enabled = kwargs.pop("state") != "disabled"
            redraw = True
        if "text" in kwargs:
            self._text = str(kwargs.pop("text"))
            self._resize()
            redraw = True
        if "command" in kwargs:
            self._command = kwargs.pop("command")
        if kwargs:
            super().configure(**kwargs)
        if redraw:
            self.after_idle(self._redraw_if_ready)
        return None

    config = configure

    def cget(self, key):
        if key == "state":
            return "normal" if self._enabled else "disabled"
        return super().cget(key)


class Switch(tk.Canvas):
    """Pill toggle with an optional caption."""

    TRACK_W = 38
    TRACK_H = 21

    def __init__(
        self,
        parent: tk.Misc,
        text: str = "",
        variable: tk.BooleanVar | None = None,
        command=None,
        **kwargs,
    ):
        self.variable = variable if variable is not None else tk.BooleanVar(value=False)
        self._command = command
        self._text = text
        self._surface = parent_bg(parent)
        self._font = (FONT, 9)
        label_width = 0
        if text:
            font = tkfont.Font(family=FONT, size=9)
            label_width = font.measure(text) + 9
        super().__init__(
            parent,
            width=self.TRACK_W + label_width + 2,
            height=24,
            bg=self._surface,
            highlightthickness=0,
            bd=0,
            cursor="hand2",
            takefocus=True,
            **kwargs,
        )
        self.variable.trace_add("write", lambda *_args: self._draw())
        self.bind("<Button-1>", self._toggle)
        self.bind("<space>", self._toggle)
        self.bind("<Configure>", lambda _e: self._draw())

    def _toggle(self, _event=None) -> str:
        self.variable.set(not bool(self.variable.get()))
        if callable(self._command):
            self._command()
        return "break"

    def _draw(self) -> None:
        self.delete("all")
        on = bool(self.variable.get())
        track = ACCENT if on else BORDER_HI
        knob = ACCENT_TEXT if on else MUTED
        x1, y1 = 1, 3
        x2, y2 = 1 + self.TRACK_W, 3 + self.TRACK_H
        rounded_rect(self, x1, y1, x2, y2, self.TRACK_H / 2, fill=track, outline=track)
        radius = (self.TRACK_H - 6) / 2
        cx = (x2 - 4 - radius) if on else (x1 + 4 + radius)
        self.create_oval(
            cx - radius,
            y1 + 3,
            cx + radius,
            y1 + 3 + 2 * radius,
            fill=knob,
            outline=knob,
        )
        if self._text:
            self.create_text(
                x2 + 8,
                3 + self.TRACK_H / 2,
                text=self._text,
                anchor="w",
                fill=TEXT if on else MUTED,
                font=self._font,
            )


class Card(tk.Frame):
    """Rounded panel that hosts arbitrary widgets in ``body``."""

    def __init__(
        self,
        parent: tk.Misc,
        padding: tuple[int, int] = (16, 14),
        radius: int = 16,
        fill: str = PANEL,
        border: str = BORDER,
        surface: str | None = None,
        fill_height: bool = False,
        **kwargs,
    ):
        surface = surface or parent_bg(parent)
        super().__init__(parent, bg=surface, **kwargs)
        self._pad = padding
        self._radius = radius
        self._fill = fill
        self._border = border
        self._fill_height = fill_height
        self.canvas = tk.Canvas(self, bg=surface, highlightthickness=0, bd=0)
        self.canvas.pack(fill="both", expand=True)
        self.body = tk.Frame(self.canvas, bg=fill)
        self._window = self.canvas.create_window(
            padding[0], padding[1], window=self.body, anchor="nw"
        )
        self._shape = None
        self.canvas.bind("<Configure>", self._on_canvas)
        self.body.bind("<Configure>", self._on_body)

    def _on_canvas(self, event) -> None:
        pad_x, pad_y = self._pad
        width = max(1, event.width - 2 * pad_x)
        if self._fill_height:
            height = max(1, event.height - 2 * pad_y)
            self.canvas.itemconfigure(self._window, width=width, height=height)
        else:
            self.canvas.itemconfigure(self._window, width=width)
        self._draw(event.width, event.height)

    def _on_body(self, _event=None) -> None:
        if self._fill_height or self.canvas.winfo_width() <= 1:
            return
        pad_x, pad_y = self._pad
        height = self.body.winfo_reqheight() + 2 * pad_y
        if int(self.canvas.cget("height")) != height:
            self.canvas.configure(height=height)

    def _draw(self, width: int, height: int) -> None:
        self.canvas.delete("card")
        rounded_rect(
            self.canvas,
            1,
            1,
            max(2, width - 1),
            max(2, height - 1),
            self._radius,
            fill=self._fill,
            outline=self._border,
            width=1,
            tags="card",
        )
        self.canvas.tag_lower("card")


class Field(tk.Frame):
    """Rounded text field (the Entry lives inside)."""

    def __init__(self, parent: tk.Misc, textvariable: tk.Variable, width: int = 32, **kwargs):
        surface = parent_bg(parent)
        super().__init__(parent, bg=surface, **kwargs)
        self.canvas = tk.Canvas(self, bg=surface, height=34, highlightthickness=0, bd=0)
        self.canvas.pack(fill="both", expand=True)
        self.entry = tk.Entry(
            self.canvas,
            textvariable=textvariable,
            bg=PANEL_HOVER,
            fg=TEXT,
            disabledbackground=PANEL_HOVER,
            disabledforeground=MUTED,
            insertbackground=ACCENT,
            relief="flat",
            bd=0,
            highlightthickness=0,
            font=(FONT, 10),
        )
        self._window = self.canvas.create_window(11, 17, window=self.entry, anchor="w")
        self.canvas.configure(width=max(120, width * 7))
        self._focused = False
        self.canvas.bind("<Configure>", self._draw)
        self.entry.bind("<FocusIn>", self._on_focus, add="+")
        self.entry.bind("<FocusOut>", self._on_blur, add="+")
        self.canvas.bind("<Button-1>", lambda _e: self.entry.focus_set())

    def _on_focus(self, _event=None) -> None:
        self._focused = True
        self._draw()

    def _on_blur(self, _event=None) -> None:
        self._focused = False
        self._draw()

    def _draw(self, _event=None) -> None:
        self.canvas.delete("field")
        width = max(int(self.canvas.winfo_width()), 60)
        height = int(self.canvas.winfo_height() or 34)
        outline = ACCENT if self._focused else BORDER
        rounded_rect(
            self.canvas,
            1,
            1,
            width - 1,
            height - 1,
            10,
            fill=PANEL_HOVER,
            outline=outline,
            width=1,
            tags="field",
        )
        self.canvas.tag_lower("field")
        self.canvas.coords(self._window, 11, height / 2)
        self.canvas.itemconfigure(self._window, width=max(20, width - 22))

    def focus_set(self) -> None:  # pragma: no cover - thin wrapper
        self.entry.focus_set()


def make_entry(parent: tk.Misc, textvariable: tk.Variable, width: int = 32) -> Field:
    return Field(parent, textvariable, width=width)


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


def make_checkbutton(
    parent: tk.Misc,
    text: str,
    variable: tk.Variable,
    command=None,
) -> Switch:
    return Switch(parent, text=text, variable=variable, command=command)


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
