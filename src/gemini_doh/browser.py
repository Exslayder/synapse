"""Browser detection, per-site profiles, app windows and Windows topmost pinning."""

from __future__ import annotations

import ctypes
import os
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from collections.abc import Callable

from .config import AppConfig, Site
from .net import BROWSER_EXES

LogFn = Callable[[str], None]

SW_RESTORE = 9
WM_CLOSE = 0x0010
HWND_TOPMOST = -1
HWND_NOTOPMOST = -2
SWP_NOSIZE = 0x0001
SWP_NOMOVE = 0x0002
SWP_NOACTIVATE = 0x0010
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000


@dataclass(frozen=True)
class Browser:
    key: str
    name: str
    path: str


@dataclass
class LaunchResult:
    ok: bool
    message: str
    pid: int = 0


def _existing(paths: list[str]) -> list[str]:
    return [path for path in paths if path and os.path.isfile(path)]


def browser_candidates() -> list[Browser]:
    found: list[Browser] = []
    if sys.platform == "win32":
        roots = [
            os.environ.get("PROGRAMFILES", r"C:\Program Files"),
            os.environ.get("PROGRAMFILES(X86)", r"C:\Program Files (x86)"),
            os.environ.get("LOCALAPPDATA", ""),
        ]
        relative = [
            ("edge", "Microsoft Edge", r"Microsoft\Edge\Application\msedge.exe"),
            ("chrome", "Google Chrome", r"Google\Chrome\Application\chrome.exe"),
            ("brave", "Brave", r"BraveSoftware\Brave-Browser\Application\brave.exe"),
        ]
        seen: set[str] = set()
        for root in roots:
            if not root:
                continue
            for key, name, rel in relative:
                path = os.path.join(root, rel)
                if path in seen or not os.path.isfile(path):
                    continue
                seen.add(path)
                found.append(Browser(key, name, path))
    else:
        candidates = [
            ("chrome", "Google Chrome", "/usr/bin/google-chrome"),
            ("chrome", "Google Chrome", "/usr/bin/google-chrome-stable"),
            ("chromium", "Chromium", "/usr/bin/chromium"),
            ("chromium", "Chromium", "/usr/bin/chromium-browser"),
            ("edge", "Microsoft Edge", "/usr/bin/microsoft-edge"),
            ("edge", "Microsoft Edge", "/usr/bin/microsoft-edge-stable"),
            (
                "chrome",
                "Google Chrome",
                "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
            ),
            (
                "edge",
                "Microsoft Edge",
                "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
            ),
            (
                "brave",
                "Brave",
                "/Applications/Brave Browser.app/Contents/MacOS/Brave Browser",
            ),
        ]
        seen_paths: set[str] = set()
        for key, name, path in candidates:
            if path not in seen_paths and os.path.isfile(path):
                seen_paths.add(path)
                found.append(Browser(key, name, path))
    return found


def resolve_browser(config: AppConfig) -> Browser | None:
    if config.browser == "custom":
        path = config.browser_path.strip()
        if path and os.path.isfile(path):
            return Browser("custom", os.path.basename(path), path)
        return None
    browsers = browser_candidates()
    if not browsers:
        path = config.browser_path.strip()
        if path and os.path.isfile(path):
            return Browser("custom", os.path.basename(path), path)
        return None
    for browser in browsers:
        if browser.key == config.browser:
            return browser
    return browsers[0]


