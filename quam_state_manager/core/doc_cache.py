"""Parse a chip file once per CONTENT, hash it once per content (w7/livewrite).

Every live-write door and every sync poll reads the live pair, and on a 19 MB
chip each read used to cost a full ``json.loads`` (0.19 s) plus a canonical
``dumps`` for the content hash (0.23 s) -- several times per request, for bytes
SM had often just written itself. This module keys both on the SHA-256 of the
exact bytes read:

* :func:`read_pair` -- the same mtime-bracketed, torn-pair-refusing pair read
  as :func:`safe_io.read_state_wiring_raw`, with each file's parse served from
  RAM when those exact bytes were parsed before.
* :func:`content_hash_raw` / :func:`history_hash_raw` -- the two canonical
  hashes of a pair, from the bytes' digests (no parse at all on a hit).
* :func:`seed` -- a writer that just serialised a document tells the cache
  what those bytes canonicalise to, so the read-back after a write is free.

Validate on read, twice over:

* the key is the digest of the bytes actually read -- a file that changed by
  a single byte (whatever its mtime says) is a different key;
* a parsed document handed out is SHARED and must be treated read-only. So a
  hit re-checks the document against the marshal digest recorded when it was
  parsed; a caller that mutated it anyway gets it dropped and re-parsed, never
  served (33 ms on a 19 MB document, against the 190 ms parse it replaces).

Callers that need a document they may MUTATE (the store's own state) must
parse their own copy -- :func:`read_pair` with ``fresh=True``.
"""
from __future__ import annotations

import hashlib
import json
import logging
import marshal
import time
from pathlib import Path
from typing import Any

from quam_state_manager.core import json_pieces, ramcache, safe_io

logger = logging.getLogger(__name__)

_MiB = 1024 * 1024
_MARSHAL_V = 2

# sha256(raw bytes) -> (parsed doc, sha1(marshal(doc)))
PARSED = ramcache.KeyedMemo("docs.parsed", max_bytes=192 * _MiB, max_entries=8)
# sha256(raw bytes) -> canonical text (json.dumps(sort_keys=True, separators=(",", ":")))
CANON = ramcache.KeyedMemo("docs.canon", max_bytes=96 * _MiB, max_entries=24)


def digest(raw: bytes) -> bytes:
    return hashlib.sha256(raw).digest()


def _mdig(doc: Any) -> bytes | None:
    try:
        return hashlib.sha1(marshal.dumps(doc, _MARSHAL_V)).digest()
    except (ValueError, TypeError, RecursionError):
        return None


def parse_bytes(raw: bytes) -> dict:
    """``safe_io.read_json_raw``'s parse: UTF-8 JSON that must be an object."""
    data = json.loads(raw.decode("utf-8"))
    if not isinstance(data, dict):
        raise ValueError("expected a JSON object")
    return data


def _parsed_sizeof(v) -> int:
    return int(v[2])          # the byte length of the source text, times a factor


def parsed(raw: bytes, dig: bytes | None = None) -> dict:
    """The parse of *raw*, shared (READ-ONLY). Raises what the parse raises."""
    dig = dig or digest(raw)
    for _ in range(2):
        doc, mdig, _n = PARSED.get(dig, None, lambda: _parse_entry(raw),
                                   sizeof=_parsed_sizeof)
        if mdig is None or _mdig(doc) == mdig:
            return doc
        # somebody mutated a shared document: never serve it again
        logger.warning("doc_cache: a shared parsed document was mutated; re-parsing")
        PARSED.drop_where(lambda s: s == dig)
    return parse_bytes(raw)


def private(raw: bytes, dig: bytes | None = None) -> dict:
    """A document of *raw* the caller OWNS (may mutate): a ``marshal`` round
    trip of the shared parse when these bytes were parsed before and that
    parse is provably untouched -- the round trip reproduces every type, key
    order and float bit and costs about half a JSON parse -- else a parse."""
    dig = dig or digest(raw)
    if PARSED.has(dig):
        try:
            doc, mdig, _n = PARSED.get(dig, None, lambda: _parse_entry(raw),
                                       sizeof=_parsed_sizeof)
            m = marshal.dumps(doc, _MARSHAL_V)
            if mdig is not None and hashlib.sha1(m).digest() == mdig:
                return marshal.loads(m)
        except (ValueError, TypeError, RecursionError):
            pass
    return parse_bytes(raw)


def _parse_entry(raw: bytes):
    doc = parse_bytes(raw)
    # a parsed chip measured at ~1.0x its source bytes (tracemalloc, 19 MB
    # big30x); 2x keeps the estimate on the safe side of the budget
    return (doc, _mdig(doc), len(raw) * 2)


def canonical_raw(raw: bytes, dig: bytes | None = None) -> str:
    dig = dig or digest(raw)
    return CANON.get(dig, None, lambda: json_pieces.canonical(parsed(raw, dig)))


def seed(raw: bytes, canon: str, dig: bytes | None = None) -> None:
    """A writer serialised a document into *raw* and holds its canonical
    text *canon* (computed in the same walk): record it, so hashing those
    bytes never needs a parse. Harmless if it is ever not called."""
    try:
        dig = dig or digest(raw)
        CANON.get(dig, None, lambda: canon)
    except Exception:  # noqa: BLE001 -- an optimisation must never fail a write
        logger.debug("doc_cache seed skipped", exc_info=True)


def _known_valid(dig: bytes) -> bool:
    """Were these exact bytes parsed before or written by SM from a document?
    Either way they are a JSON object and need no parse to hash."""
    return CANON.has(dig)


