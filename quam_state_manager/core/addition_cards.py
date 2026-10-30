"""Knowledge cards for Generate-Config additions (versioned data, not code).

A card states the convention for one role / base class: where an addition of
that kind goes (``placement``), which pointer field plays on which qubit or
channel (``pointers``), how it is named (``naming``), and what is forbidden.
The cards ship as ``knowledge/additions/v1/cards.json``; the same loader
pattern as ``core/autofit/knowledge.py``: a version, a lint at load (a card
that breaks the lint is DROPPED and logged, never used), and a content hash.

Lab overlays (``<instance>/additions_cards_overlay.json``) are ADDITIVE only:
a new card id extends the set (tried before the shipped cards, so a lab class
can have a more specific card), an id that already exists is refused -- a
local file never silently replaces a shipped convention.

A class takes the FIRST card whose ``match`` it meets: ``fields_all`` (every
named field is declared), ``mro_any`` (the class or an ancestor has that short
name). A lab subclass therefore inherits its base's card through its MRO.
A class no card matches has "no card": the wizard offers only its field form
and the environment check.
"""
from __future__ import annotations

import copy
import hashlib
import json
import logging
import re
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_ROOT = Path(__file__).resolve().parents[1] / "knowledge" / "additions"
OVERLAY_NAME = "additions_cards_overlay.json"

ROLES = ("macro_pair", "macro_qubit", "pulse", "preset", "rule")
ON_VALUES = ("moving", "control", "target", "high", "low", "coupler")
_ID_RE = re.compile(r"^[a-z][a-z0-9_]*$")
_PLACEHOLDERS = ("<qubit>", "<pair>", "<twpa>", "<any>")
_MATCH_KEYS = ("fields_all", "mro_any", "preset", "rule")


def _lint_card(c: Any) -> str | None:
    """Why a card is unusable, or None."""
    if not isinstance(c, dict):
        return "a card must be an object"
    cid = c.get("id")
    if not isinstance(cid, str) or not _ID_RE.match(cid):
        return f"id must be lower_snake_case (got {cid!r})"
    if c.get("role") not in ROLES:
        return f"role must be one of {', '.join(ROLES)}"
    m = c.get("match")
    if not isinstance(m, dict) or not m or any(k not in _MATCH_KEYS for k in m):
        return f"match must use only {', '.join(_MATCH_KEYS)}"
    for k in ("fields_all", "mro_any"):
        if k in m and (not isinstance(m[k], list) or not m[k]
                       or not all(isinstance(x, str) and x for x in m[k])):
            return f"match.{k} must be a non-empty list of names"
    for key in ("pointers", "optional_pointers"):
        p = c.get(key, {})
        if not isinstance(p, dict):
            return f"{key} must be an object"
        for f, on in p.items():
            if on not in ON_VALUES:
                return f"{key}.{f}: {on!r} is not one of {', '.join(ON_VALUES)}"
    pl = (c.get("placement") or {}).get("target")
    if pl is not None:
        if not isinstance(pl, str) or not pl:
            return "placement.target must be a dot path"
        bad = [t for t in re.findall(r"<[^>]*>", pl) if t not in _PLACEHOLDERS]
        if bad:
            return f"placement.target uses unknown placeholder {bad[0]}"
    n = c.get("naming", {})
    if not isinstance(n, dict):
        return "naming must be an object"
    for k in ("strip",):
        if k in n:
            try:
                re.compile(n[k])
            except (re.error, TypeError) as exc:
                return f"naming.{k} is not a pattern: {exc}"
    for k in ("prefix", "exact", "fallback"):
        if k in n and not (isinstance(n[k], str) and re.match(r"^[A-Za-z0-9_\-]*$", n[k])):
            return f"naming.{k} must be letters, digits, '_' or '-'"
    fb = c.get("forbidden", [])
    if not isinstance(fb, list) or not all(isinstance(x, dict) and isinstance(x.get("reason"), str)
                                           for x in fb):
        return "forbidden must be a list of {rule, reason}"
    return None


def _read(path: Path) -> dict | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def overlay_path(instance_path) -> Path:
    return Path(instance_path) / OVERLAY_NAME


