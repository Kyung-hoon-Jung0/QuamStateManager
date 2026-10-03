"""True physical output for pulse amplitudes (docs/109, docs/248).

A stored ``amplitude`` is a bare scale factor — it says nothing about what
actually leaves the instrument. What the instrument is told is the WAVEFORM,
and the QM docs define the output from its samples, not from any class field:

- **MW channel** (drive / readout — the port carries ``full_scale_power_dbm``).
  [QM docs: ``documentation-website/docs/docs/docs/Guides/opx1000_fems.md``, § "Microwave FEM (MW-FEM)" >
  "Output Power", lines 186-187]:
  "This will set the power delivered to a 50 Ω load when the waveform is set to full scale (`{-1, 1}`)."
  "The amplitude is linear in voltage, not power."
  [QM docs: ``documentation-website/docs/docs/docs/Introduction/config.md``, § "Waveforms", line 473]:
  "For a MW-FEM using the waveform, the sample values give the output amplitude as a fraction of the `full_scale_power_dbm` and must be within [-1.0, 1.0]."
  It is the SAMPLE, not a class field, that the identity is about: a sample
  ``s`` puts out ``FSP + 20*log10|s|`` dBm, so the pulse's PEAK power is
  ``P = FSP + 20*log10(max_t |I(t) + iQ(t)|)``. [derived] the I/Q magnitude:
  the MW-FEM upconverts the complex baseband ``I + iQ``, whose envelope at
  each sample is ``|I + iQ|`` (an ``axis_angle`` rotation or a ``detuning``
  chirp is a unit-modulus factor and changes no magnitude).
- **LF channel** (flux / ``analog_outputs`` port).
  [QM docs: ``documentation-website/docs/docs/docs/Introduction/config.md``, § "Waveforms", line 472]:
  "For the OPX+ or a LF-FEM using the waveform, the sample values give the output in volts"
  — the annotation is the waveform's signed PEAK sample in volts, unit naming
  rather than a conversion.

Where the peak comes from (docs/248). ``P = FSP + 20*log10|amp|`` was used for
every pulse, i.e. the stored ``amplitude`` was taken to BE the waveform peak.
That holds only for some classes: a lab's area-normalised readout class
stored amplitude 0.0526 on an FSP -2 port (-27.6 dBm by the old identity)
while its waveform peaks 7 dB higher (-20.6 dBm). The peak is therefore read
off the waveform itself, synthesized by ``waveform_synth`` — the ONE
transcription of quam's ``waveform_function`` that the golden tests pin
bit-for-bit (``tests/test_waveform_golden.py``) — and only for a class whose
``__class__`` is a full dotted path SM transcribed
(``pulse_catalog.spec_at_known_home``). A lab class, a name-only match, a
missing ``__class__``, a field the transcription does not model, or a
waveform that will not synthesize is a BLANK with a stated reason, never the
old identity applied anyway.

[derived] Every catalog waveform is homogeneous of degree 1 in ``amplitude``
(each transcription multiplies a shape that does not depend on it), so the
peak is ``|amp| * r`` with ``r = peak(amplitude=1)`` a function of the SHAPE
parameters only. ``r`` is computed once per shape and cached; the grid sends
it to the client (``peak``) so live typing recomputes the same number the
server would. Checked numerically in ``tests/test_physical_units.py``
(``TestPeakFollowsTheClass``), together with which classes have ``r == 1``:

- ``r == 1`` for every parameter value: Square, SquareReadout, SNZ, both
  GaussianFiltered classes (normalised to ``|amp|`` by construction).
- ``r == 1`` iff a flat section exists: the four FlatTop classes and
  ``_FlatTopGaussian`` (flat_length >= 1), CosineBipolar and
  ``_CosineBipolar`` (flat_length >= 2).
- Gaussian: ``r == 1`` only for ``subtracted=False`` and an ODD length (the
  centre sample exists); ``subtracted=True`` gives ``r = 1 - g_end < 1``.
- DragCosine [derived]: ``|z|^2 = (A/2)^2 [(1-cos th)^2 + k^2 sin^2 th]`` with
  ``k = alpha*1e9 / ((length-1)(anharmonicity-detuning))``. On ``cos th`` in
  [-1, 1] the maximum stays at the centre (th = pi, ``|z| = A``) iff
  ``k^2 <= 2``; beyond it ``r = k^2 / (2 sqrt(k^2-1))``. An EVEN length has no
  centre sample, so ``r`` is slightly below 1 there.
- DragGaussian (subtracted=False) [derived]: ``|z|^2 = A^2 e^{-x^2}(1 +
  kappa^2 x^2)`` with ``x = (t-c)/sigma`` and ``kappa = alpha*1e9 /
  (2 pi sigma (anharmonicity-detuning))``: the centre is the maximum iff
  ``kappa^2 <= 1``, beyond it ``r = kappa exp(-(kappa^2-1)/(2 kappa^2))``.
  With subtraction the centre stays the maximum when ``kappa^2 <= 1 - g_end``
  (necessary at the centre; sufficiency checked numerically, not proven).
- ErfSquare: ``r <= 1``, approaching 1 as the flat part outgrows the rise.

Honesty rules: annotate ONLY when the chain fully resolves to a numeric FSP
(MW) or an analog_outputs port (LF) AND the pulse class is one SM can
synthesize; anything else returns ``None`` and the surface stays blank (the
``why`` out-list then names the reason). Never invent.

Pure module: dict-walking + the in-process synthesizer, no store, no I/O.
"""

