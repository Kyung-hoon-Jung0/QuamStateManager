"""E-1 merge gate: a killed SM and broker leave nothing live after a restart (P2 spec 5 and 7, E-1, FI-8a).

Real warm worker + real broker + real SM pool, only qm_saas faked. The first SM runs in its own process so it can
be killed: it holds one idle live lease A, then starts spawn B, which the fake SaaS holds before its POST. The SM is
suspended, the broker killed, then the SM killed. A restarted SM-side pool starts a fresh broker whose reaper must
close A exactly once and mark B the named ghost; the table ends with 0 live rows.
"""
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time

import psutil
import pytest

ROOT = Path(__file__).resolve().parents[1]
# The restarted broker's clock runs past the spawn cap, so the reaper may call B a ghost (P2 spec 3.4).
RESTART_OFFSET_S = 241.0


def test_e1_merge_dependencies_are_present():
    """Fail explicitly on absent real worker, broker or fake SaaS (D-10, P2 spec 5 E-1)."""
    required = ("quam_state_manager/generator/nodesim_warm.py",
                "quam_state_manager/generator/nodesim_broker.py",
                "tests/fakes/fake_qm_saas/qm_saas/__init__.py")
    missing = [p for p in required if not (ROOT / p).is_file()]
    assert not missing, "E-1 requires merged W and K; missing: " + ", ".join(missing)


def publish(path, data):
    """Write through tmp + replace so a reader never sees half a file (review S P3-9)."""
    tmp = Path(str(path) + ".tmp")
    tmp.write_text(json.dumps(data), encoding="ascii")
    os.replace(tmp, path)


def read_json(path, default=None):
    """Read an atomically replaced file without holding a handle (P2 spec 7)."""
    try:
        return json.loads(Path(path).read_text(encoding="ascii"))
    except (OSError, ValueError):
        return default


def created(pid):
    """Process creation time, or None when it is gone (D-46)."""
    try:
        return psutil.Process(pid).create_time()
    except psutil.Error:
        return None


def alive(pid, when):
    """The recorded process still runs (D-46 tolerance)."""
    try:
        process = psutil.Process(pid)
        return abs(process.create_time() - when) <= 0.01 and process.status() != psutil.STATUS_ZOMBIE
    except psutil.Error:
        return False


def until(predicate, cap=30):
    """Poll one verdict with a fixed cap (P2 spec 7.11)."""
    deadline = time.monotonic() + cap
    while time.monotonic() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(0.05)
    raise AssertionError("verdict deadline exceeded")


def sm_main(tmp):
    """The first SM: lease A on one chip, then spawn B on another; wait to be killed (capped at 120 s)."""
    from tests.test_nodesim_seam import Seam
    tmp = Path(tmp)
    rig = Seam(tmp)
    warm = rig.pool()
    info = dict(sm_pid=os.getpid())

    def save():
        """Report every child this SM started, broker first, for the parent's reaping (spec 7.10)."""
        info["children"] = [(p.pid, created(p.pid)) for p in rig.brokers] + [
            (w.proc.pid, created(w.proc.pid)) for w in rig.workers]
        publish(tmp / "sm.json", info)

    try:
        result = rig.run(warm, rig.chip("live-a"))
        info.update(kind=result["kind"], a=next(iter(warm.leases.values()))["instance_id"])
        save()
        deadline = time.monotonic() + 60
        while not (tmp / "go").exists() and time.monotonic() < deadline:
            time.sleep(0.02)

        def spawn_b():
            """The second click needs a new instance; the fake SaaS holds it before the POST."""
            try:
                rig.run(warm, rig.chip("live-b"))
            except Exception as exc:
                info["b_error"] = type(exc).__name__

        threading.Thread(target=spawn_b, daemon=True).start()
        deadline = time.monotonic() + 120
        while time.monotonic() < deadline:
            save()
            time.sleep(0.1)
    finally:
        # Review int r1 P2-4: an early failure in this SM reaps its own broker and workers before it exits.
        save()
        rig.finish()


def restarted_seam(tmp, first):
    """A second SM-side rig on the first rig's broker directory, plan and ledger, clock offset (D-17)."""
    from tests.test_nodesim_seam import BROKER, FAKE_SAAS, Seam, record

    class Restart(Seam):
        """The production pool and broker argv, with the broker clock moved past the spawn cap."""

        def popen(self, argv, **kw):
            """Same flags as the seam rig plus the clock offset (P2 spec 3.1)."""
            assert argv[1:3] == ["-I", "-B"] and Path(argv[3]) == BROKER, argv
            code = ("import sys,time,importlib.util;"
                    f"sys.path.insert(0,{str(FAKE_SAAS)!r});"
                    f"s=importlib.util.spec_from_file_location('broker',{str(self.broker_source)!r});"
                    "b=importlib.util.module_from_spec(s);s.loader.exec_module(b);"
                    f"b.clock=lambda:time.time()+{RESTART_OFFSET_S!r};"
                    f"raise SystemExit(b.main({argv[4]!r}))")
            kw["env"] = dict(kw["env"], FAKE_SAAS_PLAN=str(self.plan_path), FAKE_SAAS_LEDGER=str(self.ledger_path))
            process = subprocess.Popen([argv[0], "-I", "-B", "-c", code], **kw)
            record(process.pid)
            self.brokers.append(process)
            return process

    rig = Restart(tmp)
    rig.directory, rig.plan_path, rig.ledger_path = first / "broker", first / "plan.json", first / "ledger.json"
    return rig


