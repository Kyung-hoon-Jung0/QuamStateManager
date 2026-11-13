"""Gaussian-filtered CZ macro builder (docs/126 ⑦b).

The customer's ``add_gaussian_cz_macros.py`` workflow as an SM feature: pick a
pair that already carries a calibrated ``cz_flattop`` macro, and build the
``cz_gaussian_unipolar`` + ``cz_gaussian_bipolar`` CZGate macros from it —
amplitude and interaction duration sourced from ``cz_flattop`` (qubit flux
pulse amplitude / flat_length, and the coupler pulse amplitude when the pair
has a tunable coupler), plus the pointer-linked channel operations on the
moving qubit's z line (and the coupler) so the macro's pulse stays the single
source of truth.

Every serialized shape here is transcribed from the customer's own run of the
script on their live chip (pair q19-20) — the CZGate skeleton, the pulse field
sets (quam_builder 0.4 names: ``padding_length``), the op labels
(``<macro>_pulse`` / ``<macro>_coupler_pulse``), and the absolute pointer
grammar (``#/qubit_pairs/<pid>/macros/<macro>/flux_pulse_qubit/<field>``) —
and is structurally pinned against that chip by ``tests/test_gaussian_cz.py``.

This module is PURE: it validates the pair and returns the subtree values +
target paths; the route writes them through ``modifier.create_subtree`` under
ONE change group (one Ctrl+Z, one Review bundle). Nothing here touches live —
creation lands in the working copy like every other edit (docs/107 covenant).
"""
from __future__ import annotations

import re
from typing import Any

from quam_state_manager.core.add_names import pulse_name
from quam_state_manager.core.pointer_path import resolve_field_target

_QB_COMMON = "quam_builder.common.pulses."
_QB_ARCH = "quam_builder.architecture.superconducting.components.pulses."
_CZ_GATE = ("quam_builder.architecture.superconducting.custom_gates."
            "flux_tunable_transmon_pair.two_qubit_gates.CZGate")

# (macro name, pulse __class__) — the two variants the script builds.
VARIANTS = (
    ("cz_gaussian_unipolar", _QB_COMMON + "GaussianFilteredSquarePulse"),
    ("cz_gaussian_bipolar", _QB_ARCH + "GaussianFilteredSymmetricBipolarPulse"),
)


_MACRO_REF = re.compile(r"^#/qubit_pairs/([^/]+)/macros/")


def _resolve_number(merged: dict, path: str):
    """A numeric leaf at *path*, following pointer chains; None if not."""
    try:
        ft = resolve_field_target(merged, path)
    except Exception:  # noqa: BLE001 — a broken chain is "not a number"
        return None
    v = ft.get("resolved_value") if ft.get("resolvable") else None
    return v if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def _qubit_name(merged: dict, pair_id: str, role_key: str) -> str | None:
    """The qubit NAME a pair's control/target reference points at.

    Follows the whole chain (modern chips route through
    ``#/wiring/qubit_pairs/<p>/c/control_qubit`` — docs/118) and takes the
    resolved path's last segment under ``qubits.``.
    """
    try:
        ft = resolve_field_target(merged, f"qubit_pairs.{pair_id}.{role_key}")
    except Exception:  # noqa: BLE001
        return None
    rp = ft.get("resolved_path") or ""
    parts = rp.split(".")
    if len(parts) == 2 and parts[0] == "qubits":
        return parts[1]
    return None


def _pulse(cls: str, *, pulse_id: str | None, pulse_length, padding_length,
           amplitude, filter_mhz) -> dict[str, Any]:
    return {
        "length": "#./inferred_length",
        "id": pulse_id,
        "digital_marker": None,
        "pulse_length": pulse_length,
        "padding_length": padding_length,
        "amplitude": amplitude,
        "gaussian_filter_frequency_mhz": filter_mhz,
        "sample_rate": 1000000000.0,
        "axis_angle": None,
        "__class__": cls,
    }


def _linked_pulse(cls: str, macro_ref: str) -> dict[str, Any]:
    """The channel-op twin: every calibrated field POINTS at the macro's pulse."""
    return _pulse(
        cls, pulse_id=None,
        pulse_length=macro_ref + "/pulse_length",
        padding_length=macro_ref + "/padding_length",
        amplitude=macro_ref + "/amplitude",
        filter_mhz=macro_ref + "/gaussian_filter_frequency_mhz",
    )


def eligible_pairs(merged: dict) -> list[dict[str, Any]]:
    """Pairs that carry a ``cz_flattop`` to source from (for the picker)."""
    out = []
    for pid, p in (merged.get("qubit_pairs") or {}).items():
        if not isinstance(p, dict):
            continue
        macros = p.get("macros")
        if not isinstance(macros, dict) or not isinstance(
                macros.get("cz_flattop"), dict):
            continue
        out.append({"pair_id": pid,
                    "has_coupler": isinstance(p.get("coupler"), dict),
                    "existing": [name for name, _ in VARIANTS
                                 if isinstance(macros.get(name), dict)]})
    return out


