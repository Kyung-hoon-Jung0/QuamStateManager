"""Bounded replay cache on disk; source contents are checked before every hit."""
import gzip
import hashlib
import json
import os
import tempfile
from pathlib import Path

from quam_state_manager.generator.iplot_sources import fingerprint

SCHEMA = "iplot/v2-cache/1"
DISK_BYTES = 256 * 1024**2
RAM_BYTES = 64 * 1024**2


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str).encode("utf-8")).hexdigest()


def valid(meta, context):
    return (meta.get("context") == context and
            meta.get("code_fingerprint") == fingerprint(meta.get("files", []), meta.get("trees", [])))


def _atomic(path, content):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(dir=path.parent, prefix=".iplot-")
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(content)
        os.replace(name, path)
    finally:
        Path(name).unlink(missing_ok=True)


class DiskCache:
    def __init__(self, instance):
        self.root = Path(instance) / "iplot_cache"

    def load(self, context):
        index = self.root / (digest(context) + ".index.json")
        try:
            meta = json.loads(index.read_text(encoding="utf-8"))
            if not valid(meta, context):
                return None
            path = self.root / (meta["key"] + ".json.gz")
            with gzip.open(path, "rb") as fh:
                raw = fh.read(RAM_BYTES + 1)
            if len(raw) > RAM_BYTES:
                return None
            result = json.loads(raw)
            os.utime(path, None)
            return meta, result, len(raw)
        except (OSError, ValueError, KeyError, EOFError):
            return None

    def store(self, context, result):
        files = [m["path"] for m in result.get("module_files", [])]
        trees = result.get("module_trees", [])
        code = fingerprint(files, trees)
        if result.get("code_fingerprint") and code != result["code_fingerprint"]:
            return None
        # Legacy synthetic responses have no source manifest. Production always does.
        meta = {"context": context, "files": files, "trees": trees, "code_fingerprint": code}
        meta["key"] = digest([context, code, result.get("revision")])
        result["cache_key"] = meta["key"]
        raw = json.dumps(result, allow_nan=False).encode("utf-8")
        if len(raw) <= RAM_BYTES:
            _atomic(self.root / (meta["key"] + ".json.gz"), gzip.compress(raw))
            _atomic(self.root / (digest(context) + ".index.json"), json.dumps(meta).encode("utf-8"))
            self.evict()
        return meta, len(raw)

    def evict(self):
        files = list(self.root.glob("*.json.gz"))
        total = sum(p.stat().st_size for p in files)
        for path in sorted(files, key=lambda p: p.stat().st_atime_ns):
            if total <= DISK_BYTES:
                break
            total -= path.stat().st_size
            key = path.name.removesuffix(".json.gz")
            path.unlink(missing_ok=True)
            for index in self.root.glob("*.index.json"):
                try:
                    if json.loads(index.read_text())["key"] == key:
                        index.unlink(missing_ok=True)
                except (OSError, ValueError, KeyError):
                    index.unlink(missing_ok=True)