# An overlay is lab data a person approved, possibly drafted by an agent: its
# ``naming.strip`` may use only a literal alternation with ``?`` (no ``*``,
# ``+`` or ``{}``, so matching is linear) and its text fields are capped.
_OVERLAY_STRIP_RE = re.compile(r"^[A-Za-z0-9_^$|?()]{0,40}$")
_OVERLAY_TEXT_MAX = 500


def _overlay_reason(c: dict) -> str | None:
    """What an OVERLAY card may not carry that a shipped card may."""
    strip = (c.get("naming") or {}).get("strip")
    if strip is not None and not (isinstance(strip, str) and _OVERLAY_STRIP_RE.match(strip)):
        return ("naming.strip in an overlay must be at most 40 characters of letters, digits, "
                "'_', '^', '$', '|', '?' and parentheses")
    for k in ("title", "notes"):
        if k in c and not (isinstance(c[k], str) and len(c[k]) <= _OVERLAY_TEXT_MAX):
            return f"{k} must be text of at most {_OVERLAY_TEXT_MAX} characters"
    return None


def load_cards(*, version: str = "v1", instance_path=None) -> dict:
    """``{"version", "hash", "cards": [...], "dropped": [(id, why)],
    "refused_overlay": [(id, why)], "calibration_field_patterns": [...]}``."""
    pack = _read(_ROOT / version / "cards.json") or {}
    kept, dropped = [], []
    for c in pack.get("cards") or []:
        why = _lint_card(c)
        if why:
            dropped.append(((c or {}).get("id") if isinstance(c, dict) else None, why))
            continue
        kept.append(c)
    refused = []
    lab = []
    if instance_path is not None:
        ov = _read(overlay_path(instance_path))
        ids = {c["id"] for c in kept}
        for c in (ov or {}).get("cards") or []:
            why = _lint_card(c) or _overlay_reason(c)
            if why is None and c["id"] in ids:
                why = "an overlay may only ADD a card; this id is a shipped card"
            if why:
                refused.append(((c or {}).get("id") if isinstance(c, dict) else None, why))
                continue
            ids.add(c["id"])
            lab.append(dict(c, origin="overlay"))
    for cid, why in dropped:
        logger.warning("additions card %s dropped: %s", cid, why)
    for cid, why in refused:
        logger.warning("additions overlay card %s refused: %s", cid, why)
    # shipped cards first: an overlay covers a class no shipped card covers and
    # can never take a shipped card's class over
    cards = [dict(c, origin="shipped") for c in kept] + lab
    pats = [p for p in pack.get("calibration_field_patterns") or [] if isinstance(p, str)]
    canon = json.dumps({"v": pack.get("version"), "cards": cards, "pats": pats}, sort_keys=True)
    return {"version": pack.get("version"), "hash": hashlib.sha256(canon.encode()).hexdigest()[:16],
            "cards": cards, "dropped": dropped, "refused_overlay": refused,
            "calibration_field_patterns": pats}


_CACHE: dict = {}


def cards(instance_path=None) -> dict:
    """Memoized :func:`load_cards` (keyed on the overlay file's mtime)."""
    key = None
    if instance_path is not None:
        p = overlay_path(instance_path)
        try:
            key = (str(p), p.stat().st_mtime_ns)
        except OSError:
            key = (str(p), None)
    if key not in _CACHE:
        _CACHE.clear()
        _CACHE[key] = load_cards(instance_path=instance_path)
    return _CACHE[key]


def _short(path: Any) -> str:
    return str(path).rsplit(".", 1)[-1]


def card_for(entry: dict | None, *, role: str | None = None, deck: dict | None = None) -> dict | None:
    """The first card a manifest class *entry* meets, or None (no card)."""
    if not isinstance(entry, dict):
        return None
    role = role or entry.get("role")
    deck = deck or cards()
    fields = entry.get("fields") or {}
    names = {_short(m) for m in (entry.get("mro") or [])}
    if entry.get("path"):
        names.add(_short(entry["path"]))
    for c in deck["cards"]:
        if c.get("role") != role:
            continue
        m = c.get("match") or {}
        if "fields_all" in m and not all(f in fields for f in m["fields_all"]):
            continue
        if "mro_any" in m and not (names & set(m["mro_any"])):
            continue
        if not ({"fields_all", "mro_any"} & set(m)):
            continue
        return c
    return None


