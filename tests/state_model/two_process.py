"""I10: two SM processes on one instance dir and one chip press Apply together.

The parent runs one app in this process; a child process runs a second app on
the SAME instance dir (so the two share the working copy on disk, the undo
journal and the chip's event journal). Each round both stage a different
field, meet at a file barrier and press Apply at once. After the round:

* at most one landed write per live hash transition (no two landed events
  share a base hash);
* a press that did not land answered a refusal, and its own tray still holds
  its edit (I4 in the loser, docs/255);
* no silent loss (I7): each process's value is on the live chip, or still in
  that process's tray.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

CHILD = r'''
import json, os, sys, time
from pathlib import Path
sys.path.insert(0, {root!r})
os.environ["SM_DISABLE_ENV_WARMUP"] = "1"
os.environ["SM_DISABLE_HISTORY_VERIFY"] = "1"
os.environ["QUALIBRATE_CONFIG_FILE"] = {nocfg!r}
from quam_state_manager.core import hub
hub.set_inline(True)
from quam_state_manager.web.app import create_app
work = Path({work!r})
app = create_app(testing=True, instance_path={inst!r})
c = app.test_client()
assert c.post("/load", data={{"folder": {live!r}}}).status_code in (200, 302)
ctx = next(v for v in app.config["contexts"].values() if v.get("type") == "quam")
(work / "child_up").write_text("1")
for r in range({rounds}):
    while not (work / f"start_{{r}}").exists():
        time.sleep(0.002)
    c.post("/state/sync", data={{"mode": "discard", "force": "1"}})
    v = 1.0e-5 + r * 1.0e-7 + 3e-9
    e = c.post("/field/edit", data={{"dot_path": {path!r}, "value": repr(v)}})
    (work / f"ready_c_{{r}}").write_text("1")
    while not (work / f"go_{{r}}").exists():
        time.sleep(0.0005)
    a = c.post("/state/apply-to-live", headers={{"Accept": "application/json"}})
    log = [(x.dot_path, x.new_value) for x in ctx["store"].change_log]
    (work / f"res_c_{{r}}.json").write_text(json.dumps({{
        "edit": e.status_code, "status": a.status_code, "body": (a.get_json(silent=True) or {{}}),
        "text": a.get_data(as_text=True)[-600:],
        "landed": a.status_code == 200 and "Applied to the live chip" in a.get_data(as_text=True),
        "dirty": bool(ctx.get("working_dirty")), "stash": sorted((ctx.get("pending_reapply") or {{}}).keys()),
        "value": v, "log": log}}))
'''


def run(tmp: Path, rounds: int = 5, *, parent_path: str = "qubits.q1.T1",
        child_path: str = "qubits.q2.T1", fault=None) -> list[str]:
    """Run the rounds; return the violations (empty when I10/I4/I7 hold)."""
    from quam_state_manager.core import hub, working_copy
    from quam_state_manager.web import routes
    from quam_state_manager.web.app import create_app
    from tests import _ram_chip

    live = tmp / "chips" / "live"
    live.mkdir(parents=True)
    state, wiring = _ram_chip.build(3, 1)
    (live / "state.json").write_text(json.dumps(state), encoding="utf-8")
    (live / "wiring.json").write_text(json.dumps(wiring), encoding="utf-8")
    inst = tmp / "_inst"
    work = tmp / "work"
    work.mkdir()
    hub.set_inline(True)
    routes._quam_cache.clear()
    app = create_app(testing=True, instance_path=str(inst))
    c = app.test_client()
    assert c.post("/load", data={"folder": str(live)}).status_code in (200, 302)
    ctx = next(v for v in app.config["contexts"].values() if v.get("type") == "quam")
    body = CHILD.format(root=str(ROOT), nocfg=str(tmp / "_nocfg"), work=str(work),
                        inst=str(inst), live=str(live), rounds=rounds, path=child_path)
    if fault == "child_forces":
        body = body.replace('headers={"Accept": "application/json"}',
                            'data={"force": "1"}, headers={"Accept": "application/json"}')
        # v1.3.0: force alone bypassed safety -> also disable the shared-copy gate to plant the fault.
        body = body.replace('c = app.test_client()',
                            'from quam_state_manager.web import routes\n'
                            'routes._shared_working_changed = lambda ctx: False\n'
                            'c = app.test_client()')
    shared_gate = routes._shared_working_changed
    if fault == "child_forces":
        routes._shared_working_changed = lambda ctx: False
    env = dict(os.environ, PYTHONUTF8="1")
    proc = subprocess.Popen([sys.executable, "-c", body], cwd=str(ROOT), env=env,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    out: list[str] = []
    try:
        t0 = time.time()
        while not (work / "child_up").exists():
            if proc.poll() is not None or time.time() - t0 > 120:
                raise AssertionError("child did not start: " + proc.stderr.read().decode()[-2000:])
            time.sleep(0.05)
        with app.app_context():
            chip = routes._hub_chip_dir(ctx["path"])
        for r in range(rounds):
            (work / f"start_{r}").write_text("1")
            c.post("/state/sync", data={"mode": "discard", "force": "1"})
            v = 2.0e-5 + r * 1.0e-7 + 7e-9
            c.post("/field/edit", data={"dot_path": parent_path, "value": repr(v)})
            while not (work / f"ready_c_{r}").exists():
                if proc.poll() is not None:
                    raise AssertionError("child died: " + proc.stderr.read().decode()[-2000:])
                time.sleep(0.0005)
            n0 = len(hub.Hub.for_chip(chip).read())
            (work / f"go_{r}").write_text("1")
            if fault == "child_forces":
                # the planted fault: both presses bypass the staleness gate
                # (Keep mine without the live hash), one after the other
                while not (work / f"res_c_{r}.json").exists():
                    time.sleep(0.002)
                a = c.post("/state/apply-to-live", data={"force": "1"},
                           headers={"Accept": "application/json"})
            else:
                a = c.post("/state/apply-to-live", headers={"Accept": "application/json"})
            pb = a.get_json(silent=True) or {}
            p_landed = a.status_code == 200 and "Applied to the live chip" in a.get_data(as_text=True)
            p_log = [(x.dot_path, x.new_value) for x in ctx["store"].change_log]
            t1 = time.time()
            while not (work / f"res_c_{r}.json").exists():
                if proc.poll() is not None or time.time() - t1 > 60:
                    raise AssertionError("child gave no result: " + proc.stderr.read().decode()[-2000:])
                time.sleep(0.002)
            time.sleep(0.05)
            ch = json.loads((work / f"res_c_{r}.json").read_text())
            lines = [o for _, _, o in hub.Hub.for_chip(chip).read()][n0:]
            landed = {o["landed"] for o in lines if "landed" in o}
            writes = [o for o in lines if "id" in o and o["id"] in landed]
            bases = [w.get("base_hash") for w in writes]
            if len(bases) != len(set(bases)):
                out.append(f"I10 round {r}: two landed writes from the same live hash {bases}")
            doc = json.loads((live / "state.json").read_text(encoding="utf-8"))
            for who, claimed, pid, path, val, log, st in (
                    ("parent", p_landed, os.getpid(), parent_path, v, p_log, a.status_code),
                    ("child", ch["landed"], proc.pid, child_path, ch["value"],
                     [tuple(x) for x in ch["log"]], ch["status"])):
                wrote = any(w.get("pid") == pid for w in writes)
                on_live = _get(doc, path) == val
                in_tray = any(p == path and nv == val for p, nv in log)
                if claimed and not wrote and not on_live and not in_tray:
                    out.append(f"I7 round {r}: the {who}'s press answered 'Applied to the live chip' "
                               f"but recorded no write, and its edit {path}={val!r} is on neither "
                               f"the chip nor its tray")
                elif wrote and not on_live:
                    out.append(f"I7 round {r}: the {who}'s write landed but live now holds "
                               f"{_get(doc, path)!r} at {path}, not its {val!r}")
                elif not claimed and not in_tray and not on_live:
                    out.append(f"I4 round {r}: the {who}'s press did not land (HTTP {st}) and its "
                               f"edit {path}={val!r} is on neither the chip nor its tray")
            if len({w.get("pid") for w in writes}) > 1:
                # both presses started from one sync point and neither pulled:
                # the second can only land by not seeing the first
                out.append(f"I10 round {r}: both processes' presses landed "
                           f"({[w.get('src') for w in writes]})")
    finally:
        routes._shared_working_changed = shared_gate
        try:
            proc.wait(timeout=60)
        except subprocess.TimeoutExpired:
            proc.kill()
    return out


def _get(doc, path):
    cur = doc
    for s in path.split("."):
        if not isinstance(cur, dict) or s not in cur:
            return None
        cur = cur[s]
    return cur
