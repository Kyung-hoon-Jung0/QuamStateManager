"""Enumerate pulses + reverse-pointer (used_by) index for the Pulses page.

Three concerns, all pure functions over a store's ``merged`` dict (plus a
small cache wrapper):

- :func:`list_pulses` — every pulse-shaped node in the chip as a flat row
  list: qubit channel operations (``qubits.<q>.{xy,z,resonator}.operations``,
  dict bodies = real pulses, string bodies = alias rows) and pair-gate flux
  slots (``qubit_pairs.<p>.macros.<g>.{flux_pulse_qubit,coupler_flux_pulse}``).
- :func:`build_reverse_pointer_index` / :func:`used_by` — who points INTO a
  given operation. Matching is on resolved **absolute path segments**, never
  substrings (``x180`` must not match ``x180_Square``; qA2's same-named op
  must not match qA1's). Direct referrers only — the alias chain is shown by
  the UI, not flattened here.
- :func:`rewrite_subtree_pointers` / :func:`rewrite_referrer_pointer` —
  pointer-correct copy/retarget rules for duplicate and rename. Both assume
  the move stays within the same parent container (operations dict / macros
  slot), which holds for every pulse operation by construction.
"""

from __future__ import annotations

import copy
import logging
from typing import Any

from quam_state_manager.core.loader import _walk
from quam_state_manager.core.pointer_path import pointer_to_abs, resolve_field_target
from quam_state_manager.core.pointer_resolver import is_pointer
from quam_state_manager.core.pulse_catalog import (
    PulseSpec,
    by_qclass,
    infer_spec,
    resolve_length,
)

logger = logging.getLogger(__name__)

__all__ = [
    "PULSE_CHANNELS",
    "GATE_SLOTS",
    "PulseIndex",
    "build_reverse_pointer_index",
    "list_pulses",
    "used_by",
    "rewrite_subtree_pointers",
    "rewrite_referrer_pointer",
]

PULSE_CHANNELS = ("xy", "z", "resonator")
GATE_SLOTS = ("flux_pulse_qubit", "coupler_flux_pulse")


# ---------------------------------------------------------------------------
# Reverse pointer index
# ---------------------------------------------------------------------------

def build_reverse_pointer_index(merged: dict) -> dict[str, list[str]]:
    """One pass over every leaf: ``{absolute_target_path: [referrer, ...]}``.

    Unresolvable-by-form pointers (malformed, relative from too-shallow
    holders) are skipped; dangling-but-well-formed targets ARE indexed (the
    target key may be re-created, and delete-safety wants to know).
    """
    index: dict[str, list[str]] = {}
    for dot_path, value, path_tuple in _walk(merged):
        if not is_pointer(value):
            continue
        target = pointer_to_abs(value, list(path_tuple))
        if not target:
            continue
        index.setdefault(".".join(target), []).append(dot_path)
    return index


def used_by(merged: dict, op_path: str,
            reverse_index: dict[str, list[str]] | None = None) -> list[str]:
    """Referrer paths whose pointer target is *op_path* or inside it.

    Referrers that live INSIDE the operation's own subtree are excluded
    (``#./default_integration_weights``-style internal self-refs are not
    inbound dependencies). Single-op lookup; for the whole library use
    :func:`build_op_referrers` (O(targets) once instead of O(rows×targets)).
    """
    if reverse_index is None:
        reverse_index = build_reverse_pointer_index(merged)
    prefix = op_path + "."
    referrers: list[str] = []
    for target, holders in reverse_index.items():
        if target != op_path and not target.startswith(prefix):
            continue
        for holder in holders:
            if holder == op_path or holder.startswith(prefix):
                continue  # internal self-reference
            referrers.append(holder)
    return sorted(set(referrers))


def _op_path_of(target: str) -> str | None:
    """The pulse-operation path a pointer *target* belongs to, or None.

    ``qubits.q.xy.operations.x180.length`` → ``qubits.q.xy.operations.x180``;
    ``qubit_pairs.p.macros.cz.flux_pulse_qubit.amplitude`` → ``…flux_pulse_qubit``.
    O(1) per target, so building the whole forward map is one pass."""
    segs = target.split(".")
    if (len(segs) >= 5 and segs[0] == "qubits"
            and segs[2] in PULSE_CHANNELS and segs[3] == "operations"):
        return ".".join(segs[:5])
    if (len(segs) >= 5 and segs[0] == "qubit_pairs"
            and segs[2] == "macros" and segs[4] in GATE_SLOTS):
        return ".".join(segs[:5])
    return None


