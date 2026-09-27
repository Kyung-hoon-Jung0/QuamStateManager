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

import bisect
import copy
import logging
from typing import Any

from quam_state_manager.core import qdac
from quam_state_manager.core.loader import _walk
from quam_state_manager.core.loader import natural_key
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


def list_pulses(merged: dict, *, with_used_by: bool = True,
                only: tuple[str, str] | None = None) -> list[dict]:
    """Flat row list of every pulse-shaped node in the chip (see module doc).

    ``with_used_by=False`` skips building the reverse-pointer index (the single
    most expensive step, ~27 ms on a 21-qubit chip) and leaves each row's
    ``used_by`` empty. Callers that don't need the reverse-pointer column — the
    waveform DAC-range diagnostics, which run on every edit — pass False for a
    big speed-up.

    ``only=("qubit", name)`` / ``("pair", name)`` restricts the rows to that
    one owner, in the order the full list would give them (the diagnostics
    waveform lint memoizes per owner; docs/2xx RAM P5).
    """
    reverse_index = build_reverse_pointer_index(merged) if with_used_by else None
    op_referrers = build_op_referrers(reverse_index) if reverse_index is not None else None
    return _list_pulses_with(merged, reverse_index, op_referrers, only=only)


def _list_pulses_with(merged: dict, reverse_index, op_referrers, *,
                      only: tuple[str, str] | None = None) -> list[dict]:
    """:func:`list_pulses` over indexes the caller already built (w7/pulses);
    ``only`` as in :func:`list_pulses` (w7/liveedit)."""
    rows: list[dict] = []

    for qubit_name, qubit in (merged.get("qubits") or {}).items():
        if not isinstance(qubit, dict):
            continue
        if only is not None and only != ("qubit", qubit_name):
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
                    rows.append(_row_for_pulse(
                        merged, path, body, owner_kind="qubit", owner=qubit_name,
                        channel=f"{bias_field}.opx_trigger_out", op_name=op_name,
                        gate=None, reverse_index=reverse_index,
                        op_referrers=op_referrers))

    for pair_name, pair in (merged.get("qubit_pairs") or {}).items():
        if not isinstance(pair, dict):
            continue
        if only is not None and only != ("pair", pair_name):
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
                    rows.append(_row_for_pulse(
                        merged, path, body, owner_kind="pair", owner=pair_name,
                        channel=slot, op_name=f"{gate_name}.{slot}", gate=gate_name,
                        reverse_index=reverse_index, op_referrers=op_referrers))
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
                rows.append(_row_for_pulse(
                    merged, path, body, owner_kind="pair", owner=pair_name,
                    channel=channel, op_name=op_name, gate=None,
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
    """RAM cache of rows + reverse index + sparklines for one store.

    Validate on read (design ram_design.md 1.1). Every read compares the
    stamp the rows were built at -- ``(store.mutation_seq, the pulse catalog's
    env overlay object)`` -- with the store's current one, so a stale entry is
    never served, even to code paths that forget :meth:`invalidate`.

    What changed since the stamp decides HOW the rows catch up
    (docs/2xx pulses RAM):

    * nothing -> served as-is;
    * only scalar->scalar value writes (``QuamStore.mutations_since`` vouches
      for every step, none structural) -> INCREMENTAL: a value write cannot
      move a pointer, so the reverse index and ``used_by`` stay exactly as
      they are, and only the rows that can SEE a written path are recomputed:
      the row whose operation holds it, plus every row reaching it through a
      pointer chain (transitively: a pointer AT it or at an ancestor of it,
      and a pointer THROUGH it);
    * anything else (create / delete / pointer move / ``__class__`` / reload /
      an unjournaled bump / an env overlay swap) -> recomputed cold.

    ``SM_RAM_VERIFY=1`` (shadow mode, tests) recomputes every incremental
    update and every sparkline hit cold and raises
    :class:`~quam_state_manager.core.ramcache.StaleCacheError` on a difference.

    A recomputed row is a NEW dict; an untouched row keeps its identity. The
    sparkline cache keys on that identity, so a field commit re-synthesizes
    only the pulses whose waveform inputs it could have changed.
    """

    def __init__(self, store) -> None:
        self.store = store
        self._rows: list[dict] | None = None
        self._pos: dict[str, int] = {}
        self._reverse: dict[str, list[str]] | None = None
        self._targets: list[str] | None = None     # sorted reverse-index keys
        self._op_referrers: dict[str, list[str]] | None = None
        self._seq: int = -1
        self._overlay: Any = _NO_OVERLAY
        self._drop_hint = False
        # op path -> (row object the SVG was drawn for, svg)
        self._spark: dict[str, tuple[dict, str | None]] = {}
        # counters, for tests and debug surfaces
        self.stats = {"cold": 0, "incremental": 0, "rows_recomputed": 0,
                      "spark_hit": 0, "spark_miss": 0}

    # -- validation ----------------------------------------------------

    def invalidate(self) -> None:
        """Hint that the store's content changed. Kept for the callers that
        run after every edit, but correctness never depends on it: reads
        validate against ``mutation_seq`` and the mutation journal. When the
        seq has NOT moved since the rows were built, the hint drops them --
        the one case a read could not detect by itself."""
        self._drop_hint = True

    def _drop(self) -> None:
        self._rows = None
        self._pos = {}
        self._reverse = None
        self._targets = None
        self._op_referrers = None
        self._seq = -1
        self._spark = {}

    def _rebuild_cold(self, seq: int, overlay: Any) -> None:
        merged = self.store.merged
        reverse = build_reverse_pointer_index(merged)
        op_ref = build_op_referrers(reverse)
        self._rows = _list_pulses_with(merged, reverse, op_ref)
        self._pos = {r["path"]: i for i, r in enumerate(self._rows)}
        self._reverse = reverse
        self._targets = sorted(reverse)
        self._op_referrers = op_ref
        self._seq = seq
        self._overlay = overlay
        self.stats["cold"] += 1

    def _sync(self) -> None:
        """Bring the rows up to the store's current stamp. Under store._lock."""
        seq = self.store.mutation_seq
        overlay = _catalog_overlay()
        hint, self._drop_hint = self._drop_hint, False
        if self._rows is not None and self._overlay is overlay:
            if self._seq == seq:
                if not hint:
                    return
            else:
                mut_since = getattr(self.store, "mutations_since", None)
                steps = mut_since(self._seq) if mut_since is not None else None
                if steps and all(vo and isinstance(p, str) for _, p, vo in steps):
                    if self._apply_value_steps([p for _, p, _ in steps], seq):
                        return
        self._drop()
        self._rebuild_cold(seq, overlay)

    def _holders_touching(self, path: str) -> list[str]:
        """Pointer holders whose target is *path*, an ancestor of it, or a
        descendant of it (a pointer THROUGH an alias that *path* is)."""
        rev = self._reverse or {}
        out: list[str] = []
        segs = path.split(".")
        for n in range(len(segs), 0, -1):
            out.extend(rev.get(".".join(segs[:n]), ()))
        targets = self._targets or []
        prefix = path + "."
        i = bisect.bisect_left(targets, prefix)
        while i < len(targets) and targets[i].startswith(prefix):
            out.extend(rev[targets[i]])
            i += 1
        return out

    def _rows_seeing(self, paths: list[str]) -> set[str]:
        """Op paths of every row that can see a write at any of *paths*. A
        value written AT an op path (an "(invalid)" scalar-bodied row) is that
        row's body: it is found as its own root and recomputed like any other."""
        seen: set[str] = set()
        frontier = list(paths)
        while frontier:
            x = frontier.pop()
            if x in seen:
                continue
            seen.add(x)
            frontier.extend(self._holders_touching(x))
        roots: set[str] = set()
        for x in seen:
            segs = x.split(".")
            for n in range(len(segs), 0, -1):
                cand = ".".join(segs[:n])
                if cand in self._pos:
                    roots.add(cand)
                    break
        return roots

    def _apply_value_steps(self, paths: list[str], seq: int) -> bool:
        roots = self._rows_seeing(paths)
        merged = self.store.merged
        new_rows = list(self._rows)
        for op in roots:
            i = self._pos[op]
            old = new_rows[i]
            body = _get_path(merged, op)
            if body is _MISSING:
                return False
            new_rows[i] = _row_for_pulse(
                merged, op, body, owner_kind=old["owner_kind"], owner=old["owner"],
                channel=old["channel"], op_name=old["op_name"], gate=old["gate"],
                op_referrers=self._op_referrers)
        if _verify_on():
            cold = list_pulses(merged)
            if cold != new_rows:
                from quam_state_manager.core.ramcache import StaleCacheError
                bad = next((c["path"] for c, n in zip(cold, new_rows) if c != n),
                           "(row count)")
                raise StaleCacheError(
                    f"PulseIndex incremental rows differ from cold at {bad} "
                    f"(writes: {paths[:5]})")
        self._rows = new_rows
        self._seq = seq
        self.stats["incremental"] += 1
        self.stats["rows_recomputed"] += len(roots)
        return True

    # -- reads ---------------------------------------------------------

    def rows(self) -> list[dict]:
        """Every pulse row at the store's current content. The list and its
        dicts are shared: a caller copies a row before adding keys to it."""
        with self.store._lock:
            self._sync()
            return self._rows

    def row(self, op_path: str) -> dict | None:
        """The row of *op_path*, or None -- O(1)."""
        with self.store._lock:
            self._sync()
            i = self._pos.get(op_path)
            return self._rows[i] if i is not None else None

    def known_paths(self):
        """Every pulse op path, as an O(1) membership container."""
        with self.store._lock:
            self._sync()
            return self._pos

    def reverse_index(self) -> dict[str, list[str]]:
        with self.store._lock:
            self._sync()
            return self._reverse

    def used_by(self, op_path: str) -> list[str]:
        """Same answer as :func:`used_by` over the whole reverse index, but
        only the targets at or under *op_path* are visited (bisect)."""
        with self.store._lock:
            self._sync()
            rev = self._reverse
            targets = self._targets
            prefix = op_path + "."
            referrers: list[str] = []
            keys = [op_path] if op_path in rev else []
            i = bisect.bisect_left(targets, prefix)
            while i < len(targets) and targets[i].startswith(prefix):
                keys.append(targets[i])
                i += 1
            for k in keys:
                for h in rev[k]:
                    if not (h == op_path or h.startswith(prefix)):
                        referrers.append(h)
            return sorted(set(referrers), key=natural_key)

    def sparkline(self, op_path: str, render):
        """Memoized sparkline SVG for *op_path*. *render* is a 0-arg callable
        that produces the SVG (or None) on a miss. Valid while the op's ROW
        object is the one it was drawn for: a row survives a mutation only
        when that mutation could not reach its waveform inputs."""
        with self.store._lock:
            self._sync()
            i = self._pos.get(op_path)
            row = self._rows[i] if i is not None else None
            hit = self._spark.get(op_path)
        if row is not None and hit is not None and hit[0] is row:
            self.stats["spark_hit"] += 1
            if _verify_on():
                fresh = render()
                if fresh != hit[1]:
                    from quam_state_manager.core.ramcache import StaleCacheError
                    raise StaleCacheError(f"sparkline for {op_path} is stale")
            return hit[1]
        self.stats["spark_miss"] += 1
        svg = render()
        if row is not None:
            with self.store._lock:
                # store only if the row is still the current one (a write may
                # have landed while we rendered)
                self._sync()
                j = self._pos.get(op_path)
                if j is not None and self._rows[j] is row:
                    self._spark[op_path] = (row, svg)
        return svg


_NO_OVERLAY = object()
_MISSING = object()


def _catalog_overlay() -> Any:
    """The pulse catalog's env overlay OBJECT (overlay swaps are whole-object,
    and holding the reference keeps its identity from being reused)."""
    try:
        from quam_state_manager.core import pulse_catalog as _pc
        return _pc.env_overlay_active()
    except Exception:  # noqa: BLE001
        return None


def _verify_on() -> bool:
    from quam_state_manager.core.ramcache import _verify_on as _v
    return _v()


def _get_path(merged: dict, dot_path: str) -> Any:
    node: Any = merged
    for seg in dot_path.split("."):
        if isinstance(node, dict) and seg in node:
            node = node[seg]
        else:
            return _MISSING
    return node
