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

from quam_state_manager.core import qdac
from quam_state_manager.core.loader import _walk
from quam_state_manager.core.loader import natural_key
from quam_state_manager.core.pointer_path import _walk as _seg_walk
from quam_state_manager.core.pointer_path import pointer_to_abs, resolve_field_target
from quam_state_manager.core.pointer_resolver import is_pointer
from quam_state_manager.core.pulse_catalog import (
    PulseSpec,
    infer_spec_ex,
    resolve_length,
    unmodeled_fields,
)

logger = logging.getLogger(__name__)

__all__ = [
    "PULSE_CHANNELS",
    "PAIR_PULSE_CHANNELS",
    "GATE_SLOTS",
    "PulseIndex",
    "build_reverse_pointer_index",
    "list_pulses",
    "used_by",
    "rewrite_subtree_pointers",
    "rewrite_referrer_pointer",
]

# Qubit-level channels holding an ``operations`` dict. ``xy_detuned`` exists on
# FixedFrequencyZZDriveTransmon qubits (the Stark-CZ target lobe drives it).
PULSE_CHANNELS = ("xy", "z", "resonator", "xy_detuned")
# Pair-level drive channels (CR/ZZ chips): the real CR drive pulses live at
# ``qubit_pairs.<p>.cross_resonance.operations.*`` — invisible to the Pulses
# page until enumerated here. ``zz_drive``/``zz`` is the same channel across
# quam-builder generations (the branch tip renamed the field); pair-level
# ``xy_detuned`` is serialized by some generations too. See docs/54.
PAIR_PULSE_CHANNELS = ("cross_resonance", "zz_drive", "zz", "xy_detuned")
# ``flux_pulse_target``: a lab's asymmetric two-flux CZ (both qubits
# pulsed, each with its own amplitude) carries a THIRD slot -- 37 of them on
# the pilot chip, none of which had a row (stress round 2026-09-16, docs/190).
GATE_SLOTS = ("flux_pulse_qubit", "coupler_flux_pulse", "flux_pulse_target")


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
    # Natural order (customer rule 2026-09-09): a referrer path's list
    # indices and qubit numbers are NUMBERS — q2 before q10, .101 before
    # .1009 — not the lexicographic order a plain sorted() gives.
    return sorted(set(referrers), key=natural_key)


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
    # Pair-channel ops (CR/ZZ drive pulses): the target-xy cancellation stubs
    # point INTO these (length/sigma/flat_length), so mapping them here is what
    # gives the CR drive pulses used_by rows + rename-impact disclosure.
    if (len(segs) >= 5 and segs[0] == "qubit_pairs"
            and segs[2] in PAIR_PULSE_CHANNELS and segs[3] == "operations"):
        return ".".join(segs[:5])
    return None


def _found_op_of(target: str, found: frozenset | set | None) -> str | None:
    """The DISCOVERED pulse path a pointer *target* is, or lies inside (the
    longest match). O(depth) per target; None when *found* is empty."""
    if not found:
        return None
    segs = target.split(".")
    for n in range(len(segs), 1, -1):
        cand = ".".join(segs[:n])
        if cand in found:
            return cand
    return None


