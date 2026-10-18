"""v1.5.1 R2 M2 + MINOR 3: whose tray checkpoint is whose.

M2. A process's checkpoint was judged "running" by its pid alone. Windows
reuses pids: a crashed SM's checkpoint whose pid an unrelated process now
holds was neither restored nor listed, with no notice -- before SB-F3 it was
always restored. The holder record and the peer file names now carry the
OS's start time of the pid; a holder is running only while both match.

MINOR 3. An older SM (no per-process checkpoints) writes the plain
checkpoint without recording itself. While one runs on the instance, its
rows were listed as "staged in another SM window that has stopped" and the
new SM restored them into its own tray, then wrote over the file. A
checkpoint that could not be moved aside was listed again on every attempt.

Pinned with real processes beside the test process.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from quam_state_manager.core import instances, pending_tray, safe_io, working_copy
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
    if mode == "stage":
        path, value = arg.split("=")
        out["edit"] = c.post("/field/edit", data={"dot_path": path, "value": value}).status_code
        pending_tray.flush_all()
    out["tray"] = [e.dot_path for e in ctx["store"].change_log]
    note = ctx.get("_tray_recovery_notice")
    out["notice"] = note.get("text") if isinstance(note, dict) else note
    out["unrecovered"] = [{"rows": [r["path"] for r in d["rows"]], "reason": d["reason"]}
                          for d in pending_tray.unrecovered(ctx["working_copy"])]
    print("RESULT " + json.dumps(out), flush=True)
    os._exit(0)                         # a crash: no clean-exit flush, no deregister
''')


def _run(inst, live, mode, arg="-"):
    env = dict(os.environ, PYTHONUTF8="1")
    r = subprocess.run([sys.executable, "-c", _CHILD, str(inst), str(live), mode, arg],
                       cwd=str(ROOT), env=env, capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=240)
    line = next((ln for ln in r.stdout.splitlines() if ln.startswith("RESULT ")), None)
    assert line, r.stdout[-2000:] + r.stderr[-4000:]
    return json.loads(line[len("RESULT "):])


@pytest.fixture
def bystander():
    """An unrelated running process (it will 'reuse' a recorded pid)."""
    p = subprocess.Popen([sys.executable, "-c", "import sys; sys.stdin.readline()"],
                         stdin=subprocess.PIPE)
    yield p
    try:
        p.stdin.write(b"\n")
        p.stdin.flush()
    except OSError:
        pass
    p.wait(30)


@pytest.fixture
def chip(tmp_path):
    live = tmp_path / "chip" / "quam_state"
    live.mkdir(parents=True)
    qs = {f"q{i}": {"id": f"q{i}", "T1": 1.0e-5 * i} for i in range(1, 6)}
    (live / "state.json").write_text(json.dumps(
        {"qubits": qs, "qubit_pairs": {}, "active_qubit_names": sorted(qs)}), encoding="utf-8")
    (live / "wiring.json").write_text(json.dumps(_WIRING), encoding="utf-8")
    inst = tmp_path / "_inst"
    routes._quam_cache.clear()
    app = create_app(testing=True, instance_path=str(inst))
    c = app.test_client()
    assert c.post("/load", data={"folder": str(live)}).status_code in (200, 302)
    _clear_memo()
    return {"app": app, "c": c, "live": live, "inst": inst}


def _clear_memo():
    getattr(pending_tray, "_OLDER_SM_MEMO", {}).clear()


def _main(chip) -> Path:
    return pending_tray.sidecar_path(working_copy.load(str(chip["inst"]), chip["live"]))


def _rows(path: Path) -> list:
    return [e["dot_path"] for e in json.loads(path.read_text(encoding="utf-8"))["entries"]]


class TestAReusedPidIsNotTheHolder:
    def test_a_crashed_holders_checkpoint_comes_back_when_its_pid_was_reused(self, chip, bystander):
        a = _run(chip["inst"], chip["live"], "stage", "qubits.q5.T1=9.1e-06")
        assert a["tray"] == ["qubits.q5.T1"]
        main = _main(chip)
        owner_f = main.with_name(main.stem + ".owner.json")
        doc = json.loads(owner_f.read_text(encoding="utf-8"))
        assert doc["pid"] == a["pid"]
        doc["pid"] = bystander.pid                  # an unrelated process now holds that pid
        owner_f.write_text(json.dumps(doc), encoding="utf-8")
        b = _run(chip["inst"], chip["live"], "open")
        shown = b["tray"] + [x for d in b["unrecovered"] for x in d["rows"]]
        assert shown == ["qubits.q5.T1"], f"the crashed process's edit is hidden: {b}"

    def test_a_crashed_peers_file_comes_back_when_its_pid_was_reused(self, chip, bystander):
        c = chip["c"]
        # this (running) process holds the plain checkpoint ...
        assert c.post("/field/edit", data={"dot_path": "qubits.q4.T1",
                                           "value": "3.05e-05"}).status_code == 200
        pending_tray.flush_all()
        # ... so another process writes a peer file, then crashes
        a = _run(chip["inst"], chip["live"], "stage", "qubits.q5.T1=9.1e-06")
        main = _main(chip)
        peers = [f for f in main.parent.glob(main.stem + ".peer-*.json")]
        assert len(peers) == 1 and peers[0].name.startswith(f"{main.stem}.peer-{a['pid']}-"), peers
        reused = peers[0].with_name(peers[0].name.replace(f".peer-{a['pid']}-",
                                                          f".peer-{bystander.pid}-", 1))
        os.replace(peers[0], reused)
        b = _run(chip["inst"], chip["live"], "open")
        shown = b["tray"] + [x for d in b["unrecovered"] for x in d["rows"]]
        assert shown == ["qubits.q5.T1"], f"the crashed process's edit is hidden: {b}"
        assert _rows(main) == ["qubits.q4.T1"], "the running process's checkpoint was touched"

    def test_the_identity_names_one_process(self):
        me = os.getpid()
        start = instances.process_start(me)
        if start is None:
            pytest.skip("the OS does not tell a process's start time here")
        assert instances.process_alive(me, start)
        assert not instances.process_alive(me, start + 1)
        assert pending_tray._SELF.get("start") == start


class TestAnOlderSmStillRunning:
    def _older_sm(self, chip, bystander):
        """The plain checkpoint as an older SM writes it (no holder recorded),
        and that SM's registry entry (no tray protocol), still running."""
        a = _run(chip["inst"], chip["live"], "stage", "qubits.q2.T1=2.2e-05")
        main = _main(chip)
        main.with_name(main.stem + ".owner.json").unlink()
        reg = chip["inst"] / "instances"
        reg.mkdir(exist_ok=True)
        (reg / f"{a['pid']}.json").unlink(missing_ok=True)
        safe_io.atomic_write_json(reg / f"{bystander.pid}.json", {
            "pid": bystander.pid, "port": 5999, "started_utc": "", "updated_utc": "",
            "chip_path": str(chip["live"]), "chip_fs_key": "", "chip_name": "",
            "project": "", "roots": []})
        _clear_memo()
        return main

    def test_its_rows_are_not_called_stopped_nor_taken(self, chip, bystander):
        main = self._older_sm(chip, bystander)
        b = _run(chip["inst"], chip["live"], "stage", "qubits.q3.T1=3.3e-05")
        assert b["tray"] == ["qubits.q3.T1"], f"the older SM's rows were taken: {b}"
        assert b["unrecovered"] == [], f"a running SM's rows listed as stopped: {b}"
        assert not (b["notice"] or "") or "stopped" not in b["notice"], b
        assert main.exists() and _rows(main) == ["qubits.q2.T1"], \
            "the older SM's checkpoint was written over"
        peers = list(main.parent.glob(main.stem + ".peer-*.json"))
        assert [_rows(p) for p in peers] == [["qubits.q3.T1"]], peers

    def test_once_it_stopped_its_rows_come_back(self, chip, bystander):
        self._older_sm(chip, bystander)
        bystander.stdin.write(b"\n")
        bystander.stdin.flush()
        bystander.wait(30)
        _clear_memo()
        b = _run(chip["inst"], chip["live"], "open")
        assert b["tray"] == ["qubits.q2.T1"], b


