"""Filesystem layout.

The config is deliberately looked up next to the launched script/executable,
as requested.  If that directory is read-only (Program Files, a locked USB
stick, ...), we fall back to LOCALAPPDATA and say so in the log.
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path

APP_DIR_NAME = "Synapse"


def launch_dir() -> Path:
    override = os.environ.get("SYNAPSE_HOME")
    if override:
        return Path(override).expanduser().resolve()

    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent

    module = Path(__file__).resolve()
    # src/synapse/paths.py -> project root
    if module.parent.parent.name == "src":
        return module.parent.parent.parent
    return Path.cwd()


def _writable(path: Path) -> bool:
    probe = path / ".write-test"
    try:
        path.mkdir(parents=True, exist_ok=True)
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
        return True
    except OSError:
        return False


def resource_path(relative: str) -> Path:
    """Resolve a bundled resource in source runs and in a PyInstaller EXE."""
    bundle = getattr(sys, "_MEIPASS", None)
    if bundle:
        return Path(bundle) / relative
    package_local = Path(__file__).resolve().parent / relative
    if package_local.exists():
        return package_local
    module = Path(__file__).resolve()
    if module.parent.parent.name == "src":
        return module.parent.parent.parent / relative
    return Path.cwd() / relative


@dataclass(frozen=True)
class AppPaths:
    root: Path
    config: Path
    profiles: Path
    logs: Path
    lock: Path
    icon: Path
    icon_png: Path

    @property
    def log_file(self) -> Path:
        return self.logs / "synapse.log"

    @classmethod
    def runtime(cls, config_override: str | None = None) -> AppPaths:
        root = launch_dir()
        if not _writable(root):
            base = os.environ.get("LOCALAPPDATA")
            root = Path(base) / APP_DIR_NAME if base else Path.home() / f".{APP_DIR_NAME.lower()}"
            root.mkdir(parents=True, exist_ok=True)

        profiles = root / "profiles"
        logs = root / "logs"
        for folder in (profiles, logs):
            folder.mkdir(parents=True, exist_ok=True)

        config = Path(config_override).expanduser().resolve() if config_override else root / "config.json"
        return cls(
            root=root,
            config=config,
            profiles=profiles,
            logs=logs,
            lock=root / "app.lock",
            icon=resource_path("assets/synapse.ico"),
            icon_png=resource_path("assets/icon.png"),
        )
