"""The tags and notes a person typed, as typeahead vocabulary.

Customer, on-site (2026-09-11):

    "우리 검색어 창에 지금 타이핑을 하면 저절로 뜨는데... 이거! 제발 data tag랑
     note에 사용자가 기재한 단어들도 넣어달라고 함!!!! 다만, 검색 pop up할때
     뜨는건 run 번호: tag 이름 (혹은 note) 이렇게 뜨도록. note는 내용이 다
     담기게 하는게 아니고 그냥 note (검색어 ...) 그냥 이렇게 compact하게."

The grammar could already find them — ``tag:flagged`` and ``note:todo`` have
been in the search help for a long time. What was missing is that **you have to
already know the word**. Every other vocabulary the box completes from is
machine-generated (node names, parameter keys); the tags and notes are the only
words in the archive a *person* chose, and they were the ones you could not be
reminded of.

Read straight from ``quashboard_tags.json``, never through ``DatasetStore``
-------------------------------------------------------------------------
That file IS the source of truth (``DatasetStore._load_tags`` reads exactly it),
it is small, and reading it costs one ``open`` per data folder. Going through a
store instead would mean a typeahead keystroke could trigger the cold run scan
docs/170 spent a whole round bounding — a 31.6 s first click at the customer's
share latency. A vocabulary must never be able to do that.

The shape on disk, per data folder::

    {"bookmarks": [...], "tags": {"<run id>": ["a", "b"]}, "notes": {"<run id>": "text"}}

Per-key tolerance, for the same reason ``_load_tags`` has it: a hand-edited or
partially corrupt file must cost that entry, never the whole vocabulary.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any, Iterable

# A note is prose. Only its WORDS are vocabulary — the customer was explicit
# that the popup must not carry the note's content ("note는 내용이 다 담기게
# 하는게 아니고"), and a whole note in a suggestion row would be unreadable
# anyway.
_WORD = re.compile(r"[^\W_]{2,}", re.UNICODE)

# Bounds. Every one of these is a "say it out loud" cap, not a silent truncation:
# `build` reports what it dropped and the route ships the count.
MAX_TAGS = 400           # distinct tag names offered
MAX_RUNS_PER_TAG = 200   # run ids remembered per tag (newest first)
MAX_NOTES = 2000         # runs whose note contributes words
MAX_WORDS_PER_NOTE = 40  # distinct words taken from one note

# Words that would match almost everything and teach nothing. Deliberately
# tiny: this is not a stop-word list, it is the three or four fillers that
# showed up as noise. Anything domain-specific stays in.
_SKIP = {"the", "and", "for", "with", "this", "that", "was", "not", "but",
         "이것", "그리고"}


def note_words(text: Any, limit: int = MAX_WORDS_PER_NOTE) -> list[str]:
    """The distinct, lower-cased words of one note, in first-seen order.

    Unicode-aware on purpose: a Korean note is exactly as searchable as an
    English one, and ``\\w`` with ``re.UNICODE`` keeps Hangul. Two characters
    minimum — a one-letter token matches half the archive.
    """
    out: list[str] = []
    seen: set[str] = set()
    for m in _WORD.finditer(str(text or "")):
        w = m.group(0).lower()
        if w in seen or w in _SKIP:
            continue
        seen.add(w)
        out.append(w)
        if len(out) >= limit:
            break
    return out


def _read_one(path: Path) -> dict:
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError, UnicodeDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def build(folders: Iterable[Path]) -> dict:
    """``{"tags": [...], "notes": [...], "dropped": int}`` over every folder.

    Tags are merged across folders by NAME (one archive, one vocabulary); their
    run ids are merged and kept newest-first, because a run id counts upward and
    the newest run is the one a person is usually thinking about.
    """
    tag_runs: dict[str, set[int]] = {}
    notes: dict[int, list[str]] = {}
    dropped = 0

    for folder in folders:
        data = _read_one(Path(folder) / "quashboard_tags.json")
        if not data:
            continue
        for rid_str, names in (data.get("tags") or {}).items():
            try:
                rid = int(rid_str)
            except (TypeError, ValueError):
                dropped += 1
                continue
            if not isinstance(names, (list, tuple)):
                dropped += 1
                continue
            for name in names:
                if not isinstance(name, str) or not name.strip():
                    continue
                tag_runs.setdefault(name.strip(), set()).add(rid)
        for rid_str, text in (data.get("notes") or {}).items():
            try:
                rid = int(rid_str)
            except (TypeError, ValueError):
                dropped += 1
                continue
            words = note_words(text)
            if not words:
                continue
            # One run, one note: a later folder's note for the same id merges
            # rather than replacing, so nothing a person wrote disappears.
            prev = notes.get(rid)
            if prev is None:
                notes[rid] = words
            else:
                for w in words:
                    if w not in prev and len(prev) < MAX_WORDS_PER_NOTE:
                        prev.append(w)

    tags = []
    for name in sorted(tag_runs, key=lambda s: (-len(tag_runs[s]), s.lower())):
        if len(tags) >= MAX_TAGS:
            dropped += 1
            continue
        runs = sorted(tag_runs[name], reverse=True)
        tags.append({"t": name, "n": len(runs), "r": runs[:MAX_RUNS_PER_TAG]})

    note_rows = []
    for rid in sorted(notes, reverse=True):
        if len(note_rows) >= MAX_NOTES:
            dropped += 1
            continue
        note_rows.append({"r": rid, "w": notes[rid]})

    return {"tags": tags, "notes": note_rows, "dropped": dropped}


def version(folders: Iterable[Path]) -> str:
    """A cheap identity for the vocabulary: the tag files' size+mtime.

    Two stats per data folder. The workspace version cannot serve here — a tag
    is typed without the archive changing at all, and a note edited between two
    polls must reach the box.
    """
    parts: list[str] = []
    for folder in sorted(str(f) for f in folders):
        p = Path(folder) / "quashboard_tags.json"
        try:
            st = os.stat(p)
            parts.append("%s:%d:%d" % (p.name, st.st_size, st.st_mtime_ns))
        except OSError:
            parts.append("-")
    return "|".join(parts) or "0"


def to_payload(built: dict) -> dict:
    """The wire shape. Kept flat and short — this rides every page that owns a
    search box, and the client filters it in the browser."""
    return {"tags": built.get("tags") or [],
            "notes": built.get("notes") or [],
            "dropped": int(built.get("dropped") or 0)}
