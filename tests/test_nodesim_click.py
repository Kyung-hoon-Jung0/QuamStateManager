"""Simulate a node, server core (core/nodesim_click.py): states, contract keys,
SaaS only, cache, prune and the fault-injection rows A-1..A-9 of the P1 spec.

The click child is faked: a FakeProc reads the two stdin lines and writes the
child's ``_status.json`` / ``_result.json`` like the real one would. No QM
stack, no cloud simulator and no live chip are touched; every file is under
``tmp_path``.
"""
from __future__ import annotations

import ast
import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from quam_state_manager.core import nodesim_click as click
from quam_state_manager.core import port_test_runs as ptr

ROOT = Path(__file__).resolve().parents[1]
PW = "pw-Zq81-secret"
CREDS = {"email": "user@example.com", "host": "saas.example.invalid", "password": PW, "version": "v3_7_3"}


def ok_result(**over):
    out = {"ok": True, "kind": "ok", "message": "", "script": "# script\nprogram()\n", "script_ok": True,
           "program_chars": 17, "qop_version": "v3_7_3", "window_ns": 20000, "last_adc_end_ns": 900,
           "guard_lines": [], "report": {"analog_waveforms": [], "adc_acquisitions": [{"end_time": 900}]}}
    out.update(over)
    return out


class FakeStdin:
    def __init__(self, proc):
        self.proc, self.buf = proc, ""

    def write(self, text):
        self.buf += text
        self.proc.lines = [x for x in self.buf.split("\n") if x]
        if "stop" in self.proc.lines:
            self.proc.stop_seen.set()
        if len(self.proc.lines) >= 2:
            self.proc.got_request.set()

    def flush(self):
        pass

    def close(self):
        pass


class FakeProc:
    """The click child: ``behave(proc, work)`` runs once the request arrived."""

    def __init__(self, argv, kw, behave):
        self.argv, self.kw, self.pid, self.returncode = argv, kw, 999999, None
        self.lines, self.got_request, self.stop_seen = [], threading.Event(), threading.Event()
        self.stdin = FakeStdin(self)
        self.work = Path(argv[3])
        self.killed = False

        def run():
            if self.got_request.wait(5.0):
                behave(self, self.work)
            if self.returncode is None:
                self.returncode = 0
        threading.Thread(target=run, daemon=True).start()

    def poll(self):
        return self.returncode

    def kill(self):
        self.killed = True
        self.returncode = -9

    def wait(self, timeout=None):
        return self.returncode


def child_writes(result, phases=(("build", 0.5), ("login", 0.1)), stdout=""):
    def behave(proc, work):
        (work / "_status.json").write_text(json.dumps(
            {"phase": "close", "phases": [{"name": n, "s": s} for n, s in phases]}), encoding="utf-8")
        if stdout:
            (work / "stdout.log").write_text(stdout, encoding="utf-8")
        if result is not None:
            (work / "_result.json").write_text(json.dumps(result), encoding="utf-8")
    return behave


