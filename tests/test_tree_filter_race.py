"""docs/267: the sidebar filter never says "no match" about runs it has not read.

A listing-first ``/workspace/add`` (docs/142) publishes STUB entries -- name and
date from the folder path, status/qubits/params empty -- and a daemon thread
parses node.json later. The filter used to judge those placeholders, so a
folder filtered right after it was added answered "No runs in this folder
match" while matching runs existed (and three TestWorkspace tests in
test_web.py passed or failed with machine load).

Every pin that needs the stub state HOLDS the real background hydrator
before it parses anything (an Event, not a sleep), so that state is reached on
every run, not on a lucky one. Each pin was mutation-checked; see docs/267.
"""
from __future__ import annotations

import json
import re
import threading
import time
import types
from pathlib import Path

import pytest

from quam_state_manager.core import scanner
from quam_state_manager.web import routes
from quam_state_manager.web.app import create_app

NOMATCH = "tree-nomatch-note"
PENDING = "tree-pending-note"


def _run(root, rid, *, matches=True, name=None, day="2026-01-01"):
    """One run whose folder-derived name and date CONTRADICT its node.json --
    a stub judged on its placeholders gives the wrong answer for every query."""
    run = root / day / f"#{rid}_placeholder_120000"
    qs = run / "quam_state"
    qs.mkdir(parents=True)
    (qs / "state.json").write_text("{}", encoding="utf-8")
    (qs / "wiring.json").write_text("{}", encoding="utf-8")
    (run / "node.json").write_text(json.dumps({
        "id": rid,
        "metadata": {"name": name or ("resonator_spec" if matches else "qubit_spec"),
                     "status": "finished" if matches else "failed"},
        "created_at": "2025-02-03T12:00:00" if matches else "2024-02-03T12:00:00",
        "data": {"parameters": {"model": {
            "qubits": ["q1" if matches else "q2"],
            "qubit_pairs": ["q1-q3" if matches else "q2-q4"],
            "reset_type": "active" if matches else "passive",
        }}},
    }), encoding="utf-8")
    return qs


@pytest.fixture
def held(monkeypatch):
    """Hold the real ``Workspace._hydrate_root`` before it parses anything;
    ``release()`` lets it run to completion and waits for it."""
    entered, go, done = threading.Event(), threading.Event(), threading.Event()
    real = scanner.Workspace._hydrate_root

    def paused(self, *args):
        entered.set()
        try:
            assert go.wait(30), "the test never released the hydrator"
            real(self, *args)
        finally:
            done.set()

    monkeypatch.setattr(scanner.Workspace, "_hydrate_root", paused)

    def release():
        go.set()
        assert done.wait(30), "hydration did not finish"

    h = types.SimpleNamespace(entered=entered, release=release)
    yield h
    go.set()
    done.wait(30)


def _add(tmp_path, root):
    app = create_app(testing=True, instance_path=str(tmp_path / "instance"))
    c = app.test_client()
    assert c.post("/workspace/add", data={"folder": str(root)}).status_code == 200
    with app.app_context():
        ws = routes._ws()
    return c, ws


def _tree(c, q):
    return c.get("/workspace/tree", query_string={"name": q}).get_data(as_text=True)


def _rids(html):
    return sorted({int(r) for r in re.findall(r'data-run-id="(\d+)"', html)})


def _pending(html):
    m = re.search(r'data-pending="(\d+)"', html)
    return int(m.group(1)) if m else 0


QUERIES = ["finished", "2025", "2025 resonator", "name:resonator",
           "qubit:q1", "pair:q1-q3", "param:reset=active", "-status:failed"]


@pytest.mark.parametrize("query", QUERIES)
def test_a_small_folder_is_answered_exactly_before_hydration(tmp_path, held, query):
    """The three flaky TestWorkspace tests, made deterministic: two runs, the
    hydrator held, the filter fired at once. A folder under the inline cap is
    read whole, so the answer is exact -- no pending note, no "no match"."""
    root = tmp_path / "runs"
    _run(root, 1)
    _run(root, 2, matches=False)
    c, ws = _add(tmp_path, root)
    assert held.entered.wait(10)
    key = str(root.resolve())
    assert all(e.needs_parse for g in ws.tree[key] for e in g.entries)
    html = _tree(c, query)
    assert NOMATCH not in html
    assert PENDING not in html
    assert "resonator_spec" in html
    assert "qubit_spec" not in html
    # the filter published nothing: the live tree is still all stubs
    assert all(e.needs_parse for g in ws.tree[key] for e in g.entries)


