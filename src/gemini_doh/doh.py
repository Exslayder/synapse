"""DNS-over-HTTPS resolver.

The DoH endpoint is contacted by bootstrap IP with the proper SNI/Host name,
because ordinary DNS for dns.dagami.ru is not reliable.  Responses are parsed
locally; only public A records are returned to the proxy.
"""

from __future__ import annotations

import secrets
import socket
import ssl
import struct
import threading
import time
from dataclasses import dataclass, field
from collections.abc import Callable

from .config import DohEndpoint, DohSettings
from .http_client import read_http_response
from .net import is_global_ip, is_ip, is_valid_domain, normalize_host

MAX_CACHE_TTL = 300
MIN_CACHE_TTL = 5
MAX_CACHE_ENTRIES = 4096


class DohError(OSError):
    pass


@dataclass
class DnsResult:
    ips: list[str] = field(default_factory=list)
    ttl: int = 30
    cname: str = ""
    rcode: int = 0


class _TruncatedDns(DohError):
    pass


def encode_name(name: str) -> bytes:
    out = bytearray()
    for label in name.strip(".").split("."):
        raw = label.encode("ascii")
        if not raw or len(raw) > 63:
            raise DohError(f"некорректная метка DNS: {label!r}")
        out.append(len(raw))
        out += raw
    out.append(0)
    return bytes(out)


def build_query(name: str, qtype: int = 1, query_id: int | None = None) -> bytes:
    if query_id is None:
        query_id = secrets.randbelow(0x10000)
    header = struct.pack(">HHHHHH", query_id, 0x0100, 1, 0, 0, 0)
    return header + encode_name(name) + struct.pack(">HH", qtype, 1)


def read_name(buf: bytes, offset: int) -> tuple[str, int]:
    labels: list[str] = []
    jumped = False
    cursor = offset
    guard = 0
    while True:
        if cursor < 0 or cursor >= len(buf):
            raise _TruncatedDns("обрезанное имя DNS")
        length = buf[cursor]
        if length == 0:
            cursor += 1
            break
        if length & 0xC0 == 0xC0:
            if cursor + 1 >= len(buf):
                raise _TruncatedDns("некорректный указатель DNS")
            pointer = ((length & 0x3F) << 8) | buf[cursor + 1]
            if pointer >= len(buf):
                raise _TruncatedDns("указатель DNS вне пакета")
            if not jumped:
                offset = cursor + 2
            cursor = pointer
            jumped = True
            guard += 1
            if guard > 16:
                raise DohError("петля указателей DNS")
            continue
        if length > 63:
            raise DohError("некорректная длина метки DNS")
        cursor += 1
        if cursor + length > len(buf):
            raise _TruncatedDns("обрезанная метка DNS")
        labels.append(buf[cursor : cursor + length].decode("ascii", "replace"))
        cursor += length
        if len(labels) > 128:
            raise DohError("слишком длинное имя DNS")
    if not jumped:
        offset = cursor
    return ".".join(labels), offset


def parse_dns_response(buf: bytes, expected_id: int | None = None) -> DnsResult:
    if len(buf) < 12:
        raise DohError("короткий DNS-ответ")
    query_id, flags, qdcount, ancount, _nscount, _arcount = struct.unpack_from(">HHHHHH", buf, 0)
    if expected_id is not None and query_id != expected_id:
        raise DohError("DNS-ответ не совпал по ID")
    if not flags & 0x8000:
        raise DohError("DNS-пакет не является ответом")
    rcode = flags & 0xF
    if rcode:
        return DnsResult(rcode=rcode)

    offset = 12
    for _ in range(qdcount):
        _, offset = read_name(buf, offset)
        if offset + 4 > len(buf):
            raise _TruncatedDns("обрезанный вопрос DNS")
        offset += 4

    ips: list[str] = []
    a_ttls: list[int] = []
    cname = ""
    cname_ttl = 30

    for _ in range(ancount):
        _, offset = read_name(buf, offset)
        if offset + 10 > len(buf):
            raise _TruncatedDns("обрезанная запись DNS")
        rtype, rclass, ttl, rdlen = struct.unpack_from(">HHIH", buf, offset)
        offset += 10
        rdata_start = offset
        rdata_end = rdata_start + rdlen
        if rdata_end > len(buf):
            raise _TruncatedDns("обрезанные данные DNS")
        if rclass == 1 and rtype == 1 and rdlen == 4:
            ips.append(socket.inet_ntoa(buf[rdata_start:rdata_end]))
            a_ttls.append(max(0, min(ttl, 86400)))
        elif rtype == 5:
            target, _ = read_name(buf, rdata_start)
            if target:
                cname = normalize_host(target)
                cname_ttl = max(0, min(ttl, 86400))
        offset = rdata_end

    if ips:
        # De-duplicate while preserving order.
        unique: list[str] = []
        for ip in ips:
            if ip not in unique:
                unique.append(ip)
        return DnsResult(
            ips=unique,
            ttl=min(a_ttls + ([cname_ttl] if cname else [])) if a_ttls else 30,
            cname=cname,
        )
    if cname:
        return DnsResult(cname=cname, ttl=cname_ttl)
    return DnsResult(ttl=30)