class Rig:
    def __init__(self, tmp: Path):
        self.tmp = tmp
        self.lib = tmp / "lib"
        (self.lib / "quam_config").mkdir(parents=True)
        (self.lib / "quam_config" / "__init__.py").write_text("x = 1\n", encoding="utf-8")
        (self.lib / "calibration_utils").mkdir()
        self.calib = self.lib / "calibrations"
        self.calib.mkdir()
        self.node = self.calib / "11_rabi_demo.py"
        self.node.write_text("# node\n", encoding="utf-8")
        self.pair_node = self.calib / "32a_cz_demo.py"
        self.pair_node.write_text("# pair node\n", encoding="utf-8")
        self.live = tmp / "live"
        self.live.mkdir()
        self.state = {"qubits": {"q1": {}, "q2": {}}, "qubit_pairs": {"q1-2": {}},
                      "ports": {"mw_outputs": {"con1": {}}}}
        (self.live / "state.json").write_text(json.dumps(self.state), encoding="utf-8")
        (self.live / "wiring.json").write_text(json.dumps({"network": {"host": "127.0.0.1", "port": 1}}),
                                               encoding="utf-8")
        self.procs, self.copies, self.removed, self.probed = [], [], [], []
        self.behave = child_writes(ok_result())
        self.version = "1.2.0"
        self.m = click.Manager(tmp / "inst", popen=self.popen, probe=self.probe,
                               make_copy=self.make_copy, remove_copy=self.remove_copy)

    def popen(self, argv, **kw):
        self.procs.append(FakeProc(argv, kw, self.behave))
        return self.procs[-1]

    def probe(self, python):
        self.probed.append(python)
        return self.version

    def make_copy(self, store):
        root = self.tmp / f"copy{len(self.copies)}"
        after = root / "after"
        after.mkdir(parents=True)
        for name in ("state.json", "wiring.json"):
            (after / name).write_bytes((self.live / name).read_bytes())
        c = SimpleNamespace(root=root, after=after, before=root / "before", seq=7, store_id=11)
        self.copies.append(c)
        return c

    def remove_copy(self, c):
        self.removed.append(c)

    def info(self, node=None, targets_name="qubits", kind="node", error=None):
        node = node or self.node
        return SimpleNamespace(file=str(node), name=Path(node).stem, kind=kind,
                               targets_name=targets_name, error=error)

    def start(self, **over):
        kw = dict(python=sys.executable, env_name="envx", library=self.lib, info=self.info(),
                  targets=["q1"], choices=["q1", "q2", "q1-2"], window_us=20, saas=dict(CREDS),
                  store=object(), live_folder=self.live)
        kw.update(over)
        window = kw.pop("window", "w1")
        return self.m.start(window, **kw)

    def wait(self, rid, window="w1", cap=100, seq=None):
        n, out = 0, None
        while n < cap:
            n += 1
            out = self.m.status(window, rid, seq=seq)
            if out["state"] not in ("starting", "running"):
                return out
            time.sleep(0.05)
        return out


@pytest.fixture
def rig(tmp_path):
    return Rig(tmp_path)


def contract(ans):
    assert set(ans) >= {"state", "line", *click.DEFAULTS}, ans
    assert ans["state"] in click.STATES
    assert isinstance(ans["line"], str) and ans["line"]
    assert isinstance(ans["phases"], list) and isinstance(ans["banners"], list)
    assert isinstance(ans["cached"], bool)
    for bad in (" -- ", "CAS", "patch"):
        assert bad not in ans["line"]
    return ans


# --- every state of spec 1.2 ---------------------------------------------------