from __future__ import annotations

import math
from functools import lru_cache
from typing import Any

from .pointer_path import resolve_field_target, _walk
from .pointer_resolver import is_pointer

#: How many ancestors above the amplitude leaf may be searched for the channel
#: (the dict carrying ``opx_output``). Real shapes are 2 (``<ch>.operations.
#: <op>.amplitude`` -> op -> operations -> channel) plus one for safety.
_MAX_UP = 4

#: A unit peak within this of 1 IS 1: an ``axis_angle`` rotation leaves
#: ``|z| = |amp|`` up to float rounding (2e-16), and 20*log10 of the gap is
#: under 1e-11 dB -- snapping keeps every peak == amp class bit-identical to
#: the plain identity it always showed.
_UNIT_TOL = 1e-12

#: Two samples within this fraction of the peak are the same peak; the FIRST
#: of them gives the sign (a bipolar pulse's leading lobe, never float noise
#: deciding between two equal lobes).
_TIE_TOL = 1e-9


def channel_of(merged: dict, amp_path: str) -> str | None:
    """The nearest ancestor of *amp_path* whose dict carries ``opx_output``.

    Works on the alias or the resolved path alike: non-dict ancestors (a
    pointer STRING at ``operations.x180``) are skipped, and the channel dict
    (``qubits.<q>.xy`` / ``.resonator`` / ``.z``) is always a real dict.
    """
    segs = amp_path.split(".")
    for up in range(1, _MAX_UP + 1):
        if len(segs) - up < 1:
            break
        anc = segs[: len(segs) - up]
        found, node = _walk(merged, anc)
        if found and isinstance(node, dict) and "opx_output" in node:
            return ".".join(anc)
    return None


def _pulse_dict(merged: dict, amp_path: str, alias_path: str | None):
    """``(resolved path, dict)`` of the pulse that PLAYS this amplitude.

    The alias wins: ``-y90.amplitude`` may point at ``x90``'s number, but the
    waveform is ``-y90``'s own shape. The alias parent is resolved through
    pointers (``operations.readout`` -> ``#./readout_<x>``)."""
    for p in (alias_path, amp_path):
        if not p or "." not in p:
            continue
        parent = p.rsplit(".", 1)[0]
        try:
            ft = resolve_field_target(merged, parent)
        except Exception:
            continue
        if not ft.get("resolvable"):
            continue
        rp = ft.get("resolved_path") or parent
        found, node = _walk(merged, rp.split("."))
        if found and isinstance(node, dict):
            return rp, node
    return None, None


def _class_label(qclass: str) -> str:
    """The class as a person reads it: the leaf name, unless that leaf is also
    a name SM knows -- then the full path, or 'SquarePulse is not one SM can
    synthesize' would read as nonsense."""
    from .pulse_catalog import PULSE_CATALOG
    leaf = qclass.rsplit(".", 1)[-1]
    return qclass if leaf in PULSE_CATALOG else leaf


def _freeze(params: dict) -> tuple | None:
    out = []
    for k in sorted(params):
        v = params[k]
        if isinstance(v, list):
            if not all(isinstance(x, (int, float, str, bool, type(None))) for x in v):
                return None
            v = ("__list__",) + tuple(v)
        elif not isinstance(v, (int, float, str, bool, type(None))):
            return None
        out.append((k, v))
    return tuple(out)


