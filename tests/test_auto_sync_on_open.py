"""Auto-Sync turns itself on when a person opens a chip.

Pins:
  - opening a live chip arms push + pull with "take live, keep my edits"
    (never the destructive replace), and an edit then reaches the chip file;
  - the pill's own gates still refuse: an archive, a read-only folder, an
    already-diverged chip, and a chip another live SM window holds;
  - a person turning it OFF is remembered per chip (reopen and restart);
    turning it ON again clears that;
  - the machine setting OFF is the old behaviour: nothing is armed;
  - the first auto-arm per chip shows one toast, never again.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from quam_state_manager.web import routes as routes_mod
from quam_state_manager.web.app import create_app

_WIRING = {"network": {"host": "1.1.1.1", "cluster_name": "C1"}}
_TOAST = "Auto-Sync is on: your edits are written to the chip file."


def _write_chip(folder: Path, f01=5.0e9):
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "state.json").write_text(json.dumps({
        "qubits": {"qA1": {"id": "qA1", "f_01": f01}},
        "qubit_pairs": {}, "active_qubit_names": ["qA1"]}), encoding="utf-8")
    (folder / "wiring.json").write_text(json.dumps(_WIRING), encoding="utf-8")


@pytest.fixture(autouse=True)
def _production_default(monkeypatch):
    monkeypatch.setenv("SM_AUTO_SYNC_ON_OPEN_DEFAULT", "1")


@pytest.fixture
def world(tmp_path):
    live = tmp_path / "chips" / "live"
    _write_chip(live)
    inst = tmp_path / "_inst"

    def new_app():
        # a restart: the in-memory chip cache is module state, drop it too
        with routes_mod._quam_cache_lock:
            routes_mod._quam_cache.clear()
        app = create_app(testing=True, instance_path=str(inst))
        return app, app.test_client()

    app, c = new_app()
    return {"app": app, "client": c, "live": live, "inst": inst,
            "tmp": tmp_path, "new_app": new_app}


def _open(w, folder=None):
    r = w["client"].post("/load", data={"folder": str(folder or w["live"])})
    assert r.status_code in (200, 302)


def _sess(w):
    with w["app"].app_context():
        return routes_mod._auto_sync_state()


def _tray(w):
    return w["client"].get("/state/tray").data.decode()


class TestArmsOnOpen:
    def test_open_arms_push_and_pull_keep_edits(self, world):
        _open(world)
        s = _sess(world)
        assert s and s["push"] and s["pull"] and not s["pull_replace"]
        assert 'data-auto-apply="1"' in _tray(world)

    def test_an_edit_reaches_the_chip_file(self, world):
        _open(world)
        c = world["client"]
        assert c.post("/field/edit", data={"dot_path": "qubits.qA1.f_01",
                                           "value": "5.1e9"}).status_code == 200
        # the client's flusher presses the one door; do what it does
        r = c.post("/state/apply-to-live")
        assert r.status_code == 200, r.data[:300]
        doc = json.loads((world["live"] / "state.json").read_text(encoding="utf-8"))
        assert doc["qubits"]["qA1"]["f_01"] == pytest.approx(5.1e9)


class TestGatesStillRefuse:
    def test_archive(self, world):
        run = world["tmp"] / "data" / "2026-01-01" / "#1_run" / "quam_state"
        _write_chip(run)
        (run.parent / "node.json").write_text("{}", encoding="utf-8")
        world["client"].post("/workspace/select", data={"path": str(run)})
        with world["app"].app_context():
            ctx = routes_mod._active_ctx()
        if (ctx.get("origin") or "live") == "live":
            # not classified as an archive by this layout: force the origin
            ctx["origin"] = "dataset_archive"
            ctx.pop("auto_apply", None)
            with world["app"].test_request_context():
                assert routes_mod._maybe_auto_arm_on_open(ctx) is False
        assert not ctx.get("auto_apply")

    def _gate(self, world, _keep_open=False, **flags):
        if not _keep_open:
            _open(world)
        with world["app"].app_context():
            ctx = routes_mod._active_ctx()
        ctx.pop("auto_apply", None)
        ctx.update(flags)
        with world["app"].test_request_context():
            assert routes_mod._maybe_auto_arm_on_open(ctx) is False
        assert not ctx.get("auto_apply")

    def test_read_only(self, world):
        self._gate(world, live_readonly_hint=True)

    def test_diverged_with_local_edits(self, world):
        _open(world)
        assert world["client"].post("/field/edit", data={
            "dot_path": "qubits.qA1.f_01", "value": "5.2e9"}).status_code == 200
        self._gate(world, live_diverged=True, _keep_open=True)

    def test_diverged_with_saved_unapplied_work(self, world):
        self._gate(world, live_diverged=True, working_dirty=True)

    def test_diverged_read_only(self, world):
        self._gate(world, live_diverged=True, live_readonly_hint=True)


class TestArmsOverDriftWhileClosed:
    def test_live_changed_while_closed_arms_and_takes_live(self, world):
        _open(world)
        _write_chip(world["live"], f01=5.3e9)        # a node saved while SM was off
        world["app"], world["client"] = world["new_app"]()
        _open(world)
        with world["app"].app_context():
            ctx = routes_mod._active_ctx()
        assert ctx.get("live_diverged")
        s = _sess(world)
        assert s and s["push"] and s["pull"] and not s["pull_replace"]
        r = world["client"].post("/auto-sync/pull")
        assert r.status_code == 200, r.data[:300]
        with world["app"].app_context():
            ctx = routes_mod._active_ctx()
            assert ctx["store"].state["qubits"]["qA1"]["f_01"] == pytest.approx(5.3e9)
            assert not ctx.get("live_diverged")
        assert _sess(world)["push"]

    def test_foreign_window_on_the_same_chip(self, world, monkeypatch):
        from quam_state_manager.core import instances
        from quam_state_manager.core.path_match import fs_key
        key = fs_key(str(world["live"].resolve()))
        monkeypatch.setattr(instances, "peers", lambda *a, **k: [
            instances.Peer(pid=1, chip_fs_key=key)])
        _open(world)
        assert not _sess(world)


class TestUserOffIsRemembered:
    def test_off_survives_reopen_and_restart_on_clears(self, world):
        _open(world)
        c = world["client"]
        assert c.post("/auto-apply/disarm").status_code == 200
        assert not _sess(world)
        _open(world)
        assert not _sess(world), "reopen re-armed a chip the user turned off"
        app2, c2 = world["new_app"]()          # a restart: fresh process state
        world["app"], world["client"] = app2, c2
        _open(world)
        assert not _sess(world), "restart re-armed a chip the user turned off"
        # turning it on again clears the opt-out
        assert c2.post("/auto-sync/set", data={"pull": "1", "push": "1"}).status_code == 200
        c2.post("/auto-apply/disarm")
        c2.post("/auto-sync/set", data={"pull": "1", "push": "1"})
        app3, c3 = world["new_app"]()
        world["app"], world["client"] = app3, c3
        _open(world)
        assert _sess(world)

    def test_panel_all_unticked_counts_as_off(self, world):
        _open(world)
        world["client"].post("/auto-sync/set", data={})
        app2, c2 = world["new_app"]()
        world["app"], world["client"] = app2, c2
        _open(world)
        assert not _sess(world)

    def test_off_is_per_chip(self, world):
        other = world["tmp"] / "chips" / "other"
        _write_chip(other)
        _open(world)
        world["client"].post("/auto-apply/disarm")
        _open(world, other)
        assert _sess(world)


class TestSettingOff:
    def test_setting_off_arms_nothing(self, world):
        r = world["client"].post("/settings/auto-sync-on-open", json={"enabled": False})
        assert r.get_json() == {"ok": True, "enabled": False}
        _open(world)
        assert not _sess(world)
        tray = _tray(world)
        assert 'data-auto-apply="1"' not in tray and _TOAST not in tray

    def test_setting_persists_across_restart(self, world):
        world["client"].post("/settings/auto-sync-on-open", data={"enabled": "0"})
        app2, c2 = world["new_app"]()
        world["app"], world["client"] = app2, c2
        _open(world)
        assert not _sess(world)

    def test_bad_body_is_400(self, world):
        r = world["client"].post("/settings/auto-sync-on-open", json={"enabled": [1]})
        assert r.status_code == 400
        r = world["client"].post("/settings/auto-sync-on-open", json=[1])
        assert r.status_code == 400

    def test_settings_shows_the_toggle(self, world):
        page = world["client"].get("/").data.decode()
        assert 'id="auto-sync-open-toggle"' in page
        assert "Turn on Auto-Sync when a chip opens" in page


class TestToastOnce:
    def test_first_auto_arm_toasts_once(self, world):
        _open(world)
        assert _TOAST in _tray(world)
        assert _TOAST not in _tray(world), "the toast is one-shot"
        world["client"].post("/auto-apply/disarm")
        world["client"].post("/auto-sync/set", data={"pull": "1", "push": "1"})
        app2, c2 = world["new_app"]()
        world["app"], world["client"] = app2, c2
        _open(world)
        assert _sess(world)
        assert _TOAST not in _tray(world), "the toast showed again for the same chip"


class TestPanelDefaults:
    def test_off_panel_ticks_take_and_push_not_replace(self, world):
        world["client"].post("/settings/auto-sync-on-open", json={"enabled": False})
        _open(world)
        assert not _sess(world)
        html = world["client"].get("/auto-sync/panel").data.decode()
        import re
        def ticked(i):
            m = re.search(r'<input[^>]*id="%s"[^>]*>' % i, html, re.S)
            return m and " checked" in m.group(0)
        assert ticked("as-pull") and ticked("as-push") and not ticked("as-pull-replace")

    def test_open_arms_the_one_default_constant(self, world):
        _open(world)
        s = _sess(world)
        assert {k: s[k] for k in routes_mod.AUTO_SYNC_DEFAULT_MODES} == routes_mod.AUTO_SYNC_DEFAULT_MODES