class TestStates:
    def test_none(self, rig):
        assert contract(rig.m.status("w1"))["state"] == "none"

    def test_no_env(self, rig):
        a = contract(rig.start(python=None))
        assert a["state"] == "no_env" and a["line"] == click.LINES["no_env"]

    def test_no_library(self, rig):
        assert contract(rig.start(library=None))["state"] == "no_library"

    def test_library_root_is_nearest_ancestor_with_quam_config(self, rig, tmp_path):
        assert click.library_root(rig.calib) == rig.lib.resolve()
        (tmp_path / "bare").mkdir()
        assert click.library_root(tmp_path / "bare") is None
        assert click.library_root("") is None

    def test_refused_by_prefix_and_kind(self, rig):
        for stem in ("15p_x", "18b_x", "38_xeb", "04d_helper"):
            p = rig.calib / f"{stem}.py"
            p.write_text("#\n", encoding="utf-8")
            a = contract(rig.start(info=rig.info(p)))
            assert a["state"] == "refused" and a["line"].startswith("This node is not simulated here: ")
        assert contract(rig.start(info=rig.info(kind="graph")))["state"] == "refused"
        assert contract(rig.start(info=None))["state"] == "refused"
        assert rig.procs == []

    def test_busy(self, rig):
        gate = threading.Event()
        rig.behave = lambda proc, work: gate.wait(5.0)
        a = rig.start()
        assert a["state"] == "running"
        assert contract(rig.start())["state"] == "busy"
        gate.set()
        rig.wait(a["id"])

    def test_starting_and_running_phases(self, rig):
        gate = threading.Event()

        def behave(proc, work):
            (work / "_status.json").write_text(json.dumps({"phase": "spawn", "phases": [
                {"name": "build", "s": 7.5}, {"name": "login", "s": 1.0}]}), encoding="utf-8")
            gate.wait(5.0)
            (work / "_result.json").write_text(json.dumps(ok_result()), encoding="utf-8")
        rig.behave = behave
        a = contract(rig.start())
        assert a["state"] == "running" and a["phase"] == "build" and a["estimate_s"] > 0
        n = 0
        st = rig.m.status("w1", a["id"])
        while st["phase"] != "spawn" and n < 100:
            n += 1
            time.sleep(0.02)
            st = rig.m.status("w1", a["id"])
        assert contract(st)["state"] == "starting"
        assert st["line"] == "Starting a cloud simulator instance (about 62.5 s)."
        assert [p["name"] for p in st["phases"]] == ["copy", "build", "login"]
        gate.set()
        assert rig.wait(a["id"])["state"] == "done"

    def test_done_banners_provenance_and_notes(self, rig):
        a = rig.start()
        d = contract(rig.wait(a["id"]))
        assert d["state"] == "done"
        assert d["line"] == "Simulated 20 us of 11_rabi_demo.py on q1. Report only, no samples."
        assert d["banners"][0] == "REDUCED SIMULATION" and "partial pass" in d["banners"]
        assert "MW-FEM amplitude 0.50x on this QOP (measured)" in d["banners"]
        res = d["result"]
        assert set(res) >= {"lanes", "marks", "notes", "cannot_draw", "script_ok", "provenance"}
        prov = res["provenance"]
        assert prov["seq"] == 7 and prov["text"].startswith("Simulated from chip copy seq 7, 11_rabi_demo.py @ ")
        assert click.NUM_SHOTS_NOTE in res["notes"] and click.NO_READOUT_NOTE not in res["notes"]
        assert [p["name"] for p in d["phases"]] == ["copy", "build", "login"]
        assert rig.removed == rig.copies     # the copy goes after the run
        req = json.loads(rig.procs[0].lines[1])
        assert req["window_ns"] == 20000 and req["overrides"] == {"qubits": ["q1"], "num_shots": 1}
        assert set(req) == {"scratch", "chip_copy", "library", "node", "targets", "window_ns", "overrides",
                            "saas_host"}
        assert req["saas_host"] == CREDS["host"]  # the child refuses any other host (1a)
        run = json.loads((rig.procs[0].work / "_run.json").read_text(encoding="utf-8"))
        assert run["SIMULATED"] is True and run["meta"]["target"] == "saas"

    def test_done_stale_after_the_chip_moves(self, rig):
        a = rig.start()
        rig.wait(a["id"])
        d = rig.m.status("w1", a["id"], seq=8)
        assert d["result"]["provenance"]["stale"] is True
        assert d["result"]["provenance"]["stale_line"] == "chip changed since - re-simulate"
        assert "chip changed since - re-simulate" in d["banners"]
        assert d["result"]["provenance"]["line"] == d["result"]["provenance"]["text"]
        fresh = rig.m.status("w1", a["id"], seq=7)
        assert fresh["result"]["provenance"]["stale"] is False and click.STALE_LINE not in fresh["banners"]

    def test_unverified_amplitude_and_no_readout_and_script_incomplete(self, rig):
        rig.behave = child_writes(ok_result(qop_version="v3_6_0", script_ok=False,
                                            report={"adc_acquisitions": []}))
        d = rig.wait(rig.start()["id"])
        assert "amplitude scale not verified for this QOP" in d["banners"]
        assert "script incomplete" in d["banners"]
        assert click.NO_READOUT_NOTE in d["result"]["notes"]

    @pytest.mark.parametrize("kind,state,start", [
        ("node_error", "node_error", "The node stopped while building its program: boom"),
        ("serializer", "serializer", "qm could not turn this program into a script: boom"),
        ("too_long", "too_long", "boom\nThe cloud simulator refused a window this long."),
        ("failed", "failed", "boom"),
        ("refused", "refused", "This node is not simulated here: boom"),
    ])
    def test_child_kinds(self, rig, kind, state, start):
        rig.behave = child_writes(ok_result(ok=False, kind=kind, message="boom", report=None))
        d = contract(rig.wait(rig.start()["id"]))
        assert d["state"] == state and d["line"].startswith(start), d["line"]

    def test_serializer_with_empty_targets_line(self):
        assert click.line("serializer", message="x", targets_empty=True).endswith(click.EMPTY_TARGETS_LINE)

    def test_guard(self, rig):
        gl = "GUARD FAIL connect 10.0.0.1:80"
        rig.behave = child_writes(ok_result(ok=False, kind="guard", message="", guard_lines=[gl]),
                                  stdout=gl + "\n")
        d = contract(rig.wait(rig.start()["id"]))
        assert d["state"] == "guard" and d["line"] == "Stopped by the safety guard: " + gl

    def test_child_timeout_uses_the_measured_wait(self, rig):
        rig.behave = child_writes(ok_result(ok=False, kind="timeout", report=None),
                                  phases=(("build", 1.0), ("wait", 120.0)))
        d = contract(rig.wait(rig.start()["id"]))
        assert d["state"] == "timeout"
        assert d["line"] == "No answer from the cloud simulator after 120 s; the instance was closed."

    def test_server_time_limit_is_timeout(self, rig):
        rig.m.limit_s = 0.2
        rig.behave = lambda proc, work: proc.stop_seen.wait(5.0)
        d = contract(rig.wait(rig.start()["id"]))
        assert d["state"] == "timeout" and "after 0.2 s" in d["line"]

    def test_stop_pressed(self, rig):
        rig.behave = lambda proc, work: proc.stop_seen.wait(5.0)
        a = rig.start()
        rig.m.stop("w1", a["id"])
        d = contract(rig.wait(a["id"]))
        assert d["state"] == "stopped" and d["line"] == "Stopped. The cloud instance was closed."
        assert (rig.procs[0].work / "stop").is_file()

    def test_failed_without_result(self, rig):
        rig.behave = child_writes(None)
        d = contract(rig.wait(rig.start()["id"]))
        assert d["state"] == "failed" and "without an answer" in d["line"]

    def test_failed_on_unknown_target_and_bad_window(self, rig):
        assert rig.start(targets=["q9"])["state"] == "failed"
        assert rig.start(window_us="abc")["state"] == "failed"
        assert rig.start(window_us=0)["state"] == "failed"
        assert rig.procs == []

    def test_answer_rejects_unknown_state(self):
        with pytest.raises(ValueError):
            click.answer("nope")

    def test_every_state_has_a_line(self):
        for s in click.STATES:
            contract(click.answer(s, message="m", reason="r", env="e", found="1.0", guard_line="g",
                                  limit_s=1, node="n", targets=["q1"], window_us=20))


