"""docs/245: an agent's run_node is ALWAYS pinned to its scratch (D-01), and its
proposed writes are only what the node itself recorded (D-02).

D-01: the scheduler passed ``--config-file`` only from ``settings.effective_config``
(empty until someone pressed "Read config"), and only that flag repointed the
qualibrate config's ``[quam] state_path`` -- which wins over QUAM_STATE_PATH for
the framework's own save. So an agent run wrote the LIVE chip. Now an item with
its own scratch is spawned with ``--isolate``; run_experiment pins the config
from the env's own resolution, proves it through qualibrate's resolver, or
refuses the run.

D-02: an offline replay's ``load_from_id`` swaps ``node.machine`` for the stored
run's snapshot; saving it made SM propose that whole snapshot (297 leaves incl.
network.host) as "the node's writes". Now the scratch is its pre-run bytes plus
what the node's ``record_state_updates`` blocks / ``node.state_updates`` recorded,
or -- for a node with no block -- its machine changes since that machine was last
assigned (so a replay's swapped-in snapshot is the baseline, never a write).

The pins use the REAL qualibrate_config / qualibrate resolvers from the cqt env
(skip without them); the node is a stand-in whose save goes where qualibrate's
own resolver says the framework saves.
"""

from __future__ import annotations

import importlib.util
import json
import sys
import threading
from pathlib import Path

import pytest

from quam_state_manager.core import agent_runs, scheduler

pytest.importorskip("qualibrate_config")
pytest.importorskip("tomli_w")
pytest.importorskip("qualibrate.core.config.resolvers")

_RE = Path(__file__).resolve().parents[1] / "quam_state_manager" / "generator" / "run_experiment.py"


@pytest.fixture(scope="module")
def rex():
    sys.path.insert(0, str(_RE.parent))
    spec = importlib.util.spec_from_file_location("run_experiment_isolation_pin", _RE)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


LIVE_STATE = {
    "qubits": {"qA1": {"f_01": 5.0e9, "resonator": {"f_01": 7.1e9, "time_of_flight": 372}},
               "qA2": {"f_01": 5.2e9}},
    "extras": {"data_folder": "D:/live/data"},
}
LIVE_WIRING = {"network": {"host": "127.0.0.1", "port": 1, "cluster_name": "rig"},
               "wiring": {"qubits": {"qA1": {"xy": "#/ports/1"}}}}

# the stored run's snapshot an offline replay swaps in: another day's everything
STORED = {
    "qubits": {"qA1": {"f_01": 4.9e9, "resonator": {"f_01": 7.0e9, "time_of_flight": 300}},
               "qA2": {"f_01": 5.9e9}, "qB9": {"f_01": 1.0}},
    "extras": {"data_folder": "D:/Customer_Codes/elsewhere"},
    "network": {"host": "10.1.1.6", "port": 9510, "cluster_name": "cloud"},
    "wiring": {"qubits": {"qA1": {"xy": "#/ports/9"}}},
}


def _write_chip(folder: Path, state=LIVE_STATE, wiring=LIVE_WIRING) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "state.json").write_text(json.dumps(state, indent=4), encoding="utf-8")
    (folder / "wiring.json").write_text(json.dumps(wiring, indent=4), encoding="utf-8")


def _bytes(folder: Path) -> dict:
    return {n: (folder / n).read_bytes() for n in ("state.json", "wiring.json")}


def _config(path: Path, state_path: Path, *, project: str | None = None,
            storage: str = "D:/data/root") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    proj = f'project = "{project}"\n' if project else ""
    path.write_text(
        "[qualibrate]\nversion = 6\n" + proj +
        "[qualibrate.storage]\ntype = \"local_storage\"\n"
        f"location = \"{storage}\"\n"
        "[quam]\n"
        f"state_path = \"{str(state_path).replace(chr(92), '/')}\"\n", encoding="utf-8")
    return path


def _framework_path(cfg) -> str:
    from qualibrate_config.resolvers import get_qualibrate_config
    from qualibrate.core.config.resolvers import get_quam_state_path
    return str(get_quam_state_path(get_qualibrate_config(Path(cfg))))


