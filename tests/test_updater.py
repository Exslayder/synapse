from __future__ import annotations

import json
import unittest
from unittest import mock

from tests._bootstrap import SRC  # noqa: F401
from synapse import __version__, updater


class VersionTests(unittest.TestCase):
    def test_parse_version_variants(self) -> None:
        self.assertEqual(updater.parse_version("v2.1.0"), (2, 1, 0))
        self.assertEqual(updater.parse_version("release 2.10.3"), (2, 10, 3))
        self.assertEqual(updater.parse_version("weekly-2026.10.28"), (2026, 10, 28))
        self.assertIsNone(updater.parse_version(""))
        self.assertIsNone(updater.parse_version("no digits here"))

    def test_is_newer(self) -> None:
        self.assertTrue(updater.is_newer("2.1.0", "2.0.9"))
        self.assertTrue(updater.is_newer("3.0", "2.99.99"))
        self.assertFalse(updater.is_newer("2.0.0", "2.0.0"))
        self.assertFalse(updater.is_newer("1.9.9", "2.0.0"))
        self.assertFalse(updater.is_newer("garbage", "2.0.0"))

    def test_repo_slug_can_be_overridden(self) -> None:
        with mock.patch.dict("os.environ", {"SYNAPSE_REPO": "someone/else"}):
            self.assertEqual(updater.repo_slug(), "someone/else")
        with mock.patch.dict("os.environ", {"SYNAPSE_REPO": ""}):
            self.assertEqual(updater.repo_slug(), updater.DEFAULT_REPO)


class ReleaseParsingTests(unittest.TestCase):
    PAYLOAD = {
        "tag_name": "v2.3.0",
        "html_url": "https://github.com/exslayder/synapse/releases/tag/v2.3.0",
        "body": "  Notes  ",
        "published_at": "2026-10-01T10:00:00Z",
        "draft": False,
        "prerelease": False,
        "assets": [
            {"name": "Synapse.exe.sha256", "browser_download_url": "https://example/sha"},
            {"name": "Synapse.exe", "browser_download_url": "https://example/exe"},
        ],
    }

    def test_release_from_json(self) -> None:
        release = updater._release_from_json(self.PAYLOAD)
        assert release is not None
        self.assertEqual(release.version, "2.3.0")
        self.assertEqual(release.tag, "v2.3.0")
        self.assertEqual(release.exe_url, "https://example/exe")
        self.assertEqual(release.exe_name, "Synapse.exe")
        self.assertEqual(release.notes, "Notes")

    def test_release_without_version_is_ignored(self) -> None:
        payload = dict(self.PAYLOAD, tag_name="weekly")
        self.assertIsNone(updater._release_from_json(payload))

    def test_release_without_exe(self) -> None:
        payload = dict(self.PAYLOAD, assets=[])
        release = updater._release_from_json(payload)
        assert release is not None
        self.assertEqual(release.exe_url, "")

    @staticmethod
    def _payload(version: str, *, prerelease: bool = False, draft: bool = False) -> dict:
        return {
            "tag_name": f"v{version}",
            "html_url": f"https://example/{version}",
            "body": "",
            "published_at": "",
            "draft": draft,
            "prerelease": prerelease,
            "assets": [{"name": "Synapse.exe", "browser_download_url": "https://example/exe"}],
        }

    def test_latest_prefers_stable_and_skips_drafts(self) -> None:
        payload = json.dumps(
            [
                self._payload("2.5.0", prerelease=True),
                self._payload("2.4.0"),
                self._payload("2.9.0", draft=True),
                self._payload("2.3.0"),
            ]
        ).encode("utf-8")
        with mock.patch.object(updater, "_request", return_value=payload):
            release = updater.latest_release()
        assert release is not None
        self.assertEqual(release.version, "2.4.0")

    def test_latest_falls_back_to_prereleases(self) -> None:
        payload = json.dumps([self._payload("2.5.0", prerelease=True)]).encode("utf-8")
        with mock.patch.object(updater, "_request", return_value=payload):
            release = updater.latest_release()
        assert release is not None
        self.assertEqual(release.version, "2.5.0")

    def test_latest_with_no_releases(self) -> None:
        with mock.patch.object(updater, "_request", return_value=b"[]"):
            self.assertIsNone(updater.latest_release())

    def test_check_reports_only_newer(self) -> None:
        payload = json.dumps([self._payload(__version__)]).encode("utf-8")
        with mock.patch.object(updater, "_request", return_value=payload):
            self.assertIsNone(updater.check())
        payload = json.dumps([self._payload("99.0.0")]).encode("utf-8")
        with mock.patch.object(updater, "_request", return_value=payload):
            release = updater.check()
        assert release is not None
        self.assertEqual(release.version, "99.0.0")


class DownloadTests(unittest.TestCase):
    def test_download_writes_file(self) -> None:
        release = updater.Release(
            version="2.3.0",
            tag="v2.3.0",
            url="https://example/page",
            notes="",
            exe_url="https://example/exe",
            exe_name="Synapse.exe",
            published="",
        )
        response = mock.MagicMock()
        response.read.return_value = b"MZ fake exe"
        response.__enter__.return_value = response
        with mock.patch.object(updater.urllib.request, "urlopen", return_value=response):
            import tempfile
            from pathlib import Path

            with tempfile.TemporaryDirectory() as temp:
                path = updater.download(release, Path(temp) / "updates")
                self.assertTrue(path.exists())
                self.assertEqual(path.read_bytes(), b"MZ fake exe")
                self.assertEqual(path.name, "Synapse.exe")

    def test_download_without_asset_fails(self) -> None:
        from pathlib import Path

        release = updater.Release(
            version="2.3.0",
            tag="v2.3.0",
            url="",
            notes="",
            exe_url="",
            exe_name="",
            published="",
        )
        with self.assertRaises(updater.UpdateError):
            updater.download(release, Path("unused"))


if __name__ == "__main__":
    unittest.main()
