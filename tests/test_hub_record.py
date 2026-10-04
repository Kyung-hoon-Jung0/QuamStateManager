"""docs/271 (S4) -- every write SM makes to a live chip is recorded exactly.

Pins, on real files in ``tmp_path`` (generic synthetic chips):

* the pure entry <-> S2 row conversion, cross-checked against the S2 rules
  by brute force on random documents and random SM edits;
* the journal: one fsync'd line BEFORE the live write; a journal that cannot
  be written means nothing is written; a write that fails after its line is
  marked failed (and, if even that cannot be written, rolled back out); a
  torn line, a large write (blob file, never a sub-directory), and a line
  nobody can vouch for after a crash;
* the ledger: exact rows, ``state_at`` equal to what was written for anchored
  and replayed SM events, rebuild from the journal alone, UNDONE / redo;
* every door: who / when / old -> new, and the doors that are NOT SM writes
  record nothing (Take live, auto-sync pull, reconcile adopt, a refused or
  no-op apply);
* fault injection: every ``apply_to_live`` call site in the package names its
  ``record=``, and every live-writing save / direct pair write is classified
  -- a new door fails this file until it records.
"""

from __future__ import annotations

import ast
import copy
import gzip
import hashlib
import json
import os
import random
import threading
from pathlib import Path

import pytest

from quam_state_manager.core import hub, hub_entries, hub_rules as rules
from quam_state_manager.core import safe_io, undo_journal, working_copy
from quam_state_manager.core.hub_store import (
    PARTLY_UNDONE, REVERTS_TO_EARLIER, UNDONE, HubStore, value)
from quam_state_manager.web import routes as routes_mod
from quam_state_manager.web.app import create_app

PKG = Path(__file__).resolve().parent.parent / "quam_state_manager"
_WIRING = {"network": {"host": "127.0.0.1", "cluster_name": "C1"}}


# ----------------------------------------------------------------------
# fixtures
# ----------------------------------------------------------------------

def _state(t1=2.0e-5, f01=5.0e9, wave=None):
    return {"qubits": {"qA1": {"id": "qA1", "f_01": f01, "T1": t1,
                               "wave": list(wave if wave is not None else range(20)),
                               "xy": {"operations": {"x180": {"amplitude": 0.25, "length": 40}}}},
                       "qA2": {"id": "qA2", "f_01": 5.1e9, "T1": 3.0e-5}},
            "qubit_pairs": {}, "active_qubit_names": ["qA1", "qA2"]}


def _write_chip(folder: Path, state: dict, wiring: dict | None = None):
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "state.json").write_text(json.dumps(state), encoding="utf-8")
    (folder / "wiring.json").write_text(json.dumps(wiring or _WIRING), encoding="utf-8")


@pytest.fixture(autouse=True)
def _inline_projection():
    """Project on the writer's thread (create_app(testing=True) does the same);
    the async projector has its own pin below."""
    old = hub._PROJECTOR.inline
    hub.set_inline(True)
    yield
    hub.set_inline(old)


@pytest.fixture
def env(tmp_path):
    live = tmp_path / "chips" / "live"
    _write_chip(live, _state())
    app = create_app(testing=True, instance_path=str(tmp_path / "_inst"))
    c = app.test_client()
    assert c.post("/load", data={"folder": str(live)}).status_code in (200, 302)
    return {"app": app, "client": c, "live": live, "tmp": tmp_path}


def _ctx(env):
    with env["app"].app_context():
        return routes_mod._active_ctx()


def _chip_dir(env) -> Path:
    with env["app"].app_context():
        return routes_mod._hub_chip_dir(_ctx(env)["path"])


def _events(env) -> list[dict]:
    return hub.Hub.for_chip(_chip_dir(env)).events()


def _ledger(env, sm_id: str) -> dict:
    with HubStore(_chip_dir(env)) as st:
        row = st.conn.execute("SELECT e.*, s.outcome, s.undoes, s.units FROM sm_events s "
                              "JOIN events e USING(eid) WHERE s.sm_id=?", (sm_id,)).fetchone()
        assert row is not None, f"event {sm_id} not in the ledger"
        rows = st.conn.execute("SELECT p.path, c.op, c.num, c.txt, c.old_num, c.old_txt FROM changes c "
                               "JOIN paths p USING(pid) WHERE c.eid=? ORDER BY p.path", (row["eid"],)).fetchall()
        out = dict(row)
        out["rows"] = {r["path"]: (value(r["old_num"], r["old_txt"]), value(r["num"], r["txt"])) for r in rows}
        out["doc"] = st.state_at(row["eid"]) if row["error"] is None else None
        return out


def _live_doc(env) -> dict:
    s = json.loads((env["live"] / "state.json").read_text(encoding="utf-8"))
    w = json.loads((env["live"] / "wiring.json").read_text(encoding="utf-8"))
    return rules.merged(s, w)


def _edit(env, path, val, headers=None):
    r = env["client"].post("/field/edit", data={"dot_path": path, "value": val}, headers=headers or {})
    assert r.status_code == 200, r.data[:300]


def _same_doc(a, b) -> bool:
    fa, fb = rules.flatten(a), rules.flatten(b)
    return not rules.diff(fa, fb)


# ======================================================================
# 1. entries <-> S2 rows (pure), cross-checked by brute force
# ======================================================================

def _rand_value(rng, depth=0):
    k = rng.random()
    if depth < 2 and k < 0.25:
        return {f"k{i}": _rand_value(rng, depth + 1) for i in range(rng.randint(0, 3))}
    if depth < 2 and k < 0.35:
        n = rng.choice([0, 3, 16, 17, 25])
        return [rng.choice([rng.random(), rng.randint(0, 5), True, None, "s"]) for _ in range(n)]
    return rng.choice([rng.random(), rng.randint(-3, 3), 1.0, float("nan"), True, False, None,
                       "#/qubits/qA1", "txt"])


def _rand_doc(rng):
    return {"qubits": {f"q{i}": {"f": rng.random(), "T1": rng.random(), "arr": [rng.random() for _ in range(rng.choice([5, 20]))],
                                 "ops": [{"a": rng.random()}, {"a": rng.random()}],
                                 "sub": {"x": rng.random(), "y": {"z": rng.randint(0, 9)}}}
                       for i in range(3)},
            "top": rng.random()}


def _leaf_paths(doc, prefix=""):
    out = []
    if isinstance(doc, dict):
        for k, v in doc.items():
            p = f"{prefix}.{k}" if prefix else k
            out.append(p)
            out.extend(_leaf_paths(v, p))
    elif isinstance(doc, list):
        for i, v in enumerate(doc):
            p = f"{prefix}.{i}"
            out.append(p)
            out.extend(_leaf_paths(v, p))
    return out


def _random_edits(rng, doc, n):
    """SM-shaped entries applied one after another (the modifier's ops)."""
    entries = []
    cur = copy.deepcopy(doc)
    for _ in range(n):
        paths = _leaf_paths(cur)
        kind = rng.random()
        if kind < 0.6 and paths:
            p = rng.choice(paths)
            old = _get(cur, p)
            new = _rand_value(rng)
            entries.append({"path": p, "old": copy.deepcopy(old), "new": copy.deepcopy(new)})
            cur = _plain_apply(cur, entries[-1])
        elif kind < 0.8:
            dicts = [p for p in paths if isinstance(_get(cur, p), dict)]
            if not dicts:
                continue
            p = f"{rng.choice(dicts)}.new{rng.randint(0, 99)}"
            if _get(cur, p, None) is not None:
                continue
            new = _rand_value(rng)
            entries.append({"path": p, "new": copy.deepcopy(new), "created": True})
            cur = _plain_apply(cur, entries[-1])
        else:
            cands = [p for p in paths if p.count(".") >= 1 and isinstance(_parent(cur, p), dict)]
            if not cands:
                continue
            p = rng.choice(cands)
            entries.append({"path": p, "old": copy.deepcopy(_get(cur, p)), "deleted": True})
            cur = _plain_apply(cur, entries[-1])
    return entries, cur