# --- fault injection A-1..A-9 ----------------------------------------------------

class TestFaultInjection:
    def test_a1_old_qm_saas_is_blocked(self, rig):
        rig.version = "1.1.0"
        a = contract(rig.start())
        assert a["state"] == "blocked"
        assert "qm-saas 1.1.0" in a["line"] and "1.2.0 or newer" in a["line"]
        assert f'{sys.executable} -m pip install "qm-saas>=1.2.0"' in a["line"]
        assert rig.procs == [] and rig.copies == []
        rig.version = None
        assert "is not installed" in rig.start()["line"]

    def test_a2_no_password_is_not_configured(self, rig):
        a = contract(rig.start(saas={**CREDS, "password": ""}))
        assert a["state"] == "not_configured" and a["line"] == click.LINES["not_configured"]
        assert rig.start(saas=None)["state"] == "not_configured"
        assert rig.procs == []

    def test_a3_empty_targets_for_a_pair_node(self, rig):
        a = contract(rig.start(info=rig.info(rig.pair_node, targets_name="qubit_pairs"), targets=[]))
        assert a["state"] == "needs_targets" and a["line"] == click.LINES["needs_targets"]
        assert rig.procs == [] and rig.copies == []

    def test_a4_window_above_100_is_refused_before_any_spawn(self, rig):
        a = contract(rig.start(window_us=150))
        assert a["state"] == "too_long"
        assert a["line"] == ("A 150 us window is above the 100 us limit.\n"
                             "The cloud simulator refused a window this long.")
        assert rig.procs == [] and rig.copies == []
        ok = rig.start(window_us=100)
        assert json.loads(rig.procs[0].lines[1])["window_ns"] == 100_000
        rig.wait(ok["id"])

    def test_a5_live_change_drops_the_result(self, rig):
        def behave(proc, work):
            (rig.live / "state.json").write_text(json.dumps({**rig.state, "touched": 1}), encoding="utf-8")
            child_writes(ok_result())(proc, work)
        rig.behave = behave
        d = contract(rig.wait(rig.start()["id"]))
        assert d["state"] == "chip_changed" and d["line"] == click.LINES["chip_changed"]
        assert d["result"] is None and rig.m.cache.entries == {}

    def test_a6_two_result_jsons_fail(self, rig):
        two = json.dumps({"ok": True, "kind": "ok"})
        rig.behave = child_writes(ok_result(), stdout=two + "\n" + two + "\n")
        d = contract(rig.wait(rig.start()["id"]))
        assert d["state"] == "failed" and d["line"] == click.DISAGREE
        assert rig.m.cache.entries == {}

    def test_a6_guard_line_not_matching_the_verdict_fails(self, rig):
        rig.behave = child_writes(ok_result(), stdout="GUARD FAIL connect 10.0.0.1:80\n")
        d = contract(rig.wait(rig.start()["id"]))
        assert d["state"] == "failed" and d["line"] == click.DISAGREE

    def test_a6_guard_kind_without_lines_fails(self, rig):
        rig.behave = child_writes(ok_result(ok=False, kind="guard", report=None))
        assert rig.wait(rig.start()["id"])["state"] == "failed"

    def test_a6_unreadable_result_fails(self, rig):
        bad = ok_result()
        del bad["guard_lines"]
        rig.behave = child_writes(bad)
        d = rig.wait(rig.start()["id"])
        assert d["state"] == "failed" and d["line"] == click.UNREADABLE

    def test_a7_settings_target_opx_still_sends_saas_only(self, rig):
        ptr.save_settings(rig.m.instance, {"target": "opx", "opx": {"host": "10.1.2.3", "port": 9510},
                                           "saas": {"email": CREDS["email"], "host": CREDS["host"]}})
        (rig.live / "wiring.json").write_text(json.dumps({"network": {"host": "10.9.9.9", "port": 9510}}),
                                              encoding="utf-8")
        a = rig.start()
        conn = json.loads(rig.procs[0].lines[0])
        assert list(conn) == ["saas"] and conn["saas"]["host"] == CREDS["host"]
        assert "10.1.2.3" not in rig.procs[0].lines[0] + rig.procs[0].lines[1]
        assert "9510" not in rig.procs[0].lines[0]
        rig.wait(a["id"])

    def test_a8_password_scrubbed_from_answer_and_files(self, rig):
        rig.behave = child_writes(ok_result(ok=False, kind="failed", message=f"login {PW} refused", report=None),
                                  stdout=f"debug {PW}\n")
        d = rig.wait(rig.start()["id"])
        assert d["state"] == "failed" and PW not in json.dumps(d) and "***" in d["line"]
        work = rig.procs[0].work
        for p in work.iterdir():
            assert PW not in p.read_text(encoding="utf-8", errors="replace"), p.name
        rig.behave = child_writes(ok_result(message=PW, script=f"# {PW}\n"))
        d2 = rig.wait(rig.start()["id"])
        assert d2["state"] == "done" and PW not in json.dumps(d2)
        for p in rig.procs[1].work.iterdir():
            assert PW not in p.read_text(encoding="utf-8", errors="replace"), p.name
        assert PW not in json.dumps(rig.m.cache.entries)

    def test_a9_no_poll_for_13_s_stops_the_run(self, rig):
        rig.behave = lambda proc, work: proc.stop_seen.wait(10.0)
        a = rig.start()
        run = rig.m.runs("w1").current
        run["seen"] = time.time() - 13.0       # the page has not asked for 13 s
        n = 0
        while not rig.procs[0].stop_seen.is_set() and n < 50:
            n += 1
            time.sleep(0.1)
        assert run["stop_why"] == "the page was left"
        d = contract(rig.wait(a["id"]))
        assert d["state"] == "stopped"


