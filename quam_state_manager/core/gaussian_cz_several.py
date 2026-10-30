"""Gaussian CZ "to several pairs": one read-only plan for every eligible pair.

Each pair gets its own pulse names (``gaussian_cz.plan(suffix_ids=...)``), so
two pairs that share a moving qubit never collide on its z line. A pair whose
names are already taken is Can't -- this path never offers Replace (that stays
on the single-pair button). The lab's own code is asked ONCE for the whole
batch (``lab_check``, the page's existing ``_lab_write_refusal`` bound to the
store) and its refused write indices are mapped back to pairs.

Status words are :mod:`add_verdict`'s, so the dialog and the rules dialog say
the same thing. ``plan_sig`` is a hash of everything the dialog showed (the
parameters, every pair's verdict and every value to be written): an apply with
a different signature means the chip moved since the dialog was opened.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any, Callable

from quam_state_manager.core import gate_action, gaussian_cz
from quam_state_manager.core.add_verdict import CANT, READY


def _reason(error: str) -> str:
    """The Can't reason in the dialog's words."""
    prefix = "a gate or operation named "
    if error.startswith(prefix) and error.endswith(" already exists"):
        name = error[len(prefix):-len(" already exists")]
        if any(name == macro for macro, _ in gaussian_cz.VARIANTS):
            return f"a gate named {name} already exists"
    return error


def _lab_reason_suffix(merged: dict, pair: str) -> str:
    try:
        return gate_action.role_input(merged, pair)["reason"]
    except Exception:  # noqa: BLE001 -- a label only, never a verdict
        return "by chip f_01"


def build(merged: dict, params: dict, *,
          lab_check: Callable[..., Any] | None = None) -> dict:
    """The plan for every eligible pair; read-only.

    ``lab_check(writes, info=...)`` returns ``(message, refused_indices)`` or
    None, like ``routes._lab_write_refusal``.
    """
    rows: list[dict] = []
    writes: list[tuple[str, Any]] = []
    owner: list[int] = []
    for info in gaussian_cz.eligible_pairs(merged):
        pid = info["pair_id"]
        row: dict[str, Any] = {"pair": pid, "control": None, "target": None,
                               "moving": None, "pulses": [], "status": READY,
                               "reason": "", "creates": []}
        rows.append(row)
        try:
            control, target = gate_action.pair_members(merged, pid)
        except ValueError as exc:
            row.update(status=CANT, reason=str(exc))
            continue
        row.update(control=control, target=target)
        res = gaussian_cz.plan(merged, pid, suffix_ids=(control, target), **params)
        if "error" in res:
            row.update(status=CANT, reason=_reason(res["error"]))
            continue
        row["moving"] = res["sources"]["moving_qubit"]
        row["creates"] = res["creates"]
        row["pulses"] = list(dict.fromkeys(
            p.rsplit(".", 1)[-1] for p, _ in res["creates"] if ".macros." not in p))
        for path, value in res["creates"]:
            writes.append((path, value))
            owner.append(len(rows) - 1)
    notes: list[str] = []
    if writes and lab_check is not None:
        info: dict = {}
        refusal = lab_check(writes, info=info)
        if refusal:
            message, indices = refusal
            for i in {owner[j] for j in indices}:
                rows[i].update(status=CANT, reason=str(message), creates=[], pulses=[])
        notes = [n for n in (info.get("notes") or []) if n]
    ready = [r for r in rows if r["status"] == READY]
    cant_reasons: dict[str, list[str]] = {}
    for r in rows:
        if r["status"] == CANT:
            cant_reasons.setdefault(r["reason"], []).append(r["pair"])
    canonical = json.dumps(
        {"params": params,
         "rows": [[r["pair"], r["status"], r["reason"], r["creates"]] for r in rows]},
        sort_keys=True, separators=(",", ":"), default=str)
    suffix = _lab_reason_suffix(merged, rows[0]["pair"]) if rows else ""
    return {
        "rows": rows,
        "plan_sig": hashlib.sha1(canonical.encode("utf-8")).hexdigest(),
        "total": len(rows),
        "ready": [{"pair": r["pair"], "control": r["control"], "target": r["target"],
                   "moving": r["moving"], "pulses": r["pulses"]} for r in ready],
        "cant": [{"reason": reason, "pairs": pairs}
                 for reason, pairs in cant_reasons.items()],
        "reason_suffix": suffix,
        "notes": notes,
    }
