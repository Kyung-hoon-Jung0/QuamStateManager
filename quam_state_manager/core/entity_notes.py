"""Operator notes on a qubit, a pair, or any single parameter (docs/167).

"q12's flux line contact is suspect — do not trust these values" had nowhere to
live. ``DatasetStore.set_note`` exists but only for a RUN; nothing could carry a
sentence about an entity or a leaf.

WHERE IT LIVES, and why that is not state.json
----------------------------------------------
A sidecar under ``instance/annotations/``, a byte-for-byte analogue of
``type_policy.assignments_path``. Three reasons, all structural rather than
preferential:

* It writes zero bytes of the customer's ``state.json``, so it cannot race an
  experiment's ``os.replace`` (the whole reason ``core/safe_io.py`` and the
  working copy exist, docs/28) and it needs no Apply.
* A LEAF note (``qubits.q12.T1``) has no home in ``extras`` without a dotted
  key, and a dotted key is ambiguous under SM's own dot-path grammar. So leaf
  notes are sidecar-only by construction.
* Most notes are operational — this fridge, this cooldown — and do not belong
  in a file that describes the chip. The ones that ARE chip properties ("q5 has
  a TLS at 4.33 GHz") can be promoted to ``<entity>.extras.note`` through the
  ordinary edit door, where they cost a live write and get reviewed like any
  other change.

WHICH CHIP KEY
--------------
``working_copy.key_for(live_folder)`` — the folder-shaped key every other
user-preference sidecar in SM already uses — and NOT
``HistoryManager.resolve_chip_dir``. The ladder is designed to re-key and heal:
it adopts a directory by ``extras.chip_name`` and can return a conflict or a
not-yet-existing dir. Those are the right semantics for a snapshot store and
the wrong ones for a note, whose text must be exactly as stable as the folder
the user is looking at.

Consequences, stated rather than discovered later:
  * Setting or changing ``extras.chip_name`` does NOT move the key (``key_for``
    never reads state.json), so notes survive a rename.
  * Renaming the FOLDER, or opening a second copy of the same chip, is a new
    key and therefore no notes. Nothing is deleted; the old sidecar stays. Each
    sidecar records its ``live_folder`` and the chip token at last write so an
    explicit "adopt these" offer can be built on top later — recording costs
    nothing and not recording would make it impossible for notes written today.

WHAT THIS MODULE WILL NOT DO
----------------------------
It never deletes, rewrites or hides a note because its subject vanished. A
regenerated chip or a renamed pulse leaves ORPHANS, and an orphan is reported
with its last-known subject so a person can re-address or delete it. With no
readable chip there is no orphan verdict at all — the same rule
``physical_units`` follows: annotate only when the input fully resolves, and
otherwise say nothing rather than declare everything broken.
"""

from __future__ import annotations

import json
import logging
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from quam_state_manager.core import safe_io

logger = logging.getLogger(__name__)

_NOTES_DIRNAME = "annotations"
# In-process only, and that is stated rather than implied: two SM windows are
# two processes (core/instances.py says so in its own prose), so this lock does
# not span them. The two things that DO close the realistic cases are below —
# a per-subject merge under a fresh re-read, and a per-note `rev` the caller
# compares against.
_notes_lock = threading.Lock()

MAX_TEXT = 2000


def notes_path(instance_path, live_folder) -> Path:
    from quam_state_manager.core import working_copy
    return (Path(instance_path) / _NOTES_DIRNAME
            / f"{working_copy.key_for(live_folder)}.json")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _read(path: Path) -> dict:
    """The whole sidecar, or an empty one. Never raises."""
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        logger.warning("corrupt notes sidecar %s — ignoring", path)
        return {}
    return data if isinstance(data, dict) else {}


def load(instance_path, live_folder) -> dict[str, dict]:
    """``{subject -> record}``. A missing or corrupt sidecar is an empty map."""
    notes = _read(notes_path(instance_path, live_folder)).get("notes")
    if not isinstance(notes, dict):
        return {}
    return {k: v for k, v in notes.items() if isinstance(v, dict) and v.get("text")}


def entity_of(subject: str) -> str:
    """The entity a subject belongs to — its first TWO dot-path segments.

    ``"qubits.q12.T1" -> "qubits.q12"``, ``"qubits.q12" -> "qubits.q12"``. A
    leaf note therefore also lights its entity, which is what makes one marker
    on a grid row mean "there is something to read about this qubit".

    Returns a DOT-PATH, not a row id — the grids key their rows on the bare id
    (``q12``), so use :func:`row_marks` for those.
    """
    parts = [p for p in str(subject or "").split(".") if p]
    return ".".join(parts[:2])


