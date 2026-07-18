"""Coupled power-write rows for resonator-spectroscopy-vs-power applies.

The rvp node's own update is ATOMIC across three kinds of state fields
(real-archive confirmed — KRISS #565/#599, KRISS_CR #9):

    resonator f_01/RF_frequency            (per fitted qubit)
    resonator readout amplitude            (per fitted qubit)
    port full_scale_power_dbm              (SHARED per feedline)
    sibling readout amplitudes             (power-preserving rescale when the
                                            shared FSP moves — every other
                                            resonator on the same line)

with the exact identity  P_dbm = FSP + 20*log10(amp)  connecting the patched
amp+FSP to fit_results.optimal_power (diff 0.0 on #599/#568), and the sibling
rescale  amp *= 10**((FSP_old - FSP_new)/20)  bit-exact on #599 qA6 (value
1.2056… — the node itself writes amp > 1).

Applying only the frequency half of that update is a PARTIAL write: the
readout power calibration silently de-couples. This module builds the full
coupled row set — but ONLY from node-authored numbers:

  * ``target_amplitude`` + ``target_full_scale_power_dbm`` + ``readout_line``
    must all be present in the fresh fit entry (the hardened node variant
    computes the dBm→(FSP, amp) split in its OWN analysis and records it).
  * Envelopes without them (older node variants — #565's −3 dB backoff shows
    the split is node-version-dependent) are REFUSED with a reason: the SM
    never re-derives the split itself (docs/47 doctrine: calibration numbers
    come from the node's own fitter).
  * qubit_spectroscopy_vs_power is refused outright: the plain FSP identity
    provably does NOT hold there (#12: constant +3.98 dB offset) — it is a
    resonator-node convention, not a universal law.

A refusal never blocks the frequency rows; it is surfaced so the UI can say
"power not applied: <reason>" — no SILENT partial write.
"""
from __future__ import annotations

import math
from typing import Any

# the only family whose node encodes power as (shared FSP, per-qubit amp)
# with the plain P = FSP + 20*log10(amp) identity (real-archive verified)
POWER_COUPLED_FAMILY = "resonator_spectroscopy_vs_power"

_MAX_REF_HOPS = 8


def _get(state: dict, dotted: str) -> Any:
    node: Any = state
    for part in dotted.split("."):
        node = node[part]
    return node


def _resolve_ref_path(state: dict, dotted: str) -> str | None:
    """Follow ``#/…`` reference strings from *dotted* to the final dotted
    path (not the value). Returns None when the walk dies or loops."""
    path = dotted
    for _ in range(_MAX_REF_HOPS):
        try:
            val = _get(state, path)
        except (KeyError, TypeError):
            return None
        if isinstance(val, str) and val.startswith("#/"):
            path = val[2:].replace("/", ".")
            continue
        return path
    return None


def _is_num(v: Any) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool) \
        and math.isfinite(v)


def _line_token(port_path: str) -> str:
    """'ports.mw_outputs.con1.1.1' → 'con1/1/1' (the node's readout_line)."""
    parts = port_path.split(".")
    return "/".join(parts[2:]) if parts[:2] == ["ports", "mw_outputs"] else ""


def _resonator_port(state: dict, qubit: str) -> str | None:
    return _resolve_ref_path(state, f"qubits.{qubit}.resonator.opx_output")