# A node stand-in whose save lands exactly where the qualibrate FRAMEWORK would put
# it: the state path qualibrate's own resolver derives from QUALIBRATE_CONFIG_FILE
# (the "Saving machine to active path <chip>" of the D-01 log).
FRAMEWORK_SAVE_NODE = '''
import json
from pathlib import Path
from qualibrate_config.resolvers import get_qualibrate_config, get_qualibrate_config_path
from qualibrate.core.config.resolvers import get_quam_state_path
active = get_quam_state_path(get_qualibrate_config(get_qualibrate_config_path()))
p = Path(active) / "state.json"
d = json.loads(p.read_text(encoding="utf-8"))
d["saved_by_framework"] = True
p.write_text(json.dumps(d), encoding="utf-8")
node = None
'''


@pytest.fixture
def rig(tmp_path, monkeypatch):
    live = tmp_path / "chip"
    _write_chip(live)
    scratch = tmp_path / "inst" / "agent_runs" / "k1" / "quam_state"
    _write_chip(scratch)
    cfg = _config(tmp_path / "qualibrate" / "config.toml", live)
    monkeypatch.setenv("QUALIBRATE_CONFIG_FILE", str(cfg))
    monkeypatch.delenv("QUAM_STATE_PATH", raising=False)
    monkeypatch.setenv("MPLBACKEND", "Agg")
    try:
        from qualibrate.core.config.resolvers import invalidate_settings_cache
        invalidate_settings_cache()
    except Exception:  # noqa: BLE001
        pass
    return {"live": live, "scratch": scratch, "cfg": cfg, "tmp": tmp_path}


# ===========================================================================
# D-01 -- isolation never hangs on a cached setting
# ===========================================================================

class TestSchedulerAlwaysIsolatesAgentItems:
    def _runner(self):
        return {"cancel": threading.Event(), "proc": None, "proc_lock": threading.Lock()}

    def _argv(self, tmp_path, monkeypatch, item_extra: dict, settings_extra: dict | None = None):
        src = tmp_path / "1Q_03_node.py"
        src.write_text('"""n"""\nfrom qualibrate import QualibrationNode\n'
                       'node = QualibrationNode(name="n")\n'
                       '@node.run_action(skip_if=node.modes.external)\n'
                       'def custom_param(node):\n    pass\n', encoding="utf-8")
        captured = {}

        class FakeProc:
            returncode = 0

            def wait(self, timeout=None):
                return 0

        def fake_spawn(argv, log_path):
            captured["argv"] = list(argv)
            return FakeProc(), open(log_path, "wb")

        monkeypatch.setattr(scheduler, "_spawn", fake_spawn)
        item = {"id": "iso00001", "source_file": str(src), "name": "n", "kind": "node",
                "has_hook": True, "targets_name": "qubits", "targets": ["qA1"]}
        item.update(item_extra)
        # NO effective_config: nobody pressed "Read config" (the D-01 precondition)
        settings = {"env_python": "/py", "global_simulate": True, "quam_state_path": "/chip",
                    "default_timeout_s": 60}
        settings.update(settings_extra or {})
        scheduler._run_item(str(tmp_path), item, settings, self._runner())
        return captured["argv"]

    def test_agent_item_without_read_config_is_isolated(self, tmp_path, monkeypatch):
        argv = self._argv(tmp_path, monkeypatch, {"state_path": "/inst/agent_runs/k/quam_state"})
        assert "--config-file" not in argv          # the cached setting is empty ...
        assert "--isolate" in argv                  # ... and isolation does not depend on it
        assert argv[argv.index("--state-path") + 1] == "/inst/agent_runs/k/quam_state"
        assert "--replay" not in argv

    def test_agent_replay_is_flagged(self, tmp_path, monkeypatch):
        argv = self._argv(tmp_path, monkeypatch, {"state_path": "/s",
                                                  "param_overrides": {"load_data_id": 1}})
        assert "--isolate" in argv and "--replay" in argv

    def test_person_item_keeps_the_runner_contract(self, tmp_path, monkeypatch):
        # a person's Runner item runs against the chip itself (documented) -- unchanged
        argv = self._argv(tmp_path, monkeypatch, {},
                          {"effective_config": {"config_file": "/cfg.toml"}})
        assert "--isolate" not in argv
        assert argv[argv.index("--state-path") + 1] == "/chip"
        assert argv[argv.index("--config-file") + 1] == "/cfg.toml"


