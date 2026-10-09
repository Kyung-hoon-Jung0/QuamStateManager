"""docs/250 — history names its source folder (B-03).

One history dir per chip IDENTITY is deliberate (the same chip opened from a new
folder keeps one continuous history). But two folders carrying the same
``extras.chip_name`` at the same time — a copied folder, a run's quam_state —
write into that one dir, and the 🕘 popover / the Versions panel / the agent's
history read of folder A answered with folder B's newest value and B's backups
as if they were A's.

Measured repro (docs/250 §1): A edit+apply -> 2 rows; B (copy, same name)
edit+apply -> 2 rows; back on A, ``field_history(A)`` listed B's 5.12 GHz as
the newest change of A's f_01 and the Versions quick-diff "#2 -> #1" showed B's
edit as what just changed on A.

Pins, each mutation-checked (docs/250 §5):
  * a parallel folder's rows leave A's value timeline and are COUNTED
  * a folder copied AFTER A's history keeps A's rows (continuity), labelled
  * a new folder with no own rows yet keeps the whole history, labelled
  * the version chip prefers this folder's own copy of identical content
  * a capture diffs against this folder's previous row, not a parallel one's
  * a take-live backup (captured from a temp folder) is this folder's
  * an ingested run row is a run, never "another folder"
  * no recorded folder -> "source unknown", never this folder's
  * the WSL spelling of this folder is this folder
  * Column History drops the parallel rows too
  * the Versions panel labels other-folder rows and diffs THIS folder
  * State History labels other-folder rows
  * the agent's field-history read carries the same answer
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

import pytest

from quam_state_manager.core import history as hmod
from quam_state_manager.core.history import HistoryManager
from quam_state_manager.core.scanner import ExperimentEntry
from quam_state_manager.web import routes as routes_mod
from quam_state_manager.web.app import create_app

_WIRING = {"network": {"host": "10.0.0.7", "cluster_name": "c1"},
           "wiring": {"qubits": {}}}


def _state(f01: float, anh: float = 2.0e8, name: str | None = "chipX") -> dict:
    st = {"qubits": {"q1": {"id": "q1", "f_01": f01, "anharmonicity": anh,
                            "chi": -5.0e5}},
          "qubit_pairs": {}, "active_qubit_names": ["q1"]}
    if name is not None:
        st["extras"] = {"chip_name": name}
    return st


_bump = [0]


def _write(folder: Path, state: dict) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "state.json").write_text(json.dumps(state), encoding="utf-8")
    (folder / "wiring.json").write_text(json.dumps(_WIRING), encoding="utf-8")
    # a strictly new mtime, so the capture's mtime gate never skips a write
    _bump[0] += 1
    t = time.time() + 50 + _bump[0]
    for n in ("state.json", "wiring.json"):
        os.utime(folder / n, (t, t))


def _snap(hm: HistoryManager, folder: Path, state: dict, **kw):
    _write(folder, state)
    kw.setdefault("force", True)
    meta = hm.check_and_snapshot(str(folder), kw.pop("trigger", "save"), **kw)
    assert meta is not None
    time.sleep(0.002)          # distinct, ordered stamps
    return meta


@pytest.fixture
def two(tmp_path):
    """A has its own history; B (same chip_name) is created afterwards and
    then records alongside A; A records again after B."""
    hm = HistoryManager(str(tmp_path / "_inst"))
    a = tmp_path / "labA" / "quam_state"
    b = tmp_path / "labB" / "quam_state"
    a1 = _snap(hm, a, _state(5.00e9))
    a2 = _snap(hm, a, _state(5.01e9))
    b1 = _snap(hm, b, _state(5.10e9))
    b2 = _snap(hm, b, _state(5.12e9))
    return {"hm": hm, "a": a, "b": b, "a1": a1, "a2": a2, "b1": b1, "b2": b2,
            "tmp": tmp_path}


class TestOneDirTwoFolders:
    def test_both_folders_share_one_chip_dir(self, two):
        """The premise: one identity, one dir (this fix keeps it)."""
        hm = two["hm"]
        assert hm.resolve_chip_dir(two["a"])[0] == hm.resolve_chip_dir(two["b"])[0]
        assert len(hm.list_snapshots(two["a"])) == 4

    def test_a_parallel_folders_rows_leave_the_timeline_and_are_counted(self, two):
        hm = two["hm"]
        out = hm.field_history(two["a"], "qubits.q1.f_01")
        vals = [p["value"] for p in out["points"]]
        assert vals == [5.01e9, 5.00e9], "B's 5.12/5.10 are not A's history"
        assert all(p["source"]["kind"] == "this" for p in out["points"])
        assert out["parallel_hidden"] == 2
        assert [f["label"] for f in out["other_folders"]] == ["labB/quam_state"]
        assert out["other_folders"][0]["snapshots"] == 2

    def test_a_folder_copied_after_keeps_the_earlier_history_labelled(self, two):
        hm = two["hm"]
        out = hm.field_history(two["b"], "qubits.q1.f_01")
        rows = [(p["value"], p["source"]["kind"], p["source"]["label"])
                for p in out["points"]]
        assert rows == [(5.12e9, "this", None), (5.10e9, "this", None),
                        (5.01e9, "other", "labA/quam_state"),
                        (5.00e9, "other", "labA/quam_state")]
        assert out["parallel_hidden"] == 0
        assert {p["source"]["lineage"] for p in out["points"][2:]} == {"earlier"}

    def test_a_new_folder_with_no_rows_yet_keeps_the_whole_history(self, two):
        """The moved-chip case: the identity ladder's continuity survives."""
        hm = two["hm"]
        c = two["tmp"] / "labC" / "quam_state"
        _write(c, _state(5.12e9))
        out = hm.field_history(c, "qubits.q1.f_01")
        assert [p["value"] for p in out["points"]] == [5.12e9, 5.10e9, 5.01e9, 5.00e9]
        assert {p["source"]["kind"] for p in out["points"]} == {"other"}
        assert out["parallel_hidden"] == 0

    def test_the_version_chip_prefers_this_folders_identical_copy(self, two):
        hm, a, b = two["hm"], two["a"], two["b"]
        # B records exactly A's current content LATER (force skips dedup)
        _snap(hm, b, _state(5.01e9))
        b_new = hm.list_snapshots(b)[0].timestamp
        _write(a, _state(5.01e9))
        assert hm.snapshot_ts_for_current_content(a) == two["a2"].timestamp
        _write(b, _state(5.01e9))
        assert hm.snapshot_ts_for_current_content(b) == b_new

    def test_a_capture_diffs_against_this_folders_previous_row(self, two):
        hm, a = two["hm"], two["a"]
        a3 = _snap(hm, a, _state(5.01e9, anh=2.1e8))
        assert a3.diff_summary["total"] == 1, (
            "one leaf changed in A; against B's newest row it would be 2")
        # B's rows now sit BETWEEN A's own rows: still not A's timeline (the
        # cut is A's FIRST own row, not its newest)
        out = hm.field_history(a, "qubits.q1.f_01")
        assert [p["value"] for p in out["points"]] == [5.01e9, 5.00e9]
        assert out["parallel_hidden"] == 2


