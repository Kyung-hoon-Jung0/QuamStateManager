"""Recover the per-GATE fidelity a Standard-RB run computed and then discarded.

`StandardRB` in state.json is **1 - EPC**: a per-CLIFFORD number. The per-GATE
number needs a divisor::

    epg = epc / average_gates_per_clifford
    average_gate_fidelity = 1 - epg

The lab's node computes exactly that (`two_qubit_rb/fidelity.py:87`) and then
stores only the Clifford one, so the divisor is nowhere on the chip. It IS in
the run that produced the number — and the chip records which run that was, in
`fidelity["StandardRB_load_id"]`.

So this module follows that id to the run's `data.json` and reads the answer the
node already worked out. It does not recompute anything: `average_gate_fidelity`
is taken as the run stated it. On this chip the divisor is **5.371** — a Clifford
is over five two-qubit gates — which is why guessing it was never an option.

Everything here degrades to ``None``. A run folder that is not loaded, a run
written by an older node with no `average_gate_fidelity`, a pair the run did not
measure: all of those mean "no derived number", and the caller keeps showing the
Clifford value labelled as a Clifford value. Silence is not a failure mode here;
inventing a gate fidelity would be.
"""

from __future__ import annotations

import json
import logging
from typing import Any

logger = logging.getLogger(__name__)

__all__ = ["pair_key_variants", "from_run_folder", "derive_for_edges"]

#: What `fit_results` may call a per-gate fidelity, best first. `epg` is the
#: error; the fidelity is 1 - epg, and only used when the fidelity itself is
#: absent (an older node stored one and not the other).
_FIDELITY_KEYS = ("average_gate_fidelity",)
_ERROR_KEYS = ("epg",)


def pair_key_variants(pair_id: str) -> list[str]:
    """Every spelling a run's `fit_results` might key this pair under.

    A pair is `q19-20` on the chip and could be `q19-q20` in a run — the target
    drops or keeps its leading `q` depending on which layer wrote it. Trying one
    spelling is how a whole class of data goes silently missing (docs/136 §18,
    docs/137 §3).
    """
    out = [pair_id]
    if "-" in pair_id:
        control, _, target = pair_id.partition("-")
        for cand in (f"{control}-{target.lstrip('qQ')}",
                     f"{control}-q{target.lstrip('qQ')}"):
            if cand not in out:
                out.append(cand)
    return out


def from_run_folder(folder: Any, pair_id: str) -> dict | None:
    """``{"average_gate_fidelity", "epg", "average_gates_per_clifford"}`` or None.

    *folder* is the run's directory (anything ``os.PathLike``/str). Reads
    ``data.json`` only — the fit is already summarised there, and the HDF5
    cubes beside it are megabytes for a number the run has spelled out.
    """
    if folder is None:
        return None
    try:
        from pathlib import Path

        path = Path(folder) / "data.json"
        if not path.is_file():
            return None
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        # A run mid-write, a truncated file, a folder that vanished. Not an
        # error worth surfacing: the caller simply has no derived number.
        logger.debug("rb_gate_fidelity: could not read %s", folder, exc_info=True)
        return None
    if not isinstance(payload, dict):
        return None

    fits = payload.get("fit_results")
    if not isinstance(fits, dict):
        return None
    entry = None
    for key in pair_key_variants(str(pair_id)):
        cand = fits.get(key)
        if isinstance(cand, dict):
            entry = cand
            break
    if entry is None:
        return None

    def _num(value: Any) -> float | None:
        return (value if isinstance(value, (int, float))
                and not isinstance(value, bool) else None)

    fidelity = next((_num(entry.get(k)) for k in _FIDELITY_KEYS
                     if _num(entry.get(k)) is not None), None)
    epg = next((_num(entry.get(k)) for k in _ERROR_KEYS
                if _num(entry.get(k)) is not None), None)
    if fidelity is None and epg is not None:
        fidelity = 1.0 - epg
    if fidelity is None:
        return None
    # A fidelity outside (0, 1] is a broken fit, not a measurement. The topology
    # already quarantines those; do not hand one out.
    if not (0.0 < fidelity <= 1.0):
        return None
    out = {"average_gate_fidelity": fidelity,
           "average_gates_per_clifford": _num(entry.get("average_gates_per_clifford"))}
    if epg is not None:
        out["epg"] = epg
    return out


def derive_for_edges(edges: Any, resolve_run) -> int:
    """Attach a derived per-gate fidelity to every Standard-RB row, IN PLACE.

    *resolve_run* is ``load_id -> run folder or None`` — supplied by the caller
    so this module stays free of the dataset layer (and so a test can hand it a
    dict). Returns how many rows were enriched, for the caller to log.

    Each enriched row gains ``derived_gate_fidelity`` and the divisor that
    produced it. The Clifford value in ``value`` is left ALONE: the two are
    different measurements of the same gate and the UI shows which is which.
    """
    if not isinstance(edges, list):
        return 0
    cache: dict = {}
    hits = 0
    for edge in edges:
        if not isinstance(edge, dict):
            continue
        pair_id = str(edge.get("pair_id") or "")
        for row in (edge.get("gate_fidelities") or []):
            if not isinstance(row, dict) or row.get("level") != "clifford":
                continue
            load_id = row.get("load_id")
            if load_id is None:
                continue
            key = (load_id, pair_id)
            if key not in cache:
                try:
                    folder = resolve_run(load_id)
                except Exception:  # noqa: BLE001 — a lookup never breaks a render
                    logger.debug("rb_gate_fidelity: resolve_run(%r) failed",
                                 load_id, exc_info=True)
                    folder = None
                cache[key] = from_run_folder(folder, pair_id)
            found = cache[key]
            if found:
                row["derived_gate_fidelity"] = found["average_gate_fidelity"]
                row["derived_from_run"] = load_id
                if found.get("average_gates_per_clifford") is not None:
                    row["average_gates_per_clifford"] = found["average_gates_per_clifford"]
                hits += 1
    return hits