def build_op_referrers(reverse_index: dict[str, list[str]],
                       found: frozenset | set | None = None) -> dict[str, list[str]]:
    """Forward ``{op_path → [external referrers]}`` over the whole chip in ONE
    pass, so :func:`list_pulses` is O(rows + targets) instead of calling
    :func:`used_by` (O(targets)) per row. Internal self-refs are excluded.
    ``_op_path_of`` maps both a field target and the op node itself (an alias
    target) to the same 5-segment op path; *found* (the shape-discovered
    pulse paths, docs/2xx) is consulted only when that static map misses."""
    out: dict[str, set] = {}
    for target, holders in reverse_index.items():
        op = _op_path_of(target)
        if op is None:
            op = _found_op_of(target, found)
        if op is None:
            continue
        prefix = op + "."
        for h in holders:
            if h == op or h.startswith(prefix):
                continue
            out.setdefault(op, set()).add(h)
    return {k: sorted(v, key=natural_key) for k, v in out.items()}


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
        "class_match": None,
        "unmodeled": [],
        "creatable": False,
        "iq": False,
        "readout": False,
        "is_alias": False,
        "alias_target": None,
        "params": {},
        "length": None,
        "length_stored": None,
        "length_implausible": False,
        "amplitude": None,
        "summary": "",
        "used_by": (op_referrers.get(path, []) if op_referrers is not None
                    else (used_by(merged, path, reverse_index)
                          if reverse_index is not None else [])),
        # docs/2xx pulse locations: a NAMED op in an ``operations`` dict can be
        # renamed / duplicated beside itself; a slot (``flux_pulse_qubit``) is
        # a schema field of its macro and cannot.
        "renamable": _in_operations(path),
        "found": False,      # True = discovered by shape, outside the whitelist
        "location": None,    # the parent path, shown as "found at <path>"
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

    spec, class_match = infer_spec_ex(body, context_slot=channel if gate else None)
    qclass = body.get("__class__")
    row["qclass"] = qclass or (spec.qclass if spec and gate else None)
    row["class_short"] = _short_class(qclass, spec)
    row["known"] = spec is not None
    row["class_match"] = class_match
    # A leaf match is a class NAME claim only — surface what the catalog spec
    # does not model so the table/detail can flag it ("env" = home verified
    # by the active env overlay, fully-known like "exact"). Implicit slots
    # are a structural guess, not a class claim: never flagged.
    row["unmodeled"] = (unmodeled_fields(spec, body)
                        if class_match in ("exact", "env", "alias", "leaf")
                        else [])
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
    # docs/190 F28: a resolved length is ARITHMETIC over pointer-followed
    # fields, so it can come back as a number no waveform could have. The
    # customer case was `length -> #./inferred_length -> pulse_length(52) +
    # padding_length(-999999 sentinel)` = -999944, which the LENGTH column
    # printed as a plain number beside real ones -- while SM's own diagnostics
    # independently flags that value class as node-run-crashing. A length is a
    # count of samples: zero or below is not a short pulse, it is an answer the
    # arithmetic could not give.
    _raw_len = resolved.get("length")
    # the value AS STORED (pointers followed), which is the thing that is
    # wrong -- `resolve_length` int()s a fractional one away, so naming the
    # display length in the warning would point at a number nobody typed
    row["length_stored"] = (_raw_len if isinstance(_raw_len, (int, float))
                            and not isinstance(_raw_len, bool) else None)
    row["length_implausible"] = bool(
        (isinstance(row["length"], (int, float))
         and not isinstance(row["length"], bool)
         and row["length"] <= 0)
        # a fractional length is impossible too, and `resolve_length` int()s it
        # away -- 100.5 would otherwise render as a perfectly ordinary 100
        or (isinstance(_raw_len, float) and not _raw_len.is_integer()))
    amp = resolved.get("amplitude")
    row["amplitude"] = amp if isinstance(amp, (int, float)) and not isinstance(amp, bool) else None

    if spec is not None:
        axis_angle = resolved.get("axis_angle")
        row["iq"] = spec.iq == "always" or (spec.iq == "optional"
                                            and axis_angle is not None)

    bits = []
    if row["length"] is not None:
        bits.append(f"{row['length']} ns"
                    + (f" (impossible: stored {row['length_stored']})"
                       if row["length_implausible"] else ""))
    if row["amplitude"] is not None:
        bits.append(f"A={row['amplitude']:.4g}")
    for extra in ("alpha", "sigma", "flat_length", "t_phi_eff"):
        v = resolved.get(extra)
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            bits.append(f"{extra}={v:.4g}")
    row["summary"] = " · ".join(bits)
    return row


def _in_operations(path: str) -> bool:
    """True when *path* is a named entry of an ``operations`` dict."""
    segs = path.split(".")
    return len(segs) >= 2 and segs[-2] == "operations"


# Top-level subtrees that hold no pulses by construction: the instrument side
# (``ports``/``wiring``/``network``), and ``extras``, which SM treats as
# free-form text everywhere (docs/81) -- a pasted note must never become a row.
_DISCOVERY_SKIP_TOP = frozenset({"ports", "wiring", "network", "extras"})