class TestSourceKinds:
    def test_unrecorded_source_is_unknown_never_this(self, two):
        hm, a = two["hm"], two["a"]
        meta = hm.list_snapshots(a)[-1]
        blank = hmod.SnapshotMeta(**{**{f: getattr(meta, f) for f in
                                        hmod._SNAPSHOT_META_FIELDS},
                                     "source_path": "", "timestamp": "20200101_000000"})
        src = hm.snapshot_source(blank, a)
        assert src["kind"] == "unknown" and src["folder"] is None

    @pytest.mark.skipif(os.name != "nt", reason="the /mnt/<drive> dialect is Windows-only")
    def test_the_wsl_spelling_of_this_folder_is_this_folder(self, two):
        hm, a = two["hm"], two["a"]
        meta = hm.list_snapshots(a)[-1]
        win = str(a.resolve())
        wsl = "/mnt/" + win[0].lower() + win[2:].replace("\\", "/")
        m2 = hmod.SnapshotMeta(**{**{f: getattr(meta, f) for f in
                                     hmod._SNAPSHOT_META_FIELDS},
                                  "source_path": wsl, "timestamp": "20200101_000000"})
        assert hm.snapshot_source(m2, a)["kind"] == "this"

    def test_an_ingested_run_is_a_run_not_another_folder(self, two):
        hm, a = two["hm"], two["a"]
        day = "2026-10-01"
        run = two["tmp"] / "data" / day / "#7_06_ramsey_120000"
        _write(run / "quam_state", _state(5.05e9))
        (run / "node.json").write_text(json.dumps(
            {"created_at": f"{day}T12:00:00", "metadata": {"name": "06_ramsey"}}),
            encoding="utf-8")
        entry = ExperimentEntry(
            folder_path=run, quam_state_path=run / "quam_state", run_id=7,
            experiment_name="06_ramsey", timestamp=f"{day}T12:00:00",
            status="finished", qubits=["q1"], qubit_pairs=[], outcomes={},
            parent_ids=[], date_str=day, is_standalone=False)
        hm.ingest_run(str(a), entry)
        srcs = hm.snapshot_sources(a)
        run_rows = [v for v in srcs.values() if v["kind"] == "run"]
        assert len(run_rows) == 1 and run_rows[0]["lineage"] == "run"
        out = hm.field_history(a, "qubits.q1.f_01")
        assert 5.05e9 in [p["value"] for p in out["points"]], \
            "a run row stays in the timeline (copied-state runs are not flagged)"

    def test_each_folder_keeps_its_own_live_baseline(self, two):
        hm, a, b = two["hm"], two["a"], two["b"]
        hm.set_live_baseline(a, _state(5.01e9), _WIRING)
        assert hm.get_live_baseline(b) is None, "A's baseline is not B's"
        hm.set_live_baseline(b, _state(5.12e9), _WIRING)
        assert hm.get_live_baseline(a)["state"]["qubits"]["q1"]["f_01"] == 5.01e9
        assert hm.get_live_baseline(b)["state"]["qubits"]["q1"]["f_01"] == 5.12e9

    def test_a_legacy_chip_baseline_is_adopted_only_when_provably_this_folders(
            self, two, tmp_path):
        hm, a = two["hm"], two["a"]
        legacy = {"captured_utc": "2026-01-01T00:00:00+00:00", "state_hash": "x",
                  "state": _state(4.9e9), "wiring": _WIRING}
        # a chip recorded from ONE folder only: the old per-chip file is its
        # (own instance: the same fingerprint would adopt chipX's dir)
        hm1 = HistoryManager(str(tmp_path / "_inst_solo"))
        solo = tmp_path / "solo" / "quam_state"
        _snap(hm1, solo, _state(4.8e9))
        sdir = hm1.resolve_chip_dir(solo)[0]
        (sdir / "_baseline.json").write_text(json.dumps(legacy), encoding="utf-8")
        assert hm1.get_live_baseline(solo)["state"]["qubits"]["q1"]["f_01"] == 4.9e9
        # two folders wrote into this dir: nobody can claim the old file
        (hm.resolve_chip_dir(a)[0] / "_baseline.json").write_text(
            json.dumps(legacy), encoding="utf-8")
        assert hm.get_live_baseline(a) is None
        assert hm.get_live_baseline(two["b"]) is None

    def test_column_history_drops_the_parallel_rows(self, two):
        hm, a = two["hm"], two["a"]
        out = hm.column_history(a, {"q1": "qubits.q1.f_01"})
        assert [r[1] for r in out["q1"]] == [5.00e9, 5.01e9]
        out = hm.column_history(a, {"q1": "qubits.q1.chi"})   # scan tier
        assert len(out["q1"]) == 2


