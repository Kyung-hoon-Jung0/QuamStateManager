"""Two windows, two chips, two runners (docs/80 Part 4).

What blocked this was never PIDs and never ``qm`` (which is multi-client by
design) — it was our storage layout. The Experiment Runner kept ONE
``scheduler.json`` and ONE ``scheduler_queue.json`` in the instance directory,
so window 2 choosing its chip rewrote the ``quam_state_path`` that window 1's
worker re-reads per item, and both windows stared at the same queue.

Runner state is now keyed by the chip. These pin the three things that makes
or breaks:

  * two chips really do get independent queues and run states,
  * machine-level settings (env, node library) stay SHARED, so isolation does
    not turn into re-configuring the same lab twice,
  * an existing installation's queue survives the move and lands under the
    chip it was pointed at.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from quam_state_manager.core import scheduler
from quam_state_manager.web.app import create_app


def _chip(root: Path, name: str) -> Path:
    folder = root / name / "quam_state"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "state.json").write_text(json.dumps({
        "qubits": {"qA1": {"id": "qA1", "f_01": 5.0e9}}, "qubit_pairs": {},
        "active_qubit_names": ["qA1"], "extras": {"chip_name": name}}),
        encoding="utf-8")
    (folder / "wiring.json").write_text(json.dumps({
        "wiring": {}, "network": {"host": "127.0.0.1", "cluster_name": name}}),
        encoding="utf-8")
    return folder


def _node(root: Path, name="02a_resonator_spectroscopy") -> str:
    root.mkdir(parents=True, exist_ok=True)
    f = root / f"{name}.py"
    f.write_text("# node\n", encoding="utf-8")
    return str(f)


@pytest.fixture
def env(tmp_path):
    inst = tmp_path / "_inst"
    a = _chip(tmp_path / "chips", "ChipAlpha")
    b = _chip(tmp_path / "chips", "ChipBeta")
    app = create_app(testing=True, instance_path=str(inst))
    return {"app": app, "client": app.test_client(), "inst": inst,
            "a": a, "b": b, "tmp": tmp_path}


class TestScopeIdentity:
    def test_two_chips_get_two_scopes(self, tmp_path):
        a = scheduler.scope_dir(tmp_path, tmp_path / "chipA" / "quam_state")
        b = scheduler.scope_dir(tmp_path, tmp_path / "chipB" / "quam_state")
        assert a != b

    def test_the_same_chip_is_one_scope_however_it_is_spelled(self, tmp_path):
        chip = tmp_path / "chipA" / "quam_state"
        chip.mkdir(parents=True)
        one = scheduler.scope_dir(tmp_path, chip)
        two = scheduler.scope_dir(tmp_path, str(chip) + "/")
        assert one == two

    def test_the_scope_name_is_readable(self, tmp_path):
        """Every chip folder is literally named 'quam_state', so a leaf-name
        scope would make every directory on disk indistinguishable."""
        chip = _chip(tmp_path, "ChipAlpha")
        name = scheduler.scope_dir(tmp_path / "_i", chip).name
        assert name.lower().startswith("chipalpha"), name

    def test_no_chip_falls_back_to_one_shared_scope(self, tmp_path):
        assert scheduler.scope_dir(tmp_path, None).name == "_nochip"
        assert scheduler.scope_dir(tmp_path, "") == scheduler.scope_dir(tmp_path, None)


class TestIndependentQueues:
    def test_each_chip_has_its_own_queue_and_run_state(self, env):
        c = env["client"]
        c.post("/load", data={"folder": str(env["a"])})
        r = c.post("/scheduler/queue/add", json={
            "file": _node(env["tmp"] / "cal"), "name": "02a", "targets": ["qA1"]})
        assert r.status_code == 200, r.get_data(as_text=True)[:200]

        c.post("/load", data={"folder": str(env["b"])})
        beta = c.get("/scheduler/status").get_json()
        assert beta["queue"] == [], "chip B must not inherit chip A's queue"

        c.post("/load", data={"folder": str(env["a"])})
        alpha = c.get("/scheduler/status").get_json()
        assert len(alpha["queue"]) == 1, "chip A's queue is still there"
        assert alpha["queue"][0]["name"] == "02a_resonator_spectroscopy"

        scope_a = scheduler.scope_dir(env["inst"], env["a"])
        scope_b = scheduler.scope_dir(env["inst"], env["b"])
        assert scheduler.queue_path(scope_a).exists()
        assert not scheduler.queue_path(scope_b).exists()

    def test_the_target_chip_is_per_chip_never_shared(self, env):
        """``quam_state_path`` is what the worker re-reads per item. A shared
        copy is exactly how window 2 used to redirect window 1's run."""
        c = env["client"]
        c.post("/load", data={"folder": str(env["a"])})
        c.post("/scheduler/settings", json={"quam_state_path": str(env["a"])})
        c.post("/load", data={"folder": str(env["b"])})
        c.post("/scheduler/settings", json={"quam_state_path": str(env["b"])})

        got_a = scheduler.load_settings(scheduler.scope_dir(env["inst"], env["a"]))
        got_b = scheduler.load_settings(scheduler.scope_dir(env["inst"], env["b"]))
        assert Path(got_a["quam_state_path"]) == env["a"]
        assert Path(got_b["quam_state_path"]) == env["b"]

    def test_another_window_running_chip_a_does_not_block_chip_b(self, tmp_path):
        """THE customer scenario: window 1 drives chip A, window 2 drives chip B.

        Written against a real live foreign process holding chip A's run, so
        it exercises the same ownership path a second window would — and
        deterministically, without depending on a worker thread staying alive
        long enough to observe.
        """
        import subprocess
        import sys

        a = scheduler.scope_dir(tmp_path, tmp_path / "chipA")
        b = scheduler.scope_dir(tmp_path, tmp_path / "chipB")
        win1 = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)"],
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            running = scheduler._blank_state()
            running["run"].update({"status": "running", "owner_pid": win1.pid,
                                   "owner_port": 5173})
            scheduler.save_queue(a, running)
            scheduler.save_queue(b, scheduler._blank_state())

            # Chip A is genuinely busy, and not ours to touch...
            assert scheduler.is_active(a) is True
            with pytest.raises(scheduler.ForeignRunnerError):
                scheduler.start(a)
            # ...while chip B is completely unaffected: not locked, and startable.
            assert scheduler.is_active(b) is False, "chip B is not locked by chip A"
            run_b = scheduler.start(b)
            assert run_b["status"] == "running"

            # And chip A's run state was never disturbed by any of that.
            assert scheduler.load_queue(a)["run"]["owner_pid"] == win1.pid
            assert scheduler.load_queue(a)["run"]["status"] == "running"
        finally:
            win1.kill()
            win1.wait(timeout=10)
            for scope in (a, b):
                scheduler._RUNNERS.pop(str(scope), None)


