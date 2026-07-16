"""Port-label-mapping CSV import for the Generate wizard (docs/54).

The QM fixed-transmon (CR) reference flow generates chips from a
``port-label-mapping.csv`` — a per-qubit table of chip grid position, readout
mux, and OPX1000 chassis/FEM/port, with per-mux ``readout to/from`` rows for
the shared feedline. This module is a **stdlib-only transcription of that
flow's ``csv_layout.py``** so the wizard can ingest the customer's exact file
and pre-fill instruments, qubits, grid, directed pairs, and port pins — making
the wizard a full replacement for the script pipeline.

Parsing rules preserved from the source (each is load-bearing on real files):
``utf-8-sig`` (Excel BOM), header whitespace-stripping, blank separator rows
skipped, ``IN2`` → input port 2, per-mux readout out/in rows must share one
controller+FEM, ``(fem, 1, 1)`` readout fallback when a mux lacks readout
rows, the y-flipped ``grid_location`` (``"{col},{maxRow-row}"``), and
grid-adjacent pairs emitted in BOTH directions (CR is directional).
"""

from __future__ import annotations

import csv
import io
from typing import Any

__all__ = ["parse_port_label_csv"]

# The wizard's qubit-id shape (matches generate.js's naming validation).
_MAX_QUBITS_PER_MUX = 8      # readout feedline multiplex bound (one MW in/out)


def _norm_row(row: dict) -> dict:
    return {(k or "").strip(): (v or "").strip() for k, v in row.items()
            if k is not None}


def _parse_port(port: str) -> tuple[int, bool]:
    """``"IN2"`` → ``(2, True)`` (input); ``"3"`` → ``(3, False)``."""
    if port.upper().startswith("IN"):
        return int(port[2:]), True
    return int(port), False


