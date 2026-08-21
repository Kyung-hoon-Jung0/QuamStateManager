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
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
from pathlib import Path
from typing import Any

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
            or _lint_violation(c.get("prescription", ""))
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
                    or _lint_violation(c.get("prescription", ""))
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
                        "provisional_registry": pack.get("provisional_registry")},
                       sort_keys=True).encode("utf-8")
    pack["manual_hash"] = hashlib.sha1(basis).hexdigest()[:16]
    return pack


def case_by_id(pack: dict[str, Any], case_id: str) -> dict[str, Any] | None:
    for c in pack.get("cases") or []:
        if c.get("id") == case_id:
            return c
    return None