def _plain_apply(doc, e):
    """The generator's OWN edit, independent of hub_entries (so the
    cross-check cannot agree with itself): plain navigation on a copy."""
    doc = copy.deepcopy(doc)
    parts = e["path"].split(".")
    node = doc
    for part in parts[:-1]:
        node = node[int(part)] if isinstance(node, list) else node[part]
    last = parts[-1]
    if isinstance(node, list):
        last = int(last)
    if e.get("deleted"):
        del node[last]
    else:
        node[last] = copy.deepcopy(e["new"])
    return doc


def _get(doc, path, default=KeyError):
    node = doc
    for part in path.split("."):
        if isinstance(node, dict) and part in node:
            node = node[part]
        elif isinstance(node, list) and part.isdigit() and int(part) < len(node):
            node = node[int(part)]
        else:
            if default is KeyError:
                raise KeyError(path)
            return default
    return node


def _parent(doc, path):
    return _get(doc, path.rsplit(".", 1)[0], None) if "." in path else doc


class TestEntriesToRows:
    def test_rows_for_equals_the_full_s2_diff_on_random_edits(self):
        """[derived] rows_for touches only the edited roots; brute force: it
        must equal the S2 diff of the whole before/after documents."""
        rng = random.Random(271)
        for trial in range(400):
            doc = _rand_doc(rng)
            entries, post = _random_edits(rng, doc, rng.randint(1, 6))
            if not entries:
                continue
            expect = rules.diff(rules.flatten(doc), rules.flatten(post))
            got = hub_entries.rows_for(entries, post)
            assert got == expect, (trial, entries)
            # the door hands the projector only the written roots
            assert hub_entries.rows_for(entries, hub_entries.post_fragments(entries, post)) == expect

    def test_revert_and_apply_are_inverse(self):
        rng = random.Random(7)
        for _ in range(100):
            doc = _rand_doc(rng)
            entries, post = _random_edits(rng, doc, 5)
            assert _same_doc(hub_entries.revert_entries(post, entries), doc)
            assert _same_doc(hub_entries.apply_entries(doc, entries), post)

    def test_fragments_take_back_edits_that_are_not_in_the_written_bytes(self):
        doc = {"q": {"a": 1, "b": {"c": 2}}}
        later = [{"path": "q.b.c", "old": 2, "new": 9}]            # landed after the save
        store_doc = hub_entries.apply_entries(doc, later)
        frag = hub_entries.post_fragments([{"path": "q.b", "old": {}, "new": {"c": 2}}], store_doc, later)
        assert frag == {"q": {"b": {"c": 2}}}

    def test_an_element_of_a_long_list_is_a_change_of_the_list_holder(self):
        doc = {"a": {"w": list(range(20))}}
        post = copy.deepcopy(doc)
        post["a"]["w"][3] = 99
        rows = hub_entries.rows_for([{"path": "a.w.3", "old": 3, "new": 99}], post)
        assert [r.path for r in rows] == ["a.w"]          # S2: one holder, not w.3
        assert json.loads(rows[0].old_txt)["_array"] == 20

    def test_inputs_are_never_mutated(self):
        doc = {"a": {"b": 1, "c": [1, 2]}}
        snap = copy.deepcopy(doc)
        hub_entries.apply_entries(doc, [{"path": "a.b", "old": 1, "new": 2},
                                        {"path": "a.c", "old": [1, 2], "deleted": True}])
        hub_entries.revert_entries(doc, [{"path": "a.b", "old": 0, "new": 1}])
        assert doc == snap


# ======================================================================
# 2. the journal
# ======================================================================

def _wc_chip(tmp_path, state=None):
    live = tmp_path / "live"
    _write_chip(live, state or _state())
    wc = working_copy.create(tmp_path / "inst", live)
    return live, wc


def _stage(wc, mutate):
    s, w = safe_io.read_state_wiring(wc.working_folder)
    mutate(s)
    safe_io.write_state_wiring(wc.working_folder, s, w)


