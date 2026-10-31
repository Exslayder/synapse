"""Native Tk widgets styled for the dark theme.

The first version of the redesign drew buttons, cards and toggles on a
``tk.Canvas``.  Tk canvas text is rendered without ClearType and cannot be
antialiased, so the result looked fuzzy and the custom hit areas made focus and
clipboard behaviour unpredictable.  Everything here is built from native
widgets instead: Tk lays out and renders their text itself, hover states are
plain colour changes, and standard copy/paste works as expected.
"""

from __future__ import annotations

import sys
import tkinter as tk
from tkinter import ttk

from .theme import (
    ACCENT,
    ACCENT_DOWN,
    ACCENT_HOVER,
    ACCENT_SOFT,
    ACCENT_TEXT,
    BG,
    BORDER,
    BORDER_HI,
    DIM,
    ERROR,
    FONT,
    MUTED,
    PANEL,
    PANEL_HOVER,
    SELECT_BG,
    SELECT_FG,
    TEXT,
)

# Windows virtual-key codes are layout independent.  Keysym names are not:
# with the Russian layout Tk reports ``Cyrillic_es`` for the physical C key.
_WIN_KEYCODES = {65: "a", 67: "c", 86: "v", 88: "x", 89: "y", 90: "z"}
_CYRILLIC_KEYSYMS = {
    "cyrillic_ef": "a",
    "cyrillic_es": "c",
    "cyrillic_em": "v",
    "cyrillic_che": "x",
    "cyrillic_en": "y",
    "cyrillic_ya": "z",
}
_TEXT_ACTIONS = {"a", "c", "v", "x", "y", "z"}
_SHIFT_MASK = 0x0001


def clipboard_key(event) -> str | None:
    """Map a key press to a text action regardless of keyboard layout."""
    keysym = str(getattr(event, "keysym", "") or "").lower()
    key = _CYRILLIC_KEYSYMS.get(keysym, keysym)
    if key in _TEXT_ACTIONS:
        return key
    if sys.platform == "win32":
        try:
            return _WIN_KEYCODES.get(int(getattr(event, "keycode", 0) or 0))
        except (TypeError, ValueError):
            return None
    return None


def attach_clipboard(widget: tk.Misc, readonly: bool = False) -> None:
    """Layout-independent Ctrl+C/V/X/A/Z/Y for Entry, Text and ttk.Spinbox.

    The widget-level binding runs before Tk's class bindings, performs the
    action once and returns ``break``, so the same keys keep working with the
    Latin layout as well.  Ctrl+Shift+Z is redo, like in most editors.
    """

    events = {
        "c": "<<Copy>>",
        "x": "<<Cut>>",
        "v": "<<Paste>>",
        "z": "<<Undo>>",
        "y": "<<Redo>>",
    }

    def run(_event, action: str) -> str:
        if action in {"x", "v", "z", "y"} and readonly:
            return "break"
        try:
            if action == "a":
                widget.event_generate("<<SelectAll>>")
            else:
                widget.event_generate(events[action])
        except tk.TclError:
            pass
        return "break"

    def on_ctrl(event) -> str | None:
        key = clipboard_key(event)
        if key is None:
            return None
        if key == "z" and int(getattr(event, "state", 0) or 0) & _SHIFT_MASK:
            key = "y"
        return run(event, key)

    widget.bind("<Control-KeyPress>", on_ctrl, add="+")
    widget.bind("<Control-Insert>", lambda event: run(event, "c"), add="+")
    if not readonly:
        widget.bind("<Shift-Insert>", lambda event: run(event, "v"), add="+")
        widget.bind("<Shift-Delete>", lambda event: run(event, "x"), add="+")


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
    """Kept for the few purely decorative shapes (e.g. the status dot)."""
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


def _popup_menu(parent: tk.Misc) -> tk.Menu:
    return tk.Menu(
        parent,
        tearoff=0,
        bg=PANEL,
        fg=TEXT,
        activebackground=ACCENT_SOFT,
        activeforeground=TEXT,
        disabledforeground=DIM,
        bd=0,
        relief="flat",
        font=(FONT, 9),
    )


def _popup(menu: tk.Menu, event) -> None:
    try:
        menu.tk_popup(event.x_root, event.y_root)
    finally:
        try:
            menu.grab_release()
        except tk.TclError:
            pass