class BrowserLauncher:
    def __init__(self, profiles_dir: Path, log: LogFn):
        self.profiles_dir = profiles_dir
        self.log = log
        self._lock = threading.Lock()
        self._procs: dict[str, subprocess.Popen] = {}

    def profile_dir(self, site: Site) -> Path:
        safe = "".join(ch for ch in site.id if ch.isalnum() or ch in "-_") or "site"
        return self.profiles_dir / safe

    def launch(self, site: Site, proxy_port: int, config: AppConfig) -> LaunchResult:
        browser = resolve_browser(config)
        if browser is None:
            return LaunchResult(
                False,
                "Не найден Edge/Chrome. Укажите путь к браузеру в настройках.",
            )
        profile = self.profile_dir(site)
        try:
            profile.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            return LaunchResult(False, f"Не удалось создать профиль браузера: {exc}")

        args = [
            browser.path,
            f"--app={site.url}",
            f"--proxy-server=http://127.0.0.1:{proxy_port}",
            "--disable-quic",
            f"--user-data-dir={profile}",
            "--no-first-run",
            "--no-default-browser-check",
            "--disable-sync",
            "--disable-background-mode",
            f"--window-size={config.browser_width},{config.browser_height}",
        ]
        try:
            proc = subprocess.Popen(
                args,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                close_fds=True,
            )
        except OSError as exc:
            self.log(f"Браузер  {site.name}: ошибка запуска: {exc}")
            return LaunchResult(False, f"Не удалось запустить браузер: {exc}")

        with self._lock:
            self._procs[site.id] = proc
        self.log(f"Окно  {site.name}  →  {site.url}")
        return LaunchResult(True, f"Открываю {site.name}…", proc.pid)

    def close_all(self) -> None:
        with self._lock:
            procs = list(self._procs.values())
            self._procs.clear()
        for proc in procs:
            if proc.poll() is not None:
                continue
            try:
                proc.terminate()
                proc.wait(timeout=2)
            except (OSError, subprocess.TimeoutExpired):
                if sys.platform == "win32":
                    try:
                        subprocess.run(
                            ["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                            stdin=subprocess.DEVNULL,
                            stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL,
                            timeout=5,
                            check=False,
                        )
                    except (OSError, subprocess.SubprocessError):
                        pass
            except Exception:
                pass

    def pids(self) -> list[int]:
        with self._lock:
            return [proc.pid for proc in self._procs.values() if proc.poll() is None]


# ---------------------------------------------------------------------------
# Windows window management
# ---------------------------------------------------------------------------

if sys.platform == "win32":
    from ctypes import wintypes

    _user32 = ctypes.WinDLL("user32", use_last_error=True)
    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

    _user32.EnumWindows.argtypes = [_WNDENUMPROC, wintypes.LPARAM]
    _user32.EnumWindows.restype = wintypes.BOOL
    _user32.IsWindowVisible.argtypes = [wintypes.HWND]
    _user32.IsWindowVisible.restype = wintypes.BOOL
    _user32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
    _user32.GetWindowTextLengthW.restype = ctypes.c_int
    _user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    _user32.GetWindowTextW.restype = ctypes.c_int
    _user32.GetWindowThreadProcessId.argtypes = [
        wintypes.HWND,
        ctypes.POINTER(wintypes.DWORD),
    ]
    _user32.GetWindowThreadProcessId.restype = wintypes.DWORD
    _user32.SetWindowPos.argtypes = [
        wintypes.HWND,
        wintypes.HWND,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_int,
        wintypes.UINT,
    ]
    _user32.SetWindowPos.restype = wintypes.BOOL
    _user32.PostMessageW.argtypes = [
        wintypes.HWND,
        wintypes.UINT,
        wintypes.WPARAM,
        wintypes.LPARAM,
    ]
    _user32.PostMessageW.restype = wintypes.BOOL
    _user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
    _user32.SetForegroundWindow.argtypes = [wintypes.HWND]

    _kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    _kernel32.OpenProcess.restype = wintypes.HANDLE
    _kernel32.QueryFullProcessImageNameW.argtypes = [
        wintypes.HANDLE,
        wintypes.DWORD,
        wintypes.LPWSTR,
        ctypes.POINTER(wintypes.DWORD),
    ]
    _kernel32.QueryFullProcessImageNameW.restype = wintypes.BOOL
    _kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    _kernel32.CloseHandle.restype = wintypes.BOOL

    _exe_cache: dict[int, tuple[float, str]] = {}
    _exe_lock = threading.Lock()

    def _process_exe(pid: int) -> str:
        now = time.monotonic()
        with _exe_lock:
            cached = _exe_cache.get(pid)
            if cached and now - cached[0] < 60:
                return cached[1]
        handle = _kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not handle:
            return ""
        try:
            size = wintypes.DWORD(1024)
            buffer = ctypes.create_unicode_buffer(size.value)
            ok = _kernel32.QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(size))
            name = os.path.basename(buffer.value).lower() if ok else ""
        finally:
            _kernel32.CloseHandle(handle)
        with _exe_lock:
            _exe_cache[pid] = (now, name)
        return name

    def _all_windows() -> list[tuple[int, str, int, str]]:
        result: list[tuple[int, str, int, str]] = []

        def callback(hwnd, _lparam):
            if not _user32.IsWindowVisible(hwnd):
                return True
            length = _user32.GetWindowTextLengthW(hwnd)
            if length <= 0:
                return True
            title_buf = ctypes.create_unicode_buffer(length + 1)
            _user32.GetWindowTextW(hwnd, title_buf, length + 1)
            title = title_buf.value.strip()
            if not title:
                return True
            pid = wintypes.DWORD()
            _user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            exe = _process_exe(int(pid.value))
            result.append((int(hwnd), title, int(pid.value), exe))
            return True

        _user32.EnumWindows(_WNDENUMPROC(callback), 0)
        return result

    def _windows() -> list[tuple[int, str, int, str]]:
        return [window for window in _all_windows() if window[3] in BROWSER_EXES]

    def _set_topmost(hwnd: int, enabled: bool) -> bool:
        return bool(
            _user32.SetWindowPos(
                wintypes.HWND(hwnd),
                wintypes.HWND(HWND_TOPMOST if enabled else HWND_NOTOPMOST),
                0,
                0,
                0,
                0,
                SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE,
            )
        )

    def focus_window(title_prefix: str) -> bool:
        prefix = title_prefix.lower()
        for hwnd, title, _pid, _exe in _all_windows():
            if title.lower().startswith(prefix):
                _user32.ShowWindow(wintypes.HWND(hwnd), SW_RESTORE)
                _user32.SetForegroundWindow(wintypes.HWND(hwnd))
                return True
        return False

    def close_site_windows(tokens: set[str]) -> int:
        lowered = {token.lower() for token in tokens if token}
        if not lowered:
            return 0
        closed = 0
        for hwnd, title, _pid, _exe in _windows():
            low = title.lower()
            if any(token in low for token in lowered):
                if _user32.PostMessageW(wintypes.HWND(hwnd), WM_CLOSE, 0, 0):
                    closed += 1
        return closed

