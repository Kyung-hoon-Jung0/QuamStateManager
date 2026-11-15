"""Simulate a previewed gate's ``apply()`` program on the QOP simulator (no QM import).

The lab side is ``generator/run_gate_sim.py``. This module holds the
request checks, the run bookkeeping (one run per window, on the port test's
:class:`core.port_test_runs.Runs` lifecycle) and the shaping of the answer
into the per-port samples the gate timeline overlays.

Connection: the port test's saved targets (Settings, "Port test run targets")
and its credentials ladder; no setting of its own. A local QOP host is tried
first when one is saved, then the QM-SaaS cloud simulator when its account is
complete. Only ``simulate`` runs; nothing is played on an instrument.

Limits: the simulated duration is the timeline's program time plus the
largest output delay of the played lines, :data:`START_LATENCY_NS` and
:data:`PAD_NS`, rounded up to 4 ns, and at most :data:`MAX_SIM_NS`. A run
ends after :data:`LIMIT_LOCAL_S` on a local QOP, :data:`LIMIT_CLOUD_S` when
the cloud simulator may be used; that is reported as ``timeout``.

Every answer a page sees is ``{"state", "line", ...}`` with ``state`` one of
:data:`STATES` and ``line`` the one sentence to show. Nothing here invents a
waveform: ``ports`` is set only in ``done`` and only from the simulator.
"""
from __future__ import annotations

import json
import math
import numbers
import os
import re
import threading
import time
from pathlib import Path
from typing import Any, Callable, Optional

from quam_state_manager.core import port_test_runs as ptr

RUNNER = Path(__file__).resolve().parent.parent / "generator" / "run_gate_sim.py"

PAD_NS = 200
#: The simulator starts its output after the program start: about 250 ns on the
#: cloud simulator version tested (a measured property, not a documented one),
#: plus the port delay of a delayed line. The window must cover it, or a
#: delayed line is cut before its end.
START_LATENCY_NS = 300
MAX_SIM_NS = 20_000
LIMIT_LOCAL_S = 60.0
LIMIT_CLOUD_S = 240.0
#: seconds past the limit before SM itself ends a runner that did not
SM_SLACK_S = 45.0

STATES = ("none", "not_configured", "no_env", "not_run", "busy", "starting", "running",
          "done", "stopped", "failed", "timeout", "too_long")

WHERE_SET = "Settings, Port test run targets"
LINES = {
    "none": "Not simulated.",
    "not_configured": ("Not simulated: no simulator is set up. Set the OPX host or the "
                       f"QM-SaaS account in {WHERE_SET}."),
    "no_env": "Not simulated: no Python environment is selected.",
    "busy": "A simulation is already running in this window. Stop it first.",
    "starting": "Starting the simulation.",
    "stopped": "Not simulated: stopped.",
}


def line(state: str, **kw) -> str:
    """The one user-facing sentence of a state."""
    if state == "not_run":
        return f"Not simulated: {kw.get('reason') or 'the gate could not be built'}."
    if state == "running":
        where = kw.get("where") or "the simulator"
        phase = kw.get("phase")
        return f"Simulating on {where}" + (f": {phase}." if phase else ".")
    if state == "done":
        return f"Simulated on {kw.get('where') or 'the simulator'}: {kw.get('sim_ns')} ns."
    if state == "failed":
        why = str(kw.get("reason") or "the simulator gave no answer").rstrip(".")
        return f"Not simulated: {why}."
    if state == "timeout":
        return f"Not simulated: the simulator gave no answer within {int(kw.get('limit_s') or 0)} s."
    if state == "too_long":
        return (f"Not simulated: the program is {kw.get('sim_ns')} ns long with the margin; "
                f"the simulation is limited to {MAX_SIM_NS // 1000} us.")
    return LINES.get(state, LINES["none"])


def answer(state: str, **kw) -> dict:
    out = {"state": state, "line": line(state, **kw)}
    out.update({k: v for k, v in kw.items() if k in ("id", "elapsed_s", "limit_s", "phase", "where")})
    return out


def target_of(settings: dict, creds: Optional[dict]) -> tuple[Optional[dict], str, float]:
    """``(target, label, limit_s)`` from the port test's saved settings and
    credentials; ``target`` None when nothing is set up. A saved OPX host is
    used only when the saved choice is the OPX."""
    local = {}
    opx = settings.get("opx") or {}
    if settings.get("target") == "opx" and str(opx.get("host") or "").strip():
        local = {"host": str(opx["host"]).strip(), "cluster": opx.get("cluster") or None,
                 "port": opx.get("port") or None}
    saas = dict(creds) if creds and creds.get("email") and creds.get("host") and creds.get("password") else {}
    if not local and not saas:
        return None, "", 0.0
    label = ("the QOP simulator at " + local["host"]) if local else "the QM-SaaS cloud simulator"
    limit = LIMIT_CLOUD_S if saas else LIMIT_LOCAL_S
    return {"local": local, "saas": saas}, label, limit