def test_a_stub_is_never_judged_on_its_folder_name(tmp_path, held):
    """Both folders are named ``placeholder`` under a 2026 date dir; node.json
    says otherwise on both counts. Neither path-derived field may match."""
    root = tmp_path / "runs"
    _run(root, 1)
    _run(root, 2, matches=False)
    c, _ws = _add(tmp_path, root)
    assert held.entered.wait(10)
    html = _tree(c, "placeholder")
    assert "resonator_spec" not in html and "qubit_spec" not in html
    assert NOMATCH in html                     # everything was read: a real "no"
    html = _tree(c, "date:2026-01")
    assert _rids(html) == [] and NOMATCH in html


def test_past_the_cap_the_tree_says_pending_never_no_match(tmp_path, held, monkeypatch):
    """Over the inline cap the rest is COUNTED, never rejected: the honest
    line appears with the right number, "no match" does not, and the line
    refetches the tree in the filter box's own hx-sync group."""
    monkeypatch.setattr(scanner, "FILTER_INLINE_PARSE_MAX", 2)
    root = tmp_path / "runs"
    for rid in range(1, 6):
        _run(root, rid, matches=rid % 2 == 1)  # 1, 3, 5 match
    c, _ws = _add(tmp_path, root)
    assert held.entered.wait(10)
    for q in ("finished", "zzzzqq", "qubit:q1"):
        html = _tree(c, q)
        assert NOMATCH not in html, q
        assert _pending(html) == 3, q
        assert "3 runs still being scanned" in html, q
        assert 'hx-trigger="load delay:2s"' in html
        assert 'hx-sync="#sidebar-filter-input:replace"' in html
    # an unread stub is not judged on its placeholders either way: its
    # folder name says "placeholder", but only node.json may answer that
    html = _tree(c, "placeholder")
    assert _rids(html) == [] and _pending(html) == 3 and NOMATCH not in html
    # the Refresh door renders through the same context
    html = c.post("/workspace/refresh", data={"name": "zzzzqq"}).get_data(as_text=True)
    assert NOMATCH not in html and _pending(html) == 3


def test_likely_matches_are_read_first(tmp_path, held, monkeypatch):
    """With room for two reads, the two stubs whose folder names already say
    ``target`` are read -- not the two newest runs -- so the first answer
    shows real matches, not just a pending count."""
    monkeypatch.setattr(scanner, "FILTER_INLINE_PARSE_MAX", 2)
    root = tmp_path / "runs"
    for rid in range(1, 9):
        name = "target_scan" if rid in (2, 4) else "other_scan"
        qs = _run(root, rid, name=name)
        qs.parent.rename(qs.parent.with_name(f"#{rid}_{name}_120000"))
    c, _ws = _add(tmp_path, root)
    assert held.entered.wait(10)
    html = _tree(c, "target")
    assert _rids(html) == [2, 4]
    assert _pending(html) == 6


def test_once_hydrated_the_right_matches_show(tmp_path, held, monkeypatch):
    """After the background parse publishes, the full answer replaces the
    partial one: every match, no pending line, and a real "no match"."""
    monkeypatch.setattr(scanner, "FILTER_INLINE_PARSE_MAX", 2)
    root = tmp_path / "runs"
    for rid in range(1, 6):
        _run(root, rid, matches=rid % 2 == 1)
    c, ws = _add(tmp_path, root)
    assert held.entered.wait(10)
    snapshot = ws.tree
    assert _pending(_tree(c, "finished")) == 3
    held.release()
    html = _tree(c, "finished")
    assert PENDING not in html and NOMATCH not in html
    assert _rids(html) == [1, 3, 5]
    assert NOMATCH in _tree(c, "zzzzqq")
    # an older reader still holding the stub snapshot is answered the same
    # bounded way, and the snapshot itself is never rewritten
    pend: dict = {}
    out = routes._filter_tree(snapshot, "finished", pend)
    assert pend == {str(root.resolve()): 3}
    assert all(e.needs_parse for g in snapshot[str(root.resolve())] for e in g.entries)
    assert [g.date_str for g in out[str(root.resolve())]] == ["2025-02-03"]


def test_a_failed_hydration_says_not_read_and_does_not_poll(tmp_path, monkeypatch):
    """Hydration that died leaves stubs and clears its flag. The note must not
    claim a scan is running, and must not refetch itself forever."""
    def failed(self, root, stubs):            # the real one's finally-block only
        with self._lock:
            self._hydrating.discard(str(root))
            self._version += 1

    monkeypatch.setattr(scanner.Workspace, "_hydrate_root", failed)
    monkeypatch.setattr(scanner, "FILTER_INLINE_PARSE_MAX", 1)
    root = tmp_path / "runs"
    for rid in range(1, 4):
        _run(root, rid)
    c, ws = _add(tmp_path, root)
    for _ in range(200):
        if not ws.hydrating_roots():
            break
        time.sleep(0.01)
    html = _tree(c, "zzzzqq")
    assert NOMATCH not in html and _pending(html) == 2
    assert "2 runs not read yet" in html
    assert "still being scanned" not in html
    assert "load delay:2s" not in html