def coupled_power_rows(fam_key: str, target: str, fresh: dict,
                       state: dict) -> dict:
    """Build the coupled power rows for one fitted target.

    Returns ``{"rows": [...], "skipped": reason|None, "warnings": [...],
    "port_path": str|None}``. ``rows`` follow the resolve_updates shape
    (``{path, value, old_hint, label, op, kind}``) with kinds
    ``power_amp`` (the fitted qubit), ``power_fsp`` (the shared port) and
    ``power_rescale`` (power-preserving sibling amps). Empty rows + a
    ``skipped`` reason = the honest refusal (frequency-only apply, disclosed).
    """
    out: dict[str, Any] = {"rows": [], "skipped": None, "warnings": [],
                           "port_path": None}

    def refuse(reason: str) -> dict:
        out["skipped"] = reason
        out["rows"] = []
        return out

    if fam_key != POWER_COUPLED_FAMILY:
        return refuse("power coupling is only defined for resonator "
                      "spectroscopy vs power (the FSP identity does not hold "
                      "for other nodes)")
    amp = fresh.get("target_amplitude")
    fsp = fresh.get("target_full_scale_power_dbm")
    line = fresh.get("readout_line")
    if not (_is_num(amp) and _is_num(fsp) and isinstance(line, str) and line):
        return refuse("node did not record target_amplitude / "
                      "target_full_scale_power_dbm / readout_line — the "
                      "dBm→amp split is node-version-dependent, so power is "
                      "not applied (re-measure with the current node to "
                      "calibrate power)")
    if amp <= 0:
        return refuse(f"node-authored target_amplitude {amp!r} is not positive")

    port_path = _resonator_port(state, target)
    if not port_path:
        return refuse(f"readout port for {target} not resolvable from state")
    token = _line_token(port_path)
    if token != line:
        return refuse(f"envelope readout_line {line!r} does not match the "
                      f"chip's wiring ({token or port_path!r}) — different "
                      "chip or rewired feedline")
    fsp_path = f"{port_path}.full_scale_power_dbm"
    try:
        fsp_cur = _get(state, fsp_path)
    except (KeyError, TypeError):
        return refuse(f"{fsp_path} not present in state")
    if not _is_num(fsp_cur):
        return refuse(f"current {fsp_path} is not a literal number")

    amp_path = f"qubits.{target}.resonator.operations.readout.amplitude"
    try:
        amp_cur = _get(state, amp_path)
    except (KeyError, TypeError):
        return refuse(f"{amp_path} not present in state")
    if not _is_num(amp_cur):
        return refuse(f"current {amp_path} is not a literal number — "
                      "refusing to overwrite a non-numeric (pointer?) value")

    fsp_moves = float(fsp) != float(fsp_cur)

    # the feedline is the apply unit: when FSP moves, EVERY sibling resonator
    # amp must be rescaled or the write is partial. Enumerate first so a
    # non-rescalable sibling refuses the whole block (never a partial line).
    rescales: list[tuple[str, str, float]] = []
    if fsp_moves:
        factor = 10.0 ** ((float(fsp_cur) - float(fsp)) / 20.0)
        for q in (state.get("qubits") or {}):
            if q == target:
                continue
            if _resonator_port(state, q) != port_path:
                continue
            sib_path = f"qubits.{q}.resonator.operations.readout.amplitude"
            try:
                sib_cur = _get(state, sib_path)
            except (KeyError, TypeError):
                continue                      # no readout op — not a member
            if not _is_num(sib_cur):
                return refuse(
                    f"feedline sibling {q}'s readout amplitude is not a "
                    "literal number — a partial feedline rescale would "
                    "silently shift its power")
            rescales.append((q, sib_path, sib_cur * factor))

    rows = [{"path": amp_path, "value": amp, "old_hint": amp_cur,
             "label": "Readout amplitude (node-authored)", "op": "assign",
             "kind": "power_amp"}]
    if _is_num(amp) and abs(amp) > 1.0:
        out["warnings"].append(
            f"{target} readout amplitude {amp:.4g} exceeds 1.0 (DAC range)")
    if fsp_moves:
        rows.append({"path": fsp_path, "value": fsp, "old_hint": fsp_cur,
                     "label": f"Feedline full-scale power ({line})",
                     "op": "assign", "kind": "power_fsp"})
        for q, sib_path, new_amp in rescales:
            rows.append({"path": sib_path, "value": new_amp,
                         "old_hint": _get(state, sib_path),
                         "label": f"{q} readout amplitude (power-preserving "
                                  "rescale)",
                         "op": "assign", "kind": "power_rescale"})
            if abs(new_amp) > 1.0:
                out["warnings"].append(
                    f"{q} rescaled readout amplitude {new_amp:.4g} exceeds "
                    "1.0 (DAC range) — the node itself writes such values; "
                    "verify readout on that qubit")
    out["rows"] = rows
    out["port_path"] = port_path
    return out