def _real(x: Any) -> bool:
    """A finite real number that is not a bool (numpy scalars included; an
    integer too large for a float is not one)."""
    if isinstance(x, bool) or not isinstance(x, numbers.Real):
        return False
    try:
        return math.isfinite(float(x))
    except (OverflowError, ValueError, TypeError):
        return False


def _whole(x: Any) -> bool:
    """A real number with an integral value that is not a bool (2 and 2.0 pass, 1.9 does not)."""
    try:
        return _real(x) and float(x) == int(x)
    except (OverflowError, ValueError, TypeError):
        return False


def _shape_ports(ports: Any) -> list:
    """Each port as the contract states it; a malformed entry is dropped,
    never padded. ``t0_ns`` and ``elements`` default only when missing or None;
    ``decimated`` and ``cut_at_end`` are set only by a stored ``true``."""
    out = []
    for p in ports if isinstance(ports, list) else []:
        if not isinstance(p, dict):
            continue
        raw_volts, raw_el, raw_t0 = p.get("volts"), p.get("elements"), p.get("t0_ns")
        raw_el = [] if raw_el is None else raw_el
        raw_t0 = 0.0 if raw_t0 is None else raw_t0
        if not isinstance(raw_volts, (list, tuple)) or not all(_real(v) for v in raw_volts):
            continue
        if not isinstance(raw_el, (list, tuple)) or not all(isinstance(e, str) for e in raw_el):
            continue
        if not _real(raw_t0):
            continue
        con, raw_fem, raw_port = p.get("controller"), p.get("fem"), p.get("port")
        if not (isinstance(con, str) and con) or not _whole(raw_fem) or not _whole(raw_port):
            continue
        fem, port = int(raw_fem), int(raw_port)
        volts = [float(v) for v in raw_volts]
        elements = list(raw_el)
        t0 = float(raw_t0)
        if not volts:
            continue
        try:
            dt = float(p.get("dt_ns"))
        except (TypeError, ValueError, OverflowError):
            dt = None
        if dt is not None and not (math.isfinite(dt) and dt > 0):
            dt = None
        out.append({"key": f"{con}/{fem}/{port}", "port_text": f"{con} / {fem} / {port}",
                    "controller": con, "fem": fem, "port": port,
                    "elements": elements,
                    "kind": p.get("kind"), "t0_ns": t0,
                    "dt_ns": dt, "time_step_unknown": dt is None, "volts": volts,
                    "decimated": p.get("decimated") is True,
                    "cut_at_end": p.get("cut_at_end") is True})
    return out


def from_status(st: dict, meta: dict) -> dict:
    """The page's answer from one :meth:`Runs.status` reading."""
    where = meta.get("where")
    limit = meta.get("limit_s")
    base = {"id": st.get("id"), "elapsed_s": st.get("elapsed_s"), "limit_s": limit, "where": where}
    if st.get("state") == "none":
        return answer("none")
    if st.get("state") == "running":
        if st.get("stopping"):
            return answer("running", phase="stopping", **base)
        phase = st.get("phase")
        if phase in (None, "connecting"):
            return answer("starting", **base)
        return answer("running", phase=phase, **base)
    res = st.get("result") or {}
    if res.get("timed_out"):
        return answer("timeout", **base)
    if res.get("stopped"):
        return answer("stopped", **base)
    if res.get("too_long"):
        return answer("too_long", sim_ns=res.get("sim_ns"), **base)
    if res.get("error_kind") in ("chip", "gate"):
        return answer("not_run", reason=_short(res.get("error")), **base)
    if not res.get("ok"):
        return answer("failed", reason=_short(res.get("error")), **base)
    ports = _shape_ports(res.get("ports"))
    if not ports:
        return answer("failed", reason="the simulator returned no output for the gate's ports", **base)
    out = answer("done", sim_ns=res.get("sim_ns"), where=res.get("backend_label") or where,
                 **{k: v for k, v in base.items() if k != "where"})
    out.update(ports=ports, sim_ns=res.get("sim_ns"), length_from=res.get("length_from"),
               cannot_draw=res.get("cannot_draw"),
               note="Simulated output after the port's filters; the time axis starts at the program start.")
    unknown = [p["port_text"] for p in ports if p["time_step_unknown"]]
    if unknown:
        out["time_step_note"] = ("The time step of these ports is not known (the simulator and the "
                                 "config give no sample rate), so they have no time axis: "
                                 + ", ".join(unknown) + ".")
    cut = [p["port_text"] for p in ports if p["cut_at_end"]]
    if cut:
        out["cut_note"] = ("The simulation window ended while these ports were still playing: "
                           + ", ".join(cut) + ".")
    return out


