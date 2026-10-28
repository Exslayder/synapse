"""Configuration model and JSON persistence.

Everything is intentionally dependency-free.  Unknown/invalid values never
prevent the program from starting: a broken file is backed up and defaults are
written instead.
"""

from __future__ import annotations

import ipaddress
import json
import os
import re
import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

from .net import HOST_RE, is_ip, normalize_host

CONFIG_VERSION = 2
DEFAULT_DOH_URL = "https://dns.dagami.ru/dns-query"
DEFAULT_BOOTSTRAP = ["45.147.31.32"]
ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
GEOMETRY_RE = re.compile(
    r"^(?P<w>\d{2,4})x(?P<h>\d{2,4})(?:(?P<x>[+-]\d{1,5})(?P<y>[+-]\d{1,5})?)?$"
)
TRANSLIT = str.maketrans(
    {
        "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ё": "e",
        "ж": "zh", "з": "z", "и": "i", "й": "y", "к": "k", "л": "l", "м": "m",
        "н": "n", "о": "o", "п": "p", "р": "r", "с": "s", "т": "t", "у": "u",
        "ф": "f", "х": "kh", "ц": "ts", "ч": "ch", "ш": "sh", "щ": "sch",
        "ъ": "", "ы": "y", "ь": "", "э": "e", "ю": "yu", "я": "ya",
    }
)


class ConfigError(ValueError):
    pass


@dataclass(frozen=True)
class DohEndpoint:
    url: str
    host: str
    port: int
    path: str


def parse_doh_url(raw: str) -> DohEndpoint:
    text = (raw or "").strip()
    if not text:
        raise ConfigError("Укажите адрес DoH-сервера.")
    if "://" not in text:
        text = "https://" + text
    try:
        parts = urlsplit(text)
        port = parts.port or 443
    except ValueError as exc:
        raise ConfigError(f"Некорректный адрес DoH: {exc}") from exc

    if parts.scheme.lower() != "https":
        raise ConfigError("DoH должен использовать https://")
    if not parts.hostname:
        raise ConfigError("В адресе DoH нет имени сервера.")
    if parts.username or parts.password:
        raise ConfigError("Адрес DoH не должен содержать логин или пароль.")
    if parts.query or parts.fragment:
        raise ConfigError("Адрес DoH не должен содержать ?параметры или #якорь.")
    if not 1 <= port <= 65535:
        raise ConfigError("Некорректный порт DoH.")

    host = normalize_host(parts.hostname)
    if not is_ip(host):
        try:
            host = host.encode("idna").decode("ascii")
        except UnicodeError as exc:
            raise ConfigError("Некорректное имя DoH-сервера.") from exc
        if not HOST_RE.match(host):
            raise ConfigError("Некорректное имя DoH-сервера.")

    path = parts.path or "/dns-query"
    if path == "/":
        path = "/dns-query"
    if not path.startswith("/"):
        path = "/" + path

    display_host = f"[{host}]" if ":" in host else host
    display = f"https://{display_host}"
    if port != 443:
        display += f":{port}"
    display += path
    return DohEndpoint(url=display, host=host, port=port, path=path)


def _parse_bootstrap(value: object) -> list[str]:
    if value is None:
        return list(DEFAULT_BOOTSTRAP)
    if isinstance(value, str):
        raw_items = re.split(r"[\s,;]+", value.strip())
    elif isinstance(value, (list, tuple)):
        raw_items = [str(item).strip() for item in value]
    else:
        raise ConfigError("Bootstrap IP должен быть строкой или списком.")
    result: list[str] = []
    for item in raw_items:
        if not item:
            continue
        try:
            ip = str(ipaddress.ip_address(item.strip("[]")))
        except ValueError as exc:
            raise ConfigError(f"Некорректный bootstrap IP: {item}") from exc
        if ip not in result:
            result.append(ip)
    return result


@dataclass
class DohSettings:
    url: str = DEFAULT_DOH_URL
    bootstrap_ips: list[str] = field(default_factory=lambda: list(DEFAULT_BOOTSTRAP))
    fallback_tcp: bool = True
    timeout: float = 8.0

    def endpoint(self) -> DohEndpoint:
        return parse_doh_url(self.url)

    def copy(self) -> DohSettings:
        return DohSettings(
            url=self.url,
            bootstrap_ips=list(self.bootstrap_ips),
            fallback_tcp=self.fallback_tcp,
            timeout=self.timeout,
        )

    @classmethod
    def from_dict(cls, data: object) -> DohSettings:
        if not isinstance(data, dict):
            data = {}
        url = str(data.get("url", DEFAULT_DOH_URL)).strip() or DEFAULT_DOH_URL
        endpoint = parse_doh_url(url)
        try:
            timeout = float(data.get("timeout", 8.0))
        except (TypeError, ValueError):
            timeout = 8.0
        timeout = max(2.0, min(30.0, timeout))
        fallback = data.get("fallback_tcp", True)
        return cls(
            url=endpoint.url,
            bootstrap_ips=_parse_bootstrap(data.get("bootstrap_ips", DEFAULT_BOOTSTRAP)),
            fallback_tcp=bool(fallback),
            timeout=timeout,
        )

    def to_dict(self) -> dict:
        return {
            "url": self.url,
            "bootstrap_ips": list(self.bootstrap_ips),
            "fallback_tcp": self.fallback_tcp,
            "timeout": self.timeout,
        }


