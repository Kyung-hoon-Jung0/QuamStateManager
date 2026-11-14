"""Preview a pulse class DRAFT with the lab's own quam (runs in the user's env).

Like ``run_pulse_waveform.py`` this script runs under the interpreter the user
selected and uses only the standard library at import time. It imports the
draft file straight from SM's instance folder under a private module name,
without installing it anywhere, and answers from what the import gives:

* ``fields``: the dataclass fields of the class as Python sees them, so a
  field the user added by hand anywhere in the file is listed;
* ``samples``: ``calculate_waveform()`` for the given field values with the
  port's sampling rate (default 1 GSa/s);
* ``proportional``: the waveform at two amplitudes; proportional when every
  sample ratio equals the amplitude ratio;
* on a syntax or runtime error: the Python error and the line in the draft.

Input (``--in`` JSON)::

    {"draft": "<abs path>", "class_name": "Name", "params": {...},
     "sampling_rate": 1e9, "amplitudes": [0.1, 0.2]}

Output (``--out`` JSON)::

    {"status": "ok", "ok": true|false, "python": "3.x", "sha256": "...",
     "fields": [{"name", "type", "default", "has_default", "inherited"}],
     "samples": [...], "n": 200, "sampling_rate": 2e9,
     "proportional": {"proportional": bool, "ratio_min", "ratio_max",
                      "ratio_expected", "amplitudes": [a1, a2]},
     "warnings": [...], "error": null | "...", "line": null | int}

``status`` is about this script; ``ok`` is about the draft.
"""

from __future__ import annotations

import argparse
import dataclasses
import hashlib
import importlib.util
import json
import math
import sys
import traceback
from pathlib import Path

#: relative tolerance of the proportional check: a template formula that is
#: linear in amplitude gives ratios equal to the amplitude ratio up to float64
#: rounding (about 1e-16 relative); 1e-9 leaves room for a few operations
#: while any real nonlinearity (amplitude squared) is off by order one
RATIO_RTOL = 1e-9
_MAX_SAMPLES = 200000


def _json_default(v):
    if v is dataclasses.MISSING:
        return None
    if isinstance(v, (bool, int, str)) or v is None:
        return v
    if isinstance(v, float):
        return v if math.isfinite(v) else None
    return repr(v)


def _draft_line(exc: BaseException, draft: str) -> int | None:
    if isinstance(exc, SyntaxError) and exc.filename and \
            Path(exc.filename).resolve() == Path(draft).resolve():
        return exc.lineno
    line = None
    for fr in traceback.extract_tb(exc.__traceback__):
        try:
            if Path(fr.filename).resolve() == Path(draft).resolve():
                line = fr.lineno
        except (OSError, ValueError):
            continue
    return line


def _load(draft: str, class_name: str, data: bytes):
    name = "sm_pulse_draft_" + hashlib.sha256(data).hexdigest()[:12]
    spec = importlib.util.spec_from_file_location(name, draft)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    code = compile(data, draft, "exec")
    exec(code, mod.__dict__)  # noqa: S102 -- the user's own draft, in the user's env
    cls = getattr(mod, class_name, None)
    if not isinstance(cls, type):
        raise LookupError(f"the draft does not define a class named {class_name}")
    return cls


def _fields(cls) -> list:
    from quam.components.pulses import Pulse
    base = {f.name for f in dataclasses.fields(Pulse)}
    out = []
    for f in dataclasses.fields(cls):
        if not f.init:
            continue
        has = f.default is not dataclasses.MISSING
        t = f.type if isinstance(f.type, str) else getattr(f.type, "__name__", repr(f.type))
        out.append({"name": f.name, "type": t,
                    "default": _json_default(f.default) if has else None,
                    "has_default": has,
                    "inherited": f.name in base and f.name not in cls.__dict__.get(
                        "__annotations__", {})})
    return out