class _UndoHistory:
    """Undo/redo for ``tk.Entry`` and ``ttk.Spinbox``.

    Tk 8.6 only has undo inside ``Text``; entries have no built-in history.
    Snapshots are collected through the widget's text variable, and a burst of
    typing is merged into one step after a short idle pause, so Ctrl+Z reverts
    the whole burst instead of a single character.
    """

    MAX_STATES = 200
    IDLE_MS = 700

    def __init__(self, widget: tk.Misc, variable: tk.Variable | None = None):
        self.widget = widget
        self._states: list[tuple[str, int]] = [self._snapshot()]
        self._index = 0
        self._pending: tuple[str, int] | None = None
        self._timer: str | None = None
        self._applying = False
        if variable is not None:
            try:
                variable.trace_add("write", self._on_variable)
            except (tk.TclError, AttributeError):
                pass
        widget.bind("<KeyRelease>", self._on_change, add="+")
        for event in ("<<Paste>>", "<<Cut>>", "<<Clear>>"):
            widget.bind(event, self._on_change, add="+")

    def _snapshot(self) -> tuple[str, int]:
        try:
            text = self.widget.get()
            cursor = int(self.widget.index("insert"))
        except (tk.TclError, ValueError):
            return "", 0
        return text, cursor

    def _on_variable(self, *_args) -> None:
        # The Tcl variable trace fires before the widget repaints with the new
        # text, so the snapshot must be taken on idle, not right now.
        if self._applying:
            return
        try:
            self.widget.after_idle(self._on_change)
        except tk.TclError:
            pass

    def _on_change(self, _event=None) -> None:
        if self._applying:
            return
        snap = self._snapshot()
        if snap == self._pending:
            return
        self._pending = snap
        if self._timer is not None:
            try:
                self.widget.after_cancel(self._timer)
            except tk.TclError:
                pass
        self._timer = self.widget.after(self.IDLE_MS, self._commit)

    def _commit(self) -> None:
        self._timer = None
        snap = self._pending
        self._pending = None
        if snap is None or snap == self._states[self._index]:
            return
        del self._states[self._index + 1:]
        self._states.append(snap)
        if len(self._states) > self.MAX_STATES:
            del self._states[: len(self._states) - self.MAX_STATES]
        self._index = len(self._states) - 1

    def _cancel(self) -> None:
        if self._timer is not None:
            try:
                self.widget.after_cancel(self._timer)
            except tk.TclError:
                pass
            self._timer = None

    def _restore(self, state: tuple[str, int]) -> None:
        text, cursor = state
        self._applying = True
        try:
            self.widget.delete(0, "end")
            self.widget.insert(0, text)
            self.widget.icursor(min(cursor, len(text)))
            self.widget.selection_clear()
        except tk.TclError:
            pass
        finally:
            self._applying = False

    def undo(self) -> str:
        self._cancel()
        if self._pending is not None:
            self._commit()
        if self._index > 0:
            self._index -= 1
            self._restore(self._states[self._index])
        return "break"

    def redo(self) -> str:
        self._cancel()
        if self._pending is not None:
            self._commit()
        if self._index < len(self._states) - 1:
            self._index += 1
            self._restore(self._states[self._index])
        return "break"


def attach_undo(widget: tk.Misc, variable: tk.Variable | None = None) -> _UndoHistory:
    """Install undo/redo on an entry-like widget and wire ``<<Undo>>`` events."""
    history = _UndoHistory(widget, variable)
    widget.bind("<<Undo>>", lambda _event: history.undo(), add="+")
    widget.bind("<<Redo>>", lambda _event: history.redo(), add="+")
    return history