class TestPinConfigStrict:
    def test_pins_from_the_envs_own_config(self, rex, rig):
        # no config_file given: resolved the way the env would (QUALIBRATE_CONFIG_FILE)
        out, why = rex._pin_config_strict(None, str(rig["scratch"]))
        assert why is None
        assert Path(out).parent == rig["scratch"].parent
        assert rex._same_path(_framework_path(out), rig["scratch"])
        assert rex._same_path(_framework_path(rig["cfg"]), rig["live"])   # original untouched

    def test_project_overlay_survives_and_cannot_win(self, rex, tmp_path):
        # the project's overlay names the LIVE chip and its own storage: the pinned
        # copy must keep the storage and still resolve the scratch
        live, scratch = tmp_path / "live", tmp_path / "runs" / "k" / "quam_state"
        scratch.mkdir(parents=True)
        root = _config(tmp_path / "q" / "config.toml", tmp_path / "root_chip", project="p")
        _config(tmp_path / "q" / "projects" / "p" / "config.toml", live,
                storage="D:/data/project_p")
        assert rex._same_path(_framework_path(root), live)          # the overlay wins at source
        out, why = rex._pin_config_strict(str(root), str(scratch))
        assert why is None
        assert rex._same_path(_framework_path(out), scratch)
        from qualibrate_config.resolvers import get_qualibrate_config
        assert "project_p" in str(get_qualibrate_config(Path(out)).storage.location)

    def test_refuses_when_the_framework_would_still_resolve_elsewhere(self, rex, rig, monkeypatch):
        # writing [quam] state_path is not the proof; qualibrate's resolver is. If it
        # still names another folder (a future precedence rule), the run is refused.
        monkeypatch.setattr(rex, "_framework_state_path", lambda cfg: str(rig["live"]))
        out, why = rex._pin_config_strict(None, str(rig["scratch"]))
        assert out is None and "not the scratch" in why

    def test_refuses_without_a_config(self, rex, rig, monkeypatch):
        monkeypatch.setenv("QUALIBRATE_CONFIG_FILE", str(rig["tmp"] / "nope" / "config.toml"))
        out, why = rex._pin_config_strict(None, str(rig["scratch"]))
        assert out is None and "no qualibrate config file" in why


class TestRunTargetIsolation:
    def test_framework_save_lands_in_the_scratch_not_live(self, rex, rig):
        target = rig["tmp"] / "node.py"
        target.write_text(FRAMEWORK_SAVE_NODE, encoding="utf-8")
        before = _bytes(rig["live"])
        rex.run_target(str(target), str(rig["scratch"]), None, isolate=True)
        assert _bytes(rig["live"]) == before                       # live untouched
        saved = json.loads((rig["scratch"] / "state.json").read_text(encoding="utf-8"))
        assert saved.get("saved_by_framework") is True             # it went to the scratch

    def test_headless_backend_survives_the_pin(self, rig, tmp_path):
        # found on the rig: the pin imports qualibrate -> matplotlib, which fixes the
        # backend at import; with MPLBACKEND set only afterwards a replay hung in
        # tkinter's mainloop on plt.show(). A real subprocess, as the Scheduler spawns it.
        import os
        import subprocess
        target = tmp_path / "node_backend.py"
        probe = tmp_path / "backend.txt"
        target.write_text("import matplotlib\n"
                          f"open({str(probe)!r}, 'w').write(matplotlib.get_backend())\n"
                          "node = None\n", encoding="utf-8")
        env = {k: v for k, v in os.environ.items() if k != "MPLBACKEND"}
        env["QUALIBRATE_CONFIG_FILE"] = str(rig["cfg"])
        env["PYTHONUTF8"] = "1"
        r = subprocess.run([sys.executable, str(_RE), "--mode", "run", "--target", str(target),
                            "--out", str(tmp_path / "w"), "--state-path", str(rig["scratch"]),
                            "--isolate"], env=env, capture_output=True, text=True, timeout=600)
        res = json.loads((tmp_path / "w" / "_result.json").read_text(encoding="utf-8"))
        assert res["status"] == "ok", (res, r.stderr[-2000:])
        assert probe.read_text().lower() == "agg"

    def test_refuses_instead_of_running_unpinned(self, rex, rig, monkeypatch):
        monkeypatch.setenv("QUALIBRATE_CONFIG_FILE", str(rig["tmp"] / "nope" / "config.toml"))
        target = rig["tmp"] / "node.py"
        target.write_text("raise AssertionError('the node must never start')\n", encoding="utf-8")
        with pytest.raises(RuntimeError, match="isolation refused"):
            rex.run_target(str(target), str(rig["scratch"]), None, isolate=True)

    def test_a_refusal_reaches_the_result_envelope(self, rex, rig, monkeypatch):
        monkeypatch.setenv("QUALIBRATE_CONFIG_FILE", str(rig["tmp"] / "nope" / "config.toml"))
        target = rig["tmp"] / "node.py"
        target.write_text("node = None\n", encoding="utf-8")
        out = rig["tmp"] / "work"
        rc = rex.main(["--mode", "run", "--target", str(target), "--out", str(out),
                       "--state-path", str(rig["scratch"]), "--isolate"])
        res = json.loads((out / "_result.json").read_text(encoding="utf-8"))
        assert rc == 1 and res["status"] == "error" and "isolation refused" in res["error"]