def _wave(cls, params: dict, rate: float):
    import warnings
    import numpy as np
    fields = {f.name for f in dataclasses.fields(cls) if f.init}
    kwargs = {k: v for k, v in params.items() if k in fields}
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        pulse = cls(**kwargs)
        # the draft is not attached to a channel: give it the port's rate
        object.__setattr__(pulse, "_get_sampling_rate", lambda: float(rate))
        wf = pulse.calculate_waveform()
    arr = np.asarray(wf)
    if arr.ndim == 0:
        n = int(round(float(pulse.length) * rate / 1e9))
        arr = np.full(n, arr.item())
    if np.iscomplexobj(arr):
        raise TypeError("the waveform is complex; the draft preview draws real waveforms")
    arr = arr.astype(float)
    if arr.size > _MAX_SAMPLES:
        raise ValueError(f"{arr.size} samples exceeds the preview limit of {_MAX_SAMPLES}")
    return arr, [str(w.message) for w in caught]


def _proportional(cls, params: dict, rate: float, amps) -> dict:
    import numpy as np
    a1, a2 = float(amps[0]), float(amps[1])
    w1, _ = _wave(cls, dict(params, amplitude=a1), rate)
    w2, _ = _wave(cls, dict(params, amplitude=a2), rate)
    want = a2 / a1
    rec = {"amplitudes": [a1, a2], "ratio_expected": want,
           "ratio_min": None, "ratio_max": None, "proportional": None}
    if w1.shape != w2.shape:
        rec["proportional"] = False
        rec["reason"] = "the sample count changes with amplitude"
        return rec
    scale = float(np.max(np.abs(w1))) if w1.size else 0.0
    nz = np.abs(w1) > 1e-12 * max(scale, 1e-300)
    if not np.any(nz):
        rec["reason"] = "the waveform is zero at the first amplitude"
        rec["proportional"] = bool(np.all(w2 == 0))
        return rec
    r = w2[nz] / w1[nz]
    rec["ratio_min"], rec["ratio_max"] = float(np.min(r)), float(np.max(r))
    zeros_ok = bool(np.all(np.abs(w2[~nz]) <= 1e-12 * max(scale, 1e-300) * abs(want)))
    rec["proportional"] = bool(np.allclose(r, want, rtol=RATIO_RTOL, atol=0) and zeros_ok)
    return rec


def run(req: dict) -> dict:
    draft = str(req["draft"])
    class_name = str(req["class_name"])
    rate = float(req.get("sampling_rate") or 1e9)
    params = dict(req.get("params") or {})
    res = {"status": "ok", "ok": False, "python": sys.version.split()[0],
           "sha256": None, "fields": [], "samples": [], "n": None,
           "sampling_rate": rate, "proportional": None, "warnings": [],
           "error": None, "line": None}
    try:
        data = Path(draft).read_bytes()
    except OSError as exc:
        res["error"] = f"could not read the draft: {exc}"
        return res
    res["sha256"] = hashlib.sha256(data).hexdigest()
    try:
        cls = _load(draft, class_name, data)
        from quam.components.pulses import Pulse
        if not (isinstance(cls, type) and issubclass(cls, Pulse)):
            raise TypeError(f"{class_name} is not a quam Pulse")
        res["fields"] = _fields(cls)
        for f in res["fields"]:
            if f["name"] not in params and f["has_default"] and f["name"] != "id":
                params.setdefault(f["name"], f["default"])
        arr, warns = _wave(cls, params, rate)
        res["samples"] = [float(x) for x in arr.tolist()]
        res["n"] = int(arr.size)
        res["warnings"] = warns[:5]
        if any(f["name"] == "amplitude" for f in res["fields"]):
            res["proportional"] = _proportional(cls, params, rate,
                                                req.get("amplitudes") or [0.1, 0.2])
        res["ok"] = True
    except BaseException as exc:  # noqa: BLE001 -- the draft's own error is the answer
        res["error"] = f"{type(exc).__name__}: {exc}"
        res["line"] = _draft_line(exc, draft)
    return res


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--in", dest="inp", required=True)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()
    try:
        result = run(json.loads(Path(args.inp).read_text(encoding="utf-8")))
    except BaseException as exc:  # noqa: BLE001
        result = {"status": "error", "ok": False,
                  "error": f"{type(exc).__name__}: {exc}"}
    Path(args.out).write_text(json.dumps(result), encoding="utf-8")
    print(json.dumps({"status": result.get("status")}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