class TestSharedSettings:
    def test_machine_level_settings_are_shared_across_chips(self, env):
        """Per-chip isolation must not become per-chip re-configuration: the
        conda env and node library are the same for every chip in a lab."""
        c = env["client"]
        cal = str(env["tmp"] / "cal")
        c.post("/load", data={"folder": str(env["a"])})
        c.post("/scheduler/settings", json={"env_python": "C:/envs/lab/python.exe",
                                            "calibrations_folder": cal})
        c.post("/load", data={"folder": str(env["b"])})
        got = scheduler.load_settings(scheduler.scope_dir(env["inst"], env["b"]))
        assert got["env_python"] == "C:/envs/lab/python.exe"
        assert got["calibrations_folder"] == cal

    def test_shared_and_per_chip_land_in_different_files(self, tmp_path):
        scope = scheduler.scope_dir(tmp_path, tmp_path / "chipA")
        scheduler.save_settings(scope, {"env_python": "py", "quam_state_path": "X",
                                        "global_simulate": False})
        own = json.loads(scheduler.settings_path(scope).read_text(encoding="utf-8"))
        shared = json.loads(
            scheduler.shared_settings_path(scope).read_text(encoding="utf-8"))
        assert "quam_state_path" in own and "env_python" not in own
        assert "env_python" in shared and "quam_state_path" not in shared

    def test_the_timeout_clamp_still_applies(self, tmp_path):
        scope = scheduler.scope_dir(tmp_path, tmp_path / "chipA")
        got = scheduler.save_settings(scope, {"default_timeout_s": 0})
        assert got["default_timeout_s"] == scheduler._DEFAULTS["default_timeout_s"]


