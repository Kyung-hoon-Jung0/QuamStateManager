"""QDAC-II as a component: the ONE place SM decides what a QDAC bias line is.

Before this module every surface made its own guess, and they disagreed. The
flatteners assumed "``z`` is a dict ⇒ it is a FluxLine"; ``physical_units``
defined a channel as "a dict carrying ``opx_output``"; ``regen_spec`` had the
only real classifier in the codebase (a class-name substring test). On the
customer's 20-qubit chip — 11 QDAC-biased, 9 LF-FEM-biased — that produced
eleven qubits whose own inspector page showed no bias fields at all, four
identically-labelled grid columns, and a ``/flux`` page listing rows it could
not fill. See ``docs/136``.

Three shapes exist, and every caller should ask :func:`bias_mode` rather than
inspecting ``z``:

``opx``
    The classic flux-tunable qubit. ``z`` is a ``FluxLine``: an ``opx_output``
    pointer into an LF-FEM analog port, plus ``joint_offset`` / ``flux_point``
    / ``operations``.

``qdac``
    ``z`` is a ``QdacBiasLine`` (the customer's
    ``quam_config.qdac_components``): a QDAC-II ``channel`` and ``dc_offset``,
    no ``opx_output`` and no ``operations`` — it cannot play a pulse. Its
    ``opx_trigger_out`` is an OPX digital marker cabled to one of the QDAC's
    four external trigger inputs (``ext1``..``ext4``).

``bias_tee``
    Both at once, through a bias tee: the QDAC holds the DC operating point
    (standing in for ``joint_offset``) while an LF-FEM port plays pulses on top
    of it. The two components are siblings on the qubit — ``z`` stays the pulse
    line and the bias line sits beside it under whatever name the lab's class
    gives it.

**Detection is structural, and deliberately so.** A lab that adds a bias-tee
class names its field whatever it likes, and SM must read that chip correctly
the day it appears — without an env change, a catalog entry, or a release. The
class name is used only as corroboration, never as the sole gate; the shape is
the evidence. Nothing here imports quam, and nothing here writes.
"""

from __future__ import annotations

from typing import Any, Iterator

__all__ = [
    "QDAC_FIELDS",
    "is_bias_line",
    "is_flux_line",
    "bias_line_of",
    "flux_line_of",
    "bias_mode",
    "trigger_ref",
    "instrument",
    "biased_qubits",
    "ext_groups",
    "spec_biased_qubits",
    "spec_bias_tee_qubits",
    "spec_bias_mode",
]

#: ``QdacBiasLine``'s own fields, in the order a human reads them: what the
#: channel IS, then what it does when triggered. The single source of truth —
#: ``regen_spec`` inverts exactly these into the wizard spec, the inspector
#: renders exactly these, and ``run_build`` writes exactly these back.
QDAC_FIELDS: tuple[str, ...] = (
    "channel",
    "dc_offset",
    "trigger_port",
    "dwell",
    "slew_rate",
    "output_range",
    "output_filter",
    "settle_time",
)

# The keys that make a dict recognisably a QDAC bias line rather than some
# other component that happens to carry one of them. `settle_time` is the one
# name a FluxLine shares, which is why membership alone is never enough.
_BIAS_MARKERS = frozenset({
    "channel", "dc_offset", "trigger_port", "dwell",
    "slew_rate", "output_range", "output_filter",
})

# A FluxLine always DECLARES these, even when their value is null — quam
# dataclasses serialise declared fields. A resonator or drive channel carries
# `opx_output` too, so the offsets are what separate a flux line from any other
# OPX-driven channel.
_FLUX_MARKERS = frozenset({
    "joint_offset", "independent_offset", "min_offset",
    "arbitrary_offset", "flux_point",
})


def _class_of(node: Any) -> str:
    """Normalised ``__class__`` of a node — lowercase, underscores removed."""
    if not isinstance(node, dict):
        return ""
    return str(node.get("__class__") or "").replace("_", "").lower()


def is_bias_line(node: Any) -> bool:
    """True when *node* is a QDAC-II bias line (a ``QdacBiasLine``-shaped dict).

    An ``opx_output`` is disqualifying and checked first: whatever else a node
    is, a component wired to an OPX analog port is not a QDAC bias line. That
    guard is what keeps a bias-tee qubit's pulse line from being read as its
    DC bias.
    """
    if not isinstance(node, dict):
        return False
    if "opx_output" in node:
        return False
    if "qdacbias" in _class_of(node):
        return True
    # No class name to go on (a hand-written chip, a stripped export): require
    # the channel plus enough of the QDAC-only knobs that no other component
    # would collide by accident.
    keys = set(node)
    return "channel" in keys and len(keys & _BIAS_MARKERS) >= 3


