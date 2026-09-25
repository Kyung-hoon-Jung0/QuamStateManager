"""sync-ux 2026-09-25 -- ONE status control + ONE sync panel (the user's decision).

Server halves of the signalling fixes the redesign carries, driven through the
real routes on a real (tmp) chip folder, plus the pure verdict in
``core/sync_status.py`` and the client selfcheck:

  SE-01  with an unapplied edit, an outside write IS announced (poll + tray)
  SE-02  an unparseable live file says "Can't read the live chip", not "Synced"
  SE-05  the verdict signature moves when the state is LOWERED too
  SU-03  the poll carries the stale-cell map ("live now …")
  SE-07  the Keep-mine preflight counts only what the LIVE chip changed
  SE-08  the panel no longer claims "You accepted edited value(s)"
  SU-06  Take live with saved edits is a second press, and keeps a backup
  SYNCEXP-04  a recorded DOM-only collision withholds the auto-pull signal
  SYNCEXP-09  typed-but-not-entered cells are never reported as discarded
  decision 2  same-field collision: picks decide per field
  decision 3  Auto-Sync's first-arm defaults
  decision 4  Take live backs up first; "↶ Bring back" restores the edits
  decision 1  the drift banner and Chip Status' banner are gone
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
from pathlib import Path

import pytest

from quam_state_manager.core import sync_status
from quam_state_manager.web import routes as routes_mod
from quam_state_manager.web.app import create_app

ROOT = Path(__file__).resolve().parent.parent


def _state(t1=(1.1e-05, 1.2e-05, 1.3e-05), t2=2.0e-05) -> dict:
    return {
        "qubits": {f"q{i + 1}": {"id": f"q{i + 1}", "T1": v, "T2ramsey": t2}
                   for i, v in enumerate(t1)},
        "qubit_pairs": {},
        "active_qubit_names": ["q1", "q2", "q3"],
    }


def _wiring() -> dict:
    return {"wiring": {"qubits": {}}, "network": {"host": "10.1.1.18"}}


_bump = [0]


def _write_live(folder: Path, state: dict | str) -> None:
    """An outside writer (an editor Save): rewrite state.json in place with a
    fresh, strictly increasing mtime so the change is unambiguous."""
    p = folder / "state.json"
    p.write_text(state if isinstance(state, str) else json.dumps(state, indent=2),
                 encoding="utf-8")
    _bump[0] += 1
    t = time.time() + 100 + _bump[0]
    os.utime(p, (t, t))


def _live(folder: Path, q: str, key: str = "T1"):
    return json.loads((folder / "state.json").read_text(encoding="utf-8"))["qubits"][q][key]


@pytest.fixture
def chip(tmp_path: Path):
    folder = tmp_path / "chip" / "quam_state"
    folder.mkdir(parents=True)
    (folder / "state.json").write_text(json.dumps(_state(), indent=2), encoding="utf-8")
    (folder / "wiring.json").write_text(json.dumps(_wiring(), indent=2), encoding="utf-8")
    app = create_app(testing=True, instance_path=str(tmp_path / "_inst"))
    client = app.test_client()
    r = client.post("/load", data={"folder": str(folder)})
    assert r.status_code in (200, 302)
    return client, folder


def _drift(client) -> dict:
    return client.get("/state/drift").get_json()


def _edit(client, path: str, value: str) -> None:
    r = client.post("/field/edit", data={"dot_path": path, "value": value},
                    headers={"HX-Request": "true"})
    assert r.status_code == 200, r.data[:300]


# ─── the pure verdict ───────────────────────────────────────────────────────

class TestVerdict:
    def test_precedence(self):
        d = sync_status.derive_state
        base = dict(archive=False, unreadable=False, refused=False, live_moved=False,
                    conflicts=0, unapplied=0, working_dirty=False)
        assert d(**base) == "synced"
        assert d(**{**base, "unapplied": 2}) == "mine"
        assert d(**{**base, "working_dirty": True}) == "staged"
        assert d(**{**base, "live_moved": True}) == "live"
        assert d(**{**base, "live_moved": True, "unapplied": 1}) == "both"
        assert d(**{**base, "live_moved": True, "unapplied": 1, "conflicts": 1}) == "collide"
        assert d(**{**base, "live_moved": True, "unapplied": 1, "refused": True}) == "refused"
        # an unreadable live file makes every live-side claim unknowable
        assert d(**{**base, "live_moved": True, "unapplied": 1, "unreadable": True}) == "unreadable"
        assert d(**{**base, "unreadable": True, "archive": True}) == "archive"

    def test_a_collision_needs_the_edit_to_still_exist(self):
        facts = {"moved": True, "conflicts": ["qubits.q1.T1"], "conflict_rows": [
            {"path": "qubits.q1.T1", "here": "1", "live": "2"}], "live_n": 1, "stale": {}}
        kw = dict(flag_moved=False, flag_count=None, archive=False, unreadable=None,
                  refused=None, working_dirty=False)
        v = sync_status.view(facts=facts, change_paths=["qubits.q1.T1"], unapplied=1, **kw)
        assert v["state"] == "collide"
        v2 = sync_status.view(facts=facts, change_paths=[], unapplied=0, **kw)
        assert v2["state"] == "live", "a discarded edit cannot still collide"

    def test_the_signature_moves_both_ways(self):
        kw = dict(flag_count=None, archive=False, unreadable=None, refused=None,
                  change_paths=[], unapplied=0, working_dirty=False)
        up = sync_status.view(facts=None, flag_moved=True, **kw)
        down = sync_status.view(facts=None, flag_moved=False, **kw)
        assert up["state"] == "live" and down["state"] == "synced"
        assert up["sig"] != down["sig"]

    def test_moved_false_attributes_nothing_to_the_live_side(self):
        class E:
            def __init__(self, p, o, n):
                self.dot_path, self.old_value, self.new_value = p, o, n
        f = sync_status.live_facts(entries=[E("qubits.q1.T1", 1, 2)], moved=False)
        assert f["external"] == [] and f["conflicts"] == [] and f["stale"] == {}


# ─── the poll and the control ───────────────────────────────────────────────

class TestSignals:
    def test_SE01_an_outside_write_is_announced_with_an_unapplied_edit(self, chip):
        client, folder = chip
        _edit(client, "qubits.q2.T1", "5.5e-05")
        assert _drift(client)["sync"]["state"] == "mine"
        _write_live(folder, _state(t1=(5.0e-05, 1.2e-05, 1.3e-05)))
        s = _drift(client)["sync"]
        assert s["state"] == "both", s
        assert s["stale"] == {"qubits.q1.T1": "5e-05"}, "SU-03: the stale cell rides the poll"
        tray = client.get("/state/tray").data.decode()
        assert 'data-sync-state="both"' in tray
        assert "1 unapplied · live changed 1" in tray
        assert s["sig"] in tray, "the control carries the signature the poll compares"

    def test_same_field_is_a_collision(self, chip):
        client, folder = chip
        _edit(client, "qubits.q2.T1", "5.5e-05")
        _write_live(folder, _state(t1=(1.1e-05, 5.0e-05, 1.3e-05)))
        assert _drift(client)["sync"]["state"] == "collide"
        tray = client.get("/state/tray").data.decode()
        assert "1 field changed on both sides" in tray and "Resolve" in tray

    def test_SE02_an_unparseable_live_file_is_said(self, chip, monkeypatch):
        client, folder = chip
        monkeypatch.setattr(routes_mod, "_LIVE_UNREADABLE_AFTER_S", 0.0)
        good = (folder / "state.json").read_text(encoding="utf-8")
        _write_live(folder, '{\n "qubits": {\n  "q1": { "T1": 1.0e-0')
        first = _drift(client)["sync"]["state"]
        assert first != "unreadable", "one torn read is an ordinary save in progress"
        assert _drift(client)["sync"]["state"] == "unreadable"
        tray = client.get("/state/tray").data.decode()
        assert "Can&#39;t read the live chip" in tray or "Can't read the live chip" in tray
        assert "In sync" not in tray
        _write_live(folder, good)
        assert _drift(client)["sync"]["state"] == "synced", "recovers on the next good read"

    def test_SE05_lowering_moves_the_signature(self, chip):
        client, folder = chip
        _write_live(folder, _state(t1=(5.0e-05, 1.2e-05, 1.3e-05)))
        raised = _drift(client)["sync"]
        assert raised["state"] == "live"
        r = client.post("/state/sync", data={"mode": "discard"}, headers={"HX-Request": "true"})
        assert r.get_json()["status"] == "ok"
        lowered = _drift(client)["sync"]
        assert lowered["state"] == "synced" and lowered["sig"] != raised["sig"]


# ─── the panel and its choices ──────────────────────────────────────────────

class TestPanel:
    def test_SE07_preflight_counts_only_live_changes(self, chip):
        client, folder = chip
        _edit(client, "qubits.q2.T1", "7.4e-05")
        _write_live(folder, _state(t1=(7.3e-05, 1.2e-05, 1.3e-05)))
        d = client.get("/state/overwrite-live/preflight").get_json()
        assert d["live_changes"] == 1, d
        assert d["live_paths"] == ["qubits.q1.T1"]
        assert d["unsaved"] == 1

    def test_SE08_no_you_accepted_claim(self, chip):
        client, folder = chip
        _edit(client, "qubits.q2.T1", "7.4e-05")
        html = client.get("/state/review").data.decode()
        assert "You accepted edited value" not in html
        assert "Your unapplied edits (1)" in html

    def test_decision2_pick_live_drops_my_edit(self, chip):
        client, folder = chip
        _edit(client, "qubits.q2.T1", "5.5e-05")
        _write_live(folder, _state(t1=(1.1e-05, 5.0e-05, 3.3e-05)))
        html = client.get("/state/review").data.decode()
        assert 'data-pick-path="qubits.q2.T1"' in html and 'id="sp-merge"' in html
        assert "disabled data-needs-picks" in html, "merge waits for the picks"
        r = client.post("/state/sync", data={"mode": "apply",
                                             "picks": json.dumps({"qubits.q2.T1": "live"})},
                        headers={"HX-Request": "true"})
        assert r.get_json()["status"] == "ok", r.get_json()
        assert _live(folder, "q2") == 5.0e-05 and _live(folder, "q3") == 3.3e-05

    def test_decision2_pick_mine_writes_my_value(self, chip):
        client, folder = chip
        _edit(client, "qubits.q2.T1", "5.5e-05")
        _write_live(folder, _state(t1=(1.1e-05, 5.0e-05, 3.3e-05)))
        _drift(client)
        r = client.post("/state/sync", data={"mode": "apply",
                                             "picks": json.dumps({"qubits.q2.T1": "mine"})},
                        headers={"HX-Request": "true"})
        assert r.get_json()["status"] == "ok", r.get_json()
        assert _live(folder, "q2") == 5.5e-05 and _live(folder, "q3") == 3.3e-05

    def test_a_pick_answers_the_collision_check_for_its_field_only(self, chip):
        client, folder = chip
        _edit(client, "qubits.q2.T1", "5.5e-05")
        _edit(client, "qubits.q3.T1", "6.6e-05")
        _write_live(folder, _state(t1=(1.1e-05, 5.0e-05, 3.3e-05)))
        r = client.post("/state/sync", data={"mode": "apply", "check_collisions": "1",
                                             "picks": json.dumps({"qubits.q2.T1": "mine"})},
                        headers={"HX-Request": "true"})
        body = r.get_json()
        assert body["status"] == "collision" and body["paths"] == ["qubits.q3.T1"]

    def test_decision4_take_live_backs_up_and_brings_back(self, chip):
        client, folder = chip
        _edit(client, "qubits.q2.T1", "5.5e-05")
        _write_live(folder, _state(t1=(4.4e-05, 1.2e-05, 1.3e-05)))
        r = client.post("/state/sync", data={"mode": "discard", "force": "1"},
                        headers={"HX-Request": "true"})
        body = r.get_json()
        assert body["status"] == "ok" and body["backup"]["n"] == 1
        with client.application.app_context():
            snaps = routes_mod._history().list_snapshots(str(folder))
        assert any((s.label or "").startswith("Backup before Take live") for s in snaps), \
            [s.label for s in snaps]
        panel = client.get("/state/review").data.decode()
        assert "sp-bring-back" in panel
        r = client.post("/state/take-live/restore", headers={"HX-Request": "true"})
        assert r.status_code == 200
        tray = client.get("/state/tray").data.decode()
        assert 'data-change-count="1"' in tray, "the edit is back, unapplied"
        assert _live(folder, "q2") == 1.2e-05, "Bring back never writes the live chip"

    def test_SU06_take_live_over_saved_edits_is_a_second_press(self, chip):
        client, folder = chip
        _edit(client, "qubits.q2.T1", "5.5e-05")
        client.post("/save", headers={"HX-Request": "true"})
        _write_live(folder, _state(t1=(4.4e-05, 1.2e-05, 1.3e-05)))
        html = client.get("/state/review").data.decode()
        i = html.index("sp-take")
        tag = html[html.rindex("<button", 0, i):html.index(">", i)]
        assert "sync-arm" in tag and "data-arm-label" in tag, tag
        assert "a backup is kept first" in html

    def test_refused_apply_is_one_row(self, chip):
        client, folder = chip
        html = (ROOT / "quam_state_manager/web/templates/_state_apply_conflict.html").read_text(
            encoding="utf-8")
        assert "doStateSync(" not in html, "the choices live in the panel, not a 4-row tray"
        assert "_sync_control.html" in html


# ─── Auto-Sync ──────────────────────────────────────────────────────────────

class TestAutoSync:
    def test_decision3_first_arm_defaults(self, chip):
        client, _ = chip
        html = (ROOT / "quam_state_manager/web/templates/_auto_sync_panel.html").read_text(
            encoding="utf-8")
        with client.application.test_request_context():
            from flask import render_template
            out = render_template("_auto_sync_panel.html", auto_sync=None)
        def checked(i):
            j = out.index(f'id="{i}"')
            return "checked" in out[j:out.index(">", j)]
        assert checked("as-pull") and not checked("as-pull-replace") and not checked("as-push")

    def test_SYNCEXP09_typed_cells_are_not_discarded_work(self, chip):
        client, folder = chip
        client.post("/auto-sync/set", data={"pull": "1", "pull_replace": "1"},
                    headers={"HX-Request": "true"})
        _write_live(folder, _state(t1=(4.4e-05, 1.2e-05, 1.3e-05)))
        _drift(client)
        r = client.post("/auto-sync/pull?dom_dirty=1&dom_path=qubits.q2.T1",
                        headers={"HX-Request": "true"})
        trig = json.loads(r.headers.get("HX-Trigger") or "{}")
        assert "autoSyncPulled" in trig, (r.status_code, trig)
        assert trig["autoSyncPulled"]["replaced"] is False

    def test_SYNCEXP04_a_dom_collision_stops_the_pull_signal(self, chip):
        client, folder = chip
        client.post("/auto-sync/set", data={"pull": "1"}, headers={"HX-Request": "true"})
        _write_live(folder, _state(t1=(4.4e-05, 1.2e-05, 1.3e-05)))
        assert _drift(client)["auto_pull"] is True
        r = client.post("/auto-sync/pull?dom_dirty=1&dom_path=qubits.q1.T1",
                        headers={"HX-Request": "true"})
        assert r.status_code == 204
        assert _drift(client)["auto_pull"] is False, \
            "the recorded collision must stop the every-5-s re-press"


# ─── the banners are gone ───────────────────────────────────────────────────

class TestNoBanners:
    def test_a_diverged_chip_renders_no_banner(self, chip):
        client, folder = chip
        _write_live(folder, _state(t1=(5.0e-05, 1.2e-05, 1.3e-05)))
        _drift(client)
        page = client.get("/bulk").data.decode()
        assert "live-diverged-banner" not in page
        assert client.get("/state/diverged-banner").data.decode().strip() == ""
        assert 'data-sync-state="live"' in page

    def test_chip_status_inserts_no_banner(self):
        js = (ROOT / "quam_state_manager/web/static/chip-status.js").read_text(encoding="utf-8")
        i = js.index("function showBanner()")
        body = js[i:js.index("function hideBanner()", i)]
        assert "insertBefore" not in body

    def test_client_selfcheck(self):
        node = shutil.which("node")
        if not node:
            pytest.skip("node not installed")
        r = subprocess.run([node, str(ROOT / "tests" / "sync_control_selfcheck.cjs")],
                           capture_output=True, text=True, encoding="utf-8", timeout=300)
        if r.returncode == 2 and "jsdom not installed" in (r.stderr or ""):
            pytest.skip("jsdom not installed")
        assert r.returncode == 0, (r.stdout or "")[-3000:] + (r.stderr or "")[-2000:]



class TestTheDiffTableNeverSplitsAToken:
    """QA fix6 (reviewer P1): the sync panel's diff table wrapped a 13-digit
    frequency mid-number ("3,400,810,798.207" / "0656") and a path mid-word
    ("qubits.q2.xy.RF_fre" / "quency") at 1366 AND 1920 px. The layout is
    real-Chrome verified; these pin the three rules that make it."""

    _CSS = (Path(__file__).resolve().parent.parent / "quam_state_manager" / "web"
            / "static" / "style.css")

    def _rule(self, css, selector):
        import re as _re
        m = _re.search(r"(?m)^" + _re.escape(selector) + r"\s*\{([^}]*)\}", css)
        assert m, selector
        return m.group(1)

    def test_the_table_sizes_its_columns_by_content(self):
        css = self._CSS.read_text(encoding="utf-8")
        body = self._rule(css, ".sp-diff")
        assert "table-layout: auto" in body and "fixed" not in body, body
        assert ".sp-diff th:nth-child(" not in css, \
            "percent column widths lock a value column to a share of the panel"

    def test_values_never_wrap_and_nothing_breaks_anywhere(self):
        css = self._CSS.read_text(encoding="utf-8")
        assert "white-space: nowrap" in self._rule(css, ".sp-diff .sp-val, .sp-diff .sp-delta")
        for sel in (".sp-diff td", ".sp-diff code"):
            body = self._rule(css, sel)
            assert "anywhere" not in body and "break-all" not in body, (sel, body)

    def test_a_path_breaks_only_at_its_dots(self, tmp_path):
        from flask import render_template
        app = create_app(testing=True, instance_path=str(tmp_path / "_inst"))
        with app.test_request_context():
            html = render_template(
                "_state_review.html", sync={"state": "mine", "sig": "s"},
                mine_rows=[{"path": "qubits.q2.xy.RF_<b>freq", "new": 3400900000.0,
                            "old": 3400810798.2070656, "live": 3400810798.2070656,
                            "index": 0, "gid": "", "created": False, "deleted": False}],
                unsaved=1, change_sig="x")
        assert "qubits.<wbr>q2.<wbr>xy.<wbr>RF_&lt;b&gt;freq</code>" in html, \
            "the path is split at dots only, and each segment stays escaped"