def _unit_peak_raw(spec_key: str, params: dict) -> tuple:
    """``(r, sign, real, error)`` for one shape at amplitude 1."""
    import numpy as np
    from .waveform_synth import synthesize
    pay = synthesize(spec_key, params)
    if not pay.get("ok"):
        return None, 1, True, pay.get("error") or "it does not synthesize"
    i = pay.get("i") or []
    q = pay.get("q")
    if any(v is None for v in i) or (q is not None and any(v is None for v in q)):
        return None, 1, True, "some of its samples are not finite"
    ia = np.asarray(i, dtype=float)
    qa = np.asarray(q, dtype=float) if q is not None else np.zeros_like(ia)
    if ia.size == 0:
        return None, 1, True, "it has no samples"
    mag = np.hypot(ia, qa)
    peak = float(mag.max())
    if not math.isfinite(peak) or peak == 0.0:
        return None, 1, True, "its waveform is zero"
    if abs(peak - 1.0) <= _UNIT_TOL:
        peak = 1.0
    first = int(np.argmax(mag >= peak * (1.0 - _TIE_TOL)))
    sign = -1 if ia[first] < 0 else 1
    real = not bool(np.any(qa != 0.0))
    return peak, sign, real, None


@lru_cache(maxsize=4096)
def _unit_peak_cached(spec_key: str, frozen: tuple) -> tuple:
    params = {k: (list(v[1:]) if isinstance(v, tuple) and v[:1] == ("__list__",) else v)
              for k, v in frozen}
    return _unit_peak_raw(spec_key, params)


def pulse_peak(merged: dict, amp_path: str, alias_path: str | None = None) -> dict:
    """The waveform peak per unit amplitude of the pulse behind *amp_path*.

    Returns ``{"ratio": r | None, "sign": +1/-1, "real": bool, "reads": [...],
    "why": str | None}``: ``ratio`` is ``max|I+iQ| / |amplitude|`` (``None``
    when SM cannot know it, ``why`` then says why in a short English phrase),
    ``sign`` the sign of the first peak sample per positive amplitude, ``real``
    whether the waveform has no Q part. ``reads`` are the stored paths the
    answer depends on: the pulse dict itself (its ``__class__`` and every
    shape field) and the target of every pointer-valued shape field (RAM P6:
    a grid re-annotates a cell when a read path moves).
    """
    from .pulse_catalog import spec_at_known_home
    out: dict = {"ratio": None, "sign": 1, "real": True, "reads": [], "why": None}
    ppath, node = _pulse_dict(merged, amp_path, alias_path)
    if node is None:
        out["why"] = "the pulse it belongs to was not found"
        return out
    out["reads"].append(ppath)
    qclass = node.get("__class__")
    if not isinstance(qclass, str) or not qclass:
        out["why"] = "the pulse declares no __class__"
        return out
    spec = spec_at_known_home(qclass)
    if spec is None:
        out["why"] = f"pulse class {_class_label(qclass)} is not one SM can synthesize"
        return out
    # Strict: a field the transcription does not model may shape the waveform
    # (a newer generation of the same class). The preview only WARNS about it;
    # a stated power must not be built on a dropped field.
    known = {p.name for p in spec.params} | {"__class__", "length"}
    for p in spec.params:
        known.update(p.aliases)
    extra = sorted(k for k in node if k not in known)
    if extra:
        out["why"] = (f"pulse class {_class_label(qclass)} carries "
                      f"field(s) SM does not model: {', '.join(extra[:3])}")
        return out
    params: dict = {}
    for fname, fval in node.items():
        if fname in ("__class__", "amplitude"):
            continue
        sp = spec.param(fname)
        if fname != "length" and (sp is None or not sp.synth):
            # ids, markers, thresholds, integration weights: the catalog
            # declares them shape-free (``synth=False``), so they are neither
            # read nor part of the cache key
            continue
        if is_pointer(fval):
            try:
                ft = resolve_field_target(merged, f"{ppath}.{fname}")
            except Exception:
                ft = {}
            if ft.get("resolvable") and ft.get("resolved_path"):
                rp = ft["resolved_path"]
                v = ft.get("resolved_value")
                if v is None:
                    _found, v = _walk(merged, rp.split("."))
                out["reads"].append(rp)
                params[fname] = v
            else:
                # left as the pointer: synthesize() classifies it -- an error
                # only where the field shapes the waveform
                params[fname] = fval
        else:
            params[fname] = fval
    params["amplitude"] = 1.0
    frozen = _freeze(params)
    if frozen is not None:
        r, sign, real, err = _unit_peak_cached(spec.key, frozen)
    else:
        r, sign, real, err = _unit_peak_raw(spec.key, params)
    if r is None:
        out["why"] = f"its {spec.key} waveform cannot be synthesized ({err})"
        return out
    out.update(ratio=r, sign=sign, real=real)
    return out


