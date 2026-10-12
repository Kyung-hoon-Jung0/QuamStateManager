"""Content fingerprints of replay source files, including package membership."""
import hashlib
import json
import os
from pathlib import Path


def _members(trees):
    return sorted({os.path.abspath(p) for tree in trees for p in Path(tree).rglob("*.py")})


def fingerprint(files, trees=()):
    paths = {os.path.abspath(p) for p in files}
    paths.update(_members(trees))
    records = []
    for name in sorted(paths):
        p = Path(name)
        try:
            records.append((name, hashlib.sha256(p.read_bytes()).hexdigest()))
        except OSError:
            records.append((name, None))
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