def attach_entry_menu(
    entry: tk.Misc,
    readonly: bool = False,
    variable: tk.Variable | None = None,
) -> None:
    """Right-click menu + clipboard/undo shortcuts for text fields.

    Works for ``tk.Entry``, ``ttk.Entry`` and ``ttk.Spinbox``; the generic
    shortcuts are installed separately so they also survive a non-Latin
    keyboard layout.
    """
    menu = _popup_menu(entry)
    if not readonly:
        attach_undo(entry, variable)

    def copy() -> None:
        try:
            entry.event_generate("<<Copy>>")
        except tk.TclError:
            pass

    def cut() -> None:
        try:
            entry.event_generate("<<Cut>>")
        except tk.TclError:
            pass

    def paste() -> None:
        try:
            entry.event_generate("<<Paste>>")
        except tk.TclError:
            pass

    def select_all() -> None:
        try:
            entry.event_generate("<<SelectAll>>")
        except tk.TclError:
            pass

    def undo() -> None:
        try:
            entry.event_generate("<<Undo>>")
        except tk.TclError:
            pass

    def redo() -> None:
        try:
            entry.event_generate("<<Redo>>")
        except tk.TclError:
            pass

    if not readonly:
        menu.add_command(label="Отменить", accelerator="Ctrl+Z", command=undo)
        menu.add_command(label="Повторить", accelerator="Ctrl+Y", command=redo)
        menu.add_separator()
        menu.add_command(label="Вырезать", command=cut)
    menu.add_command(label="Копировать", command=copy)
    if not readonly:
        menu.add_command(label="Вставить", command=paste)
    menu.add_separator()
    menu.add_command(label="Выделить всё", command=select_all)

    entry.bind("<Button-3>", lambda event: _popup(menu, event), add="+")
    attach_clipboard(entry, readonly=readonly)


def attach_text_menu(text: tk.Text, on_clear=None, readonly: bool = True) -> None:
    """Right-click menu for a Text widget: undo, copy, select all, clear."""
    menu = _popup_menu(text)
    if not readonly:
        try:
            text.configure(undo=True, autoseparators=True, maxundo=-1)
        except tk.TclError:
            pass

    def undo() -> None:
        try:
            text.event_generate("<<Undo>>")
        except tk.TclError:
            pass

    def redo() -> None:
        try:
            text.event_generate("<<Redo>>")
        except tk.TclError:
            pass

    def copy() -> None:
        try:
            text.event_generate("<<Copy>>")
        except tk.TclError:
            pass

    def select_all() -> None:
        try:
            text.event_generate("<<SelectAll>>")
        except tk.TclError:
            pass

    if not readonly:
        menu.add_command(label="Отменить", accelerator="Ctrl+Z", command=undo)
        menu.add_command(label="Повторить", accelerator="Ctrl+Y", command=redo)
        menu.add_separator()
    menu.add_command(label="Копировать", command=copy)
    menu.add_command(label="Выделить всё", command=select_all)
    if on_clear is not None:
        menu.add_separator()
        menu.add_command(label="Очистить журнал", command=on_clear)

    text.bind("<Button-3>", lambda event: _popup(menu, event), add="+")
    attach_clipboard(text, readonly=readonly)


class FlatButton(tk.Button):
    """Flat native button: crisp ClearType text, centred by Tk itself."""

    def __init__(
        self,
        parent: tk.Misc,
        text: str,
        command=None,
        accent: bool = False,
        small: bool = False,
        ghost: bool = False,
        **kwargs,
    ):
        self._accent = bool(accent)
        self._ghost = bool(ghost) and not accent
        self._small = bool(small)
        self._surface = parent_bg(parent)
        self._hover = False
        super().__init__(
            parent,
            text=text,
            command=command,
            bd=0,
            relief="flat",
            highlightthickness=1,
            cursor="hand2",
            font=(FONT, 9, "bold") if small else ((FONT, 10, "bold") if accent else (FONT, 10)),
            padx=10 if small else 14,
            pady=3 if small else 6,
            **kwargs,
        )
        self.bind("<Enter>", self._on_enter, add="+")
        self.bind("<Leave>", self._on_leave, add="+")
        self._sync_colors()

    def _palette(self) -> tuple[str, str, str]:
        if self._accent:
            fill = ACCENT_HOVER if self._hover else ACCENT
            return fill, ACCENT_TEXT, fill
        if self._ghost:
            fill = PANEL_HOVER if self._hover else self._surface
            return fill, TEXT if self._hover else MUTED, BORDER_HI if self._hover else BORDER
        fill = BORDER_HI if self._hover else PANEL_HOVER
        return fill, TEXT, BORDER

    def _sync_colors(self) -> None:
        fill, foreground, outline = self._palette()
        try:
            self.configure(
                bg=fill,
                fg=foreground,
                activebackground=ACCENT_DOWN if self._accent else fill,
                activeforeground=foreground,
                highlightbackground=outline,
                highlightcolor=ACCENT_HOVER if self._accent else ACCENT,
                disabledforeground=DIM,
            )
        except tk.TclError:
            pass

    def _on_enter(self, _event=None) -> None:
        if str(self.cget("state")) == "disabled":
            return
        self._hover = True
        self._sync_colors()

    def _on_leave(self, _event=None) -> None:
        self._hover = False
        self._sync_colors()

    def set_active(self, active: bool) -> None:
        """Used by the segmented tabs: selected = accent, otherwise ghost."""
        self._accent = bool(active)
        self._ghost = not active
        self._hover = False
        self._sync_colors()


