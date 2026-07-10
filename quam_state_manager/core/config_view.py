"""Slice + waveform helpers over a QM config dict.

The dict comes from :func:`quam_state_manager.core.config_generator.run_config_preview`
(which runs ``machine.generate_config()`` in a subprocess). These helpers
are pure functions over the resulting dict — no QUAM imports, no subprocess
calls — so they can run inside the Flask process and be unit-tested
without the QM stack installed.

A QM config has roughly this shape (post-deepcopy from
``qua_config_template.py``)::

    {
      "version": 1,
      "controllers": {"con1": {...}},
      "elements": {"q1.xy": {...}, "q1.z": {...}, ...},
      "pulses": {"x180_DragCosine.pulse": {...}, ...},
      "waveforms": {
          "wf_q1_xy_x180_I": {"type": "arbitrary", "samples": [...]},
          "wf_q1_z_const_0.1": {"type": "constant", "sample": 0.1},
      },
      "integration_weights": {...},
      "digital_waveforms": {...},
      "mixers": {...},
      "oscillators": {...},
      "octaves": {...},
    }

Element names use QUAM's ``"{qubit}.{channel}"`` convention (e.g.
``"q1.xy"``). The helpers below scan the element keys to find what belongs
to a qubit, then walk each element's ``operations`` dict to find its
pulses, then resolve each pulse to a waveform.
"""

from __future__ import annotations

import re
from typing import Any, Iterable


# ---------------------------------------------------------------------------
# Top-level slice
# ---------------------------------------------------------------------------

def top_level_keys(config: dict) -> list[str]:
    """Return the top-level keys in *config* in a stable display order."""
    preferred = [
        "version", "controllers", "elements", "pulses", "waveforms",
        "integration_weights", "digital_waveforms", "mixers", "oscillators",
        "octaves",
    ]
    seen = set()
    out: list[str] = []
    for key in preferred:
        if key in config:
            out.append(key)
            seen.add(key)
    for key in config.keys():
        if key not in seen:
            out.append(key)
    return out


# ---------------------------------------------------------------------------
# Per-qubit / per-pair slices
# ---------------------------------------------------------------------------

def _element_keys_for(config: dict, target_prefix: str) -> list[str]:
    """Element keys starting with ``"<target_prefix>."``."""
    elements = config.get("elements") or {}
    if not isinstance(elements, dict):
        return []
    prefix = f"{target_prefix}."
    return sorted(k for k in elements.keys() if k.startswith(prefix))


def _pulse_names_referenced_by(elements: dict, element_keys: Iterable[str]) -> list[str]:
    """All pulse names referenced via ``element["operations"]`` on the given keys."""
    out: set[str] = set()
    for key in element_keys:
        elem = elements.get(key) or {}
        ops = elem.get("operations")
        if isinstance(ops, dict):
            for pulse_name in ops.values():
                if isinstance(pulse_name, str):
                    out.add(pulse_name)
    return sorted(out)


def _waveform_names_referenced_by(pulses: dict, pulse_names: Iterable[str]) -> list[str]:
    """All waveform names referenced via ``pulse["waveforms"]`` on the given pulses."""
    out: set[str] = set()
    for pname in pulse_names:
        pulse = pulses.get(pname) or {}
        wfs = pulse.get("waveforms")
        if isinstance(wfs, dict):
            for wname in wfs.values():
                if isinstance(wname, str):
                    out.add(wname)
        elif isinstance(wfs, str):
            out.add(wfs)
    return sorted(out)


