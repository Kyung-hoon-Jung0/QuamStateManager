"""Tests for the node_scan two-tier scan cache — performance + safety.

The cache must (1) make a warm scan stat-only (no re-read/parse), (2) re-parse
only files whose (mtime, size) changed, (3) drop deleted files from the persisted
set, (4) discard the disk cache on a heuristics-version bump, and — the safety
invariant — (5) never return a stale classification for an edited file, so the
Scheduler's add-path stays server-authoritative.
"""
import json
from pathlib import Path

import pytest

from quam_state_manager.core import node_scan

NODE_SRC = 'from qualibrate import QualibrationNode\nnode = QualibrationNode(name="n_{i}")\n'
GRAPH_SRC = 'from qualibrate import QualibrationGraph\ng = QualibrationGraph(name="g_{i}")\n'


def _write(folder: Path, name: str, src: str) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    p = folder / name
    p.write_text(src)
    return p


def _pad_to(src: str, n: int) -> str:
    """Pad source with a trailing comment to exactly n bytes (ast ignores it)."""
    b = src.encode()
    assert len(b) <= n, (len(b), n)
    return src + "\n# " + "p" * (n - len(b) - 3)


def _count_parses(monkeypatch):
    """Patch scan_file (called by scan_folder on a miss) to record real read+parses."""
    calls = []
    orig = node_scan.scan_file
    monkeypatch.setattr(
        node_scan, "scan_file",
        lambda p: (calls.append(Path(p).name), orig(p))[1])
    return calls


@pytest.fixture(autouse=True)
def _isolate_mem_cache():
    node_scan.clear_cache()       # _MEM_CACHE is module-global — isolate every test
    yield
    node_scan.clear_cache()


