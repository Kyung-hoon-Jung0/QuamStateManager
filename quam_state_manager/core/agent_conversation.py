"""The Agent panel's conversation, and the ones put away (clear / archive).

A clear never edits the agent event log: ``<instance>/agent_events/<day>.jsonl``
stays the record of what the agent did and said. A clear moves where the
panel's conversation STARTS -- ``since_n``, the chat counter's high-water mark
at the moment of the clear -- and, when the person keeps it, writes a copy of
the cleared conversation's chat events beside the index:

    <instance>/agent_conversations/<chip>/index.json
    <instance>/agent_conversations/<chip>/<archive id>.jsonl

``<chip>`` is the chip's NAME, the identity the panel's feed itself filters
chat events by: what one panel shows is what its clear puts away.

The index is ``{"since_n": int, "cleared_at": float | None,
"closed_sessions": {session id: upto n | None}, "archives": [meta, ...]}``
(archives oldest first). ``closed_sessions`` names the driving sessions a
clear ended. Their trailing events (the process's own Stop/Result, written
after the clear) belong to the cleared conversation: hidden. ``None`` hides
every later event of that session; once a new session starts, the entry is
capped at the counter of that moment (``reopen``), so a CLI that hands out the
same id again is shown from there on. A closed id is never resumed (``closed``).

An index that exists but cannot be read is never rewritten from nothing: a
clear refuses (``Unreadable``) instead of erasing the list of archives. Only a
missing file is "never cleared".
"""

from __future__ import annotations

import json
import os
import re
import threading
import time
import uuid
from pathlib import Path

from quam_state_manager.core import safe_io
from quam_state_manager.core import journal as journal_mod

_ID = re.compile(r"^[0-9a-f]{12}$")
_CLOSED_KEEP = 20
TITLE_CHARS = 120


class Unreadable(RuntimeError):
    """The index exists but cannot be read or parsed."""


class Moved(RuntimeError):
    """The conversation was cleared since the caller looked (another window)."""


def _dir(instance_path, chip: str) -> Path:
    return Path(instance_path) / "agent_conversations" / journal_mod._safe_key(chip)


def index_path(instance_path, chip: str) -> Path:
    return _dir(instance_path, chip) / "index.json"


def _empty() -> dict:
    return {"since_n": 0, "cleared_at": None, "closed_sessions": {}, "archives": []}


def _norm(d: dict) -> dict:
    out = _empty()
    try:
        out["since_n"] = max(0, int(d.get("since_n") or 0))
    except (TypeError, ValueError):
        pass
    try:
        out["cleared_at"] = float(d["cleared_at"]) if d.get("cleared_at") is not None else None
    except (TypeError, ValueError):
        pass
    closed = d.get("closed_sessions") or {}
    if isinstance(closed, list):
        closed = {str(s): None for s in closed if s}
    keep: dict = {}
    for sid, upto in closed.items() if isinstance(closed, dict) else ():
        try:
            keep[str(sid)] = None if upto is None else int(upto)
        except (TypeError, ValueError):
            keep[str(sid)] = None
    out["closed_sessions"] = dict(list(keep.items())[-_CLOSED_KEEP:])
    out["archives"] = [a for a in (d.get("archives") or []) if isinstance(a, dict) and _ID.match(str(a.get("id") or ""))]
    return out


_cache: dict[str, tuple[tuple, dict]] = {}
_last_good: dict[str, dict] = {}
_cache_lock = threading.Lock()


def _copy(d: dict) -> dict:
    return json.loads(json.dumps(d))


def _remember(p: Path, sig, d: dict) -> None:
    with _cache_lock:
        if sig is not None:
            _cache[str(p)] = (sig, _copy(d))
        else:
            _cache.pop(str(p), None)
        _last_good[str(p)] = _copy(d)


def _read(p: Path, *, strict: bool) -> dict:
    """The index. Absent -> never cleared. Present but unreadable -> strict:
    ``Unreadable``; otherwise the last good read of it (a transient error must
    not flash a cleared conversation back), or never-cleared if there is none."""
    key = str(p)

    def fallback(why: str) -> dict:
        if strict:
            raise Unreadable(f"{p}: {why}")
        with _cache_lock:
            good = _last_good.get(key)
        return _copy(good) if good is not None else _empty()

    try:
        st = p.stat()
    except FileNotFoundError:
        return _empty()
    except OSError as exc:
        return fallback(str(exc))
    sig = (st.st_mtime_ns, st.st_size)
    with _cache_lock:
        hit = _cache.get(key)
    if hit and hit[0] == sig:
        return _copy(hit[1])
    try:
        d = safe_io.read_json(p)
    except FileNotFoundError:
        return _empty()
    except (OSError, ValueError) as exc:
        return fallback(str(exc))
    if not isinstance(d, dict):
        return fallback("not an object")
    out = _norm(d)
    _remember(p, sig, out)
    return _copy(out)


def _write(p: Path, cur: dict) -> None:
    safe_io.atomic_write_json(p, cur)
    _remember(p, None, cur)


def load(instance_path, chip: str) -> dict:
    """The chip's conversation state (never raises)."""
    return _read(index_path(instance_path, chip), strict=False)


def read_strict(instance_path, chip: str) -> dict:
    return _read(index_path(instance_path, chip), strict=True)


def visible(e: dict, conv: dict) -> bool:
    """Does this chat event belong to the panel's current conversation?"""
    try:
        n = int(e.get("n") or 0)
    except (TypeError, ValueError):
        return False
    if n <= int(conv.get("since_n") or 0):
        return False
    sid = e.get("session_id")
    closed = conv.get("closed_sessions") or {}
    if not sid or str(sid) not in closed:
        return True
    upto = closed[str(sid)]
    return upto is not None and n > upto


