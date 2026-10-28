from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tests._bootstrap import SRC  # noqa: F401
from gemini_doh.config import (
    AppConfig,
    ConfigError,
    ConfigStore,
    DohSettings,
    Site,
    default_sites,
    normalize_site_url,
    parse_doh_url,
    slugify,
)

ROOT = Path(__file__).resolve().parents[1]


class DohUrlTests(unittest.TestCase):
    def test_default_host_gets_https_and_path(self) -> None:
        endpoint = parse_doh_url("dns.dagami.ru")
        self.assertEqual(endpoint.url, "https://dns.dagami.ru/dns-query")
        self.assertEqual(endpoint.host, "dns.dagami.ru")
        self.assertEqual(endpoint.port, 443)
        self.assertEqual(endpoint.path, "/dns-query")

    def test_root_path_becomes_dns_query(self) -> None:
        self.assertEqual(
            parse_doh_url("https://dns.example/").path,
            "/dns-query",
        )

    def test_custom_port_and_path(self) -> None:
        endpoint = parse_doh_url("https://dns.example:8443/custom")
        self.assertEqual(endpoint.url, "https://dns.example:8443/custom")
        self.assertEqual(endpoint.port, 8443)

    def test_ipv6_endpoint(self) -> None:
        endpoint = parse_doh_url("https://[2606:4700::1111]/dns-query")
        self.assertEqual(endpoint.host, "2606:4700::1111")
        self.assertEqual(endpoint.url, "https://[2606:4700::1111]/dns-query")

    def test_rejects_insecure_or_ambiguous_urls(self) -> None:
        for value in (
            "http://dns.example/dns-query",
            "https://user:pass@dns.example/dns-query",
            "https://dns.example/dns-query?x=1",
            "https://dns.example/dns-query#frag",
            "https://dns.example:99999/dns-query",
            "https:///dns-query",
            "",
        ):
            with self.subTest(value=value):
                with self.assertRaises(ConfigError):
                    parse_doh_url(value)

    def test_idna_host(self) -> None:
        endpoint = parse_doh_url("https://днс.пример/dns-query")
        self.assertEqual(endpoint.host, "xn--d1asm.xn--e1afmkfd")


class DohSettingsTests(unittest.TestCase):
    def test_bootstrap_parsing_and_dedup(self) -> None:
        settings = DohSettings.from_dict(
            {"bootstrap_ips": "45.147.31.32, 8.8.8.8; 45.147.31.32", "timeout": 999}
        )
        self.assertEqual(settings.bootstrap_ips, ["45.147.31.32", "8.8.8.8"])
        self.assertEqual(settings.timeout, 30.0)

    def test_bootstrap_invalid(self) -> None:
        with self.assertRaises(ConfigError):
            DohSettings.from_dict({"bootstrap_ips": "not-an-ip"})

    def test_roundtrip(self) -> None:
        settings = DohSettings(url="https://dns.example/dns-query", timeout=4.5)
        again = DohSettings.from_dict(settings.to_dict())
        self.assertEqual(again.to_dict(), settings.to_dict())
        self.assertIsNot(again.bootstrap_ips, settings.bootstrap_ips)


class SiteAndSlugTests(unittest.TestCase):
    def test_normalize_site_url(self) -> None:
        self.assertEqual(normalize_site_url("example.com"), "https://example.com")
        self.assertEqual(
            normalize_site_url("http://example.com/path?q=1#frag"),
            "http://example.com/path?q=1",
        )
        self.assertEqual(normalize_site_url("https://example.com/"), "https://example.com")
        with self.assertRaises(ConfigError):
            normalize_site_url("ftp://example.com")
        with self.assertRaises(ConfigError):
            normalize_site_url("https://user:pass@example.com")

    def test_slugify_cyrillic(self) -> None:
        self.assertEqual(slugify("Мой сайт"), "moy-sayt")
        self.assertEqual(slugify("***"), "site")

    def test_default_sites(self) -> None:
        urls = {site.url for site in default_sites()}
        self.assertEqual(
            urls,
            {
                "https://gemini.google.com",
                "https://chatgpt.com",
                "https://claude.ai",
                "https://grok.com",
            },
        )

    def test_duplicate_ids_are_suffixed(self) -> None:
        known: set[str] = set()
        first = Site.from_dict({"name": "Test", "url": "https://a.example"}, known)
        known.add(first.id)
        second = Site.from_dict({"name": "Test", "url": "https://b.example"}, known)
        self.assertEqual((first.id, second.id), ("test", "test-2"))
        self.assertEqual(second.match_tokens(), ["test", "b.example"])


