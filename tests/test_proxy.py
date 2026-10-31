from __future__ import annotations

import socket
import unittest

from tests._bootstrap import SRC  # noqa: F401
from synapse.proxy import (
    connect_host,
    read_request_head,
    rewrite_request,
    split_http_target,
)


class RequestParsingTests(unittest.TestCase):
    def test_absolute_form(self) -> None:
        self.assertEqual(
            split_http_target("http://example.com:8080/a?b=1"),
            ("example.com", 8080, "/a?b=1", "http"),
        )
        self.assertEqual(
            split_http_target("https://example.com/"),
            ("example.com", 443, "/", "https"),
        )
        self.assertEqual(split_http_target("/local"), (None, 0, None, ""))
        self.assertEqual(split_http_target("http://user:pass@example.com/"), (None, 0, None, ""))
        self.assertEqual(split_http_target("ftp://example.com/"), (None, 0, None, ""))

    def test_rewrite_request(self) -> None:
        head = (
            b"GET http://example.com/a?b=1 HTTP/1.1\r\n"
            b"Host: example.com\r\n"
            b"Proxy-Connection: keep-alive\r\n"
            b"Proxy-Authorization: Basic x\r\n"
            b"Connection: keep-alive\r\n"
            b"User-Agent: test\r\n"
        )
        rewritten = rewrite_request(head, "/a?b=1")
        self.assertIn(b"GET /a?b=1 HTTP/1.1\r\n", rewritten)
        self.assertIn(b"Host: example.com\r\n", rewritten)
        self.assertIn(b"User-Agent: test\r\n", rewritten)
        self.assertNotIn(b"Proxy-Connection", rewritten)
        self.assertNotIn(b"Proxy-Authorization", rewritten)
        self.assertNotIn(b"keep-alive", rewritten)
        self.assertTrue(rewritten.endswith(b"Connection: close"))

    def test_read_head_with_rest(self) -> None:
        client, server = socket.socketpair()
        try:
            server.sendall(b"GET / HTTP/1.1\r\nHost: x\r\n\r\nbody")
            server.shutdown(socket.SHUT_WR)
            head, rest = read_request_head(client)
            self.assertEqual(head, b"GET / HTTP/1.1\r\nHost: x")
            self.assertEqual(rest, b"body")
        finally:
            client.close()
            server.close()

    def test_connect_host_rejects_private_literal(self) -> None:
        class Resolver:
            def resolve_detailed(self, _host):
                raise AssertionError("resolver must not be called for a literal IP")

        self.assertIsNone(connect_host("127.0.0.1", 443, Resolver(), lambda _line: None))
        self.assertIsNone(connect_host("10.0.0.1", 443, Resolver(), lambda _line: None))


if __name__ == "__main__":
    unittest.main()
