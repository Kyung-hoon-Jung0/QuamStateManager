"""Fit-Auditor driver — gate-migration triage (docs/50).

Replays a saved run's OWN committed node-analysis over its frozen ``ds_raw.h5`` in
the user-selected QM env (``generator/run_fit_audit.py``) and compares the fresh
verdict to the stored claim, codifying each qubit as:

    = agrees   ⚠ reject (stored-T→fresh-F)   ↺ recover (stored-F→fresh-T)
    ~ drift (both-T, |Δvalue|>tol)            ? unverifiable

The verdict is always *"disagrees with gate <hash>"*, never *"this fit is bad"*: the
oracle is the node's own hardened gate (stamped ``gate_hash`` + ``lib_versions``),
and the applied number, if any, stays the node's own fitted value. The SM process
never imports the QM stack — the subprocess produces the ``fitaudit/v1`` JSON, this
module only reshapes + codifies it.
"""
from __future__ import annotations

import json
import logging
import re
import subprocess
import threading
from pathlib import Path

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Family registry (Phase-1: the two §6-③ pilots)
# ---------------------------------------------------------------------------
# normalized family -> util module, the FitParameters value field, drift tolerance.
FAMILIES: dict[str, dict] = {
    "qubit_spectroscopy": {
        "util": "qubit_spectroscopy", "value_field": "frequency",
        "value_tol": 1e6, "label": "Qubit spectroscopy (f_01)",
    },
    "resonator_spectroscopy_vs_power": {
        "util": "resonator_spectroscopy_vs_power_iq", "value_field": "resonator_frequency",
        "value_tol": 1e6, "label": "Resonator spectroscopy vs power",
    },
}
# calibration-file variants that share a util / family. The node names the real
# graphs ship carry suffixes the FAMILIES keys don't: qubit-spec's improved-v2 is
# ``*_qubit_spectroscopy_new`` and the resonator node is ``*_resonator_spectroscopy
# _vs_power_iq`` (util IS the _iq form). Without these, family_for() returns None and
# every such run silently drops from the backlog.
_ALIASES = {
    "qubit_spectroscopy_new": "qubit_spectroscopy",
    "resonator_spectroscopy_vs_power_iq": "resonator_spectroscopy_vs_power",
}


def _derive_family(node_name: str) -> str:
    """Node name -> normalized family key (strip graph + node prefixes; de-alias)."""
    s = node_name or ""
    s = re.sub(r"^[0-9]+[A-Za-z]?Q_", "", s)   # graph prefix 1Q_/2Q_
    s = re.sub(r"^[0-9]+[a-z]?_", "", s)        # node prefix 05b_/08_
    s = s.strip()
    return _ALIASES.get(s, s)


def family_for(node_name: str) -> str | None:
    """The auditable family for a node name, or ``None`` if not in the registry."""
    fam = _derive_family(node_name)
    return fam if fam in FAMILIES else None


# ---------------------------------------------------------------------------
# source-root setting (which hardened analysis tree the audit replays against)
# ---------------------------------------------------------------------------

def get_audit_source_root(instance_path) -> str | None:
    """The configured ``calibration_utils``/``quam_config`` tree to audit against.

    Distinct from interactive-replot (which uses the env's *installed* analysis):
    the auditor points at a specific hardened tree so the verdict is anchored to a
    known gate, and ``gate_hash`` stamps exactly which. ``None`` = rely on the env
    install.
    """
    from quam_state_manager.core.config_generator import _settings_path
    path = _settings_path(instance_path)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return None
    return data.get("fit_audit_source_root") or None


