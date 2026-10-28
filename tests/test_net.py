from __future__ import annotations

import ipaddress
import unittest

from tests._bootstrap import SRC  # noqa: F401
from gemini_doh.net import (
    is_global_ip,
    is_ip,
    is_valid_domain,
    is_valid_host,
    normalize_host,
    split_host_port,
)


class NetTests(unittest.TestCase):
    def test_public_and_private_ips(self) -> None:
        self.assertTrue(is_global_ip("8.8.8.8"))
        self.assertTrue(is_global_ip("1.1.1.1"))
        self.assertTrue(is_global_ip("2606:4700:4700::1111"))
        for value in (
            "127.0.0.1",
            "10.0.0.1",
            "172.16.0.1",
            "192.168.1.1",
            "169.254.1.1",
            "0.0.0.0",
            "224.0.0.1",
            "100.64.0.1",
            "::1",
            "fc00::1",
            "fe80::1",
            "not-an-ip",
        ):
            with self.subTest(value=value):
                self.assertFalse(is_global_ip(value))

    def test_ip_detection(self) -> None:
        self.assertTrue(is_ip("8.8.8.8"))
        self.assertTrue(is_ip("[2001:4860:4860::8888]"))
        self.assertFalse(is_ip("example.com"))

    def test_host_validation(self) -> None:
        self.assertTrue(is_valid_host("example.com"))
        self.assertTrue(is_valid_host("sub.example.co.uk."))
        self.assertTrue(is_valid_host("8.8.8.8"))
        self.assertFalse(is_valid_host("10.0.0.1"))
        self.assertFalse(is_valid_host("localhost"))
        self.assertFalse(is_valid_host(""))
        self.assertFalse(is_valid_domain("8.8.8.8"))
        self.assertTrue(is_valid_domain("example.com"))

    def test_normalize_host(self) -> None:
        self.assertEqual(normalize_host("[2001:DB8::1]"), "2001:db8::1")
        self.assertEqual(normalize_host(" Example.COM. "), "example.com")

    def test_split_host_port(self) -> None:
        self.assertEqual(split_host_port("example.com", 443), ("example.com", 443))
        self.assertEqual(split_host_port("example.com:8443", 443), ("example.com", 8443))
        self.assertEqual(split_host_port("[2001:db8::1]:443", 80), ("2001:db8::1", 443))
        self.assertEqual(split_host_port("[2001:db8::1]", 80), ("2001:db8::1", 80))
        self.assertEqual(split_host_port("example.com:bad", 443), ("example.com", 0))

    def test_standard_library_agrees_for_examples(self) -> None:
        for value in ("8.8.8.8", "127.0.0.1", "2606:4700::1111"):
            with self.subTest(value=value):
                self.assertEqual(is_global_ip(value), ipaddress.ip_address(value).is_global)


if __name__ == "__main__":
    unittest.main()
