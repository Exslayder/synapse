"""GitHub release checks.

The application bundles only the Python standard library, so there are no pip
packages to update at runtime.  The single thing that can become outdated is the
application itself, and that is what this module checks.
"""

from __future__ import annotations

import json
import os
import re
import ssl
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from . import __version__

DEFAULT_REPO = "exslayder/gemini-doh"
API_ROOT = "https://api.github.com"
VERSION_RE = re.compile(r"(\d+)\.(\d+)(?:\.(\d+))?")
USER_AGENT = f"GeminiDoH/{__version__}"


class UpdateError(RuntimeError):
    pass


@dataclass(frozen=True)
class Release:
    version: str
    tag: str
    url: str
    notes: str
    exe_url: str
    exe_name: str
    published: str

    @property
    def page(self) -> str:
        return self.url


def repo_slug() -> str:
    value = os.environ.get("GEMINI_DOH_REPO", "").strip()
    return value or DEFAULT_REPO


def parse_version(text: str) -> tuple[int, int, int] | None:
    if not text:
        return None
    match = VERSION_RE.search(text)
    if not match:
        return None
    major, minor, patch = match.groups()
    return (int(major), int(minor), int(patch or 0))


def is_newer(latest: str, current: str = __version__) -> bool:
    new = parse_version(latest)
    old = parse_version(current)
    if new is None or old is None:
        return False
    return new > old


def _request(url: str, timeout: float) -> bytes:
    request = urllib.request.Request(
        url,
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": USER_AGENT,
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    context = ssl.create_default_context()
    try:
        with urllib.request.urlopen(request, timeout=timeout, context=context) as response:
            return response.read()
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            raise UpdateError("Репозиторий релизов не найден (404).") from exc
        if exc.code == 403:
            raise UpdateError("GitHub ограничил запросы, попробуйте позже.") from exc
        raise UpdateError(f"GitHub ответил кодом {exc.code}.") from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise UpdateError(f"Нет связи с GitHub: {exc}") from exc


def _release_from_json(data: dict) -> Release | None:
    tag = str(data.get("tag_name", "")).strip()
    version = parse_version(tag)
    if version is None:
        return None
    exe_url = ""
    exe_name = ""
    for asset in data.get("assets") or []:
        name = str(asset.get("name", ""))
        if name.lower().endswith(".exe"):
            exe_url = str(asset.get("browser_download_url", ""))
            exe_name = name
            if name.lower() == "geminidoh.exe":
                break
    return Release(
        version=".".join(str(part) for part in version),
        tag=tag,
        url=str(data.get("html_url", "")),
        notes=str(data.get("body", "") or "").strip(),
        exe_url=exe_url,
        exe_name=exe_name,
        published=str(data.get("published_at", "")),
    )


def latest_release(timeout: float = 8.0) -> Release | None:
    """Newest published release, stable preferred over pre-releases."""
    payload = _request(f"{API_ROOT}/repos/{repo_slug()}/releases?per_page=20", timeout)
    try:
        releases = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise UpdateError("GitHub вернул неожиданный ответ.") from exc
    if not isinstance(releases, list):
        raise UpdateError("GitHub вернул неожиданный ответ.")

    parsed: list[tuple[bool, Release]] = []
    for item in releases:
        if not isinstance(item, dict) or item.get("draft"):
            continue
        release = _release_from_json(item)
        if release is None:
            continue
        parsed.append((bool(item.get("prerelease")), release))
    if not parsed:
        return None
    stable = [release for prerelease, release in parsed if not prerelease]
    pool = stable or [release for _prerelease, release in parsed]
    pool.sort(key=lambda release: parse_version(release.version) or (0, 0, 0), reverse=True)
    return pool[0]


def check(current: str = __version__, timeout: float = 8.0) -> Release | None:
    """Return the newest release if it is newer than ``current``."""
    release = latest_release(timeout=timeout)
    if release is None:
        return None
    return release if is_newer(release.version, current) else None


def download(release: Release, target_dir: Path, timeout: float = 120.0) -> Path:
    """Download the release EXE into ``target_dir`` and return its path."""
    if not release.exe_url:
        raise UpdateError("В релизе нет файла .exe.")
    target_dir.mkdir(parents=True, exist_ok=True)
    name = release.exe_name or f"GeminiDoH-{release.version}.exe"
    target = target_dir / name
    request = urllib.request.Request(release.exe_url, headers={"User-Agent": USER_AGENT})
    context = ssl.create_default_context()
    try:
        with urllib.request.urlopen(request, timeout=timeout, context=context) as response:
            payload = response.read()
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise UpdateError(f"Не удалось скачать обновление: {exc}") from exc
    target.write_bytes(payload)
    return target
