"""Application entry point."""

from __future__ import annotations

import argparse
import ctypes
import os
import queue
import socket
import ssl
import sys
import threading
import time

from . import APP_NAME, __version__
from .browser import focus_window
from .config import AppConfig, ConfigStore
from .doh import DohResolver
from .http_client import read_http_response
from .logging_setup import AppLogger
from .paths import AppPaths
from .runtime import Services


def configure_dpi() -> None:
    if sys.platform != "win32":
        return
    try:
        ctypes.windll.shcore.SetProcessDpiAwarenessContext(-4)
        return
    except Exception:
        pass
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        pass


def pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if sys.platform == "win32":
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel32.OpenProcess.restype = wintypes.HANDLE
        kernel32.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
        kernel32.GetExitCodeProcess.restype = wintypes.BOOL
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel32.CloseHandle.restype = wintypes.BOOL

        handle = kernel32.OpenProcess(0x1000, False, pid)
        if not handle:
            return False
        try:
            code = wintypes.DWORD()
            ok = kernel32.GetExitCodeProcess(handle, ctypes.byref(code))
            return bool(ok) and code.value == 259  # STILL_ACTIVE
        finally:
            kernel32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def acquire_lock(path) -> bool:
    if path.exists():
        try:
            pid = int(path.read_text(encoding="utf-8").strip().splitlines()[0] or "0")
        except (OSError, ValueError, IndexError):
            pid = 0
        if pid_alive(pid):
            return False
    try:
        path.write_text(f"{os.getpid()}\n{time.time():.0f}\n", encoding="utf-8")
    except OSError:
        pass
    return True


def release_lock(path) -> None:
    try:
        path.unlink()
    except OSError:
        pass


def fatal(message: str, logger: AppLogger | None = None) -> None:
    if logger is not None:
        logger.error(message)
    if sys.platform == "win32":
        try:
            ctypes.windll.user32.MessageBoxW(None, message, APP_NAME, 0x10)
            return
        except Exception:
            pass
    print(message, file=sys.stderr)


def run_check(config: AppConfig, logger: AppLogger) -> int:
    started = time.monotonic()
    print(f"config={config.doh.url}")
    print(f"bootstrap={','.join(config.doh.bootstrap_ips) or 'system'}")
    try:
        resolver = DohResolver(config.doh, logger.info)
        ips, via = resolver.resolve_detailed("gemini.google.com", use_cache=False)
    except Exception as exc:
        print(f"dns=fail {exc}")
        return 1
    print(f"via={via}")
    print("gemini.google.com=" + ",".join(ips))
    if not ips:
        return 1

    raw = None
    try:
        raw = socket.create_connection((ips[0], 443), timeout=10)
        tls = ssl.create_default_context().wrap_socket(raw, server_hostname="gemini.google.com")
        try:
            tls.sendall(
                b"GET / HTTP/1.1\r\n"
                b"Host: gemini.google.com\r\n"
                b"User-Agent: GeminiDoH-check\r\n"
                b"Connection: close\r\n\r\n"
            )
            response = read_http_response(tls)
        finally:
            tls.close()
    except Exception as exc:
        print(f"http=fail {exc}")
        return 1
    finally:
        if raw is not None:
            try:
                raw.close()
            except OSError:
                pass

    elapsed = time.monotonic() - started
    print(f"http={response.status}")
    print(f"elapsed={elapsed:.2f}s")
    return 0 if 200 <= response.status < 400 else 1


def _install_exception_hooks(logger: AppLogger) -> None:
    def main_hook(exc_type, exc, _tb):
        logger.error("Необработанная ошибка", exc)
        fatal(f"Неожиданная ошибка:\n{exc_type.__name__}: {exc}\n\nЖурнал: {logger.path}", logger)

    def thread_hook(args):
        logger.error(
            f"Ошибка в потоке {args.thread.name if args.thread else '?'}",
            args.exc_value,
        )

    sys.excepthook = main_hook
    threading.excepthook = thread_hook


def _show_gui(services: Services, log_queue: queue.Queue[str]) -> int:
    import tkinter as tk

    from .ui.main_window import MainWindow

    root = tk.Tk()
    window = MainWindow(root, services, log_queue)
    window.run()
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="GeminiDoH", description="Gemini DoH browser hub")
    parser.add_argument("--check", action="store_true", help="проверить DNS/TLS и выйти")
    parser.add_argument("--version", action="store_true", help="показать версию")
    parser.add_argument("--config", default=None, help="путь к config.json")
    args = parser.parse_args(argv)

    if args.version:
        print(f"{APP_NAME} {__version__}")
        return 0

    configure_dpi()
    paths = AppPaths.runtime(args.config)
    store = ConfigStore(paths.config)
    config = store.load()

    log_queue: queue.Queue[str] = queue.Queue(maxsize=5000)
    logger = AppLogger(paths.log_file, log_queue)
    logger.info(f"=== {APP_NAME} {__version__} ===")
    logger.info(f"config: {paths.config}")
    logger.info(f"profiles: {paths.profiles}")
    logger.info(f"python: {sys.version.split()[0]}  frozen={getattr(sys, 'frozen', False)}")
    for warning in store.warnings:
        logger.warning(warning)

    _install_exception_hooks(logger)

    if args.check:
        return run_check(config, logger)

    if not acquire_lock(paths.lock):
        if focus_window(APP_NAME):
            return 0
        fatal(
            f"{APP_NAME} уже запущен.\n"
            "Закройте первое окно программы и запустите снова.\n\n"
            f"Журнал: {paths.log_file}",
            logger,
        )
        return 1

    services: Services | None = None
    try:
        services = Services(paths, store, config, logger)
        logger.info(f"proxy: 127.0.0.1:{services.port}")
        logger.info(f"doh: {services.doh_url}")
        return _show_gui(services, log_queue)
    except Exception as exc:
        logger.error("Критическая ошибка", exc)
        fatal(
            f"Не удалось запустить {APP_NAME}.\n{exc}\n\nЖурнал: {paths.log_file}",
            logger,
        )
        return 1
    finally:
        if services is not None:
            try:
                services.shutdown(services.config.close_browsers_on_exit)
            except Exception:
                pass
        release_lock(paths.lock)
