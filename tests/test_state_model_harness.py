"""Model-based state-update harness (VPLAN A.4-A.6), a plain seeded loop.

Every sequence opens a chip through the real app on real files, draws ops
from ``tests/state_model/ops.propose`` (weights per A.6), runs each through
the routes, moves the oracle (``tests/state_model/oracle.Model``) and checks
I1-I12 after every step (``tests/state_model/invariants``). A red names the
seed, the step and the op list that replays it.

Knobs (environment):
  SM_STATE_MODEL_SEEDS   sequences per chip kind (default 10 synthetic, 3 lab-shaped)
  SM_STATE_MODEL_STEPS   steps per sequence (default 25)
  SM_STATE_MODEL_SEED0   first seed (default 0)
  SM_STATE_MODEL_REPORT  a directory: one JSON record per sequence is written there
  SM_STATE_MODEL_KIND    synthetic | lab | both (default both)

The lab-shaped chip is a pruned copy of a real lab chip (two qubits and one
pair with their pulse mirrors, one MW port, one waveform pulse, one readout
pulse of a lab class), built in ``tmp_path``; it skips when that chip is absent.
"""
from __future__ import annotations

import copy
import json
import os
import random
import time
from pathlib import Path

import pytest

from tests import _ram_chip
from tests.state_model import invariants as INV
from tests.state_model import oracle as O
from tests.state_model.ops import Rig, propose

#: Set to 1 to fail on findings already recorded (FINDINGS_WP1.md) too.
STRICT = os.environ.get("SM_STATE_MODEL_STRICT") == "1"
#: Recorded findings that only touch what the status control SAYS: a sequence
#: that meets one notes it and goes on (the model and SM agree on everything else).
CONTINUE_PAST = ("K:SU-01", "K:SU-02", "K:SU-03", "K:SU-04", "K:SU-05", "K:SU-08", "K:SU-09",
                 "K:SU-14")
SEEDS = int(os.environ.get("SM_STATE_MODEL_SEEDS", "0") or 0)
STEPS = int(os.environ.get("SM_STATE_MODEL_STEPS", "25") or 25)
SEED0 = int(os.environ.get("SM_STATE_MODEL_SEED0", "0") or 0)
REPORT = os.environ.get("SM_STATE_MODEL_REPORT") or ""
KIND = os.environ.get("SM_STATE_MODEL_KIND", "both")


@pytest.fixture(autouse=True)
def _clocks(monkeypatch):
    """The harness's controlled clock: the 30 s live-hash recheck and the
    2.5 s unreadable debounce are throttles, not contracts; with them at 0 a
    poll answers from what is on disk now (A.5: the weaker forms)."""
    from quam_state_manager.core import history
    from quam_state_manager.web import routes
    monkeypatch.setattr(routes, "_LIVE_HASH_RECHECK_S", 0.0)
    monkeypatch.setattr(routes, "_LIVE_UNREADABLE_AFTER_S", 0.0)
    # the chip never changes identity here; a frozen history-dir memo keeps a
    # torn read from re-resolving it (SU-06 has its own repro)
    monkeypatch.setattr(history, "_HIST_SEQ_RESOLVE_TTL_S", 1e9)


# ----------------------------------------------------------------------
# chips
# ----------------------------------------------------------------------

def synthetic_chip(seed: int) -> tuple[dict, dict]:
    state, wiring = _ram_chip.build(n_qubits=3, seed=seed)
    return state, wiring


def _lab_root() -> Path | None:
    try:
        from tests.archive_roots import lab_path
        p = lab_path("lab-G")
    except Exception:  # noqa: BLE001
        return None
    return p if (p / "state.json").exists() and (p / "wiring.json").exists() else None