def _owner_of(merged: dict, segs: list[str]) -> tuple[str, str, int]:
    """``(owner_kind, owner, n_owner_segs)`` for a discovered pulse path.

    ``qubits.<q>``/``qubit_pairs.<p>`` keep the kinds the page already speaks.
    Any other top-level key is a component: a classed one (``coupler_bus`` with
    a ``__class__``) is its own owner; an unclassed collection (``twpas``) is
    owned by its member (``twpaA``)."""
    top = segs[0]
    if top == "qubits" and len(segs) > 2:
        return "qubit", segs[1], 2
    if top == "qubit_pairs" and len(segs) > 2:
        return "pair", segs[1], 2
    node = merged.get(top)
    if isinstance(node, dict) and "__class__" not in node and len(segs) > 2:
        return "component", segs[1], 2
    return "component", top, 1


def _discover(merged: dict, known: set[str]) -> list[tuple[str, Any, dict]]:
    """Pulses found by SHAPE, not by name (docs/2xx pulse locations).

    A lab hand-adds pulses where SM's whitelist never looked (a second drive
    channel ``xy2``, a coupler's own ``operations``, a TWPA pump, a new macro
    slot). The rule, measured on 49 real chip states (every one of the 565
    rows it adds outside the whitelist IS a pulse, and no
    non-pulse class passes it):

    R1  every entry of an ``operations`` dict that is a dict carrying a
        ``__class__`` (quam's contract: ``Channel.operations`` is
        ``Dict[str, Pulse]``), or a ``#`` pointer string (an op alias -- the
        same alias row the whitelisted channels already show);
    R2  outside an ``operations`` dict: a dict whose ``__class__`` passes
        :func:`pulse_catalog.is_pulse_class` (probed bases > catalog > env
        roster > the ``...Pulse`` name convention).

    Never rows: a dict with no ``__class__`` outside the whitelist (no evidence
    it is a pulse), a pointer string outside ``operations`` (a gate-level alias
    such as ``"cz": "#./cz_unipolar"`` -- still in used_by), anything under
    :data:`_DISCOVERY_SKIP_TOP`, anything inside a list, and anything inside a
    pulse (a pulse's own sub-dicts are its fields). Paths already enumerated by
    the whitelist are skipped, so no row moves or duplicates.
    """
    from quam_state_manager.core.pulse_catalog import is_pulse_class

    out: list[tuple[str, Any, dict]] = []
    cls_memo: dict[str, bool] = {}

    def pulse_cls(c: Any) -> bool:
        if not isinstance(c, str):
            return False
        hit = cls_memo.get(c)
        if hit is None:
            hit = cls_memo[c] = is_pulse_class(c)
        return hit

    def add(path: str, body: Any, segs: list[str], in_ops: bool) -> None:
        kind, owner, n = _owner_of(merged, segs)
        rel = segs[n:]
        gate = None
        if in_ops:
            channel = ".".join(rel[:-2]) or segs[-3]
            op_name = segs[-1]
        elif kind == "pair" and len(rel) >= 3 and rel[0] == "macros":
            # a new macro slot: named like the whitelisted gate slots are
            gate = rel[1]
            channel = ".".join(rel[2:])
            op_name = f"{gate}.{channel}"
        else:
            channel = ".".join(rel[:-1]) or segs[-1]
            op_name = segs[-1]
        out.append((path, body, dict(
            owner_kind=kind, owner=owner, channel=channel, op_name=op_name,
            gate=gate, location=".".join(segs[:-1]))))

    def walk(node: dict, segs: list[str]) -> None:
        for key, val in node.items():
            if not isinstance(val, dict):
                continue
            kseg = segs + [key]
            if key == "operations":
                for op, body in val.items():
                    if not isinstance(op, str):
                        continue
                    osegs = kseg + [op]
                    p = ".".join(osegs)
                    if p in known:
                        continue
                    if isinstance(body, dict) and isinstance(body.get("__class__"), str):
                        add(p, body, osegs, True)
                    elif is_pointer(body):
                        add(p, body, osegs, True)
                    elif isinstance(body, dict):
                        walk(body, osegs)  # an unclassed dict: not a pulse, look inside
                continue
            if pulse_cls(val.get("__class__")):
                p = ".".join(kseg)
                if p not in known:
                    add(p, val, kseg, False)
                continue  # a pulse's sub-dicts are its fields, never pulses
            walk(val, kseg)

    for top, node in merged.items():
        if top in _DISCOVERY_SKIP_TOP or not isinstance(node, dict):
            continue
        if pulse_cls(node.get("__class__")):
            continue  # a top-level pulse has no owner to name; not a quam shape
        walk({top: node}, [])
    return out