class TestJournal:
    def test_one_line_is_durable_before_the_live_write(self, tmp_path, monkeypatch):
        live, wc = _wc_chip(tmp_path)
        chip = tmp_path / "hist"
        _stage(wc, lambda s: s["qubits"]["qA1"].__setitem__("T1", 9e-5))
        seen = {}
        real_write = safe_io.write_state_wiring_bytes
        fsyncs = []
        real_fsync = os.fsync

        def spy_fsync(fd):
            fsyncs.append(fd)
            return real_fsync(fd)

        def spy_write(folder, sb, wb):
            if Path(folder) == live:
                seen["lines_at_write"] = (chip / "events.jsonl").read_text(encoding="utf-8").count("\n")
                seen["fsyncs_at_write"] = len(fsyncs)
            return real_write(folder, sb, wb)

        monkeypatch.setattr(os, "fsync", spy_fsync)
        monkeypatch.setattr(safe_io, "write_state_wiring_bytes", spy_write)
        p = hub.Pending(chip, "sm_apply", "human:operator", "apply",
                        entries=[{"path": "qubits.qA1.T1", "old": 2e-5, "new": 9e-5}])
        working_copy.apply_to_live(wc, record=p)
        assert seen["lines_at_write"] == 1, "the line exists BEFORE the chip is written"
        assert seen["fsyncs_at_write"] >= 1, "...and was fsync'd"
        assert p.landed
        ev = hub.Hub.for_chip(chip).events()
        assert len(ev) == 1 and ev[0]["id"] == p.id and "outcome" not in ev[0]
        assert ev[0]["base_hash"] != ev[0]["post_hash"]
        assert ev[0]["post_hash"] == working_copy.live_content_hash(wc)

    def test_an_unwritable_journal_means_nothing_is_written(self, tmp_path):
        live, wc = _wc_chip(tmp_path)
        chip = tmp_path / "hist"
        (chip / "events.jsonl").mkdir(parents=True)        # cannot be opened as a file
        before = (live / "state.json").read_bytes()
        _stage(wc, lambda s: s["qubits"]["qA1"].__setitem__("T1", 9e-5))
        with pytest.raises(hub.RecordError):
            working_copy.apply_to_live(wc, record=hub.Pending(chip, "sm_apply", "human", "apply",
                                                               entries=[]))
        assert (live / "state.json").read_bytes() == before

    def test_a_write_that_fails_after_its_line_is_marked_failed(self, tmp_path, monkeypatch):
        live, wc = _wc_chip(tmp_path)
        chip = tmp_path / "hist"
        _stage(wc, lambda s: s["qubits"]["qA1"].__setitem__("T1", 9e-5))

        def boom(*_a, **_k):
            raise PermissionError(13, "read-only share")

        monkeypatch.setattr(safe_io, "write_state_wiring_bytes", boom)
        p = hub.Pending(chip, "sm_apply", "human", "apply",
                        entries=[{"path": "qubits.qA1.T1", "old": 2e-5, "new": 9e-5}])
        with pytest.raises(PermissionError):
            working_copy.apply_to_live(wc, record=p)
        lines = hub.Hub.for_chip(chip).read()
        assert len(lines) == 2 and lines[1][2]["failed"] == p.id
        ev = hub.Hub.for_chip(chip).events()
        assert ev[0]["outcome"] == "failed" and "read-only" in ev[0]["error"]
        with HubStore(chip) as st:
            row = st.conn.execute("SELECT e.status, e.error, e.n_changes FROM events e "
                                  "JOIN sm_events s USING(eid) WHERE s.sm_id=?", (p.id,)).fetchone()
            assert row["status"] == "failed" and row["n_changes"] == 0 and row["error"]
            assert st.conn.execute("SELECT COUNT(*) FROM changes").fetchone()[0] == 0
            with pytest.raises(ValueError):
                st.state_at(st.conn.execute("SELECT eid FROM sm_events").fetchone()[0])

    def test_an_unverified_write_is_marked_failed(self, tmp_path, monkeypatch):
        live, wc = _wc_chip(tmp_path)
        chip = tmp_path / "hist"
        _stage(wc, lambda s: s["qubits"]["qA1"].__setitem__("T1", 9e-5))
        real = working_copy.live_content_hash
        calls = {"n": 0}

        def racing(w, **k):
            calls["n"] += 1
            return "someone-else" if calls["n"] >= 2 else real(w, **k)

        monkeypatch.setattr(working_copy, "live_content_hash", racing)
        p = hub.Pending(chip, "sm_apply", "human", "apply", entries=[{"path": "x", "old": 1, "new": 2}])
        with pytest.raises(safe_io.LiveFileError):
            working_copy.apply_to_live(wc, record=p)
        assert hub.Hub.for_chip(chip).events()[0]["outcome"] == "failed"

    def test_unmarkable_failure_rolls_the_line_back_out(self, tmp_path, monkeypatch):
        live, wc = _wc_chip(tmp_path)
        chip = tmp_path / "hist"
        h = hub.Hub.for_chip(chip)
        pre = h.record("sm_apply", "human", [], None, None, "apply")
        pre.landed()
        size0 = h.journal.stat().st_size
        rec = h.record("sm_apply", "human", [{"path": "a", "old": 1, "new": 2}], None, None, "apply")
        real_append = hub.Hub._append

        def no_more(self, obj):
            if "failed" in obj:
                raise hub.RecordError("disk full")
            return real_append(self, obj)

        monkeypatch.setattr(hub.Hub, "_append", no_more)
        rec.failed(OSError("write failed"))
        assert h.journal.stat().st_size == size0, "the failed write's line is gone"
        assert [e["id"] for e in h.events()] == [pre.id]

    def test_rewriting_the_same_content_is_not_an_event_and_costs_no_diff(self, tmp_path):
        chip = tmp_path / "hist"

        def never():
            raise AssertionError("a wholesale diff was computed for a no-change write")

        p = hub.Pending(chip, "sm_apply", "human", "apply", entries=lambda _post: never())
        assert p.commit(base_hash="H", post_state=(b"{}", b"{}"), post_hash="H", live=None) is None
        assert p.skipped and not (chip / "events.jsonl").exists()

    def test_a_write_with_no_chip_to_file_it_under_is_not_made(self, tmp_path):
        live, wc = _wc_chip(tmp_path)
        before = (live / "state.json").read_bytes()
        _stage(wc, lambda s: s["qubits"]["qA1"].__setitem__("T1", 9e-5))
        with pytest.raises(hub.RecordError):
            working_copy.apply_to_live(wc, record=hub.Pending(None, "sm_apply", "human", "apply",
                                                               entries=[]))
        assert (live / "state.json").read_bytes() == before

    def test_a_torn_last_line_is_skipped_and_the_next_line_starts_fresh(self, tmp_path):
        chip = tmp_path / "hist"
        h = hub.Hub.for_chip(chip)
        a = h.record("sm_apply", "human", [], None, None, "apply")
        a.landed()
        with open(h.journal, "ab") as f:
            f.write(b'{"v":1,"id":"torn","kind"')          # a crash mid-append
        b = h.record("sm_apply", "human", [], None, None, "apply")
        b.landed()
        assert [e["id"] for e in h.events()] == [a.id, b.id]

    def test_a_large_write_is_one_line_plus_one_blob_file_never_a_directory(self, tmp_path):
        chip = tmp_path / "hist"
        h = hub.Hub.for_chip(chip)
        big = [{"path": f"qubits.q{i}.w", "old": list(range(50)), "new": list(range(1, 51))}
               for i in range(2000)]
        rec = h.record("restore", "human", big, None, None, "restore_live")
        line = h.read()[0][2]
        assert line["entries"] is None and line["n"] == 2000
        assert h.entries_of(line) == big
        assert not [p for p in chip.iterdir() if p.is_dir()], \
            "history scans every sub-directory of a chip dir as a snapshot"
        rec.landed()

    def test_another_live_windows_line_waits_for_that_window(self, tmp_path, monkeypatch):
        """Two SM windows on one chip share the journal: a line whose writer
        is still a live SM process is that process's to decide -- never
        guessed here -- and its failure line, once written, decides it."""
        chip = tmp_path / "hist"
        h = hub.Hub.for_chip(chip)
        monkeypatch.setattr(hub, "_live_peer_pids", lambda _h: {424242})
        h._append({"v": 1, "id": "peer1", "kind": "sm_apply", "t_utc_us": 1, "t": "x", "actor": "human",
                   "src": "apply", "base_hash": "B", "post_hash": "P", "live": None, "pid": 424242, "n": 0,
                   "entries": []})
        assert hub.project(h) == 0
        with HubStore(chip) as st:
            assert st.conn.execute("SELECT COUNT(*) FROM sm_events").fetchone()[0] == 0
        h._append({"v": 1, "failed": "peer1", "t_utc_us": 2, "error": "the peer's write failed"})
        assert hub.project(h) == 1
        with HubStore(chip) as st:
            assert st.conn.execute("SELECT outcome FROM sm_events WHERE sm_id='peer1'").fetchone()[0] == "failed"

    def test_lines_nobody_can_vouch_for_are_decided_by_evidence(self, tmp_path):
        """A line with no failure mark from a writer that is gone (SM stopped
        mid-write): landed only if a later write was taken against its
        post-state or the chip holds it now; otherwise unconfirmed and the
        ledger claims nothing."""
        live, wc = _wc_chip(tmp_path)
        chip = tmp_path / "hist"
        h = hub.Hub.for_chip(chip)
        now_hash = working_copy.live_content_hash(wc)
        dead = 1                                            # never a live SM pid
        for lid, post, base in (("a1", "P1", "B0"), ("a2", "P2", "P1"), ("a3", "P9", "P2"),
                                ("a4", now_hash, "PX")):
            h._append({"v": 1, "id": lid, "kind": "sm_apply", "t_utc_us": 1, "t": "x", "actor": "human",
                       "src": "apply", "base_hash": base, "post_hash": post, "live": str(live),
                       "pid": dead, "n": 1, "entries": [{"path": "qubits.qA1.T1", "old": 1, "new": 2}]})
        hub.project(h)
        with HubStore(chip) as st:
            got = {r["sm_id"]: (r["outcome"], r["error"] is None) for r in st.conn.execute(
                "SELECT s.sm_id, s.outcome, e.error FROM sm_events s JOIN events e USING(eid)")}
        assert got["a1"] == ("landed", True)                # a2 was taken against P1
        assert got["a2"] == ("landed", True)                # a3 was taken against P2
        assert got["a3"][0] == "unconfirmed" and not got["a3"][1]
        assert got["a4"] == ("landed", True)                # the chip holds it now


# ======================================================================
# 3. the ledger
# ======================================================================

