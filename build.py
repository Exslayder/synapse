#!/usr/bin/env python3
"""Build a single-file Windows executable with PyInstaller."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "src"))

from gemini_doh import APP_NAME, __version__  # noqa: E402

DIST = ROOT / "dist"
BUILD = ROOT / "build"
ICON = ROOT / "src" / "gemini_doh" / "assets" / "gemini-doh.ico"
VERSION_FILE = BUILD / "version_info.txt"
EXE_NAME = "GeminiDoH"


def version_tuple(version: str) -> tuple[int, int, int, int]:
    numbers = [int(part) for part in re.findall(r"\d+", version)[:4]]
    return tuple((numbers + [0, 0, 0, 0])[:4])  # type: ignore[return-value]


def write_version_info() -> Path:
    major, minor, patch, build = version_tuple(__version__)
    text = f"""# UTF-8
VSVersionInfo(
  ffi=FixedFileInfo(
    filevers=({major}, {minor}, {patch}, {build}),
    prodvers=({major}, {minor}, {patch}, {build}),
    mask=0x3f,
    flags=0x0,
    OS=0x40004,
    fileType=0x1,
    subtype=0x0,
    date=(0, 0)
  ),
  kids=[
    StringFileInfo([
      StringTable(
        '040904B0',
        [StringStruct('CompanyName', '{APP_NAME} contributors'),
         StringStruct('FileDescription', '{APP_NAME} - DNS-over-HTTPS browser hub'),
         StringStruct('FileVersion', '{__version__}'),
         StringStruct('InternalName', '{EXE_NAME}'),
         StringStruct('LegalCopyright', 'MIT License'),
         StringStruct('OriginalFilename', '{EXE_NAME}.exe'),
         StringStruct('ProductName', '{APP_NAME}'),
         StringStruct('ProductVersion', '{__version__}')]
      )
    ]),
    VarFileInfo([VarStruct('Translation', [1033, 1200])])
  ]
)
"""
    BUILD.mkdir(parents=True, exist_ok=True)
    VERSION_FILE.write_text(text, encoding="utf-8")
    return VERSION_FILE


def git_commit() -> str:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--short=12", "HEAD"],
            cwd=ROOT,
            text=True,
            capture_output=True,
            timeout=10,
            check=False,
        )
        return result.stdout.strip() or "unknown"
    except (OSError, subprocess.SubprocessError):
        return "unknown"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def ensure_icon() -> None:
    if ICON.exists():
        return
    print("icon missing, generating…")
    subprocess.run([sys.executable, str(ROOT / "tools" / "make_icon.py")], check=True)


def build(skip_clean: bool = False) -> int:
    try:
        import PyInstaller  # noqa: F401
    except ImportError:
        print(
            "PyInstaller не установлен.\n"
            "Установите сборочные зависимости: python -m pip install -r requirements-build.txt",
            file=sys.stderr,
        )
        return 2

    ensure_icon()
    write_version_info()
    DIST.mkdir(parents=True, exist_ok=True)
    BUILD.mkdir(parents=True, exist_ok=True)

    args = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--noconfirm",
        "--onefile",
        "--windowed",
        "--name",
        EXE_NAME,
        "--icon",
        str(ICON),
        "--version-file",
        str(VERSION_FILE),
        "--paths",
        str(ROOT / "src"),
        "--add-data",
        f"{ICON}{os.pathsep}assets",
        "--distpath",
        str(DIST),
        "--workpath",
        str(BUILD / "pyinstaller"),
        "--specpath",
        str(BUILD),
    ]
    if not skip_clean:
        args.append("--clean")
    args.append(str(ROOT / "run.py"))

    print("$ " + " ".join(args))
    completed = subprocess.run(args, cwd=ROOT, check=False)
    if completed.returncode != 0:
        return completed.returncode

    exe = DIST / f"{EXE_NAME}.exe"
    if not exe.exists():
        print(f"сборка завершилась без {exe}", file=sys.stderr)
        return 1

    checksum = sha256(exe)
    (DIST / f"{EXE_NAME}.exe.sha256").write_text(
        f"{checksum}  {EXE_NAME}.exe\n", encoding="ascii"
    )
    info = {
        "app": APP_NAME,
        "version": __version__,
        "file": exe.name,
        "bytes": exe.stat().st_size,
        "sha256": checksum,
        "built_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "python": platform.python_version(),
        "platform": platform.platform(),
        "pyinstaller": getattr(__import__("PyInstaller"), "__version__", "unknown"),
        "git_commit": git_commit(),
    }
    (DIST / f"{EXE_NAME}-build-info.json").write_text(
        json.dumps(info, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print()
    print(f"EXE:    {exe}")
    print(f"SHA256: {checksum}")
    print(f"INFO:   {DIST / f'{EXE_NAME}-build-info.json'}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build GeminiDoH.exe")
    parser.add_argument("--no-clean", action="store_true", help="не очищать кэш PyInstaller")
    parser.add_argument("--distclean", action="store_true", help="удалить dist/ и build/ перед сборкой")
    args = parser.parse_args(argv)
    if args.distclean:
        for folder in (DIST, BUILD):
            if folder.exists():
                shutil.rmtree(folder, ignore_errors=True)
    return build(skip_clean=args.no_clean)


if __name__ == "__main__":
    raise SystemExit(main())