def card_by_id(cid: str, deck: dict | None = None) -> dict | None:
    deck = deck or cards()
    return next((c for c in deck["cards"] if c.get("id") == cid), None)


def pointer_map(card: dict | None, fields) -> dict[str, str]:
    """``{field: on}`` for the pointer fields *card* names that *fields* has."""
    if not card:
        return {}
    out = {f: on for f, on in (card.get("pointers") or {}).items() if f in fields}
    out.update({f: on for f, on in (card.get("optional_pointers") or {}).items() if f in fields})
    return out


def all_pointer_fields(deck: dict | None = None) -> tuple[str, ...]:
    deck = deck or cards()
    out: list[str] = []
    for c in deck["cards"]:
        for key in ("pointers", "optional_pointers"):
            for f in c.get(key) or {}:
                if f not in out:
                    out.append(f)
    return tuple(out)


def default_name(card: dict | None, class_path: str) -> str | None:
    """The card's name for a new addition of *class_path*, or None."""
    if not card:
        return None
    n = card.get("naming") or {}
    if n.get("exact"):
        return n["exact"]
    stem = re.sub(r"[^A-Za-z0-9]+", "_", _short(class_path)).lower()
    if n.get("strip"):
        stem = re.sub(n["strip"], "", stem)
    stem = stem or n.get("fallback") or ""
    prefix = n.get("prefix") or ""
    return (prefix + stem) if stem else (prefix.rstrip("_") or "x")


def placement_ok(card: dict | None, target: str) -> bool:
    """Does *target* fit the card's ``placement.target`` pattern?"""
    pl = ((card or {}).get("placement") or {}).get("target")
    if not pl:
        return True
    rx = re.escape(pl)
    for ph, sub in (("<qubit>", r"[\w\-]+"), ("<pair>", r"[\w\-]+"), ("<twpa>", r"[\w\-]+"),
                    ("<any>", r"[\w\-]+(\.[\w\-]+)*")):
        rx = rx.replace(re.escape(ph), sub)
    return re.fullmatch(rx, target) is not None


def forbidden_reason(card: dict | None, rule: str) -> str | None:
    for f in (card or {}).get("forbidden") or []:
        if f.get("rule") == rule:
            return f["reason"]
    return None


def calibration_field(name: str, card: dict | None = None, deck: dict | None = None) -> bool:
    """Is *name* a calibration value (amplitude, length, frequency, phase,
    weights, ...)? The patterns are part of the versioned cards."""
    deck = deck or cards()
    if name in ((card or {}).get("calibration_fields") or []):
        return True
    return any(re.search(p, name) for p in deck.get("calibration_field_patterns") or [])


def public_card(card: dict | None) -> dict | None:
    """The card as the wizard / an agent sees it."""
    if not card:
        return None
    keep = ("id", "title", "role", "placement", "pointers", "optional_pointers", "naming",
            "forbidden", "notes", "origin", "group_form", "fields")
    return {k: copy.deepcopy(card[k]) for k in keep if k in card}


# ---------------------------------------------------------------------------
# overlay drafts (an agent may propose one; a person approves it)
# ---------------------------------------------------------------------------

def overlay_card_reason(card: Any, instance_path=None) -> str | None:
    """Why *card* cannot be added to the lab overlay, or None."""
    why = _lint_card(card) or _overlay_reason(card)
    if why:
        return why
    deck = load_cards(instance_path=instance_path)
    if any(c["id"] == card["id"] for c in deck["cards"]):
        return f"a card named {card['id']} exists; an overlay may only add new cards"
    return None


def add_overlay_card(card: dict, instance_path) -> str | None:
    """Append *card* to the overlay file (after :func:`overlay_card_reason`).
    Returns a refusal reason, or None when written."""
    why = overlay_card_reason(card, instance_path)
    if why:
        return why
    p = overlay_path(instance_path)
    cur = _read(p) or {}
    if p.exists() and not isinstance(cur.get("cards"), list):
        return "the overlay file is unreadable; it is never rewritten"
    body = {"cards": list(cur.get("cards") or []) + [
        {k: v for k, v in card.items() if k != "origin"}]}
    from quam_state_manager.core import safe_io
    p.parent.mkdir(parents=True, exist_ok=True)
    safe_io.atomic_write_json(p, body)
    _CACHE.clear()
    return None