def amp_annotation(merged: dict, amp_path: str, amp_value: Any,
                   reads: list | None = None, *, alias_path: str | None = None,
                   why: list | None = None) -> dict | None:
    """Physical annotation for one amplitude leaf, or ``None`` (stay blank).

    Returns ``{"kind": "mw", "fsp": float, "dbm": float, "text": str,
    "fsp_path": str, "peak": float}`` or ``{"kind": "lf", "volts": float,
    "text": str, "peak": float}``. ``peak`` is the waveform peak per unit
    amplitude (signed for LF): the client multiplies a typed amplitude by it.

    *alias_path* is the path the surface shows when *amp_path* is the
    resolved one (the grid passes both): the pulse whose SHAPE counts is the
    alias's. *why*, when given, receives ``{"mark", "text"}`` when the blank
    is the pulse class's doing -- the one blank a person can act on.
    """
    if isinstance(amp_value, bool) or not isinstance(amp_value, (int, float)):
        return None
    if not amp_path.endswith(".amplitude"):
        return None
    ch = channel_of(merged, amp_path)
    if ch is None:
        return None
    try:
        ft = resolve_field_target(merged, ch + ".opx_output.full_scale_power_dbm")
    except Exception:
        ft = {}
    fsp = ft.get("resolved_value") if ft.get("resolvable") else None
    if reads is not None and ft.get("resolvable") and ft.get("resolved_path"):
        # the one VALUE this annotation reads besides the amplitude itself
        # (RAM P6: the Live-Edit grid re-annotates a cell when it moves)
        reads.append(ft["resolved_path"])
    if isinstance(fsp, (int, float)) and not isinstance(fsp, bool):
        if amp_value == 0:
            return None            # no output — a fabricated "-inf dBm" helps no one
        pk = pulse_peak(merged, amp_path, alias_path)
        if reads is not None:
            reads.extend(pk["reads"])
        if pk["ratio"] is None:
            if why is not None:
                why.append({"mark": "dBm ?", "text": "dBm unknown: " + pk["why"]})
            return None
        r = pk["ratio"]
        dbm = float(fsp) + 20.0 * math.log10(abs(float(amp_value)) * r)
        # fsp_path (docs/238): WHERE the FSP lives, so a grid can follow an
        # FSP edit to every amplitude on that port without asking twice
        return {"kind": "mw", "fsp": float(fsp), "dbm": dbm,
                "text": f"{dbm:.1f} dBm",
                "fsp_path": ft.get("resolved_path") or "",
                "peak": r}
    # Not an MW port — LF (flux) if the channel's port resolves under
    # ports.analog_outputs; a sample value is then literally volts.
    try:
        pft = resolve_field_target(merged, ch + ".opx_output")
    except Exception:
        pft = {}
    if not pft.get("resolvable"):
        return None
    rp = pft.get("resolved_path") or ""
    if ".analog_outputs." not in f".{rp}.":
        return None
    pk = pulse_peak(merged, amp_path, alias_path)
    if reads is not None:
        reads.extend(pk["reads"])
    if pk["ratio"] is None or not pk["real"]:
        if why is not None:
            text = pk["why"] or "an I/Q waveform on a single-ended LF port"
            why.append({"mark": "V ?", "text": "volts unknown: " + text})
        return None
    peak = pk["sign"] * pk["ratio"]
    v = float(amp_value) * peak
    return {"kind": "lf", "volts": v, "text": format_volts(v), "peak": float(peak)}


def format_volts(v: float) -> str:
    """3-sig-fig engineering volts: 0.012 -> ``12 mV``, 0.5 -> ``500 mV``,
    1.2 -> ``1.2 V``. Amplitude 0 still formats (``0 V``) — an LF zero is a
    real, meaningful level, unlike an MW log of zero."""
    if abs(v) >= 1.0 or v == 0:
        return f"{v:.3g} V"
    return f"{v * 1e3:.3g} mV"