def set_audit_source_root(instance_path, source_root: str) -> None:
    """Persist the audit source-root under ``instance/`` (merged into the settings)."""
    from quam_state_manager.core import safe_io
    from quam_state_manager.core.config_generator import _settings_path
    path = _settings_path(instance_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        data = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    except (ValueError, OSError):
        data = {}
    data["fit_audit_source_root"] = source_root or ""
    # Atomic, never a plain write_text: the settings file is a SHARED
    # read-modify-write target (selected_env_python lives here too) — a
    # torn/partial write would silently drop the other keys.
    safe_io.atomic_write_json(path, data)


def validate_source_root(source_root: str | None) -> tuple[bool, str]:
    """(ok, message) for a candidate audit source-root, so a typo/blank can't
    silently anchor verdicts to the wrong (or absent) gate.

    Blank = rely on the env's installed analysis (allowed). Non-blank must be an
    existing directory holding a ``calibration_utils/`` subtree (where the gate
    lives). Does not claim a gate hash — that's computed per-run at replay.
    """
    import os
    sr = (source_root or "").strip()
    if not sr:
        return True, "using the env's installed analysis (no source root set)"
    if not os.path.isdir(sr):
        return False, f"path not found: {sr}"
    if not os.path.isdir(os.path.join(sr, "calibration_utils")):
        return False, "no calibration_utils/ here — point at the …/superconducting folder"
    return True, f"found calibration_utils/ at {sr}"


# ---------------------------------------------------------------------------
# stored claim (what was written at acquisition time)
# ---------------------------------------------------------------------------

def _read_fit_results(folder: Path) -> dict:
    from quam_state_manager.core import safe_io
    p = folder / "data.json"
    try:
        # safe_io (not read_text): the fit-audit popup is most-used on the NEWEST
        # run — the one whose writer may still be doing its fit-result writeback.
        # A plain read lacks FILE_SHARE_DELETE (blocks the writer on Windows) and
        # has no mid-write retry (a torn read would silently drop the fit results).
        j = safe_io.read_json(p)
    except (OSError, ValueError):
        return {}
    fr = j.get("fit_results")
    if isinstance(fr, dict):
        return fr
    # some nodes nest it; shallow deep-find as a fallback
    for v in j.values() if isinstance(j, dict) else []:
        if isinstance(v, dict) and isinstance(v.get("fit_results"), dict):
            return v["fit_results"]
    return {}


def _stored_success(v: dict):
    """Normalize the 3 stored success shapes to a bool (or None if absent)."""
    if not isinstance(v, dict):
        return None
    if "success" in v:
        return bool(v["success"])
    oc = v.get("outcome") or v.get("status")
    if isinstance(oc, str):
        return oc.strip().lower() in ("successful", "success", "pass", "passed", "ok")
    return None


def stored_claim(folder: Path, value_field: str) -> dict:
    """``{qubit: (stored_success: bool|None, stored_value: float|None)}`` from data.json."""
    out = {}
    for q, v in _read_fit_results(folder).items():
        if not isinstance(v, dict):
            continue
        val = v.get(value_field)
        out[q] = (_stored_success(v),
                  float(val) if isinstance(val, (int, float)) else None)
    return out


# ---------------------------------------------------------------------------
# verdict codifier
# ---------------------------------------------------------------------------

VERDICTS = ("agrees", "reject", "recover", "drift", "unverifiable")


def _codify(stored_succ, stored_val, fresh, value_field, tol):
    """One (verdict, detail) for a qubit from its stored claim + fresh replay dict."""
    if fresh is None:
        return "unverifiable", "no fresh result for this qubit"
    if not fresh.get("deterministic", True):
        return "unverifiable", "refit non-deterministic (two replays disagree)"
    if stored_succ is None:
        return "unverifiable", "no stored success claim"
    fresh_succ = bool(fresh.get("success"))
    fresh_val = fresh.get(value_field)
    if stored_succ and not fresh_succ:
        return "reject", "the current gate rejects a fit the original node accepted"
    if (not stored_succ) and fresh_succ:
        return "recover", "the current gate accepts a fit the original node discarded"
    if stored_succ and fresh_succ:
        if isinstance(stored_val, (int, float)) and isinstance(fresh_val, (int, float)):
            d = fresh_val - stored_val
            if abs(d) > tol:
                from quam_state_manager.core import units
                base = units.stored_unit_label(value_field) or "Hz"
                sign = "+" if d > 0 else ""   # format_metric drops the sign; direction matters
                return "drift", (f"value differs by {sign}{units.format_metric(d, base)} "
                                 f"(tolerance {units.format_metric(tol, base)})")
        return "agrees", ""
    return "agrees", "both reject"


# ---------------------------------------------------------------------------
# subprocess driver (WSL<->Windows path aware; stdout-captured)
# ---------------------------------------------------------------------------

def _script_path() -> Path:
    from quam_state_manager.core.config_generator import _script_path as sp
    return sp("run_fit_audit.py")


def _is_windows_interp(env: str) -> bool:
    return bool(env) and env.strip().lower().endswith(".exe")


def _to_win(p: str) -> str:
    """``/mnt/d/x/y`` -> ``D:\\x\\y`` (only for a Windows interpreter)."""
    m = re.match(r"^/mnt/([a-zA-Z])/(.*)$", p or "")
    if not m:
        return p
    return f"{m.group(1).upper()}:\\" + m.group(2).replace("/", "\\")


def _pth(env: str, p: str) -> str:
    return _to_win(p) if _is_windows_interp(env) else p


def _run_engine(env, folder, util, source_root, timeout=180) -> dict:
    """Spawn run_fit_audit.py; return the parsed ``fitaudit/v1`` envelope."""
    script = str(_script_path())
    args = [env, _pth(env, script), "--run", _pth(env, str(folder)), "--util", util]
    if source_root:
        args += ["--source-root", _pth(env, str(source_root))]
    try:
        # errors='replace': a Windows QM child can emit non-UTF-8 bytes (cp1252
        # µ/±/π in logs); strict decoding would raise and downgrade a good run.
        proc = subprocess.run(args, capture_output=True, text=True,
                              encoding="utf-8", errors="replace", timeout=timeout)
    except subprocess.TimeoutExpired:
        return {"qubits": {}, "errors": [{"stage": "timeout", "trace": f"exceeded {timeout}s"}]}
    except OSError as e:
        return {"qubits": {}, "errors": [{"stage": "spawn", "trace": str(e)}]}
    # the engine prints one JSON line to stdout; qm logs go to stderr
    for line in reversed((proc.stdout or "").splitlines()):
        line = line.strip()
        if line.startswith("{"):
            try:
                return json.loads(line)
            except ValueError:
                break
    return {"qubits": {}, "errors": [{"stage": "subprocess",
            "trace": (proc.stderr or proc.stdout or "no output")[-2000:]}]}


# ---------------------------------------------------------------------------
# single-run audit
# ---------------------------------------------------------------------------

def audit_capability(node_name, folder, instance_path) -> dict:
    """Cheap no-subprocess check: can this run be audited?"""
    from quam_state_manager.core import config_generator
    fam = family_for(node_name or "")
    env = config_generator.get_selected_env(instance_path)
    folder = Path(folder or "")
    if fam is None:
        return {"available": False, "reason": "Node family is not in the Phase-1 "
                "audit registry.", "family": None, "env": env}
    if not env:
        return {"available": False, "reason": "No QM environment selected "
                "(set one in Generate Config).", "family": fam, "env": None}
    if not (folder / "ds_raw.h5").exists():
        return {"available": False, "reason": "Run has no ds_raw.h5 to re-analyze.",
                "family": fam, "env": env}
    if not (folder / "quam_state").is_dir():
        return {"available": False, "reason": "Run has no quam_state/ snapshot.",
                "family": fam, "env": env}
    return {"available": True, "reason": "", "family": fam, "env": env}


def audit_run(node_name, folder, env, source_root, *, timeout=180) -> dict:
    """Audit one run. Returns a dict with ``family``, ``gate_hash``, ``lib_versions``,
    per-qubit ``rows`` (stored/fresh/verdict), and any engine ``errors``.
    """
    folder = Path(folder)
    fam = family_for(node_name or "")
    if fam is None:
        return {"auditable": False, "reason": "family not registered",
                "family": None, "rows": [], "counts": {}}
    spec = FAMILIES[fam]
    stored = stored_claim(folder, spec["value_field"])
    env_json = _run_engine(env, folder, spec["util"], source_root, timeout=timeout)
    fresh_q = env_json.get("qubits") or {}
    errors = env_json.get("errors") or []
    # A run whose real preprocessing failed can't be trusted per-qubit.
    preproc_bad = bool(env_json.get("has_process_fn")) and not env_json.get("preprocessing_ok")

    rows = []
    # union of stored + fresh qubits, so a qubit present in only one side still shows
    qubits = list(stored.keys()) or list(fresh_q.keys())
    for q in qubits:
        ssucc, sval = stored.get(q, (None, None))
        fresh = fresh_q.get(q)
        if preproc_bad:
            verdict, detail = "unverifiable", "node preprocessing failed in replay"
        elif errors and not fresh_q:
            verdict, detail = "unverifiable", f"replay error: {errors[0].get('stage')}"
        else:
            verdict, detail = _codify(ssucc, sval, fresh, spec["value_field"], spec["value_tol"])
        rows.append({
            "qubit": q,
            "stored_success": ssucc,
            "stored_value": sval,
            "fresh_success": (bool(fresh.get("success")) if fresh else None),
            "fresh_value": (fresh.get(spec["value_field"]) if fresh else None),
            "deterministic": (fresh.get("deterministic") if fresh else None),
            "verdict": verdict,
            "detail": detail,
        })
    counts = {v: sum(1 for r in rows if r["verdict"] == v) for v in VERDICTS}
    return {
        "auditable": True, "family": fam, "family_label": spec["label"],
        "gate_hash": env_json.get("gate_hash"),
        "lib_versions": env_json.get("lib_versions") or {},
        "value_field": spec["value_field"], "value_tol": spec["value_tol"],
        "rows": rows, "counts": counts, "errors": errors,
    }


# ---------------------------------------------------------------------------
# single-run verdict cache (shared by the apply-popup badge + the sweep)
# ---------------------------------------------------------------------------
# key "<folder>|<source_root>" -> {"fp": mtime/size stamp, "result": audit_run dict}.
# Bounded; in-flight coalesced so the popup's N concurrent opens + the sweep's pass
# over the same run collapse onto ONE subprocess (mirrors interactive-replot).
_VERDICT_CACHE: dict[str, dict] = {}
_VERDICT_CACHE_MAX = 64
_VERDICT_LOCK = threading.Lock()
_VERDICT_INFLIGHT: dict[str, threading.Event] = {}


def _run_fingerprint(folder: Path) -> str:
    """Cheap content stamp of the run inputs an audit depends on: the cube
    (ds_raw.h5), the params (node.json), AND the stored claim (data.json's
    fit_results). Analysis-code edits are caught by the env key + gate_hash."""
    parts = []
    for name in ("node.json", "ds_raw.h5", "data.json"):
        try:
            st = (folder / name).stat()
            # st_mtime_ns, not int(st_mtime): second-truncation made a
            # same-second same-size rewrite invisible → stale cached verdict.
            parts.append(f"{name}:{st.st_mtime_ns}:{st.st_size}")
        except OSError:
            parts.append(f"{name}:-")
    return "|".join(parts)


def audit_run_cached(node_name, folder, env, source_root, *, force=False, timeout=180) -> dict:
    """``audit_run`` with a fingerprint cache + in-flight coalescing.

    Repeated apply-popup opens for the same run (and the sweep's pass over it) reuse
    one subprocess result; a data change (new fingerprint) or ``force`` re-runs.
    """
    folder = Path(folder)
    # env is IN the key: with a blank source_root (the default) the gate is the
    # env's *installed* analysis, so a different env is a different gate — a shared
    # key would serve a stale wrong-gate verdict after an env switch.
    key = f"{folder}|{source_root or ''}|{env or ''}"
    fp = _run_fingerprint(folder)
    while True:
        with _VERDICT_LOCK:
            hit = _VERDICT_CACHE.get(key)
            if not force and hit and hit["fp"] == fp:
                return hit["result"]
            ev = _VERDICT_INFLIGHT.get(key)
            if ev is None:
                ev = _VERDICT_INFLIGHT[key] = threading.Event()
                break
            force = False   # a waiter never forces a second run
        ev.wait(timeout=timeout + 30)
        with _VERDICT_LOCK:
            hit = _VERDICT_CACHE.get(key)
            if hit and hit["fp"] == fp:
                return hit["result"]
            if _VERDICT_INFLIGHT.get(key) is None:
                continue   # producer finished without a usable entry — retry as owner
        ev.wait(timeout=5)

    result = None
    try:
        result = audit_run(node_name, folder, env, source_root, timeout=timeout)
        return result
    finally:
        with _VERDICT_LOCK:
            # Cache only usable results; a transient error stays retryable.
            if result and result.get("auditable") and not result.get("errors"):
                _VERDICT_CACHE[key] = {"fp": fp, "result": result}
                while len(_VERDICT_CACHE) > _VERDICT_CACHE_MAX:
                    _VERDICT_CACHE.pop(next(iter(_VERDICT_CACHE)), None)
            _VERDICT_INFLIGHT.pop(key, None)
        ev.set()


def cached_result(folder, source_root, env) -> dict | None:
    """The already-computed audit for a run, or ``None`` (no subprocess) — so the
    apply-popup can show a verdict instantly when warm and offer an opt-in check
    when cold, instead of blocking every popup open on a fresh replay. Must key on
    ``env`` too (see audit_run_cached) so a warm peek can't cross envs."""
    folder = Path(folder)
    key = f"{folder}|{source_root or ''}|{env or ''}"
    fp = _run_fingerprint(folder)
    with _VERDICT_LOCK:
        hit = _VERDICT_CACHE.get(key)
        return hit["result"] if hit and hit["fp"] == fp else None


# ---------------------------------------------------------------------------
# backlog sweep (background job feeding the /fit-audit digest)
# ---------------------------------------------------------------------------

class SweepJob:
    """A running/finished backlog sweep. Thread-safe snapshot via :meth:`snapshot`."""

    def __init__(self, total: int, env: str, source_root: str | None):
        self.total = total
        self.done = 0
        self.status = "running"      # running | done | error | cancelled
        self.error: str | None = None
        self.env = env
        self.source_root = source_root
        self.runs: list[dict] = []   # per-run audit summaries (one per auditable run)
        self._cancel = threading.Event()
        self._lock = threading.Lock()

    def add(self, run_summary: dict):
        with self._lock:
            self.runs.append(run_summary)
            self.done += 1

    def cancel(self):
        """Request stop; the worker breaks after the current run (not mid-subprocess)."""
        self._cancel.set()

    @property
    def cancelled(self) -> bool:
        return self._cancel.is_set()

    def finish(self, error: str | None = None):
        with self._lock:
            if error:
                self.status, self.error = "error", error
            elif self._cancel.is_set():
                self.status = "cancelled"
            else:
                self.status = "done"

    def snapshot(self) -> dict:
        with self._lock:
            return {
                "total": self.total, "done": self.done, "status": self.status,
                "error": self.error, "env": self.env, "source_root": self.source_root,
                "runs": list(self.runs), "digest": _digest(self.runs),
            }


# job_key -> SweepJob (one active per context; a new sweep replaces the old).
_SWEEPS: dict[str, SweepJob] = {}
_SWEEP_LOCK = threading.Lock()


def _digest(run_summaries: list[dict]) -> dict:
    """Aggregate per-family confusion counts + the flagged (non-agree) rows."""
    fams: dict[str, dict] = {}
    flagged: list[dict] = []
    for rs in run_summaries:
        fam = rs.get("family")
        if not fam:
            continue
        f = fams.setdefault(fam, {"label": rs.get("family_label", fam),
                                  "counts": {v: 0 for v in VERDICTS},
                                  "gate_hashes": set(), "runs": 0})
        f["runs"] += 1
        if rs.get("gate_hash"):
            f["gate_hashes"].add(rs["gate_hash"][:12])
        for v, n in (rs.get("counts") or {}).items():
            f["counts"][v] = f["counts"].get(v, 0) + n
        for r in rs.get("rows", []):
            if r["verdict"] in ("reject", "recover", "drift"):
                flagged.append({**r, "run": rs.get("run"), "uid": rs.get("uid"),
                                "family": fam})
    # sets aren't JSON-able
    for f in fams.values():
        f["gate_hashes"] = sorted(f["gate_hashes"])
    order = {"reject": 0, "drift": 1, "recover": 2}
    flagged.sort(key=lambda r: order.get(r["verdict"], 9))
    return {"families": fams, "flagged": flagged}


def _sweep_worker(job: SweepJob, targets: list[dict], env: str, source_root: str | None):
    try:
        for t in targets:
            if job._cancel.is_set():   # stop after the current run (checked between runs)
                break
            try:
                # cached so the digest + apply-popup badge share one subprocess per run
                res = audit_run_cached(t["node_name"], t["folder"], env, source_root)
            except Exception as e:   # one bad run must not kill the sweep
                logger.exception("fit-audit run failed: %s", t.get("folder"))
                res = {"auditable": True, "family": family_for(t["node_name"]),
                       "rows": [], "counts": {v: 0 for v in VERDICTS},
                       "errors": [{"stage": "driver", "trace": str(e)}]}
            job.add({**res, "run": t.get("run"), "uid": t.get("uid")})
        job.finish()
    except Exception as e:
        logger.exception("fit-audit sweep crashed")
        job.finish(error=str(e))


def start_sweep(job_key: str, targets: list[dict], env: str,
                source_root: str | None) -> SweepJob:
    """Start a background sweep over ``targets`` (``{folder, node_name, run, uid}``).

    Coalesces: a still-running job for the same key is returned unchanged rather than
    replaced, so a double-click / second browser tab can't spawn a second heavy
    subprocess chain against an orphaned job.
    """
    with _SWEEP_LOCK:
        existing = _SWEEPS.get(job_key)
        if existing is not None and existing.status == "running":
            return existing
        job = SweepJob(total=len(targets), env=env, source_root=source_root)
        _SWEEPS[job_key] = job
    try:
        threading.Thread(target=_sweep_worker, args=(job, targets, env, source_root),
                         daemon=True).start()
    except RuntimeError as e:   # thread exhaustion — don't pin the job at 'running' forever
        job.finish(error=f"could not start sweep thread: {e}")
    return job


def get_sweep(job_key: str) -> SweepJob | None:
    with _SWEEP_LOCK:
        return _SWEEPS.get(job_key)
