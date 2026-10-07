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
* fault injection: every call site that can put bytes into a chip folder is
  classified per site (apply / save / pair write / named file write, aliases
  resolved), seven planted doors are each reported, and a door the scan misses
  still reaches ``apply_to_live`` with no record -- refused in strict mode;
* the review round (docs/271 "Review round"): two windows and two processes
  on one chip, the journal under concurrent appends, content the change log
  does not name, the outcome settle, rebuild from the journal alone, offsets,
  a bad line, discarded saves, the request thread's cost, one equality.
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
        assert len(ev) == 1 and ev[0]["id"] == p.id
        # review P1-3: the outcome is in the journal too -- a landed line
        # AFTER the write (a rebuild from the journal alone agrees)
        assert ev[0]["outcome"] == "landed"
        marks = [o for _, _, o in hub.Hub.for_chip(chip).read() if "landed" in o]
        assert [m["landed"] for m in marks] == [p.id]
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

    def test_an_overwritten_write_is_left_to_evidence(self, tmp_path, monkeypatch):
        """Our bytes land, then an experiment's save replaces them before the
        verify read: the apply raises (unverified), the line is NOT marked
        failed (the chip holds neither the old content nor ours -- nobody can
        tell from here), and with no evidence the ledger claims nothing."""
        live, wc = _wc_chip(tmp_path)
        chip = tmp_path / "hist"
        _stage(wc, lambda s: s["qubits"]["qA1"].__setitem__("T1", 9e-5))
        real_write = safe_io.write_state_wiring_bytes

        def write_then_overwritten(folder, sb, wb):
            real_write(folder, sb, wb)
            if Path(folder) == live:
                s = json.loads((live / "state.json").read_text())
                s["qubits"]["qA2"]["T1"] = 7.7e-5
                (live / "state.json").write_text(json.dumps(s))

        monkeypatch.setattr(safe_io, "write_state_wiring_bytes", write_then_overwritten)
        p = hub.Pending(chip, "sm_apply", "human", "apply", entries=[{"path": "x", "old": 1, "new": 2}])
        with pytest.raises(safe_io.LiveFileError):
            working_copy.apply_to_live(wc, record=p)
        assert not any(o.get("failed") == p.id for _, _, o in hub.Hub.for_chip(chip).read())
        with HubStore(chip) as st:
            assert st.conn.execute("SELECT outcome FROM sm_events WHERE sm_id=?",
                                   (p.id,)).fetchone()[0] == "unconfirmed"

    def test_a_write_found_unchanged_is_marked_failed(self, tmp_path, monkeypatch):
        """The write raised and the chip still holds exactly what it held
        before: it did not land -- marked failed, the ledger never claims it."""
        live, wc = _wc_chip(tmp_path)
        chip = tmp_path / "hist"
        _stage(wc, lambda s: s["qubits"]["qA1"].__setitem__("T1", 9e-5))

        def refused(folder, sb, wb):
            raise PermissionError(13, "access denied")

        monkeypatch.setattr(safe_io, "write_state_wiring_bytes", refused)
        p = hub.Pending(chip, "sm_apply", "human", "apply", entries=[{"path": "x", "old": 1, "new": 2}])
        with pytest.raises(PermissionError):
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
        monkeypatch.setattr(hub, "_pid_alive", lambda pid: pid == 424242)
        import time as _t
        h._append({"v": 1, "id": "peer1", "kind": "sm_apply", "t_utc_us": _t.time_ns() // 1000, "t": "x",
                   "actor": "human",
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

    def test_plain_apply_is_recorded_as_an_apply(self, env):
        """docs/301 F16: the sync panel's Apply over an unchanged live chip
        pulls nothing in -- it was recorded as "Pull & apply (merge)"."""
        _edit(env, "qubits.qA1.T1", "3.3e-5")
        r = env["client"].post("/state/sync", data={"mode": "apply"})
        assert r.get_json()["status"] == "ok" and not r.get_json()["pulled_other_changes"]
        e = _events(env)[-1]
        assert e["src"] == "apply" and e["entries"][0]["new"] == 3.3e-5
        assert r.get_json()["hub_event"] == e["id"]

    def test_pull_and_apply(self, env):
        """The same press when the live chip moved meanwhile: the pull merges
        the outside change in, and the record says so."""
        _edit(env, "qubits.qA1.T1", "3.3e-5")
        st = json.loads((env["live"] / "state.json").read_text(encoding="utf-8"))
        st["qubits"]["qA1"]["f_01"] = 5.05e9
        (env["live"] / "state.json").write_text(json.dumps(st), encoding="utf-8")
        r = env["client"].post("/state/sync", data={"mode": "apply"})
        assert r.get_json()["status"] == "ok" and r.get_json()["pulled_other_changes"]
        e = _events(env)[-1]
        assert e["src"] == "pull_apply"
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

#: Every call site that can put bytes into a chip folder, per (file, innermost
#: function, kind) with its exact count. Kinds:
#:   apply      -- ``apply_to_live`` or any alias of it (``as``-import, assignment)
#:   unrecorded -- ``hub.unrecorded(...)``: a write that deliberately records nothing
#:   save       -- any ``.save(`` whose receiver is not a known non-chip store
#:   pair       -- ``safe_io.write_state_wiring[_bytes]`` outside safe_io
#:   named      -- a file write / copy / move whose arguments or receiver name a
#:                 chip file or folder (state.json, wiring.json, *live*, state_path ...)
#: A new site, or a second call in a classified function, is not in this
#: table, so this file fails until someone decides what the new write is.
DOOR_SITES = {
    # ---- the live-write doors: each names its record
    ("core/autofit/writer.py", "_apply_live_with_one_retry", "apply"): (2, "autofit apply/revert/restore (+ its one retry)"),
    ("web/routes.py", "_sync_pull_apply_to_live", "apply"): (1, "pull & apply / staged apply / dataset apply / Ctrl+Z / redo"),
    ("web/routes.py", "state_apply_to_live", "apply"): (1, "apply / keep mine / auto-apply / agent / approval"),
    ("web/routes.py", "state_history_restore_live", "apply"): (1, "restore"),
    # ---- the one documented record-nothing: the simulator's synthetic chip
    ("core/autofit/writer.py", "_pending", "unrecorded"): (1, "a handle with no chip ledger (simulator)"),
    # ---- saves
    ("cli.py", "save", "save"): (1, "a fresh store: an empty log writes nothing"),
    ("core/autofit/writer.py", "_save", "save"): (1, "working copy; live via _apply_live_with_one_retry"),
    ("core/hub.py", "record_direct_save", "save"): (1, "the CLI's LIVE save, journalled write-ahead around it"),
    ("generator/run_build.py", "run_build", "save"): (1, "a NEW chip folder (Generate's output)"),
    ("generator/run_experiment.py", "_persist_node_state", "save"): (1, "the run's scratch copy (docs/245)"),
    ("web/agent_api.py", "_take_back_saved", "save"): (1, "working copy (a refused approval put back)"),
    ("web/routes.py", "_press_save", "save"): (1, "working copy, before an apply door"),
    ("web/routes.py", "_rollback_walk_step", "save"): (1, "working copy (a refused walk step put back)"),
    ("web/routes.py", "save", "save"): (1, "working copy (/save); the next apply records it"),
    # ---- direct pair writes
    ("core/working_copy.py", "apply_to_live", "pair"): (1, "THE live write -- journalled write-ahead"),
    ("core/working_copy.py", "create", "pair"): (1, "working copy seed"),
    ("core/working_copy.py", "restore_working_bytes", "pair"): (1, "working copy"),
    ("core/working_copy.py", "_try_sync", "pair"): (1, "working copy (pull restore)"),
    ("core/working_copy.py", "sync_from_live", "pair"): (1, "working copy (pull)"),
    ("core/history.py", "check_and_snapshot", "pair"): (1, "history snapshot dir"),
    ("core/history.py", "_ingest_entries_into", "pair"): (1, "history snapshot dir"),
    ("core/autofit/simbackend.py", "run_step", "pair"): (1, "the simulator's synthetic chip"),
    ("web/routes.py", "_reconcile_cached_quam_ctx", "pair"): (1, "working copy (C29 re-persist)"),
    ("web/routes.py", "state_history_stage", "pair"): (1, "working copy (stage)"),
    ("web/routes.py", "state_history_restore_live", "pair"): (1, "working copy, then apply_to_live(record=...)"),
    ("web/routes.py", "auto_sync_pull", "pair"): (2, "working copy (re-persist)"),
    ("web/routes.py", "_take_live_backup", "pair"): (1, "a temp folder captured as a backup version"),
    ("web/routes.py", "_autofit_start_sim", "pair"): (1, "the simulator's synthetic chip"),
    ("web/routes.py", "dataset_load_state", "pair"): (1, "working copy (stage a run's state)"),
    ("web/routes.py", "compare_hub_stash", "pair"): (1, "a compare stash folder"),
    # ---- named chip-file writes
    ("core/autofit/replay.py", "evaluate_run", "named"): (1, "a replay sandbox (copy of a run's quam_state)"),
    ("core/autofit/synth.py", "synth_run", "named"): (2, "a synthetic sim run folder"),
    ("core/entity_notes.py", "delete", "named"): (1, "an instance sidecar that NAMES the live folder"),
    ("core/entity_notes.py", "readdress", "named"): (1, "an instance sidecar that NAMES the live folder"),
    ("core/entity_notes.py", "save", "named"): (1, "an instance sidecar that NAMES the live folder"),
    ("core/regenerate.py", "run_regenerate", "named"): (4, "a NEW folder (Re-generate's output)"),
    ("core/type_policy.py", "delete_assignment", "named"): (1, "an instance sidecar that NAMES the live folder"),
    ("core/type_policy.py", "save_assignment", "named"): (1, "an instance sidecar that NAMES the live folder"),
    ("generator/run_build.py", "_inject_qdac_state", "named"): (1, "the NEW folder Generate builds"),
    ("generator/run_build.py", "_link_input_downconverters_to_outputs", "named"): (1, "the NEW folder Generate builds"),
    ("generator/run_experiment.py", "_apply_pointer_writes", "named"): (1, "the run's scratch copy (docs/245)"),
    ("generator/run_experiment.py", "_restore_files", "named"): (1, "the run's scratch copy (docs/245)"),
    ("generator/run_generate_config.py", "_retry_loop", "named"): (2, "a scratch folder (config preview)"),
    ("core/history_rekey.py", "migrate_history_rekey_v4", "named"): (1, "a history-dir migration journal"),
}

#: Receivers of ``.save(`` that are instance-dir stores, never a chip.
NON_CHIP_SAVERS = {"agent_session", "entity_notes", "limits", "spec_thresholds", "MappingStore", "quantize"}
_FILE_WRITERS = {"atomic_write_json", "atomic_write_text", "atomic_write_bytes", "write_text", "write_bytes",
                 "copy", "copy2", "copyfile", "copytree", "move", "replace", "rename", "dump", "open"}
_CHIPISH = __import__("re").compile(
    r"state\.json|wiring\.json|\blive\w*|\w*_live\b|\bstate_path\b|\bquam_state\w*|\bstate_dir\b"
    r"|\bchip_folder\b|\bchip_dir\b", __import__("re").I)


def _nm(f):
    return f.attr if isinstance(f, ast.Attribute) else (f.id if isinstance(f, ast.Name) else None)


def door_sites(sources) -> list[tuple[str, str, str, ast.Call]]:
    """``[(file, innermost function, kind, call)]`` for *sources* =
    ``[(package-relative path, source text)]``."""
    out = []
    for rel, text in sources:
        tree = ast.parse(text)
        aliases = {"apply_to_live"}
        for n in ast.walk(tree):
            if isinstance(n, ast.ImportFrom):
                aliases |= {a.asname for a in n.names if a.name == "apply_to_live" and a.asname}
            elif (isinstance(n, ast.Assign) and not isinstance(n.value, ast.Call)
                  and _nm(n.value) in aliases):
                aliases |= {t.id for t in n.targets if isinstance(t, ast.Name)}

        def visit(node, fn):
            for ch in ast.iter_child_nodes(node):
                cur = ch.name if isinstance(ch, (ast.FunctionDef, ast.AsyncFunctionDef)) else fn
                if isinstance(ch, ast.Call):
                    nm = _nm(ch.func)
                    kind = None
                    if nm in aliases:
                        kind = "apply"
                    elif nm == "unrecorded":
                        kind = "unrecorded"
                    elif nm == "save" and isinstance(ch.func, ast.Attribute):
                        recv = ch.func.value
                        if (_nm(recv.func) if isinstance(recv, ast.Call) else _nm(recv)) not in NON_CHIP_SAVERS:
                            kind = "save"
                    elif nm in ("write_state_wiring", "write_state_wiring_bytes"):
                        kind = "pair" if rel != "core/safe_io.py" else None
                    elif nm in _FILE_WRITERS:
                        recv = ast.unparse(ch.func.value) if isinstance(ch.func, ast.Attribute) else ""
                        args = " ".join(ast.unparse(a) for a in [*ch.args, *(k.value for k in ch.keywords)])
                        is_str_replace = nm == "replace" and recv != "os" and len(ch.args) != 1
                        is_read_open = nm == "open" and not any(
                            isinstance(a, ast.Constant) and isinstance(a.value, str) and set(a.value) & set("wax+")
                            for a in [*ch.args[1:], *(k.value for k in ch.keywords if k.arg == "mode")])
                        if not is_str_replace and not is_read_open and _CHIPISH.search(recv + " " + args):
                            kind = "named"
                    if kind:
                        out.append((rel, cur, kind, ch))
                elif (isinstance(ch, (ast.Name, ast.Attribute)) and _nm(ch) in aliases
                      and isinstance(getattr(ch, "ctx", None), ast.Load)
                      and not (isinstance(node, ast.Call) and node.func is ch)
                      and not isinstance(node, ast.Assign)):
                    # passed around (a callback, getattr target...): a door nobody can follow
                    out.append((rel, cur, "apply_ref", ch))
                visit(ch, cur)

        visit(tree, "<module>")
    return out


def door_violations(sources, table=None) -> list[str]:
    """Everything wrong with the chip-writing call sites of *sources*: an
    unclassified site, a count that moved, a live write whose record is
    missing / None / ``unrecorded(...)``, a reference to apply_to_live that is
    not a call."""
    table = DOOR_SITES if table is None else table
    bad = []
    counts: dict = {}
    for f, fn, kind, node in door_sites(sources):
        where = f"{f}:{node.lineno} ({fn})"
        counts[(f, fn, kind)] = counts.get((f, fn, kind), 0) + 1
        if kind == "apply_ref":
            bad.append(f"{where}: apply_to_live referenced, not called -- a door the scan cannot follow")
        if kind == "apply":
            kw = {k.arg: k.value for k in node.keywords}
            rec = kw.get("record")
            if rec is None:
                bad.append(f"{where}: writes the live chip without record=")
            elif isinstance(rec, ast.Constant) and rec.value is None:
                bad.append(f"{where}: passes record=None")
            elif isinstance(rec, ast.Call) and _nm(rec.func) == "unrecorded":
                bad.append(f"{where}: a live-write door passes record=unrecorded(...)")
    for key, n in sorted(counts.items()):
        if key[2] == "apply_ref":
            continue
        want = table.get(key)
        if want is None:
            bad.append(f"unclassified {key[2]} site(s) {key[0]} ({key[1]}) x{n}: if it can write a live chip "
                       "it must record; classify it in DOOR_SITES either way")
        elif want[0] != n:
            bad.append(f"{key[0]} ({key[1]}): {n} {key[2]} call(s), {want[0]} classified -- "
                       "a new write in a classified function is a new door")
    for key in sorted(set(table) - set(counts)):
        bad.append(f"classified site gone: {key} -- update DOOR_SITES")
    return bad


def _package_sources():
    return [(p.relative_to(PKG).as_posix(), p.read_text(encoding="utf-8")) for p in sorted(PKG.rglob("*.py"))]


def _innermost(name: str):
    """``[(file, innermost function, call)]`` of every call named *name*."""
    out = []
    for rel, text in _package_sources():
        tree = ast.parse(text)

        def visit(node, fn):
            for ch in ast.iter_child_nodes(node):
                cur = ch.name if isinstance(ch, (ast.FunctionDef, ast.AsyncFunctionDef)) else fn
                if isinstance(ch, ast.Call) and _nm(ch.func) == name:
                    out.append((rel, cur, ch))
                visit(ch, cur)

        visit(tree, "<module>")
    return out


# The seven doors an adversarial review planted (docs/271 review P2-1): each
# writes a live chip and records nothing. Every one must be reported.
_PLANT_NEW_FILES = {
    "a_saver_inline": '''
from quam_state_manager.core.loader import QuamStore
from quam_state_manager.core.modifier import Modifier
from quam_state_manager.core.saver import Saver


def plant(live_folder, path, value):
    store = QuamStore(live_folder)
    Modifier(store).set_value(path, value)
    Saver(store).save()
''',
    "b_saver_named": '''
from quam_state_manager.core.loader import QuamStore
from quam_state_manager.core.saver import Saver


def plant(live_folder):
    sv = Saver(QuamStore(live_folder))
    sv.save()
''',
    "c_named_path": '''
from pathlib import Path
from quam_state_manager.core import safe_io


def plant(live_folder, state):
    state_path = Path(live_folder) / "state.json"
    safe_io.atomic_write_json(state_path, state)
''',
    "e_alias": '''
from quam_state_manager.core.working_copy import apply_to_live as push_to_chip


def plant(wc):
    push_to_chip(wc)
''',
    "f_copytree": '''
import shutil


def plant(src_folder, live_folder):
    shutil.copytree(src_folder, live_folder, dirs_exist_ok=True)
''',
    "h_callback": '''
from quam_state_manager.core import working_copy


def plant(wc, run):
    run(working_copy.apply_to_live, wc)
''',
}


def _routes_with(edit):
    """The real routes.py source with one planted edit applied by *edit*
    (``(text, tree) -> new text``)."""
    src = (PKG / "web" / "routes.py").read_text(encoding="utf-8")
    return edit(src, ast.parse(src))


def _plant_second_write_in_stage(src, tree):
    """d: a second state/wiring write -- to the LIVE folder -- inside a
    function already classified as a working-copy writer."""
    fn = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == "state_history_stage")
    call = next(n for n in ast.walk(fn) if isinstance(n, ast.Call) and _nm(n.func) == "write_state_wiring")
    lines = src.splitlines(keepends=True)
    line = lines[call.lineno - 1]
    indent = line[:len(line) - len(line.lstrip())]
    lines.insert(call.end_lineno, f'{indent}safe_io.write_state_wiring(Path(ctx["path"]), state, wiring)\n')
    return "".join(lines)


def _plant_unrecorded_in_door(src, tree):
    """g: a live-write door that passes ``record=hub.unrecorded(...)``."""
    fn = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)
              and n.name == "state_history_restore_live")
    call = next(n for n in ast.walk(fn) if isinstance(n, ast.Call) and _nm(n.func) == "apply_to_live")
    lines = src.splitlines(keepends=True)
    seg = "".join(lines[call.lineno - 1:call.end_lineno])
    assert seg.count("record=_pend") == 1, seg
    lines[call.lineno - 1:call.end_lineno] = [seg.replace(
        "record=_pend", 'record=_hub.unrecorded("restore without a record")')]
    return "".join(lines)


