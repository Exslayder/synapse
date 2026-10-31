from __future__ import annotations

import sys
import unittest
from types import SimpleNamespace

from tests._bootstrap import SRC  # noqa: F401

from synapse.ui.widgets import clipboard_key


class ClipboardKeyTests(unittest.TestCase):
    """Ctrl+C/V/X/A must work with a non-Latin keyboard layout."""

    def test_cyrillic_layout_keysyms(self) -> None:
        cases = {
            "Cyrillic_ef": "a",
            "Cyrillic_es": "c",
            "Cyrillic_em": "v",
            "Cyrillic_che": "x",
            "cyrillic_em": "v",
            "Cyrillic_ya": "z",
            "Cyrillic_en": "y",
        }
        for keysym, expected in cases.items():
            event = SimpleNamespace(keysym=keysym, keycode=0)
            self.assertEqual(clipboard_key(event), expected, keysym)

    def test_latin_keysyms_are_case_insensitive(self) -> None:
        self.assertEqual(clipboard_key(SimpleNamespace(keysym="C", keycode=67)), "c")
        self.assertEqual(clipboard_key(SimpleNamespace(keysym="a", keycode=65)), "a")
        self.assertEqual(clipboard_key(SimpleNamespace(keysym="Z", keycode=90)), "z")
        self.assertEqual(clipboard_key(SimpleNamespace(keysym="Y", keycode=89)), "y")

    @unittest.skipUnless(sys.platform == "win32", "Windows virtual-key codes")
    def test_windows_keycode_fallback(self) -> None:
        # With the Russian layout Windows still reports the physical VK code.
        self.assertEqual(clipboard_key(SimpleNamespace(keysym="??", keycode=67)), "c")
        self.assertEqual(clipboard_key(SimpleNamespace(keysym="??", keycode=86)), "v")
        self.assertEqual(clipboard_key(SimpleNamespace(keysym="??", keycode=90)), "z")
        self.assertEqual(clipboard_key(SimpleNamespace(keysym="??", keycode=89)), "y")

    def test_unrelated_key_is_ignored(self) -> None:
        self.assertIsNone(clipboard_key(SimpleNamespace(keysym="s", keycode=83)))


if __name__ == "__main__":
    unittest.main()