class AppConfigTests(unittest.TestCase):
    def test_default_roundtrip(self) -> None:
        config = AppConfig.default()
        again = AppConfig.from_dict(config.to_dict())
        self.assertEqual(again.to_dict(), config.to_dict())

    def test_example_matches_defaults(self) -> None:
        example = json.loads((ROOT / "config.example.json").read_text(encoding="utf-8"))
        self.assertEqual(example, AppConfig.default().to_dict())

    def test_invalid_values_are_sanitized(self) -> None:
        config = AppConfig.from_dict(
            {
                "proxy_port": 999999,
                "allowed_ports": ["bad", 443, 443],
                "browser_width": 1,
                "browser_height": 99999,
                "window_geometry": "evil; rm -rf",
                "browser": "netscape",
            }
        )
        self.assertEqual(config.proxy_port, 65535)
        self.assertEqual(config.allowed_ports, [443])
        self.assertEqual(config.browser_width, 640)
        self.assertEqual(config.browser_height, 2160)
        self.assertEqual(config.window_geometry, "")
        self.assertEqual(config.browser, "auto")

    def test_sites_default_when_missing(self) -> None:
        config = AppConfig.from_dict({"sites": None})
        self.assertEqual(len(config.sites), 4)

    def test_size_only_geometry_is_kept(self) -> None:
        config = AppConfig.from_dict({"window_geometry": "640x720"})
        self.assertEqual(config.window_geometry, "640x720")

    def test_bad_sites_type_raises(self) -> None:
        with self.assertRaises(ConfigError):
            AppConfig.from_dict({"sites": "nope"})


class ConfigStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "config.json"

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_missing_file_is_created(self) -> None:
        store = ConfigStore(self.path)
        config = store.load()
        self.assertTrue(self.path.exists())
        self.assertEqual(config.doh.url, DohSettings().url)
        self.assertEqual(store.warnings, [])

    def test_save_is_atomic_and_no_temp_left(self) -> None:
        store = ConfigStore(self.path)
        store.save(AppConfig.default())
        self.assertFalse((self.path.parent / "config.json.tmp").exists())
        self.assertEqual(
            json.loads(self.path.read_text(encoding="utf-8"))["version"],
            2,
        )

    def test_corrupt_file_is_backed_up(self) -> None:
        self.path.write_text("{not json", encoding="utf-8")
        store = ConfigStore(self.path)
        config = store.load()
        self.assertEqual(len(config.sites), 4)
        self.assertTrue(store.warnings)
        backups = list(self.path.parent.glob("config.broken-*.json"))
        self.assertEqual(len(backups), 1)
        self.assertIn("{not json", backups[0].read_text(encoding="utf-8"))
        self.assertTrue(json.loads(self.path.read_text(encoding="utf-8")))

    def test_future_version_warns(self) -> None:
        payload = AppConfig.default().to_dict()
        payload["version"] = 99
        self.path.write_text(json.dumps(payload), encoding="utf-8")
        store = ConfigStore(self.path)
        store.load()
        self.assertTrue(any("v99" in warning for warning in store.warnings))


class PathTests(unittest.TestCase):
    def test_runtime_uses_environment_home(self) -> None:
        from gemini_doh.paths import AppPaths

        with tempfile.TemporaryDirectory() as temp:
            with mock.patch.dict(os.environ, {"GEMINI_DOH_HOME": temp}):
                paths = AppPaths.runtime()
                self.assertEqual(paths.root, Path(temp).resolve())
                self.assertTrue(paths.profiles.is_dir())
                self.assertTrue(paths.logs.is_dir())
                self.assertEqual(paths.config, Path(temp).resolve() / "config.json")
                self.assertEqual(paths.log_file.name, "gemini-doh.log")

    def test_config_override_is_resolved(self) -> None:
        from gemini_doh.paths import AppPaths

        with tempfile.TemporaryDirectory() as temp:
            with mock.patch.dict(os.environ, {"GEMINI_DOH_HOME": temp}):
                paths = AppPaths.runtime(str(Path(temp) / "custom.json"))
                self.assertEqual(paths.config, (Path(temp) / "custom.json").resolve())


if __name__ == "__main__":
    unittest.main()
