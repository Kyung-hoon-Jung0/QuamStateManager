"""Content fingerprints of replay source files, including package membership."""
import hashlib
import json
import os
from pathlib import Path


def _members(trees):
    return sorted({os.path.abspath(p) for tree in trees for p in Path(tree).rglob("*.py")})


# D-4: path -> (mtime_ns, size, sha256). A long-lived replay worker re-hashes
# ~1,000 lab files per request; a file whose stat is unchanged keeps its digest.
# The fingerprint is the same with or without the memo.
_SHA_MEMO: dict[str, tuple[int, int, str]] = {}


def _sha(name):
    try:
        before = os.stat(name)
        memo = _SHA_MEMO.get(name)
        if memo and memo[0] == before.st_mtime_ns and memo[1] == before.st_size:
            return memo[2]
        digest = hashlib.sha256(Path(name).read_bytes()).hexdigest()
        after = os.stat(name)
    except OSError:
        _SHA_MEMO.pop(name, None)
        return None
    if (after.st_mtime_ns, after.st_size) == (before.st_mtime_ns, before.st_size):
        _SHA_MEMO[name] = (after.st_mtime_ns, after.st_size, digest)
    return digest


def fingerprint(files, trees=()):
    paths = {os.path.abspath(p) for p in files}
    paths.update(_members(trees))
    records = [(name, _sha(name)) for name in sorted(paths)]
    return hashlib.sha256(json.dumps(records).encode("utf-8")).hexdigest()


def snapshot(files, trees=()):
    """Cheap stat picture of the same file set ``fingerprint`` hashes.

    Equal snapshots mean no file was edited, added or removed, so a hit can
    skip re-hashing every source; any difference falls back to the hash.
    """
    stats = {}
    for name in sorted({os.path.abspath(p) for p in files} | set(_members(trees))):
        try:
            st = os.stat(name)
            stats[name] = [st.st_mtime_ns, st.st_size]
        except OSError:
            stats[name] = None
    return stats
