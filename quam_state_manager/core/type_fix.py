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


def strnum_signature(paths: list[str]) -> str:
    """Fingerprint of the stored-as-text anomaly SET (paths only).

    Deliberately NOT :func:`plan_signature`: this one gates whether the user
    is told again, and the user's judgement is about *which fields are text*.
    Folding the stored values in would re-raise the alarm every time an
    already-flagged field's text changed ("0.13" → "0.14"), which is nagging
    about a set the user already dismissed. Keeping the formula byte-identical
    to r14's also keeps every dismissal already on disk valid.
    """
    if not paths:
        return ""
    return hashlib.sha1("\n".join(sorted(paths)).encode("utf-8")).hexdigest()[:16]


def env_signature(findings: list[dict]) -> str:
    """Fingerprint of the env-schema mismatch SET.

    Keyed on the aggregated identity ``(kind, class, field, code)`` and NOT on
    the instance counts: one defect on a 21st qubit is the same defect the user
    already judged, so it must not re-raise.
    """
    keys = sorted(
        "\x00".join(str(f.get(k) or "") for k in ("kind", "class", "field", "code"))
        for f in (findings or [])
    )
    if not keys:
        return ""
    return hashlib.sha1("\n".join(keys).encode("utf-8")).hexdigest()[:16]


def env_items(findings: list[dict], *, cap: int = 5) -> list[dict]:
    """Display records for the env-schema mismatches (same location grammar as
    ``state_env_validate.to_diag_findings``, so the popup, the card and the
    diagnostics list all name a defect identically)."""
    out: list[dict] = []
    for rec in (findings or [])[:cap]:
        cls = (rec.get("class") or "").rsplit(".", 1)[-1]
        fld = rec.get("field") or ""
        loc = f"{cls}.{fld}" if cls and fld else (cls or fld or "state")
        examples = rec.get("example_paths") or []
        out.append({
            "location": loc,
            "kind": rec.get("kind") or "",
            "code": rec.get("code") or "",
            "severity": rec.get("severity") or "warning",
            "message": rec.get("detail") or rec.get("kind") or "env mismatch",
            "fix_hint": rec.get("fix_hint") or "",
            "example_path": examples[0] if examples else "",
            "count": rec.get("count") or 0,
        })
    return out


def alert_summary(plan: dict | None, env_findings: list[dict] | None,
                  paths: list[str] | None) -> dict:
    """The two anomaly classes in one payload, for the alert popup + the card.

    They are deliberately kept apart: stored-as-text is repairable by SM (the
    plan says exactly how), while an env-schema mismatch is a DISAGREEMENT
    between the chip and the selected environment — SM reports it and the user
    decides, because the library may simply have changed.
    """
    plan = plan or {}
    rows = plan.get("rows") or []
    env_findings = list(env_findings or [])
    strnum_count = len(paths or [])
    examples = [{"path": r["path"], "current": r["current_display"],
                 "proposed": r["proposed_display"], "type": r["proposed_type"]}
                for r in rows[:3]]
    env_errors = sum(1 for f in env_findings if f.get("severity") == "error")
    # docs/136 — two honesty repairs to this line.
    #
    # (a) Findings are AGGREGATED by (kind, class, field): four findings can
    #     stand for twenty-three actual places in the state. "4 fields don't
    #     match" then undercounts the work by a factor of six.
    # (b) A class the env cannot import at all is not a FIELD. On the
    #     customer's chip the whole `QdacBiasLine` class is missing when
    #     quam_config is absent, and calling that a field mismatch sends the
    #     reader looking for a typo instead of a missing package.
    class_kinds = {"unimportable_class", "unknown_class"}
    env_classes = sum(1 for f in env_findings if f.get("kind") in class_kinds)
    env_fields = len(env_findings) - env_classes
    env_places = sum(int(f.get("count") or 1) for f in env_findings)
    return {
        "strnum": {
            "count": strnum_count,
            "fixable": plan.get("total", len(rows)),
            "skipped": len(plan.get("skipped") or []),
            "examples": examples,
        },
        "env": {
            "count": len(env_findings),
            "classes": env_classes,
            "fields": env_fields,
            "places": env_places,
            "errors": env_errors,
            "warnings": len(env_findings) - env_errors,
            # NOT "items": Jinja resolves ``env.items`` to the dict method, so a
            # template would silently iterate the wrong thing.
            "entries": env_items(env_findings),
        },
        "total": strnum_count + len(env_findings),
    }


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
