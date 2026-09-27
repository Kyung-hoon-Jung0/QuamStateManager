"""core/gzsplice: independently compressed pieces make ONE valid gzip member
(RAM P6 -- the Live-Edit page is spliced from cached compressed rows)."""
from __future__ import annotations

import gzip
import os
import random
import zlib

from quam_state_manager.core import gzsplice as G


def test_crc32_combine_matches_zlib_on_random_splits():
    rng = random.Random(7)
    for _ in range(300):
        a = os.urandom(rng.randrange(0, 3000))
        b = os.urandom(rng.randrange(0, 3000))
        assert G.crc32_combine(zlib.crc32(a), zlib.crc32(b), len(b)) == zlib.crc32(a + b)


def test_spliced_pieces_decompress_to_the_concatenation():
    rng = random.Random(11)
    for trial in range(60):
        raws = []
        for _ in range(rng.randrange(0, 12)):
            kind = rng.random()
            if kind < 0.2:
                raws.append(b"")
            elif kind < 0.6:
                raws.append(("<td class='x'>%d</td>" % rng.randrange(10 ** 6)).encode() * rng.randrange(1, 400))
            else:
                raws.append(os.urandom(rng.randrange(1, 70000)))
        body = G.assemble([G.piece(r) for r in raws])
        assert gzip.decompress(body) == b"".join(raws), trial
        # the trailer is what a strict decoder checks
        crc, n = int.from_bytes(body[-8:-4], "little"), int.from_bytes(body[-4:], "little")
        assert crc == zlib.crc32(b"".join(raws)) and n == len(b"".join(raws)) % 2 ** 32


def test_a_piece_is_reusable_across_assemblies_in_any_order():
    ps = [G.piece("héllo " * i) for i in range(1, 6)]
    for order in ([0, 1, 2, 3, 4], [4, 2, 0], [3, 3, 3]):
        body = G.assemble([ps[i] for i in order])
        assert gzip.decompress(body).decode("utf-8") == "".join("héllo " * (i + 1) for i in order)