else:

    def focus_window(_title_prefix: str) -> bool:
        return False

    def close_site_windows(_tokens: set[str]) -> int:
        return 0


class PinManager:
    """Keep windows of pinned sites above other windows (Windows only)."""

    def __init__(self, log: LogFn):
        self.log = log
        self._lock = threading.Lock()
        self._pinned: dict[str, list[str]] = {}
        self._known: dict[int, str] = {}
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if sys.platform != "win32" or self._thread is not None:
            return
        self._thread = threading.Thread(target=self._run, name="window-pins", daemon=True)
        self._thread.start()

    def set_pinned(self, mapping: dict[str, list[str]]) -> None:
        clean: dict[str, list[str]] = {}
        for site_id, tokens in mapping.items():
            values = [token.strip().lower() for token in tokens if token and token.strip()]
            if values:
                clean[site_id] = values
        with self._lock:
            self._pinned = clean
        self._wake.set()

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()
        if self._thread is not None:
            self._thread.join(timeout=2)
            self._thread = None

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                self._apply()
            except Exception as exc:
                self.log(f"Закрепление окна: {type(exc).__name__}: {exc}")
            self._wake.wait(2.0)
            self._wake.clear()

    def _apply(self) -> None:
        with self._lock:
            pinned = {site_id: list(tokens) for site_id, tokens in self._pinned.items()}

        if not pinned:
            for hwnd in list(self._known):
                _set_topmost(hwnd, False)
            self._known.clear()
            return

        matched: dict[int, str] = {}
        for hwnd, title, _pid, _exe in _windows():
            low = title.lower()
            for site_id, tokens in pinned.items():
                if any(token in low for token in tokens):
                    matched[hwnd] = site_id
                    break

        for hwnd, site_id in matched.items():
            if self._known.get(hwnd) != site_id:
                if _set_topmost(hwnd, True):
                    self.log(f"Поверх всех: {site_id}")

        for hwnd in list(self._known):
            if hwnd not in matched:
                _set_topmost(hwnd, False)

        self._known = matched