# --- SaaS only (spec section 3 item 4) ------------------------------------------

FORBIDDEN = ("target_of", "run_sim", '"opx"', "'opx'", "9510", "19510")


@pytest.mark.parametrize("rel", ["quam_state_manager/core/nodesim_click.py",
                                 "quam_state_manager/generator/nodesim_click.py"])
def test_saas_only_text_scan(rel):
    path = ROOT / rel
    if not path.is_file():
        pytest.skip(f"{rel} is built by another piece")
    text = path.read_text(encoding="utf-8")
    for bad in FORBIDDEN:
        assert bad not in text, (rel, bad)
    names = {n.id for n in ast.walk(ast.parse(text)) if isinstance(n, ast.Name)}
    names |= {n.attr for n in ast.walk(ast.parse(text)) if isinstance(n, ast.Attribute)}
    assert not names & {"target_of", "run_sim"}


def test_saas_only_scan_can_fail(tmp_path):
    bad = tmp_path / "x.py"
    bad.write_text('t = sim.target_of(settings)\nif t == "opx": pass\n', encoding="utf-8")
    text = bad.read_text(encoding="utf-8")
    assert any(b in text for b in FORBIDDEN)


def test_no_qm_import_in_a_fresh_interpreter():
    code = ("import sys; import quam_state_manager.web.nodesim_routes, quam_state_manager.core.nodesim_click; "
            "bad = [m for m in ('qm', 'quam', 'qm_saas') if m in sys.modules]; print(bad)")
    env = {**os.environ, "PYTHONPATH": str(ROOT)}
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=120,
                         cwd=str(ROOT), env=env)
    assert out.returncode == 0, out.stderr[-2000:]
    assert out.stdout.strip().splitlines()[-1] == "[]"