# ===========================================================================
# D-02 -- the proposal is what the node itself changed, not the machine it holds
# ===========================================================================

# A node stand-in that goes through qualibrate's REAL QualibrationNode pieces the
# recorder hooks: ``node.machine = ...`` runs the real ``machine`` property (as
# the constructor's ``machine=Quam.load()`` and ``load_from_id`` do), and
# ``record_state_updates`` is the real context manager. Both are looked up on
# QualibrationNode at call time, so the run's wrappers are what run.
NODE_HEAD = '''
import copy, json, sys
from pathlib import Path
from qualibrate.core.qualibration_node import QualibrationNode

SCRATCH = Path(__SCRATCH__)
STORED = json.loads(__STORED__)

class Machine:
    def __init__(self, d):
        self.d = d
    def to_dict(self, include_defaults=True):
        return self.d          # the machine's OWN dict: the recorder must copy it
    def save(self):   # the framework's save: the WHOLE machine it holds, re-serialized
        (SCRATCH / "state.json").write_text(json.dumps(
            {k: v for k, v in self.d.items() if k not in ("network", "wiring")}
            | {"twpa_ext": None}), encoding="utf-8")
        (SCRATCH / "wiring.json").write_text(json.dumps(
            {k: v for k, v in self.d.items() if k in ("network", "wiring")}), encoding="utf-8")

class Modes:
    interactive = False

class Params:
    load_data_id = __LOAD_ID__

class Node:
    modes = Modes()
    parameters = Params()
    name = "03_resonator_spectroscopy_single"
    machine = property(lambda s: QualibrationNode.machine.__get__(s),
                       lambda s, v: QualibrationNode.machine.__set__(s, v))
    def __init__(self, machine):
        self.state_updates = {}
        self.machine = machine          # the constructor's machine=Quam.load()
    def record_state_updates(self, *a, **k):
        return QualibrationNode.record_state_updates(self, *a, **k)
    def log(self, *a, **k):
        pass
    def load_from_id(self, i):          # qualibrate's _load_from_id: self.machine = stored
        self.machine = Machine(copy.deepcopy(STORED))

def _live():
    return json.loads((SCRATCH / "state.json").read_text()) | json.loads((SCRATCH / "wiring.json").read_text())

node = Node(Machine(_live()))
if Params.load_data_id is not None:
    node.load_from_id(Params.load_data_id)
q = node.machine.d["qubits"]["qA1"]
'''

BLOCK_BODY = '''
q["resonator"]["time_of_flight"] = 28          # measurement scaffolding, outside the block
with node.record_state_updates():
    q["resonator"]["f_01"] = 7.2e9
    q["resonator"]["RF_frequency"] = 7.2e9
node.machine.save()
'''

# 17d / 15e style: update_state writes node.machine directly, no block
DIRECT_BODY = '''
node.machine.d["flux_crosstalk_dc"] = {"qubits": ["qA1"], "M_inv": [[1.0]]}
q["resonator"]["f_01"] = 7.3e9
node.machine.save()
'''


def _node_file(tmp: Path, scratch: Path, load_id, body: str = BLOCK_BODY) -> Path:
    src = (NODE_HEAD.replace("__SCRATCH__", repr(str(scratch)))
           .replace("__STORED__", repr(json.dumps(STORED)))
           .replace("__LOAD_ID__", repr(load_id))) + body
    p = tmp / "node_standin.py"
    p.write_text(src, encoding="utf-8")
    return p