def slice_for(config: dict, target_prefix: str) -> dict:
    """Return the slice of *config* that belongs to one qubit or pair.

    *target_prefix* is the QUAM-side name as it appears in element keys —
    e.g. ``"q1"`` for a single qubit or ``"q1-2"`` for a pair (the same
    naming convention quam-builder uses).

    The returned dict has the same top-level shape as the full config but
    only includes the elements, pulses, waveforms (etc.) that this target
    actually touches.
    """
    elements_dict = config.get("elements") or {}
    pulses_dict = config.get("pulses") or {}
    waveforms_dict = config.get("waveforms") or {}
    integration_dict = config.get("integration_weights") or {}
    mixers_dict = config.get("mixers") or {}

    element_keys = _element_keys_for(config, target_prefix)
    pulse_names = _pulse_names_referenced_by(elements_dict, element_keys)
    waveform_names = _waveform_names_referenced_by(pulses_dict, pulse_names)

    # Integration weights live under pulses' integration_weights too.
    iw_names: set[str] = set()
    for pname in pulse_names:
        pulse = pulses_dict.get(pname) or {}
        iws = pulse.get("integration_weights")
        if isinstance(iws, dict):
            for iw_name in iws.values():
                if isinstance(iw_name, str):
                    iw_names.add(iw_name)

    # Mixers referenced by any element in the slice.
    mixer_names: set[str] = set()
    for key in element_keys:
        elem = elements_dict.get(key) or {}
        mix_obj = elem.get("mixInputs") or elem.get("MWInput") or {}
        if isinstance(mix_obj, dict):
            mn = mix_obj.get("mixer")
            if isinstance(mn, str):
                mixer_names.add(mn)

    return {
        "elements": {k: elements_dict[k] for k in element_keys},
        "pulses": {n: pulses_dict[n] for n in pulse_names if n in pulses_dict},
        "waveforms": {
            n: waveforms_dict[n] for n in waveform_names if n in waveforms_dict
        },
        "integration_weights": {
            n: integration_dict[n] for n in sorted(iw_names) if n in integration_dict
        },
        "mixers": {n: mixers_dict[n] for n in sorted(mixer_names) if n in mixers_dict},
    }


# ---------------------------------------------------------------------------
# Waveform resolution for plotting
# ---------------------------------------------------------------------------

def resolve_waveform(config: dict, waveform_name: str) -> dict:
    """Resolve a named waveform to a ready-to-plot ``{x, y, kind}`` dict.

    Constant descriptors are expanded to a flat line of the correct length
    so users always see voltage-vs-time. The pulse length is inferred from
    the first pulse that references this waveform (waveforms themselves
    don't carry duration in the QM config); when no referencing pulse
    carries a length, a 16-sample placeholder window is used and
    ``length_inferred`` is set so the UI can say "length unknown" instead
    of presenting the guess as truth.

    Returns::

        {
          "name": waveform_name,
          "kind": "constant" | "arbitrary" | "unknown",
          "length_ns": int,
          "length_inferred": bool,           # True when the placeholder fired
          "x": [0, 1, 2, ..., length-1],    # ns
          "y": [...],                        # volts at 50 Ω
          "constant_value": float | None,    # set when kind == "constant"
        }
    """
    waveforms = config.get("waveforms") or {}
    pulses = config.get("pulses") or {}
    wf = waveforms.get(waveform_name)
    inferred = _infer_pulse_length(pulses, waveform_name)
    length = inferred if inferred is not None else 16  # placeholder window
    length_inferred = inferred is None

    if not isinstance(wf, dict):
        return {
            "name": waveform_name,
            "kind": "unknown",
            "length_ns": length,
            "length_inferred": length_inferred,
            "x": list(range(length)),
            "y": [0.0] * length,
            "constant_value": None,
        }

    kind = wf.get("type")
    if kind == "constant":
        sample = float(wf.get("sample", 0.0))
        return {
            "name": waveform_name,
            "kind": "constant",
            "length_ns": length,
            "length_inferred": length_inferred,
            "x": list(range(length)),
            "y": [sample] * length,
            "constant_value": sample,
        }
    if kind == "arbitrary":
        samples = wf.get("samples") or []
        if not isinstance(samples, (list, tuple)):
            samples = list(samples)
        # Trust the array length; ignore the inferred length if there's a mismatch.
        n = len(samples)
        return {
            "name": waveform_name,
            "kind": "arbitrary",
            "length_ns": n,
            "length_inferred": False,
            "x": list(range(n)),
            "y": [float(s) for s in samples],
            "constant_value": None,
        }
    return {
        "name": waveform_name,
        "kind": "unknown",
        "length_ns": length,
        "length_inferred": length_inferred,
        "x": list(range(length)),
        "y": [0.0] * length,
        "constant_value": None,
    }