def is_flux_line(node: Any) -> bool:
    """True when *node* is an OPX-driven flux line (a ``FluxLine``-shaped dict)."""
    if not isinstance(node, dict) or "opx_output" not in node:
        return False
    return bool(set(node) & _FLUX_MARKERS)


def _children(qubit: Any) -> Iterator[tuple[str, dict]]:
    """Dict-valued children of a qubit, ``z`` first so the common case is O(1)."""
    if not isinstance(qubit, dict):
        return
    z = qubit.get("z")
    if isinstance(z, dict):
        yield "z", z
    for key, value in qubit.items():
        if key != "z" and isinstance(value, dict):
            yield key, value


def bias_line_of(qubit: Any) -> tuple[str, dict] | None:
    """``(field_name, node)`` of this qubit's QDAC bias line, or None.

    The field name is returned because it is not always ``z``: on a QDAC-only
    qubit the bias line REPLACES ``z``, while on a bias-tee qubit it sits
    beside it. Callers that need a dot-path must use the name they are given
    here rather than assuming one.
    """
    for name, node in _children(qubit):
        if is_bias_line(node):
            return name, node
    return None


def flux_line_of(qubit: Any) -> tuple[str, dict] | None:
    """``(field_name, node)`` of this qubit's OPX flux line, or None."""
    for name, node in _children(qubit):
        if is_flux_line(node):
            return name, node
    return None


def bias_mode(qubit: Any) -> str | None:
    """``"opx"`` | ``"qdac"`` | ``"bias_tee"`` | ``None`` for one qubit dict.

    ``None`` means the qubit is not flux-biased at all (a fixed-frequency
    transmon) — distinct from "biased in a way we could not read", which this
    module never reports, because an unreadable shape would be a silent lie on
    every page that asks.
    """
    has_bias = bias_line_of(qubit) is not None
    has_flux = flux_line_of(qubit) is not None
    if has_bias and has_flux:
        return "bias_tee"
    if has_bias:
        return "qdac"
    if has_flux:
        return "opx"
    return None


def instrument(state: Any) -> dict | None:
    """The chip's top-level ``qdac`` instrument entry, or None.

    Nothing points at it and it points at nothing — the qubit's bias line is
    NOT linked to the instrument by a pointer, so "which QDAC" is answered by
    the chip having exactly one.
    """
    if not isinstance(state, dict):
        return None
    inst = state.get("qdac")
    return inst if isinstance(inst, dict) else None


def biased_qubits(merged: Any) -> dict[str, dict]:
    """``{qubit_id: bias_line_node}`` for every QDAC-biased qubit on the chip.

    Includes bias-tee qubits — they ARE QDAC-biased; what makes them different
    is that they also have a pulse line. Ask :func:`bias_mode` when the
    distinction matters.
    """
    qubits = merged.get("qubits") if isinstance(merged, dict) else None
    if not isinstance(qubits, dict):
        return {}
    out: dict[str, dict] = {}
    for qid, qubit in qubits.items():
        found = bias_line_of(qubit)
        if found is not None:
            out[qid] = found[1]
    return out


def trigger_ref(qubit: Any, merged: Any, qid: str | None = None) -> dict | None:
    """Where this qubit's QDAC trigger marker physically lands, or None.

    Returns ``{"con", "slot", "port", "ext", "ref"}``. The chain is two hops and
    the first one lives in state while the second lives in wiring::

        z.opx_trigger_out.digital_outputs.<name>.opx_output
            -> "#/wiring/qubits/q1/qt/digital_output"
            -> "#/ports/digital_outputs/con1/4/1"

    ``ext`` is the QDAC-II external trigger input the marker is cabled to, read
    from the bias line's own ``trigger_port``. One OPX digital output feeds one
    ext input and arms every channel on it, so several qubits legitimately
    share one port — see :func:`ext_groups`.

    Falls back to ``wiring.qubits.<qid>.qt.digital_output`` when the state-side
    channel is absent, which is how a chip built by an older generator reads.
    """
    from quam_state_manager.core.query import _follow_port_ref

    found = bias_line_of(qubit)
    bias = found[1] if found else None
    ext = bias.get("trigger_port") if isinstance(bias, dict) else None

    ref: Any = None
    trig = (bias or {}).get("opx_trigger_out")
    digital = trig.get("digital_outputs") if isinstance(trig, dict) else None
    if isinstance(digital, dict):
        for entry in digital.values():
            if isinstance(entry, dict) and entry.get("opx_output"):
                ref = entry["opx_output"]
                break

    if ref is None and qid:
        wiring_q = (((merged or {}).get("wiring") or {}).get("qubits") or {}).get(qid)
        qt = wiring_q.get("qt") if isinstance(wiring_q, dict) else None
        if isinstance(qt, dict):
            ref = qt.get("digital_output")

    if not isinstance(ref, str):
        return None
    final = _follow_port_ref(merged, ref) if ref.startswith("#/") else None
    if not final:
        return None
    parts = final[2:].split("/")          # ports/digital_outputs/con1/4/1
    if len(parts) < 5 or parts[0] != "ports" or not parts[1].startswith("digital"):
        return None
    con, slot, port = parts[2], parts[3], parts[4]
    return {"con": con, "slot": slot, "port": port, "ext": ext, "ref": final}