def build_op_referrers(reverse_index: dict[str, list[str]]) -> dict[str, list[str]]:
    """Forward ``{op_path → [external referrers]}`` over the whole chip in ONE
    pass, so :func:`list_pulses` is O(rows + targets) instead of calling
    :func:`used_by` (O(targets)) per row. Internal self-refs are excluded.
    ``_op_path_of`` maps both a field target and the op node itself (an alias
    target) to the same 5-segment op path."""
    out: dict[str, set] = {}
    for target, holders in reverse_index.items():
        op = _op_path_of(target)
        if op is None:
            continue
        prefix = op + "."
        for h in holders:
            if h == op or h.startswith(prefix):
                continue
            out.setdefault(op, set()).add(h)
    return {k: sorted(v) for k, v in out.items()}


# ---------------------------------------------------------------------------
# Enumeration
# ---------------------------------------------------------------------------

def _short_class(qclass: str | None, spec: PulseSpec | None) -> str:
    if spec is not None:
        return spec.key
    if isinstance(qclass, str) and qclass:
        return qclass.rsplit(".", 1)[-1]
    return "(implicit)"


def _resolved_scalar(merged: dict, field_path: str, raw: Any) -> Any:
    """Resolve a pointer-valued scalar for display; raw value on failure."""
    if not is_pointer(raw):
        return raw
    target = resolve_field_target(merged, field_path)
    if target.get("resolvable"):
        return target.get("resolved_value")
    return raw


def _row_for_pulse(merged: dict, path: str, body: Any, *,
                   owner_kind: str, owner: str, channel: str,
                   op_name: str, gate: str | None,
                   reverse_index: dict[str, list[str]] | None = None,
                   op_referrers: dict[str, list[str]] | None = None) -> dict:
    row = {
        "path": path,
        "owner_kind": owner_kind,
        "owner": owner,
        "channel": channel,
        "op_name": op_name,
        "gate": gate,
        "qclass": None,
        "class_short": None,
        "known": False,
        "creatable": False,
        "iq": False,
        "readout": False,
        "is_alias": False,
        "alias_target": None,
        "params": {},
        "length": None,
        "amplitude": None,
        "summary": "",
        "used_by": (op_referrers.get(path, []) if op_referrers is not None
                    else (used_by(merged, path, reverse_index)
                          if reverse_index is not None else [])),
    }

    if is_pointer(body):
        row["is_alias"] = True
        target = resolve_field_target(merged, path)
        if target.get("resolvable"):
            row["alias_target"] = target["resolved_path"]
            # display length/amp from the resolved target (follows pointers)
            for fname in ("length", "amplitude"):
                t = resolve_field_target(merged,
                                         f"{target['resolved_path']}.{fname}")
                if t.get("resolvable"):
                    v = t.get("resolved_value")
                    if isinstance(v, (int, float)) and not isinstance(v, bool):
                        row[fname] = v
        else:
            row["alias_target"] = body  # dangling — show the raw pointer
        row["summary"] = f"alias → {row['alias_target']}"
        return row

    if not isinstance(body, dict):
        row["class_short"] = "(invalid)"
        row["summary"] = repr(body)
        return row

    spec = infer_spec(body, context_slot=channel if gate else None)
    qclass = body.get("__class__")
    row["qclass"] = qclass or (spec.qclass if spec and gate else None)
    row["class_short"] = _short_class(qclass, spec)
    row["known"] = spec is not None
    row["creatable"] = bool(spec and spec.creatable)
    row["readout"] = bool(spec and spec.readout)
    row["params"] = {k: v for k, v in body.items() if k != "__class__"}

    # Resolve display scalars (pointers followed, inferred lengths computed).
    resolved: dict[str, Any] = {}
    for fname, fval in row["params"].items():
        if is_pointer(fval) and not fval.startswith("#./inferred"):
            resolved[fname] = _resolved_scalar(merged, f"{path}.{fname}", fval)
        else:
            resolved[fname] = fval
    row["length"] = resolve_length(spec, resolved)
    amp = resolved.get("amplitude")
    row["amplitude"] = amp if isinstance(amp, (int, float)) and not isinstance(amp, bool) else None

    if spec is not None:
        axis_angle = resolved.get("axis_angle")
        row["iq"] = spec.iq == "always" or (spec.iq == "optional"
                                            and axis_angle is not None)

    bits = []
    if row["length"] is not None:
        bits.append(f"{row['length']} ns")
    if row["amplitude"] is not None:
        bits.append(f"A={row['amplitude']:.4g}")
    for extra in ("alpha", "sigma", "flat_length", "t_phi_eff"):
        v = resolved.get(extra)
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            bits.append(f"{extra}={v:.4g}")
    row["summary"] = " · ".join(bits)
    return row


