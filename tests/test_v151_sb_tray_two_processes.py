"""v1.5.1 SB-F3: two SM processes on one instance, one chip.

Both wrote ``<key>.pending_tray.json``; the last writer won. One process
staged q5, the other q4 -- the file held only q4. The first process, killed
and restarted, showed "1 unapplied edit: qubits.q4.T1" (the OTHER process's
edit) and its own q5 was gone with no notice.

Now one process holds the plain checkpoint (recorded beside it) and every
other writes its own peer file, decided under a cross-process lock. A process
that opens the chip restores only a STOPPED process's checkpoint, never a
running one's; when several stopped processes left rows, one is restored and
the others are listed in the sync panel (Restore / Discard).

Pinned with real processes: the test process is one SM, and spawned Python
processes are the others (a pid is what tells processes apart).
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from quam_state_manager.core import pending_tray, working_copy
from quam_state_manager.web import routes
from quam_state_manager.web.app import create_app

ROOT = Path(__file__).resolve().parent.parent
_WIRING = {"network": {"host": "127.0.0.1", "cluster_name": "C1"}}

_CHILD = textwrap.dedent(r'''
    import json, os, sys
    inst, live, mode, arg = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4]
    from quam_state_manager.web.app import create_app
    from quam_state_manager.core import pending_tray
    app = create_app(testing=True, instance_path=inst)
    c = app.test_client()
    assert c.post("/load", data={"folder": live}).status_code in (200, 302)
    ctx = app.config["contexts"][app.config["active_context"]]
    out = {"pid": os.getpid()}
    if mode in ("stage", "stage_wait"):
        path, value = arg.split("=")
        r = c.post("/field/edit", data={"dot_path": path, "value": value})
        out["edit"] = r.status_code
        pending_tray.flush_all()
    out["tray"] = [e.dot_path for e in ctx["store"].change_log]
    note = ctx.get("_tray_recovery_notice")
    out["notice"] = note.get("text") if isinstance(note, dict) else note
    out["unrecovered"] = [{"rows": [r["path"] for r in d["rows"]], "reason": d["reason"]}
                          for d in pending_tray.unrecovered(ctx["working_copy"])]
    print("RESULT " + json.dumps(out), flush=True)
    if mode == "stage_wait":
        sys.stdin.readline()            # stay running until told
    os._exit(0)                         # a kill: no clean-exit flush, no deregister
''')


def _env():
    e = dict(os.environ)
    e["PYTHONUTF8"] = "1"
    return e


def _run(inst, live, mode, arg="-"):
    r = subprocess.run([sys.executable, "-c", _CHILD, str(inst), str(live), mode, arg],
                       cwd=str(ROOT), env=_env(), capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=240)
    line = next((ln for ln in r.stdout.splitlines() if ln.startswith("RESULT ")), None)
    assert line, r.stdout[-2000:] + r.stderr[-4000:]
    return json.loads(line[len("RESULT "):])


def _start(inst, live, mode, arg="-"):
    """A process that stages and then keeps running (until _stop)."""
    p = subprocess.Popen([sys.executable, "-c", _CHILD, str(inst), str(live), mode, arg],
                         cwd=str(ROOT), env=_env(), stdin=subprocess.PIPE,
                         stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                         encoding="utf-8", errors="replace")
    while True:
        line = p.stdout.readline()
        if not line:
            raise AssertionError("child ended early: " + p.stderr.read()[-4000:])
        if line.startswith("RESULT "):
            return p, json.loads(line[len("RESULT "):])


def _stop(p):
    try:
        p.stdin.write("\n")
        p.stdin.flush()
    except OSError:
        pass
    p.wait(60)


@pytest.fixture
def chip(tmp_path):
    live = tmp_path / "chip" / "quam_state"
    live.mkdir(parents=True)
    qs = {f"q{i}": {"id": f"q{i}", "T1": 1.0e-5 * i} for i in range(1, 6)}
    (live / "state.json").write_text(json.dumps(
        {"qubits": qs, "qubit_pairs": {}, "active_qubit_names": sorted(qs)}), encoding="utf-8")
    (live / "wiring.json").write_text(json.dumps(_WIRING), encoding="utf-8")
    inst = tmp_path / "_inst"
    # the chip's working copy exists before any process opens it
    routes._quam_cache.clear()
    app = create_app(testing=True, instance_path=str(inst))
    c = app.test_client()
    assert c.post("/load", data={"folder": str(live)}).status_code in (200, 302)
    return {"app": app, "c": c, "live": live, "inst": inst}


def _wc(chip):
    return working_copy.load(str(chip["inst"]), chip["live"])


def _rows_on_disk(chip) -> dict:
    """{checkpoint file name: [row paths]} of every checkpoint of the chip."""
    main = pending_tray.sidecar_path(_wc(chip))
    out = {}
    for f in main.parent.glob(main.stem + "*.json"):
        if f.name.endswith(".owner.json"):
            continue
        try:
            doc = json.loads(f.read_text(encoding="utf-8"))
        except ValueError:
            continue
        out[f.name] = [e["dot_path"] for e in doc.get("entries") or []]
    return out


class TestTwoProcessesKeepTheirOwnRows:
    def test_neither_overwrites_the_other_and_a_restart_gets_its_own(self, chip):
        c = chip["c"]
        # this process (running throughout) stages q4
        assert c.post("/field/edit", data={"dot_path": "qubits.q4.T1",
                                           "value": "3.05e-05"}).status_code == 200
        pending_tray.flush_all()
        # another process stages q5, then is killed
        other = _run(chip["inst"], chip["live"], "stage", "qubits.q5.T1=9.1e-06")
        assert other["edit"] == 200 and other["tray"] == ["qubits.q5.T1"]
        disk = _rows_on_disk(chip)
        flat = [p for rows in disk.values() for p in rows]
        assert "qubits.q4.T1" in flat and "qubits.q5.T1" in flat, \
            f"one process's staged edit was overwritten by the other's: {disk}"
        # restarted, it gets ITS edit back -- never this process's
        again = _run(chip["inst"], chip["live"], "open")
        assert again["tray"] == ["qubits.q5.T1"], again
        assert "qubits.q4.T1" not in json.dumps(again)
        # and this (running) process's checkpoint still holds q4
        disk = _rows_on_disk(chip)
        main = pending_tray.sidecar_path(_wc(chip)).name
        assert disk.get(main) == ["qubits.q4.T1"], disk

    def test_an_emptied_tray_never_deletes_the_other_processes_checkpoint(self, chip):
        c = chip["c"]
        assert c.post("/field/edit", data={"dot_path": "qubits.q4.T1",
                                           "value": "3.05e-05"}).status_code == 200
        pending_tray.flush_all()
        p, _ = _start(chip["inst"], chip["live"], "stage_wait", "qubits.q5.T1=9.1e-06")
        try:
            # this process discards its edit: only ITS checkpoint goes
            assert c.post("/discard_all").status_code == 200
            pending_tray.flush_all()
            flat = [x for rows in _rows_on_disk(chip).values() for x in rows]
            assert flat == ["qubits.q5.T1"], _rows_on_disk(chip)
        finally:
            _stop(p)

    def test_two_stopped_processes_one_restored_one_listed(self, chip):
        # two other processes stage while both run, then both stop
        p1, r1 = _start(chip["inst"], chip["live"], "stage_wait", "qubits.q2.T1=2.2e-05")
        try:
            r2 = _run(chip["inst"], chip["live"], "stage", "qubits.q3.T1=3.3e-05")
        finally:
            _stop(p1)
        assert r1["tray"] == ["qubits.q2.T1"] and r2["tray"] == ["qubits.q3.T1"]
        opened = _run(chip["inst"], chip["live"], "open")
        listed = [x for d in opened["unrecovered"] for x in d["rows"]]
        got = sorted(opened["tray"] + listed)
        assert got == ["qubits.q2.T1", "qubits.q3.T1"], opened
        assert len(opened["tray"]) == 1 and len(listed) == 1, opened
        assert any("stopped" in d["reason"] for d in opened["unrecovered"]), opened
        assert opened["notice"] and "listed in the sync panel" in opened["notice"], opened

    def test_a_running_processes_rows_are_never_restored_elsewhere(self, chip):
        p1, _ = _start(chip["inst"], chip["live"], "stage_wait", "qubits.q2.T1=2.2e-05")
        try:
            opened = _run(chip["inst"], chip["live"], "open")
            assert opened["tray"] == [] and opened["unrecovered"] == [], opened
        finally:
            _stop(p1)