class Switch(tk.Checkbutton):
    """Toggle chip with a native label (no canvas, no pixelated text)."""

    def __init__(
        self,
        parent: tk.Misc,
        text: str = "",
        variable: tk.BooleanVar | None = None,
        command=None,
        **kwargs,
    ):
        self.variable = variable if variable is not None else tk.BooleanVar(value=False)
        self._surface = parent_bg(parent)
        super().__init__(
            parent,
            text=text,
            variable=self.variable,
            command=command,
            indicatoron=False,
            bd=0,
            relief="flat",
            offrelief="flat",
            overrelief="flat",
            highlightthickness=1,
            padx=9,
            pady=3,
            font=(FONT, 9),
            cursor="hand2",
            takefocus=True,
            **kwargs,
        )
        self.variable.trace_add("write", lambda *_args: self._sync())
        self.bind("<Enter>", lambda _e: self._hover(True), add="+")
        self.bind("<Leave>", lambda _e: self._hover(False), add="+")
        self._sync()

    def _on(self) -> bool:
        return bool(self.variable.get())

    def _hover(self, state: bool) -> None:
        if str(self.cget("state")) == "disabled":
            return
        if self._on():
            fill = ACCENT_HOVER if state else ACCENT
            self.configure(bg=fill, selectcolor=fill)
        else:
            self.configure(highlightbackground=ACCENT if state else BORDER)

    def _sync(self) -> None:
        if self._on():
            self.configure(
                bg=ACCENT,
                fg=ACCENT_TEXT,
                activebackground=ACCENT_HOVER,
                activeforeground=ACCENT_TEXT,
                selectcolor=ACCENT,
                highlightbackground=ACCENT,
                highlightcolor=ACCENT_HOVER,
                disabledforeground=DIM,
            )
        else:
            self.configure(
                bg=PANEL_HOVER,
                fg=MUTED,
                activebackground=BORDER_HI,
                activeforeground=TEXT,
                selectcolor=PANEL_HOVER,
                highlightbackground=BORDER,
                highlightcolor=ACCENT,
                disabledforeground=DIM,
            )


class Card(tk.Frame):
    """Flat panel with a one-pixel border that hosts widgets in ``body``."""

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
        super().__init__(
            parent,
            bg=fill,
            bd=0,
            highlightthickness=1,
            highlightbackground=border,
            highlightcolor=border,
            **kwargs,
        )
        self.body = tk.Frame(self, bg=fill)
        if fill_height:
            self.body.pack(fill="both", expand=True, padx=padding[0], pady=padding[1])
        else:
            self.body.pack(fill="x", padx=padding[0], pady=padding[1])


class Field(tk.Frame):
    """Text field: a native Entry inside a one-pixel focus frame."""

    def __init__(
        self,
        parent: tk.Misc,
        textvariable: tk.Variable,
        width: int = 32,
        readonly: bool = False,
        **kwargs,
    ):
        super().__init__(
            parent,
            bg=PANEL_HOVER,
            bd=0,
            highlightthickness=1,
            highlightbackground=BORDER,
            highlightcolor=ACCENT,
            **kwargs,
        )
        self.entry = tk.Entry(
            self,
            textvariable=textvariable,
            width=width,
            bg=PANEL_HOVER,
            fg=TEXT,
            disabledbackground=PANEL_HOVER,
            disabledforeground=MUTED,
            readonlybackground=PANEL_HOVER,
            insertbackground=ACCENT,
            selectbackground=SELECT_BG,
            selectforeground=SELECT_FG,
            relief="flat",
            bd=0,
            highlightthickness=0,
            font=(FONT, 10),
            state="readonly" if readonly else "normal",
        )
        self.entry.pack(fill="both", expand=True, padx=9, pady=5)
        self.entry.bind("<FocusIn>", self._on_focus, add="+")
        self.entry.bind("<FocusOut>", self._on_blur, add="+")
        attach_entry_menu(self.entry, readonly=readonly, variable=textvariable)

    def _on_focus(self, _event=None) -> None:
        self.configure(highlightbackground=ACCENT)

    def _on_blur(self, _event=None) -> None:
        self.configure(highlightbackground=BORDER)

    def focus_set(self) -> None:  # pragma: no cover - thin wrapper
        self.entry.focus_set()


