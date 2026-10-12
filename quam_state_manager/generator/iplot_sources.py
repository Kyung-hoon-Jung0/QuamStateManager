"""Content fingerprints of replay source files, including package membership."""
import hashlib
import json
import os
from pathlib import Path


def fingerprint(files, trees=()):
    paths = {os.path.abspath(p) for p in files}
    for tree in trees:
        paths.update(str(p.resolve()) for p in Path(tree).rglob("*.py"))
    records = []
    for name in sorted(paths):
        p = Path(name)
        try:
            records.append((name, hashlib.sha256(p.read_bytes()).hexdigest()))
        except OSError:
            records.append((name, None))
    return hashlib.sha256(json.dumps(records).encode("utf-8")).hexdigest()