class TestEveryDoorRecords:
    def test_the_package_has_no_unrecorded_door(self):
        sites = door_sites(_package_sources())
        assert {k for _, _, k, _ in sites} >= {"apply", "save", "pair", "named", "unrecorded"}, \
            "the scan found nothing of some kind -- it is broken, not the code clean"
        assert door_violations(_package_sources()) == []

    @pytest.mark.parametrize("plant", sorted(_PLANT_NEW_FILES))
    def test_a_planted_new_file_door_is_reported(self, plant):
        sources = _package_sources() + [(f"core/zz_plant_{plant}.py", _PLANT_NEW_FILES[plant])]
        bad = door_violations(sources)
        assert any(f"zz_plant_{plant}" in b for b in bad), (plant, bad)

    @pytest.mark.parametrize("edit", [_plant_second_write_in_stage, _plant_unrecorded_in_door],
                             ids=["d_second_write_in_a_classified_function", "g_unrecorded_record_in_a_door"])
    def test_a_planted_edit_to_a_classified_function_is_reported(self, edit):
        sources = [(rel, _routes_with(edit) if rel == "web/routes.py" else text)
                   for rel, text in _package_sources()]
        bad = door_violations(sources)
        assert any("web/routes.py" in b for b in bad), bad

    def test_an_aliased_door_is_refused_at_runtime_in_strict_mode(self, tmp_path, monkeypatch):
        """The scan reads source; the guard reads calls. A door the scan
        missed still reaches apply_to_live with no record: strict mode (the
        suite) refuses it before the chip is touched."""
        from quam_state_manager.core.working_copy import apply_to_live as push_to_chip
        monkeypatch.setattr(hub, "STRICT", True)
        live, wc = _wc_chip(tmp_path)
        _stage(wc, lambda s: s["qubits"]["qA1"].__setitem__("T1", 9e-5))
        before = (live / "state.json").read_bytes()
        with pytest.raises(hub.RecordError):
            push_to_chip(wc)
        assert (live / "state.json").read_bytes() == before

    def test_an_unrecorded_door_in_production_is_recorded_unattributed(self, tmp_path, monkeypatch):
        """Production never refuses a person's write over bookkeeping: the
        write lands and is recorded as ``unattributed`` with the whole-chip
        difference, in the chip's ledger (found through the app's resolver)."""
        live, wc = _wc_chip(tmp_path)
        chip = tmp_path / "hist"
        monkeypatch.setattr(hub, "STRICT", False)
        monkeypatch.setattr(hub, "_CHIP_DIR_RESOLVER", lambda _live: chip)
        _stage(wc, lambda s: s["qubits"]["qA1"].__setitem__("T1", 9e-5))
        working_copy.apply_to_live(wc)
        assert json.loads((live / "state.json").read_text())["qubits"]["qA1"]["T1"] == 9e-5
        ev = hub.Hub.for_chip(chip).events()
        assert len(ev) == 1 and ev[0]["actor"] == "unattributed" and ev[0]["src"] == "unrecorded_door"
        assert [e["path"] for e in ev[0]["entries"]] == ["qubits.qA1.T1"]
        assert ev[0]["outcome"] == "landed"

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