def lab_shaped_chip() -> tuple[dict, dict] | None:
    """Two qubits + the pair between them that carries a two-flux CZ macro
    whose pulses each qubit mirrors by pointer; everything naming a dropped
    entity is pruned. Name-free: the pair is found by shape."""
    root = _lab_root()
    if root is None:
        return None
    state = json.loads((root / "state.json").read_text(encoding="utf-8"))
    wiring = json.loads((root / "wiring.json").read_text(encoding="utf-8"))
    pairs = state.get("qubit_pairs") or {}
    pick = None
    for pid, pair in pairs.items():
        macros = pair.get("macros") or {}
        for mname, mac in macros.items():
            if not isinstance(mac, dict):
                continue
            fq = mac.get("flux_pulse_qubit")
            if isinstance(fq, dict) and isinstance(mac.get("flux_pulse_target"), dict):
                c = str(pair.get("qubit_control", "")).rsplit("/", 1)[-1]
                t = str(pair.get("qubit_target", "")).rsplit("/", 1)[-1]
                if c in state["qubits"] and t in state["qubits"]:
                    pick = (pid, mname, c, t)
                    break
        if pick:
            break
    if pick is None:
        return None
    pid, _mname, c, t = pick
    keep_q, keep_p = {c, t}, {pid}
    drop_q = set(state["qubits"]) - keep_q
    drop_p = set(pairs) - keep_p

    def dead(v) -> bool:
        if not isinstance(v, str) or not v.startswith("#/"):
            return False
        segs = v[2:].split("/")
        if len(segs) >= 2 and segs[0] == "qubits" and segs[1] in drop_q:
            return True
        if len(segs) >= 2 and segs[0] == "qubit_pairs" and segs[1] in drop_p:
            return True
        if len(segs) >= 3 and segs[0] == "wiring" and segs[1] == "qubits" and segs[2] in drop_q:
            return True
        return False

    def prune(obj):
        if isinstance(obj, dict):
            out = {}
            for k, v in obj.items():
                # a dead pointer, or a dict holding one (a mirror of a dropped pair)
                if dead(v) or (isinstance(v, dict) and any(dead(x) for x in v.values())):
                    continue
                out[k] = prune(v)
            return out
        if isinstance(obj, list):
            return [prune(x) for x in obj if not dead(x)]
        return obj

    state["qubits"] = {q: state["qubits"][q] for q in sorted(keep_q)}
    state["qubit_pairs"] = {pid: pairs[pid]}
    state["active_qubit_names"] = sorted(keep_q)
    state["active_qubit_pair_names"] = [pid]
    for key in ("octaves", "mixers", "twpas", "active_twpa_names"):
        state.pop(key, None)
    for key in ("flux_crosstalk_dc", "flux_crosstalk_max_v"):
        if isinstance(state.get(key), dict):
            state[key] = {k: ({kk: vv for kk, vv in v.items() if kk in keep_q} if isinstance(v, dict) else v)
                          for k, v in state[key].items() if k in keep_q}
    # one waveform pulse and one lab readout pulse are kept; spectator maps
    # and park pulses for dropped qubits go with them
    state = prune(state)
    if isinstance(state.get("extras"), dict):
        state["extras"].pop("data_folder", None)
    wq = (wiring.get("wiring") or {}).get("qubits") or {}
    wiring["wiring"]["qubits"] = {q: v for q, v in wq.items() if q in keep_q}
    (wiring.get("wiring") or {}).pop("twpas", None)
    return state, wiring


# ----------------------------------------------------------------------
# the loop
# ----------------------------------------------------------------------

def run_sequence(tmp: Path, state: dict, wiring: dict, seed: int, steps: int, *,
                 label: str, ops: list | None = None, fault=None) -> dict:
    """Run one sequence; returns its record (first red, op list, timings)."""
    rig = Rig(tmp, copy.deepcopy(state), copy.deepcopy(wiring), label=label)
    rng = random.Random(seed)
    log: list = []
    red = None
    t0 = time.perf_counter()
    plan = [o for o in ops if o[0] != "AUTOPULL"] if ops is not None else None
    n = len(plan) if plan is not None else steps
    known: list = []
    for i in range(n):
        name, args = plan[i][:2] if plan is not None else propose(rig, rng)
        pre = INV.pre_facts(rig)
        if fault is not None:
            fault.before(rig, i, name)
        step = rig.run(name, copy.deepcopy(args))
        if fault is not None:
            fault.after(rig, i, name, step)
        log.append([name, args, step.obs, getattr(step.exp, "kind", None)]
                   + ([step.observations] if step.observations else []))
        bad = INV.check_step(rig, step, pre)
        if not bad and rig.model.pull_armed:
            pre2 = INV.pre_facts(rig)
            auto = rig.poll_autopull()
            if auto is not None:
                log.append(["AUTOPULL", {}, auto.obs, getattr(auto.exp, "kind", None)])
                bad = INV.check_step(rig, auto, pre2)
        new = [v for v in bad if not str(v[0]).startswith("K:")]
        kn = [v for v in bad if str(v[0]).startswith("K:")]
        if kn:
            known.append({"step": i, "op": name, "violations": kn})
        if kn and not new and not STRICT and all(v[0] in CONTINUE_PAST for v in kn):
            continue
        if bad:
            red = {"step": i, "op": name, "args": args, "violations": bad,
                   "obs": step.obs, "exp": getattr(step.exp, "kind", None),
                   "known_only": bool(kn) and not new}
            break
    if red is None:
        bad = INV.final_sweep(rig)
        if bad:
            red = {"step": "final", "violations": bad}
    if red is not None:
        try:
            units, pcur = rig.journal()
            red["sm_journal"] = [[e["path"] for e in u.get("entries") or []][:6] + [u.get("meta")]
                                 for u in units] + [pcur]
            red["model_journal"] = [[e.path for e in u.entries][:6] + [u.kind]
                                    for u in rig.model.J] + [rig.model.pcursor]
        except Exception:  # noqa: BLE001
            pass
    rec = {"label": label, "seed": seed, "steps": len(log), "ops": log, "red": red,
           "known": known, "seconds": round(time.perf_counter() - t0, 2)}
    return rec


def _write_report(rec: dict):
    if not REPORT:
        return
    d = Path(REPORT)
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{rec['label']}_{rec['seed']}.json").write_text(json.dumps(rec, default=repr, indent=1),
                                                          encoding="utf-8")