def test_e1_killed_sm_and_broker_leave_nothing_live(tmp_path):
    """E-1: A closed exactly once by the restart's reaper, B the named ghost, 0 live rows (P2-14)."""
    from tests.test_nodesim_seam import CREDS
    first, second = tmp_path / "sm1", tmp_path / "sm2"
    first.mkdir()
    second.mkdir()
    env = {k: v for k, v in os.environ.items() if not k.startswith("SAAS_")}
    env.update(PYTHONPATH=str(ROOT), PYTHONUTF8="1")
    started = []
    log = open(first / "sm.log", "wb")
    sm = subprocess.Popen([sys.executable, "-B", str(Path(__file__)), "--sm", str(first)], cwd=ROOT, env=env,
                          stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT)
    started.append((sm.pid, created(sm.pid)))
    rig = None

    def rows():
        """The durable broker table (D-17)."""
        return (read_json(first / "broker" / "leases.json", {}) or {}).get("rows", [])

    def ledger(event):
        """Successful fake SaaS side effects (D-10)."""
        return [e for e in read_json(first / "ledger.json", []) if e["event"] == event]

    try:
        until(lambda: (read_json(first / "sm.json") or {}).get("a"), cap=90)
        info = read_json(first / "sm.json")
        assert info["kind"] == "ok"
        a = info["a"]
        publish(first / "plan.json", dict(hold_before_post=True))
        (first / "go").write_text("go", encoding="ascii")
        until(lambda: any(r["state"] == "pending" for r in rows()), cap=60)
        info = read_json(first / "sm.json")
        children = [tuple(c) for c in info["children"] if c[1]]
        started.extend(children)
        assert len(children) >= 3, info  # one broker and two workers
        broker = children[0]

        # Kill both owners of the leases: freeze SM first so it cannot restart a broker in the gap.
        psutil.Process(sm.pid).suspend()
        psutil.Process(broker[0]).kill()
        until(lambda: not alive(*broker), cap=10)
        sm.kill()
        sm.wait(timeout=10)
        publish(first / "plan.json", {})
        assert not ledger("close"), "the dying pair closed A; the restart's reaper would have nothing to do"
        before = sorted(r["state"] for r in rows())
        assert before == ["live", "pending"], before
        for pid, when in children:
            until(lambda: not alive(pid, when), cap=15)

        # Restart: a new SM-side pool starts a fresh broker on the same table.
        rig = restarted_seam(second, first)
        warm = rig.pool()
        warm.ensure_broker(sys.executable, CREDS)
        assert rig.starts == 1
        until(lambda: [e for e in ledger("close") if e["id"] == a], cap=30)
        until(lambda: len(rows()) == 1 and rows()[0]["state"] == "ghost", cap=30)
        status = warm.instances()
        time.sleep(1.0)
        assert [e["id"] for e in ledger("close")] == [a], ledger("close")
        assert [e["id"] for e in ledger("launch")] == [a], "B was never posted"
        assert [r["state"] for r in status["rows"]] == ["ghost"], status
        assert not [r for r in rows() if r["state"] == "live"]
    finally:
        try:
            if rig is not None:
                rig.finish()
        finally:
            # Review int r1 P2-4: on a red verdict before the children were recorded, take the first SM's
            # process tree and its own report before it is killed, so its broker and workers are reaped too.
            if sm.poll() is None:
                try:
                    started.extend((c.pid, c.create_time()) for c in psutil.Process(sm.pid).children(recursive=True))
                except psutil.Error:
                    pass
            started.extend(tuple(c) for c in (read_json(first / "sm.json") or {}).get("children", []) if c[1])
            for pid, when in started:
                if when and alive(pid, when):
                    psutil.Process(pid).kill()
            if sm.poll() is None:
                sm.kill()
            sm.wait(timeout=10)
            log.close()
            left = [pid for pid, when in started if when and alive(pid, when)]
            assert not left, "E-1 processes left: " + repr(left)


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "--sm":
        sys.path.insert(0, str(ROOT))
        sm_main(sys.argv[2])
    else:
        raise SystemExit(pytest.main([__file__]))