class TestLedger:
    def test_the_async_projector_projects_off_the_writer_thread(self, tmp_path):
        hub.set_inline(False)
        live, wc = _wc_chip(tmp_path)
        chip = tmp_path / "hist"
        _stage(wc, lambda s: s["qubits"]["qA1"].__setitem__("T1", 9e-5))
        main = threading.get_ident()
        seen = []
        real = hub.project

        def spy(h, proj=None):
            seen.append(threading.get_ident())
            return real(h, proj)

        try:
            hub.project = spy
            p = hub.Pending(chip, "sm_apply", "human", "apply",
                            entries=[{"path": "qubits.qA1.T1", "old": 2e-5, "new": 9e-5}])
            working_copy.apply_to_live(wc, record=p)
            assert hub.flush(30)
        finally:
            hub.project = real
        assert seen and main not in seen
        assert _ledger_of(chip, p.id)["rows"] == {"qubits.qA1.T1": (2e-5, 9e-5)}

    def test_state_at_equals_what_was_written_anchored_or_replayed(self, tmp_path, monkeypatch):
        monkeypatch.setattr(hub, "ANCHOR_EVERY", 3)
        live, wc = _wc_chip(tmp_path)
        chip = tmp_path / "hist"
        written = []
        for i in range(7):
            def mut(s, i=i):
                s["qubits"]["qA1"]["T1"] = 1e-5 * (i + 3)
                if i == 3:
                    s["qubits"]["qA2"]["new_block"] = {"a": [1, 2, 3], "b": {}}
                if i == 5:
                    s["qubits"]["qA1"]["wave"][4] = -1
            s0, w0 = safe_io.read_state_wiring(wc.working_folder)
            _stage(wc, mut)
            s1, w1 = safe_io.read_state_wiring(wc.working_folder)
            ents = hub_entries.rows_for  # noqa: F841 (exercise import)
            entries = []
            for path in ("qubits.qA1.T1",):
                entries.append({"path": path, "old": s0["qubits"]["qA1"]["T1"], "new": s1["qubits"]["qA1"]["T1"]})
            if i == 3:
                entries.append({"path": "qubits.qA2.new_block", "new": s1["qubits"]["qA2"]["new_block"],
                                "created": True})
            if i == 5:
                entries.append({"path": "qubits.qA1.wave.4", "old": 4, "new": -1})
            p = hub.Pending(chip, "sm_apply", "human", "apply", entries=entries)
            working_copy.apply_to_live(wc, record=p)
            written.append((p.id, rules.merged(s1, w1)))
        with HubStore(chip) as st:
            anchors = st.conn.execute("SELECT COUNT(*) FROM sm_anchors").fetchone()[0]
            assert 1 <= anchors < 7, "some events replay from entries, some are anchors"
            for sm_id, doc in written:
                eid = st.conn.execute("SELECT eid FROM sm_events WHERE sm_id=?", (sm_id,)).fetchone()[0]
                assert st.state_at(eid) == doc
        row = _ledger_of(chip, written[5][0])
        assert set(row["rows"]) == {"qubits.qA1.T1", "qubits.qA1.wave"}

    def test_the_ledger_rebuilds_from_the_journal_alone(self, tmp_path):
        live, wc = _wc_chip(tmp_path)
        chip = tmp_path / "hist"
        ids = []
        for t1 in (3e-5, 4e-5, 5e-5):
            s0, _ = safe_io.read_state_wiring(wc.working_folder)
            _stage(wc, lambda s, t1=t1: s["qubits"]["qA1"].__setitem__("T1", t1))
            p = hub.Pending(chip, "sm_apply", "human:operator", "apply",
                            entries=[{"path": "qubits.qA1.T1", "old": s0["qubits"]["qA1"]["T1"], "new": t1}])
            working_copy.apply_to_live(wc, record=p)
            ids.append(p.id)
        before = _dump_ledger(chip)
        for name in ("ledger.sqlite", "ledger.sqlite-wal", "ledger.sqlite-shm"):
            (chip / name).unlink(missing_ok=True)
        hub.project(hub.Hub.for_chip(chip))
        after = _dump_ledger(chip)
        assert [r[:4] for r in after["events"]] == [r[:4] for r in before["events"]]
        assert after["changes"] == before["changes"]

    def test_undo_marks_the_target_and_redo_puts_it_back(self, tmp_path):
        chip = tmp_path / "hist"
        h = hub.Hub.for_chip(chip)

        def ev(kind, units, undoes=None):
            r = h.record(kind, "human", [], None, None, kind, undoes=undoes, units=units)
            r.landed()
            return r.id

        e1 = ev("sm_apply", ["u1", "u2"])
        u1 = ev("undo", ["u2"], undoes=[{"event": e1, "units": ["u2"]}])
        assert _flags(chip, e1) & PARTLY_UNDONE and not _flags(chip, e1) & UNDONE
        u2 = ev("undo", ["u1"], undoes=[{"event": e1, "units": ["u1"]}])
        assert _flags(chip, e1) & UNDONE
        ev("redo", ["u1"], undoes=[{"event": u2, "units": None}])
        assert _flags(chip, u2) & UNDONE
        assert _flags(chip, e1) & PARTLY_UNDONE and not _flags(chip, e1) & UNDONE, \
            "the redo put u1 back: the apply is only partly undone again"
        assert not _flags(chip, u1) & (UNDONE | PARTLY_UNDONE)

    def test_an_sm_head_does_not_break_the_offline_builder(self, tmp_path):
        """S3's builder resumes on a ledger whose head is an SM event (no root,
        no shape): a run identical to the SM write is a zero-change event."""
        from quam_state_manager.core import hub_build
        live, wc = _wc_chip(tmp_path)
        chip = tmp_path / "hist"
        _stage(wc, lambda s: s["qubits"]["qA1"].__setitem__("T1", 7e-5))
        working_copy.apply_to_live(wc, record=hub.Pending(chip, "sm_apply", "human", "apply",
                                                           entries=[{"path": "qubits.qA1.T1", "old": 2e-5,
                                                                     "new": 7e-5}]))
        root = tmp_path / "data"
        run = root / "2030-01-01" / "#1_scan_120000"
        run.mkdir(parents=True)
        (run / "node.json").write_text(json.dumps({"created_at": "2030-01-01T12:00:00+00:00",
                                                   "metadata": {"name": "scan", "status": "finished"}}))
        (run / "quam_state").mkdir()
        for n in ("state.json", "wiring.json"):
            (run / "quam_state" / n).write_bytes((live / n).read_bytes())
        out = hub_build.build(root, chip)
        assert out["added"] == 1
        with HubStore(chip) as st:
            head = st.head()
            assert head["kind"] == "run" and head["n_changes"] == 0
            assert st.state_at(head["eid"]) == _live_doc({"live": live})


def _flags(chip, sm_id):
    with HubStore(chip) as st:
        return st.conn.execute("SELECT e.flags FROM events e JOIN sm_events s USING(eid) WHERE s.sm_id=?",
                               (sm_id,)).fetchone()[0]


def _ledger_of(chip, sm_id):
    with HubStore(chip) as st:
        eid = st.conn.execute("SELECT eid FROM sm_events WHERE sm_id=?", (sm_id,)).fetchone()[0]
        rows = st.conn.execute("SELECT p.path, c.num, c.txt, c.old_num, c.old_txt FROM changes c "
                               "JOIN paths p USING(pid) WHERE c.eid=?", (eid,)).fetchall()
        return {"rows": {r["path"]: (value(r["old_num"], r["old_txt"]), value(r["num"], r["txt"])) for r in rows}}