def _infer_pulse_length(pulses: dict, waveform_name: str) -> int | None:
    """Find the length of any pulse that references *waveform_name*.

    Returns ``None`` when no referencing pulse carries a positive int
    length — callers decide how to present the unknown.
    """
    for pulse in pulses.values():
        if not isinstance(pulse, dict):
            continue
        wfs = pulse.get("waveforms")
        if isinstance(wfs, dict):
            referenced = wfs.values()
        elif isinstance(wfs, str):
            referenced = (wfs,)
        else:
            continue
        if waveform_name in referenced:
            length = pulse.get("length")
            if isinstance(length, int) and length > 0:
                return length
    return None


def waveform_for_operation(
    config: dict, target_prefix: str, op_name: str, *, channel: str | None = None,
) -> dict | None:
    """Resolve every waveform that *op_name* on *target_prefix* plays.

    Returns one trace per entry in the pulse's ``waveforms`` — an IQ pulse
    (e.g. ``x180_DragCosine`` on ``xy``) yields both the I and Q sample
    arrays; a real-valued pulse (flux/z) yields its single trace. Trace
    order is ``single`` → ``I`` → ``Q`` → any remaining keys in dict order.

    Returns::

        {
          "element": str, "operation": str, "pulse": str,
          "traces": [
            {"label": "single"|"I"|"Q"|..., **resolve_waveform(...)},
            ...
          ],
        }

    or ``None`` if no matching operation (or no resolvable waveform) is found.
    """
    elements_dict = config.get("elements") or {}
    pulses_dict = config.get("pulses") or {}
    candidate_keys: list[str]
    if channel is not None:
        candidate_keys = [f"{target_prefix}.{channel}"]
    else:
        candidate_keys = _element_keys_for(config, target_prefix)
    for elem_key in candidate_keys:
        elem = elements_dict.get(elem_key) or {}
        ops = elem.get("operations") or {}
        if not isinstance(ops, dict) or op_name not in ops:
            continue
        pulse_name = ops[op_name]
        traces = _traces_for_pulse(config, pulses_dict.get(pulse_name) or {})
        if traces is None:
            return None
        return {
            "element": elem_key,
            "operation": op_name,
            "pulse": pulse_name,
            "traces": traces,
        }

    return None


def _traces_for_pulse(config: dict, pulse: dict) -> list[dict] | None:
    """Build the ordered I/Q/single trace list for one pulse, or None.

    Shared by :func:`waveform_for_operation` and
    :func:`pair_waveform_for_operation`. Trace order is
    ``single`` → ``I`` → ``Q`` → any remaining keys in dict order.
    """
    wfs = pulse.get("waveforms")
    pairs: list[tuple[str, str]] = []  # (label, waveform_name)
    if isinstance(wfs, dict):
        for key in ("single", "I", "Q"):
            if isinstance(wfs.get(key), str):
                pairs.append((key, wfs[key]))
        for key, val in wfs.items():
            if key not in ("single", "I", "Q") and isinstance(val, str):
                pairs.append((key, val))
    elif isinstance(wfs, str):
        pairs.append(("single", wfs))

    if not pairs:
        return None
    traces = []
    for label, wf_name in pairs:
        trace = resolve_waveform(config, wf_name)
        trace["label"] = label
        traces.append(trace)
    return traces


# ---------------------------------------------------------------------------
# Operation discovery (for "view waveform" links on detail pages)
# ---------------------------------------------------------------------------

