"""Minimal HTTP/1.1 response reader used for DoH and the --check command."""

from __future__ import annotations

import socket
from dataclasses import dataclass, field

MAX_HEADER_BYTES = 64 * 1024
MAX_BODY_BYTES = 2 * 1024 * 1024


class HttpError(OSError):
    pass


@dataclass
class HttpResponse:
    status: int
    headers: dict[str, str] = field(default_factory=dict)
    body: bytes = b""


def _read_line(fp, limit: int = MAX_HEADER_BYTES) -> bytes:
    line = fp.readline(limit + 1)
    if not line:
        raise HttpError("соединение закрыто до конца заголовков")
    if not line.endswith(b"\n"):
        raise HttpError("слишком длинная строка HTTP")
    return line


def _read_exact(fp, size: int) -> bytes:
    chunks: list[bytes] = []
    remaining = size
    while remaining > 0:
        chunk = fp.read(remaining)
        if not chunk:
            raise HttpError("соединение закрыто до конца тела ответа")
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


def read_http_response(sock: socket.socket, max_body: int = MAX_BODY_BYTES) -> HttpResponse:
    """Read one response.  Handles Content-Length, chunked and close-delimited."""
    fp = sock.makefile("rb")
    try:
        status_line = _read_line(fp, 16 * 1024).decode("iso-8859-1", "replace").strip()
        parts = status_line.split(" ", 2)
        if len(parts) < 2 or not parts[0].upper().startswith("HTTP/"):
            raise HttpError(f"некорректный статус HTTP: {status_line[:80]!r}")
        try:
            status = int(parts[1])
        except ValueError as exc:
            raise HttpError(f"некорректный статус HTTP: {status_line[:80]!r}") from exc

        headers: dict[str, str] = {}
        used = 0
        while True:
            line = _read_line(fp)
            used += len(line)
            if used > MAX_HEADER_BYTES:
                raise HttpError("слишком большие заголовки HTTP")
            if line in (b"\r\n", b"\n"):
                break
            text = line.decode("iso-8859-1", "replace").rstrip("\r\n")
            if ":" not in text:
                continue
            key, value = text.split(":", 1)
            key = key.strip().lower()
            value = value.strip()
            headers[key] = f"{headers[key]}, {value}" if key in headers else value

        if status in (204, 304):
            return HttpResponse(status, headers, b"")

        transfer = headers.get("transfer-encoding", "").lower()
        if "chunked" in transfer:
            body = _read_chunked(fp, max_body)
            return HttpResponse(status, headers, body)

        if "content-length" in headers:
            try:
                length = int(headers["content-length"].split(",")[0].strip())
            except ValueError as exc:
                raise HttpError("некорректный Content-Length") from exc
            if length < 0 or length > max_body:
                raise HttpError(f"слишком большое тело ответа: {length} байт")
            return HttpResponse(status, headers, _read_exact(fp, length))

        body = bytearray()
        while True:
            chunk = fp.read(65536)
            if not chunk:
                break
            body.extend(chunk)
            if len(body) > max_body:
                raise HttpError("слишком большое тело ответа")
        return HttpResponse(status, headers, bytes(body))
    finally:
        try:
            fp.close()
        except OSError:
            pass


def _read_chunked(fp, max_body: int) -> bytes:
    body = bytearray()
    while True:
        line = _read_line(fp, 1024).strip()
        if b";" in line:
            line = line.split(b";", 1)[0]
        try:
            size = int(line, 16)
        except ValueError as exc:
            raise HttpError("некорректный chunked-ответ") from exc
        if size == 0:
            while True:
                trailer = _read_line(fp, 8192)
                if trailer in (b"\r\n", b"\n"):
                    break
            break
        if size < 0 or len(body) + size > max_body:
            raise HttpError("слишком большое тело chunked-ответа")
        body.extend(_read_exact(fp, size))
        ending = _read_exact(fp, 2)
        if ending != b"\r\n":
            raise HttpError("некорректное окончание chunked-блока")
    return bytes(body)