def _dump_ledger(chip):
    with HubStore(chip) as st:
        return {"events": [tuple(r) for r in st.conn.execute(
                    "SELECT kind, actor, src, status, n_changes, flags FROM events ORDER BY ord")],
                "changes": sorted(tuple(r) for r in st.conn.execute(
                    "SELECT e.ord, p.path, c.op, c.num, c.txt, c.old_num, c.old_txt FROM changes c "
                    "JOIN paths p USING(pid) JOIN events e USING(eid)"))}


# ======================================================================
# 4. the doors
# ======================================================================

class TestDoors:
    def test_apply_records_who_when_old_new(self, env):
        t0 = __import__("time").time_ns() // 1000
        _edit(env, "qubits.qA1.T1", "3.5e-5")
        r = env["client"].post("/state/apply-to-live", headers={"X-SM-Actor": "operator"})
        assert r.status_code == 200
        ev = _events(env)
        assert len(ev) == 1
        e = ev[0]
        assert (e["kind"], e["actor"], e["src"]) == ("sm_apply", "human:operator", "apply")
        assert e["t_utc_us"] >= t0
        assert e["entries"] == [{"path": "qubits.qA1.T1", "old": 2e-5, "new": 3.5e-5, "by": "human"}]
        led = _ledger(env, e["id"])
        assert led["rows"] == {"qubits.qA1.T1": (2e-5, 3.5e-5)}
        assert led["doc"] == _live_doc(env)
        assert led["status"] == "landed" and led["actor"] == "human:operator"

    def test_the_applied_units_name_the_event(self, env):
        _edit(env, "qubits.qA1.T1", "3.5e-5")
        env["client"].post("/state/apply-to-live")
        ev = _events(env)[0]
        ctx = _ctx(env)
        units = undo_journal.load(undo_journal.sidecar_path(env["app"].instance_path, ctx["path"]))
        assert [(u.get("meta") or {}).get("hub") for u in units] == [ev["id"]]
        assert ev["units"] == [u["id"] for u in units]

    def test_keep_mine_records_the_outside_value_it_replaced(self, env):
        _edit(env, "qubits.qA1.T1", "3.5e-5")
        s = json.loads((env["live"] / "state.json").read_text())
        s["qubits"]["qA2"]["T1"] = 9.9e-5                     # an experiment wrote live
        (env["live"] / "state.json").write_text(json.dumps(s))
        with env["app"].app_context():
            h0 = working_copy.live_content_hash(_ctx(env)["working_copy"])
        r = env["client"].post(f"/state/apply-to-live?force=1&expect_live_hash={h0}",
                               headers={"X-SM-Actor": "operator"})
        assert r.status_code == 200
        e = _events(env)[-1]
        assert e["src"] == "keep_mine" and e["base_hash"] == h0
        paths = {x["path"]: (x.get("old"), x.get("new")) for x in e["entries"]}
        assert paths["qubits.qA1.T1"] == (2e-5, 3.5e-5)
        assert paths["qubits.qA2.T1"] == (9.9e-5, 3.0e-5), "the value Keep mine overwrote is named"
        assert _ledger(env, e["id"])["doc"] == _live_doc(env)

    def test_an_edit_landing_after_the_save_is_not_recorded_as_written(self, env, monkeypatch):
        """/field/edit takes only the store lock: an edit can land between the
        press's save and its write. It is not in the bytes, so it is not in the
        event -- neither its entries nor its rows -- and the projector never
        parses the chip to find that out."""
        _edit(env, "qubits.qA1.T1", "3.5e-5")
        real = working_copy.apply_to_live

        def late_edit(wc, **kw):
            ctx = _ctx(env)
            ctx["modifier"].set_value("qubits.qA1.T1", 8.8e-5)   # the same leaf, after the save
            return real(wc, **kw)

        monkeypatch.setattr(working_copy, "apply_to_live", late_edit)
        monkeypatch.setattr(hub, "_parse_pair", lambda *a: (_ for _ in ()).throw(AssertionError("parsed the chip")))
        assert env["client"].post("/state/apply-to-live").status_code == 200
        e = _events(env)[-1]
        assert [x["path"] for x in e["entries"]] == ["qubits.qA1.T1"]
        led = _ledger(env, e["id"])
        assert led["rows"] == {"qubits.qA1.T1": (2e-5, 3.5e-5)}
        assert _live_doc(env)["qubits"]["qA1"]["T1"] == 3.5e-5
        assert led["doc"] == _live_doc(env)

    def test_keep_mine_over_nothing_lists_no_phantom_unit(self, env):
        """A forced push that overwrote nothing beyond the tray journals no
        force-overwrite unit -- so the event must not list one either (an
        event that names a unit that never existed could never be UNDONE)."""
        _edit(env, "qubits.qA1.T1", "3.5e-5")
        with env["app"].app_context():
            h0 = working_copy.live_content_hash(_ctx(env)["working_copy"])
        assert env["client"].post(f"/state/apply-to-live?force=1&expect_live_hash={h0}").status_code == 200
        e = _events(env)[-1]
        units = {u["id"] for u in undo_journal.load(
            undo_journal.sidecar_path(env["app"].instance_path, _ctx(env)["path"]))}
        assert len(e["units"]) == 1 and set(e["units"]) <= units
        env["client"].post("/undo")
        assert _flags(_chip_dir(env), e["id"]) & UNDONE

    def test_agent_apply_is_an_agent_event_with_its_plan(self, env):
        _edit(env, "qubits.qA1.T1", "4e-5", headers={"X-SM-Agent": "claude"})
        r = env["client"].post("/state/apply-to-live",
                               headers={"X-SM-Agent": "claude", "X-SM-Plan": "plan-7",
                                        "Accept": "application/json"})
        assert r.status_code == 200, r.data[:300]
        e = _events(env)[-1]
        assert (e["kind"], e["actor"], e["plan_id"]) == ("agent", "by_claude", "plan-7")
        assert e["entries"][0]["by"] == "by_claude"

    def test_approval_names_the_person_and_the_agent(self, env):
        from quam_state_manager.web import agent_api
        with env["app"].test_request_context("/"):
            out = agent_api._stage_writes(env["app"], str(env["live"]),
                                          [{"path": "qubits.qA1.T1", "old": 2e-5, "new": 4.5e-5}],
                                          "agent:k1", "by_claude", "plan-9", True, presser="human:operator")
        assert out["applied"], out
        e = _events(env)[-1]
        assert (e["kind"], e["actor"], e["plan_id"]) == ("agent", "human:operator", "plan-9")
        assert e["entries"] == [{"path": "qubits.qA1.T1", "old": 2e-5, "new": 4.5e-5, "by": "by_claude"}]

    def test_auto_apply_session_flush(self, env):
        assert env["client"].post("/auto-apply/arm").status_code == 200
        _edit(env, "qubits.qA1.T1", "5e-5")
        env["client"].post("/state/apply-to-live")
        e = _events(env)[-1]
        assert e["src"] == "auto_apply" and e["entries"][0]["new"] == 5e-5

    def test_pull_and_apply(self, env):
        _edit(env, "qubits.qA1.T1", "3.3e-5")
        r = env["client"].post("/state/sync", data={"mode": "apply"})
        assert r.get_json()["status"] == "ok"
        e = _events(env)[-1]
        assert e["src"] == "pull_apply" and e["entries"][0]["new"] == 3.3e-5
        assert r.get_json()["hub_event"] == e["id"]

    def test_staged_version_then_apply_records_the_wholesale_difference(self, env):
        c = env["client"]
        _edit(env, "qubits.qA1.T1", "3.5e-5")
        c.post("/state/apply-to-live")
        ctx = _ctx(env)
        with env["app"].app_context():
            snaps = routes_mod._history().list_snapshots(ctx["path"])
        pre = [s for s in snaps if s.kind == "backup"][0].timestamp
        assert c.post(f"/state-history/{pre}/stage").status_code == 200
        r = c.post("/state/apply-to-live", headers={"X-SM-Actor": "operator"})
        assert r.status_code == 200
        e = _events(env)[-1]
        assert e["src"] == "apply_staged" and e["ref"] == {"snapshot": pre}
        assert e["entries"] == [{"path": "qubits.qA1.T1", "old": 3.5e-5, "new": 2e-5}]
        assert _ledger(env, e["id"])["doc"] == _live_doc(env)
        # the event lists exactly the journal units that exist: the docs/160 B
        # wholesale unit, built before the write and committed after it
        units = {u["id"]: u for u in undo_journal.load(
            undo_journal.sidecar_path(env["app"].instance_path, _ctx(env)["path"]))}
        assert e["units"] and set(e["units"]) <= set(units)
        assert all((units[u].get("meta") or {}).get("hub") == e["id"] for u in e["units"])

    def test_restore_live(self, env):
        c = env["client"]
        _edit(env, "qubits.qA1.T1", "3.5e-5")
        c.post("/state/apply-to-live")
        ctx = _ctx(env)
        with env["app"].app_context():
            pre = [s for s in routes_mod._history().list_snapshots(ctx["path"]) if s.kind == "backup"][0].timestamp
        r = c.post(f"/state-history/{pre}/restore-live?force=1")
        assert r.status_code == 200, r.data[:300]
        e = _events(env)[-1]
        assert (e["kind"], e["src"]) == ("restore", "restore_live") and e["ref"]["snapshot"] == pre
        assert {x["path"]: (x["old"], x["new"]) for x in e["entries"]} == {"qubits.qA1.T1": (3.5e-5, 2e-5)}
        assert _ledger(env, e["id"])["doc"] == _live_doc(env)

    def test_dataset_apply_to_chip_carries_the_run(self, env):
        c = env["client"]
        root = env["tmp"] / "data"
        run = root / "2026-12-30" / "#31_08_spec_010000"
        run.mkdir(parents=True)
        (run / "node.json").write_text(json.dumps({
            "metadata": {"name": "08_spec", "status": "successful", "run_start": "2026-12-30T01:00:00",
                         "run_end": "2026-12-30T01:00:01"},
            "data": {"parameters": {"model": {"qubits": ["qA1"]}}, "outcomes": {}},
            "id": 31, "parents": [], "created_at": "2026-12-30T01:00:00"}), encoding="utf-8")
        (run / "data.json").write_text("{}", encoding="utf-8")
        _write_chip(run / "quam_state", _state(f01=4.9e9))
        c.post("/workspace/add", data={"folder": str(root)})
        uid = f"{routes_mod._folder_key(root)}:31"
        r = c.post(f"/dataset/{uid}/load-state?apply=1", headers={"X-SM-Actor": "operator"})
        assert r.status_code == 200, r.data[:300]
        e = _events(env)[-1]
        assert (e["src"], e["run_uid"], e["actor"]) == ("dataset_apply", uid, "human:operator")
        assert e["entries"] == [{"path": "qubits.qA1.f_01", "old": 5.0e9, "new": 4.9e9}]
        assert _ledger(env, e["id"])["run_uid"] if False else True
        with HubStore(_chip_dir(env)) as st:
            assert st.conn.execute("SELECT run_uid FROM sm_events WHERE sm_id=?", (e["id"],)).fetchone()[0] == uid
        # the shared apply core lists the docs/160 B unit it journals -- and only it
        units = {u["id"]: u for u in undo_journal.load(
            undo_journal.sidecar_path(env["app"].instance_path, _ctx(env)["path"]))}
        assert e["units"] and set(e["units"]) <= set(units)
        assert all((units[u].get("meta") or {}).get("hub") == e["id"] for u in e["units"])

    def test_ctrl_z_and_ctrl_shift_z_on_the_chip(self, env):
        c = env["client"]
        _edit(env, "qubits.qA1.T1", "3.5e-5")
        _edit(env, "qubits.qA2.T1", "4.5e-5")
        c.post("/state/apply-to-live")
        apply_ev = _events(env)[-1]
        assert len(apply_ev["units"]) == 2
        r = c.post("/undo")
        assert json.loads(r.headers["HX-Trigger"])["cellsReverted"]["live"] is True
        undo_ev = _events(env)[-1]
        assert (undo_ev["kind"], undo_ev["src"]) == ("undo", "ctrl_z")
        assert undo_ev["undoes"] == [{"event": apply_ev["id"], "units": [apply_ev["units"][1]]}]
        assert undo_ev["entries"] == [{"path": "qubits.qA2.T1", "old": 4.5e-5, "new": 3.0e-5, "by": "human"}]
        assert _flags(_chip_dir(env), apply_ev["id"]) & PARTLY_UNDONE
        c.post("/undo")
        assert _flags(_chip_dir(env), apply_ev["id"]) & UNDONE
        r = c.post("/redo")
        redo_ev = _events(env)[-1]
        assert redo_ev["kind"] == "redo"
        assert redo_ev["undoes"] == [{"event": _events(env)[-2]["id"], "units": None}]
        assert not _flags(_chip_dir(env), apply_ev["id"]) & UNDONE
        assert _ledger(env, redo_ev["id"])["doc"] == _live_doc(env)
        # the redo puts the chip back exactly where the first undo left it
        assert _flags(_chip_dir(env), redo_ev["id"]) & REVERTS_TO_EARLIER

    def test_the_applied_log_revert_names_what_it_takes_back(self, env):
        """The applied log's revert button stages the inverse under ``alr:<unit>``; the
        Apply that lands it is recorded as taking back that unit's event."""
        c = env["client"]
        _edit(env, "qubits.qA1.T1", "3.5e-5")
        c.post("/state/apply-to-live")
        apply_ev = _events(env)[-1]
        uid = apply_ev["units"][0]
        r = c.post("/auto-apply/revert", data={"unit_id": uid})
        assert r.status_code == 200, r.data[:300]
        c.post("/state/apply-to-live")
        e = _events(env)[-1]
        assert e["undoes"] == [{"event": apply_ev["id"], "units": [uid]}]
        assert e["entries"][0]["new"] == 2e-5
        assert _flags(_chip_dir(env), apply_ev["id"]) & UNDONE

    def test_saved_then_applied_records_the_saved_edits(self, env):
        c = env["client"]
        _edit(env, "qubits.qA1.T1", "3.5e-5")
        assert c.post("/save").status_code == 200
        assert _events(env) == [], "a save writes the working copy, not the chip"
        c.post("/state/apply-to-live")
        e = _events(env)[-1]
        assert e["entries"] == [{"path": "qubits.qA1.T1", "old": 2e-5, "new": 3.5e-5}]
        units = undo_journal.load(undo_journal.sidecar_path(env["app"].instance_path, _ctx(env)["path"]))
        assert (units[-1].get("meta") or {}).get("hub") == e["id"] and units[-1]["id"] in e["units"]

    def test_autofit_writer(self, tmp_path):
        import threading as _t
        from quam_state_manager.core.autofit import writer
        from quam_state_manager.core.loader import QuamStore
        from quam_state_manager.core.modifier import Modifier
        from quam_state_manager.core.saver import Saver
        live, wc = _wc_chip(tmp_path)
        store = QuamStore(wc.working_folder)
        chip = tmp_path / "hist"
        handle = writer.ChipHandle(store=store, modifier=Modifier(store), saver=Saver(store), wc=wc,
                                   build_lock=_t.RLock(), live_path=str(live), hub_dir=chip,
                                   hub_plan="autofit:p1")
        out = writer.apply_rows(handle, [{"path": "qubits.qA1.f_01", "value": 5.2e9}], apply_live=True,
                                label="qubit_spec")
        assert out.ok and out.action == "applied"
        e = hub.Hub.for_chip(chip).events()[-1]
        assert (e["kind"], e["actor"], e["plan_id"], e["src"]) == (
            "autofit", "autofit", "autofit:p1", "autofit_apply:qubit_spec")
        assert e["entries"] == [{"path": "qubits.qA1.f_01", "old": 5.0e9, "new": 5.2e9, "by": "autofit"}]
        out = writer.revert_patches(handle, [{"op": "replace", "path": "/quam/qubits/qA1/f_01",
                                              "old": 5.0e9, "value": 5.2e9}], apply_live=True, label="rej")
        assert out.ok
        assert hub.Hub.for_chip(chip).events()[-1]["src"] == "autofit_revert:rej"

    def test_cli_set_save(self, tmp_path):
        from typer.testing import CliRunner
        from quam_state_manager.cli import app as cli_app
        from quam_state_manager.core.history import HistoryManager
        live = tmp_path / "chips" / "live"
        _write_chip(live, _state())
        inst = tmp_path / "inst"
        res = CliRunner().invoke(cli_app, ["set", "qubits.qA1.T1", "6e-5", "-f", str(live), "--save",
                                           "--instance", str(inst)])
        assert res.exit_code == 0, res.output
        chip = HistoryManager(inst).resolve_chip_dir(live)[0]
        e = hub.Hub.for_chip(chip).events()[-1]
        assert (e["actor"], e["src"]) == ("cli", "cli_set")
        assert e["entries"] == [{"path": "qubits.qA1.T1", "old": 2e-5, "new": 6e-5, "by": "human"}]
        led = _ledger_of(chip, e["id"])
        assert led["rows"] == {"qubits.qA1.T1": (2e-5, 6e-5)}