def _build_context() -> ssl.SSLContext:
    context = ssl.create_default_context()
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    try:
        context.set_alpn_protocols(["http/1.1"])
    except NotImplementedError:
        pass
    return context


def _host_header(endpoint: DohEndpoint) -> str:
    host = f"[{endpoint.host}]" if ":" in endpoint.host else endpoint.host
    if endpoint.port != 443:
        host += f":{endpoint.port}"
    return host


def doh_query(
    packet: bytes,
    ip: str,
    endpoint: DohEndpoint,
    timeout: float,
    context: ssl.SSLContext,
) -> bytes:
    if ":" in ip:
        ip = ip.strip("[]")
    raw = socket.create_connection((ip, endpoint.port), timeout=timeout)
    raw.settimeout(timeout)
    try:
        tls = context.wrap_socket(raw, server_hostname=endpoint.host)
    except Exception:
        raw.close()
        raise
    try:
        head = (
            f"POST {endpoint.path} HTTP/1.1\r\n"
            f"Host: {_host_header(endpoint)}\r\n"
            "Content-Type: application/dns-message\r\n"
            "Accept: application/dns-message\r\n"
            f"Content-Length: {len(packet)}\r\n"
            "User-Agent: GeminiDoH/2\r\n"
            "Connection: close\r\n\r\n"
        ).encode("ascii")
        tls.sendall(head + packet)
        response = read_http_response(tls)
    finally:
        try:
            tls.close()
        except OSError:
            pass
    if response.status != 200:
        raise DohError(f"DoH HTTP {response.status}")
    if not response.body:
        raise DohError("пустой ответ DoH")
    return response.body


def _recv_exact(sock: socket.socket, size: int) -> bytes:
    data = bytearray()
    while len(data) < size:
        chunk = sock.recv(size - len(data))
        if not chunk:
            raise DohError("DNS/53 закрыл соединение")
        data.extend(chunk)
    return bytes(data)


def tcp_dns_query(packet: bytes, ip: str, timeout: float) -> bytes:
    if ":" in ip:
        ip = ip.strip("[]")
    sock = socket.create_connection((ip, 53), timeout=timeout)
    sock.settimeout(timeout)
    try:
        sock.sendall(struct.pack(">H", len(packet)) + packet)
        header = _recv_exact(sock, 2)
        need = struct.unpack(">H", header)[0]
        if need > 65535:
            raise DohError("слишком длинный DNS-ответ")
        return _recv_exact(sock, need)
    finally:
        sock.close()


def candidate_ips(settings: DohSettings, endpoint: DohEndpoint) -> list[str]:
    found: list[str] = []
    for raw in settings.bootstrap_ips:
        ip = str(raw).strip("[]")
        if ip and ip not in found:
            found.append(ip)
    try:
        infos = socket.getaddrinfo(endpoint.host, endpoint.port, type=socket.SOCK_STREAM)
    except OSError:
        infos = []
    for info in infos:
        ip = str(info[4][0])
        if ip and ip not in found:
            found.append(ip)
    return found


