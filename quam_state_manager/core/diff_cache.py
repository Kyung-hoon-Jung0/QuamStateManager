"""Diffs between two chip CONTENTS, shared across the surfaces that ask.

w7/livewrite (P4). The drift poll diffs the baseline against the live chip;
a moment later the pre-apply backup snapshot of that same live chip diffs it
against the newest snapshot -- which is, after every apply, the baseline's
content again. On a 19 MB chip each is a ~0.2 s tree diff on the write path.

An entry is keyed on the two sides' content hashes (``history``'s canonical
state+wiring hash, the one both the drift baseline and every snapshot meta
already record), so a hit is by construction the diff of exactly those two
contents. Two conditions make one entry serve both askers:

* it holds the diff taken with NO ignored keys -- a caller that ignores
  some (the snapshot's ``Differ`` default) filters the leaves, which is what
  ``Differ`` itself does, path by path;
* it is only stored when neither side has a top-level key in BOTH state.json
  and wiring.json, the one case where the drift diff's flat merge and the
  snapshot's ``QuamStore`` deep merge build different documents.
"""
from __future__ import annotations

from quam_state_manager.core import ramcache

PAIRS = ramcache.KeyedMemo("diff.content_pairs", max_entries=16,
                           max_bytes=32 * 1024 * 1024)


def merges_alike(state, wiring) -> bool:
    """True when the flat and the deep state+wiring merge build the same
    document (no top-level key in both files)."""
    try:
        return not (set(state) & set(wiring))
    except TypeError:
        return False


def raw_key(state_digest: bytes | None, wiring_digest: bytes | None):
    """A content key for side *b* from the raw BYTES' digests (doc_cache's
    sha256 of each file). Equal bytes are equal content, so this names the
    same diff the canonical hash would -- without the canonical dump, which
    the drift poll never needs for itself (w7/livewrite: Take live's
    snapshot diff was recomputed because the live side's canonical hash
    was not known when the poll offered its diff)."""
    if not state_digest or not wiring_digest:
        return None
    return ("raw", bytes(state_digest), bytes(wiring_digest))


def remember(hash_a: str, hash_b, entries: list) -> None:
    """Record ``Differ().diff(a, b, ignore_keys=set())`` for two contents
    whose merges are alike (the caller checked :func:`merges_alike`)."""
    if not hash_a or not hash_b:
        return
    PAIRS.get((hash_a, hash_b), None, lambda: list(entries))


_MISS = object()


def lookup(hash_a: str, hash_b, ignore: set | frozenset = frozenset()) -> list | None:
    """The remembered diff of the two contents with *ignore*'s leaf keys
    left out (``Differ``'s own filter), or None."""
    if not hash_a or not hash_b:
        return None
    key = (hash_a, hash_b)
    # w7 fq-sync P3d: a lookup, never a compute. Through ``get`` with a
    # "missing" compute, shadow mode (SM_RAM_VERIFY) compared every hit with
    # that sentinel and raised on a correct diff -- and the snapshot capture
    # that asked recorded a zero diff_summary. The entry is content-addressed
    # (the key IS the two contents); shadow mode checks it where it is written
    # (``remember``'s own get compares a re-remembered diff with the held one).
    entries = PAIRS.get_held(key, None, _MISS)
    if entries is _MISS:
        return None
    if not ignore:
        return list(entries)
    from quam_state_manager.core.differ import _leaf_key
    return [e for e in entries if _leaf_key(e.dot_path) not in ignore]
