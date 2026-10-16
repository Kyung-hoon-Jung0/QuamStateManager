"""Measured population loss of the open chip's qubits, for the CZ gate plan.

Read-only and file-derived: every curve comes from a run of the lab's
population-loss family (node name containing ``population_loss``) in one of
the chip's own data folders, never from SM's ledger, so the curves draw while
the ledger is still being built. The family is the measurement behind each
qubit's column of [paper: arXiv:2112.03708v1, p.11, Fig. 6c] "Measured
population loss (gray areas)" ... "for a duration of 100 ns" -- the duration is
a property of each run here (``interaction_time_ns``), so every curve carries it.

What a run holds (the lab's own analysis writes it): ``ds_fit.h5`` with
``p_loss`` and the coordinates ``frequency`` (absolute Hz, placed by the lab
at RUN time through its own flux conversion; NaN where the lab had no axis),
``detuning`` (Hz from the operating point), ``p_loss_stderr`` (optional) and
``flux_conversion``; ``data.json`` ``fit_results[q].defects``; ``node.json``
``data.parameters.model.interaction_time_ns``.

Two facts about the frequency axis shape everything below:

* it is not monotonic: a sweep can start below idle, jump past it and climb
  (one run measured: 13 samples down to -38.6 MHz, then +38.9 MHz upward). A
  curve is therefore split into PIECES (:func:`split_pieces`) and a piece is
  never joined to another;
* the stored defect list (``extras.population_loss_defects_json``) names no
  run, and the node writes no patches, so which run wrote it is decided by
  CONTENT (:func:`defects_match`), not by the ledger.
"""
from __future__ import annotations

import io
import json
import math
import os
import threading
from collections import OrderedDict
from pathlib import Path
from typing import Any, Callable

import numpy as np

from quam_state_manager.core import run_time, safe_io, value_history
from quam_state_manager.core.hub_build import _DAY, _RUN

#: substring of the node name that selects the family
FAMILY = "population_loss"
#: newest candidate runs read per request
MAX_RUNS = 60
#: points kept per piece after peak-preserving decimation
MAX_POINTS = 400
#: a step larger than this many median steps starts a new piece [derived]
JUMP_FACTOR = 3.0
#: two bracketing samples further apart than this many median steps are a gap [derived]
GAP_STEPS = 2.0
#: |idle at run - f_01 today| above this is reported
IDLE_TOLERANCE_HZ = 1e6
#: a stored defect frequency and a run's equal within this
MATCH_TOLERANCE_HZ = 1.0
#: rounding of what is sent to the browser
FREQ_STEP_HZ = 1e3
LOSS_DECIMALS = 4
#: parsed runs kept
CACHE_MAX = 256

NO_ROOTS = "no data folder is declared for this chip"
NOT_MEASURED = "not measured at this frequency"
AXIS_NOTE = ("the stored defects sit on the frequency axis the lab placed at run time; the gate's"
             " trajectory is on today's flux conversion")


def _num(v: Any) -> float | None:
    if isinstance(v, bool):
        return None
    try:
        f = float(v)
    except (TypeError, ValueError, OverflowError):
        return None
    return f if math.isfinite(f) else None


def _norm(p: Any) -> str:
    return os.path.normcase(os.path.normpath(str(p)))


# ---------------------------------------------------------------- pieces
def median_step(freq) -> float:
    """Median |step| of a frequency sequence over its NON-zero steps (a quad axis
    that saturates repeats a frequency; those zero steps say nothing about the
    sampling). 0.0 when there is no non-zero step. [derived]"""
    f = np.asarray(freq, dtype=float)
    if f.size < 2:
        return 0.0
    mag = np.abs(np.diff(f))
    mag = mag[np.isfinite(mag) & (mag > 0)]
    return float(np.median(mag)) if mag.size else 0.0


