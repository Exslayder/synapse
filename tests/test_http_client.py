from __future__ import annotations

import socket
import unittest

from tests._bootstrap import SRC  # noqa: F401
from gemini_doh.http_client import HttpError, read_http_response


class HttpReaderTests(unittest.TestCase):
    def _read(self, payload: bytes):
        client, server = socket.socketpair()
        try:
            server.sendall(payload)
            server.shutdown(socket.SHUT_WR)
            response = read_http_response(client)
            return response
        finally:
            client.close()
            server.close()

    def test_content_length(self) -> None:
        response = self._read(
            b"HTTP/1.1 200 OK\r\nContent-Length: 5\r\nContent-Type: text/plain\r\n\r\nhello"
        )
        self.assertEqual(response.status, 200)
        self.assertEqual(response.body, b"hello")

    def test_chunked_with_extension_and_trailer(self) -> None:
        payload = (
            b"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\n"
            b"5;foo=bar\r\nhello\r\n"
            b"6\r\n world\r\n"
            b"0\r\nX-Trailer: yes\r\n\r\n"
        )
        response = self._read(payload)
        self.assertEqual(response.body, b"hello world")
        self.assertEqual(response.headers["transfer-encoding"], "chunked")

    def test_close_delimited(self) -> None:
        response = self._read(b"HTTP/1.1 200 OK\r\n\r\nuntil close")
        self.assertEqual(response.body, b"until close")

    def test_no_content(self) -> None:
        response = self._read(b"HTTP/1.1 204 No Content\r\nContent-Length: 99\r\n\r\n")
        self.assertEqual(response.status, 204)
        self.assertEqual(response.body, b"")

    def test_duplicate_headers_are_joined(self) -> None:
        response = self._read(
            b"HTTP/1.1 200 OK\r\nX-Test: one\r\nX-Test: two\r\nContent-Length: 0\r\n\r\n"
        )
        self.assertEqual(response.headers["x-test"], "one, two")

    def test_bad_status(self) -> None:
        with self.assertRaises(HttpError):
            self._read(b"ICY 200 OK\r\n\r\n")

    def test_bad_content_length(self) -> None:
        with self.assertRaises(HttpError):
            self._read(b"HTTP/1.1 200 OK\r\nContent-Length: nope\r\n\r\n")

    def test_too_large_body(self) -> None:
        with self.assertRaises(HttpError):
            self._read(b"HTTP/1.1 200 OK\r\nContent-Length: 999999999\r\n\r\n")

    def test_bad_chunk_ending(self) -> None:
        with self.assertRaises(HttpError):
            self._read(
                b"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\n"
                b"3\r\nabcXX"
            )


if __name__ == "__main__":
    unittest.main()