class DohResolver:
    def __init__(
        self,
        settings: DohSettings,
        log: Callable[[str], None] | None = None,
    ):
        self._log = log
        self._lock = threading.RLock()
        self._settings = settings.copy()
        self._endpoint = settings.endpoint()
        self._context = _build_context()
        self._cache: dict[str, tuple[float, list[str], str]] = {}

    @property
    def endpoint(self) -> DohEndpoint:
        with self._lock:
            return self._endpoint

    def settings(self) -> DohSettings:
        with self._lock:
            return self._settings.copy()

    def configure(self, settings: DohSettings) -> None:
        endpoint = settings.endpoint()
        context = _build_context()
        with self._lock:
            self._settings = settings.copy()
            self._endpoint = endpoint
            self._context = context
            self._cache.clear()
        self._log_line(
            f"DoH: {endpoint.url}  bootstrap={', '.join(settings.bootstrap_ips) or 'системный DNS'}"
        )

    def clear_cache(self) -> None:
        with self._lock:
            self._cache.clear()

    def resolve(self, host: str) -> list[str]:
        ips, _via = self.resolve_detailed(host)
        return ips

    def resolve_detailed(self, host: str, use_cache: bool = True) -> tuple[list[str], str]:
        host = normalize_host(host)
        if not host:
            raise DohError("пустое имя")
        if is_ip(host):
            if not is_global_ip(host):
                raise DohError("локальный IP запрещён")
            return [host], "IP"

        if not is_valid_domain(host):
            raise DohError("некорректное имя")

        now = time.monotonic()
        if use_cache:
            with self._lock:
                hit = self._cache.get(host)
                if hit and hit[0] > now:
                    return list(hit[1]), hit[2]

        try:
            ips, via, ttl = self._resolve_uncached(host)
        except DohError as exc:
            self._log_line(f"DNS  {host}  →  ошибка: {exc}")
            raise

        ttl = max(MIN_CACHE_TTL, min(ttl, MAX_CACHE_TTL))
        with self._lock:
            if len(self._cache) > MAX_CACHE_ENTRIES:
                expired = [key for key, value in self._cache.items() if value[0] <= now]
                for key in expired:
                    self._cache.pop(key, None)
            self._cache[host] = (now + ttl, list(ips), via)

        self._log_line(f"{via}  {host}  →  {', '.join(ips)}")
        return ips, via

    def _resolve_uncached(self, host: str) -> tuple[list[str], str, int]:
        current = host
        seen: set[str] = set()
        via = "DoH"
        for _ in range(6):
            if current in seen:
                raise DohError("петля CNAME")
            seen.add(current)
            result, via = self._query(current)
            allowed = [ip for ip in result.ips if is_global_ip(ip)]
            if allowed:
                return allowed, via, result.ttl
            if result.cname and result.cname != current:
                current = result.cname
                continue
            if result.ips:
                raise DohError("DNS вернул только локальные адреса")
            raise DohError("нет A-записи")
        raise DohError("слишком длинная цепочка CNAME")

    def _query(self, name: str) -> tuple[DnsResult, str]:
        with self._lock:
            settings = self._settings.copy()
            endpoint = self._endpoint
            context = self._context

        packet = build_query(name, 1)
        expected_id = struct.unpack_from(">H", packet, 0)[0]
        errors: list[str] = []

        ips = candidate_ips(settings, endpoint)
        if not ips:
            raise DohError("нет bootstrap-адресов и не удалось получить IP DoH-сервера")

        for ip in ips:
            try:
                body = doh_query(packet, ip, endpoint, settings.timeout, context)
                result = parse_dns_response(body, expected_id)
            except Exception as exc:
                errors.append(f"{ip}: {exc}")
                continue
            if result.rcode:
                if result.rcode == 3:
                    raise DohError("NXDOMAIN")
                errors.append(f"{ip}: DNS rcode {result.rcode}")
                continue
            return result, f"DoH {ip}"

        if settings.fallback_tcp:
            for ip in settings.bootstrap_ips:
                try:
                    body = tcp_dns_query(packet, ip, settings.timeout)
                    result = parse_dns_response(body, expected_id)
                except Exception as exc:
                    errors.append(f"{ip} tcp: {exc}")
                    continue
                if result.rcode:
                    if result.rcode == 3:
                        raise DohError("NXDOMAIN")
                    errors.append(f"{ip} tcp: DNS rcode {result.rcode}")
                    continue
                return result, f"DNS/53 {ip}"

        raise DohError("; ".join(errors[:4]) or "DNS не ответил")

    def _log_line(self, message: str) -> None:
        if self._log is not None:
            try:
                self._log(message)
            except Exception:
                pass
