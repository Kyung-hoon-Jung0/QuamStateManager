"""Draw pulse waveforms with the LAB's own class code (docs/2xx adaptive pulses).

Runs under the *external* interpreter the user selected (like
``probe_state_schema.py``), so at import time it uses ONLY the standard
library; the pulse classes are imported inside :func:`draw`.

Why this exists: ``core/waveform_synth.py`` mirrors quam's own pulse classes.
A class the lab wrote (a CZ flux pulse, a readout-weights pulse) has no mirror
there, and transcribing it into SM would ship a COPY that goes stale the day
the lab edits its module (docs/189 §2). The generated config is one answer,
but it is only as fresh as the last ``generate_config()`` run and it cannot
draw a pulse that does not exist yet (the create form). This script asks the
class itself: build the dataclass from the pulse's fields and call quam's own
``Pulse.calculate_waveform()`` -- the method ``_config_add_waveforms`` calls
when the config is generated, so both answers come from the same line of the
lab's code.

Input (``--in`` JSON)::

    {"items": [{"qclass": "pkg.mod.Class", "params": {...}}, ...],
     "max_samples": 20000}

Output (``--out`` JSON)::

    {"status": "ok", "python": "3.11.9",
     "items": [{"ok": true, "canonical": "...", "i": [...], "q": [...] | null,
                "iq": false, "kind": "arbitrary" | "constant", "length": 88,
                "dropped": ["field the class does not declare"],
                "error": null}, ...],
     "sources": {"<lab file>": [mtime_ns, size], ...},
     "error": null}

``sources`` are the out-of-env files imported to answer (see
``_script_common.out_of_env_sources``) -- the SM side re-stats them on every
read of the cached waveform, so an edit to the lab's module is a miss.

Run standalone::  python run_pulse_waveform.py --in i.json --out o.json
"""

from __future__ import annotations

import argparse
import dataclasses
import importlib
import json
import math
import sys
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _script_common import out_of_env_sources  # noqa: E402

_MAX_SAMPLES = 20000


def _import_class(path: str):
    """``pkg.mod.Class`` (or ``pkg.mod.Outer.Inner``) -> the class object."""
    parts = path.split(".")
    for cut in range(len(parts) - 1, 0, -1):
        mod_name = ".".join(parts[:cut])
        try:
            obj = importlib.import_module(mod_name)
        except ModuleNotFoundError as exc:
            if exc.name and (mod_name == exc.name or mod_name.startswith(exc.name + ".")):
                continue
            raise
        for attr in parts[cut:]:
            obj = getattr(obj, attr)
        if not isinstance(obj, type):
            raise TypeError(f"{path} is not a class")
        return obj
    raise ModuleNotFoundError(f"no importable module in {path!r}")


def _floats(seq) -> list:
    out = []
    for v in seq:
        f = float(v)
        out.append(f if math.isfinite(f) else None)
    return out


def _draw_one(qclass: str, params: dict, max_samples: int) -> dict:
    res = {"ok": False, "canonical": None, "i": [], "q": None, "iq": False,
           "kind": None, "length": None, "dropped": [], "warnings": [],
           "error": None}
    try:
        cls = _import_class(qclass)
    except BaseException as exc:  # noqa: BLE001 -- SystemExit from a lab import too
        res["error"] = f"could not import {qclass}: {type(exc).__name__}: {exc}"
        return res
    res["canonical"] = f"{cls.__module__}.{cls.__qualname__}"
    try:
        from quam.components.pulses import Pulse
    except Exception as exc:  # noqa: BLE001
        res["error"] = f"quam is not importable in this environment: {exc}"
        return res
    if not issubclass(cls, Pulse):
        res["error"] = f"{res['canonical']} is not a quam Pulse"
        return res
    if not dataclasses.is_dataclass(cls):
        res["error"] = f"{res['canonical']} is not a dataclass"
        return res

    fields = {f.name: f for f in dataclasses.fields(cls) if f.init}
    kwargs = {k: v for k, v in (params or {}).items()
              if k in fields and k != "__class__"}
    res["dropped"] = sorted(k for k in (params or {})
                            if k not in fields and k != "__class__")
    missing = [n for n, f in fields.items()
               if n not in kwargs
               and f.default is dataclasses.MISSING
               and f.default_factory is dataclasses.MISSING]  # type: ignore[misc]
    if missing:
        res["error"] = ("missing required field" + ("s" if len(missing) > 1 else "")
                        + ": " + ", ".join(missing))
        return res
    import warnings
    try:
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            pulse = cls(**kwargs)
            wf = pulse.calculate_waveform()
    except BaseException as exc:  # noqa: BLE001 -- the lab's own validation speaks
        res["error"] = f"{type(exc).__name__}: {exc}"
        return res
    # what the class itself said while drawing -- e.g. quam's "not attached to
    # a channel, cannot determine sampling rate": the pulse is drawn DETACHED
    # from its channel, and a class that reads the channel says so here
    res["warnings"] = list(dict.fromkeys(
        str(w.message).strip() for w in caught if str(w.message).strip()))[:5]
    if wf is None:
        res["error"] = "the class's calculate_waveform() returned None"
        return res

    length = None
    try:
        raw_len = pulse.length
        if isinstance(raw_len, (int, float)) and not isinstance(raw_len, bool):
            length = int(raw_len)
    except Exception:  # noqa: BLE001
        length = None

    import numpy as np
    arr = np.asarray(wf)
    if arr.ndim == 0:
        # a constant waveform: quam hands the config a single number
        n = length if length and length > 0 else 16
        res["kind"] = "constant"
        arr = np.full(min(n, max_samples), arr.item())
    elif arr.ndim != 1:
        res["error"] = f"unsupported waveform shape {arr.shape}"
        return res
    else:
        res["kind"] = "arbitrary"
    if arr.size > max_samples:
        res["error"] = (f"{arr.size} samples exceeds the preview limit of "
                        f"{max_samples}")
        return res
    # the same test quam's _config_add_waveforms uses to pick I/Q
    is_iq = bool(np.iscomplexobj(arr))
    res["i"] = _floats(np.real(arr).astype(float).tolist())
    res["q"] = _floats(np.imag(arr).astype(float).tolist()) if is_iq else None
    res["iq"] = bool(is_iq)
    res["length"] = length if length is not None else int(arr.size)
    res["ok"] = True
    return res


def draw(items: list, max_samples: int = _MAX_SAMPLES) -> dict:
    out = []
    for it in items or []:
        qclass = (it or {}).get("qclass")
        params = (it or {}).get("params") or {}
        if not isinstance(qclass, str) or not qclass:
            out.append({"ok": False, "error": "no class named"})
            continue
        out.append(_draw_one(qclass, params if isinstance(params, dict) else {},
                             max_samples))
    return {"python": sys.version.split()[0], "items": out,
            "sources": out_of_env_sources()}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--in", dest="inp", required=True)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()
    result = {"status": "error", "python": "", "items": [], "sources": {},
              "error": None, "traceback": None}
    try:
        spec = json.loads(Path(args.inp).read_text(encoding="utf-8"))
        result.update(draw(spec.get("items") or [],
                           int(spec.get("max_samples") or _MAX_SAMPLES)))
        result["status"] = "ok"
    except BaseException as exc:  # noqa: BLE001
        result["error"] = f"{type(exc).__name__}: {exc}"
        result["traceback"] = traceback.format_exc()
    Path(args.out).write_text(json.dumps(result), encoding="utf-8")
    print(json.dumps({"status": result["status"]}))
    return 0 if result["status"] == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