def list_pulses(merged: dict, *, with_used_by: bool = True) -> list[dict]:
    """Flat row list of every pulse-shaped node in the chip (see module doc).

    ``with_used_by=False`` skips building the reverse-pointer index (the single
    most expensive step, ~27 ms on a 21-qubit chip) and leaves each row's
    ``used_by`` empty. Callers that don't need the reverse-pointer column — the
    waveform DAC-range diagnostics, which run on every edit — pass False for a
    big speed-up.
    """
    reverse_index = build_reverse_pointer_index(merged) if with_used_by else None
    op_referrers = build_op_referrers(reverse_index) if reverse_index is not None else None
    rows: list[dict] = []

    for qubit_name, qubit in (merged.get("qubits") or {}).items():
        if not isinstance(qubit, dict):
            continue
        for channel in PULSE_CHANNELS:
            chan = qubit.get(channel)
            if not isinstance(chan, dict):
                continue
            operations = chan.get("operations")
            if not isinstance(operations, dict):
                continue
            for op_name, body in operations.items():
                path = f"qubits.{qubit_name}.{channel}.operations.{op_name}"
                rows.append(_row_for_pulse(
                    merged, path, body, owner_kind="qubit", owner=qubit_name,
                    channel=channel, op_name=op_name, gate=None,
                    reverse_index=reverse_index, op_referrers=op_referrers))

    for pair_name, pair in (merged.get("qubit_pairs") or {}).items():
        if not isinstance(pair, dict):
            continue
        macros = pair.get("macros")
        if not isinstance(macros, dict):
            continue
        for gate_name, macro in macros.items():
            if not isinstance(macro, dict):
                continue  # gate-level aliases ("cz": "#./cz_unipolar") are
                # not pulse rows; they still appear in used_by via the index
            for slot in GATE_SLOTS:
                if slot not in macro:
                    continue
                body = macro[slot]
                if body is None:
                    continue  # declared-but-empty coupler slot
                path = f"qubit_pairs.{pair_name}.macros.{gate_name}.{slot}"
                rows.append(_row_for_pulse(
                    merged, path, body, owner_kind="pair", owner=pair_name,
                    channel=slot, op_name=f"{gate_name}.{slot}", gate=gate_name,
                    reverse_index=reverse_index, op_referrers=op_referrers))

    return rows


# ---------------------------------------------------------------------------
# Pointer rewriting (duplicate / rename)
# ---------------------------------------------------------------------------

def _flavor(pointer: str) -> str:
    if pointer.startswith("#../"):
        return "#../"
    if pointer.startswith("#./"):
        return "#./"
    return "#/"


def _derive_pointer(flavor: str, holder: list[str], target: list[str]) -> str:
    """Express *target* from *holder* in *flavor* (absolute fallback)."""
    if flavor == "#./" and len(holder) >= 1 and target[:len(holder) - 1] == holder[:-1]:
        return "#./" + "/".join(target[len(holder) - 1:])
    if flavor == "#../" and len(holder) >= 2 and target[:len(holder) - 2] == holder[:-2]:
        return "#../" + "/".join(target[len(holder) - 2:])
    return "#/" + "/".join(target)


def _is_inside(target: list[str], prefix: list[str]) -> bool:
    return len(target) >= len(prefix) and target[:len(prefix)] == prefix