# ======================================================================
# 6. the review round (docs/271 "Review round"): each executed refutation
#    of the first cut, kept as a permanent pin
# ======================================================================

def _disk_diff_paths(before: dict, after: dict, prefix: str = "") -> set[str]:
    out = set()
    for k in set(before) | set(after):
        p = f"{prefix}.{k}" if prefix else k
        a, b = before.get(k, "<absent>"), after.get(k, "<absent>")
        if isinstance(a, dict) and isinstance(b, dict):
            out |= _disk_diff_paths(a, b, p)
        elif a != b or type(a) is not type(b):
            out.add(p)
    return out


def _handle_from_ctx(env, ctx):
    from quam_state_manager.core.autofit import writer
    return writer.ChipHandle(store=ctx["store"], modifier=ctx["modifier"], saver=ctx["saver"],
                             wc=ctx["working_copy"], build_lock=threading.RLock(),
                             live_path=str(env["live"]), hub_dir=_chip_dir(env), hub_plan="autofit:p1")


def _full_rows(before, after):
    return {(c.path, c.op) for c in rules.diff(rules.flatten(before), rules.flatten(after))}


def _ledger_rows(env, sm_id):
    from quam_state_manager.core.hub_store import OPS
    names = {v: k for k, v in OPS.items()}
    with HubStore(_chip_dir(env)) as st:
        eid = st.conn.execute("SELECT eid FROM sm_events WHERE sm_id=?", (sm_id,)).fetchone()[0]
        rows = st.conn.execute("SELECT p.path, c.op FROM changes c JOIN paths p USING(pid) "
                               "WHERE c.eid=?", (eid,)).fetchall()
        return {(r["path"], names[r["op"]]) for r in rows}, st.state_at(eid)


