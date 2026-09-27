"""RAM P10, the I/O half of a cold open:

* the workspace listing cache carries each run's RESOLVED path, so a
  cache-served root does no per-run link probing on the request thread -- and
  the background verify re-derives it, so a folder that became a link since
  the cache was written is re-keyed;
* ``safe_io`` retries a failed replace after 10 ms first (the transient
  WinError 1175 clears in ~10 ms), not after 0.5 s.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from quam_state_manager.core import safe_io, scanner
from quam_state_manager.core.scanner import Workspace


def _mk_run(root: Path, date: str, rid: int) -> Path:
    run = root / date / f"#{rid}_04_power_rabi_1200{rid % 60:02d}"
    qs = run / "quam_state"
    qs.mkdir(parents=True)
    (qs / "state.json").write_text(json.dumps({"qubits": {"q1": {"f_01": 1}}}))
    (qs / "wiring.json").write_text("{}")
    (run / "node.json").write_text(json.dumps({
        "id": rid, "created_at": f"{date}T12:00:{rid % 60:02d}+00:00",
        "metadata": {"name": "04_power_rabi", "status": "finished"},
        "data": {"parameters": {"model": {"qubits": ["q1"]}}}}))
    return qs


def _cached_session(tmp_path) -> tuple[Path, Path]:
    root = tmp_path / "data"
    for i in range(3):
        _mk_run(root, "2026-03-01", 7 + i)
    cache = tmp_path / "cache"
    ws1 = Workspace()
    ws1.cache_dir = cache
    ws1.add_root(root, defer_parse=True)
    deadline = time.time() + 15
    while ws1.hydrating_roots() or not list(cache.glob("ws_*.json")):
        assert time.time() < deadline, "listing cache never written"
        time.sleep(0.05)
    return root, cache


class TestResolvedPathsInTheListingCache:
    def test_cache_carries_resolved_paths_and_a_hit_resolves_nothing(self, tmp_path, monkeypatch):
        root, cache = _cached_session(tmp_path)
        rows = json.loads(next(cache.glob("ws_*.json")).read_text())["entries"]
        assert all(r.get("qsr") for r in rows)
        calls = []
        real = scanner._fast_resolve
        monkeypatch.setattr(scanner, "_fast_resolve",
                            lambda p, memo: (calls.append(p), real(p, memo))[1])
        monkeypatch.setattr(Workspace, "_verify_cached_root", lambda self, r: None)
        ws2 = Workspace()
        ws2.cache_dir = cache
        entries = ws2.add_root(root, defer_parse=True)
        assert calls == []                      # nothing probed on the request thread
        assert {str(e.qs_resolved) for e in entries} == {r["qsr"] for r in rows}
        assert all(ws2._entries_by_path[e.qs_resolved] is e for e in entries)

    def test_the_background_verify_rekeys_a_wrong_cached_path(self, tmp_path, monkeypatch):
        root, cache = _cached_session(tmp_path)
        p = next(cache.glob("ws_*.json"))
        raw = json.loads(p.read_text())
        truth = raw["entries"][0]["qsr"]
        raw["entries"][0]["qsr"] = str(tmp_path / "elsewhere" / "quam_state")
        p.write_text(json.dumps(raw))
        monkeypatch.setattr(Workspace, "_verify_cached_root", lambda self, r: None)
        ws2 = Workspace()
        ws2.cache_dir = cache
        ws2.add_root(root, defer_parse=True)
        v0 = ws2.version if hasattr(ws2, "version") else None
        moved = ws2._reresolve_cached(root.resolve())
        assert moved == 1
        assert Path(truth) in ws2._entries_by_path
        assert Path(tmp_path / "elsewhere" / "quam_state") not in ws2._entries_by_path
        if v0 is not None:
            assert ws2.version > v0

    def test_a_cache_without_resolved_paths_still_opens(self, tmp_path):
        root, cache = _cached_session(tmp_path)
        p = next(cache.glob("ws_*.json"))
        raw = json.loads(p.read_text())
        for r in raw["entries"]:
            r.pop("qsr", None)
        p.write_text(json.dumps(raw))
        ws2 = Workspace()
        ws2.cache_dir = cache
        entries = ws2.add_root(root, defer_parse=True)
        assert all(e.qs_resolved is not None for e in entries)


class TestReplaceRetryLadder:
    def test_first_retry_waits_ten_ms_and_the_budget_is_kept(self, tmp_path, monkeypatch):
        dst = tmp_path / "f.json"
        dst.write_text("{}")
        sleeps: list[float] = []
        monkeypatch.setattr(safe_io.time, "sleep", lambda s: sleeps.append(s))

        class Busy(OSError):
            winerror = 1175

        n = {"calls": 0}

        def flaky(_tmp, _dst):
            n["calls"] += 1
            if n["calls"] < 3:
                raise Busy(1175, "Unable to remove the file to be replaced")
            import os
            os.replace(_tmp, _dst)

        monkeypatch.setattr(safe_io, "_IS_WINDOWS", True)
        monkeypatch.setattr(safe_io, "_replace_file_windows", flaky)
        safe_io.atomic_write_json(dst, {"v": 1})
        assert json.loads(dst.read_text()) == {"v": 1}
        assert sleeps == [pytest.approx(0.01), pytest.approx(0.03)]
        # a genuinely held file is still waited out about as long as before
        total = safe_io._WRITE_BACKOFF_S * sum(safe_io._WRITE_BACKOFF_STEPS)
        assert 1.5 <= total <= 2.0
