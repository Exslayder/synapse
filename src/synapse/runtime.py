"""Runtime services: config, resolver, proxy, browsers, pins."""

from __future__ import annotations

import threading

from .browser import (
    BrowserLauncher,
    LaunchResult,
    PinManager,
    focus_pids,
    process_tree,
    resolve_browser,
)
from .config import AppConfig, ConfigStore, Site
from .doh import DohResolver
from .logging_setup import AppLogger
from .paths import AppPaths
from .proxy import ProxyServer, serve, shutdown_server


class Services:
    def __init__(
        self,
        paths: AppPaths,
        store: ConfigStore,
        config: AppConfig,
        logger: AppLogger,
    ):
        self.paths = paths
        self.store = store
        self.config = config
        self.logger = logger
        self.resolver = DohResolver(config.doh, logger.info)
        self.server: ProxyServer
        self.port: int
        self.server, self.port = serve(
            config.proxy_port,
            self.resolver,
            config.allowed_ports,
            logger.info,
        )
        self.launcher = BrowserLauncher(paths.profiles, logger.info)
        self.pins = PinManager(logger.info, self.launcher.root_pids)
        self.pins.start()
        self.pins.set_pinned(self._pinned_ids(config))
        self._closed = False
        self._lock = threading.Lock()

    @property
    def doh_url(self) -> str:
        return self.resolver.endpoint.url

    def _pinned_ids(self, config: AppConfig) -> set[str]:
        return {site.id for site in config.sites if site.pinned and site.enabled}

    def open_site(self, site: Site) -> LaunchResult:
        if not site.enabled:
            return LaunchResult(False, f"{site.name} отключён в настройках.")
        root = self.launcher.root_pids().get(site.id)
        if root and focus_pids(process_tree(root)):
            return LaunchResult(True, f"{site.name}: окно уже открыто", root)
        return self.launcher.launch(site, self.port, self.config)

    def apply_config(self, new_config: AppConfig) -> list[str]:
        # Validate the endpoint before mutating anything.
        new_config.doh.endpoint()
        warnings: list[str] = []

        if new_config.proxy_port != self.port:
            warnings.append(
                "Порт прокси изменится только после перезапуска программы. "
                f"Сейчас используется {self.port}."
            )

        self.resolver.configure(new_config.doh)
        self.server.allowed_ports = list(new_config.allowed_ports)
        self.pins.set_pinned(self._pinned_ids(new_config))
        self.config = new_config

        browser = resolve_browser(new_config)
        if browser is None:
            warnings.append("Браузер не найден. Укажите путь к Chrome или Firefox в настройках.")
        return warnings

    def shutdown(self, close_browsers: bool) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
        self.logger.info("Останавливаю прокси…")
        self.pins.stop()
        if close_browsers:
            # Only the private browser processes started by Synapse.  The
            # user's own Chrome/Firefox is never touched, even when a window
            # title happens to contain a site name.
            self.launcher.close_all()
        shutdown_server(self.server)
        self.logger.info("Готово.")