def split_pieces(freq) -> list[tuple[int, int]]:
    """``[(start, stop), ...]`` slices of *freq* (finite, in sample order) that are
    each one monotonic sweep: a new piece starts after a direction change (a zero
    step is NOT a direction change) and across a step larger than
    :data:`JUMP_FACTOR` x the median non-zero step. [derived] -- the rule was
    checked on 45 real runs (48 curves -> 52 pieces; only the one qubit whose
    sweep starts below idle splits, at its 78 MHz jump)."""
    f = np.asarray(freq, dtype=float)
    n = f.size
    if n == 0:
        return []
    steps = np.diff(f)
    med = median_step(f)
    bounds, prev = [0], 0
    for i, s in enumerate(steps):
        sign = int(s > 0) - int(s < 0)
        jump = med > 0 and abs(s) > JUMP_FACTOR * med
        turn = sign != 0 and prev != 0 and sign != prev
        if jump or turn:
            bounds.append(i + 1)
            prev = 0
        elif sign != 0:
            prev = sign
    bounds.append(n)
    return [(a, b) for a, b in zip(bounds[:-1], bounds[1:]) if b > a]


def decimate(loss, max_points: int = MAX_POINTS) -> np.ndarray:
    """Indices kept from one piece: all of them up to *max_points*, else the first
    and last sample plus the lowest and highest loss of each of
    ``(max_points - 2) // 2`` equal index buckets -- the peak always survives."""
    v = np.asarray(loss, dtype=float)
    n = v.size
    if n <= max_points:
        return np.arange(n)
    nb = max(1, (max_points - 2) // 2)
    edges = np.linspace(0, n, nb + 1).astype(int)
    keep = {0, n - 1}
    for a, b in zip(edges[:-1], edges[1:]):
        if b > a:
            seg = v[a:b]
            keep.add(a + int(np.argmax(seg)))
            keep.add(a + int(np.argmin(seg)))
    return np.array(sorted(keep))


def _rounded(arr, step=None, decimals=None) -> list:
    out = []
    for x in arr:
        x = float(x)
        if not math.isfinite(x):
            out.append(None)
        elif step:
            out.append(float(round(x / step) * step))
        else:
            out.append(round(x, decimals))
    return out


# ---------------------------------------------------------------- [derived] helpers
def loss_at(pieces: list[dict], f_hz: float) -> dict:
    """The scan's loss at frequency *f_hz*. [derived] -- linear interpolation of a
    measured trace, used only between the two bracketing samples of ONE piece
    and only when they are at most :data:`GAP_STEPS` median steps apart (the
    median over every piece's non-zero steps); elsewhere the answer is
    ``None`` ("not measured at this frequency"). Every piece whose span holds
    *f_hz* is reported; ``loss`` is the largest of their interpolated values
    (the conservative reading when the axis folds back over itself)."""
    f = _num(f_hz)
    out = {"loss": None, "pieces": [], "reason": NOT_MEASURED}
    if f is None:
        return out
    freqs = [np.asarray([x if x is not None else np.nan for x in p.get("frequency_hz") or ()], dtype=float)
             for p in pieces]
    steps = np.concatenate([np.abs(np.diff(x)) for x in freqs if x.size > 1] or [np.zeros(0)])
    steps = steps[np.isfinite(steps) & (steps > 0)]
    med = float(np.median(steps)) if steps.size else 0.0
    for k, (p, x) in enumerate(zip(pieces, freqs)):
        y = np.asarray([v if v is not None else np.nan for v in p.get("loss") or ()], dtype=float)
        ok = np.isfinite(x) & np.isfinite(y)
        if not ok.any() or not (np.nanmin(x[ok]) <= f <= np.nanmax(x[ok])):
            continue
        value, reason = None, NOT_MEASURED
        hit = np.flatnonzero(ok & (x == f))
        if hit.size:
            value = float(np.max(y[hit]))
            reason = None
        else:
            for i in range(x.size - 1):
                a, b = x[i], x[i + 1]
                if not (ok[i] and ok[i + 1]) or not (min(a, b) < f < max(a, b)):
                    continue
                if med > 0 and abs(b - a) <= GAP_STEPS * med:
                    v = float(y[i] + (y[i + 1] - y[i]) * (f - a) / (b - a))
                    value = v if value is None else max(value, v)
                    reason = None
                else:
                    reason = f"{NOT_MEASURED} (a {abs(b - a) / 1e6:.1f} MHz gap between samples)"
        out["pieces"].append({"piece": k, "loss": value, "reason": reason})
    found = [p["loss"] for p in out["pieces"] if p["loss"] is not None]
    if found:
        out["loss"], out["reason"] = max(found), None
    elif out["pieces"]:
        out["reason"] = next((p["reason"] for p in out["pieces"] if p["reason"]), NOT_MEASURED)
    else:
        # outside every piece: say what the scan does cover, so "not measured" is not read as a failure
        spans = [(float(np.nanmin(x)), float(np.nanmax(x))) for x in freqs if np.isfinite(x).any()]
        if spans:
            out["reason"] = "%s (the scan covers %s GHz)" % (
                NOT_MEASURED, ", ".join("%.3f-%.3f" % (a / 1e9, b / 1e9) for a, b in sorted(spans)))
    return out


def defects_swept(defects: list[dict], f_min: float, f_max: float) -> list[dict]:
    """The defects a frequency excursion over ``[f_min, f_max]`` touches. [derived]
    -- a defect counts when its centre lies within half its FWHM of the
    interval (``|f_d - [f_min, f_max]| <= FWHM / 2``; no FWHM: inside only).
    ``inside`` says whether the centre itself was crossed or reached."""
    lo, hi = _num(f_min), _num(f_max)
    if lo is None or hi is None:
        return []
    lo, hi = min(lo, hi), max(lo, hi)
    out = []
    for d in defects or ():
        if not isinstance(d, dict):
            continue
        fd = _num(d.get("frequency_hz"))
        if fd is None:
            continue
        half = (_num(d.get("fwhm_hz")) or 0.0) / 2
        dist = 0.0 if lo <= fd <= hi else min(abs(fd - lo), abs(fd - hi))
        if dist <= half:
            out.append({"frequency_hz": fd, "fwhm_hz": _num(d.get("fwhm_hz")),
                        "peak_loss": _num(d.get("peak_loss")), "strong": bool(d.get("strong")),
                        "inside": dist == 0.0, "distance_hz": dist})
    return out


# ---------------------------------------------------------------- stored defects
def stored_frequencies(raw: Any) -> list | None:
    """The frequency list of a stored ``population_loss_defects_json`` (a JSON
    string or an already-parsed list): sorted numbers, ``None`` entries for a
    defect whose frequency is not finite; ``None`` when unreadable."""
    try:
        rows = json.loads(raw) if isinstance(raw, str) else raw
    except (TypeError, ValueError):
        return None
    if not isinstance(rows, list):
        return None
    return _sorted_freqs(rows)


def stored_defects(raw: Any) -> list[dict]:
    """The rows of a stored ``population_loss_defects_json`` (JSON text or a list);
    ``[]`` when absent or unreadable."""
    try:
        rows = json.loads(raw) if isinstance(raw, str) else raw
    except (TypeError, ValueError):
        return []
    return [r for r in rows if isinstance(r, dict)] if isinstance(rows, list) else []


def _sorted_freqs(rows) -> list:
    vals = [_num(r.get("frequency_hz")) if isinstance(r, dict) else None for r in rows]
    return sorted(vals, key=lambda v: (v is not None, v if v is not None else 0.0))


def defects_match(stored: list, measured: list, tol_hz: float = MATCH_TOLERANCE_HZ) -> bool:
    """Two frequency lists (as :func:`stored_frequencies` sorts them) name the same
    defects: equal length, order-free, each within *tol_hz* (non-finite only
    matches non-finite)."""
    if stored is None or measured is None or len(stored) != len(measured):
        return False
    for a, b in zip(stored, measured):
        if (a is None) != (b is None):
            return False
        if a is not None and abs(a - b) > tol_hz:
            return False
    return True


# ---------------------------------------------------------------- listing
_LISTINGS: dict[str, dict] = {}
_LIST_LOCK = threading.Lock()


def _day_runs(day_path: str) -> list[tuple[int, str]]:
    out = []
    with os.scandir(day_path) as it:
        for e in it:
            m = _RUN.fullmatch(e.name)
            if m and FAMILY in m[2] and e.is_dir():
                out.append((int(m[1]), e.name))
    return out


def candidates(roots) -> list[dict]:
    """Every run folder of the family under *roots* (data folders holding day
    folders), newest first by (day, run id); a folder reached through two roots
    is listed once. Cached per root as ``{day: (dir mtime_ns, runs)}``: a warm
    listing costs one directory read per root and one stat per day."""
    seen, found = set(), []
    for root in roots:
        key = _norm(root)
        try:
            with os.scandir(root) as it:
                days = [e for e in it if _DAY.fullmatch(e.name) and e.is_dir()]
        except OSError:
            continue
        with _LIST_LOCK:
            memo = _LISTINGS.get(key) or {}
        fresh = {}
        for d in days:
            try:
                mtime = d.stat().st_mtime_ns
            except OSError:
                continue
            hit = memo.get(d.name)
            if hit is not None and hit[0] == mtime:
                runs = hit[1]
            else:
                try:
                    runs = _day_runs(d.path)
                except OSError:
                    continue
            fresh[d.name] = (mtime, runs)
            for rid, name in runs:
                folder = os.path.join(d.path, name)
                nk = _norm(folder)
                if nk in seen:
                    continue
                seen.add(nk)
                found.append({"root": str(root), "day": d.name, "run_id": rid, "name": name, "folder": folder})
        with _LIST_LOCK:
            if len(_LISTINGS) > 32:
                _LISTINGS.clear()
            _LISTINGS[key] = fresh
    found.sort(key=lambda c: (c["day"], c["run_id"], c["name"]), reverse=True)
    return found


# ---------------------------------------------------------------- one run
_RUNS: "OrderedDict[tuple, dict]" = OrderedDict()
_RUN_LOCK = threading.Lock()
_NAMES: "OrderedDict[tuple, str | None]" = OrderedDict()


def _sig(p: Path):
    try:
        st = os.stat(p)
    except OSError:
        return None
    return (st.st_size, st.st_mtime_ns)


def _text(v: Any) -> str:
    if isinstance(v, bytes):
        return v.decode("utf-8", "replace")
    if isinstance(v, np.ndarray) and v.shape == ():
        return _text(v[()])
    return str(v)


def _fail(reason: str) -> dict:
    return {"ok": False, "reason": reason}


def _read_h5(raw: bytes) -> dict:
    import h5py
    with h5py.File(io.BytesIO(raw), "r") as f:
        if "p_loss" not in f or "qubit" not in f:
            raise ValueError("no p_loss for any qubit")
        names = [_text(x) for x in np.atleast_1d(f["qubit"][()])]
        nq = len(names)

        def grid(key, required=True):
            if key not in f:
                if required:
                    raise ValueError(f"no {key} coordinate")
                return None
            a = np.asarray(f[key][()], dtype=float)
            if a.ndim == 1:                         # a legacy shared axis
                a = np.broadcast_to(a, (nq, a.size))
            elif a.ndim == 2 and a.shape[0] != nq and a.shape[1] == nq:
                a = a.T
            if a.ndim != 2 or a.shape[0] != nq:
                raise ValueError(f"{key} has an unexpected shape {a.shape}")
            return a
        loss = grid("p_loss")
        freq = grid("frequency")
        det = grid("detuning", required=False)
        err = grid("p_loss_stderr", required=False)
        conv = ([_text(x) for x in np.atleast_1d(f["flux_conversion"][()])]
                if "flux_conversion" in f else [None] * nq)
    return {"names": names, "loss": loss, "freq": freq, "det": det, "err": err, "conv": conv}


def _qubit_curve(i: int, h5: dict) -> dict:
    """One qubit's trace, split and decimated (the cached, browser-ready form)."""
    f, y = h5["freq"][i], h5["loss"][i]
    n = min(f.size, y.size)
    f, y = f[:n], y[:n]
    det = h5["det"][i][:n] if h5["det"] is not None else None
    err = h5["err"][i][:n] if h5["err"] is not None else None
    idle = None
    if det is not None:
        zero = np.flatnonzero(np.isfinite(det) & (np.abs(det) < 0.5) & np.isfinite(f))
        idle = float(f[zero[0]]) if zero.size else None
    ok = np.isfinite(f) & np.isfinite(y)
    out = {"n_samples": int(n), "dropped": int(n - ok.sum()), "idle_at_run_hz": idle,
           "flux_conversion": h5["conv"][i], "pieces": [], "median_step_hz": None,
           "loss_min": None, "loss_max": None, "range_hz": None}
    if not ok.any():
        return out
    idx = np.flatnonzero(ok)
    fv, yv = f[idx], y[idx]
    out.update(median_step_hz=median_step(fv), loss_min=float(yv.min()), loss_max=float(yv.max()),
               range_hz=[float(fv.min()), float(fv.max())])
    for a, b in split_pieces(fv):
        keep = idx[a:b][decimate(yv[a:b])]
        out["pieces"].append({
            "n_raw": int(b - a),
            "frequency_hz": _rounded(f[keep], step=FREQ_STEP_HZ),
            "loss": _rounded(y[keep], decimals=LOSS_DECIMALS),
            "detuning_hz": _rounded(det[keep], step=FREQ_STEP_HZ) if det is not None else None,
            "stderr": _rounded(err[keep], decimals=LOSS_DECIMALS) if err is not None else None})
    return out


def _defects(rows) -> list[dict]:
    out = []
    for r in rows if isinstance(rows, list) else ():
        if isinstance(r, dict):
            out.append({k: (_num(r.get(k)) if k not in ("strong", "edge_truncated") else bool(r.get(k)))
                        for k in ("frequency_hz", "fwhm_hz", "peak_loss", "detuning_hz", "strong",
                                  "edge_truncated")})
    return out


def read_run(folder: Any) -> dict:
    """One run folder of the family: ``{"ok": True, ...}`` with every qubit's curve
    (in the run's own qubit names), or ``{"ok": False, "reason"}``. A folder
    still being written (``hub_sync._fresh``), an unfinished run, an unreadable
    or non-HDF5 ``ds_fit.h5`` is skipped; a failure is never cached, a success
    is cached by the size + mtime of ``ds_fit.h5``, ``data.json`` and
    ``node.json`` (LRU of :data:`CACHE_MAX`)."""
    from quam_state_manager.core import hub_sync
    folder = Path(folder)
    if hub_sync._fresh(folder):
        return _fail("still being written")
    key = (_norm(folder), _sig(folder / "ds_fit.h5"), _sig(folder / "data.json"), _sig(folder / "node.json"))
    with _RUN_LOCK:
        hit = _RUNS.get(key)
        if hit is not None:
            _RUNS.move_to_end(key)
            return hit
    node = safe_io.scan_json(folder / "node.json")
    if not isinstance(node, dict):
        return _fail("node.json is unreadable")
    meta = node.get("metadata") if isinstance(node.get("metadata"), dict) else {}
    if meta.get("status") != "finished":
        return _fail(f"run status is {meta.get('status')!r}, not finished")
    raw = safe_io.scan_bytes(folder / "ds_fit.h5")
    if raw is None:
        return _fail("ds_fit.h5 is missing or unreadable")
    if not raw.startswith(b"\x89HDF"):
        return _fail("ds_fit.h5 is not an HDF5 file")
    try:
        h5 = _read_h5(raw)
    except Exception as exc:  # noqa: BLE001 -- a truncated or foreign file is a skip, never a 500
        return _fail(f"ds_fit.h5 cannot be read ({type(exc).__name__})")
    data = safe_io.scan_json(folder / "data.json")
    fits = (data or {}).get("fit_results") if isinstance(data, dict) else None
    fits = fits if isinstance(fits, dict) else {}
    model = ((node.get("data") or {}).get("parameters") or {}).get("model") or {}
    t_ns = _num(model.get("interaction_time_ns")) if isinstance(model, dict) else None
    m = _RUN.fullmatch(folder.name)
    utc_us, quality = run_time.resolve(node.get("created_at"), meta.get("run_end"), folder)
    qubits = {}
    for i, name in enumerate(h5["names"]):
        curve = _qubit_curve(i, h5)
        fit = fits.get(name)
        curve["in_fit_results"] = isinstance(fit, dict)
        curve["defects"] = _defects(fit.get("defects")) if isinstance(fit, dict) else None
        curve["defect_freqs"] = (_sorted_freqs(fit.get("defects")) if isinstance(fit, dict)
                                 and isinstance(fit.get("defects"), list) else None)
        qubits[name] = curve
    out = {"ok": True, "folder": str(folder), "name": folder.name,
           "run_id": int(m[1]) if m else _num(node.get("id")), "node_name": meta.get("name"),
           "t_us": utc_us, "t": value_history.iso_z(utc_us),
           "time_quality": quality, "interaction_time_ns": t_ns, "qubits": qubits,
           "fit_only": sorted(q for q in fits if q not in qubits)}
    with _RUN_LOCK:
        _RUNS[key] = out
        while len(_RUNS) > CACHE_MAX:
            _RUNS.popitem(last=False)
    return out


def run_chip_name(folder: Any) -> str | None:
    """``extras.chip_name`` of the run's own saved state (cached per file version);
    None when it declares none or cannot be read."""
    p = Path(folder) / "quam_state" / "state.json"
    key = (_norm(p), _sig(p))
    if key[1] is None:
        return None
    with _RUN_LOCK:
        if key in _NAMES:
            return _NAMES[key]
    state = safe_io.scan_json(p)
    extras = state.get("extras") if isinstance(state, dict) else None
    name = extras.get("chip_name") if isinstance(extras, dict) else None
    name = name.strip() if isinstance(name, str) and name.strip() else None
    with _RUN_LOCK:
        _NAMES[key] = name
        while len(_NAMES) > CACHE_MAX:
            _NAMES.popitem(last=False)
    return name


def clear_caches() -> None:
    with _RUN_LOCK:
        _RUNS.clear()
        _NAMES.clear()
    with _LIST_LOCK:
        _LISTINGS.clear()


# ---------------------------------------------------------------- the chip
def _curve_view(run: dict, run_name: str, today_f01: float | None) -> dict:
    c = run["qubits"][run_name]
    t_ns = run["interaction_time_ns"]
    notes = []
    if run["time_quality"] != "offset":
        notes.append("order not proven (the run's time carries no offset)")
    if t_ns is None:
        notes.append("scan duration unknown")
    idle, moved, idle_note = c["idle_at_run_hz"], None, None
    if idle is not None and today_f01 is not None:
        moved = today_f01 - idle
        if abs(moved) > IDLE_TOLERANCE_HZ:
            idle_note = (f"idle then {idle / 1e9:.4f} GHz, now {today_f01 / 1e9:.4f} GHz"
                         f" ({moved / 1e6:+.1f} MHz)")
            notes.append(idle_note)
    return {"run_id": run["run_id"], "run_name": run["name"], "folder": run["folder"],
            "t": run["t"], "t_us": run["t_us"], "time_quality": run["time_quality"],
            "order_proven": run["time_quality"] == "offset",
            "interaction_time_ns": t_ns, "run_qubit": run_name,
            "flux_conversion": c["flux_conversion"], "n_samples": c["n_samples"], "dropped": c["dropped"],
            "range_hz": c["range_hz"], "median_step_hz": c["median_step_hz"],
            "loss_min": c["loss_min"], "loss_max": c["loss_max"],
            "idle_at_run_hz": idle, "idle_now_hz": today_f01, "idle_moved_hz": moved, "idle_note": idle_note,
            "defects": c["defects"], "pieces": c["pieces"], "notes": notes}


def build(roots: list, chip_qubits: dict, *, identity: Callable | None = None,
          names: Callable | None = None, cap: int = MAX_RUNS) -> dict:
    """Every qubit's measured loss curve and the run its stored defects came from.

    *roots*: the chip's data folders (strongest first, all searched).
    *chip_qubits*: ``{today's name: {"f01_hz", "stored"}}`` -- ``stored`` is the
    raw ``extras.population_loss_defects_json`` or absent.
    *identity(folder) -> (ok, how, reason)*: whether a run is this chip's.
    *names(folder) -> RunNames | None*: the run's names in today's (None: the
    chip was never renamed); a run whose era cannot be told is skipped.

    Per qubit: ``scan`` -- the newest run with a usable curve; ``marker`` -- the
    newest run that MEASURED the qubit (in both ``fit_results`` and
    ``ds_fit.h5``) whose defect frequencies equal the stored list (1 Hz,
    order-free), ``ambiguous`` when the stored list is empty and several runs
    match; ``marker_curve`` -- that run's curve when it is not the scan. The
    newest :data:`MAX_RUNS` candidates are read."""
    out = {"roots": [str(r) for r in roots], "reason": None, "cap": cap, "runs_listed": 0, "runs_read": 0,
           "skipped": [], "curves": {}}
    stored = {q: (stored_frequencies(v["stored"]) if "stored" in v else "absent") for q, v in chip_qubits.items()}
    curves = {q: {"scan": None, "marker": None, "marker_curve": None, "reason": None} for q in chip_qubits}
    if not roots:
        out["reason"] = NO_ROOTS
        for q in curves:
            curves[q]["reason"] = NO_ROOTS
            curves[q]["marker"] = _marker_none(stored[q], NO_ROOTS)
        out["curves"] = curves
        return out
    listed = candidates(roots)
    out["runs_listed"] = len(listed)
    scans: dict[str, tuple] = {}
    matches: dict[str, list] = {q: [] for q in chip_qubits}
    no_axis: dict[str, int] = {}
    for cand in listed[:cap]:
        run = read_run(cand["folder"])
        out["runs_read"] += 1
        if not run.get("ok"):
            out["skipped"].append({"run_id": cand["run_id"], "name": cand["name"], "reason": run["reason"]})
            continue
        if identity is not None:
            ok, _how, why = identity(cand["folder"])
            if not ok:
                out["skipped"].append({"run_id": run["run_id"], "name": run["name"],
                                       "reason": why or "another chip's run"})
                continue
        rn = names(cand["folder"]) if names is not None else None
        if rn is not None and not rn.known:
            out["skipped"].append({"run_id": run["run_id"], "name": run["name"],
                                   "reason": "the chip's qubits were renamed and this run's names cannot be told"})
            continue
        for run_name, c in run["qubits"].items():
            today = rn.current(run_name) if rn is not None else run_name
            if today is None or today not in curves:
                continue
            if c["pieces"] and today not in scans:
                scans[today] = (run, run_name)
            elif not c["pieces"]:
                no_axis.setdefault(today, run["run_id"])
            st = stored.get(today)
            if (c["in_fit_results"] and isinstance(st, list) and c["defect_freqs"] is not None
                    and defects_match(st, c["defect_freqs"])):
                matches[today].append((run, run_name))
    for q, entry in curves.items():
        f01 = chip_qubits[q].get("f01_hz")
        if q in scans:
            run, rname = scans[q]
            entry["scan"] = _curve_view(run, rname, f01)
        else:
            entry["reason"] = (f"run #{no_axis[q]} has no frequency axis for {q}" if q in no_axis
                               else "no population-loss run in the chip's data folders" if not listed
                               else f"no population-loss run of {q} among the {out['runs_read']} newest read")
        entry["marker"] = _marker(stored[q], matches[q], entry["scan"])
        if matches[q] and entry["marker"]["run_id"] != (entry["scan"] or {}).get("run_id"):
            run, rname = matches[q][0]
            if run["qubits"][rname]["pieces"]:
                entry["marker_curve"] = _curve_view(run, rname, f01)
    out["curves"] = curves
    return out


def _marker_none(st, reason) -> dict:
    if st == "absent":
        return {"status": "none", "run_id": None, "matches": 0, "label": "no stored defects"}
    if st is None:
        return {"status": "unreadable", "run_id": None, "matches": 0,
                "label": "the stored defect list cannot be read"}
    return {"status": "no_match", "run_id": None, "matches": 0, "label": reason}


def _marker(st, found: list, scan: dict | None) -> dict:
    if st == "absent" or st is None or not found:
        return _marker_none(st, "source of the stored defects not in the data folder")
    run = found[0][0]
    n = len(found)
    out = {"status": "match", "run_id": run["run_id"], "run_name": run["name"], "matches": n,
           "same_as_scan": bool(scan and scan["run_id"] == run["run_id"] and scan["folder"] == run["folder"]),
           "label": f"stored defects match run #{run['run_id']}"}
    if not st and n > 1:
        out.update(status="ambiguous", label=f"ambiguous (empty list matches {n} runs); newest #{run['run_id']}")
    return out