def _proposal(scratch: Path) -> dict:
    before = scratch.parent / "before"
    writes, _ = agent_runs.diff_states(before, scratch)
    return {w["path"]: (None if w.get("deleted") else w["new"]) for w in writes}


@pytest.fixture
def run_dir(rig):
    before = rig["scratch"].parent / "before"
    _write_chip(before)            # agent_runs.make_scratch keeps the same bytes here
    return rig


class TestProposalIsTheNodesOwnUpdate:
    def test_offline_replay_proposes_only_the_nodes_update(self, rex, run_dir):
        live_before = _bytes(run_dir["live"])
        target = _node_file(run_dir["tmp"], run_dir["scratch"], 1)
        rex.run_target(str(target), str(run_dir["scratch"]), None, isolate=True, replay=True)
        assert _proposal(run_dir["scratch"]) == {
            "qubits.qA1.resonator.f_01": 7.2e9,
            "qubits.qA1.resonator.RF_frequency": 7.2e9,
        }                                   # not network.host, data_folder, qA2, qB9, ToF 28
        assert _bytes(run_dir["live"]) == live_before
        assert rex._ISOLATION_REPORT["source"] == "record_state_updates"

    def test_replay_without_a_block_proposes_its_direct_writes_not_the_snapshot(self, rex, run_dir):
        # a 17d-style node replayed: it writes node.machine directly, no block. The
        # baseline is the machine load_from_id swapped in, so the stored snapshot's
        # network/data_folder/qA2/qB9 never count -- only what the node did after
        target = _node_file(run_dir["tmp"], run_dir["scratch"], 1, DIRECT_BODY)
        rex.run_target(str(target), str(run_dir["scratch"]), None, isolate=True, replay=True)
        assert _proposal(run_dir["scratch"]) == {
            "flux_crosstalk_dc.qubits.0": "qA1",
            "flux_crosstalk_dc.M_inv.0.0": 1.0,
            "qubits.qA1.resonator.f_01": 7.3e9,
        }
        assert rex._ISOLATION_REPORT["source"] == "machine_since_load"

    def test_normal_run_without_a_block_still_proposes_what_it_wrote(self, rex, run_dir):
        target = _node_file(run_dir["tmp"], run_dir["scratch"], None, DIRECT_BODY)
        rex.run_target(str(target), str(run_dir["scratch"]), None, isolate=True)
        prop = _proposal(run_dir["scratch"])
        assert prop == {"flux_crosstalk_dc.qubits.0": "qA1", "flux_crosstalk_dc.M_inv.0.0": 1.0,
                        "qubits.qA1.resonator.f_01": 7.3e9}
        assert "twpa_ext" not in json.loads((run_dir["scratch"] / "state.json").read_text())

    def test_normal_run_with_a_block_proposes_the_block_and_names_the_rest(self, rex, run_dir, capsys):
        target = _node_file(run_dir["tmp"], run_dir["scratch"], None)
        rex.run_target(str(target), str(run_dir["scratch"]), None, isolate=True)
        assert _proposal(run_dir["scratch"]) == {"qubits.qA1.resonator.f_01": 7.2e9,
                                                 "qubits.qA1.resonator.RF_frequency": 7.2e9}
        assert "twpa_ext" not in json.loads((run_dir["scratch"] / "state.json").read_text())
        err = capsys.readouterr().err
        assert "OUTSIDE its record_state_updates" in err and "time_of_flight" in err
        assert rex._ISOLATION_REPORT["not_proposed"] == ["/qubits/qA1/resonator/time_of_flight"]

    def test_replaced_subtree_drops_the_old_fields(self, rex, run_dir):
        # 15e swaps a pulse for another class: a field the new class lacks must go,
        # or the merged state is unloadable
        body = '''
q["resonator"] = {"f_01": 7.1e9, "kernel": [1, 2]}
node.machine.save()
'''
        target = _node_file(run_dir["tmp"], run_dir["scratch"], None, body)
        rex.run_target(str(target), str(run_dir["scratch"]), None, isolate=True)
        st = json.loads((run_dir["scratch"] / "state.json").read_text())
        assert st["qubits"]["qA1"]["resonator"] == {"f_01": 7.1e9, "kernel": [1, 2]}

    def test_a_write_that_bypassed_node_machine_is_named_not_silently_dropped(self, rex, run_dir, capsys):
        body = '''
p = SCRATCH / "state.json"
d = json.loads(p.read_text()); d["qubits"]["qA2"]["f_01"] = 1.23e9
p.write_text(json.dumps(d))     # a second machine object saved by hand
'''
        target = _node_file(run_dir["tmp"], run_dir["scratch"], None, body)
        rex.run_target(str(target), str(run_dir["scratch"]), None, isolate=True)
        assert _proposal(run_dir["scratch"]) == {}
        err = capsys.readouterr().err
        assert "NOT proposed" in err and "/qubits/qA2/f_01" in err

    def test_reverted_and_recorded_style_is_applied(self, rex, run_dir):
        # interactive_only=False: qualibrate reverts the machine and records node.state_updates
        rec = rex._UpdateRecorder()
        rec.blocks = 1

        class N:
            parameters = None
            state_updates = {"x": {"key": "#/qubits/qA1/resonator/time_of_flight",
                                   "attr": "time_of_flight", "old": 372, "new": 361}}
            machine = None

        orig = rex._snapshot_files(str(run_dir["scratch"]))
        rex._persist_isolated({"node": N()}, str(run_dir["scratch"]), None, orig, rec)
        assert _proposal(run_dir["scratch"]) == {"qubits.qA1.resonator.time_of_flight": 361}

    def test_replay_that_could_not_be_observed_proposes_nothing(self, rex, run_dir):
        rec = rex._UpdateRecorder()        # nothing observed: no block, no assignment

        class P:
            load_data_id = 3

        class M:
            def save(self):
                (run_dir["scratch"] / "wiring.json").write_text(json.dumps(STORED), encoding="utf-8")

        class N:
            parameters = P()
            state_updates = {}
            machine = M()

        orig = rex._snapshot_files(str(run_dir["scratch"]))
        N.machine.save()           # the node's own save of the swapped machine
        rep = rex._persist_isolated({"node": N()}, str(run_dir["scratch"]), None, orig, rec)
        assert _proposal(run_dir["scratch"]) == {} and rep["source"] == "none"

    def test_a_failed_node_leaves_no_swapped_machine_behind(self, rex, run_dir):
        target = run_dir["tmp"] / "node_dies.py"
        target.write_text(
            "import json\nfrom pathlib import Path\n"
            f"p = Path({str(run_dir['scratch'])!r}) / 'wiring.json'\n"
            "p.write_text(json.dumps({'network': {'host': '10.1.1.6'}}))\n"
            "raise RuntimeError('QM connect failed')\n", encoding="utf-8")
        with pytest.raises(RuntimeError, match="QM connect"):
            rex.run_target(str(target), str(run_dir["scratch"]), None, isolate=True)
        assert _proposal(run_dir["scratch"]) == {}

    def test_sys_exit_zero_keeps_the_nodes_update(self, rex, run_dir):
        target = _node_file(run_dir["tmp"], run_dir["scratch"], 1, BLOCK_BODY + "sys.exit(0)\n")
        with pytest.raises(SystemExit):
            rex.run_target(str(target), str(run_dir["scratch"]), None, isolate=True, replay=True)
        assert _proposal(run_dir["scratch"]) == {"qubits.qA1.resonator.f_01": 7.2e9,
                                                 "qubits.qA1.resonator.RF_frequency": 7.2e9}

    def test_recorder_is_removed_after_the_run(self, rex, run_dir):
        from qualibrate.core.qualibration_node import QualibrationNode
        orig_rsu = QualibrationNode.__dict__["record_state_updates"]
        orig_machine = QualibrationNode.__dict__["machine"]
        target = _node_file(run_dir["tmp"], run_dir["scratch"], None)
        rex.run_target(str(target), str(run_dir["scratch"]), None, isolate=True)
        assert QualibrationNode.__dict__["record_state_updates"] is orig_rsu
        assert QualibrationNode.__dict__["machine"] is orig_machine


class TestUnobservedNormalRunKeepsDocs173:
    def test_normal_run_with_nothing_observed_still_saves_the_machine(self, rex, run_dir, monkeypatch):
        calls = []
        monkeypatch.setattr(rex, "_persist_node_state", lambda ns, sp, roots=None: calls.append(sp))

        class N:
            parameters = None
            state_updates = {}

        rex._persist_isolated({"node": N()}, str(run_dir["scratch"]), {"qubits"}, {},
                              rex._UpdateRecorder())
        assert calls == [str(run_dir["scratch"])]