class TestCatchUp:
    def test_opening_a_chip_projects_what_its_journal_holds(self, env, tmp_path):
        """A line the ledger never got (SM stopped right after the write) is
        projected when the chip is opened again -- no page visit needed."""
        c = env["client"]
        chip = _chip_dir(env)
        with env["app"].app_context():
            now = working_copy.live_content_hash(_ctx(env)["working_copy"])
        hub.Hub.for_chip(chip)._append({
            "v": 1, "id": "orphan1", "kind": "sm_apply", "t_utc_us": 1, "t": "x", "actor": "human:operator",
            "src": "apply", "base_hash": "B", "post_hash": now, "live": str(env["live"]), "pid": 1, "n": 1,
            "entries": [{"path": "qubits.qA1.T1", "old": 1e-5, "new": 2e-5}]})
        other = tmp_path / "chips" / "other"
        _write_chip(other, _state(t1=7e-5), {"network": {"host": "127.0.0.1", "cluster_name": "C9"}})
        assert c.post("/load", data={"folder": str(other)}).status_code in (200, 302)
        with env["app"].app_context():
            for k in list(routes_mod._quam_cache.keys()):
                routes_mod._quam_cache.pop(k, None)          # a fresh build, as after a restart
        assert c.post("/load", data={"folder": str(env["live"])}).status_code in (200, 302)
        hub.flush(30)
        with HubStore(chip) as st:
            row = st.conn.execute("SELECT s.outcome, e.actor FROM sm_events s JOIN events e USING(eid) "
                                  "WHERE s.sm_id='orphan1'").fetchone()
        assert row is not None and tuple(row) == ("landed", "human:operator")


