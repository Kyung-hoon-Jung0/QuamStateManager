"""Dynamic qubit-property column derivation for the Live State Edit qubit grid.

The curated ``param_specs._BULK_COLUMNS_SPEC`` covers the ~46 high-churn fields;
everything ELSE a chip's qubits carry (per-pulse parameters, port filter taps,
gate-fidelity breakdowns, ``extras``, lab-custom leaves) was unreachable from
Table View — searching "exponential_filter" found nothing.  This module mirrors
``pair_columns.derive_pair_columns``: walk every ``qubits.<qid>`` subtree down
to its leaves, templatize per-neighbor operation suffixes, classify leaf kinds,
drop all-null columns, and return an opt-in (all ``default_on=False``) column
model the ``/bulk`` route renders only when explicitly requested (``?dyncols=``).

Design decisions (from the pair-grid precedents + the r6 item-4 brief):

* **Anchored entity-suffix strip** — a per-neighbor operation id like
  ``cr_cosine_q41-40`` or ``cz_flattop_pulse_qA4`` collapses by stripping an
  exact trailing ``_<entity>`` where entity ∈ the chip's qubit ids ∪ pair ids,
  ONLY under an ``operations`` parent (longest entity wins, never a global
  replace) — heterogeneous neighbor suffixes fold into ONE column while
  mid-string matches are never corrupted.
* **Dedupe against the curated spec** — a derived template equal to a curated
  ``tmpl`` is dropped: the curated column already renders it (often default-on)
  and a twin would double-write through the same resolved node.
* **Port leaves resolve through the pointer chain** — a channel's
  ``opx_output``/``opx_input`` wiring POINTER is not itself a column; the
  resolved port dict's SCALAR + LIST leaves become
  ``qubits.{name}.<chan>.<io>.<leaf>`` templates (the alias path, so cells ride
  the same state→wiring→ports.* resolution as the curated port columns).
  Nested port dicts (multi-DUC ``upconverters``) are skipped — a dict is not a
  grid cell.
* **Kinds** — real JSON list → ``listedit`` (whole-value ✎ JSON popup);
  ``#./`` self-ref → ``runtime`` (read-only ⟳, exactly like the pair grid:
  editing ``operations.x180 = "#./x180_DragCosine"`` or ``#./inferred_*``
  would overwrite the pointer with a literal); everything else (scalar / null /
  cross-ref pointer) → ``edit``.
* **All-null columns dropped** (pair-grid precedent); derivation **cached** per
  ``(store → mutation_seq)`` in a ``WeakKeyDictionary``; **capped** at
  ``MAX_DYNAMIC_COLUMNS`` with an honest ``kind="note"`` truncation entry.
"""

from __future__ import annotations

import re
from typing import Any
from weakref import WeakKeyDictionary

from quam_state_manager.core.param_specs import _BULK_COLUMNS_SPEC
from quam_state_manager.core.pair_columns import _SEG_SHORT, _humanize, _unit_of
from quam_state_manager.core.pointer_resolver import is_pointer, is_self_ref

# Identity / structural keys — never become columns. digital_marker is a REAL
# value on modern chips (a marker name), so it deliberately stays.
_SKIP_KEYS = {"__class__", "id"}

# Channel-level port pointer keys — expanded into "<Chan> Port+" leaf columns
# instead of surfacing the raw wiring pointer string itself.
_IO_KEYS = ("opx_output", "opx_input")
_IO_SHORT = {"opx_output": "out", "opx_input": "in"}

# Column-count armor: a pathological chip can't ship a 5,000-entry menu model.
MAX_DYNAMIC_COLUMNS = 400

_CURATED_TMPLS: frozenset[str] = frozenset(c["tmpl"] for c in _BULK_COLUMNS_SPEC)

# store → (invalidation key, (columns, curated_tmpls)). The walk is pure over
# store.merged; mutation_seq is bumped on every edit but RESET on a reload, so
# the key also folds id(merged) (a reload swaps the merged dict object).
_CACHE: "WeakKeyDictionary[Any, tuple[tuple, tuple[list[dict], set[str]]]]" = (
    WeakKeyDictionary()
)


def _strip_entity_suffix(key: str, entities: tuple[str, ...]) -> str:
    """Strip an exact trailing ``_<entity>`` (anchored, once; longest wins)."""
    for ent in entities:
        suffix = "_" + ent
        if len(key) > len(suffix) and key.endswith(suffix):
            return key[: -len(suffix)]
    return key