# ─── the routes: a real app, two real folders ──────────────────────────────

def _edit(client, path, value):
    r = client.post("/field/edit", data={"dot_path": path, "value": value},
                    headers={"HX-Request": "true"})
    assert r.status_code == 200, r.data[:300]


def _apply(client):
    r = client.post("/state/apply-to-live", headers={"HX-Request": "true"})
    assert r.status_code == 200, r.data[:300]


@pytest.fixture
def app_two(tmp_path):
    a = tmp_path / "labA" / "quam_state"
    b = tmp_path / "labB" / "quam_state"
    _write(a, _state(5.00e9))
    _write(b, _state(5.10e9))
    app = create_app(testing=True, instance_path=str(tmp_path / "_inst"))
    c = app.test_client()
    assert c.post("/load", data={"folder": str(a)}).status_code in (200, 302)
    _edit(c, "qubits.q1.f_01", "5.01e9")
    _apply(c)
    assert c.post("/load", data={"folder": str(b)}).status_code in (200, 302)
    _edit(c, "qubits.q1.f_01", "5.12e9")
    _apply(c)
    assert c.post("/load", data={"folder": str(a)}).status_code in (200, 302)
    return {"app": app, "client": c, "a": a, "b": b, "tmp": tmp_path}


class TestTheSurfaces:
    def test_the_popover_shows_only_this_folders_values_and_says_why(self, app_two):
        html = app_two["client"].get("/field/history?path=qubits.q1.f_01").data.decode()
        assert 'data-value="5120000000.0"' not in html, "B's value is not A's to revert to"
        assert "fh-other-note" in html and "labB/quam_state" in html
        assert "not part of this folder&#39;s timeline" in html \
            or "not part of this folder's timeline" in html

    def test_the_copied_folders_popover_labels_the_earlier_rows(self, app_two):
        c = app_two["client"]
        assert c.post("/load", data={"folder": str(app_two["b"])}).status_code in (200, 302)
        html = c.get("/field/history?path=qubits.q1.f_01").data.decode()
        # A's rows predate B's own history: kept, and named as A's
        assert html.count('class="snap-src fh-srcfolder"') == 2
        assert ">from labA/quam_state<" in html
        # S10 C1.5: snapshot-path title "snapshot of another folder (" -> the ledger drawer's
        # badge title, because the drawer now reads the chip's ledger through B's folder view
        assert "before this folder&#39;s own history began" in html \
            or "before this folder's own history began" in html
        assert "snapshot of the live folder" not in html
        assert "fh-other-note" not in html

    def test_the_versions_panel_labels_and_diffs_this_folder(self, app_two):
        html = app_two["client"].get("/state/versions?changes=all").data.decode()
        assert html.count('class="snap-src sv-src"') == 2, "B's two rows carry its folder"
        assert ">from labB/quam_state<" in html
        # A's own edit, not B's: the quick diff is #4 -> #3 (A's backup -> A's save)
        assert '<span class="sv-quick-ord">#4 → #3</span>' in html
        assert "5000000000.0</span> → <b>5010000000.0</b>" in html
        assert "5120000000.0" not in html.split('class="state-versions-actions"')[0]

    def test_state_history_labels_other_folder_rows(self, app_two):
        # S10 C1.5: snapshot path -> the ledger listing, whose older snapshots are matched
        # against the ledger in the background: wait for it, then count as before
        from quam_state_manager.core import hub_versions
        for chip_dir in (app_two["tmp"] / "_inst" / "history").iterdir():
            if (chip_dir / "ledger.sqlite").exists():
                app_two["client"].get("/state-history")
                assert hub_versions.hashes_for(chip_dir).wait(30)
        html = app_two["client"].get("/state-history").data.decode()
        assert html.count('class="snap-src sh-src"') == 2
        # the Chip Status history drawer lists the same metas
        html = app_two["client"].get("/api/history").data.decode()
        assert html.count('class="snap-src hp-src"') == 2

    def test_the_agent_reads_the_same_answer(self, app_two):
        body = app_two["client"].get(
            "/api/agent/field-history?path=qubits.q1.f_01").get_json()
        pts = body["history"]["points"]
        assert [p["value"] for p in pts] == [5.01e9, 5.0e9]
        # S10 C1.5: 2 (B's snapshots) -> 1 (B's ledger events): the ledger holds B's apply
        # as one SM write, counted once by A's folder view
        # S10 C3: 1 -> 2, B's pre-apply capture now reaches the ledger at once as B's observed
        # state (capture refresh), so A's view hides B's two recorded states: that one and B's write
        assert body["history"]["parallel_hidden"] == 2

    def test_the_agents_versions_name_their_folder(self, app_two):
        # docs/250 at integration: /api/agent/versions read the same rows with nothing saying whose
        body = app_two["client"].get("/api/agent/versions?n=10").get_json()
        kinds = [v["source"]["kind"] for v in body["versions"]]
        assert kinds.count("other") == 2 and kinds.count("this") == 2, kinds
        assert all(v["source"]["label"] == "labB/quam_state"
                   for v in body["versions"] if v["source"]["kind"] == "other")
        assert body["other_folders"], "the other folder is summarized for the agent"

    def test_live_drift_counts_this_folders_changes_only(self, app_two):
        """The State History banner "N parameters changed on the live chip
        since baseline": B's apply re-seeded the ONE per-chip baseline with
        B's content, and A's drift then counted A-vs-B as live changes."""
        app = app_two["app"]
        with app.app_context():
            info = routes_mod._compute_drift(routes_mod._active_ctx(), full=True)
        assert info is not None and info["count"] == 0, info and info.get("entries")

    def test_a_take_live_backup_is_this_folders(self, tmp_path):
        """Captured from <instance>/working_state/<wc>.takelive_backup/... --
        a temporary stand-in for THIS folder's working copy."""
        a = tmp_path / "labA" / "quam_state"
        _write(a, _state(5.00e9))
        app = create_app(testing=True, instance_path=str(tmp_path / "_inst"))
        c = app.test_client()
        assert c.post("/load", data={"folder": str(a)}).status_code in (200, 302)
        _edit(c, "qubits.q1.f_01", "5.02e9")
        _write(a, _state(5.03e9))                     # an outside writer
        r = c.post("/state/sync", data={"mode": "discard", "force": "1"},
                   headers={"HX-Request": "true"})
        assert r.get_json()["status"] == "ok", r.get_json()
        with app.app_context():
            hm = routes_mod._history()
            snaps = hm.list_snapshots(str(a))
            bk = [s for s in snaps
                  if (s.label or "").startswith("Backup before Take live")]
            assert bk and ".takelive_backup" in bk[0].source_path
            src = hm.snapshot_source(bk[0], str(a))
        assert src["kind"] == "this", src
        html = c.get("/state/versions?changes=all").data.decode()
        assert "snap-src" not in html


def test_a_snapshot_annotation_goes_through_safe_io():
    """annotate_snapshot read and replaced meta.json bare; a snapshot listing reading
    the same file at that moment made it fail transiently, and Take live's backup
    silently lost its label (this file's take-live test failed intermittently)."""
    import inspect
    from quam_state_manager.core.history import HistoryManager
    body = inspect.getsource(HistoryManager.annotate_snapshot)
    assert "safe_io.read_json(" in body and "safe_io.atomic_write_json(" in body
    assert ".read_text(" not in body and ".replace(" not in body