class TestNotDoors:
    """What is NOT an SM write to the chip records nothing (docs/271)."""

    def test_take_live_records_nothing(self, env):
        _edit(env, "qubits.qA1.T1", "3.5e-5")
        r = env["client"].post("/state/sync", data={"mode": "discard", "force": "1"})
        assert r.get_json()["status"] == "ok"
        assert _events(env) == []

    def test_a_refused_apply_records_nothing(self, env):
        _edit(env, "qubits.qA1.T1", "3.5e-5")
        s = json.loads((env["live"] / "state.json").read_text())
        s["qubits"]["qA2"]["T1"] = 9.9e-5
        (env["live"] / "state.json").write_text(json.dumps(s))
        r = env["client"].post("/state/apply-to-live", headers={"Accept": "application/json"})
        assert r.status_code == 409
        assert not _chip_dir(env).joinpath("events.jsonl").exists() or _events(env) == []

    def test_a_refusal_found_after_the_save_records_nothing(self, env, monkeypatch):
        _edit(env, "qubits.qA1.T1", "3.5e-5")
        monkeypatch.setattr(routes_mod, "_apply_preflight", lambda ctx: False)
        s = json.loads((env["live"] / "state.json").read_text())
        s["qubits"]["qA2"]["T1"] = 9.9e-5
        (env["live"] / "state.json").write_text(json.dumps(s))
        env["client"].post("/state/apply-to-live", headers={"Accept": "application/json"})
        assert _events(env) == []

    def test_a_no_op_apply_records_nothing(self, env):
        r = env["client"].post("/state/apply-to-live")
        assert r.status_code == 200
        assert _events(env) == []

    def test_reconcile_adopt_and_auto_sync_pull_record_nothing(self, env):
        s = json.loads((env["live"] / "state.json").read_text())
        s["qubits"]["qA2"]["T1"] = 9.9e-5
        (env["live"] / "state.json").write_text(json.dumps(s))
        os.utime(env["live"] / "state.json", (1, 1))
        with env["app"].test_request_context("/"):
            ctx = routes_mod._active_ctx()
            routes_mod._reconcile_cached_quam_ctx(ctx["path"], ctx, auto_adopt=True)
        env["client"].post("/auto-sync/pull")
        assert not _chip_dir(env).joinpath("events.jsonl").exists() or _events(env) == []

    def test_a_failed_live_write_is_marked_and_claims_no_rows(self, env, monkeypatch):
        _edit(env, "qubits.qA1.T1", "3.5e-5")
        live = env["live"]
        real = safe_io.write_state_wiring_bytes

        def ro(folder, *a, **k):
            if Path(folder) == live:
                raise PermissionError(13, "read-only")
            return real(folder, *a, **k)

        monkeypatch.setattr(safe_io, "write_state_wiring_bytes", ro)
        r = env["client"].post("/state/apply-to-live")
        assert r.status_code == 500
        e = _events(env)[-1]
        assert e["outcome"] == "failed"
        led = _ledger(env, e["id"])
        assert led["status"] == "failed" and led["rows"] == {} and led["doc"] is None


# ======================================================================
# 5. fault injection: a door that does not record breaks the build
# ======================================================================

def _innermost(name: str):
    """Like :func:`_calls`, with the INNERMOST enclosing function."""
    out = {}
    for path in sorted(PKG.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))

        def visit(node, fn):
            for child in ast.iter_child_nodes(node):
                cur = child.name if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)) else fn
                if isinstance(child, ast.Call):
                    f = child.func
                    nm = f.attr if isinstance(f, ast.Attribute) else (f.id if isinstance(f, ast.Name) else None)
                    if nm == name:
                        out[(path.relative_to(PKG).as_posix(), child.lineno, child.col_offset)] = (cur, child)
                visit(child, cur)

        visit(tree, "<module>")
    return [(k[0], v[0], v[1]) for k, v in sorted(out.items())]