def _fmt(rec: dict) -> str:
    red = rec["red"]
    lines = [f"{rec['label']} seed {rec['seed']} red at step {red['step']} "
             f"({red.get('op')} {red.get('args')}) obs={red.get('obs')} model={red.get('exp')}"]
    lines += [f"  [{inv}] {msg}" for inv, msg in red["violations"][:8]]
    lines.append("  ops: " + json.dumps(rec["ops"][-12:], default=repr)[:1500])
    return "\n".join(lines)


_N_SYN = SEEDS or 10
_N_LAB = SEEDS or 3


@pytest.mark.skipif(KIND not in ("both", "synthetic"), reason="SM_STATE_MODEL_KIND")
@pytest.mark.parametrize("seed", range(SEED0, SEED0 + _N_SYN))
def test_state_model_synthetic(tmp_path, seed):
    state, wiring = synthetic_chip(seed)
    rec = run_sequence(tmp_path, state, wiring, seed, STEPS, label="synthetic")
    _write_report(rec)
    _assert_green(rec)


def _assert_green(rec: dict):
    red = rec["red"]
    if red is not None and red.get("known_only") and not STRICT:
        pytest.xfail("recorded finding: " + ", ".join(sorted({v[0] for v in red["violations"]})))
    assert red is None, _fmt(rec)


@pytest.mark.skipif(KIND not in ("both", "lab"), reason="SM_STATE_MODEL_KIND")
@pytest.mark.parametrize("seed", range(SEED0, SEED0 + _N_LAB))
def test_state_model_lab_shaped(tmp_path, seed):
    chip = lab_shaped_chip()
    if chip is None:
        pytest.skip("the lab chip copy is absent")
    rec = run_sequence(tmp_path, chip[0], chip[1], seed, STEPS, label="lab")
    _write_report(rec)
    _assert_green(rec)


# ----------------------------------------------------------------------
# planted faults: the harness is not vacuous
# ----------------------------------------------------------------------

FAULTS_OUT = os.environ.get("SM_STATE_MODEL_FAULTS_OUT") or ""


def _fault_run(tmp_path, fault=None) -> dict:
    from tests.state_model import faults as F
    state, wiring = synthetic_chip(F.CHIP_SEED)
    return run_sequence(tmp_path, state, wiring, F.CHIP_SEED, 0, label="fault",
                        ops=F.script(), fault=fault)


def test_the_fault_script_is_green_without_a_fault(tmp_path):
    rec = _fault_run(tmp_path)
    assert rec["red"] is None, _fmt(rec)


def _fault_ids():
    from tests.state_model import faults as F
    return [f.name for f in F.ALL]


@pytest.mark.parametrize("name", _fault_ids())
def test_a_planted_fault_turns_the_harness_red(tmp_path, monkeypatch, name):
    from tests.state_model import faults as F
    cls = next(f for f in F.ALL if f.name == name)
    fault = cls(monkeypatch)
    rec = _fault_run(tmp_path, fault)
    red = rec["red"] or {}
    tags = [v[0] for v in red.get("violations", [])]
    if FAULTS_OUT:
        out = Path(FAULTS_OUT)
        doc = json.loads(out.read_text(encoding="utf-8")) if out.exists() else {}
        doc[name] = {"invariant": cls.invariant, "how": cls.how, "fired": fault.fired,
                     "red_at_step": red.get("step"), "red_op": red.get("op"), "tags": tags,
                     "caught": cls.invariant in tags,
                     "first_message": next((v[1] for v in red.get("violations", [])
                                            if v[0] == cls.invariant), None)}
        out.write_text(json.dumps(doc, indent=1, default=repr), encoding="utf-8")
    assert fault.fired, "the fault never fired"
    assert cls.invariant in tags, f"{name} was not caught as {cls.invariant}: {red}"


# ----------------------------------------------------------------------
# I10: two processes, one instance dir, one chip
# ----------------------------------------------------------------------

def test_two_processes_pressing_apply_together(tmp_path):
    from tests.state_model import two_process
    bad = two_process.run(tmp_path, rounds=int(os.environ.get("SM_STATE_MODEL_2P_ROUNDS", "4")))
    if bad and not STRICT and all(b.startswith(KNOWN_2P) for b in bad):
        pytest.xfail("recorded finding: " + bad[0])
    assert not bad, bad


#: SU-10 (FINDINGS_WP1.md): two processes on one instance dir share the working
#: copy on disk; a press can answer "Applied" with nothing written (I7) or a
#: refused press can move its edit out of the tray (I4)
KNOWN_2P: tuple = ("I7 round", "I4 round")


def test_a_planted_two_process_fault_turns_red(tmp_path):
    """The child presses Keep mine (force) without naming the live hash: its
    write can no longer be refused, so both land -- the check must say so."""
    from tests.state_model import two_process
    bad = two_process.run(tmp_path, rounds=3, fault="child_forces")
    assert any(b.startswith("I10") for b in bad), bad
