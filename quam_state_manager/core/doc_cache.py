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
from dataclasses import dataclass
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


def _parse_entry(raw: bytes):
    doc = parse_bytes(raw)
    return (doc, _mdig(doc), len(raw) * 6)


def canonical_raw(raw: bytes, dig: bytes | None = None) -> str:
    dig = dig or digest(raw)
    return CANON.get(dig, None, lambda: json_pieces.canonical(parsed(raw, dig)))


def seed(raw: bytes | None, doc: dict, dig: bytes | None = None) -> None:
    """A writer serialised *doc* into *raw*: record what those bytes
    canonicalise to. Correct by construction -- ``parse(dumps(doc))`` equals
    *doc* for JSON data -- and harmless if it is ever not called."""
    try:
        dig = dig or digest(raw)
        CANON.get(dig, None, lambda: json_pieces.canonical(doc))
    except Exception:  # noqa: BLE001 -- an optimisation must never fail a write
        logger.debug("doc_cache seed skipped", exc_info=True)


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


@dataclass
class PairRead:
    state: dict
    wiring: dict
    state_bytes: bytes
    wiring_bytes: bytes
    state_digest: bytes
    wiring_digest: bytes

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
                data = parse_bytes(raw)
            elif mode == "hash" and CANON.has(dig):
                data = None
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
