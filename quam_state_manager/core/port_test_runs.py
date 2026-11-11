"""Port test: run targets, saved credentials and the one running test.

Settings (non-secret: host, cluster, email, SaaS host, amplitude cap) live in
``<instance>/port_test/settings.json``. A password is a SECRET: it is stored
ONLY in the OS keyring (``keyring``: Windows Credential Manager, macOS
Keychain, Secret Service) -- never in a file, state.json, the repo, a log line
or a page. Without a usable keyring a password is refused, not written
elsewhere. A plain-text ``secrets.json`` left by an older SM is moved into the
keyring (or, with no keyring, deleted) the first time credentials are read.

One test runs at a time per SM process. The run is a subprocess of the chip's
env (``generator/run_port_test.py``); the request with the credentials goes
over its stdin, which SM keeps open: a ``stop`` line or SM's exit (EOF) makes
the runner halt and close its QM. A hardware runner is never killed before a
fresh helper (``--recover``) has halted the job and closed OUR QM by its
recorded id. Active runs are recorded under ``<instance>/port_test/runs`` so a
restarted SM shows them and can still clean them up. A run whose page stopped
asking for its status is stopped by a watchdog (leaving the page stops it).
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

import psutil

logger = logging.getLogger(__name__)

RUNNER = Path(__file__).resolve().parent.parent / "generator" / "run_port_test.py"
#: the loopback offset calibration job runner (``@@SM-LOOPCAL@@`` progress lines on stdout)
LOOPCAL_RUNNER = Path(__file__).resolve().parent.parent / "generator" / "run_loopback_cal.py"
LOOPCAL_PREFIX = "@@SM-LOOPCAL@@"
KEYRING_SERVICE = "quam-state-manager/port-test"
#: a page polls status every ~1 s; this many seconds without a poll stops the run
HEARTBEAT_S = 12.0
#: a hardware run never lasts longer than this unless stopped sooner
MAX_RUN_S = 120.0
STOP_GRACE_S = 15.0
#: a simulator run is never killed mid-call (that would leave the cloud
#: instance running): it ends at its next check, or after this long
SAAS_STOP_GRACE_S = 180.0
KEEP_RUNS = 20
#: runner phases before any QM exists: a stop may end the runner outright
PRE_QM_PHASES = (None, "connecting", "checking for running jobs", "connecting to the local simulator")
#: the --recover helper (fresh QMM connection, halt, close) gets this long
RECOVER_TIMEOUT_S = 75.0
RECOVER_RETRY_S = 10.0


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


class KeyringUnavailable(ValueError):
    """No usable OS keyring: a password cannot be saved (the message is for the user)."""


KEYRING_MISSING = ("No OS keyring is available to SM (the 'keyring' package, or a keyring "
                   "backend, is missing), so the password cannot be saved. SM never stores "
                   "passwords in a file. Reinstall SM (keyring is one of its dependencies) "
                   "or, on Linux, install a Secret Service backend.")


def _keyring():
    try:
        import keyring  # noqa: PLC0415
        kr = keyring.get_keyring()
        # A chainer is only as good as what it chains; keyrings.alt's backends
        # write the password to a file, which is exactly what is refused.
        for backend in [kr] + list(getattr(kr, "backends", None) or []):
            mod = type(backend).__module__.lower()
            if "fail" in mod or "null" in mod or mod.startswith("keyrings.alt"):
                return None
        if type(kr).__module__.lower().endswith("chainer") and not getattr(kr, "backends", None):
            return None
        return keyring
    except Exception:  # noqa: BLE001 - no keyring: passwords cannot be saved
        return None


def keyring_available() -> bool:
    return _keyring() is not None


def _migrate_file_secrets(instance) -> None:
    """Move a plain-text secrets.json (older SM) into the keyring, then delete it.
    Without a keyring it is deleted anyway: a plain-text password is never kept."""
    p = _dir(instance) / "secrets.json"
    if not p.exists():
        return
    data = _read(p)
    kr = _keyring()
    moved = 0
    if kr is not None:
        for key, value in data.items():
            if isinstance(key, str) and isinstance(value, str) and value:
                try:
                    if not kr.get_password(KEYRING_SERVICE, key):
                        kr.set_password(KEYRING_SERVICE, key, value)
                    moved += 1
                except Exception:  # noqa: BLE001 - the file still goes
                    pass
    try:
        p.unlink()
    except OSError:
        logger.warning("port test: could not delete the old plain-text secrets file")
        return
    logger.info("port test: removed the old plain-text secrets file (%d moved to the keyring)", moved)


def save_password(instance, settings: dict, password: str) -> str:
    """Store the SaaS password in the OS keyring; returns "keyring".
    Raises KeyringUnavailable when there is no usable keyring."""
    _migrate_file_secrets(instance)
    key = _secret_key(settings)
    if not key:
        raise ValueError("Set the email and host before saving a password.")
    kr = _keyring()
    if kr is None:
        raise KeyringUnavailable(KEYRING_MISSING)
    try:
        kr.set_password(KEYRING_SERVICE, key, password)
    except Exception as exc:  # noqa: BLE001 - never fall back to a file
        raise KeyringUnavailable(f"The OS keyring refused the password ({type(exc).__name__}); "
                                 "it was not saved.") from None
    return "keyring"


def load_password(instance, settings: dict) -> tuple[Optional[str], Optional[str]]:
    _migrate_file_secrets(instance)
    key = _secret_key(settings)
    kr = _keyring()
    if not key or kr is None:
        return None, None
    try:
        v = kr.get_password(KEYRING_SERVICE, key)
    except Exception:  # noqa: BLE001
        return None, None
    return (v, "keyring") if v else (None, None)


def remove_password(instance, settings: dict) -> None:
    _migrate_file_secrets(instance)
    key = _secret_key(settings)
    kr = _keyring()
    if not key or kr is None:
        return
    try:
        kr.delete_password(KEYRING_SERVICE, key)
    except Exception:  # noqa: BLE001 - nothing saved there
        pass


def public_view(instance) -> dict:
    """Settings as a page may see them: the password only as saved / where."""
    s = load_settings(instance)
    _, where = load_password(instance, s)
    s["saas"]["password_saved"] = bool(where)
    s["saas"]["password_where"] = "OS keyring" if where else None
    s["keyring_available"] = keyring_available()
    return s


class SavedProcess:
    """A run started by an earlier SM process: observed by PID + creation time
    (a recycled PID is never mistaken for it)."""

    def __init__(self, pid, created):
        self.pid, self.created = pid, created
        self.returncode = None
        self.stdin = None

    def poll(self):
        try:
            proc = psutil.Process(int(self.pid))
            if self.created is not None and proc.is_running() and \
                    abs(proc.create_time() - float(self.created)) < 1e-3:
                return None
        except (psutil.Error, TypeError, ValueError):
            pass
        self.returncode = -1
        return self.returncode

    def kill(self):
        if self.poll() is None:
            psutil.Process(int(self.pid)).kill()


def _needs_cleanup(run: dict) -> bool:
    """A hardware run whose QM may still be open (opened or opening, not yet
    reported closed, and no verified recovery)."""
    if not run.get("hardware_target"):
        return False
    st = _read(run["work"] / "_status.json")
    if st.get("cleaned") or _read(run["work"] / "_recovery.json").get("ok"):
        return False
    return bool(st.get("qm_id")) or st.get("phase") == "opening a QM"


def _is_unknown_qm_state(run: dict, st: dict | None = None) -> bool:
    """Runner ended while opening a QM before recording its id."""
    if not run.get("hardware_target"):
        return False
    data = st if isinstance(st, dict) else _read(run["work"] / "_status.json")
    if data.get("cleaned") or _read(run["work"] / "_recovery.json").get("ok"):
        return False
    return data.get("phase") == "opening a QM" and not data.get("qm_id")


class Runs:
    """The one port test running in this SM process."""

    def __init__(self, instance, *, resume: bool = True):
        self.instance = Path(instance)
        self._lock = threading.Lock()
        #: one-time hardware confirmations: token -> (expires monotonic, owner, binding)
        self.confirmations: dict = {}
        self.port_play_generation = 0
        self.current: Optional[dict] = None
        if resume:
            self._resume()

    def _resume(self) -> None:
        """Pick up a run an earlier SM left behind: still alive, or a hardware
        run whose QM was not reported closed. It shows on the page and can be stopped."""
        saved = sorted(self._runs_dir().glob("*/_run.json"), key=lambda p: p.stat().st_mtime)
        for path in reversed(saved):
            data = _read(path)
            if not data.get("id"):
                continue
            run = {**data, "work": path.parent, "proc": SavedProcess(data.get("pid"), data.get("created")),
                   "seen": time.time(), "stop_at": None, "resumed": True}
            if run["proc"].poll() is None or _needs_cleanup(run):
                self.current = run
                threading.Thread(target=self._watch, args=(run,), daemon=True).start()
                return

    def _runs_dir(self) -> Path:
        d = _dir(self.instance) / "runs"
        d.mkdir(exist_ok=True)
        return d

    def _prune(self):
        runs = sorted(self._runs_dir().iterdir(), key=lambda p: p.stat().st_mtime)
        for p in runs[:-KEEP_RUNS]:
            if self.current and p == self.current["work"]:
                continue
            if _needs_cleanup({**_read(p / "_run.json"), "work": p}):
                continue  # its QM id is the only way to close it
            shutil.rmtree(p, ignore_errors=True)

    def start(self, python: str, script: str, request: dict, meta: dict,
              popen=subprocess.Popen, job: Optional[dict] = None) -> dict:
        """Start the one port test. With ``job`` the loopback calibration
        runner runs that job instead of ``script`` (same busy/stop/owner rules;
        Stop = the ``stop`` file, result = ``_result.json``)."""
        with self._lock:
            generation = request.get("_port_play_generation")
            if generation is not None and generation != self.port_play_generation:
                raise RuntimeError("Play was stopped while preparing the config.")
            cur = self.current
            if cur and cur["proc"].poll() is None:
                raise RuntimeError("Another port test is still running; stop it first.")
            if cur and _needs_cleanup(cur):
                if _is_unknown_qm_state(cur):
                    raise RuntimeError("A previous run is in an unknown state: the QM may be open. "
                                       "Use 'Close our port-test QMs' first.")
                raise RuntimeError("The previous hardware run's QM is not confirmed closed yet; "
                                   "press Stop to close it first.")
            rid = time.strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:6]
            work = self._runs_dir() / rid
            work.mkdir()
            (work / "script.py").write_text(script, encoding="utf-8")
            if job is not None:
                (work / "job.json").write_text(json.dumps(
                    {**job, "result_file": "_result.json", "stop_file": "stop"}), encoding="utf-8")
            req = dict(request)
            req.pop("_port_play_generation", None)
            play_owner = req.pop("_play_owner", None)
            req.setdefault("max_s", MAX_RUN_S)
            flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if sys.platform == "win32" else 0
            if job is None:
                proc = popen([python, str(RUNNER), str(work / "script.py"), str(work)],
                             stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                             text=True, creationflags=flags)
            else:
                with open(work / "stdout.log", "w", encoding="utf-8") as out,                         open(work / "stderr.log", "w", encoding="utf-8") as err:
                    proc = popen([python, str(LOOPCAL_RUNNER), str(work / "job.json")],
                                 stdin=subprocess.PIPE, stdout=out, stderr=err,
                                 text=True, creationflags=flags)
            try:
                # One line, then stdin stays OPEN: end-of-file tells the runner SM is gone.
                proc.stdin.write(json.dumps(req) + "\n")
                proc.stdin.flush()
            except (OSError, ValueError, AttributeError):
                pass
            try:
                created = psutil.Process(proc.pid).create_time()
            except (AttributeError, TypeError, ValueError, psutil.Error):
                created = None  # a fake process in tests
            saved = {"id": rid, "pid": getattr(proc, "pid", None), "created": created,
                     "python": python, "meta": dict(meta), "play_owner": play_owner, "started": time.time(),
                     "hardware_target": (dict(request.get("target") or {})
                                         if request.get("mode") == "opx" else None)}
            _write(work / "_run.json", saved)
            self.current = {**saved, "work": work, "proc": proc, "seen": time.time(), "stop_at": None}
            threading.Thread(target=self._watch, args=(self.current,), daemon=True).start()
            self._prune()
            return {"id": rid}

    def _recover(self, run: dict) -> bool:
        """Halt + close OUR QM from a fresh helper process; True when verified
        (or when there is nothing to clean)."""
        if not _needs_cleanup(run):
            return True
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if sys.platform == "win32" else 0
        try:
            result = subprocess.run([run["python"], str(RUNNER), str(run["work"] / "script.py"),
                                     str(run["work"]), "--recover"],
                                    stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                    stderr=subprocess.DEVNULL, timeout=RECOVER_TIMEOUT_S,
                                    creationflags=flags)
            ok = result.returncode == 0
        except (OSError, subprocess.TimeoutExpired):
            ok = False
        return ok and not _needs_cleanup(run)

    def close_matching_open_qms(self, rid: Optional[str] = None) -> dict:
        """List open QMs on the run target and close only config-matching ones."""
        run = self.current
        if not run or (rid and run["id"] != rid):
            raise RuntimeError("No matching port test run is active.")
        if run["proc"].poll() is None:
            raise RuntimeError("Stop the running test first.")
        if not _is_unknown_qm_state(run):
            raise RuntimeError("This action is only for an unknown opening-QM state.")
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if sys.platform == "win32" else 0
        info_path = run["work"] / "_close_matching.json"
        try:
            result = subprocess.run(
                [run["python"], str(RUNNER), str(run["work"] / "script.py"), str(run["work"]), "--close-matching"],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=RECOVER_TIMEOUT_S,
                creationflags=flags,
            )
            data = _read(info_path)
            if result.returncode != 0 or not data.get("ok"):
                raise RuntimeError(data.get("error") or "Could not list/close matching open QMs.")
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise RuntimeError(f"Could not list/close matching open QMs ({type(exc).__name__}).") from None
        st = _read(run["work"] / "_status.json")
        st["cleaned"] = True
        st["phase"] = "unknown state cleared"
        _write(run["work"] / "_status.json", st)
        with self._lock:
            if self.current is run:
                self.current = None
        data["cleared_run"] = run["id"]
        return data

    @staticmethod
    def _close_stdin(run: dict) -> None:
        pipe = getattr(run["proc"], "stdin", None)
        try:
            if pipe is not None:
                pipe.close()
        except (OSError, ValueError):
            pass

    def _watch(self, run: dict) -> None:
        while True:
            if run["proc"].poll() is not None:
                self._close_stdin(run)
                # The runner is gone; its QM may not be: retry until closed.
                while _needs_cleanup(run) and self.current is run:
                    if self._recover(run):
                        break
                    run["stop_why"] = "the QM is not confirmed closed; retrying halt/close"
                    time.sleep(RECOVER_RETRY_S)
                return
            time.sleep(1.0)
            if run["stop_at"] is None and time.time() - run["seen"] > HEARTBEAT_S:
                logger.info("port test %s: the page stopped asking; stopping the run", run["id"])
                self._request_stop(run, "the page was left")
            if run["stop_at"] is not None and self._stop_before_qm(run):
                continue
            grace = SAAS_STOP_GRACE_S if run["meta"].get("target") in ("saas", "sim") else STOP_GRACE_S
            if run["stop_at"] is not None and time.time() - run["stop_at"] > grace:
                # The runner did not halt in time. Never kill a hardware runner
                # first: a fresh helper halts the job and closes OUR QM, then kill.
                if self._recover(run):
                    run["proc"].kill()
                else:
                    run["stop_why"] = "the QM is not confirmed closed; retrying halt/close"
                    run["stop_at"] = time.time()

    def _stop_before_qm(self, run: dict) -> bool:
        """A stop asked while the runner has not opened a QM yet (it may sit up to
        a call timeout inside ``connect``): nothing on the instrument is ours, so
        the runner is ended at once and the answer says it was stopped. A cloud
        simulator run is never cut (its instance would keep running)."""
        if run["meta"].get("target") == "saas" or _needs_cleanup(run):
            return False
        st = _read(run["work"] / "_status.json")
        if st.get("qm_id") or st.get("phase") not in PRE_QM_PHASES:
            return False
        try:
            run["proc"].kill()
        except (OSError, AttributeError, psutil.Error):
            return False
        if _needs_cleanup(run):  # it reached "opening a QM" meanwhile: the cleanup path owns it
            return True
        result = run["work"] / "_result.json"
        if not _read(result):
            _write(result, {"ok": False, "stopped": True, "why": run.get("stop_why")})
        return True

    def _request_stop(self, run: dict, why: str) -> None:
        if run["stop_at"] is None:
            run["stop_at"] = time.time()
            run["stop_why"] = why
        try:
            (run["work"] / "stop").write_text(why, encoding="utf-8")
        except OSError:
            pass
        pipe = getattr(run["proc"], "stdin", None)
        try:
            if pipe is not None:
                pipe.write("stop\n")
                pipe.flush()
        except (OSError, ValueError, AttributeError):
            pass

    def stop(self, rid: Optional[str] = None, why: str = "Stop pressed") -> bool:
        with self._lock:
            self.port_play_generation += 1
        run = self.current
        if not run or (rid and run["id"] != rid):
            return False
        if run["proc"].poll() is not None:
            if not _needs_cleanup(run):
                return False
            # A runner that died (or an earlier SM's) with its QM maybe open.
            run["stop_why"] = why
            threading.Thread(target=self._recover, args=(run,), daemon=True).start()
            return True
        self._request_stop(run, why)
        return True

    def status(self, rid: Optional[str] = None) -> dict:
        run = self.current
        if not run or (rid and run["id"] != rid):
            return {"state": "none"}
        run["seen"] = time.time()
        st = _read(run["work"] / "_status.json")
        alive = run["proc"].poll() is None
        unclean = _needs_cleanup(run)
        unknown = (not alive) and unclean and _is_unknown_qm_state(run, st)
        out = {"id": run["id"], "state": "running" if alive or unclean else "done",
               "phase": st.get("phase"), "detail": st.get("detail"),
               "qm_id": st.get("qm_id"), "job_id": st.get("job_id"),
               "cleanup_pending": unclean and not alive, "resumed": bool(run.get("resumed")),
               "recovery": _read(run["work"] / "_recovery.json"), "meta": run["meta"],
               "elapsed_s": round(time.time() - run["started"], 1),
               "stopping": run["stop_at"] is not None, "stop_why": run.get("stop_why"),
               "unknown_qm_state": unknown}
        progress = loopcal_progress(run["work"])
        if progress is not None:
            out["progress"] = progress
        if not alive and unclean:
            out["phase"] = ("unknown state: the QM may be open"
                            if unknown else "the runner ended; closing our QM")
        if not alive and not unclean:
            res = _read(run["work"] / "_result.json")
            if not res:
                res = {"ok": False, "error": "The test process ended without an answer "
                                             f"(exit code {run['proc'].returncode})."}
            if run["stop_at"] is not None and not res.get("ok"):
                # A stop the user asked for ends as stopped, whatever the SDK raised meanwhile.
                res = dict(res, stopped=True)
            out["result"] = res
        return out


def loopcal_progress(work: Path) -> Optional[list]:
    """Progress messages of a loopback calibration run (None for other runs)."""
    path = Path(work) / "stdout.log"
    if not path.exists():
        return None
    lines = []
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    for line in text.splitlines():
        if not line.startswith(LOOPCAL_PREFIX):
            continue
        try:
            event = json.loads(line[len(LOOPCAL_PREFIX):])
        except ValueError:
            continue
        if isinstance(event, dict) and event.get("event") == "progress":
            lines.append(str(event.get("message", "")))
    return lines
