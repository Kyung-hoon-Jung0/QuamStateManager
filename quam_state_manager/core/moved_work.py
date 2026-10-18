"""Unapplied work left behind by a chip folder that moved (v1.5.1, SB-F6).

A working copy is keyed by its live folder's path. A chip folder moved (or
renamed) while it held unapplied work opens at the new path with a NEW,
clean working copy -- "In sync", no rows, no notice -- while the draft and
the tray rows sat under the old key, reachable only by opening a path that
is no longer there.

On a chip's first open at a path, :func:`find` looks for working copies
whose live folder is GONE, that hold the same chip (network and labels
align), and that carry unapplied work: staged rows (a tray checkpoint, of
any process, or one already set aside) and/or saved-but-unapplied changes
(the working files differ from their sync point). :func:`hand_over` turns
that work into ONE refused checkpoint of the new chip -- the sync panel then
lists it, persistently, naming the old folder, with Restore (staged,
collision-checked against what the chip holds now) and Discard -- and marks
the old copy as handed over, so it is offered once. The old copy's files
stay where they are; nothing is written to any chip folder.

Saved-but-unapplied changes become rows only when they can be told apart:
when the new folder still holds the content the old copy was last synced
with (a plain move). Otherwise the notice says the old copy's saved changes
could not be matched and names its folder.
"""
from __future__ import annotations

import json
import logging
import os
import uuid
from pathlib import Path

from . import pending_tray, safe_io, working_copy
from .loader import ChangeEntry

logger = logging.getLogger(__name__)

_MOVED_TAG = ".moved-"
#: (meta path, its mtime, the chip asked about) already judged "another
#: chip" -- not read again until that meta changes
_SKIP: set = set()


def _checkpoints(root: Path, key: str) -> list[Path]:
    """Every tray checkpoint of *key* that still holds undecided rows: the
    plain one, every process's peer file, and refused (listed) ones."""
    return sorted(working_copy.tray_checkpoints(root, key),
                  key=lambda p: p.stat().st_mtime_ns if p.exists() else 0)


def holds_checkpoint(instance_path, key: str) -> bool:
    """Does working copy *key* have a tray checkpoint with undecided rows?
    (The working-copy GC keeps such a copy, whatever its files say.)"""
    return bool(_checkpoints(working_copy.working_state_root(instance_path), key))


def find(instance_path, live_folder, state: dict, wiring: dict) -> list[dict]:
    """The vanished-folder working copies of this chip that hold unapplied
    work, oldest first. Cheap when there are none: one directory listing and
    the small meta files; working files are read only for a copy whose live
    folder is gone."""
    from . import history
    root = working_copy.working_state_root(instance_path)
    try:
        me = working_copy.key_for(live_folder)
    except Exception:  # noqa: BLE001
        return []
    try:
        metas = sorted(root.glob("*.meta.json"))
    except OSError:
        return []
    want = history.fingerprint_from_dicts(state, wiring)
    out = []
    for meta_path in metas:
        key = meta_path.name[: -len(".meta.json")]
        if key == me:
            continue
        try:
            skip = (str(meta_path), meta_path.stat().st_mtime_ns, me)
        except OSError:
            continue
        if skip in _SKIP:
            continue
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        old = meta.get("live_folder")
        if not old or meta.get("moved_to"):
            continue
        try:
            if Path(old).exists():
                continue                    # not moved: that folder is still there
        except OSError:
            continue
        working = root / key
        try:
            w_state, w_wiring = safe_io.read_state_wiring(working)
        except (OSError, ValueError):
            continue
        if history.align(want, history.fingerprint_from_dicts(w_state, w_wiring)) \
                != history.ALIGN_ALIGNED:
            _SKIP.add(skip)
            continue                        # another chip
        synced = meta.get("synced_live_hash")
        saved = isinstance(synced, str) and working_copy.content_hash(w_state, w_wiring) != synced
        tray = _checkpoints(root, key)
        if not saved and not tray:
            continue                        # nothing unapplied
        out.append({"key": key, "live_folder": old, "working_folder": str(working),
                    "meta_path": str(meta_path), "meta": meta, "synced": synced,
                    "state": w_state, "wiring": w_wiring, "saved": saved, "tray": tray})
    return out