def _outcomes(chip) -> dict:
    with HubStore(chip) as st:
        return {r[0]: r[1] for r in st.conn.execute("SELECT sm_id, outcome FROM sm_events")}


_CHILD_PRELUDE = '''
import json, os, sys, time
from pathlib import Path
sys.path.insert(0, {root!r})
from quam_state_manager.core import hub, safe_io, working_copy
hub.set_inline(True)
work = Path({work!r})
def wait_go():
    while not (work / "go").exists():
        time.sleep(0.001)
'''


def _spawn(tmp_path, body: str, count: int, **fmt) -> list[str]:
    """Run *count* child Python processes of *body* (``{k}`` = the child's index),
    started together; returns their stdout."""
    import subprocess
    import sys as _sys
    root = str(PKG.parent)
    procs = []
    for k in range(count):
        script = tmp_path / f"child{k}.py"
        script.write_text(_CHILD_PRELUDE.format(root=root, work=str(tmp_path)) + body.format(k=k, **fmt),
                          encoding="utf-8")
        env = dict(os.environ, PYTHONUTF8="1")
        env.pop("SM_HUB_STRICT", None)
        procs.append(subprocess.Popen([_sys.executable, str(script)], stdout=subprocess.PIPE,
                                      stderr=subprocess.PIPE, env=env))
    import time as _t
    _t.sleep(2.5)                                  # every child imported and waiting
    (tmp_path / "go").write_text("1")
    outs = []
    try:
        for p in procs:
            out, err = p.communicate(timeout=300)
            assert p.returncode == 0, err.decode("utf-8", "replace")[-2000:]
            outs.append(out.decode("utf-8"))
    finally:
        for p in procs:                            # never leave a child behind
            if p.poll() is None:
                p.kill()
    return outs