def list_pulses(merged: dict, *, with_used_by: bool = True,
                discover: bool = True) -> list[dict]:
    """Flat row list of every pulse-shaped node in the chip (see module doc).

    ``with_used_by=False`` skips building the reverse-pointer index (the single
    most expensive step, ~27 ms on a 21-qubit chip) and leaves each row's
    ``used_by`` empty. Callers that don't need the reverse-pointer column — the
    waveform DAC-range diagnostics, which run on every edit — pass False for a
    big speed-up.
    """
    reverse_index = build_reverse_pointer_index(merged) if with_used_by else None
    # (path, body, kwargs) first; rows are built once the discovered paths are
    # known, because the used_by map needs them (a pointer INTO a discovered
    # pulse maps to that pulse's row).
    pending: list[tuple[str, Any, dict]] = []

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
                pending.append((path, body, dict(owner_kind="qubit", owner=qubit_name,
                    channel=channel, op_name=op_name, gate=None)))

        # docs/136 — the QDAC trigger marker. It is a real pulse on a real OPX
        # digital output, but it lives one level deeper than any other qubit
        # pulse (`<bias>.opx_trigger_out.operations.trigger`), so the loop
        # above cannot see it. On the customer's 20-qubit chip that is eleven
        # pulses missing from a page that prints a definite total.
        #
        # The bias field is asked for rather than assumed: it is `z` on a
        # QDAC-only qubit and a sibling of `z` on a bias-tee one.
        found = qdac.bias_line_of(qubit)
        if found:
            bias_field, bias = found
            trig = bias.get("opx_trigger_out")
            trig_ops = trig.get("operations") if isinstance(trig, dict) else None
            if isinstance(trig_ops, dict):
                for op_name, body in trig_ops.items():
                    path = (f"qubits.{qubit_name}.{bias_field}"
                            f".opx_trigger_out.operations.{op_name}")
                    pending.append((path, body, dict(owner_kind="qubit", owner=qubit_name,
                        channel=f"{bias_field}.opx_trigger_out", op_name=op_name,
                        gate=None)))

    for pair_name, pair in (merged.get("qubit_pairs") or {}).items():
        if not isinstance(pair, dict):
            continue
        macros = pair.get("macros")
        if isinstance(macros, dict):
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
                    pending.append((path, body, dict(owner_kind="pair", owner=pair_name,
                        channel=slot, op_name=f"{gate_name}.{slot}", gate=gate_name)))
        # Pair drive channels (CR/ZZ): every op is a real pulse row — sparkline,
        # detail, synth, used_by (the target-xy cancel stubs point in here).
        for channel in PAIR_PULSE_CHANNELS:
            chan = pair.get(channel)
            if not isinstance(chan, dict):
                continue  # explicit-null (zz_drive on CR-only pairs) or absent
            operations = chan.get("operations")
            if not isinstance(operations, dict):
                continue
            for op_name, body in operations.items():
                path = f"qubit_pairs.{pair_name}.{channel}.operations.{op_name}"
                pending.append((path, body, dict(owner_kind="pair", owner=pair_name,
                    channel=channel, op_name=op_name, gate=None)))

    known = {p for p, _b, _k in pending}
    # docs/2xx: ``discover=False`` is the whitelist alone -- the pin that the
    # shape discovery never moves, alters or duplicates an existing row
    found = _discover(merged, known) if discover else []
    op_referrers = (build_op_referrers(
        reverse_index, frozenset(p for p, _b, _k in found) or None)
        if reverse_index is not None else None)
    rows = [_row_for_pulse(merged, p, b, reverse_index=reverse_index,
                           op_referrers=op_referrers, **kw)
            for p, b, kw in pending]
    for p, b, kw in found:
        loc = kw.pop("location")
        row = _row_for_pulse(merged, p, b, reverse_index=reverse_index,
                             op_referrers=op_referrers, **kw)
        row["found"] = True
        row["location"] = loc
        rows.append(row)
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
        self._seq = None
        # Cache of rendered sparkline SVGs keyed by op path, valid only at one
        # mutation_seq (a mutation can change any pulse's shape). Lets repeated
        # search / pagination over an unchanged chip pay zero re-synth.
        self._spark: dict[str, str | None] = {}
        self._spark_seq: int = -1
        self._by_path: tuple[list, dict] | None = None

    def invalidate(self) -> None:
        self._rows = None
        self._reverse = None
        self._seq = None

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

    def _stamp(self):
        # mutation_seq AND the installed class knowledge: shape discovery
        # (pulse_catalog.is_pulse_class) reads the probed chip-class
        # inventory, so a probe landing must re-derive rows on next read.
        from quam_state_manager.core import pulse_catalog
        return (getattr(self.store, "mutation_seq", None),
                pulse_catalog.class_info_generation())

    def _fresh(self) -> bool:
        return self._seq == self._stamp()

    def rows(self) -> list[dict]:
        with self.store._lock:
            if self._rows is None or not self._fresh():
                self._rows = list_pulses(self.store.merged)
                self._reverse = None  # rebuilt lazily at the same seq
                self._seq = self._stamp()
            return self._rows

    def row(self, path: str) -> dict | None:
        """The row at *path* (O(1), built once per rows() list)."""
        with self.store._lock:
            rows = self.rows()
            by = self._by_path
            if by is None or by[0] is not rows:
                by = self._by_path = (rows, {r["path"]: r for r in rows})
            return by[1].get(path)

    def has_path(self, path: str) -> bool:
        return self.row(path) is not None

    def reverse_index(self) -> dict[str, list[str]]:
        with self.store._lock:
            if self._reverse is None or not self._fresh():
                self._reverse = build_reverse_pointer_index(self.store.merged)
                if not self._fresh():
                    self._rows = None
                self._seq = self._stamp()
            return self._reverse

    def used_by(self, op_path: str) -> list[str]:
        with self.store._lock:
            return used_by(self.store.merged, op_path, self.reverse_index())


