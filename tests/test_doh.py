from __future__ import annotations

import struct
import unittest

from tests._bootstrap import SRC  # noqa: F401
from synapse.doh import (
    DohError,
    build_query,
    encode_name,
    parse_dns_response,
    read_name,
)

QNAME = encode_name("example.com")
QUESTION = QNAME + struct.pack(">HH", 1, 1)


def _response(
    query_id: int = 0x1234,
    flags: int = 0x8180,
    ancount: int = 0,
    answers: bytes = b"",
) -> bytes:
    return (
        struct.pack(">HHHHHH", query_id, flags, 1, ancount, 0, 0)
        + QUESTION
        + answers
    )


def _cname_and_a() -> tuple[bytes, int]:
    owner = struct.pack(">H", 0xC00C)
    target = encode_name("cdn.example.com")
    cname = owner + struct.pack(">HHIH", 5, 1, 60, len(target)) + target
    cname_rdata_offset = 12 + len(QUESTION) + len(owner) + 10
    a_owner = struct.pack(">H", 0xC000 | cname_rdata_offset)
    a = a_owner + struct.pack(">HHIH", 1, 1, 120, 4) + bytes((93, 184, 216, 34))
    return cname + a, cname_rdata_offset


class EncodeTests(unittest.TestCase):
    def test_encode_name(self) -> None:
        self.assertEqual(
            encode_name("example.com"),
            b"\x07example\x03com\x00",
        )
        self.assertEqual(encode_name("example.com."), b"\x07example\x03com\x00")

    def test_encode_invalid_label(self) -> None:
        with self.assertRaises(DohError):
            encode_name("a" * 64 + ".com")
        with self.assertRaises(DohError):
            encode_name("bad..example")

    def test_build_query(self) -> None:
        packet = build_query("example.com", query_id=0xBEEF)
        query_id, flags, qd, an, ns, ar = struct.unpack_from(">HHHHHH", packet)
        self.assertEqual(query_id, 0xBEEF)
        self.assertEqual(flags, 0x0100)
        self.assertEqual((qd, an, ns, ar), (1, 0, 0, 0))
        self.assertTrue(packet.endswith(b"\x00\x00\x01\x00\x01"))

    def test_random_id_in_range(self) -> None:
        for _ in range(20):
            query_id = struct.unpack_from(">H", build_query("example.com"))[0]
            self.assertGreaterEqual(query_id, 0)
            self.assertLessEqual(query_id, 0xFFFF)


class ReadNameTests(unittest.TestCase):
    def test_plain_name(self) -> None:
        name, offset = read_name(b"\x07example\x03com\x00rest", 0)
        self.assertEqual(name, "example.com")
        self.assertEqual(offset, 13)

    def test_compressed_name(self) -> None:
        packet = b"\x03www\x07example\x03com\x00" + b"\xc0\x00"
        name, offset = read_name(packet, 17)
        self.assertEqual(name, "www.example.com")
        self.assertEqual(offset, 19)

    def test_pointer_loop(self) -> None:
        with self.assertRaises(DohError):
            read_name(b"\xc0\x00", 0)

    def test_truncated(self) -> None:
        with self.assertRaises(DohError):
            read_name(b"\x07exam", 0)


class ParseTests(unittest.TestCase):
    def test_a_record(self) -> None:
        answer = (
            struct.pack(">H", 0xC00C)
            + struct.pack(">HHIH", 1, 1, 300, 4)
            + bytes((142, 250, 74, 46))
        )
        result = parse_dns_response(_response(ancount=1, answers=answer), 0x1234)
        self.assertEqual(result.ips, ["142.250.74.46"])
        self.assertEqual(result.ttl, 300)
        self.assertEqual(result.rcode, 0)
        self.assertEqual(result.cname, "")

    def test_cname_chase_and_min_ttl(self) -> None:
        answers, _ = _cname_and_a()
        result = parse_dns_response(_response(ancount=2, answers=answers), 0x1234)
        self.assertEqual(result.ips, ["93.184.216.34"])
        self.assertEqual(result.cname, "cdn.example.com")
        self.assertEqual(result.ttl, 60)

    def test_id_mismatch(self) -> None:
        with self.assertRaises(DohError):
            parse_dns_response(_response(query_id=1), 2)

    def test_not_a_response(self) -> None:
        with self.assertRaises(DohError):
            parse_dns_response(_response(flags=0x0100))

    def test_rcode(self) -> None:
        result = parse_dns_response(_response(flags=0x8183), 0x1234)
        self.assertEqual(result.rcode, 3)

    def test_truncated_packet(self) -> None:
        with self.assertRaises(DohError):
            parse_dns_response(b"\x00" * 11)

    def test_short_answer(self) -> None:
        short = (
            struct.pack(">H", 0xC00C)
            + struct.pack(">HH", 1, 1)
        )
        with self.assertRaises(DohError):
            parse_dns_response(_response(ancount=1, answers=short), 0x1234)

    def test_private_a_record_is_parsed_but_filtered_later(self) -> None:
        answer = (
            struct.pack(">H", 0xC00C)
            + struct.pack(">HHIH", 1, 1, 30, 4)
            + bytes((192, 168, 1, 1))
        )
        result = parse_dns_response(_response(ancount=1, answers=answer), 0x1234)
        self.assertEqual(result.ips, ["192.168.1.1"])


class DnsResultTests(unittest.TestCase):
    def test_ttl_is_clamped_to_uint32_range(self) -> None:
        answer = (
            struct.pack(">H", 0xC00C)
            + struct.pack(">HHIH", 1, 1, 0xFFFFFFFF, 4)
            + bytes((8, 8, 8, 8))
        )
        result = parse_dns_response(_response(ancount=1, answers=answer), 0x1234)
        self.assertEqual(result.ttl, 86400)


if __name__ == "__main__":
    unittest.main()