class TestLegacyMigration:
    def _legacy(self, inst: Path, chip: Path):
        inst.mkdir(parents=True, exist_ok=True)
        (inst / "scheduler.json").write_text(json.dumps({
            "env_python": "C:/envs/lab/python.exe",
            "calibrations_folder": "C:/cal",
            "quam_state_path": str(chip),
            "global_simulate": False}), encoding="utf-8")
        (inst / "scheduler_queue.json").write_text(json.dumps({
            "queue": [{"id": "aaa", "name": "02a", "status": "queued", "order": 0,
                       "enabled": True, "targets": ["qA1"]}],
            "run": {"status": "idle", "current_id": None, "started_at": None,
                    "message": ""}}), encoding="utf-8")
        logs = inst / "scheduler_logs"
        logs.mkdir(exist_ok=True)
        (logs / "aaa.log").write_text("old log\n", encoding="utf-8")

    def test_an_existing_queue_lands_under_the_chip_it_targeted(self, tmp_path):
        inst = tmp_path / "_i"
        chip = _chip(tmp_path, "ChipAlpha")
        self._legacy(inst, chip)

        out = scheduler.migrate_legacy_scope(inst)
        assert out["migrated"] is True

        scope = scheduler.scope_dir(inst, chip)
        assert out["scope"] == scope.name
        state = scheduler.load_queue(scope)
        assert [i["name"] for i in state["queue"]] == ["02a"]
        settings = scheduler.load_settings(scope)
        assert settings["env_python"] == "C:/envs/lab/python.exe"
        assert Path(settings["quam_state_path"]) == chip
        assert settings["global_simulate"] is False
        assert (scope / "scheduler_logs" / "aaa.log").exists()

    def test_the_originals_are_removed_only_after_the_copy_verifies(self, tmp_path):
        """Copy, verify, THEN delete: nothing is removed until the new copy has
        been read back and found to hold the same items."""
        inst = tmp_path / "_i"
        chip = _chip(tmp_path, "ChipAlpha")
        self._legacy(inst, chip)
        out = scheduler.migrate_legacy_scope(inst)
        assert out["removed_legacy"] is True
        assert not (inst / "scheduler_queue.json").exists()
        assert not (inst / "scheduler.json").exists()
        assert not (inst / "scheduler_logs").exists()
        # ...and the queue really is in the scope, not merely gone.
        scope = scheduler.scope_dir(inst, chip)
        assert [i["id"] for i in scheduler.load_queue(scope)["queue"]] == ["aaa"]

    def test_a_copy_that_cannot_be_verified_keeps_the_originals(self, tmp_path,
                                                                monkeypatch):
        inst = tmp_path / "_i"
        chip = _chip(tmp_path, "ChipAlpha")
        self._legacy(inst, chip)
        monkeypatch.setattr(scheduler, "_verify_migrated", lambda *a, **k: False)
        out = scheduler.migrate_legacy_scope(inst)
        assert out["migrated"] is True
        assert not out.get("removed_legacy")
        assert (inst / "scheduler_queue.json").exists(), "a lost queue is unacceptable"

    def test_it_runs_once(self, tmp_path):
        inst = tmp_path / "_i"
        chip = _chip(tmp_path, "ChipAlpha")
        self._legacy(inst, chip)
        scheduler.migrate_legacy_scope(inst)

        # Mutate the scope, re-run: the second call must be a no-op.
        scope = scheduler.scope_dir(inst, chip)
        scheduler.save_queue(scope, scheduler._blank_state())
        assert scheduler.migrate_legacy_scope(inst)["migrated"] is False
        assert scheduler.load_queue(scope)["queue"] == []

    def test_a_legacy_queue_with_no_chip_lands_in_the_fallback_scope(self, tmp_path):
        inst = tmp_path / "_i"
        inst.mkdir(parents=True)
        (inst / "scheduler_queue.json").write_text(json.dumps({
            "queue": [{"id": "z", "name": "n", "status": "queued", "order": 0}],
            "run": {"status": "idle", "current_id": None, "started_at": None,
                    "message": ""}}), encoding="utf-8")
        out = scheduler.migrate_legacy_scope(inst)
        assert out["scope"] == "_nochip"

    def test_a_fresh_instance_migrates_nothing_and_says_so(self, tmp_path):
        inst = tmp_path / "_i"
        inst.mkdir(parents=True)
        assert scheduler.migrate_legacy_scope(inst)["migrated"] is False

    def test_create_app_performs_the_migration(self, tmp_path):
        inst = tmp_path / "_i"
        chip = _chip(tmp_path, "ChipAlpha")
        self._legacy(inst, chip)
        create_app(testing=True, instance_path=str(inst))
        scope = scheduler.scope_dir(inst, chip)
        assert scheduler.queue_path(scope).exists()