def row_marks(notes: dict[str, dict]) -> tuple[dict[str, str], dict[str, str]]:
    """``(qubit_marks, pair_marks)`` keyed the way each grid's rows are keyed.

    Two maps rather than one because a qubit and a pair could share an id
    string, and the two tables would then mark each other's rows. Each value is
    the text of one note plus a count when the entity carries several, which is
    what a row head's ``title`` can usefully show.
    """
    by_entity: dict[str, list[str]] = {}
    for subject, rec in notes.items():
        ent = entity_of(subject)
        if not ent:
            continue
        by_entity.setdefault(ent, []).append(str(rec.get("text") or ""))
    qubits: dict[str, str] = {}
    pairs: dict[str, str] = {}
    for ent, texts in by_entity.items():
        head, _, ident = ent.partition(".")
        if not ident:
            continue
        label = texts[0]
        if len(texts) > 1:
            label += f"  (+{len(texts) - 1} more)"
        if head == "qubits":
            qubits[ident] = label
        elif head == "qubit_pairs":
            pairs[ident] = label
    return qubits, pairs


def classify(merged: dict | None, notes: dict[str, dict]) -> dict[str, dict]:
    """Stamp each record ``orphan: bool`` against a chip's merged tree.

    With no readable chip (``merged`` is None) NOTHING is stamped — a panel
    that declared every note orphaned because no chip was open would be worse
    than one that says nothing about orphan-ness at all.

    A pointer subject counts as PRESENT when the alias key itself is there: SM
    stores pointer strings as real leaves, so a note on
    ``qubits.q1.xy.operations.x180`` survives whether or not that pointer
    resolves. The note is on the address the user typed, and following the
    pointer would silently re-address it.
    """
    out: dict[str, dict] = {}
    for subject, rec in notes.items():
        item = dict(rec)
        item["subject"] = subject
        item["entity"] = entity_of(subject)
        if isinstance(merged, dict):
            item["orphan"] = not _present(merged, subject)
        out[subject] = item
    return out


def _present(merged: dict, subject: str) -> bool:
    cur: Any = merged
    for seg in str(subject or "").split("."):
        if not seg:
            return False
        if isinstance(cur, dict):
            if seg not in cur:
                return False
            cur = cur[seg]
        elif isinstance(cur, list):
            if not seg.isdigit() or int(seg) >= len(cur):
                return False
            cur = cur[int(seg)]
        else:
            return False
    return True


class NoteConflict(Exception):
    """The stored note moved under the caller (compare-and-swap refusal)."""

    def __init__(self, stored: dict):
        super().__init__("note changed since it was read")
        self.stored = stored


def hand_tuned(notes: dict[str, dict]) -> list[str]:
    """The subjects somebody marked as hand-tuned, sorted.

    This is the ADVISORY half of "value locking", and advisory is the whole
    design. A real lock would have to be honoured by thirteen write paths, and
    the actor that actually overwrites a hand-tuned flux point is the lab's own
    calibration node — an external process SM spawns but does not mediate. A
    padlock that process walks straight through is WORSE than no padlock,
    because people stop checking. A mark that makes the existing confirmations
    say "3 of these are hand-tuned" is honest about exactly what it is: a note
    to the person pressing the button.
    """
    return sorted(k for k, v in notes.items()
                  if isinstance(v, dict) and v.get("hand_tuned"))


def touches(subjects, changed_paths) -> list[str]:
    """Which marked subjects a set of changing dot-paths would disturb.

    A subject matches a path when either contains the other: a mark on
    ``qubits.q12`` is disturbed by a write to ``qubits.q12.T1``, and a mark on
    ``qubits.q12.T1`` is disturbed by a write that replaces ``qubits.q12``.
    Deliberately generous — this decides what a CONFIRMATION mentions, and the
    cost of naming one path too many is a sentence, while the cost of missing
    one is the whole point of the mark.
    """
    hits: set[str] = set()
    for path in changed_paths or ():
        p = str(path or "")
        for subject in subjects:
            if p == subject or p.startswith(subject + ".") or subject.startswith(p + "."):
                hits.add(subject)
    return sorted(hits)