class OptionCheck(tk.Frame):
    """Checkbox built from native labels: a crisp square and a check mark.

    ``ttk``'s clam theme paints a cross inside the indicator on Windows, which
    looked broken, while the native ``tk.Checkbutton`` box stays bright white.
    This keeps full control over colours without drawing on a canvas.
    """

    def __init__(
        self,
        parent: tk.Misc,
        text: str,
        variable: tk.Variable,
        command=None,
        **kwargs,
    ):
        self.variable = variable
        self.command = command
        self._surface = parent_bg(parent)
        self._hover = False
        self._focus = False
        super().__init__(
            parent,
            bg=self._surface,
            cursor="hand2",
            takefocus=True,
            highlightthickness=0,
            **kwargs,
        )
        self.box = tk.Label(
            self,
            text="",
            width=2,
            bg=PANEL_HOVER,
            fg=MUTED,
            font=(FONT, 9, "bold"),
            bd=0,
            highlightthickness=1,
            highlightbackground=BORDER,
            highlightcolor=ACCENT,
            padx=0,
            pady=0,
            cursor="hand2",
        )
        self.box.pack(side="left", ipady=1)
        self.label = tk.Label(
            self,
            text=text,
            bg=self._surface,
            fg=TEXT,
            font=(FONT, 10),
            anchor="w",
            cursor="hand2",
        )
        self.label.pack(side="left", padx=(8, 0))
        for widget in (self, self.box, self.label):
            widget.bind("<Button-1>", self._on_click, add="+")
            widget.bind("<Enter>", self._on_enter, add="+")
            widget.bind("<Leave>", self._on_leave, add="+")
        self.bind("<space>", self._on_key, add="+")
        self.bind("<Return>", self._on_key, add="+")
        self.bind("<FocusIn>", self._on_focus, add="+")
        self.bind("<FocusOut>", self._on_blur, add="+")
        self.variable.trace_add("write", lambda *_args: self._sync())
        self._sync()

    def invoke(self) -> None:
        self.variable.set(not bool(self.variable.get()))
        if self.command is not None:
            self.command()

    def _on_click(self, _event=None) -> str:
        self.focus_set()
        self.invoke()
        return "break"

    def _on_key(self, _event=None) -> str:
        self.invoke()
        return "break"

    def _on_enter(self, _event=None) -> None:
        self._hover = True
        self._sync()

    def _on_leave(self, _event=None) -> None:
        self._hover = False
        self._sync()

    def _on_focus(self, _event=None) -> None:
        self._focus = True
        self._sync()

    def _on_blur(self, _event=None) -> None:
        self._focus = False
        self._sync()

    def _sync(self) -> None:
        on = bool(self.variable.get())
        if on:
            border = ACCENT_HOVER if (self._hover or self._focus) else ACCENT
        elif self._hover or self._focus:
            border = ACCENT
        else:
            border = BORDER
        self.box.configure(
            text="\u2713" if on else "",
            bg=ACCENT if on else PANEL_HOVER,
            fg=ACCENT_TEXT if on else MUTED,
            highlightbackground=border,
        )


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
) -> OptionCheck:
    return OptionCheck(parent, text, variable, command)


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


__all__ = [
    "Card",
    "Field",
    "FlatButton",
    "OptionCheck",
    "ScrollFrame",
    "Switch",
    "attach_clipboard",
    "attach_entry_menu",
    "attach_text_menu",
    "clipboard_key",
    "make_checkbutton",
    "make_entry",
    "make_label",
    "parent_bg",
    "rounded_rect",
    "ERROR",
]