def parse_port_label_csv(text: str) -> dict:
    """Parse a port-label CSV → the wizard prefill payload.

    Returns::

        {"ok": bool, "errors": [str, ...],           # ok=False → errors only
         "instruments": {"controllers": [{"con", "fems": [{"slot", "fem"}]}]},
         "qubits": ["q0", ...],                      # chip-position order
         "grid": {"q0": "0,0", ...},                 # y-flipped, board-ready
         "qubit_pairs": [["q0","q1"], ["q1","q0"], ...],   # BOTH directions
         "pins": {"q0": {"drive": {...}, "resonator": {...}}},
         "feedlines": {"q0": "mux0_0"},              # resonator group ids
         "warnings": [str, ...]}
    """
    errors: list[str] = []
    warnings: list[str] = []

    try:
        rows = [_norm_row(r) for r in csv.DictReader(io.StringIO(text.lstrip("﻿")))]
    except csv.Error as exc:
        return {"ok": False, "errors": [f"not parseable as CSV: {exc}"]}

    required = {"Chip qubit index", "chip control row", "chip control column",
                "mux row", "mux column", "chip port",
                "QM chassis", "QM FEM", "QM port"}
    have = set(rows[0].keys()) if rows else set()
    missing = required - have
    if missing:
        return {"ok": False, "errors": [
            "missing CSV columns: " + ", ".join(sorted(missing))
            + " — expected the port-label-mapping layout"]}

    # --- qubit rows -----------------------------------------------------
    entries: list[dict] = []
    seen_ports: dict[tuple, str] = {}       # (con, fem, port, is_input) -> owner
    seen_idx: dict[int, int] = {}           # chip qubit index -> csv row number
    for i, row in enumerate(rows):
        if not row.get("chip control row") or not row.get("chip control column"):
            continue                          # separator / readout row
        try:
            idx = int(row["Chip qubit index"])
            pos = (int(row["chip control row"]), int(row["chip control column"]))
            mux = (int(row["mux row"]), int(row["mux column"]))
            con = int(row["QM chassis"])
            fem = int(row["QM FEM"])
            port, is_in = _parse_port(row["QM port"])
        except (KeyError, ValueError) as exc:
            errors.append(f"row {i + 2}: unparseable qubit row ({exc})")
            continue
        if is_in:
            errors.append(f"row {i + 2}: a control port cannot be an input (IN…)")
            continue
        if idx in seen_idx:
            errors.append(f"row {i + 2}: chip qubit index {idx} already used "
                          f"by row {seen_idx[idx]} — duplicate qubit ids "
                          "would corrupt the wizard spec")
            continue
        seen_idx[idx] = i + 2
        name = f"q{idx}"
        key = (con, fem, port, False)
        if key in seen_ports:
            errors.append(
                f"row {i + 2}: port con{con}/{fem}/{port} already used by "
                f"{seen_ports[key]}")
        seen_ports[key] = name
        entries.append({"idx": idx, "name": name, "pos": pos, "mux": mux,
                        "con": con, "fem": fem, "port": port})

    if not entries and not errors:
        errors.append("no qubit rows found (chip control row/column empty everywhere)")
    if errors:
        return {"ok": False, "errors": errors}

    dup_pos: dict[tuple, str] = {}
    for e in entries:
        if e["pos"] in dup_pos:
            errors.append(f"{e['name']}: grid position {e['pos']} already held "
                          f"by {dup_pos[e['pos']]}")
        dup_pos[e["pos"]] = e["name"]

    # --- readout rows (per mux) ------------------------------------------
    selected_muxes = {e["mux"] for e in entries}
    readout: dict[tuple, dict] = {}
    for i, row in enumerate(rows):
        cp = row.get("chip port", "").lower()
        if not cp.startswith("readout "):
            continue
        try:
            mux = (int(row["mux row"]), int(row["mux column"]))
        except (KeyError, ValueError):
            continue
        if mux not in selected_muxes:
            continue
        endpoint = "out" if cp.startswith("readout to") else "in"
        try:
            con = int(row["QM chassis"])
            fem = int(row["QM FEM"])
            port, is_in = _parse_port(row["QM port"])
        except (KeyError, ValueError) as exc:
            errors.append(f"row {i + 2}: unparseable readout row ({exc})")
            continue
        # Keep the DIRECTION: MW-FEM input N and output N are distinct
        # physical ports — "out 1 / IN1" is the standard feedline pairing
        # (and this module's own fallback), never a same-port typo.
        readout.setdefault(mux, {})[endpoint] = (con, fem, port, is_in)
    for mux, ep in readout.items():
        if ("in" in ep and "out" in ep and ep["in"] == ep["out"]):
            errors.append(f"mux {mux}: readout in and out name the same "
                          "directed port")
        if ("in" in ep and "out" in ep
                and ep["in"][:2] != ep["out"][:2]):
            errors.append(f"mux {mux}: readout in/out rows use different "
                          "controller/FEM")
    if errors:
        return {"ok": False, "errors": errors}

    entries.sort(key=lambda e: e["pos"])
    grid_max_row = max(e["pos"][0] for e in entries)

    # --- assemble the payload --------------------------------------------
    fems: dict[int, set[int]] = {}
    for e in entries:
        fems.setdefault(e["con"], set()).add(e["fem"])
    for ep in readout.values():
        for con, fem, _p, _is_in in ep.values():
            fems.setdefault(con, set()).add(fem)

    qubits = [e["name"] for e in entries]
    grid = {e["name"]: f"{e['pos'][1]},{grid_max_row - e['pos'][0]}"
            for e in entries}

    mux_members: dict[tuple, list[str]] = {}
    pins: dict[str, dict] = {}
    feedlines: dict[str, str] = {}
    for e in entries:
        mux_members.setdefault(e["mux"], []).append(e["name"])
        pin: dict[str, Any] = {"drive": {"kind": "mw_fem", "con": e["con"],
                                         "slot": e["fem"], "out_port": e["port"]}}
        ep = readout.get(e["mux"])
        if ep and "in" in ep and "out" in ep:
            (ocon, ofem, oport, _oin) = ep["out"]
            (_icon, _ifem, iport, _iin) = ep["in"]
            pin["resonator"] = {"kind": "mw_fem", "con": ocon, "slot": ofem,
                                "out_port": oport, "in_port": iport}
        else:
            # source-flow fallback: same FEM, out 1 / in 1
            pin["resonator"] = {"kind": "mw_fem", "con": e["con"],
                                "slot": e["fem"], "out_port": 1, "in_port": 1}
            warnings.append(f"mux {e['mux']}: no readout rows — {e['name']}'s "
                            "feedline pinned to the FEM's port 1 (source-flow "
                            "fallback); review step 5.")
        pins[e["name"]] = pin
        feedlines[e["name"]] = f"mux{e['mux'][0]}_{e['mux'][1]}"

    for mux, members in mux_members.items():
        if len(members) > _MAX_QUBITS_PER_MUX:
            errors.append(f"mux {mux}: {len(members)} qubits on one feedline — "
                          f"the readout multiplex bound is {_MAX_QUBITS_PER_MUX}")
    if errors:
        return {"ok": False, "errors": errors}

    # grid-adjacent pairs, BOTH directions (CR is control→target directional)
    by_pos = {e["pos"]: e["name"] for e in entries}
    pairs: list[list[str]] = []
    for e in entries:
        r, c = e["pos"]
        for dr, dc in ((1, 0), (0, 1)):
            nb = by_pos.get((r + dr, c + dc))
            if nb is not None:
                pairs.append([e["name"], nb])
                pairs.append([nb, e["name"]])

    controllers = [{"con": con, "fems": [{"slot": s, "fem": "mw"}
                                         for s in sorted(slots)]}
                   for con, slots in sorted(fems.items())]

    return {
        "ok": True,
        "errors": [],
        "instruments": {"controllers": controllers, "opx_plus": [], "octaves": []},
        "qubits": qubits,
        "grid": grid,
        "qubit_pairs": pairs,
        "pins": pins,
        "feedlines": feedlines,
        "warnings": warnings,
    }