class TestASetAsideThatCannotMove:
    def test_is_never_listed_twice(self, chip, monkeypatch):
        main = _main(chip)
        wc = working_copy.load(str(chip["inst"]), chip["live"])
        src = main.with_name(main.stem + ".peer-999999-0-abcdef012345.json")
        safe_io.atomic_write_json(src, {
            "version": 2, "live_folder": str(chip["live"]), "base_hash": None, "applied": 0,
            "synced_live_hash": wc.synced_live_hash, "mutation_seq": 0, "flags": {},
            "entries": [{"dot_path": "qubits.q1.T1", "old_value": 1e-5, "new_value": 1.5e-5,
                         "source_file": "state", "created": False, "deleted": False,
                         "group_id": None, "actor": "human"}]})
        real = type(src).unlink

        def refuse(self, *a, **k):
            if self == src:
                raise PermissionError(13, "in use", str(self))
            return real(self, *a, **k)
        monkeypatch.setattr(type(src), "unlink", refuse)
        for _ in range(2):
            got = pending_tray._set_aside_stopped(wc, src, {"pid": 999999, "nonce": "x"})
            assert not got, got
        monkeypatch.setattr(type(src), "unlink", real)
        listed = pending_tray.unrecovered(wc)
        assert listed == [], f"listed while still in place: {listed}"
        assert src.exists() and _rows(src) == ["qubits.q1.T1"]