def operations_for(config: dict, target_prefix: str) -> list[dict]:
    """List all operations under any channel of *target_prefix*.

    Used to render "view waveform" links: one entry per (element, op_name)
    pair. Returns ``[{"element", "channel", "op_name", "pulse"}, ...]``.
    """
    elements_dict = config.get("elements") or {}
    out: list[dict] = []
    for elem_key in _element_keys_for(config, target_prefix):
        # element key shape is "<target>.<channel>"
        try:
            _, channel = elem_key.rsplit(".", 1)
        except ValueError:
            channel = elem_key
        elem = elements_dict.get(elem_key) or {}
        ops = elem.get("operations") or {}
        if not isinstance(ops, dict):
            continue
        for op_name, pulse_name in ops.items():
            out.append({
                "element": elem_key,
                "channel": channel,
                "op_name": op_name,
                "pulse": pulse_name if isinstance(pulse_name, str) else None,
            })
    return out


# ---------------------------------------------------------------------------
# Pair (2-qubit-gate) resolution
# ---------------------------------------------------------------------------
# Unlike single qubits, a pair has no ``"<pair>.<channel>"`` elements in the
# generated config — quam-builder names 2Q gates one of two ways:
#   1. a dedicated element ``cr_<c>_<t>`` / ``zz_<c>_<t>`` / ``coupler_<c>_<t>``
#      (cross-resonance / ZZ / tunable-coupler), OR
#   2. an *operation* on the control qubit's flux line (``<c>.z``) whose name
#      references the partner — e.g. ``cz_unipolar_pulse_<t>`` or
#      ``cz_..._<c>-<t>`` (flux-tunable CZ).
# So matching a bare ``"<pair>."`` prefix (the old behaviour) always returned
# an empty slice. These helpers resolve the pair from its two qubit names.

_PAIR_GATE_PREFIXES = ("cr_", "zz_", "coupler_", "cz_")


def _name_tokens(name: str) -> set[str]:
    """Split a config name into qubit-name-bearing tokens.

    ``"cr_q0_q4"`` -> ``{cr, q0, q4}``; ``"cz_unipolar_pulse_qA1"`` ->
    ``{cz, unipolar, pulse, qA1}``; ``"cz_..._qA2-qA1"`` -> ``{..., qA2, qA1}``.
    Token boundaries are ``_ - . /`` so a qubit name is matched whole (``q1``
    never matches inside ``q12``).
    """
    return {t for t in re.split(r"[_\-./]", name) if t}


def _resolve_pair_elements_ops(
    config: dict, control: str, target: str, pair_name: str | None = None,
):
    """Find the config elements + operations that belong to one qubit pair.

    Returns ``(dedicated_element_keys, [(element, op_name, pulse_name), ...])``:
    every dedicated 2Q-gate element referencing both qubits, plus every
    operation on either qubit's own elements whose name references the partner.
    When *pair_name* is given, elements keyed by the pair itself
    (``"<pair>.coupler"`` — a layout some quam_builder versions emit) are also
    included, so this resolves both the modern and the legacy naming.
    """
    elements = config.get("elements") or {}
    dedicated: list[str] = []
    op_entries: list[tuple[str, str, str | None]] = []
    if not isinstance(elements, dict):
        return dedicated, op_entries

    legacy_prefix = f"{pair_name}." if pair_name else None

    for ekey, elem in elements.items():
        if not isinstance(elem, dict):
            continue
        ops = elem.get("operations")
        ops = ops if isinstance(ops, dict) else {}

        # Legacy: element keyed by the pair name itself ("<pair>.coupler").
        if legacy_prefix and ekey.startswith(legacy_prefix):
            dedicated.append(ekey)
            for opn, pulse in ops.items():
                op_entries.append(
                    (ekey, opn, pulse if isinstance(pulse, str) else None))
            continue

        if not control or not target:
            continue

        if ekey.startswith(_PAIR_GATE_PREFIXES):
            toks = _name_tokens(ekey)
            if control in toks and target in toks:
                dedicated.append(ekey)
                for opn, pulse in ops.items():
                    op_entries.append(
                        (ekey, opn, pulse if isinstance(pulse, str) else None))
            continue

        # Operation on a qubit's own element (e.g. "<c>.z") referencing the
        # partner — flux-tunable CZ lives here. Scope to this pair's qubits so
        # a control qubit shared by several pairs doesn't leak the others.
        base = ekey.split(".", 1)[0]
        if base == control or base == target:
            partner = target if base == control else control
            for opn, pulse in ops.items():
                if partner in _name_tokens(opn):
                    op_entries.append(
                        (ekey, opn, pulse if isinstance(pulse, str) else None))

    return dedicated, op_entries


