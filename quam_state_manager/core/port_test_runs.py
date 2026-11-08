"""Port test: run targets, saved credentials and the one running test.

Settings (non-secret: host, cluster, email, SaaS host, amplitude cap) live in
``<instance>/port_test/settings.json``. A password is a SECRET: it goes to the
OS keyring when the ``keyring`` package is importable in SM's own process,
else to ``<instance>/port_test/secrets.json`` -- never to state.json, the repo,
a log line or a page. A page only ever sees ``saved`` + where.

One test runs at a time per SM process. The run is a subprocess of the chip's
env (``generator/run_port_test.py``); the request with the credentials goes
over its stdin. A run whose page stopped asking for its status is stopped by a
watchdog (leaving the page stops the run).
"""
from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

RUNNER = Path(__file__).resolve().parent.parent / "generator" / "run_port_test.py"
KEYRING_SERVICE = "quam-state-manager/port-test"
#: a page polls status every ~1 s; this many seconds without a poll stops the run
HEARTBEAT_S = 12.0
#: a hardware run never lasts longer than this unless stopped sooner
MAX_RUN_S = 600.0
STOP_GRACE_S = 15.0
#: a simulator run is never killed mid-call (that would leave the cloud
#: instance running): it ends at its next check, or after this long
SAAS_STOP_GRACE_S = 180.0
KEEP_RUNS = 20


def _dir(instance) -> Path:
    d = Path(instance) / "port_test"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _read(p: Path) -> dict:
    try:
        v = json.loads(p.read_text(encoding="utf-8"))
        return v if isinstance(v, dict) else {}
    except (OSError, ValueError):
        return {}


def _write(p: Path, data: dict) -> None:
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=1), encoding="utf-8")
    os.replace(tmp, p)


DEFAULT_SETTINGS = {"target": "saas", "opx": {"host": "", "cluster": "", "port": ""},
                    "saas": {"email": "", "host": "", "version": ""},
                    "amp_cap": 0.5}


def load_settings(instance) -> dict:
    s = _read(_dir(instance) / "settings.json")
    out = json.loads(json.dumps(DEFAULT_SETTINGS))
    if s.get("target") in ("saas", "opx"):
        out["target"] = s["target"]
    for k in ("opx", "saas"):
        if isinstance(s.get(k), dict):
            out[k].update({kk: str(v) for kk, v in s[k].items() if kk in out[k]})
    try:
        out["amp_cap"] = float(s.get("amp_cap", out["amp_cap"]))
    except (TypeError, ValueError):
        pass
    return out


def save_settings(instance, settings: dict) -> None:
    clean = load_settings(instance)
    if settings.get("target") in ("saas", "opx"):
        clean["target"] = settings["target"]
    for k in ("opx", "saas"):
        if isinstance(settings.get(k), dict):
            clean[k].update({kk: str(v).strip() for kk, v in settings[k].items() if kk in clean[k]})
    if "amp_cap" in settings:
        clean["amp_cap"] = float(settings["amp_cap"])
    _write(_dir(instance) / "settings.json", clean)


def _secret_key(settings: dict) -> Optional[str]:
    s = settings.get("saas") or {}
    if not s.get("email") or not s.get("host"):
        return None
    return f"saas:{s['email']}@{s['host']}"


def _keyring():
    try:
        import keyring  # noqa: PLC0415
        kr = keyring.get_keyring()
        if "fail" in type(kr).__module__.lower() or "null" in type(kr).__module__.lower():
            return None
        return keyring
    except Exception:  # noqa: BLE001 - no keyring -> the instance folder
        return None


def save_password(instance, settings: dict, password: str) -> str:
    """Store the SaaS password; returns where ("keyring" or "instance")."""
    key = _secret_key(settings)
    if not key:
        raise ValueError("Set the email and host before saving a password.")
    kr = _keyring()
    if kr is not None:
        try:
            kr.set_password(KEYRING_SERVICE, key, password)
            _drop_file_secret(instance, key)
            return "keyring"
        except Exception:  # noqa: BLE001 - fall back to the file
            logger.warning("port test: the OS keyring refused the secret; using the instance folder")
    p = _dir(instance) / "secrets.json"
    data = _read(p)
    data[key] = password
    _write(p, data)
    return "instance"


def _drop_file_secret(instance, key: str) -> None:
    p = _dir(instance) / "secrets.json"
    data = _read(p)
    if key in data:
        data.pop(key)
        _write(p, data)