def _col_key(tmpl_segs: list[str]) -> str:
    return "dyn__" + re.sub(r"[^A-Za-z0-9_]+", "_", ".".join(tmpl_segs))


def _kind_of(value: Any) -> str:
    if isinstance(value, list):
        return "listedit"          # editable whole-value via the ✎ JSON popup
    if is_self_ref(value):
        return "runtime"           # #./ alias/inferred — editing breaks the link
    return "edit"                  # scalar, None, or cross-ref pointer (write-through)


def _make_leaf(qid: str, real_segs: list[str], tmpl_segs: list[str], value: Any,
               *, port: bool, chan_order: dict[str, int]) -> dict:
    head = tmpl_segs[0]
    if port:
        chan_order.setdefault(head, len(chan_order))
        sec_key, sec_label = "port:" + head, _humanize(head) + " Port+"
        io = tmpl_segs[-2]
        label = _IO_SHORT.get(io, io) + " · " + tmpl_segs[-1]
    elif len(tmpl_segs) == 1:
        sec_key, sec_label = "qubit", "Qubit+"
        label = tmpl_segs[0]
    elif head == "extras":
        sec_key, sec_label = "extras", "Extras"
        label = " · ".join(tmpl_segs[1:])
    else:
        chan_order.setdefault(head, len(chan_order))
        sec_key, sec_label = "chan:" + head, _humanize(head) + "+"
        label = " · ".join(_SEG_SHORT.get(s, s) for s in tmpl_segs[1:])
    return {
        "tmpl_segs": tmpl_segs,
        "tmpl": "qubits.{name}." + ".".join(tmpl_segs),
        "section_key": sec_key, "section": sec_label,
        "label": label, "unit": _unit_of(str(real_segs[-1])),
        "kind": _kind_of(value), "value": value,
    }


def _port_leaves(qid: str, real_segs: list[str], tmpl_segs: list[str],
                 merged: dict, leaves: list[dict],
                 chan_order: dict[str, int]) -> None:
    """Enumerate a wired port's scalar + list leaves through the pointer chain."""
    from quam_state_manager.core.pointer_path import _walk as _walk_abs, resolve_field_target
    try:
        ft = resolve_field_target(merged, "qubits." + qid + "." + ".".join(real_segs))
    except Exception:  # noqa: BLE001 — a broken wiring pointer yields no columns
        return
    if not ft.get("resolvable"):
        return
    # resolved_value is scalar-nulled for containers — fetch the real port dict.
    found, port = _walk_abs(merged, (ft.get("resolved_path") or "").split("."))
    if not found or not isinstance(port, dict):
        return
    for k, v in port.items():
        if k in _SKIP_KEYS or isinstance(v, dict):
            continue          # nested dicts (multi-DUC upconverters) never become columns
        leaves.append(_make_leaf(qid, real_segs + [k], tmpl_segs + [k], v,
                                 port=True, chan_order=chan_order))


def _walk_qubit(qid: str, node: Any, real_segs: list[str], tmpl_segs: list[str],
                entities: tuple[str, ...], merged: dict, leaves: list[dict],
                chan_order: dict[str, int]) -> None:
    """Recurse one qubit object, appending leaf descriptors.

    Guards ``None`` / non-dict at every level; an empty dict yields nothing.
    """
    if not isinstance(node, dict):
        return
    parent = tmpl_segs[-1] if tmpl_segs else None
    for k, v in node.items():
        if k in _SKIP_KEYS:
            continue
        tk = _strip_entity_suffix(k, entities) if parent == "operations" else k
        r2 = real_segs + [k]
        t2 = tmpl_segs + [tk]
        if k in _IO_KEYS and is_pointer(v) and not is_self_ref(v):
            _port_leaves(qid, r2, t2, merged, leaves, chan_order)
            continue
        if isinstance(v, dict):
            if v:
                _walk_qubit(qid, v, r2, t2, entities, merged, leaves, chan_order)
            continue          # empty dict → no leaf
        leaves.append(_make_leaf(qid, r2, t2, v, port=False, chan_order=chan_order))