def plan(merged: dict, pair_id: str, *, padding_length: int = 20,
         qubit_filter_mhz: float = 20.0,
         coupler_filter_mhz: float = 20.0,
         suffix_ids: tuple[str, str] | None = None,
         own_names: bool = False) -> dict[str, Any]:
    """Validate + build. Returns ``{"creates": [(path, value)], "existing":
    [...], "sources": {...}}`` or ``{"error": str}`` — never raises on chip
    shape (every refusal names what is missing, the script's own guard set).

    ``own_names=True`` (the single-pair door) names every operation after
    this pair's own control and target, the same names the several-pairs
    path writes, so two pairs sharing a moving qubit never share an
    operation. Unlike an explicit ``suffix_ids``, what THIS pair already
    owns stays replaceable (``existing``); an operation another pair owns is
    refused with ``conflict`` naming that pair. A bare-named operation left
    by an older build is removed on replace only when this pair owns it."""
    if suffix_ids is not None and (not isinstance(suffix_ids, (tuple, list)) or len(suffix_ids) != 2):
        return {"error": "suffix_ids must contain control and target"}
    pairs = merged.get("qubit_pairs") or {}
    p = pairs.get(pair_id)
    if not isinstance(p, dict):
        return {"error": f"Pair '{pair_id}' not found."}
    macros = p.get("macros") if isinstance(p.get("macros"), dict) else {}
    flattop = macros.get("cz_flattop")
    if not isinstance(flattop, dict):
        return {"error": f"Pair '{pair_id}' has no 'cz_flattop' macro to "
                         "source amplitude/duration from."}

    base = f"qubit_pairs.{pair_id}.macros.cz_flattop"
    qubit_amp = _resolve_number(merged, base + ".flux_pulse_qubit.amplitude")
    flat_len = _resolve_number(merged, base + ".flux_pulse_qubit.flat_length")
    if qubit_amp is None or flat_len is None:
        return {"error": f"Pair '{pair_id}': cz_flattop.flux_pulse_qubit must "
                         "carry numeric amplitude and flat_length "
                         f"(got amplitude={qubit_amp!r}, flat_length={flat_len!r})."}

    role = p.get("moving_qubit")
    if role not in ("control", "target"):
        return {"error": f"Pair '{pair_id}' records no moving_qubit role — "
                         "the z operations have no home."}
    mq = _qubit_name(merged, pair_id,
                     "qubit_control" if role == "control" else "qubit_target")
    if not mq:
        return {"error": f"Pair '{pair_id}': the {role} qubit reference does "
                         "not resolve to a qubit."}
    z = ((merged.get("qubits") or {}).get(mq) or {}).get("z")
    if not isinstance(z, dict) or not isinstance(z.get("operations"), dict):
        return {"error": f"Qubit '{mq}' (the moving qubit) has no z "
                         "operations to host the pulse — a QDAC-biased z "
                         "line cannot carry OPX flux pulses (docs/119)."}

    has_coupler = (isinstance(p.get("coupler"), dict)
                   and isinstance(flattop.get("coupler_flux_pulse"), dict))
    coupler_amp = None
    if has_coupler:
        coupler_amp = _resolve_number(
            merged, base + ".coupler_flux_pulse.amplitude")
        if coupler_amp is None:
            return {"error": f"Pair '{pair_id}': cz_flattop.coupler_flux_pulse"
                             ".amplitude is not numeric."}
        cops = (p.get("coupler") or {}).get("operations")
        if not isinstance(cops, dict):
            return {"error": f"Pair '{pair_id}': the coupler has no "
                             "operations dict."}

    actual = tuple(_qubit_name(merged, pair_id, "qubit_" + r) for r in ("control", "target"))
    if own_names and suffix_ids is None:
        if not all(actual):
            return {"error": f"Pair '{pair_id}': the control/target references "
                             "do not resolve to qubits."}
        suffix_ids = actual
        strict = False
    else:
        strict = suffix_ids is not None
        if strict and tuple(suffix_ids) != actual:
            return {"error": "suffix_ids must match this pair's control and target"}
    rev_cache: dict[str, Any] = {}
    notes: list[str] = []

    def _owners(op_path: str, value: Any) -> set[str]:
        # Which pairs this operation belongs to: the macros its own pointers
        # name, plus the macros pointing at it. An alias (the op itself is a
        # pointer string) is a deliberate share, never another pair's.
        if not isinstance(value, dict):
            return set()
        found: set[str] = set()

        def scan(v):
            if isinstance(v, dict):
                for x in v.values():
                    scan(x)
            elif isinstance(v, str):
                m = _MACRO_REF.match(v)
                if m:
                    found.add(m.group(1))
        scan(value)
        from quam_state_manager.core.pulse_index import (
            build_reverse_pointer_index, used_by)
        if "rev" not in rev_cache:
            rev_cache["rev"] = build_reverse_pointer_index(merged)
        for holder in used_by(merged, op_path, rev_cache["rev"]):
            seg = holder.split(".")
            if len(seg) >= 3 and seg[0] == "qubit_pairs" and seg[2] == "macros":
                found.add(seg[1])
        return found

    def _claim(op_path: str, value: Any) -> dict[str, Any] | None:
        # own_names: an existing operation is this pair's to replace, or
        # another pair's to leave alone (a refusal naming that pair).
        others = sorted(_owners(op_path, value) - {pair_id})
        if others:
            return {"error": (f"{op_path.rsplit('.', 1)[-1]} on "
                              f"{op_path.split('.')[1]} belongs to pair "
                              f"{', '.join(others)}; it is left alone. "
                              "Rename or remove it there first."),
                    "conflict": True, "owner": others[0]}
        existing.append(op_path)
        return None

    creates: list[tuple[str, Any]] = []
    existing: list[str] = []
    for gname, pulse_cls in VARIANTS:
        try:
            qname = f"{gname}_pulse" if suffix_ids is None else pulse_name(gname, "pulse", *suffix_ids)
            cname = f"{gname}_coupler_pulse" if suffix_ids is None else pulse_name(gname, "coupler_pulse", *suffix_ids)
        except ValueError as exc:
            return {"error": str(exc), "status": "Can't"}
        macro_path = f"qubit_pairs.{pair_id}.macros.{gname}"
        if isinstance(macros.get(gname), dict):
            existing.append(macro_path)
        if not strict and suffix_ids is not None:
            # An older build's bare-named op: removed with this pair's own
            # set on replace, never when another pair's macro uses it.
            coupler = p.get("coupler") if isinstance(p.get("coupler"), dict) else {}
            for legacy, host, ops in (
                    (f"{gname}_pulse", f"qubits.{mq}.z.operations",
                     z.get("operations")),
                    (f"{gname}_coupler_pulse",
                     f"qubit_pairs.{pair_id}.coupler.operations",
                     coupler.get("operations"))):
                if not isinstance(ops, dict) or legacy not in ops:
                    continue
                lpath = f"{host}.{legacy}"
                lowners = _owners(lpath, ops[legacy])
                if lowners == {pair_id}:
                    existing.append(lpath)
                elif lowners:
                    notes.append(f"{legacy} on {host.split('.')[1]} stays "
                                 f"with pair {', '.join(sorted(lowners))}")
        macro_ref = f"#/qubit_pairs/{pair_id}/macros/{gname}"
        macro = {
            "id": "#./inferred_id",
            "fidelity": {},
            "duration": "#./inferred_duration",
            "flux_pulse_qubit": _pulse(
                pulse_cls, pulse_id=qname, pulse_length=flat_len,
                padding_length=padding_length, amplitude=qubit_amp,
                filter_mhz=qubit_filter_mhz),
            "coupler_flux_pulse": (_pulse(
                pulse_cls, pulse_id=cname,
                pulse_length=flat_len, padding_length=padding_length,
                amplitude=coupler_amp, filter_mhz=coupler_filter_mhz)
                if has_coupler else None),
            "phase_shift_control": 0.0,
            "phase_shift_target": 0.0,
            "spectator_qubits": {},
            "spectator_qubits_control": {},
            "spectator_qubits_phase_shift": {},
            "extras": {},
            "duration_qubit": None,
            "__class__": _CZ_GATE,
        }
        creates.append((macro_path, macro))

        z_op_path = f"qubits.{mq}.z.operations.{qname}"
        if qname in (z.get("operations") or {}):
            if strict:
                return {"error": f"{mq} z already carries {qname} from another pair", "status": "Can't"}
            if suffix_ids is not None:
                refused = _claim(z_op_path, z["operations"][qname])
                if refused:
                    return refused
            else:
                existing.append(z_op_path)
        creates.append((z_op_path,
                        _linked_pulse(pulse_cls, macro_ref + "/flux_pulse_qubit")))

        if has_coupler:
            c_op_path = (f"qubit_pairs.{pair_id}.coupler.operations."
                         f"{cname}")
            cops_now = (p.get("coupler") or {}).get("operations") or {}
            if cname in cops_now:
                if not strict and suffix_ids is not None:
                    refused = _claim(c_op_path, cops_now[cname])
                    if refused:
                        return refused
                else:
                    existing.append(c_op_path)
            creates.append((c_op_path,
                            _linked_pulse(pulse_cls,
                                          macro_ref + "/coupler_flux_pulse")))

    if strict and existing:
        return {"error": f"a gate or operation named {existing[0].rsplit('.', 1)[-1]} already exists",
                "status": "Can't"}
    return {
        "creates": creates,
        "existing": existing,
        "sources": {"moving_qubit": mq, "role": role,
                    "qubit_amplitude": qubit_amp,
                    "flat_length": flat_len,
                    "coupler_amplitude": coupler_amp,
                    "has_coupler": has_coupler,
                    "notes": notes},
    }