def rewrite_subtree_pointers(value: Any, src_path: str, dst_path: str) -> Any:
    """Deep-copied *value* with self-targeting pointers retargeted to *dst*.

    Rule: a pointer whose absolute resolution (in the ORIGINAL location)
    lands inside the *src_path* subtree is rewritten to the corresponding
    node under *dst_path*, preserving its original flavor where the relative
    base still holds; every other pointer is kept verbatim. ``#./`` internal
    refs and ``#../`` family refs to OTHER ops therefore stay unchanged
    (both translate correctly because duplicate/rename never change the
    parent container).
    """
    src_segs = src_path.split(".")
    dst_segs = dst_path.split(".")
    copied = copy.deepcopy(value)

    def rewrite(node: Any, rel: list[str]) -> Any:
        if isinstance(node, dict):
            return {k: rewrite(v, rel + [k]) for k, v in node.items()}
        if isinstance(node, list):
            return [rewrite(v, rel + [str(i)]) for i, v in enumerate(node)]
        if is_pointer(node):
            holder_src = src_segs + rel
            target = pointer_to_abs(node, holder_src)
            if target and _is_inside(target, src_segs):
                new_target = dst_segs + target[len(src_segs):]
                holder_dst = dst_segs + rel
                return _derive_pointer(_flavor(node), holder_dst, new_target)
        return node

    return rewrite(copied, [])


def rewrite_referrer_pointer(pointer: str, holder_path: str,
                             old_target_path: str, new_target_path: str) -> str | None:
    """New pointer string for a referrer after its target moved.

    *pointer* (at *holder_path*) resolves somewhere inside
    *old_target_path*; the returned pointer addresses the corresponding node
    under *new_target_path* in the same flavor (absolute fallback). Returns
    None when the pointer does not actually resolve inside the old target.
    """
    holder = holder_path.split(".")
    old_segs = old_target_path.split(".")
    target = pointer_to_abs(pointer, holder)
    if not target or not _is_inside(target, old_segs):
        return None
    new_target = new_target_path.split(".") + target[len(old_segs):]
    return _derive_pointer(_flavor(pointer), holder, new_target)


# ---------------------------------------------------------------------------
# Cache wrapper (lives on the context dict, dropped by _invalidate_engine_cache)
# ---------------------------------------------------------------------------

class PulseIndex:
    """Lazy cache of rows + reverse index for one store.

    Self-validating: every cache read compares its stamp against
    ``store.mutation_seq`` (incremented under ``store._lock`` by every
    mutation AND by ``reload()``), so a stale entry can never be served —
    even from code paths that forget to call :meth:`invalidate` (which
    remains as an optimization hook). Reads rebuild under the store lock,
    making destructive used_by checks linearizable with mutations.
    """

    def __init__(self, store) -> None:
        self.store = store
        self._rows: list[dict] | None = None
        self._reverse: dict[str, list[str]] | None = None
        self._seq: int = -1
        # Cache of rendered sparkline SVGs keyed by op path, valid only at one
        # mutation_seq (a mutation can change any pulse's shape). Lets repeated
        # search / pagination over an unchanged chip pay zero re-synth.
        self._spark: dict[str, str | None] = {}
        self._spark_seq: int = -1

    def invalidate(self) -> None:
        self._rows = None
        self._reverse = None
        self._seq = -1

    def sparkline(self, op_path: str, render):
        """Memoized sparkline SVG for *op_path*. *render* is a 0-arg callable
        that produces the SVG (or None) on a cache miss. Cleared whenever the
        chip mutates (keyed on ``store.mutation_seq``)."""
        seq = getattr(self.store, "mutation_seq", 0)
        if seq != self._spark_seq:
            self._spark = {}
            self._spark_seq = seq
        if op_path not in self._spark:
            self._spark[op_path] = render()
        return self._spark[op_path]

    def _fresh(self) -> bool:
        return self._seq == getattr(self.store, "mutation_seq", None)

    def rows(self) -> list[dict]:
        with self.store._lock:
            if self._rows is None or not self._fresh():
                self._rows = list_pulses(self.store.merged)
                self._reverse = None  # rebuilt lazily at the same seq
                self._seq = self.store.mutation_seq
            return self._rows

    def reverse_index(self) -> dict[str, list[str]]:
        with self.store._lock:
            if self._reverse is None or not self._fresh():
                self._reverse = build_reverse_pointer_index(self.store.merged)
                if not self._fresh():
                    self._rows = None
                self._seq = self.store.mutation_seq
            return self._reverse

    def used_by(self, op_path: str) -> list[str]:
        with self.store._lock:
            return used_by(self.store.merged, op_path, self.reverse_index())