def _order_key(col: dict, chan_order: dict[str, int]) -> tuple:
    sk = col["section_key"]
    if sk.startswith("chan:"):
        base = 100 + chan_order.get(sk[5:], 0) * 10
    elif sk.startswith("port:"):
        base = 100 + chan_order.get(sk[5:], 0) * 10 + 5   # a channel's port right after it
    elif sk == "qubit":
        base = 800
    else:                     # extras
        base = 900
    return (base, col["tmpl"])


def _derive(store) -> tuple[list[dict], set[str]]:
    with store._lock:
        merged = store.merged
        qubits = merged.get("qubits") or {}
        qids = list(store.qubit_names)
        entities = tuple(sorted(
            [*qids, *store.qubit_pair_names], key=len, reverse=True))
        chan_order: dict[str, int] = {}
        per_qubit: dict[str, list[dict]] = {}
        for qid in qids:
            leaves: list[dict] = []
            _walk_qubit(qid, qubits.get(qid) or {}, [], [], entities,
                        merged, leaves, chan_order)
            per_qubit[qid] = leaves

    cols: dict[str, dict] = {}
    order: list[str] = []
    for qid in qids:
        for lf in per_qubit[qid]:
            if lf["tmpl"] in _CURATED_TMPLS:
                continue      # the curated grid already renders this template
            ck = _col_key(lf["tmpl_segs"])
            # sanitize collision (a.b_c vs a.b.c) — disambiguate deterministically
            n = 1
            while ck in cols and cols[ck]["tmpl"] != lf["tmpl"]:
                n += 1
                ck = _col_key(lf["tmpl_segs"]) + "_" + str(n)
            col = cols.get(ck)
            if col is None:
                col = {"key": ck, "label": lf["label"], "section": lf["section"],
                       "section_key": lf["section_key"], "unit": lf["unit"],
                       "tmpl": lf["tmpl"], "kinds": set(), "nonnull": 0}
                cols[ck] = col
                order.append(ck)
            col["kinds"].add(lf["kind"])
            if lf["value"] is not None:
                col["nonnull"] += 1

    # Drop columns that are null on every qubit (pair-grid precedent), then order.
    kept = [cols[k] for k in order if cols[k]["nonnull"] > 0]
    kept.sort(key=lambda c: _order_key(c, chan_order))

    out: list[dict] = []
    for c in kept:
        ks = c["kinds"]
        if "listedit" in ks:
            kind = "listedit"     # any list cell ⇒ the ✎ popup column
        elif ks == {"runtime"}:
            kind = "runtime"
        else:
            kind = "edit"
        out.append({"key": c["key"], "label": c["label"], "section": c["section"],
                    "unit": c["unit"], "tmpl": c["tmpl"], "kind": kind,
                    "default_on": False})

    if len(out) > MAX_DYNAMIC_COLUMNS:
        dropped = len(out) - MAX_DYNAMIC_COLUMNS
        out = out[:MAX_DYNAMIC_COLUMNS]
        out.append({"key": "__dyn_truncated__",
                    "label": f"… {dropped} more not shown "
                             f"({MAX_DYNAMIC_COLUMNS}-column cap)",
                    "section": "Extras", "unit": "", "tmpl": "",
                    "kind": "note", "default_on": False})
    return out, set(_CURATED_TMPLS)


def derive_qubit_columns(store) -> tuple[list[dict], set[str]]:
    """Return ``(columns, curated_tmpls)`` for the loaded chip's qubits.

    ``columns`` — ordered list of ``{key, label, section, unit, tmpl, kind,
    default_on}`` (channel groups first, each followed by its Port+ group,
    then Qubit+ direct scalars, Extras last). Every column is ``default_on=
    False`` — dynamic columns are strictly opt-in via ``?dyncols=``.
    ``curated_tmpls`` — the curated templates the derivation deduped against.

    Callers get fresh dict copies (the route stamps ``group_start`` etc. onto
    its column dicts; the cache master must stay pristine).
    """
    key = (getattr(store, "mutation_seq", None), id(store.merged))
    try:
        cached = _CACHE.get(store)
    except TypeError:         # non-weakref-able store (defensive)
        cached = None
    if cached is not None and cached[0] == key:
        cols, curated = cached[1]
        return [dict(c) for c in cols], set(curated)
    result = _derive(store)
    try:
        _CACHE[store] = (key, result)
    except TypeError:
        pass
    cols, curated = result
    return [dict(c) for c in cols], set(curated)