def pair_operations_for(
    config: dict, control: str, target: str, pair_name: str | None = None,
) -> list[dict]:
    """List the 2Q-gate operations for a pair (for "view waveform" links).

    Returns ``[{"element", "channel", "op_name", "pulse"}, ...]`` spanning both
    dedicated gate elements and the partner-referencing ops on each qubit.
    """
    _dedicated, op_entries = _resolve_pair_elements_ops(
        config, control, target, pair_name)
    out: list[dict] = []
    for elem_key, op_name, pulse_name in op_entries:
        channel = elem_key.rsplit(".", 1)[1] if "." in elem_key else elem_key
        out.append({
            "element": elem_key,
            "channel": channel,
            "op_name": op_name,
            "pulse": pulse_name,
        })
    return out


def pair_slice_for(
    config: dict, control: str, target: str, pair_name: str | None = None,
) -> dict:
    """Return the config slice belonging to one qubit pair.

    ``elements`` holds the dedicated gate elements (empty for flux-tunable CZ,
    whose pulses live as ops on ``<c>.z``); ``pulses`` / ``waveforms`` /
    ``integration_weights`` are those referenced by ALL resolved pair ops.
    ``operation_count`` lets the caller distinguish "no 2Q gate for this pair"
    from "config not generated yet".
    """
    elements_dict = config.get("elements") or {}
    pulses_dict = config.get("pulses") or {}
    waveforms_dict = config.get("waveforms") or {}
    integration_dict = config.get("integration_weights") or {}

    dedicated, op_entries = _resolve_pair_elements_ops(
        config, control, target, pair_name)
    pulse_names = sorted({p for _e, _o, p in op_entries if p})
    waveform_names = _waveform_names_referenced_by(pulses_dict, pulse_names)

    iw_names: set[str] = set()
    for pname in pulse_names:
        pulse = pulses_dict.get(pname) or {}
        iws = pulse.get("integration_weights")
        if isinstance(iws, dict):
            for iw_name in iws.values():
                if isinstance(iw_name, str):
                    iw_names.add(iw_name)

    return {
        "elements": {k: elements_dict[k] for k in dedicated if k in elements_dict},
        "pulses": {n: pulses_dict[n] for n in pulse_names if n in pulses_dict},
        "waveforms": {
            n: waveforms_dict[n] for n in waveform_names if n in waveforms_dict
        },
        "integration_weights": {
            n: integration_dict[n] for n in sorted(iw_names) if n in integration_dict
        },
        "mixers": {},
        "operation_count": len(op_entries),
    }


def pair_waveform_for_operation(
    config: dict, control: str, target: str, op_name: str,
    *, element: str | None = None, pair_name: str | None = None,
) -> dict | None:
    """Resolve a pair operation's waveform traces (the "view waveform" payload).

    *element* disambiguates op names shared across a pair's elements (e.g.
    ``square`` exists on both ``cr_<c>_<t>`` and ``cr_<t>_<c>``).
    """
    pulses_dict = config.get("pulses") or {}
    _dedicated, op_entries = _resolve_pair_elements_ops(
        config, control, target, pair_name)
    for elem_key, opn, pulse_name in op_entries:
        if opn != op_name:
            continue
        if element is not None and elem_key != element:
            continue
        traces = _traces_for_pulse(config, pulses_dict.get(pulse_name) or {})
        if traces is None:
            continue
        return {
            "element": elem_key,
            "operation": op_name,
            "pulse": pulse_name,
            "traces": traces,
        }
    return None