# --- cache, prune, files ---------------------------------------------------------

class TestCache:
    def test_hit_and_miss(self, rig):
        d1 = rig.wait(rig.start()["id"])
        assert d1["state"] == "done" and d1["cached"] is False and len(rig.procs) == 1
        d2 = rig.start()
        assert d2["state"] == "done" and d2["cached"] is True and len(rig.procs) == 1
        assert [p["name"] for p in d2["phases"]] == ["copy", "cache"]
        assert d2["result"]["report"] == d1["result"]["report"]
        assert rig.m.status("w1", d2["id"])["cached"] is True
        d3 = rig.start(window_us=50)                 # another window: a miss
        assert d3["state"] == "running" and len(rig.procs) == 2
        rig.wait(d3["id"])
        rig.node.write_text("# node edited\n", encoding="utf-8")
        d4 = rig.start()                             # the node changed: a miss
        assert d4["state"] == "running"
        rig.wait(d4["id"])

    def test_evict_at_21(self):
        c = click.Cache()
        for i in range(21):
            c.put(f"i{i}", f"k{i}", {"result": {"n": i}})
        assert len(c.entries) == 20 and c.get("i0") is None and c.get("i1") == {"result": {"n": 1}}
        c.get("i1")
        c.put("i21", "k21", {"result": {}})
        assert c.get("i1") is not None and c.get("i2") is None   # least recently used goes first

    def test_cache_key_is_script_window_qop(self):
        k = click.cache_key("s", 20000, "v3_7_3")
        assert k == click.cache_key("s", 20000, "v3_7_3")
        assert len({k, click.cache_key("t", 20000, "v3_7_3"), click.cache_key("s", 10000, "v3_7_3"),
                    click.cache_key("s", 20000, "v3_6_0")}) == 4


def test_prune_at_21_runs(rig):
    ids = []
    for i in range(21):
        rig.behave = child_writes(ok_result(script=f"# {i}\n"))
        rig.node.write_text(f"# node {i}\n", encoding="utf-8")   # no cache hit
        ids.append(rig.wait(rig.start()["id"])["id"])
    folders = sorted(p.name for p in (rig.tmp / "inst" / "nodesim" / "w1").iterdir())
    assert len(folders) == 20 and ids[0] not in folders and ids[-1] in folders


def test_files_written_only_under_tmp(rig, tmp_path):
    rig.wait(rig.start()["id"])
    work = rig.procs[0].work
    assert work.resolve().is_relative_to(tmp_path.resolve())
    assert rig.procs[0].kw["cwd"] == str(rig.copies[0].root.resolve())
    env = rig.procs[0].kw["env"]
    assert env["PYTHONPATH"] == str(rig.lib.resolve()) and env["PYTHONHASHSEED"] == "0"
    assert Path(env["QUALIBRATE_CONFIG_FILE"]).resolve().is_relative_to(tmp_path.resolve())
    assert not (ROOT / "instance" / "nodesim").exists()


def test_overlapping_live_and_scratch_refused(rig):
    a = rig.start(live_folder=rig.tmp)
    assert a["state"] == "failed" and "overlap" in a["line"] and rig.procs == []


def test_estimates():
    e = click.estimate("11_rabi.py", 20)
    assert e["family"] == "rabi" and abs(e["estimate_s"] - (sum(click.COLD_S.values()) + 2.75 + 0.215 * 20)) < 0.1
    u = click.estimate("09_t1.py", 20)
    assert u["estimate_s"] is None and u["line"].startswith("No measured time for this kind of node yet.")


