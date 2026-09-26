"""One gzip member assembled from independently compressed pieces (RAM P6).

The Live-Edit page is a few hundred KB of per-request chrome around tens of MB
of grid markup that changes a row at a time. Compressing the whole page on
every request cost more than the render it replaced, so each piece is
compressed ONCE, kept, and the response is spliced together:

* every piece is a raw DEFLATE stream from a fresh compressor, ended with a
  SYNC flush -- byte-aligned, non-final blocks, no dictionary reaching back
  into a previous piece -- so any sequence of pieces is itself a valid DEFLATE
  stream once a final empty block closes it (the method ``pigz`` uses);
* the gzip trailer needs the CRC-32 and length of the WHOLE body. The CRC of a
  concatenation follows from the pieces' own CRCs (zlib's ``crc32_combine``,
  re-implemented here since Python's ``zlib`` does not expose it); each piece
  keeps the one operator the combination needs, so assembling N pieces costs
  N small polynomial products, never a pass over the bytes.

The result is ONE gzip member (never concatenated members, which not every
client decodes), byte-for-byte decompressing to the concatenated pieces --
pinned against ``gzip.decompress`` and ``zlib.crc32`` in
``tests/test_gzsplice.py``.
"""
from __future__ import annotations

import struct
import zlib

__all__ = ["Piece", "piece", "assemble", "crc32_combine"]

_POLY = 0xEDB88320
LEVEL = 5          # the level /bulk used before (docs/103)


def _multmodp(a: int, b: int) -> int:
    """a * b modulo the CRC-32 polynomial (reflected), zlib's ``multmodp``."""
    m = 1 << 31
    p = 0
    while True:
        if a & m:
            p ^= b
            if (a & (m - 1)) == 0:
                break
        m >>= 1
        b = (b >> 1) ^ _POLY if b & 1 else b >> 1
    return p


def _x2n_table() -> list[int]:
    t = []
    p = 1 << 30                        # x^1
    t.append(p)
    for _ in range(1, 32):
        p = _multmodp(p, p)
        t.append(p)
    return t


_X2N = _x2n_table()


def _x2nmodp(n: int, k: int) -> int:
    """x^(n * 2^k) modulo the polynomial, zlib's ``x2nmodp``."""
    p = 1 << 31                        # x^0 == 1
    while n:
        if n & 1:
            p = _multmodp(_X2N[k & 31], p)
        n >>= 1
        k += 1
    return p


def crc32_combine(crc1: int, crc2: int, len2: int) -> int:
    """CRC-32 of A+B from crc(A), crc(B) and len(B)."""
    return _multmodp(_x2nmodp(len2, 3), crc1) ^ crc2


class Piece:
    """A compressed piece: raw DEFLATE (sync-flushed), its CRC-32, its length
    and the combine operator ``x^(8*len)`` so assembly never recomputes it."""

    __slots__ = ("data", "crc", "n", "op")

    def __init__(self, data: bytes, crc: int, n: int):
        self.data = data
        self.crc = crc
        self.n = n
        self.op = _x2nmodp(n, 3)

    def __len__(self) -> int:
        return len(self.data)


def piece(raw: bytes | str, level: int = LEVEL) -> Piece:
    if isinstance(raw, str):
        raw = raw.encode("utf-8")
    c = zlib.compressobj(level, zlib.DEFLATED, -15)
    data = c.compress(raw) + c.flush(zlib.Z_SYNC_FLUSH)
    return Piece(data, zlib.crc32(raw), len(raw))


_FINAL = zlib.compressobj(LEVEL, zlib.DEFLATED, -15).flush(zlib.Z_FINISH)
# 10-byte header: magic, CM=8, no flags, mtime 0, XFL 0, OS 255 (unknown)
_HEADER = b"\x1f\x8b\x08\x00\x00\x00\x00\x00\x00\xff"


def assemble(pieces: list[Piece]) -> bytes:
    crc = 0
    n = 0
    parts = [_HEADER]
    for p in pieces:
        if not p.n:
            continue
        crc = _multmodp(p.op, crc) ^ p.crc
        n += p.n
        parts.append(p.data)
    parts.append(_FINAL)
    parts.append(struct.pack("<II", crc & 0xFFFFFFFF, n & 0xFFFFFFFF))
    return b"".join(parts)