class TestReviewRound:
    # ---- P0-1: two windows, one chip --------------------------------------
    def test_a_window_holding_the_chip_lock_is_waited_for_then_refused(self, tmp_path):
        """Another SM window holds this chip's write lock and writes the chip
        under it: this apply waits for the lock (it never interleaves its
        re-check with that write) and then refuses as stale -- it records
        nothing and overwrites nothing."""
        from quam_state_manager.core import xlock
        live, wc = _wc_chip(tmp_path)
        chip = tmp_path / "hist"
        _stage(wc, lambda s: s["qubits"]["qA1"].__setitem__("T1", 9e-5))
        holding, release = threading.Event(), threading.Event()

        def other_window():
            with xlock.held(xlock.live_lock_path(live)):
                holding.set()
                release.wait(5)
                s = json.loads((live / "state.json").read_text())
                s["qubits"]["qA2"]["T1"] = 7.7e-5
                (live / "state.json").write_text(json.dumps(s))

        t = threading.Thread(target=other_window)
        t.start()
        assert holding.wait(5)
        threading.Timer(0.3, release.set).start()
        p = hub.Pending(chip, "sm_apply", "human:operator", "apply",
                        entries=[{"path": "qubits.qA1.T1", "old": 2e-5, "new": 9e-5}])
        with pytest.raises(working_copy.StaleLiveError):
            working_copy.apply_to_live(wc, record=p)
        t.join()
        assert json.loads((live / "state.json").read_text())["qubits"]["qA2"]["T1"] == 7.7e-5
        assert p.recorded is None and hub.Hub.for_chip(chip).events() == []

    def test_an_outside_write_during_the_journal_fsync_is_refused_not_overwritten(self, env, monkeypatch):
        c = env["client"]
        _edit(env, "qubits.qA1.T1", "3.5e-5")
        live = env["live"]
        real_append = hub.Hub._append
        fired = {}

        def slow_append(self, obj, **kw):
            if "id" in obj and not fired:
                # an experiment's save lands while SM journals (fsync)
                s = json.loads((live / "state.json").read_text())
                s["qubits"]["qA2"]["T1"] = 9.9e-5
                (live / "state.json").write_text(json.dumps(s))
                fired["line"] = obj["id"]
            return real_append(self, obj, **kw)

        monkeypatch.setattr(hub.Hub, "_append", slow_append)
        r = c.post("/state/apply-to-live", headers={"Accept": "application/json"})
        assert r.status_code == 409
        assert json.loads((live / "state.json").read_text())["qubits"]["qA2"]["T1"] == 9.9e-5
        ev = {e["id"]: e for e in _events(env)}
        assert ev[fired["line"]]["outcome"] == "failed"
        assert _outcomes(_chip_dir(env))[fired["line"]] == "failed"

    def test_two_processes_pressing_apply_never_both_land(self, tmp_path):
        """Two SM processes, each with its own working copy of one chip,
        press Apply at the same instant, round after round: in no round do
        both writes land (the second would silently replace the first)."""
        rounds = 25
        _write_chip(tmp_path / "live", {"qubits": {"qA1": {"T1": 2e-5}, "qA2": {"T1": 3e-5}}})
        body = '''
k = {k}
wc = working_copy.create(work / f"inst{{k}}", work / "live")
out = []
for r in range({rounds}):
    try:
        working_copy.sync_from_live(wc)
    except Exception:
        pass
    s, w = safe_io.read_state_wiring(wc.working_folder)
    s["qubits"][f"qA{{k+1}}"]["T1"] = 1e-5 + r * 1e-7 + k * 1e-9
    safe_io.write_state_wiring(wc.working_folder, s, w)
    p = hub.Pending(work / "hist", "sm_apply", f"human:w{{k}}", "apply",
                    entries=[{{"path": f"qubits.qA{{k+1}}.T1", "old": None, "new": 0}}])
    (work / f"ready{{k}}_{{r}}").write_text("1")
    while not all((work / f"ready{{j}}_{{r}}").exists() for j in (0, 1)):
        time.sleep(0.0005)
    if r == 0:
        wait_go()
    try:
        working_copy.apply_to_live(wc, record=p)
        out.append("landed")
    except working_copy.StaleLiveError:
        out.append("stale")
    except Exception as exc:  # noqa: BLE001 -- the round goes on; the parent judges
        out.append("error: " + type(exc).__name__)
print(json.dumps(out))
'''
        outs = [json.loads(o.strip().splitlines()[-1]) for o in _spawn(tmp_path, body, 2, rounds=rounds)]
        both = sum(1 for a, b in zip(*outs) if a == b == "landed")
        assert both == 0, outs
        assert all(len(o) == rounds for o in outs)
        assert not [x for o in outs for x in o if x.startswith("error")], outs
        assert all(sorted(pair) == ["landed", "stale"] for pair in zip(*outs)), outs
        landed_lines = [o for _, _, o in hub.Hub(tmp_path / "hist").read() if "landed" in o]
        assert len(landed_lines) == sum(o.count("landed") for o in outs), (landed_lines, outs)

    # ---- found by the browser race: two processes, one working copy ----------
    def test_bytes_another_process_saved_are_recorded_by_content(self, tmp_path):
        """The door names the working pair its own save produced; when the
        writer reads a different pair (another SM process sharing this working
        copy saved in between), the entries are what is written, by content,
        and no unit is claimed. With the expected pair, the door's entries
        stand."""
        live, wc = _wc_chip(tmp_path)
        chip = tmp_path / "hist"
        _stage(wc, lambda s: s["qubits"]["qA1"].__setitem__("T1", 9e-5))
        mine = working_copy.working_fingerprint(wc)
        p = hub.Pending(chip, "sm_apply", "human:a", "apply",
                        entries=[{"path": "qubits.qA1.T1", "old": 2e-5, "new": 9e-5}],
                        units=["u1"], expect_fp=mine)
        working_copy.apply_to_live(wc, record=p)
        ev = hub.Hub.for_chip(chip).events()[-1]
        assert not p.foreign_bytes and ev["units"] == ["u1"]
        assert [e["path"] for e in ev["entries"]] == ["qubits.qA1.T1"]
        # now another process's save lands between this press's save and the write
        _stage(wc, lambda s: s["qubits"]["qA1"].__setitem__("T1", 9.5e-5))
        mine = working_copy.working_fingerprint(wc)
        _stage(wc, lambda s: (s["qubits"]["qA1"].__setitem__("T1", 9e-5),
                              s["qubits"]["qA2"].__setitem__("T1", 4.4e-5)))
        p2 = hub.Pending(chip, "sm_apply", "human:a", "apply",
                         entries=[{"path": "qubits.qA1.T1", "old": 9e-5, "new": 9.5e-5}],
                         units=["u2"], expect_fp=mine)
        working_copy.apply_to_live(wc, record=p2)
        ev = hub.Hub.for_chip(chip).events()[-1]
        assert p2.foreign_bytes and ev["units"] == []
        assert ev["entries"] == [{"path": "qubits.qA2.T1", "old": 3e-5, "new": 4.4e-5}]
        with HubStore(chip) as st:
            eid = st.conn.execute("SELECT eid FROM sm_events WHERE sm_id=?", (p2.id,)).fetchone()[0]
            doc = st.state_at(eid)
        disk = json.loads((live / "state.json").read_text())
        assert doc["qubits"]["qA1"]["T1"] == disk["qubits"]["qA1"]["T1"] == 9e-5
        assert doc["qubits"]["qA2"]["T1"] == disk["qubits"]["qA2"]["T1"] == 4.4e-5

    def test_the_autofit_writer_records_what_it_wrote_when_its_files_were_replaced(self, tmp_path,
                                                                                    monkeypatch):
        from quam_state_manager.core.autofit import writer
        from quam_state_manager.core.loader import QuamStore
        from quam_state_manager.core.modifier import Modifier
        from quam_state_manager.core.saver import Saver
        live, wc = _wc_chip(tmp_path)
        store = QuamStore(wc.working_folder)
        real_apply = working_copy.apply_to_live

        def other_window_saves_then_apply(w, **kw):
            # between the writer's save and its write, another window's save
            _stage(wc, lambda s: (s["qubits"]["qA1"].__setitem__("T1", 2e-5),
                                  s["qubits"]["qA2"].__setitem__("T1", 4.4e-5)))
            return real_apply(w, **kw)

        monkeypatch.setattr(working_copy, "apply_to_live", other_window_saves_then_apply)
        chip = tmp_path / "hist"
        h = writer.ChipHandle(store=store, modifier=Modifier(store), saver=Saver(store), wc=wc,
                              build_lock=threading.RLock(), live_path=str(live), hub_dir=chip,
                              hub_plan="autofit:p1")
        assert writer.apply_rows(h, [{"path": "qubits.qA1.T1", "value": 7.0e-5}], apply_live=True,
                                 label="t1").ok
        ev = hub.Hub.for_chip(chip).events()[-1]
        assert ev["entries"] == [{"path": "qubits.qA2.T1", "old": 3e-5, "new": 4.4e-5}]

    def test_a_press_whose_saved_files_were_replaced_records_what_it_wrote(self, env, monkeypatch):
        """The same through the Apply door: between the press's save and the
        write, another window's save replaces the shared working files."""
        c = env["client"]
        real = routes_mod._press_save

        def save_then_other_window_saves(ctx, press):
            real(ctx, press)
            wc = ctx["working_copy"]
            s, w = safe_io.read_state_wiring(wc.working_folder)
            s["qubits"]["qA1"]["T1"] = 2e-5                 # the other window's store: qA1 untouched
            s["qubits"]["qA2"]["T1"] = 4.4e-5               # ...and its own edit
            safe_io.write_state_wiring(wc.working_folder, s, w)

        monkeypatch.setattr(routes_mod, "_press_save", save_then_other_window_saves)
        _edit(env, "qubits.qA1.T1", "3.5e-5")
        before = _live_doc(env)
        assert c.post("/state/apply-to-live").status_code == 200
        after = _live_doc(env)
        e = _events(env)[-1]
        assert e["units"] == []
        assert {x["path"] for x in e["entries"]} == _disk_diff_paths(before, after) == {"qubits.qA2.T1"}
        got, doc = _ledger_rows(env, e["id"])
        assert got == _full_rows(before, after) and _same_doc(doc, after)

    # ---- P0-2: one journal, many processes ---------------------------------
    def test_concurrent_processes_append_without_loss_or_tearing(self, tmp_path):
        n_procs, n_lines = 3, 80
        body = '''
h = hub.Hub(work / "hist")
wait_go()
for i in range({n}):
    r = h.record("sm_apply", "human:p{k}", [{{"path": "qubits.q{k}.T1", "old": i, "new": i + 1}}],
                 None, None, "apply")
    r.outcome = "landed"
print("ok")
'''
        _spawn(tmp_path, body, n_procs, n=n_lines)
        raw = (tmp_path / "hist" / "events.jsonl").read_bytes()
        lines = [ln for ln in raw.split(b"\n") if ln.strip()]
        parsed = [json.loads(ln) for ln in lines]                       # a torn line raises here
        assert len(parsed) == n_procs * n_lines
        assert len({o["id"] for o in parsed}) == n_procs * n_lines
        assert raw.endswith(b"\n")

    def test_a_failure_mark_truncation_never_cuts_another_writers_line(self, tmp_path, monkeypatch):
        """A failure line that cannot be written rolls this write's own line
        back out -- by truncating to where it started, under the journal lock,
        and only if nothing was appended after it."""
        chip = tmp_path / "hist"
        h = hub.Hub.for_chip(chip)
        mine = h.record("sm_apply", "human", [{"path": "a", "old": 1, "new": 2}], None, None, "apply")
        other = hub.Hub(chip).record("sm_apply", "human", [{"path": "b", "old": 1, "new": 2}],
                                     None, None, "apply")
        real = hub.Hub._append

        def no_failure_line(self, obj, **kw):
            if "failed" in obj:
                raise hub.RecordError("disk full")
            return real(self, obj, **kw)

        monkeypatch.setattr(hub.Hub, "_append", no_failure_line)
        mine.failed(OSError("the write failed"))
        ids = [o.get("id") for _, _, o in h.read()]
        assert other.id in ids, "another writer's line was truncated away"

    # ---- P1-1: content the change log does not name -----------------------
    def test_a_review_save_then_a_person_apply_records_everything_that_lands(self, env):
        from quam_state_manager.core.autofit import writer
        c = env["client"]
        _edit(env, "qubits.qA1.T1", "3.1e-5")
        assert c.post("/state/apply-to-live").status_code == 200
        out = writer.apply_rows(_handle_from_ctx(env, _ctx(env)), [{"path": "qubits.qA2.f_01", "value": 5.3e9}],
                                apply_live=False, label="qubit_spec")
        assert out.ok and out.action == "staged"
        before = _live_doc(env)
        _edit(env, "qubits.qA1.T1", "3.2e-5")
        assert c.post("/state/apply-to-live").status_code == 200
        after = _live_doc(env)
        e = _events(env)[-1]
        landed = _disk_diff_paths(before, after)
        assert landed == {"qubits.qA1.T1", "qubits.qA2.f_01"}
        assert landed <= {x["path"] for x in e["entries"]}
        led = _ledger(env, e["id"])
        assert led["doc"]["qubits"]["qA2"]["f_01"] == after["qubits"]["qA2"]["f_01"]

    def test_the_autofit_writer_records_a_review_save_it_later_applies(self, tmp_path):
        from quam_state_manager.core.autofit import writer
        from quam_state_manager.core.loader import QuamStore
        from quam_state_manager.core.modifier import Modifier
        from quam_state_manager.core.saver import Saver
        live, wc = _wc_chip(tmp_path)
        store = QuamStore(wc.working_folder)
        chip = tmp_path / "hist"
        h = writer.ChipHandle(store=store, modifier=Modifier(store), saver=Saver(store), wc=wc,
                              build_lock=threading.RLock(), live_path=str(live), hub_dir=chip,
                              hub_plan="autofit:p1")
        assert writer.apply_rows(h, [{"path": "qubits.qA1.T1", "value": 7.0e-5}], apply_live=True, label="t1").ok
        ev1 = hub.Hub.for_chip(chip).events()[-1]
        assert [x["path"] for x in ev1["entries"]] == ["qubits.qA1.T1"], "content ok: the log is the entries"
        assert ev1["entries"][0].get("by") == "autofit", "...the writer's own, stamped as such"
        assert writer.apply_rows(h, [{"path": "qubits.qA2.f_01", "value": 5.3e9}], apply_live=False,
                                 label="review").action == "staged"
        assert writer.apply_rows(h, [{"path": "qubits.qA1.f_01", "value": 5.2e9}], apply_live=True,
                                 label="spec").ok
        disk = json.loads((live / "state.json").read_text())
        ev = hub.Hub.for_chip(chip).events()[-1]
        assert {x["path"] for x in ev["entries"]} == {"qubits.qA2.f_01", "qubits.qA1.f_01"}
        with HubStore(chip) as st:
            eid = st.conn.execute("SELECT eid FROM sm_events WHERE sm_id=?", (ev["id"],)).fetchone()[0]
            doc = st.state_at(eid)
        assert doc["qubits"]["qA2"]["f_01"] == disk["qubits"]["qA2"]["f_01"] == 5.3e9

    # ---- P1-2: a landed write is never marked failed -----------------------
    def test_a_landed_write_whose_verify_read_failed_is_recorded_landed(self, env, monkeypatch):
        c = env["client"]
        _edit(env, "qubits.qA1.T1", "3.5e-5")
        real = working_copy.live_content_hash
        state = {"armed": False}
        real_write = safe_io.write_state_wiring_bytes

        def write_then_arm(folder, sb, wb):
            real_write(folder, sb, wb)
            if Path(folder) == env["live"]:
                state["armed"] = True

        def flaky(w, **k):
            if state["armed"]:
                state["armed"] = False
                raise PermissionError(32, "sharing violation (a reader holds the file)")
            return real(w, **k)

        monkeypatch.setattr(safe_io, "write_state_wiring_bytes", write_then_arm)
        monkeypatch.setattr(working_copy, "live_content_hash", flaky)
        r1 = c.post("/state/apply-to-live", headers={"Accept": "application/json"})
        assert r1.status_code == 200, "the second read found the written content: the press succeeds"
        assert _live_doc(env)["qubits"]["qA1"]["T1"] == 3.5e-5
        e = _events(env)[-1]
        assert e["outcome"] == "landed" and _outcomes(_chip_dir(env))[e["id"]] == "landed"
        assert any(o.get("landed") == e["id"] and o.get("verified") == "second read"
                   for _, _, o in hub.Hub.for_chip(_chip_dir(env)).read())

    def test_a_write_marked_failed_that_had_landed_is_relabelled_by_the_next_noop(self, tmp_path):
        """The adopt path (docs/116: live already holds exactly this content)
        writes a late landed line for a write marked failed, and the ledger
        relabels that event landed in place."""
        live, wc = _wc_chip(tmp_path)
        chip = tmp_path / "hist"
        _stage(wc, lambda s: s["qubits"]["qA1"].__setitem__("T1", 9e-5))
        h = hub.Hub.for_chip(chip)
        p1 = hub.Pending(chip, "sm_apply", "human", "apply",
                         entries=[{"path": "qubits.qA1.T1", "old": 2e-5, "new": 9e-5}])
        p1.prepare(post_state=None, post_hash=None)
        sb = (wc.working_folder / "state.json").read_bytes()
        wb = (wc.working_folder / "wiring.json").read_bytes()
        post = working_copy.working_content_hash(wc)
        rec = p1.commit(base_hash=wc.synced_live_hash, post_state=(sb, wb), post_hash=post, live=live)
        safe_io.write_state_wiring_bytes(live, sb, wb)              # it landed...
        rec.failed(OSError("the verify read failed"))                # ...but was marked failed
        assert _outcomes(chip)[p1.id] == "failed"
        ord0 = HubStore(chip).conn.execute("SELECT e.ord FROM sm_events s JOIN events e USING(eid) "
                                           "WHERE s.sm_id=?", (p1.id,)).fetchone()[0]
        p2 = hub.Pending(chip, "sm_apply", "human", "apply",
                         entries=[{"path": "qubits.qA1.T1", "old": 2e-5, "new": 9e-5}])
        working_copy.apply_to_live(wc, record=p2)                    # no-op: live holds it
        assert p2.skipped and p2.recorded is None
        assert _outcomes(chip)[p1.id] == "landed"
        with HubStore(chip) as st:
            ord1 = st.conn.execute("SELECT e.ord FROM sm_events s JOIN events e USING(eid) "
                                   "WHERE s.sm_id=?", (p1.id,)).fetchone()[0]
        assert ord1 == ord0, "relabelled in the same place of the timeline"
        assert any(o.get("landed") == p1.id and o.get("late") for _, _, o in h.read())

    def _half_pair(self, tmp_path, monkeypatch, *, wiring_changes: bool):
        live, wc = _wc_chip(tmp_path)
        chip = tmp_path / "hist"
        s, w = safe_io.read_state_wiring(wc.working_folder)
        s["qubits"]["qA1"]["T1"] = 9e-5
        if wiring_changes:
            w["network"]["cluster_name"] = "C2"
        safe_io.write_state_wiring(wc.working_folder, s, w)
        real_replace, real_tmp = safe_io._replace_into_place, safe_io._write_tmp_bytes
        st = {"wiring_failed": False}

        def replace(tmp, target):
            if Path(target) == live / "wiring.json" and not st["wiring_failed"]:
                st["wiring_failed"] = True
                raise PermissionError(32, "wiring.json held open by a reader")
            return real_replace(tmp, target)

        def write_tmp(target, data):
            if st["wiring_failed"] and Path(target) == live / "state.json":
                raise OSError(28, "no space left for the rollback copy")
            return real_tmp(target, data)

        monkeypatch.setattr(safe_io, "_replace_into_place", replace)
        monkeypatch.setattr(safe_io, "_write_tmp_bytes", write_tmp)
        p1 = hub.Pending(chip, "sm_apply", "human", "apply",
                         entries=[{"path": "qubits.qA1.T1", "old": 2e-5, "new": 9e-5}])
        return live, wc, chip, p1

    def test_a_half_failed_pair_whose_content_is_whole_landed(self, tmp_path, monkeypatch):
        """The wiring replace failed, but the wiring was not changing: the
        chip holds exactly the written content, so the write landed and the
        press succeeds (it is never marked failed and then adopted unseen)."""
        live, wc, chip, p1 = self._half_pair(tmp_path, monkeypatch, wiring_changes=False)
        working_copy.apply_to_live(wc, record=p1)
        assert json.loads((live / "state.json").read_text())["qubits"]["qA1"]["T1"] == 9e-5
        assert p1.landed and _outcomes(chip)[p1.id] == "landed"

    def test_a_true_half_pair_is_left_to_evidence_never_marked_failed(self, tmp_path, monkeypatch):
        """New state + old wiring: neither the old content nor the new. The
        write is not claimed failed (state.json DID change); evidence decides,
        and with the chip holding neither, the ledger claims nothing."""
        live, wc, chip, p1 = self._half_pair(tmp_path, monkeypatch, wiring_changes=True)
        with pytest.raises(OSError):
            working_copy.apply_to_live(wc, record=p1)
        assert json.loads((live / "state.json").read_text())["qubits"]["qA1"]["T1"] == 9e-5
        assert not any(o.get("failed") == p1.id for _, _, o in hub.Hub.for_chip(chip).read())
        assert _outcomes(chip)[p1.id] == "unconfirmed"

    # ---- P1-3: the journal alone rebuilds the same ledger -------------------
    def test_a_rebuild_from_the_journal_alone_agrees(self, env):
        c = env["client"]
        _edit(env, "qubits.qA1.T1", "3.5e-5")
        assert c.post("/state/apply-to-live").status_code == 200
        live = env["live"]
        s = json.loads((live / "state.json").read_text())
        s["qubits"]["qA2"]["T1"] = 9.9e-5
        (live / "state.json").write_text(json.dumps(s))                 # an experiment runs
        assert c.post("/state/sync", data={"mode": "discard", "force": "1"}).get_json()["status"] == "ok"
        _edit(env, "qubits.qA1.f_01", "5.02e9")
        assert c.post("/state/apply-to-live").status_code == 200
        chip = _chip_dir(env)
        orig = _outcomes(chip)
        assert set(orig.values()) == {"landed"} and len(orig) == 2
        for name in ("ledger.sqlite", "ledger.sqlite-wal", "ledger.sqlite-shm"):
            if (chip / name).exists():
                (chip / name).unlink()
        hub.project(hub.Hub(chip), hub._Projector())                  # a later process: no RAM
        assert _outcomes(chip) == orig

    # ---- P2-3: the offset follows the file ---------------------------------
    def test_a_shortened_journal_is_rescanned(self, tmp_path):
        chip = tmp_path / "hist"
        h = hub.Hub.for_chip(chip)
        for _ in range(3):
            h.record("sm_apply", "human", [{"path": "a", "old": 1, "new": 2}], None, None, "apply").landed()
        lines = h.journal.read_bytes().splitlines(keepends=True)
        h.journal.write_bytes(lines[0])                    # a restored backup / a trimmed copy
        new = h.record("sm_apply", "human", [{"path": "b", "old": 1, "new": 2}], None, None, "apply")
        new.landed()
        hub.project(h)
        assert new.id in _outcomes(chip)

    def test_an_offset_not_on_a_line_boundary_is_rescanned(self, tmp_path):
        """An offset that points into the middle of a line (a journal replaced
        by another of a different layout) is not trusted: read from there,
        the line it cuts would be skipped as torn and never projected."""
        chip = tmp_path / "hist"
        h = hub.Hub.for_chip(chip)
        h._append({"v": 1, "id": "cut1", "kind": "sm_apply", "t_utc_us": 1, "t": "x", "actor": "human",
                   "src": "apply", "base_hash": "B", "post_hash": "P", "live": None, "pid": 1, "n": 0,
                   "entries": []})
        with HubStore(chip) as st, st.conn:
            st.set_meta("journal_offset", "7")             # inside the unprojected line
        hub.project(h)
        assert "cut1" in _outcomes(chip)

    def test_a_rolled_back_line_behind_the_offset_does_not_hide_the_next(self, tmp_path, monkeypatch):
        from typer.testing import CliRunner
        from quam_state_manager.cli import app as cli_app
        from quam_state_manager.core.history import HistoryManager
        from quam_state_manager.core.saver import Saver
        live = tmp_path / "chips" / "live"
        _write_chip(live, _state())
        inst = tmp_path / "inst"
        chip = HistoryManager(inst).resolve_chip_dir(live)[0]
        real_append = hub.Hub._append

        def save_fails(self, *a, **k):
            hub.project(hub.Hub(chip), hub._Projector())          # a window projects meanwhile
            raise OSError(28, "disk full")

        def append(self, obj, **kw):
            if "failed" in obj:
                raise hub.RecordError("disk full")
            return real_append(self, obj, **kw)

        monkeypatch.setattr(Saver, "save", save_fails)
        monkeypatch.setattr(hub.Hub, "_append", append)
        CliRunner().invoke(cli_app, ["set", "qubits.qA1.T1", "6e-5", "-f", str(live), "--save",
                                     "--instance", str(inst)])
        monkeypatch.setattr(hub.Hub, "_append", real_append)
        w = hub.Hub(chip).record("sm_apply", "human:operator",
                                 [{"path": "qubits.qA2.T1", "old": 3e-5, "new": 3.3e-5}],
                                 "B", (b'{"a":1}', b"{}"), "apply", post_hash="P")
        w.landed()
        hub.project(hub.Hub(chip))
        assert _outcomes(chip).get(w.id) == "landed"

    # ---- P2-4: another writer's line in flight -----------------------------
    def test_a_cli_line_in_flight_waits_for_its_writer(self, tmp_path, monkeypatch):
        """A window projecting while the CLI's write is in flight (the CLI is
        no registered SM window) waits for the line's writer pid; the line is
        then decided by its own landed mark."""
        from typer.testing import CliRunner
        from quam_state_manager.cli import app as cli_app
        from quam_state_manager.core.history import HistoryManager
        from quam_state_manager.core.saver import Saver
        live = tmp_path / "chips" / "live"
        _write_chip(live, _state())
        inst = tmp_path / "inst"
        chip = HistoryManager(inst).resolve_chip_dir(live)[0]
        real_save = Saver.save

        seen = {}

        def save_while_window_projects(self, *a, **k):
            hub.project(hub.Hub(chip), hub._Projector())
            with HubStore(chip) as st:                     # the in-flight line waited
                seen["during"] = st.conn.execute("SELECT COUNT(*) FROM sm_events").fetchone()[0]
            return real_save(self, *a, **k)

        monkeypatch.setattr(Saver, "save", save_while_window_projects)
        res = CliRunner().invoke(cli_app, ["set", "qubits.qA1.T1", "6e-5", "-f", str(live), "--save",
                                           "--instance", str(inst)])
        assert res.exit_code == 0, res.output
        assert json.loads((live / "state.json").read_text())["qubits"]["qA1"]["T1"] == 6e-5
        assert seen["during"] == 0, "a line whose writer is alive is never decided by another projector"
        assert list(_outcomes(chip).values()) == ["landed"]

    def test_a_reused_pid_cannot_stall_a_line(self, tmp_path, monkeypatch):
        """The writer-pid wait is bounded: an old line whose pid now belongs
        to some other live process is decided by evidence."""
        chip = tmp_path / "hist"
        h = hub.Hub.for_chip(chip)
        monkeypatch.setattr(hub, "_pid_alive", lambda pid: True)
        old = (__import__("time").time_ns() // 1000) - int((hub.WRITER_GRACE_S + 5) * 1e6)
        h._append({"v": 1, "id": "old1", "kind": "sm_apply", "t_utc_us": old, "t": "x", "actor": "human",
                   "src": "apply", "base_hash": "B", "post_hash": "P", "live": None, "pid": 424242, "n": 0,
                   "entries": []})
        assert hub.project(h) == 1
        assert _outcomes(chip)["old1"] == "unconfirmed"

    # ---- P2-5: one bad line never blocks the rest --------------------------
    def test_one_unprojectable_line_is_kept_with_its_error_and_the_tail_moves_on(self, tmp_path, monkeypatch):
        chip = tmp_path / "hist"
        h = hub.Hub.for_chip(chip)
        real_rows = hub_entries.rows_for

        def rows_for(entries, doc):
            if any(e["path"] == "poison" for e in entries):
                raise KeyError("a projection bug on one line")
            return real_rows(entries, doc)

        monkeypatch.setattr(hub_entries, "rows_for", rows_for)
        bad = h.record("sm_apply", "human", [{"path": "poison", "old": 1, "new": 2}], None, (b"{}", b"{}"), "apply")
        bad.landed()
        goods = []
        for i in range(3):
            g = h.record("sm_apply", "human", [{"path": f"ok{i}", "old": 1, "new": 2}], None,
                         (b"{}", b"{}"), "apply")
            g.landed()
            goods.append(g.id)
        hub.catch_up(chip)
        with HubStore(chip) as st:
            rows = {r[0]: r[1] for r in st.conn.execute(
                "SELECT s.sm_id, e.error FROM sm_events s JOIN events e USING(eid)")}
        assert set(rows) == {bad.id, *goods}
        assert all(rows[g] is None for g in goods)
        assert rows[bad.id].startswith("projection error: KeyError")
        assert any("a projection bug on one line" in e for e in hub.projection_errors(chip))

    def test_diagnostics_names_a_write_the_history_could_not_take_in(self, env, monkeypatch):
        real_rows = hub_entries.rows_for

        def rows_for(entries, doc):
            if any(e["path"] == "qubits.qA1.T1" for e in entries):
                raise KeyError("a projection bug on one line")
            return real_rows(entries, doc)

        monkeypatch.setattr(hub_entries, "rows_for", rows_for)
        _edit(env, "qubits.qA1.T1", "3.5e-5")
        assert env["client"].post("/state/apply-to-live").status_code == 200
        with env["app"].test_request_context():
            routes_mod.current_app.config.update(env["app"].config)
            f = [x for x in routes_mod._hub_ledger_findings(_ctx(env)["store"])]
        assert len(f) == 1 and f[0].category == "history_ledger" and f[0].severity == "warning"
        assert "a projection bug on one line" in f[0].detail
        page = env["client"].get("/diagnostics").get_data(as_text=True)
        assert "could not be added to the change history" in page

    # ---- P2-6: a saved edit Take live dropped is never claimed --------------
    def test_a_saved_edit_dropped_by_take_live_is_never_claimed(self, env):
        c = env["client"]
        _edit(env, "qubits.qA1.T1", "3.5e-5")
        assert c.post("/save").status_code == 200
        sc = undo_journal.sidecar_path(env["app"].instance_path, _ctx(env)["path"])
        saved = {u["id"] for u in undo_journal.load(sc) if (u.get("meta") or {}).get("saved")}
        assert saved
        assert c.post("/state/sync", data={"mode": "discard", "force": "1"}).get_json()["status"] == "ok"
        assert all((u.get("meta") or {}).get("discarded") for u in undo_journal.load(sc) if u["id"] in saved)
        _edit(env, "qubits.qA2.T1", "4.4e-5")
        assert c.post("/save").status_code == 200
        assert c.post("/state/apply-to-live").status_code == 200
        e = _events(env)[-1]
        assert _live_doc(env)["qubits"]["qA1"]["T1"] == 2e-5
        assert not (set(e["units"]) & saved)
        r = c.post("/undo")                                     # the one real edit taken back
        assert r.status_code == 200
        with HubStore(_chip_dir(env)) as st:
            fl = st.conn.execute("SELECT e.flags FROM events e JOIN sm_events s USING(eid) "
                                 "WHERE s.sm_id=?", (e["id"],)).fetchone()[0]
        assert fl & UNDONE and not fl & PARTLY_UNDONE

    # ---- P2-2: the request thread pays for content only when it must -------
    def test_a_plain_apply_never_parses_the_chip_inside_the_commit(self, env, monkeypatch):
        calls = {"in_commit": False, "parse_in_commit": 0, "tree": 0, "tree_in_commit": 0}
        real_tree, real_commit = routes_mod._live_merged_tree, hub.Pending.commit
        real_te = hub_entries.tree_entries

        def tree(wc):
            calls["parse_in_commit"] += calls["in_commit"]
            return real_tree(wc)

        def te(*a, **k):
            calls["tree"] += 1
            calls["tree_in_commit"] += calls["in_commit"]
            return real_te(*a, **k)

        def commit(self, **kw):
            calls["in_commit"] = True
            try:
                return real_commit(self, **kw)
            finally:
                calls["in_commit"] = False

        monkeypatch.setattr(routes_mod, "_live_merged_tree", tree)
        monkeypatch.setattr(hub_entries, "tree_entries", te)
        monkeypatch.setattr(hub.Pending, "commit", commit)
        for v in ("3.5e-5", "3.55e-5"):                   # edit -> Apply, twice
            _edit(env, "qubits.qA1.T1", v)
            assert env["client"].post("/state/apply-to-live").status_code == 200
        assert calls["tree"] == 0 and calls["parse_in_commit"] == 0,             "an edit-then-Apply needs no whole-chip difference (the sync point follows each apply)"
        _edit(env, "qubits.qA1.T1", "3.6e-5")
        assert env["client"].post("/save").status_code == 200
        assert env["client"].post("/state/apply-to-live").status_code == 200
        assert calls["tree"] == 1, "after /save the content check needs one whole-chip difference"
        assert calls["tree_in_commit"] == 0 and calls["parse_in_commit"] == 0,             "...computed before the chip lock, never inside the commit"
        assert [x["path"] for x in _events(env)[-1]["entries"]] == ["qubits.qA1.T1"]

    # ---- P3a: one equality ---------------------------------------------------
    def test_the_whole_chip_difference_never_calls_1_true(self):
        assert hub_entries.tree_entries({"b": 1}, {"b": True}) == [{"path": "b", "old": 1, "new": True}]
        assert hub_entries.tree_entries({"b": 1}, {"b": 1.0}) == []
        assert hub_entries.tree_entries({"q": {"x": {"b": 0}}}, {"q": {"x": {"b": False}}}) == \
            [{"path": "q.x.b", "old": 0, "new": False}]

    def test_keep_mine_over_a_number_to_bool_change_records_the_row(self, env):
        c = env["client"]
        live = env["live"]
        s = json.loads((live / "state.json").read_text())
        s["qubits"]["qA2"]["enabled"] = True
        (live / "state.json").write_text(json.dumps(s))
        assert c.post("/state/sync", data={"mode": "discard", "force": "1"}).get_json()["status"] == "ok"
        _edit(env, "qubits.qA1.T1", "3.5e-5")
        s = json.loads((live / "state.json").read_text())
        s["qubits"]["qA2"]["enabled"] = 1                          # an outside writer
        (live / "state.json").write_text(json.dumps(s))
        before = _live_doc(env)
        with env["app"].app_context():
            h0 = working_copy.live_content_hash(_ctx(env)["working_copy"])
        assert c.post(f"/state/apply-to-live?force=1&expect_live_hash={h0}").status_code == 200
        after = _live_doc(env)
        got, doc = _ledger_rows(env, _events(env)[-1]["id"])
        assert ("qubits.qA2.enabled", "set") in _full_rows(before, after)
        assert got == _full_rows(before, after)
        assert _same_doc(doc, after)

    # ---- P3b: near-linear entry -> row conversion ----------------------------
    def test_rows_and_fragments_scale_near_linearly(self):
        import time as _t
        doc = {"qubits": {f"q{i}": {"T1": float(i), "f": float(i), "g": {"a": i}} for i in range(4000)}}

        def cost(n):
            ents = [{"path": f"qubits.q{i}.T1", "old": float(i), "new": float(i) + 0.5} for i in range(n)]
            best = 1e9
            for _ in range(3):
                t0 = _t.perf_counter()
                frag = hub_entries.post_fragments(ents, doc)
                hub_entries.rows_for(ents, frag)
                best = min(best, _t.perf_counter() - t0)
            return best

        small, big = cost(500), cost(4000)
        assert big / small < 16, f"8x the entries cost {big / small:.1f}x (quadratic would be ~64x)"

    # ---- route exactness controls (rows == the S2 diff of the bytes) --------
    def test_route_exactness_a_staged_version_plus_tray_edits(self, env):
        c = env["client"]
        _edit(env, "qubits.qA1.T1", "3.5e-5")
        _edit(env, "qubits.qA2.T1", "4.5e-5")
        c.post("/state/apply-to-live")
        ctx = _ctx(env)
        with env["app"].app_context():
            pre = [s for s in routes_mod._history().list_snapshots(ctx["path"]) if s.kind == "backup"][0].timestamp
        assert c.post(f"/state-history/{pre}/stage").status_code == 200
        assert _live_doc(env)["qubits"]["qA1"]["T1"] == 3.5e-5, "a stage writes nothing live"
        _edit(env, "qubits.qA1.f_01", "5.01e9")
        c.post("/field/create", data={"dot_path": "qubits.qA2.k_new", "value": "3"})
        before = _live_doc(env)
        assert c.post("/state/apply-to-live").status_code == 200
        after = _live_doc(env)
        got, doc = _ledger_rows(env, _events(env)[-1]["id"])
        assert got == _full_rows(before, after) and _same_doc(doc, after)

    def test_route_exactness_the_pulse_page(self, env):
        c = env["client"]
        _edit(env, "qubits.qA2.f_01", "5.15e9")
        c.post("/state/apply-to-live")
        steps = [
            lambda: c.post("/api/pulse/duplicate", data={"path": "qubits.qA1.xy.operations.x180",
                                                         "new_name": "x180_copy"}),
            lambda: c.post("/api/pulse/rename", data={"path": "qubits.qA1.xy.operations.x180_copy",
                                                      "new_name": "x90"}),
            lambda: c.post("/api/pulse/delete", data={"path": "qubits.qA1.xy.operations.x90"}),
        ]
        for act in steps:
            before = _live_doc(env)
            assert act().status_code < 400
            assert c.post("/state/apply-to-live").status_code == 200
            after = _live_doc(env)
            got, doc = _ledger_rows(env, _events(env)[-1]["id"])
            assert got == _full_rows(before, after) and _same_doc(doc, after)