def test_a_run_that_cannot_be_parsed_stays_unread_not_a_500(tmp_path, held):
    """The inline read runs inside a keystroke's request. A node.json of an
    unexpected shape (``metadata: null`` raises in the parser) must leave that
    run unread and counted -- never fail the whole filter."""
    root = tmp_path / "runs"
    _run(root, 1)
    bad = _run(root, 2)
    node = json.loads((bad.parent / "node.json").read_text(encoding="utf-8"))
    node["metadata"] = None
    (bad.parent / "node.json").write_text(json.dumps(node), encoding="utf-8")
    c, _ws = _add(tmp_path, root)
    assert held.entered.wait(10)
    resp = c.get("/workspace/tree", query_string={"name": "finished"})
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert _rids(html) == [1]
    assert _pending(html) == 1 and "1 run still being scanned" in html
    assert NOMATCH not in html


def test_a_snapshot_published_over_mid_request_refetches(monkeypatch):
    """Hydration can publish (and clear its flag) while a request is still
    filtering the older stub snapshot. The version moved, so the note keeps
    its refetch instead of stranding the partial answer until the next poll."""
    stub = scanner.ExperimentEntry(
        folder_path=None, quam_state_path=None, run_id=1, experiment_name="x",
        timestamp="", status="", qubits=[], qubit_pairs=[], outcomes={},
        parent_ids=[], date_str="2026-01-01", is_standalone=False,
        needs_parse=True)

    class WS:
        reads = 0
        tree = {"/r": [scanner.DateGroup(date_str="2026-01-01", entries=[stub])]}

        @property
        def version(self):
            WS.reads += 1
            return WS.reads             # moves between the two reads

        def hydrating_roots(self):
            return set()

    monkeypatch.setattr(scanner, "FILTER_INLINE_PARSE_MAX", 0)
    ctx = routes._filtered_tree_ctx(WS(), "zzzzqq")
    assert ctx["tree_pending"] == {"/r": 1}
    assert ctx["tree_pending_live"] == {"/r"}


def test_5000_pending_runs_cost_at_most_the_cap_in_reads(monkeypatch):
    """The keystroke cost bound, counted rather than timed (a timing assert
    is a load-dependent flake; the measured milliseconds live in docs/267):
    5,000 pending runs read exactly the cap; a parsed tree reads nothing."""
    reads = []

    def counted(path):
        reads.append(path)
        rid = int(path.parent.name.split("_")[0].lstrip("#"))
        return scanner.ExperimentEntry(
            folder_path=path.parent, quam_state_path=path, run_id=rid,
            experiment_name="resonator_spec", timestamp="2025-02-03T12:00:00",
            status="finished", qubits=["q1"], qubit_pairs=[], outcomes={},
            parent_ids=[], date_str="2025-02-03", is_standalone=False)

    monkeypatch.setattr(scanner, "_parse_experiment_folder", counted)
    stubs = [scanner.ExperimentEntry(
        folder_path=Path(f"/a/2026-01-01/#{i}_placeholder_120000"),
        quam_state_path=Path(f"/a/2026-01-01/#{i}_placeholder_120000/quam_state"),
        run_id=i, experiment_name="placeholder", timestamp="", status="",
        qubits=[], qubit_pairs=[], outcomes={}, parent_ids=[],
        date_str="2026-01-01", is_standalone=False, needs_parse=True)
        for i in range(5000)]
    tree = {"/a": scanner._group_by_date(stubs)}
    cap = scanner.FILTER_INLINE_PARSE_MAX
    pend: dict = {}
    t0 = time.perf_counter()
    out = routes._filter_tree(tree, "finished", pend)
    ms = (time.perf_counter() - t0) * 1000
    assert len(reads) == cap
    assert pend == {"/a": 5000 - cap}
    assert sum(g.count for g in out["/a"]) == cap
    # the newest runs are the ones read when no stub looks like a match
    assert sorted(int(p.parent.name.split("_")[0][1:]) for p in reads) == list(
        range(5000 - cap, 5000))
    parsed_tree = {"/a": scanner._group_by_date([counted(s.quam_state_path) for s in stubs])}
    reads.clear()
    pend.clear()
    out = routes._filter_tree(parsed_tree, "2025 resonator", pend)
    assert reads == [] and pend == {}
    assert sum(g.count for g in out["/a"]) == 5000
    print(f"5,000 pending stubs, parse stubbed: {ms:.1f} ms")
