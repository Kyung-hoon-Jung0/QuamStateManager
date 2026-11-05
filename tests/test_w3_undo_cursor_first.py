"""F-5 (user decision 2026-11-05): a live undo/redo persists the undo-journal
cursor BEFORE it writes the chip.

Two SM processes share one instance folder: the working copy and the undo
journal sidecar. With the cursor written AFTER the chip, a second process
whose pull tick ran in that window read a sidecar that still called the
undone unit applied, judged the undone value a lost apply (W3-5's chain) and
wrote the pre-undo value back -- the user's Ctrl+Z reverted by SM itself.
Pinned with a real second process reading the sidecar and pulling in exactly
that window. A refused write puts the cursor back and takes the stamp off.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import textwrap
import threading
from pathlib import Path

import quam_state_manager.web.routes as routes_mod
from quam_state_manager.core import undo_journal
from tests.test_autosync_lost_applied import _apply_name
from tests.test_autosync_merge import _state, env  # noqa: F401
from tests.test_w3_undo_lost import _live_t1, _sidecar, _undo

ROOT = Path(__file__).resolve().parent.parent

# The second SM process: opens the same chip from the same instance folder,
# arms pull, then waits for commands on stdin. PULL = what its pull tick does
# -- judge the applied chain from the shared sidecar, notice the live change
# and press /auto-sync/pull -- reporting what it judged and what it wrote.
_CHILD = textwrap.dedent(r'''
    import json, sys
    inst, live = sys.argv[1], sys.argv[2]
    from quam_state_manager.web.app import create_app
    from quam_state_manager.web import routes
    from quam_state_manager.core import undo_journal
    app = create_app(testing=True, instance_path=inst)
    c = app.test_client()
    assert c.post("/load", data={"folder": live}).status_code in (200, 302)
    assert c.post("/auto-sync/set", data={"pull": "1", "pull_replace": "0", "push": "0"}).status_code == 200
    def live_t1():
        return json.load(open(live + "/state.json", encoding="utf-8"))["qubits"]["qA1"]["T1"]
    print("RESULT " + json.dumps({"ready": True, "t1": live_t1()}), flush=True)
    for line in sys.stdin:
        cmd = line.strip()
        if cmd == "PULL":
            with app.test_request_context():
                ctx = routes._active_ctx()
                held = routes._applied_values_held(ctx)
                units, cur = undo_journal.load_state(undo_journal.sidecar_path(inst, ctx["path"]))
                ctx["_live_hash_checked_at"] = None
                routes._refresh_live_diverged(ctx)
                diverged = bool(ctx.get("live_diverged"))
            r = c.post("/auto-sync/pull", data={"dom_dirty": "0"})
            trig = json.loads(r.headers.get("HX-Trigger") or "{}")
            print("RESULT " + json.dumps({
                "held_t1": held.get("qubits.qA1.T1"), "cursor": cur, "n": len(units),
                "stamped": "undone_ts" in ((units[-1].get("meta") or {}) if units else {}),
                "diverged": diverged, "status": r.status_code,
                "restored": (trig.get("autoSyncRestored") or {}).get("count"),
                "lost": (trig.get("autoSyncPulled") or {}).get("lost"),
                "live_t1": live_t1()}), flush=True)
        elif cmd == "QUIT":
            break
    sys.exit(0)
''')


def _read_result(p, timeout=120.0):
    out: dict = {}

    def _rd():
        while True:
            line = p.stdout.readline()
            if not line:
                out["eof"] = True
                return
            if line.startswith("RESULT "):
                out["r"] = json.loads(line[len("RESULT "):])
                return
    t = threading.Thread(target=_rd, daemon=True)
    t.start()
    t.join(timeout)
    assert "r" in out, f"no RESULT from the second process: {out} {p.stderr.read()[-3000:] if out.get('eof') else ''}"
    return out["r"]


def _start_second_process(env):
    e = dict(os.environ)
    e["PYTHONUTF8"] = "1"
    p = subprocess.Popen([sys.executable, "-c", _CHILD, env["app"].instance_path, str(env["live"])],
                         cwd=str(ROOT), env=e, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                         stderr=subprocess.PIPE, text=True, encoding="utf-8", errors="replace")
    ready = _read_result(p, timeout=240.0)
    assert ready.get("ready") and ready["t1"] == 3.3e-05, ready
    return p


def _tell(p, cmd):
    p.stdin.write(cmd + "\n")
    p.stdin.flush()


def test_a_second_process_in_the_write_window_never_reverts_the_undo(env, monkeypatch):  # noqa: F811
    """The second process reads the sidecar and pulls right after the undo's
    chip write returned, before anything else this process does: it must
    already see the unit undone, hold nothing to restore, and adopt the undo."""
    _apply_name(env)                                   # T1 2e-5 -> 3.3e-5, applied
    p = _start_second_process(env)                     # loads: RAM holds 3.3e-5
    real = routes_mod._flush_walk_step_live
    seen: dict = {}

    def _write_then_let_the_other_process_poll(ctx, *a, **k):
        body = real(ctx, *a, **k)
        if body.get("status") == "ok" and "pull" not in seen:
            _tell(p, "PULL")
            seen["pull"] = _read_result(p)
        return body
    monkeypatch.setattr(routes_mod, "_flush_walk_step_live", _write_then_let_the_other_process_poll)
    try:
        _undo(env)                                     # Ctrl+Z writes live: T1 back to 2e-5
    finally:
        try:
            _tell(p, "QUIT")
        except OSError:
            pass
        try:
            p.wait(60)
        except subprocess.TimeoutExpired:
            p.kill()
    other = seen.get("pull")
    assert other, "the hook never ran: the undo did not reach the chip"
    assert other["diverged"] and other["status"] == 200, other
    # what the second process judged from the shared sidecar, in the window
    assert other["cursor"] == other["n"] - 1 and other["stamped"], (
        "the sidecar still called the undone unit applied while the chip already held the undo: %r" % other)
    assert other["held_t1"] is None, "the undone value was judged a held apply: %r" % other
    # and what it did
    assert other["restored"] in (None, 0) and other["lost"] == [], other
    assert other["live_t1"] == 2e-05, "the second process wrote the pre-undo value back: %r" % other
    assert _live_t1(env) == 2e-05


def _moved_outside(env, **kw):
    """Someone else writes the live chip after our apply; the walk's door
    then refuses (the staleness gate) and the step is rolled back."""
    (env["live"] / "state.json").write_text(json.dumps(_state(**kw)), encoding="utf-8")
    os.utime(env["live"] / "state.json", None)


def test_a_refused_live_undo_puts_the_sidecar_cursor_back(env):  # noqa: F811
    _apply_name(env)
    units0, cur0 = undo_journal.load_state(_sidecar(env))
    assert cur0 == len(units0)
    _moved_outside(env, f01=5.5e9, t1=3.3e-05)
    r = env["client"].post("/undo")
    t = json.loads(r.headers["HX-Trigger"])["cellsReverted"]
    assert t["live"] is False and t["message"].startswith("Not undone"), t
    units, cur = undo_journal.load_state(_sidecar(env))
    assert cur == len(units) == cur0, "the cursor persisted before the refused write was not put back"
    assert "undone_ts" not in (units[-1].get("meta") or {}), "a refused undo left its stamp"
    with env["app"].test_request_context():
        assert routes_mod._active_ctx()["undo_cursor"] == cur0


def test_a_refused_live_redo_puts_the_sidecar_cursor_back(env):  # noqa: F811
    _apply_name(env)
    _undo(env)
    units0, cur0 = undo_journal.load_state(_sidecar(env))
    assert cur0 == len(units0) - 1 and "undone_ts" in units0[-1]["meta"]
    _moved_outside(env, f01=5.5e9, t1=2e-05)
    r = env["client"].post("/redo")
    assert r.status_code == 200
    assert _live_t1(env) == 2e-05, "the refused redo wrote the chip"
    units, cur = undo_journal.load_state(_sidecar(env))
    assert cur == cur0, "the cursor persisted before the refused write was not put back"
    assert "undone_ts" in (units[-1].get("meta") or {}), "the still-undone unit lost its stamp"