def save(instance_path, live_folder, subject: str, text: str, *,
         author: str = "", expect_rev: int | None = None,
         chip_token: str = "", hand_tuned_flag: bool = False) -> dict:
    """Write one note. Returns the stored record.

    Two disciplines, and neither is claimed to be more than it is:

    * The load-modify-write re-reads the file INSIDE the lock and applies only
      this subject, so two windows noting DIFFERENT qubits — overwhelmingly the
      likely collision — both survive. The write itself is
      :func:`safe_io.atomic_write_json`, so a reader sees old bytes or new
      bytes and never a torn file.
    * ``expect_rev`` is a compare-and-swap token. Pass the ``rev`` you rendered
      and a note somebody else changed meanwhile raises :class:`NoteConflict`
      carrying THEIR text, so the caller can show it rather than overwrite it.
      Pass ``None`` to write regardless — deliberately a separate decision from
      passing a rev, the two-token discipline docs/120 established.

    The residual, stated plainly: two windows writing the SAME subject inside
    one read-to-replace window can still lose a write. Closing that properly
    means one file per note, which is a bigger change than a handful of
    human-rate notes justifies today.
    """
    subject = str(subject or "").strip()
    if not subject:
        raise ValueError("a note needs a subject")
    text = str(text or "").strip()
    if not text:
        raise ValueError("a note needs text (use delete to remove one)")
    if len(text) > MAX_TEXT:
        raise ValueError(f"a note is at most {MAX_TEXT} characters")

    path = notes_path(instance_path, live_folder)
    with _notes_lock:
        data = _read(path)
        notes = data.get("notes") if isinstance(data.get("notes"), dict) else {}
        prev = notes.get(subject) if isinstance(notes.get(subject), dict) else None
        if expect_rev is not None:
            stored_rev = int((prev or {}).get("rev") or 0)
            if stored_rev != int(expect_rev):
                raise NoteConflict(dict(prev or {}, subject=subject))
        now = _now()
        record = {
            "text": text,
            "created_at": (prev or {}).get("created_at") or now,
            "updated_at": now,
            "author": str(author or "").strip(),
            # docs/167: the advisory half of "value locking" -- see hand_tuned().
            "hand_tuned": bool(hand_tuned_flag),
            "rev": int((prev or {}).get("rev") or 0) + 1,
        }
        notes[subject] = record
        path.parent.mkdir(parents=True, exist_ok=True)
        safe_io.atomic_write_json(path, {
            "version": 1,
            "live_folder": str(live_folder),
            # Recorded for a future "adopt the notes written for <old folder>"
            # offer. Recording costs nothing; NOT recording makes the offer
            # impossible for every note written before it exists.
            "chip_token": str(chip_token or data.get("chip_token") or ""),
            "notes": notes,
        })
    return dict(record, subject=subject)


def delete(instance_path, live_folder, subject: str) -> bool:
    path = notes_path(instance_path, live_folder)
    with _notes_lock:
        data = _read(path)
        notes = data.get("notes") if isinstance(data.get("notes"), dict) else {}
        if subject not in notes:
            return False
        notes.pop(subject)
        path.parent.mkdir(parents=True, exist_ok=True)
        safe_io.atomic_write_json(path, {
            "version": 1,
            "live_folder": str(live_folder),
            "chip_token": str(data.get("chip_token") or ""),
            "notes": notes,
        })
    return True


def readdress(instance_path, live_folder, subject: str, new_subject: str) -> dict:
    """Move a note to a new subject, keeping its text and its history.

    This is how an ORPHAN is repaired: the chip was regenerated, the pulse was
    renamed, the observation still stands. Deliberately an explicit user act —
    nothing here guesses where a vanished subject went.
    """
    new_subject = str(new_subject or "").strip()
    if not new_subject:
        raise ValueError("a note needs a subject")
    path = notes_path(instance_path, live_folder)
    with _notes_lock:
        data = _read(path)
        notes = data.get("notes") if isinstance(data.get("notes"), dict) else {}
        rec = notes.get(subject)
        if not isinstance(rec, dict):
            raise KeyError(subject)
        if new_subject != subject and new_subject in notes:
            raise ValueError(f"{new_subject} already has a note")
        moved = dict(rec)
        moved["updated_at"] = _now()
        moved["rev"] = int(rec.get("rev") or 0) + 1
        notes.pop(subject, None)
        notes[new_subject] = moved
        path.parent.mkdir(parents=True, exist_ok=True)
        safe_io.atomic_write_json(path, {
            "version": 1,
            "live_folder": str(live_folder),
            "chip_token": str(data.get("chip_token") or ""),
            "notes": notes,
        })
    return dict(moved, subject=new_subject)