def slugify(text: str, fallback: str = "site") -> str:
    text = (text or "").strip().lower().translate(TRANSLIT)
    text = re.sub(r"[^a-z0-9]+", "-", text).strip("-")
    return (text[:48] or fallback).strip("-") or fallback


def normalize_site_url(raw: str) -> str:
    text = (raw or "").strip()
    if not text:
        raise ConfigError("Укажите адрес сайта.")
    if any(ch.isspace() for ch in text):
        raise ConfigError("Адрес сайта не должен содержать пробелы.")
    if "://" not in text:
        text = "https://" + text
    try:
        parts = urlsplit(text)
        port = parts.port
    except ValueError as exc:
        raise ConfigError(f"Некорректный адрес сайта: {exc}") from exc
    if parts.scheme.lower() not in ("http", "https"):
        raise ConfigError("Сайт должен открываться по http:// или https://")
    if not parts.hostname:
        raise ConfigError("В адресе сайта нет имени сервера.")
    if parts.username or parts.password:
        raise ConfigError("Адрес сайта не должен содержать логин или пароль.")
    host = normalize_host(parts.hostname)
    if not is_ip(host):
        try:
            host = host.encode("idna").decode("ascii")
        except UnicodeError as exc:
            raise ConfigError("Некорректное имя сайта.") from exc
        if not HOST_RE.match(host):
            raise ConfigError(f"Некорректное имя сайта: {host}")
    display_host = f"[{host}]" if ":" in host else host
    netloc = display_host + (f":{port}" if port else "")
    path = parts.path or ""
    if path == "/":
        path = ""
    return urlunsplit((parts.scheme.lower(), netloc, path, parts.query, ""))


@dataclass
class Site:
    id: str
    name: str
    url: str
    pinned: bool = False
    enabled: bool = True

    @property
    def host(self) -> str:
        try:
            return (urlsplit(self.url).hostname or "").lower()
        except ValueError:
            return ""

    def match_tokens(self) -> list[str]:
        tokens = [self.name, self.host]
        if self.host.startswith("www."):
            tokens.append(self.host[4:])
        return [token.strip().lower() for token in tokens if token and token.strip()]

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "url": self.url,
            "pinned": self.pinned,
            "enabled": self.enabled,
        }

    @classmethod
    def from_dict(cls, data: object, known_ids: set[str] | None = None) -> Site:
        if isinstance(data, str):
            data = {"url": data}
        if not isinstance(data, dict):
            raise ConfigError("Описание сайта должно быть объектом.")
        url = normalize_site_url(str(data.get("url", "")))
        host = normalize_host(urlsplit(url).hostname or "")
        name = str(data.get("name", "")).strip() or host
        raw_id = str(data.get("id", "")).strip().lower()
        site_id = raw_id if ID_RE.match(raw_id) else slugify(name or host, "site")
        known = known_ids if known_ids is not None else set()
        if site_id in known:
            base = site_id
            index = 2
            while f"{base}-{index}" in known:
                index += 1
            site_id = f"{base}-{index}"
        return cls(
            id=site_id,
            name=name[:80],
            url=url,
            pinned=bool(data.get("pinned", False)),
            enabled=bool(data.get("enabled", True)),
        )


def default_sites() -> list[Site]:
    return [
        Site("gemini", "Gemini", "https://gemini.google.com"),
        Site("chatgpt", "ChatGPT", "https://chatgpt.com"),
        Site("claude", "Claude", "https://claude.ai"),
        Site("grok", "Grok", "https://grok.com"),
    ]