def _short(text: Any, n: int = 300) -> str:
    s = re.sub(r"\s+", " ", str(text or "")).strip()
    return s[:n]


_WINDOW = re.compile(r"[^A-Za-z0-9_-]")


def window_key(raw: Any) -> str:
    """A folder-safe window identity (unknown windows share ``default``)."""
    s = _WINDOW.sub("", str(raw or ""))[:40]
    return s or "default"


class SimRuns(ptr.Runs):
    """The port test's run lifecycle for gate simulations of one window,
    in its own folder."""

    def __init__(self, instance, window: str):
        self.window = window_key(window)
        self.copies: dict = {}
        super().__init__(instance, resume=False)

    def _runs_dir(self) -> Path:
        d = Path(self.instance) / "cz_gate_sim" / self.window
        d.mkdir(parents=True, exist_ok=True)
        return d

    def busy(self) -> bool:
        cur = self.current
        return bool(cur and cur["proc"].poll() is None)


class Manager:
    """All windows' :class:`SimRuns` plus the chip copies they run on."""

    def __init__(self, instance):
        self.instance = Path(instance)
        self._lock = threading.Lock()
        self.windows: dict[str, SimRuns] = {}

    def runs(self, window: Any) -> SimRuns:
        key = window_key(window)
        with self._lock:
            if key not in self.windows:
                self.windows[key] = SimRuns(self.instance, key)
            return self.windows[key]

    def start(self, window: Any, *, python: Optional[str], target: Optional[dict], label: str,
              limit_s: float, make_copy: Callable[[], Any], remove_copy: Callable[[Any], Any],
              macro: str, popen_for: Callable[[str], Callable]) -> tuple[dict, int]:
        """Start one run; ``(answer, http status)``. *make_copy* returns an
        object with ``after`` (the chip copy folder) and ``root_class``;
        *popen_for(folder)* the ``popen`` the runner is started with."""
        if not python:
            return answer("no_env"), 400
        if target is None:
            return answer("not_configured"), 400
        runs = self.runs(window)
        if runs.busy():
            return answer("busy"), 409
        copy = make_copy()
        job = {"folder": str(copy.after), "root_class": copy.root_class, "macro": macro,
               "max_ns": MAX_SIM_NS, "pad_ns": PAD_NS, "start_ns": START_LATENCY_NS, "limit_s": limit_s}
        meta = {"target": "saas" if target.get("saas") else "sim", "where": label,
                "limit_s": limit_s}
        try:
            started = runs.start(python, json.dumps(job), {"target": target}, meta,
                                 popen=popen_for(str(copy.after)), runner=RUNNER)
        except RuntimeError:
            remove_copy(copy)
            return answer("busy"), 409
        except Exception:
            remove_copy(copy)
            raise
        runs.copies[started["id"]] = copy
        threading.Thread(target=self._reap, args=(runs, started["id"], limit_s, remove_copy),
                         daemon=True).start()
        return answer("starting", id=started["id"], limit_s=limit_s, where=label), 200

    def _reap(self, runs: SimRuns, rid: str, limit_s: float, remove_copy, poll_s: float = 0.5):
        """Remove the run's chip copy once its runner ended; past the limit
        plus :data:`SM_SLACK_S` SM asks it to stop."""
        t0 = time.monotonic()
        cap = limit_s + SM_SLACK_S + ptr.SAAS_STOP_GRACE_S + 60
        run = runs.current
        while run is not None and run["id"] == rid and run["proc"].poll() is None:
            el = time.monotonic() - t0
            if el > limit_s + SM_SLACK_S and run["stop_at"] is None:
                runs._request_stop(run, "time limit")
            if el > cap:
                try:
                    run["proc"].kill()
                except Exception:  # noqa: BLE001
                    pass
                break
            time.sleep(poll_s)
        copy = runs.copies.pop(rid, None)
        if copy is not None:
            remove_copy(copy)

    def status(self, window: Any, rid: Optional[str]) -> dict:
        runs = self.runs(window)
        st = runs.status(rid or None)
        meta = (runs.current or {}).get("meta") or {}
        return from_status(st, meta)

    def stop(self, window: Any, rid: Optional[str]) -> dict:
        runs = self.runs(window)
        if not runs.stop(rid or None, "Stop pressed"):
            return self.status(window, rid)
        return answer("running", phase="stopping", id=(runs.current or {}).get("id"))


def lab_popen(env_extra: dict) -> Callable:
    """A ``popen`` for :meth:`Runs.start` that starts the lab env the way the
    gate check does (lab code folder on the path, owned process tree)."""
    from quam_state_manager.core import lab_worker

    def popen(argv, **kw):
        kw["env"] = dict(os.environ, PYTHONDONTWRITEBYTECODE="1", PYTHONUTF8="1", **env_extra)
        return lab_worker._spawn(argv, **kw)
    return popen
