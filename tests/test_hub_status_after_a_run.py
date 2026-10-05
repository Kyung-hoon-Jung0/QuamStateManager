"""S8 review P2-2: after a new run, the ledger surfaces redo only what the run changed
on the file system side.

Measured before this (32 qubits, after ONE new run): a 2,000-run chip's Trends
took 0.80-0.86 s, of which 0.53 s was ``Path.resolve`` of every point's run
folder (two Windows file-system calls each, redone on every request because
the folder->dataset-uid memo lived on the per-request table); a 10,000-run
chip took 6-9 s. Each folder is now resolved once per process and each
folder's uid is shared by every request under the same dataset roots.
"""

from __future__ import annotations

from pathlib import Path

from quam_state_manager.core import hub_sync
from quam_state_manager.web import hub_status, routes
from tests.test_hub_drawer import _inline, chip_state, patch, run, sm  # noqa: F401

TRENDS = "/topology/trends?metrics=T1"
GRID = "/param-history?since=all"


def _count_resolves(monkeypatch):
    seen: list[str] = []
    real = Path.resolve

    def counting(self, *a, **kw):
        seen.append(str(self))
        return real(self, *a, **kw)
    monkeypatch.setattr(Path, "resolve", counting)
    return seen


def test_a_new_run_resolves_only_its_own_folder(sm, monkeypatch):
    c = sm["client"]
    hub_status._UID_MEMOS.clear()
    assert c.get(TRENDS).status_code == 200 and c.get(GRID).status_code == 200
    assert any(any("#2_" in str(k) for k in m) for m in hub_status._UID_MEMOS.values()), \
        "the requests share one folder->uid memo (a per-request memo redid it every time)"
    resolved = _count_resolves(monkeypatch)
    run(sm["data"], 5, chip_state(t1=4.0e-5, f01=5.1e9, amp=0.25, alias="#./x180_DragCosine"),
        patches=[patch("qubits.qA1.T1", 4.0e-5, 3.0e-5)])
    with sm["app"].app_context():
        hub_sync.on_roots_moved([str(sm["data"])])
    del resolved[:]
    assert c.get(TRENDS).status_code == 200 and c.get(GRID).status_code == 200
    run_folders = [p for p in resolved if "#" in Path(p).name]
    assert len(set(run_folders)) <= 1 and all("#5_" in Path(p).name for p in run_folders), run_folders


def test_the_resolved_folder_is_the_resolve_answer(tmp_path):
    d = tmp_path / "data" / "2026-01-01" / "#1_scan_000000"
    d.mkdir(parents=True)
    assert routes._resolved_folder(str(d)) == d.resolve()
    assert routes._resolved_folder(str(d)) is routes._resolved_folder(str(d))


def test_one_uid_memo_per_root_set():
    a, b = ("root-a", "k1"), ("root-b", "k2")
    assert hub_status._shared_uid_memo((a,)) is hub_status._shared_uid_memo((a,))
    assert hub_status._shared_uid_memo((a,)) is not hub_status._shared_uid_memo((b,)), \
        "other dataset roots resolve other uids"


def test_a_second_drawer_request_resolves_no_run_folder(sm, monkeypatch):
    """The value drawer (and Column History, Changes) keep a per-request uid memo;
    the folder resolution under it is shared by the process."""
    c = sm["client"]
    assert c.get("/field/history", query_string={"path": "qubits.qA1.T1"}).status_code == 200
    resolved = _count_resolves(monkeypatch)
    assert c.get("/field/history", query_string={"path": "qubits.qA1.T1"}).status_code == 200
    assert not [p for p in resolved if "#" in Path(p).name], resolved
