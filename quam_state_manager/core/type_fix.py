"""One-click repair for numbers stored as TEXT (docs/77).

SM already *detects* the anomaly — a state.json where ``0.13`` was written as
``"0.13"`` — and warns about it (r14, docs/56 amendment). Repairing it used to
mean visiting every field and retyping the value. This module builds the
**plan** behind the one-click fix: for every offending leaf, what is stored
now, what SM proposes to store instead, and which type that makes it.

The plan is the whole safety story, so it is pure (no Flask, no I/O) and
independently testable. Two rules shape it:

**Nothing is converted that SM cannot argue for.** A value only becomes a
candidate when the text is an unambiguous plain number AND nothing says the
field is genuinely textual. Everything else is *excluded with a reason the
user can read* — never silently dropped, never silently converted.

**The proposal never changes the number.** The parse must round-trip to the
same numeric value, so a fix can only ever change the stored TYPE. Text that
would change meaning (``"4,8"`` → ``48``) or that is probably an identifier
(``"02"``, ``"007"``) is excluded rather than guessed at.
"""

from __future__ import annotations

import hashlib
import math
import re
from typing import Any, Optional

# A plan bigger than this is reported honestly rather than rendered — a
# thousand-row confirmation dialog is not a confirmation.
PLAN_CAP = 500

# "02" / "007" / "-0042": a leading zero is how humans write labels (port 02,
# channel 007), not numbers. Converting would silently rewrite the text.
_LEADING_ZERO = re.compile(r"^[+-]?0\d")


class SkipReason:
    READ_ONLY = "identity / membership key — read-only"
    ENV_TEXT = "the environment's schema types this field as text"
    LEADING_ZERO = "leading zero — the text reads like a label, not a number"
    SEPARATOR = "contains a separator — could be a grouped number or a pair"
    NOT_PLAIN = "not a plain finite number"
    NOT_A_STRING = "no longer stored as text"


def _parse_plain(raw: str) -> Optional[float | int]:
    """int/float for an UNAMBIGUOUS plain numeric string, else None.

    Deliberately stricter than :func:`type_policy.parse_value`: no comma
    stripping (``"4,8"`` is a grid location, not 48), no bool words, no JSON.
    """
    s = raw.strip()
    if not s or "," in s or "_" in s:
        return None
    try:
        f = float(s)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(f):
        return None
    # int only when the text itself is written as one — "1.0" stays real.
    if re.fullmatch(r"[+-]?\d+", s):
        try:
            return int(s)
        except ValueError:
            return None
    return f


def _env_says_text(policy: Any, merged: dict, path: str) -> bool:
    """True when the ENV SCHEMA (not an inference) types this field str."""
    if policy is None:
        return False
    try:
        exp = policy.expected_for(merged, path, infer=False)
    except Exception:  # noqa: BLE001 — a policy bug must never block the plan
        return False
    if exp is None or not getattr(exp, "enforced", False):
        return False
    spec = getattr(exp, "spec", None) or {}
    return spec.get("base") == "str"


def _env_types_numeric(policy: Any, merged: dict, path: str) -> bool:
    """True when an ENFORCED expectation already types this field numeric —
    such a field needs no user assignment: the write itself stores a number
    (modifier._checked_value bypasses the legacy string coercion)."""
    if policy is None:
        return False
    try:
        exp = policy.expected_for(merged, path, infer=False)
    except Exception:  # noqa: BLE001
        return False
    if exp is None or not getattr(exp, "enforced", False):
        return False
    spec = getattr(exp, "spec", None) or {}
    return spec.get("base") in ("int", "float", "number", "real")


def plan_signature(rows: list[dict], skipped: list[dict]) -> str:
    """Stable fingerprint of (path, stored text) over the WHOLE anomaly set.

    The apply step re-derives the plan and compares this, so a fix computed
    against one chip state can never be applied to another (the same doctrine
    as the diagnostics one-click fix: re-validate, never trust the rendered
    form).
    """
    parts = [f"{r['path']}\x00{r['current_raw']}" for r in rows]
    parts += [f"{s['path']}\x00{s.get('current_raw', '')}" for s in skipped]
    return hashlib.sha1("\n".join(sorted(parts)).encode()).hexdigest()[:16]


def build_plan(store: Any, *, policy: Any = None, paths: list[str] | None = None,
               editability: Any = None) -> dict:
    """Describe the stored-as-text repair for the loaded chip.

    ``rows`` are convertible (each carries what it is now, what it becomes and
    the resulting type); ``skipped`` are the ones SM refuses to guess at, each
    with a reason. ``sig`` fingerprints the anomaly set for the apply step.
    """
    from quam_state_manager.core.diagnostics import numeric_string_leaves
    from quam_state_manager.core.edit_policy import editability_reason
    from quam_state_manager.core.type_policy import format_type

    editability = editability or editability_reason
    if policy is None:
        policy = getattr(store, "type_policy", None)
    state = getattr(store, "state", None) or {}
    merged = getattr(store, "merged", None) or {}

    candidates = list(paths) if paths is not None else numeric_string_leaves(state)

    rows: list[dict] = []
    skipped: list[dict] = []

    def _skip(path: str, raw: Any, reason: str) -> None:
        skipped.append({"path": path,
                        "current_raw": raw if isinstance(raw, str) else "",
                        "current_display": _display(raw),
                        "reason": reason})

    for path in candidates:
        try:
            raw = store.get_value(path)
        except Exception:  # noqa: BLE001 — a vanished path is not an error here
            continue
        if not isinstance(raw, str):
            _skip(path, raw, SkipReason.NOT_A_STRING)
            continue
        reason = editability(store, path)
        if reason:
            _skip(path, raw, SkipReason.READ_ONLY)
            continue
        if _env_says_text(policy, merged, path):
            _skip(path, raw, SkipReason.ENV_TEXT)
            continue
        if "," in raw or "_" in raw:
            _skip(path, raw, SkipReason.SEPARATOR)
            continue
        if _LEADING_ZERO.match(raw.strip()):
            _skip(path, raw, SkipReason.LEADING_ZERO)
            continue
        parsed = _parse_plain(raw)
        if parsed is None:
            _skip(path, raw, SkipReason.NOT_PLAIN)
            continue
        kind = "int" if isinstance(parsed, int) else "real"
        rows.append({
            "path": path,
            "current_raw": raw,
            "current_display": _display(raw),
            "proposed_value": parsed,
            "proposed_display": repr(parsed) if isinstance(parsed, float) else str(parsed),
            "proposed_type": kind,
            "type_label": format_type(kind) if kind != "real" else "real",
            # A field the env already types numeric is repaired by the write
            # alone; only an untyped field needs the user assignment that
            # makes the numeric type stick.
            "needs_assignment": not _env_types_numeric(policy, merged, path),
        })

    sig = plan_signature(rows, skipped)
    truncated = len(rows) > PLAN_CAP
    return {
        "rows": rows[:PLAN_CAP],
        "skipped": skipped,
        "sig": sig,
        "total": len(rows),
        "truncated": truncated,
        "hidden": max(0, len(rows) - PLAN_CAP),
    }


def _display(value: Any) -> str:
    """How the stored value is shown — quoted, so 'text' is visible as text."""
    if isinstance(value, str):
        return f'"{value}"'
    if value is None:
        return "null"
    return str(value)