# ---------------------------------------------------------------------------
# Copy a pulse onto another channel (2026-09-27, the one create flow)
# ---------------------------------------------------------------------------

def _owner_segs(segs: list[str]) -> list[str]:
    """``qubits.qX`` / ``qubit_pairs.pX`` -- the entity a path belongs to."""
    return list(segs[:2]) if len(segs) >= 2 and segs[0] in (
        "qubits", "qubit_pairs") else []


def copy_pulse_to(merged: dict, src_path: str, dst_path: str, *,
                  kept_out: list[str] | None = None,
                  dropped_out: list[str] | None = None) -> tuple[Any, list[str]]:
    """The subtree to write at *dst_path* so it plays what *src_path* plays,
    laid out the way the chip lays out its own pulses.

    The chip's own layout is the pattern (surveyed on the KRS 5Q chip: an op
    points at its OWN qubit's properties -- ``anharmonicity =
    "#/qubits/q1/anharmonicity"`` -- and at sibling ops of its own channel --
    ``length = "#../x180_DragCosine/length"``). So a copy:

    * follows an alias (``x180 = "#./x180_DragCosine"``) to the pulse itself;
    * keeps a pointer INTO the copied pulse, re-expressed at the copy;
    * maps a pointer into the SOURCE entity (``#/qubits/q1/...``) to the same
      place in the TARGET entity when that place exists there;
    * keeps a relative pointer verbatim when it lands on something that
      exists from the copy's location (the target channel has the sibling);
    * keeps any other absolute pointer verbatim (a machine-wide value);
    * otherwise writes the VALUE the pointer resolves to on the source
      (named in the returned notes), so the copy never dangles.

    Two more rules (2026-09-27 verifier):

    * the source must BE a pulse -- a dict whose declared ``__class__`` is a
      pulse class (:func:`pulse_catalog.is_pulse_class`; the caller checks the
      path is a pulse row, which covers the class-less implicit pulse). A qubit, a channel or a
      gate macro copied into ``operations`` made ``Quam.load()`` fail for the
      whole chip ("Required type Pulse, Actual type XYDriveMW");
    * an explicit ``id`` that is not the copy's own name is dropped (quam's
      ``Pulse.name`` answers the id first, so a copy on q3 named by the q1-2
      gate's op would play under the SOURCE's label); it is appended to
      *dropped_out* as ``"id <old value>"``.

    An absolute link kept verbatim that points into ANOTHER qubit or pair
    (not the target's own entity) is appended to *kept_out* as
    ``"<field> -> <pointer>"``: editing the copy's field then retunes that
    other entity, which the caller must say.

    Raises ``ValueError`` when the source is not a pulse or a pointer that
    must be materialized does not resolve.
    """
    from quam_state_manager.core.pulse_catalog import is_pulse_class
    ft = resolve_field_target(merged, src_path)
    if not ft.get("resolvable"):
        raise ValueError(f"{src_path} does not resolve to a pulse")
    real = ft.get("resolved_path") or src_path
    # resolved_value carries LEAF values only; a pulse is a dict -- walk to it
    _found, body = _seg_walk(merged, real.split("."))
    if not isinstance(body, dict):
        raise ValueError(f"{src_path} is not a pulse (a {type(body).__name__})")
    cls = body.get("__class__")
    # no __class__: the chip's own implicit pulse (an op / gate slot whose
    # class quam takes from the annotation) -- the caller vouches it is a
    # pulse row; a declared class must be a pulse class
    if cls is not None and not is_pulse_class(cls):
        what = str(cls).rsplit(".", 1)[-1] or repr(cls)
        raise ValueError(
            f"{src_path} is not a pulse (it is {what}) -- only a pulse can be "
            "copied into a channel's operations")
    src_segs = real.split(".")
    dst_segs = dst_path.split(".")
    src_owner, dst_owner = _owner_segs(src_segs), _owner_segs(dst_segs)
    notes: list[str] = []

    def exists(segs: list[str]) -> bool:
        found, _ = _seg_walk(merged, segs)
        return found

    def materialize(target: list[str], rel: list[str]) -> Any:
        r = resolve_field_target(merged, ".".join(target))
        if not r.get("resolvable"):
            raise ValueError(
                f"{'.'.join(rel)} points at {'.'.join(target)}, which does "
                "not resolve on the source")
        notes.append(".".join(rel))
        _f, val = _seg_walk(merged, (r.get("resolved_path") or "").split("."))
        return copy.deepcopy(val if _f else r.get("resolved_value"))

    def rewrite(node: Any, rel: list[str]) -> Any:
        if isinstance(node, dict):
            return {k: rewrite(v, rel + [k]) for k, v in node.items()}
        if isinstance(node, list):
            return [rewrite(v, rel + [str(i)]) for i, v in enumerate(node)]
        if not is_pointer(node):
            return node
        holder_src = src_segs + rel
        holder_dst = dst_segs + rel
        target = pointer_to_abs(node, holder_src)
        if target is None:
            return node
        if _is_inside(target, src_segs):
            return _derive_pointer(_flavor(node), holder_dst,
                                   dst_segs + target[len(src_segs):])
        if _flavor(node) != "#/":
            landing = pointer_to_abs(node, holder_dst)
            if landing is not None and (_is_inside(landing, dst_segs)
                                        or exists(landing)):
                return node
            return materialize(target, rel)
        if src_owner and dst_owner and _is_inside(target, src_owner):
            mapped = dst_owner + target[len(src_owner):]
            if exists(mapped):
                return "#/" + "/".join(mapped)
            return materialize(target, rel)
        t_owner = _owner_segs(target)
        if (kept_out is not None and t_owner
                and not (dst_owner and _is_inside(target, dst_owner))):
            kept_out.append(f"{'.'.join(rel)} -> {node}")
        return node

    out = rewrite(copy.deepcopy(body), [])
    own_name = dst_segs[-1] if len(dst_segs) >= 2 and dst_segs[-2] == "operations" else None
    pid = out.get("id")
    if own_name and pid is not None and pid != own_name:
        del out["id"]
        if dropped_out is not None:
            dropped_out.append(f"id {pid!r}")
    return out, notes