def ext_groups(merged: Any) -> dict[tuple[str, str, str], dict]:
    """``{(con, slot, port): {"ext", "qubits", "conflict"}}`` — the real cabling.

    One OPX digital output drives one QDAC ext input, so every qubit landing on
    the same port MUST declare the same ``ext``: the port and the ext are two
    names for one cable. ``conflict`` carries the set of differing exts when
    they disagree, which is a wiring error no other check would catch — it
    would simply arm the wrong channels at run time.
    """
    qubits = merged.get("qubits") if isinstance(merged, dict) else None
    if not isinstance(qubits, dict):
        return {}
    groups: dict[tuple[str, str, str], dict] = {}
    for qid, qubit in qubits.items():
        ref = trigger_ref(qubit, merged, qid)
        if not ref:
            continue
        key = (ref["con"], ref["slot"], ref["port"])
        entry = groups.setdefault(key, {"ext": ref["ext"], "qubits": [], "conflict": set()})
        entry["qubits"].append(qid)
        if ref["ext"] and ref["ext"] != entry["ext"]:
            entry["conflict"].add(entry["ext"])
            entry["conflict"].add(ref["ext"])
    for entry in groups.values():
        entry["qubits"].sort(key=lambda q: (len(q), q))
    return groups


# ---------------------------------------------------------------------------
# The wizard SPEC side (docs/136 §13)
# ---------------------------------------------------------------------------
# Above this line everything reads a built CHIP. Below it everything reads the
# generate/re-generate SPEC, which describes a chip that does not exist yet.
# They are deliberately separate readers over the same vocabulary: a spec has
# no `__class__` to corroborate against and no pointers to follow, so the
# structural test that identifies a bias line in state.json has nothing to work
# with here. What a spec has instead is a DECLARATION.


def spec_biased_qubits(spec: Any) -> dict[str, dict]:
    """``{qid: fields}`` for every QDAC-biased qubit a spec declares."""
    if not isinstance(spec, dict):
        return {}
    qubits = (spec.get("qdac") or {}).get("qubits")
    return {q: f for q, f in qubits.items() if isinstance(f, dict)} \
        if isinstance(qubits, dict) else {}


def spec_bias_tee_qubits(spec: Any) -> dict[str, dict]:
    """``{qid: fields}`` for the bias-tee qubits — QDAC **and** an OPX flux line.

    The marker is the explicit ``bias_tee`` flag on the qubit's own QDAC entry,
    not the mere co-presence of a ``flux`` line. Co-presence is ambiguous: it is
    equally the signature of a mistake (a qubit switched to QDAC while its flux
    line lingered), and that mistake used to be a hard validation error worth
    keeping. A flag says which of the two happened, and validation then checks
    the flag against the lines both ways.
    """
    return {q: f for q, f in spec_biased_qubits(spec).items() if f.get("bias_tee")}


def spec_bias_mode(spec: Any, qid: str) -> str:
    """``"opx"`` / ``"qdac"`` / ``"bias_tee"`` for one qubit of a spec.

    ``"opx"`` is the answer for a qubit with no QDAC entry — including one with
    no flux line at all, since "no bias" and "OPX bias" are the same statement
    about the QDAC, which is what this reports on.
    """
    fields = spec_biased_qubits(spec).get(qid)
    if not fields:
        return "opx"
    return "bias_tee" if fields.get("bias_tee") else "qdac"
