"""File + UI logging."""

from __future__ import annotations

import queue
import threading
import time
import traceback
from pathlib import Path

MAX_LOG_BYTES = 2 * 1024 * 1024


class AppLogger:
    def __init__(self, path: Path, ui_queue: queue.Queue[str] | None = None):
        self.path = path
        self.ui_queue = ui_queue
        self._lock = threading.Lock()

    def info(self, message: str) -> None:
        self._write(message)

    def warning(self, message: str) -> None:
        self._write("! " + message)

    def error(self, message: str, exc: BaseException | None = None) -> None:
        text = "ОШИБКА: " + message
        if exc is not None:
            text += f": {type(exc).__name__}: {exc}"
        self._write(text)
        if exc is not None:
            stack = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
            self._write(stack, stamp=False, raw=True)

    def exception(self, message: str, exc: BaseException) -> None:
        self.error(message, exc)

    def _write(self, message: str, stamp: bool = True, raw: bool = False) -> None:
        text = message.rstrip("\n")
        line = (time.strftime("%Y-%m-%d %H:%M:%S  ") + text) if stamp else text
        with self._lock:
            try:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                if self.path.exists() and self.path.stat().st_size > MAX_LOG_BYTES:
                    backup = self.path.with_suffix(self.path.suffix + ".1")
                    try:
                        self.path.replace(backup)
                    except OSError:
                        pass
                with self.path.open("a", encoding="utf-8") as handle:
                    handle.write(line + "\n")
            except OSError:
                pass
        if self.ui_queue is not None:
            for part in line.splitlines() or [""]:
                try:
                    self.ui_queue.put_nowait(part)
                except queue.Full:
                    pass
