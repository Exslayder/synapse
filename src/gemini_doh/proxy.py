"""Local HTTP/HTTPS proxy that resolves every host through the DoH resolver."""

from __future__ import annotations

import socket
import socketserver
import threading
from collections.abc import Callable
from urllib.parse import urlsplit

from .doh import DohError, DohResolver
from .net import is_global_ip, is_ip, is_valid_host, split_host_port

MAX_HEAD = 64 * 1024
CONNECT_TIMEOUT = 10.0
HEADER_TIMEOUT = 20.0

LogFn = Callable[[str], None]


def _tune(sock: socket.socket) -> None:
    try:
        sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
    except OSError:
        pass


def read_request_head(client: socket.socket) -> tuple[bytes, bytes]:
    data = bytearray()
    while b"\r\n\r\n" not in data:
        chunk = client.recv(4096)
        if not chunk:
            raise ConnectionError("клиент закрыл соединение до заголовков")
        data.extend(chunk)
        if len(data) > MAX_HEAD:
            raise ValueError("слишком длинные заголовки запроса")
    head, rest = bytes(data).split(b"\r\n\r\n", 1)
    return head, rest


def header_value(head: bytes, name: str) -> str:
    wanted = name.lower().encode("ascii")
    for raw in head.split(b"\r\n")[1:]:
        key, sep, value = raw.partition(b":")
        if sep and key.strip().lower() == wanted:
            return value.strip().decode("iso-8859-1", "replace")
    return ""


def split_http_target(target: str) -> tuple[str | None, int, str | None, str]:
    """absolute-form URL -> (host, port, path, scheme); origin-form returns None."""
    if "://" not in target:
        return None, 0, None, ""
    try:
        parts = urlsplit(target)
        port = parts.port
    except ValueError:
        return None, 0, None, ""
    scheme = parts.scheme.lower()
    if scheme not in ("http", "https") or not parts.hostname:
        return None, 0, None, ""
    if port is None:
        port = 443 if scheme == "https" else 80
    if parts.username or parts.password:
        return None, 0, None, ""
    path = parts.path or "/"
    if parts.query:
        path += "?" + parts.query
    return parts.hostname, port, path, scheme


def rewrite_request(head: bytes, path: str) -> bytes:
    text = head.decode("iso-8859-1", "replace")
    lines = text.split("\r\n")
    first = lines[0].split(" ", 2)
    if len(first) >= 3:
        lines[0] = f"{first[0]} {path} {first[2]}"
    else:
        lines[0] = f"{first[0]} {path} HTTP/1.1"

    kept = [lines[0]]
    for line in lines[1:]:
        lower = line.lower()
        if lower.startswith("proxy-connection:") or lower.startswith("proxy-authorization:"):
            continue
        if lower.startswith("connection:"):
            continue
        kept.append(line)
    kept.append("Connection: close")
    return "\r\n".join(kept).encode("iso-8859-1", "replace")


def connect_host(
    host: str,
    port: int,
    resolver: DohResolver,
    log: LogFn,
) -> socket.socket | None:
    if is_ip(host):
        if not is_global_ip(host):
            log(f"connect  {host}:{port}  локальный IP запрещён")
            return None
        ips = [host]
    else:
        try:
            ips, _via = resolver.resolve_detailed(host)
        except DohError as exc:
            log(f"connect  {host}:{port}  DNS: {exc}")
            return None
    for ip in ips:
        try:
            remote = socket.create_connection((ip, port), timeout=CONNECT_TIMEOUT)
        except OSError as exc:
            log(f"connect  {host}  {ip}:{port}  {exc}")
            continue
        _tune(remote)
        return remote
    return None


def _send_error(client: socket.socket, code: int, reason: str) -> None:
    body = f"{code} {reason}\n".encode()
    head = (
        f"HTTP/1.1 {code} {reason}\r\n"
        "Content-Type: text/plain; charset=utf-8\r\n"
        f"Content-Length: {len(body)}\r\n"
        "Connection: close\r\n\r\n"
    ).encode("ascii")
    try:
        client.sendall(head + body)
    except OSError:
        pass