#: The live-write doors and the event each records. A new ``apply_to_live``
#: call site is not in this table, so this file fails until it is added --
#: and it must name ``record=`` to be added at all.
APPLY_DOORS = {
    ("core/autofit/writer.py", "_apply_live_with_one_retry"): "autofit (apply/revert/restore)",
    ("web/routes.py", "state_history_restore_live"): "restore",
    ("web/routes.py", "_sync_pull_apply_to_live"): "pull & apply / staged apply / dataset apply / Ctrl+Z / redo",
    ("web/routes.py", "state_apply_to_live"): "apply / keep mine / auto-apply / agent / approval",
}

#: ``.save(`` calls on a Saver. Only one lands on a LIVE chip (the CLI's,
#: recorded write-ahead); every other writes a working copy, whose content
#: reaches the chip only through one of the APPLY_DOORS.
SAVER_SAVES = {
    ("cli.py", "save"): "unreachable for a write: a freshly loaded store has an empty log",
    ("core/autofit/writer.py", "apply_rows"): "working copy; live via _apply_live_with_one_retry",
    ("core/autofit/writer.py", "_apply_live_with_one_retry"): "working copy (re-stage after a pull)",
    ("core/autofit/writer.py", "revert_patches"): "working copy; live via _apply_live_with_one_retry",
    ("core/autofit/writer.py", "restore_values"): "working copy; live via _apply_live_with_one_retry",
    ("core/hub.py", "record_direct_save"): "the CLI's LIVE save, journalled write-ahead around it",
    ("web/routes.py", "_press_save"): "working copy, before an apply door",
    ("web/routes.py", "save"): "working copy (/save); the next apply records it",
    ("web/routes.py", "_rollback_walk_step"): "working copy (a refused walk step put back)",
    ("web/agent_api.py", "_take_back_saved"): "working copy (a refused approval put back)",
}

#: Direct state/wiring pair writes. None of them is a live chip except the
#: working copy's own apply (inside apply_to_live) and the autofit simulator's
#: synthetic chip.
PAIR_WRITES = {
    ("core/working_copy.py", "apply_to_live"): "THE live write -- journalled write-ahead",
    ("core/working_copy.py", "create"): "working copy seed",
    ("core/working_copy.py", "restore_working_bytes"): "working copy",
    ("core/working_copy.py", "_try_sync"): "working copy (pull restore)",
    ("core/working_copy.py", "sync_from_live"): "working copy (pull)",
    ("core/history.py", "check_and_snapshot"): "history snapshot dir",
    ("core/history.py", "_ingest_entries_into"): "history snapshot dir",
    ("core/autofit/simbackend.py", "run_step"): "the simulator's synthetic chip (instance/autofit/sim)",
    ("web/routes.py", "_reconcile_cached_quam_ctx"): "working copy (C29 re-persist)",
    ("web/routes.py", "state_history_stage"): "working copy (stage)",
    ("web/routes.py", "state_history_restore_live"): "working copy, then apply_to_live(record=...)",
    ("web/routes.py", "auto_sync_pull"): "working copy (re-persist)",
    ("web/routes.py", "_take_live_backup"): "a temp folder captured as a backup version",
    ("web/routes.py", "_autofit_start_sim"): "the simulator's synthetic chip",
    ("web/routes.py", "dataset_load_state"): "working copy (stage a run's state)",
    ("web/routes.py", "compare_hub_stash"): "a compare stash folder",
}


class TestEveryDoorRecords:
    def test_every_apply_to_live_call_names_its_record(self):
        sites = [(f, fn, n) for f, fn, n in _innermost("apply_to_live")]
        assert sites, "the scan found nothing -- it is broken, not the code clean"
        found = {}
        for f, fn, node in sites:
            kw = {k.arg: k.value for k in node.keywords}
            assert "record" in kw, f"{f}:{node.lineno} ({fn}) writes the live chip without record="
            assert not (isinstance(kw["record"], ast.Constant) and kw["record"].value is None), \
                f"{f}:{node.lineno} ({fn}) passes record=None"
            found.setdefault((f, fn), 0)
            found[(f, fn)] += 1
        unknown = set(found) - set(APPLY_DOORS)
        assert not unknown, f"new live-write door(s) {unknown}: record them and list them here"
        assert set(APPLY_DOORS) <= set(found), f"door(s) gone: {set(APPLY_DOORS) - set(found)}"

    def test_every_saver_save_is_classified(self):
        sites = {(f, fn) for f, fn, node in _innermost("save")
                 if (isinstance(node.func, ast.Attribute)
                     and (isinstance(node.func.value, ast.Name) and node.func.value.id in ("saver",)
                          or isinstance(node.func.value, ast.Attribute) and node.func.value.attr == "saver"
                          or isinstance(node.func.value, ast.Subscript)
                          and getattr(node.func.value.slice, "value", None) == "saver"))}
        assert sites, "the scan found nothing"
        unknown = sites - set(SAVER_SAVES)
        assert not unknown, (f"new Saver.save call(s) {unknown}: if it lands on a live chip it must be "
                             "journalled (hub.record_direct_save); classify it here either way")

    def test_every_direct_pair_write_is_classified(self):
        sites = {(f, fn) for name in ("write_state_wiring", "write_state_wiring_bytes")
                 for f, fn, _ in _innermost(name) if f != "core/safe_io.py"}
        assert sites
        unknown = sites - set(PAIR_WRITES)
        assert not unknown, (f"new state/wiring writer(s) {unknown}: a live chip is written only by "
                             "apply_to_live(record=...); classify it here")

    def test_every_named_state_file_write_is_classified(self):
        """Writes that name state.json / wiring.json outright (not through the
        pair writers): none is a live chip today. A new one fails here."""
        known = {("core/autofit/synth.py", "synth_run"): "a synthetic sim run folder",
                 ("core/regenerate.py", "run_regenerate"): "a NEW folder (Re-generate's output)",
                 ("generator/run_generate_config.py", "_retry_loop"): "a scratch folder (config preview)"}
        found = set()
        for path in sorted(PKG.rglob("*.py")):
            src = path.read_text(encoding="utf-8")
            tree = ast.parse(src)
            lines = src.splitlines()

            def visit(node, fn):
                for child in ast.iter_child_nodes(node):
                    cur = child.name if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)) else fn
                    if isinstance(child, ast.Call):
                        f = child.func
                        nm = f.attr if isinstance(f, ast.Attribute) else getattr(f, "id", None)
                        if nm in ("atomic_write_json", "write_text", "write_bytes", "copy2", "copy", "copyfile",
                                  "replace", "move"):
                            seg = " ".join(lines[child.lineno - 1:child.end_lineno])
                        else:
                            seg = ""
                        if '"state.json"' in seg or '"wiring.json"' in seg:
                            found.add((path.relative_to(PKG).as_posix(), cur))
                    visit(child, cur)

            visit(tree, "<module>")
        assert found, "the scan found nothing"
        unknown = found - set(known)
        assert not unknown, f"new state-file writer(s) {unknown}: if one writes a live chip it must record"

    def test_every_chip_handle_names_its_ledger(self):
        for f, fn, node in _innermost("ChipHandle"):
            assert "hub_dir" in {k.arg for k in node.keywords}, \
                f"{f}:{node.lineno} ({fn}) builds an autofit ChipHandle without hub_dir="

    def test_the_real_autofit_handle_has_a_ledger(self):
        """The simulator may pass hub_dir=None; the real chassis must not."""
        src = (PKG / "web" / "routes.py").read_text(encoding="utf-8")
        tree = ast.parse(src)
        fn = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == "_autofit_start_real")
        call = next(n for n in ast.walk(fn) if isinstance(n, ast.Call)
                    and getattr(n.func, "id", None) == "ChipHandle")
        kw = {k.arg: k.value for k in call.keywords}
        assert not (isinstance(kw["hub_dir"], ast.Constant) and kw["hub_dir"].value is None)