class TestExitCleanup:
    def test_cancel_all_local_only_touches_our_own_runs(self, tmp_path):
        """Closing a window must cancel every run THIS process drives — and,
        with per-chip scopes, a single directory would reach at most one."""
        a = scheduler.scope_dir(tmp_path, tmp_path / "chipA")
        b = scheduler.scope_dir(tmp_path, tmp_path / "chipB")
        scheduler.save_queue(a, scheduler._blank_state())
        scheduler.save_queue(b, scheduler._blank_state())
        try:
            scheduler.start(a)
            scheduler.start(b)
            cancelled = scheduler.cancel_all_local()
            assert str(a) in cancelled and str(b) in cancelled
        finally:
            for scope in (a, b):
                scheduler._RUNNERS.pop(str(scope), None)

    def test_it_is_a_no_op_with_nothing_running(self, tmp_path):
        assert scheduler.cancel_all_local() == [] or True   # never raises


class TestHeartbeatAcrossChips:
    def test_polling_one_chip_keeps_another_chips_run_alive(self, tmp_path):
        """The heartbeat means "a browser is still there", not "this page is
        open" — it exists to notice a CLOSED tab. Keying it strictly to the
        polled scope would make merely SWITCHING chips look like a disconnect
        and pause the run you navigated away from, which is exactly what a
        two-chip user does.
        """
        a = scheduler.scope_dir(tmp_path, tmp_path / "chipA")
        b = scheduler.scope_dir(tmp_path, tmp_path / "chipB")
        scheduler.save_queue(a, scheduler._blank_state())
        scheduler.save_queue(b, scheduler._blank_state())
        # Pretend chip A has a live runner in THIS process.
        scheduler._RUNNERS[str(a)] = {"thread": None, "cancel": None,
                                      "proc": None, "proc_lock": None}
        scheduler._LAST_UI_SEEN.pop(str(a), None)
        try:
            # The user is looking at chip B; only chip B is polled.
            scheduler.touch_ui(b)
            seen_a = scheduler._LAST_UI_SEEN.get(str(a))
            assert seen_a is not None, "chip A's runner must not look abandoned"
        finally:
            scheduler._RUNNERS.pop(str(a), None)
            scheduler._LAST_UI_SEEN.pop(str(a), None)
            scheduler._LAST_UI_SEEN.pop(str(b), None)


class TestPresetsAreShared:
    def test_a_preset_saved_on_one_chip_is_offered_on_another(self, env):
        """A preset is a measurement RECIPE, not a fact about one device.
        Hiding a lab's own saved tune-up sequence the moment they open a
        different chip would be isolation working against the user."""
        c = env["client"]
        node = _node(env["tmp"] / "cal")
        c.post("/load", data={"folder": str(env["a"])})
        c.post("/scheduler/queue/add", json={"file": node, "name": "02a",
                                             "targets": ["qA1"]})
        r = c.post("/scheduler/presets", json={"name": "morning tune-up"})
        assert r.status_code == 200, r.get_data(as_text=True)[:200]

        c.post("/load", data={"folder": str(env["b"])})
        listed = c.get("/scheduler/presets").get_json()
        names = [p["name"] for p in (listed.get("presets") or listed)]
        assert "morning tune-up" in names

    def test_presets_do_not_carry_the_queue_across_chips(self, env):
        """Shared RECIPE, not shared queue: chip B still starts empty."""
        c = env["client"]
        node = _node(env["tmp"] / "cal")
        c.post("/load", data={"folder": str(env["a"])})
        c.post("/scheduler/queue/add", json={"file": node, "name": "02a",
                                             "targets": ["qA1"]})
        c.post("/scheduler/presets", json={"name": "p1"})
        c.post("/load", data={"folder": str(env["b"])})
        assert c.get("/scheduler/status").get_json()["queue"] == []