def pipe(src: socket.socket, dst: socket.socket) -> None:
    try:
        while True:
            chunk = src.recv(65536)
            if not chunk:
                break
            dst.sendall(chunk)
    except OSError:
        pass
    finally:
        try:
            dst.shutdown(socket.SHUT_WR)
        except OSError:
            pass


class ProxyHandler(socketserver.BaseRequestHandler):
    def handle(self) -> None:
        client: socket.socket = self.request
        remote: socket.socket | None = None
        server: ProxyServer = self.server  # type: ignore[assignment]
        try:
            client.settimeout(HEADER_TIMEOUT)
            head, rest = read_request_head(client)
            request_line = head.split(b"\r\n", 1)[0].decode("iso-8859-1", "replace")
            parts = request_line.split(" ", 2)
            if len(parts) < 2:
                _send_error(client, 400, "Bad Request")
                return
            method, target = parts[0].upper(), parts[1]

            if method == "CONNECT":
                host, port = split_host_port(target, 443)
                path = None
            else:
                host, port, path, scheme = split_http_target(target)
                if host is None:
                    # Origin-form request: host comes from the Host header.
                    host_header = header_value(head, "host")
                    host, port = split_host_port(host_header, 80)
                    path = target
                    scheme = "http"
                if scheme == "https":
                    _send_error(client, 400, "HTTPS requires CONNECT")
                    return

            if (
                not host
                or not is_valid_host(host)
                or port not in server.allowed_ports
                or (path is not None and not path.startswith("/"))
            ):
                _send_error(client, 403, "Forbidden")
                return

            remote = connect_host(host, port, server.resolver, server.log)
            if remote is None:
                _send_error(client, 502, "Bad Gateway")
                return

            client.settimeout(None)
            remote.settimeout(None)

            if method == "CONNECT":
                client.sendall(b"HTTP/1.1 200 Connection Established\r\nProxy-Agent: GeminiDoH\r\n\r\n")
                if rest:
                    remote.sendall(rest)
            else:
                remote.sendall(rewrite_request(head, path or "/") + b"\r\n\r\n" + rest)

            left = threading.Thread(target=pipe, args=(client, remote), daemon=True)
            right = threading.Thread(target=pipe, args=(remote, client), daemon=True)
            left.start()
            right.start()
            left.join()
            right.join()
        except (ConnectionError, TimeoutError, ValueError) as exc:
            server.log(f"proxy  {type(exc).__name__}: {exc}")
        except OSError as exc:
            server.log(f"proxy  {type(exc).__name__}: {exc}")
        except Exception as exc:  # pragma: no cover - defensive
            server.log(f"proxy  неожиданная ошибка: {type(exc).__name__}: {exc}")
        finally:
            if remote is not None:
                try:
                    remote.close()
                except OSError:
                    pass


class ProxyServer(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True
    request_queue_size = 128

    def __init__(
        self,
        server_address: tuple[str, int],
        resolver: DohResolver,
        allowed_ports: list[int],
        log: LogFn,
    ):
        self.resolver = resolver
        self.allowed_ports = list(allowed_ports)
        self.log = log
        super().__init__(server_address, ProxyHandler)


def serve(
    port: int,
    resolver: DohResolver,
    allowed_ports: list[int],
    log: LogFn,
) -> tuple[ProxyServer, int]:
    last: Exception | None = None
    for candidate in range(port, port + 20):
        try:
            server = ProxyServer(("127.0.0.1", candidate), resolver, allowed_ports, log)
        except OSError as exc:
            last = exc
            continue
        thread = threading.Thread(target=server.serve_forever, name="doh-proxy", daemon=True)
        thread.start()
        return server, candidate
    raise OSError(f"не удалось открыть локальный порт: {last}")


def shutdown_server(server: ProxyServer) -> None:
    try:
        server.shutdown()
    except Exception:
        pass
    try:
        server.server_close()
    except Exception:
        pass