@dataclass
class AppConfig:
    version: int = CONFIG_VERSION
    doh: DohSettings = field(default_factory=DohSettings)
    proxy_port: int = 8877
    allowed_ports: list[int] = field(default_factory=lambda: [80, 443])
    sites: list[Site] = field(default_factory=default_sites)
    browser: str = "auto"
    browser_path: str = ""
    browser_width: int = 1280
    browser_height: int = 860
    close_browsers_on_exit: bool = True
    window_geometry: str = ""
    window_topmost: bool = False

    @classmethod
    def default(cls) -> AppConfig:
        return cls()

    def to_dict(self) -> dict:
        return {
            "version": CONFIG_VERSION,
            "doh": self.doh.to_dict(),
            "proxy_port": self.proxy_port,
            "allowed_ports": list(self.allowed_ports),
            "browser": self.browser,
            "browser_path": self.browser_path,
            "browser_width": self.browser_width,
            "browser_height": self.browser_height,
            "close_browsers_on_exit": self.close_browsers_on_exit,
            "window_geometry": self.window_geometry,
            "window_topmost": self.window_topmost,
            "sites": [site.to_dict() for site in self.sites],
        }

    @classmethod
    def from_dict(cls, data: object) -> AppConfig:
        if not isinstance(data, dict):
            raise ConfigError("Корень config.json должен быть объектом.")

        doh = DohSettings.from_dict(data.get("doh", {}))

        try:
            proxy_port = int(data.get("proxy_port", 8877))
        except (TypeError, ValueError):
            proxy_port = 8877
        proxy_port = max(1024, min(65535, proxy_port))

        raw_ports = data.get("allowed_ports", [80, 443])
        ports: list[int] = []
        if isinstance(raw_ports, (list, tuple)):
            for item in raw_ports:
                try:
                    port = int(item)
                except (TypeError, ValueError):
                    continue
                if 1 <= port <= 65535 and port not in ports:
                    ports.append(port)
        if not ports:
            ports = [80, 443]

        sites: list[Site] = []
        raw_sites = data.get("sites")
        if raw_sites is None:
            sites = default_sites()
        elif not isinstance(raw_sites, (list, tuple)):
            raise ConfigError("Поле sites должно быть списком.")
        else:
            known: set[str] = set()
            for item in raw_sites:
                site = Site.from_dict(item, known)
                known.add(site.id)
                sites.append(site)

        geometry = str(data.get("window_geometry", "") or "").strip()
        if geometry and not GEOMETRY_RE.match(geometry):
            geometry = ""

        browser = str(data.get("browser", "auto") or "auto").lower()
        if browser not in {"auto", "edge", "chrome", "brave", "custom"}:
            browser = "auto"

        try:
            width = int(data.get("browser_width", 1280))
        except (TypeError, ValueError):
            width = 1280
        try:
            height = int(data.get("browser_height", 860))
        except (TypeError, ValueError):
            height = 860
        width = max(640, min(3840, width))
        height = max(480, min(2160, height))

        return cls(
            doh=doh,
            proxy_port=proxy_port,
            allowed_ports=ports,
            sites=sites,
            browser=browser,
            browser_path=str(data.get("browser_path", "") or ""),
            browser_width=width,
            browser_height=height,
            close_browsers_on_exit=bool(data.get("close_browsers_on_exit", True)),
            window_geometry=geometry,
            window_topmost=bool(data.get("window_topmost", False)),
        )


class ConfigStore:
    """Load/save config atomically and keep non-fatal warnings for the UI."""

    def __init__(self, path: Path):
        self.path = path
        self.warnings: list[str] = []

    def load(self) -> AppConfig:
        self.warnings = []
        if not self.path.exists():
            config = AppConfig.default()
            self.save(config)
            return config
        try:
            data = json.loads(self.path.read_text(encoding="utf-8-sig"))
            config = AppConfig.from_dict(data)
        except (OSError, json.JSONDecodeError, ConfigError, ValueError) as exc:
            backup = self._backup()
            self.warnings.append(
                f"config.json повреждён ({exc}). Созданы настройки по умолчанию."
                + (f" Резервная копия: {backup}" if backup else "")
            )
            config = AppConfig.default()
            self.save(config)
            return config

        if isinstance(data, dict):
            version = data.get("version")
            if isinstance(version, int) and version > CONFIG_VERSION:
                self.warnings.append(
                    f"config.json создан более новой версией (v{version}); "
                    "неизвестные поля будут проигнорированы."
                )
        return config

    def save(self, config: AppConfig) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(config.to_dict(), ensure_ascii=False, indent=2) + "\n"
        tmp = self.path.with_name(self.path.name + ".tmp")
        tmp.write_text(payload, encoding="utf-8")
        os.replace(tmp, self.path)

    def _backup(self) -> Path | None:
        stamp = time.strftime("%Y%m%d-%H%M%S")
        backup = self.path.with_name(f"config.broken-{stamp}.json")
        try:
            shutil.copy2(self.path, backup)
            return backup
        except OSError:
            return None