def load_password(instance, settings: dict) -> tuple[Optional[str], Optional[str]]:
    key = _secret_key(settings)
    if not key:
        return None, None
    kr = _keyring()
    if kr is not None:
        try:
            v = kr.get_password(KEYRING_SERVICE, key)
            if v:
                return v, "keyring"
        except Exception:  # noqa: BLE001
            pass
    v = _read(_dir(instance) / "secrets.json").get(key)
    return (v, "instance") if v else (None, None)


def remove_password(instance, settings: dict) -> None:
    key = _secret_key(settings)
    if not key:
        return
    kr = _keyring()
    if kr is not None:
        try:
            kr.delete_password(KEYRING_SERVICE, key)
        except Exception:  # noqa: BLE001 - nothing saved there
            pass
    _drop_file_secret(instance, key)


def public_view(instance) -> dict:
    """Settings as a page may see them: the password only as saved / where."""
    s = load_settings(instance)
    _, where = load_password(instance, s)
    s["saas"]["password_saved"] = bool(where)
    s["saas"]["password_where"] = {"keyring": "OS keyring", "instance": "SM instance folder"}.get(where)
    return s


class Runs:
    """The one port test running in this SM process."""

    def __init__(self, instance):
        self.instance = Path(instance)
        self._lock = threading.Lock()
        self.current: Optional[dict] = None

    def _runs_dir(self) -> Path:
        d = _dir(self.instance) / "runs"
        d.mkdir(exist_ok=True)
        return d

    def _prune(self):
        runs = sorted(self._runs_dir().iterdir(), key=lambda p: p.stat().st_mtime)
        for p in runs[:-KEEP_RUNS]:
            shutil.rmtree(p, ignore_errors=True)

    def start(self, python: str, script: str, request: dict, meta: dict,
              popen=subprocess.Popen) -> dict:
        with self._lock:
            if self.current and self.current["proc"].poll() is None:
                raise RuntimeError("Another port test is still running; stop it first.")
            rid = time.strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:6]
            work = self._runs_dir() / rid
            work.mkdir()
            (work / "script.py").write_text(script, encoding="utf-8")
            req = dict(request)
            req.setdefault("max_s", MAX_RUN_S)
            flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if sys.platform == "win32" else 0
            proc = popen([python, str(RUNNER), str(work / "script.py"), str(work)],
                         stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         text=True, creationflags=flags)
            try:
                proc.stdin.write(json.dumps(req))
                proc.stdin.close()
            except OSError:
                pass
            self.current = {"id": rid, "work": work, "proc": proc, "meta": dict(meta),
                            "started": time.time(), "seen": time.time(), "stop_at": None}
            threading.Thread(target=self._watch, args=(self.current,), daemon=True).start()
            self._prune()
            return {"id": rid}

    def _watch(self, run: dict) -> None:
        while run["proc"].poll() is None:
            time.sleep(1.0)
            if run["stop_at"] is None and time.time() - run["seen"] > HEARTBEAT_S:
                logger.info("port test %s: the page stopped asking; stopping the run", run["id"])
                self._request_stop(run, "the page was left")
            grace = SAAS_STOP_GRACE_S if run["meta"].get("target") == "saas" else STOP_GRACE_S
            if run["stop_at"] is not None and time.time() - run["stop_at"] > grace:
                run["proc"].kill()

    def _request_stop(self, run: dict, why: str) -> None:
        if run["stop_at"] is None:
            run["stop_at"] = time.time()
            run["stop_why"] = why
            try:
                (run["work"] / "stop").write_text(why, encoding="utf-8")
            except OSError:
                pass

    def stop(self, rid: Optional[str] = None, why: str = "Stop pressed") -> bool:
        run = self.current
        if not run or (rid and run["id"] != rid) or run["proc"].poll() is not None:
            return False
        self._request_stop(run, why)
        return True

    def status(self, rid: Optional[str] = None) -> dict:
        run = self.current
        if not run or (rid and run["id"] != rid):
            return {"state": "none"}
        run["seen"] = time.time()
        st = _read(run["work"] / "_status.json")
        alive = run["proc"].poll() is None
        out = {"id": run["id"], "state": "running" if alive else "done", "phase": st.get("phase"),
               "detail": st.get("detail"), "meta": run["meta"],
               "elapsed_s": round(time.time() - run["started"], 1),
               "stopping": run["stop_at"] is not None, "stop_why": run.get("stop_why")}
        if not alive:
            res = _read(run["work"] / "_result.json")
            if not res:
                res = {"ok": False, "error": "The test process ended without an answer "
                                             f"(exit code {run['proc'].returncode})."}
            out["result"] = res
        return out