class TestNodeScanCache:
    def test_warm_scan_does_no_reparse(self, tmp_path, monkeypatch):
        cal = tmp_path / "cal"
        for i in range(3):
            _write(cal, f"1Q_0{i}_x.py", NODE_SRC.format(i=i))
        calls = _count_parses(monkeypatch)
        r1 = node_scan.scan_folder(cal, instance_path=tmp_path)   # cold
        assert len(r1) == 3 and len(calls) == 3
        calls.clear()
        r2 = node_scan.scan_folder(cal, instance_path=tmp_path)   # warm
        assert len(r2) == 3 and calls == []                      # stat-only, no parse
        assert {i.name for i in r1} == {i.name for i in r2}

    def test_edit_reparses_only_changed(self, tmp_path, monkeypatch):
        cal = tmp_path / "cal"
        _write(cal, "a.py", NODE_SRC.format(i=0))
        b = _write(cal, "b.py", NODE_SRC.format(i=1))
        node_scan.scan_folder(cal, instance_path=tmp_path)
        node_scan.clear_cache()                                   # force the disk path
        calls = _count_parses(monkeypatch)
        b.write_text(NODE_SRC.format(i=1) + "\n# changed, now a longer file\n")
        node_scan.scan_folder(cal, instance_path=tmp_path)
        assert calls == ["b.py"]                                  # a.py reused from disk

    def test_delete_drops_from_persisted(self, tmp_path):
        cal = tmp_path / "cal"
        _write(cal, "a.py", NODE_SRC.format(i=0))
        bp = _write(cal, "b.py", NODE_SRC.format(i=1))
        node_scan.scan_folder(cal, instance_path=tmp_path)
        cache = json.loads((tmp_path / node_scan._CACHE_FILENAME).read_text())
        assert str(bp) in cache["files"]
        bp.unlink()
        node_scan.clear_cache()
        node_scan.scan_folder(cal, instance_path=tmp_path)
        cache = json.loads((tmp_path / node_scan._CACHE_FILENAME).read_text())
        assert str(bp) not in cache["files"]

    def test_version_bump_discards_disk_cache(self, tmp_path, monkeypatch):
        cal = tmp_path / "cal"
        _write(cal, "a.py", NODE_SRC.format(i=0))
        node_scan.scan_folder(cal, instance_path=tmp_path)
        node_scan.clear_cache()
        monkeypatch.setattr(node_scan, "_HEURISTICS_VERSION",
                            node_scan._HEURISTICS_VERSION + 1)
        calls = _count_parses(monkeypatch)
        node_scan.scan_folder(cal, instance_path=tmp_path)        # stale version -> re-parse
        assert calls == ["a.py"]

    def test_scan_file_always_fresh_even_on_mtime_size_collision(self, tmp_path):
        # THE safety pin: scan_file (the add/run path) must ALWAYS re-derive, so a
        # mtime+size-preserving graph<->node swap can never feed a queued/run item a
        # stale kind. The folder cache is display-only and MAY go stale here.
        import os
        cal = tmp_path / "cal"
        f = _write(cal, "x.py", _pad_to(NODE_SRC.format(i=0), 400))   # a node, 400 bytes
        st0 = f.stat()
        node_scan.scan_folder(cal, instance_path=tmp_path)            # caches x as a node
        # overwrite with a GRAPH of the SAME size, then restore the exact mtime ->
        # the (mtime_ns, size) fingerprint is now identical, defeating the stat key
        f.write_bytes(_pad_to(GRAPH_SRC.format(i=0), 400).encode())
        os.utime(f, ns=(st0.st_atime_ns, st0.st_mtime_ns))
        assert f.stat().st_size == st0.st_size
        assert f.stat().st_mtime_ns == st0.st_mtime_ns               # collision confirmed
        # the cached library scan still shows the STALE node (display-only) ...
        cached = node_scan.scan_folder(cal, instance_path=tmp_path)
        assert cached[0].kind == node_scan.KIND_NODE
        # ... but the safety-critical scan_file ALWAYS reports the truth: graph
        assert node_scan.scan_file(f).kind == node_scan.KIND_GRAPH

    def test_two_folders_do_not_evict_each_other(self, tmp_path, monkeypatch):
        # Scanning folder B must not wipe folder A's persisted rows (merge, not
        # replace), so A's cross-restart cache survives a folder switch.
        a, b = tmp_path / "A", tmp_path / "B"
        _write(a, "a1.py", NODE_SRC.format(i=1))
        _write(b, "b1.py", GRAPH_SRC.format(i=1))
        node_scan.scan_folder(a, instance_path=tmp_path)
        node_scan.scan_folder(b, instance_path=tmp_path)
        cache = json.loads((tmp_path / node_scan._CACHE_FILENAME).read_text())
        keys = set(cache["files"])
        assert any(k.endswith("a1.py") for k in keys)        # A survived B's scan
        assert any(k.endswith("b1.py") for k in keys)
        node_scan.clear_cache()                              # simulate restart (mem gone)
        calls = _count_parses(monkeypatch)
        node_scan.scan_folder(a, instance_path=tmp_path)     # A is warm off disk -> no re-parse
        assert calls == []

    def test_no_instance_path_still_works_without_persisting(self, tmp_path):
        cal = tmp_path / "cal"
        _write(cal, "a.py", NODE_SRC.format(i=0))
        r = node_scan.scan_folder(cal)                            # no instance_path
        assert len(r) == 1 and r[0].kind == node_scan.KIND_NODE
        assert not (tmp_path / node_scan._CACHE_FILENAME).exists()

    def test_read_error_is_not_cached(self, tmp_path, monkeypatch):
        # A transient read failure must not be cached (so it retries next scan).
        cal = tmp_path / "cal"
        f = _write(cal, "a.py", NODE_SRC.format(i=0))
        real_read = Path.read_text

        def boom(self, *a, **k):
            if Path(self) == f:
                raise OSError("transient")
            return real_read(self, *a, **k)

        monkeypatch.setattr(Path, "read_text", boom)
        r1 = node_scan.scan_folder(cal, instance_path=tmp_path)
        assert r1[0].error and "read error" in r1[0].error
        monkeypatch.undo()                                        # read recovers
        r2 = node_scan.scan_folder(cal, instance_path=tmp_path)
        assert r2[0].error is None and r2[0].kind == node_scan.KIND_NODE