def content_hash_raw(sb: bytes, wb: bytes, ds: bytes | None = None,
                     dw: bytes | None = None) -> str:
    """``working_copy.content_hash(parse(sb), parse(wb))``, from the bytes."""
    h = hashlib.sha256()
    h.update(b"[")
    h.update(canonical_raw(sb, ds).encode("utf-8"))
    h.update(b",")
    h.update(canonical_raw(wb, dw).encode("utf-8"))
    h.update(b"]")
    return h.hexdigest()


def history_hash_raw(sb: bytes, wb: bytes, ds: bytes | None = None,
                     dw: bytes | None = None) -> str:
    """``history._canonical_hash_of(parse(sb), parse(wb))``, from the bytes."""
    h = hashlib.sha256()
    h.update(b"STATE:")
    h.update(canonical_raw(sb, ds).encode("utf-8"))
    h.update(b"\nWIRING:")
    h.update(canonical_raw(wb, dw).encode("utf-8"))
    return h.hexdigest()


class PairRead:
    """One armored read of a state/wiring pair: the exact bytes, their
    SHA-256 digests, and the two documents. In ``"shared"`` / ``"hash"`` mode
    a document is parsed (or served from :data:`PARSED`, validated) only
    when something reads ``.state`` / ``.wiring`` -- a caller that only
    needs the digests or the content hash (a cache hit, a status poll) pays
    for the read and the digest, nothing else. Bytes that were never seen
    are still parsed DURING the read, so a torn or invalid file fails there,
    inside the retry ladder, exactly as before."""

    __slots__ = ("_state", "_wiring", "state_bytes", "wiring_bytes",
                 "state_digest", "wiring_digest")

    def __init__(self, state, wiring, state_bytes: bytes, wiring_bytes: bytes,
                 state_digest: bytes, wiring_digest: bytes) -> None:
        self._state, self._wiring = state, wiring
        self.state_bytes, self.wiring_bytes = state_bytes, wiring_bytes
        self.state_digest, self.wiring_digest = state_digest, wiring_digest

    @property
    def state(self) -> dict:
        if self._state is None:
            self._state = parsed(self.state_bytes, self.state_digest)
        return self._state

    @property
    def wiring(self) -> dict:
        if self._wiring is None:
            self._wiring = parsed(self.wiring_bytes, self.wiring_digest)
        return self._wiring

    def content_hash(self) -> str:
        return content_hash_raw(self.state_bytes, self.wiring_bytes,
                                self.state_digest, self.wiring_digest)

    def history_hash(self) -> str:
        return history_hash_raw(self.state_bytes, self.wiring_bytes,
                                self.state_digest, self.wiring_digest)


def _read_json_bytes(path: Path, mode: str) -> tuple[dict | None, bytes, bytes]:
    """``safe_io.read_json_raw`` -- same share-delete handle, same retry ladder,
    same errors -- with the parse served by :data:`PARSED` (``mode="shared"``),
    parsed privately (``"fresh"``), or skipped when only the content hash is
    wanted and these exact bytes are already known to be a valid JSON object
    (``"hash"``: their canonical text is cached -- they parsed before)."""
    tries = safe_io._READ_ATTEMPTS
    last_exc: Exception | None = None
    for attempt in range(tries):
        try:
            with safe_io.open_shared(path) as f:
                raw = f.read()
            dig = digest(raw)
            if mode == "fresh":
                data = private(raw, dig)
            elif _known_valid(dig) or PARSED.has(dig):
                data = None          # valid JSON already; parsed on first use
            else:
                data = parsed(raw, dig)
            return data, raw, dig
        except (OSError, ValueError) as exc:
            last_exc = exc
            if attempt + 1 < tries:
                time.sleep(safe_io._READ_BACKOFF_S * (attempt + 1))
    if isinstance(last_exc, FileNotFoundError):
        raise last_exc
    if isinstance(last_exc, ValueError):
        raise safe_io.LiveFileError(f"{path.name} is not valid JSON: {last_exc}") from last_exc
    raise safe_io.LiveFileError(f"Could not read {path} after {tries} attempts: {last_exc}") from last_exc


def read_pair(folder: Path | str, *, attempts: int | None = None,
              mode: str = "shared") -> PairRead:
    """:func:`safe_io.read_state_wiring_raw` (same bracket, same refusal of a
    torn pair, same errors) returning a :class:`PairRead`. ``mode``:
    ``"shared"`` -- documents from RAM, READ-ONLY; ``"fresh"`` -- the caller's
    own parse; ``"hash"`` -- documents may be ``None`` (only the hashes are
    wanted)."""
    folder = Path(folder)
    n = attempts if attempts is not None else safe_io._PAIR_READ_ATTEMPTS
    sp, wp = folder / "state.json", folder / "wiring.json"
    for attempt in range(n):
        before = safe_io._pair_fingerprint_settled(folder)
        s, sb, ds = _read_json_bytes(sp, mode)
        w, wb, dw = _read_json_bytes(wp, mode)
        after = safe_io._pair_fingerprint_settled(folder)
        if before == after:
            return PairRead(s, w, sb, wb, ds, dw)
        time.sleep(safe_io._READ_BACKOFF_S * (attempt + 1))
    raise safe_io.LiveFileError(
        f"state.json + wiring.json in {folder} kept changing across "
        f"{n} read attempts (an external writer is actively "
        "saving) — not returning a possibly-torn pair; try again")
