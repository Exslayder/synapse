from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

from tests._bootstrap import SRC  # noqa: F401
from synapse.browser import (
    Browser,
    browser_kind,
    firefox_install_name,
    firefox_user_js,
    firefox_xulstore,
    process_tree,
    resolve_browser,
)
from synapse.config import AppConfig


class FirefoxProfileTests(unittest.TestCase):
    def test_user_js_sets_proxy_and_skips_first_run(self) -> None:
        text = firefox_user_js(8877)
        self.assertIn('user_pref("network.proxy.type", 1);', text)
        self.assertIn('user_pref("network.proxy.http", "127.0.0.1");', text)
        self.assertIn('user_pref("network.proxy.http_port", 8877);', text)
        self.assertIn('user_pref("network.proxy.ssl_port", 8877);', text)
        # DoH inside Firefox would bypass our proxy, so it must stay off.
        self.assertIn('user_pref("network.trr.mode", 5);', text)
        # The Nightly terms-of-use modal would cover the site window.
        self.assertIn('user_pref("termsofuse.bypassNotification", true);', text)
        self.assertIn('user_pref("browser.aboutwelcome.enabled", false);', text)

    def test_user_js_port_is_not_quoted(self) -> None:
        text = firefox_user_js(12345)
        self.assertIn("12345", text)
        self.assertNotIn('"12345"', text)

    def test_xulstore_is_valid_json(self) -> None:
        payload = json.loads(firefox_xulstore(1280, 860))
        window = payload["chrome://browser/content/browser.xhtml"]["main-window"]
        self.assertEqual(window["width"], "1280")
        self.assertEqual(window["height"], "860")


class InstallNameTests(unittest.TestCase):
    def test_reads_application_ini(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp)
            (folder / "firefox.exe").write_bytes(b"MZ")
            (folder / "application.ini").write_text(
                "[App]\nName=Firefox\nCodeName=Firefox Nightly\nVersion=161.0a1\n",
                encoding="utf-8",
            )
            self.assertEqual(firefox_install_name(str(folder / "firefox.exe")), "Firefox Nightly 161.0a1")

    def test_missing_ini_falls_back(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            exe = Path(temp) / "firefox.exe"
            exe.write_bytes(b"MZ")
            self.assertEqual(firefox_install_name(str(exe)), "Mozilla Firefox")


class ResolveTests(unittest.TestCase):
    def test_browser_kind(self) -> None:
        self.assertEqual(browser_kind(r"C:\Program Files (x86)\Firefox\firefox.exe"), "firefox")
        self.assertEqual(browser_kind(r"C:\Program Files\Google\Chrome\Application\chrome.exe"), "chromium")

    def test_custom_browser_must_exist(self) -> None:
        config = AppConfig(browser="custom", browser_path=r"C:\definitely\missing\firefox.exe")
        self.assertIsNone(resolve_browser(config))

    def test_custom_firefox_is_detected(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            exe = Path(temp) / "firefox.exe"
            exe.write_bytes(b"MZ")
            config = AppConfig(browser="custom", browser_path=str(exe))
            browser = resolve_browser(config)
            assert browser is not None
            self.assertEqual(browser.kind, "firefox")
            self.assertTrue(browser.is_firefox)

    def test_custom_chromium_is_detected(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            exe = Path(temp) / "chrome.exe"
            exe.write_bytes(b"MZ")
            config = AppConfig(browser="custom", browser_path=str(exe))
            browser = resolve_browser(config)
            assert browser is not None
            self.assertFalse(browser.is_firefox)

    def test_browser_dataclass_flags(self) -> None:
        self.assertTrue(Browser("firefox", "Firefox", "x", "firefox").is_firefox)
        self.assertFalse(Browser("chrome", "Chrome", "x").is_firefox)


class ProcessTreeTests(unittest.TestCase):
    def test_zero_pid_has_empty_tree(self) -> None:
        self.assertEqual(process_tree(0), set())

    def test_negative_pid_has_empty_tree(self) -> None:
        self.assertEqual(process_tree(-5), set())

    @unittest.skipUnless(sys.platform == "win32", "Windows process tree")
    def test_descendants_are_collected(self) -> None:
        from synapse.browser import _tree_from_parents

        parents = {1: 0, 2: 1, 3: 1, 4: 3, 5: 4, 6: 99}
        self.assertEqual(_tree_from_parents(parents, 1), {1, 2, 3, 4, 5})
        self.assertEqual(_tree_from_parents(parents, 6), {6})
        self.assertEqual(_tree_from_parents(parents, 0), set())


if __name__ == "__main__":
    unittest.main()