def closed(conv: dict, sid) -> bool:
    """Was this session id ended by a clear? (It is never resumed.)"""
    return bool(sid) and str(sid) in (conv.get("closed_sessions") or {})


def ended_by_clear(conv: dict, sid) -> bool:
    """Ended by a clear, and no session has started since (its events are all hidden)."""
    return closed(conv, sid) and conv["closed_sessions"][str(sid)] is None


def _title(events: list[dict]) -> str:
    for e in events:
        if e.get("hook_event_name") == "User" and str(e.get("text") or "").strip():
            t = " ".join(str(e["text"]).split())
            return t if len(t) <= TITLE_CHARS else t[: TITLE_CHARS - 1] + "…"
    return ""


def _messages(events: list[dict]) -> int:
    return sum(1 for e in events if e.get("hook_event_name") in ("User", "Text"))


def _write_lines(p: Path, events: list[dict]) -> None:
    tmp = p.with_name(p.name + f".{uuid.uuid4().hex[:8]}.tmp")
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            for e in events:
                f.write(json.dumps(e, default=str) + "\n")
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, p)
    finally:
        try:
            tmp.unlink()
        except OSError:
            pass


def clear(instance_path, chip: str, *, since_n: int, events: list[dict], who: str, keep: bool,
          closed_session: str | None = None, expect_since: int | None = None,
          now: float | None = None) -> dict | None:
    """Start the panel's conversation over after ``since_n``. With ``keep``
    and something to keep, the events become an archive; returns its meta
    (None when nothing was kept). ``expect_since`` is the ``since_n`` the
    caller saw: if another clear moved it meanwhile, ``Moved`` is raised and
    nothing is written. Raises ``Unreadable`` rather than rewrite an index it
    could not read; an ``OSError`` writing leaves the index as it was."""
    p = index_path(instance_path, chip)
    p.parent.mkdir(parents=True, exist_ok=True)
    now = time.time() if now is None else now
    with safe_io.path_lock(p):
        cur = _read(p, strict=True)
        prev = int(cur["since_n"])
        if expect_since is not None and int(expect_since) != prev:
            raise Moved(f"the conversation was cleared meanwhile (since {prev}, not {expect_since})")
        events = [e for e in events if int(e.get("n") or 0) > prev]
        meta = None
        if keep and events:
            aid = uuid.uuid4().hex[:12]
            _write_lines(p.parent / (aid + ".jsonl"), events)
            ts = [float(e.get("ts") or 0) for e in events if e.get("ts")]
            meta = {"id": aid, "from_n": prev, "to_n": int(since_n),
                    "from_ts": min(ts) if ts else None, "to_ts": max(ts) if ts else None,
                    "title": _title(events), "messages": _messages(events), "events": len(events),
                    "cleared_by": who, "cleared_at": now}
            cur["archives"].append(meta)
        cur["since_n"] = max(prev, int(since_n))
        cur["cleared_at"] = now
        if closed_session:
            closed_map = dict(cur["closed_sessions"])
            closed_map.pop(str(closed_session), None)
            closed_map[str(closed_session)] = None
            cur["closed_sessions"] = dict(list(closed_map.items())[-_CLOSED_KEEP:])
        try:
            _write(p, cur)
        except OSError:
            if meta is not None:
                try:
                    (p.parent / (meta["id"] + ".jsonl")).unlink()
                except OSError:
                    pass
            raise
    return meta


def reopen(instance_path, chip: str, upto_n: int) -> None:
    """A session started after the clear: a closed session's events are hidden
    up to ``upto_n`` (the counter just before that start) and shown after it --
    a CLI that reuses a session id is the new conversation from there on."""
    p = index_path(instance_path, chip)
    if not p.exists():
        return
    with safe_io.path_lock(p):
        try:
            cur = _read(p, strict=True)
        except Unreadable:
            return
        open_ = {k: v for k, v in cur["closed_sessions"].items() if v is None}
        if not open_:
            return
        for k in open_:
            cur["closed_sessions"][k] = int(upto_n)
        try:
            _write(p, cur)
        except OSError:
            pass


def archives(instance_path, chip: str) -> list[dict]:
    """The kept conversations, newest first."""
    return list(reversed(load(instance_path, chip)["archives"]))


def read_archive(instance_path, chip: str, aid: str) -> tuple[dict, list[dict]] | None:
    """(meta, events) of one archive, or None when there is no such archive.
    A copy whose file went missing reads as its meta with no events."""
    if not _ID.match(str(aid or "")):
        return None
    meta = next((a for a in load(instance_path, chip)["archives"] if a.get("id") == aid), None)
    if meta is None:
        return None
    out: list[dict] = []
    try:
        with safe_io.open_shared(_dir(instance_path, chip) / (aid + ".jsonl")) as f:
            text = f.read().decode("utf-8")
    except OSError:
        return meta, out
    for line in text.splitlines():
        try:
            e = json.loads(line)
        except ValueError:
            continue
        if isinstance(e, dict):
            out.append(e)
    return meta, out


def delete_archive(instance_path, chip: str, aid: str) -> bool:
    """Forget one archive: its entry first, then its file (a file left behind
    by a failed unlink is unreachable, never shown)."""
    if not _ID.match(str(aid or "")):
        return False
    p = index_path(instance_path, chip)
    with safe_io.path_lock(p):
        cur = _read(p, strict=True)
        keep = [a for a in cur["archives"] if a.get("id") != aid]
        if len(keep) == len(cur["archives"]):
            return False
        cur["archives"] = keep
        _write(p, cur)
    try:
        (p.parent / (aid + ".jsonl")).unlink()
    except OSError:
        pass
    return True
