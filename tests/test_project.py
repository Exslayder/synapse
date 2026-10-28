from __future__ import annotations

import re
import struct
import unittest
from pathlib import Path

from tests._bootstrap import SRC  # noqa: F401

from gemini_doh import __version__

ROOT = Path(__file__).resolve().parents[1]


class ProjectFilesTests(unittest.TestCase):
    def test_version_is_semver(self) -> None:
        parts = __version__.split(".")
        self.assertEqual(len(parts), 3)
        self.assertTrue(all(part.isdigit() for part in parts))

    def test_package_icon_is_a_multi_size_ico(self) -> None:
        icon = ROOT / "src" / "gemini_doh" / "assets" / "gemini-doh.ico"
        self.assertTrue(icon.is_file())
        data = icon.read_bytes()
        reserved, kind, count = struct.unpack("<HHH", data[:6])
        self.assertEqual((reserved, kind), (0, 1))
        self.assertGreaterEqual(count, 6)
        for index in range(count):
            offset = 6 + index * 16
            size, data_offset = struct.unpack("<II", data[offset + 8 : offset + 16])
            self.assertGreater(size, 0)
            self.assertLessEqual(data_offset + size, len(data))

    def test_docs_icon_is_png(self) -> None:
        icon = ROOT / "assets" / "icon.png"
        self.assertTrue(icon.is_file())
        self.assertEqual(icon.read_bytes()[:8], b"\x89PNG\r\n\x1a\n")

    def test_pyproject_version_matches_package(self) -> None:
        text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
        match = re.search(r'^version\s*=\s*"([^"]+)"', text, re.MULTILINE)
        self.assertIsNotNone(match)
        assert match is not None
        self.assertEqual(match.group(1), __version__)

    def test_ci_and_build_files_exist(self) -> None:
        for relative in (
            ".github/workflows/build.yml",
            ".github/dependabot.yml",
            "build.py",
            "requirements-build.txt",
            "README.md",
            "LICENSE",
            "docs/PUBLISHING.md",
            "assets/screenshot-main.png",
        ):
            self.assertTrue((ROOT / relative).is_file(), relative)


if __name__ == "__main__":
    unittest.main()