def _saved_rows(orphan: dict, store) -> list[ChangeEntry]:
    """The old copy's saved-but-unapplied changes as rows onto the new chip:
    every leaf where its working files differ from what the new folder holds.
    Only called when the new folder holds the old sync point's content."""
    from .differ import Differ
    rows = []
    new_side = (store.state, store.wiring)
    wiring_keys = set(store.wiring or {})
    for d in Differ().diff(new_side, (orphan["state"], orphan["wiring"]), ignore_keys=set()):
        src = "wiring" if d.dot_path.split(".", 1)[0] in wiring_keys or (
            d.dot_path.split(".", 1)[0] in (orphan["wiring"] or {})) else "state"
        if d.change_type == "added":
            rows.append(ChangeEntry(d.dot_path, None, d.new_value, src, created=True))
        elif d.change_type == "removed":
            rows.append(ChangeEntry(d.dot_path, d.old_value, None, src, deleted=True))
        else:
            rows.append(ChangeEntry(d.dot_path, d.old_value, d.new_value, src))
    return rows


def _tray_rows(path: Path) -> list[ChangeEntry]:
    try:
        doc = safe_io.read_json(path, attempts=1)
    except (OSError, ValueError):
        return []
    raw = doc.get("entries") if isinstance(doc, dict) else None
    rows = []
    known = set(pending_tray._ROW_FIELDS)
    for e in raw or []:
        if not isinstance(e, dict) or not isinstance(e.get("dot_path"), str) or not e["dot_path"]:
            continue
        if e.get("source_file") not in ("state", "wiring"):
            continue
        fields = {k: v for k, v in e.items() if k in known}
        fields.setdefault("old_value", None)
        fields.setdefault("new_value", None)
        fields["created"] = fields.get("created") is True
        fields["deleted"] = fields.get("deleted") is True
        if not isinstance(fields.get("actor"), str):
            fields["actor"] = "human"
        if fields.get("group_id") is not None and not isinstance(fields["group_id"], str):
            fields["group_id"] = None
        fields["origin"] = None
        try:
            rows.append(ChangeEntry(**fields))
        except TypeError:
            continue
    return rows


def hand_over(orphan: dict, wc, store) -> dict:
    """Turn *orphan*'s unapplied work into a refused checkpoint of *wc* (the
    sync panel lists it with Restore / Discard) and mark the old copy handed
    over. Returns ``{"rows": [paths], "saved_unmatched": bool, "file": str}``."""
    rows: list[ChangeEntry] = []
    saved_unmatched = False
    if orphan["saved"]:
        if working_copy.content_hash(store.state, store.wiring) == orphan["synced"] or \
                working_copy.live_content_hash(wc) == orphan["synced"]:
            rows += _saved_rows(orphan, store)
        else:
            saved_unmatched = True
    for f in orphan["tray"]:
        rows += _tray_rows(f)
    out = {"rows": [r.dot_path for r in rows], "saved_unmatched": saved_unmatched, "file": ""}
    if rows:
        reason = (f"unapplied work left at {orphan['live_folder']}, which is not there any "
                  "more (moved or deleted)")
        out["file"] = str(pending_tray.set_aside(wc, rows, reason=reason))
    # handed over: the old checkpoints are archived beside the old copy (their
    # bytes stay), and its meta records where the work went -- offered once
    root = Path(orphan["meta_path"]).parent
    for f in orphan["tray"]:
        try:
            os.replace(f, root / f"{orphan['key']}.pending_tray{_MOVED_TAG}{uuid.uuid4().hex[:8]}.json")
        except OSError:
            logger.warning("moved work: could not archive %s", f, exc_info=True)
    if not saved_unmatched:
        # (saved changes that could not be matched are not handed over: the
        # next open of this chip names them again)
        try:
            meta = dict(orphan["meta"])
            meta["moved_to"] = wc.key
            safe_io.atomic_write_json(Path(orphan["meta_path"]), meta)
        except OSError:
            logger.warning("moved work: could not mark %s handed over", orphan["key"],
                           exc_info=True)
    return out