def test_version_ok():
    assert click.version_ok("1.2.0") and click.version_ok("1.10") and not click.version_ok("1.1.9")
    assert not click.version_ok(None) and not click.version_ok("")


def test_check_result_rules():
    assert click.check_result(ok_result(), "") is None
    assert click.check_result({"ok": True}, "") == click.UNREADABLE
    assert click.check_result(ok_result(kind="ok", ok=False), "") == click.UNREADABLE


def test_save_script(rig, tmp_path):
    d = rig.wait(rig.start()["id"])
    out = tmp_path / "out"
    out.mkdir()
    s = rig.m.save_script(d["id"], str(out))
    assert s["ok"] and Path(s["path"]).name == "11_rabi_demo_simulated.py"
    assert "program()" in Path(s["path"]).read_text(encoding="utf-8")
    assert not rig.m.save_script(d["id"], str(out))["ok"]          # never overwrites
    assert not rig.m.save_script(d["id"], str(rig.lib), forbidden=(rig.lib,))["ok"]
    assert not rig.m.save_script(d["id"], str(rig.m.instance))["ok"]
    assert not rig.m.save_script("../x", str(out))["ok"]


@pytest.mark.parametrize("state,fields,expected", [
    ("warming", {"env": "envx"}, "Preparing the node simulator in env envx (about 5 s)."),
    ("ready", {}, "Node simulator ready."),
    ("starting", {"in_use_here": 1}, "Starting a cloud simulator instance (about 62 s); "
     "1 of 3 instances of this account in use on this PC."),
    ("spawn_failed", {"message": "reason\nsecond line"}, "The cloud simulator instance could not start: reason"),
    ("broker_failed", {"message": "reason\nsecond line"}, "The cloud instance manager could not start: reason"),
    ("needs_instance", {}, "This chip's FEM layout differs from the open instance; "
     "it needs a new instance (about 62 s)."),
    ("renewing", {}, "Starting a replacement cloud instance; "
     "clicks keep using the current one until it is ready."),
    ("expired_retry", {}, "The cloud instance expired during this click; "
     "simulating again on a fresh instance (about 62 s)."),
    ("lab_busy", {}, "The lab environment is busy with another check; try again."),
    ("build_watchdog", {}, "The node's build took over 30 s; "
     "the simulator process was stopped and the lab lock released."),
    ("worker_crashed", {"code": 7}, "The simulator process stopped unexpectedly (exit code 7); "
     "the next click starts a fresh one."),
    ("worker_restart", {"file": "source.py"}, "A lab file changed (source.py); "
     "the simulator process restarts (about 5 s). The cloud instance is kept."),
    ("busy_chip", {"name": "copy"}, "The simulator is busy with chip copy; this click waits."),
    ("stopped_spawning", {}, "Stopped. The cloud instance is still starting and is closed as soon as it is up."),
    ("stopped_kept", {}, "Stopped. The cloud instance stays open for the next click."),
    ("idle_closed", {}, "The cloud instance was closed after 10 min without a click."),
    ("cold_fallback", {}, "Using a one-off simulator process this time."),
])
def test_p2_exact_lines(state, fields, expected):
    """Pin every P2 state line independently of the renderer (P2 spec 1, review r2 P3-5)."""
    assert contract(click.answer(state, **fields))["line"] == expected


def test_p2_expiry_lines_use_local_server_time():
    """The expiry strip and offers use the server timestamp (D-17, P2 spec 1)."""
    stamp = "2026-01-01T12:34:00+00:00"
    from datetime import datetime
    hhmm = datetime.fromisoformat(stamp).astimezone().strftime("%H:%M")
    assert click.line("instance", expires_at=stamp, in_use_here=1) == (
        f"Cloud instance open until {hhmm}; it closes after 10 min without a click. 1 of 3 in use on this PC.")
    assert click.line("no_slot", earliest_close=stamp, in_use_here=3) == (
        f"All 3 cloud simulator instances of this account are in use (3 by this PC). "
        f"The earliest one here closes at {hhmm}.")
    assert click.line("renew_offer", expires_at=stamp) == f"The cloud instance closes at {hhmm}."
