"""Domain-knowledge packs — versioned case manuals per family (docs/129).

A pack is data, not code: ``knowledge/v1/<family>/cases.json`` (machine) +
``cases.md`` (the same content for humans and the vision judge). It encodes
what the pilot corpus taught — map-geometry cases, orthogonal flags,
prescriptions as bounded knob moves, a provisional registry (rules expected
to change, managed as their own category by expert direction), and edge-case
references (concrete archived runs the loop must handle unaided).

Clause-B lint at load (the judge_pack rule, docs/78): a case whose geometry
or prescription names an absolute frequency/power or sizes a feature against
the swept window teaches the next chip a falsehood — such a case is DROPPED
and logged, never taught. Edge-case references are exempt (they cite specific
runs, where absolute values are the point).

Lab overlays (``instance/knowledge_overlay/<family>/cases.json``) are
ADDITIVE only: a new case id extends the pack, an existing id is refused —
a local edit must never silently replace verified shipped knowledge.

The pack content hash is part of a verdict's validity context: judgments made
under different manual versions are not comparable (verification.py doctrine).

Round-② additions (docs/135):

* ``closure_rules`` — the C-layer of the A/B/C doctrine: WHEN a conclusion is
  licensed (a bounded try-set exhausted; escalate-to-human is a conclusion).
  Same Clause-B lint as cases; a rule whose predicate needs an absolute scale
  is dropped, never taught. Part of ``manual_hash``.
* ``requires_profile`` — a case or closure rule may gate on a chip-profile
  field (``chip_profile.FIELDS``). Gates naming a field or case the registry
  does not know are dropped with a warning (a rule that can never resolve
  correctly must not half-apply). :func:`active_view` splits a pack by the
  chip's actual answers; an UNANSWERED gating field deactivates the rule and
  queues the question — the conservative branch is silence, not a default.
* ``signal_map`` values may be profile-branched: ``{"default": id,
  "by_profile": {field: {case: id}}}``. :func:`resolve_signal` resolves one
  signal against the answers; a plain-string entry behaves byte-identically
  to before.
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
from pathlib import Path
from typing import Any

from quam_state_manager.core.autofit import chip_profile

logger = logging.getLogger(__name__)

_ROOT = Path(__file__).resolve().parents[2] / "knowledge"

# Clause-B violations: absolute frequencies/powers, window-relative sizes.
# Relative dB steps ("a few dB below the knee") are legitimate geometry.
_LINT_PATTERNS = [
    re.compile(r"\d+(\.\d+)?\s*GHz", re.IGNORECASE),
    re.compile(r"\d+(\.\d+)?\s*MHz", re.IGNORECASE),
    re.compile(r"-?\d+(\.\d+)?\s*dBm", re.IGNORECASE),
    re.compile(r"%\s*of\s+the\s+(window|span|sweep)", re.IGNORECASE),
]


def _lint_violation(text: str) -> str | None:
    for pat in _LINT_PATTERNS:
        m = pat.search(text or "")
        if m:
            return m.group(0)
    return None


def _bad_profile_gate(gate: Any) -> str | None:
    """A ``requires_profile`` gate is {field: [case, ...]}; naming a field or
    case the registry does not know makes the rule unresolvable — say why."""
    if gate is None:
        return None
    if not isinstance(gate, dict) or not gate:
        return "requires_profile must be a non-empty dict"
    for fkey, cases in gate.items():
        f = chip_profile.FIELD_BY_KEY.get(fkey)
        if f is None:
            return f"unknown profile field {fkey!r}"
        if isinstance(cases, str):
            cases = [cases]
        if not isinstance(cases, list) or not cases:
            return f"{fkey}: cases must be a non-empty list"
        for c in cases:
            if c not in f.cases:
                return f"{fkey}: unknown case {c!r}"
    return None


def pack_path(family: str, version: str = "v1") -> Path:
    return _ROOT / version / family / "cases.json"


def load_family(family: str, *, version: str = "v1",
                overlay_dir: str | Path | None = None) -> dict[str, Any] | None:
    """Load a family's knowledge pack, linted, with optional lab overlay.

    Returns None when no pack exists for the family — knowledge is
    strictly optional; every consumer must behave identically without it.
    """
    p = pack_path(family, version)
    try:
        pack = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None

    kept, dropped = [], []
    for c in pack.get("cases") or []:
        bad = _lint_violation(c.get("geometry", "")) \
            or _lint_violation(c.get("prescription", "")) \
            or _bad_profile_gate(c.get("requires_profile"))
        if bad:
            dropped.append((c.get("id"), bad))
            continue
        kept.append(c)
    for cid, bad in dropped:
        logger.warning("knowledge %s/%s: case %s dropped by Clause-B lint "
                       "(%r) — an absolute-scale rule is never taught",
                       version, family, cid, bad)
    pack["cases"] = kept
    pack["lint_dropped"] = [cid for cid, _ in dropped]

    # closure rules — same lint, same fate; a predicate that needs an
    # absolute scale, or a gate on a field the registry does not know,
    # is dropped and logged, never half-applied
    cl_kept, cl_dropped = [], []
    for r in pack.get("closure_rules") or []:
        bad = _lint_violation(r.get("trigger", "")) \
            or _lint_violation(r.get("text", "")) \
            or _bad_profile_gate(r.get("requires_profile"))
        if bad:
            cl_dropped.append((r.get("id"), bad))
            continue
        cl_kept.append(r)
    for rid, bad in cl_dropped:
        logger.warning("knowledge %s/%s: closure rule %s dropped by lint "
                       "(%r)", version, family, rid, bad)
    pack["closure_rules"] = cl_kept
    pack["closure_lint_dropped"] = [rid for rid, _ in cl_dropped]

    if overlay_dir is not None:
        opath = Path(overlay_dir) / family / "cases.json"
        try:
            overlay = json.loads(opath.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            overlay = None
        if isinstance(overlay, dict):
            have = {c.get("id") for c in kept}
            added, refused = [], []
            for c in overlay.get("cases") or []:
                cid = c.get("id")
                bad = _lint_violation(c.get("geometry", "")) \
                    or _lint_violation(c.get("prescription", "")) \
                    or _bad_profile_gate(c.get("requires_profile"))
                if cid in have:
                    refused.append(cid)
                elif bad:
                    refused.append(cid)
                    logger.warning("knowledge overlay %s: case %s refused "
                                   "by Clause-B lint (%r)", family, cid, bad)
                else:
                    added.append(dict(c, overlay=True))
            if refused:
                logger.warning("knowledge overlay %s: refused ids %s "
                               "(duplicate of shipped, or lint) — overlays "
                               "are additive only", family, refused)
            pack["cases"] = kept + added
            pack["overlay_added"] = [c["id"] for c in added]
            pack["overlay_refused"] = refused

    # content hash of what was ACTUALLY loaded — part of the verdict context
    basis = json.dumps({"cases": pack["cases"],
                        "rules": pack.get("rules"),
                        "closure_rules": pack.get("closure_rules"),
                        "provisional_registry": pack.get("provisional_registry")},
                       sort_keys=True).encode("utf-8")
    pack["manual_hash"] = hashlib.sha1(basis).hexdigest()[:16]
    return pack


def case_by_id(pack: dict[str, Any], case_id: str) -> dict[str, Any] | None:
    for c in pack.get("cases") or []:
        if c.get("id") == case_id:
            return c
    return None


# --- profile resolution (docs/135) -----------------------------------------

def _gate_state(gate: Any, answers: dict[str, str]) -> tuple[str, str | None]:
    """('active'|'inactive'|'pending', pending_field). ``pending`` means a
    gating field is unanswered — the rule must NOT apply and the question
    must surface (the conservative branch is silence, not a default)."""
    if not gate:
        return "active", None
    for fkey, cases in gate.items():
        if isinstance(cases, str):
            cases = [cases]
        got = answers.get(fkey)
        if got is None:
            return "pending", fkey
        if got not in cases:
            return "inactive", None
    return "active", None


def active_view(pack: dict[str, Any],
                answers: dict[str, str] | None) -> dict[str, Any]:
    """Split a loaded pack by the chip's profile answers.

    Returns {"cases", "closure_rules"} (active only), plus
    "inactive_case_ids"/"inactive_closure_ids" (gated off by an answer) and
    "pending_questions" (profile fields whose absence deactivated something —
    what the GUI should ask before those rules can speak)."""
    answers = answers or {}
    out: dict[str, Any] = {"cases": [], "closure_rules": [],
                           "inactive_case_ids": [], "inactive_closure_ids": [],
                           "pending_questions": []}
    for kind, key_all, key_off in (("cases", "cases", "inactive_case_ids"),
                                   ("closure_rules", "closure_rules",
                                    "inactive_closure_ids")):
        for item in pack.get(key_all) or []:
            state, pend = _gate_state(item.get("requires_profile"), answers)
            if state == "active":
                out[kind].append(item)
            elif state == "inactive":
                out[key_off].append(item.get("id"))
            else:
                out[key_off].append(item.get("id"))
                if pend and pend not in out["pending_questions"]:
                    out["pending_questions"].append(pend)
    return out


def resolve_signal(pack: dict[str, Any], signal: str,
                   answers: dict[str, str] | None
                   ) -> tuple[str | None, str | None]:
    """(case_id, pending_field) for one semantic signal.

    A plain-string ``signal_map`` entry resolves byte-identically to before.
    A branched entry picks the declared case's id; with the gating field
    unanswered it falls back to ``default`` and names the field so the
    caller can surface the question alongside the conservative reading."""
    entry = (pack.get("signal_map") or {}).get(signal)
    if entry is None or isinstance(entry, str):
        return entry, None
    if not isinstance(entry, dict):
        return None, None
    answers = answers or {}
    by = entry.get("by_profile")
    if isinstance(by, dict):
        for fkey, cmap in by.items():
            if not isinstance(cmap, dict):
                continue
            got = answers.get(fkey)
            if got is None:
                return entry.get("default"), fkey
            if got in cmap:
                return cmap[got], None
    return entry.get("default"), None
