"""Offline, durable SaaS ledger for P2 spec 5 piece K fault injection."""

import datetime as dt
import json
import os
from pathlib import Path
import threading
import time
import uuid

LOCK = threading.RLock()


def plan():
    """Read a replaceable plan so tests can release in-flight operations (P2 spec 5)."""
    for attempt in range(100):
        try:
            return json.loads(Path(os.environ["FAKE_SAAS_PLAN"]).read_text(encoding="ascii"))
        except PermissionError:
            if attempt == 99:
                raise
            time.sleep(0.005)


def record(kind, **fields):
    """Append one atomic ledger entry; tests serialize separate brokers (P2 spec 5)."""
    with LOCK:
        path = Path(os.environ["FAKE_SAAS_LEDGER"])
        entries = json.loads(path.read_text(encoding="ascii")) if path.exists() else []
        entries.append(dict(event=kind, **fields))
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(entries), encoding="ascii")
        for attempt in range(100):
            try:
                os.replace(tmp, path)
                return entries
            except PermissionError:
                if attempt == 99:
                    raise
                time.sleep(0.005)


def gate(name):
    """Hold a named crash window with a real-time cap (P2 spec 5)."""
    deadline = time.monotonic() + 20
    while plan().get(name):
        if time.monotonic() > deadline:
            raise TimeoutError(name)
        time.sleep(0.01)


class Client:
    """The close-by-id API surface of qm-saas 1.2.0 (D-17(c))."""

    def __init__(self, protocol, host, port, email, password, log):
        """Record login without persisting credentials (D-11)."""
        self._jwt = None
        self.log = log
        self._login()
        if password == "bad-password-sentinel" or plan().get("login_error"):
            log.error(password)
            raise Exception("login refused " + password)
        self._jwt = "session-sentinel-68ef-" + str(self.generation)

    def _login(self):
        """Keep a generation for a failure that only re-login can fix (P2 spec 3.3)."""
        entries = record("login")
        self.generation = sum(entry["event"] == "login" for entry in entries)

    def close_simulator(self, instance_id):
        """Fail before deletion, then record exactly one successful close (D-17(c))."""
        control = plan()
        if control.get("close_fails") or instance_id in control.get("bad_close_ids", []):
            record("close_failed", id=instance_id)
            raise Exception("close refused " + self._jwt + " token-sentinel-391a")
        if control.get("close_fails_until_relogin") and self.generation == 1:
            record("close_failed", id=instance_id)
            raise Exception("session expired " + self._jwt)
        entries = json.loads(Path(os.environ["FAKE_SAAS_LEDGER"]).read_text(encoding="ascii"))
        if not any(entry["event"] == "launch" and entry["id"] == instance_id for entry in entries) or any(
                entry["event"] == "close" and entry["id"] == instance_id for entry in entries):
            raise Exception("Unknown or closed simulator")
        record("close", id=instance_id)

    def launch_simulator(self, version, cluster_config):
        """Expose both sides of the POST crash window without networking (P2 spec 5)."""
        gate("hold_before_post")
        control = plan()
        if control.get("spawn_error"):
            raise Exception(control["spawn_error"])
        identifier = "fake-" + uuid.uuid4().hex[:12]
        record("launch", id=identifier,
               ghost=bool(control.get("die_after_post") or control.get("raise_after_post")), version=version,
               cluster=cluster_config)
        if control.get("raise_after_post"):
            raise Exception("ambiguous launch failure")
        if control.get("die_after_post"):
            os._exit(71)
        time.sleep(min(float(control.get("spawn_sleep_s", 0)), 20))
        gate("hold_after_post")
        now = float(control.get("now", time.time()))
        return dict(id=identifier, token="token-sentinel-391a", host=control.get("host", "192.0.2.20"),
                    port=control.get("port", 443), expires_at=dt.datetime.fromtimestamp(
                        now + 840, dt.timezone.utc).isoformat())
