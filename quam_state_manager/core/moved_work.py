"""Unapplied work left behind by a chip folder that moved (v1.5.1, SB-F6).

A working copy is keyed by its live folder's path. A chip folder moved (or
renamed) while it held unapplied work opens at the new path with a NEW,
clean working copy -- "In sync", no rows, no notice -- while the draft and
the tray rows sat under the old key, reachable only by opening a path that
is no longer there.

On a chip's first open at a path, :func:`find` looks for working copies
whose live folder cannot be found, that hold the same chip (network and
labels align), and that carry unapplied work: staged rows (a tray checkpoint,
of any process, or one already set aside) and/or saved-but-unapplied changes
(the working files differ from their sync point). It also looks at tray
checkpoints an older working-copy GC left without their copy (R2 MINOR 7).

**Only on evidence of a move (R2 M5).** A folder that cannot be found may be
only out of reach for a while -- a network share reconnecting, a USB disk, a
parent folder renamed for a minute. The work is brought over only when the
new folder holds exactly the content the old copy was last synced with (its
``synced_live_hash``); otherwise the notice names the old folder and where
its work is kept, and nothing moves.

**A copy, never a move (R2 M5).** :func:`hand_over` COPIES that work into
ONE refused checkpoint of the new chip -- the sync panel lists it,
persistently, naming the old folder, with Restore (staged, collision-checked
against what the chip holds now) and Discard. The old copy's checkpoints stay
where they are, so the original folder, when it is back, opens with its own
tray. The offer is recorded under both keys: beside the old copy
(``<old key>.pending_tray.moved-offer.json``: offered once, and the old
folder is told on its next open) and in the new checkpoint (``moved_from``).
Nothing is written to any chip folder.

**Bounded probes (R2 M5).** "Can the old folder be found" is asked through
:mod:`folder_probe` -- parallel threads, at most 1 s in all, memoised per
process: an unreachable UNC path took 5-42 s per ``exists()``, paid for every
stale copy on every fresh build under that chip's build lock. An unanswered
probe counts as "there" (nothing is brought over).
"""
from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path

from . import folder_probe, pending_tray, safe_io, working_copy
from .loader import ChangeEntry

logger = logging.getLogger(__name__)

#: (meta path, its mtime, the chip asked about) already judged "another
#: chip" -- not read again until that meta changes
_SKIP: set = set()
#: The offer record beside the old copy (R2 M5). The GC deletes it with the
#: copy (it is one of the copy's ``<key>.pending_tray*`` files).
_OFFER_SUFFIX = ".pending_tray.moved-offer.json"
#: (old key, new key) a no-evidence note was already said for, this process
_NOTED: set = set()


def folders_present(paths) -> dict:
    """{path: True / False / None}: :func:`folder_probe.folders_present`
    (parallel, bounded, memoised; None = no answer, counted as there)."""
    return folder_probe.folders_present(paths)


def _checkpoints(root: Path, key: str) -> list[Path]:
    """Every tray checkpoint of *key* that still holds undecided rows: the
    plain one, every process's peer file, and refused (listed) ones."""
    return sorted(working_copy.tray_checkpoints(root, key),
                  key=lambda p: p.stat().st_mtime_ns if p.exists() else 0)


def holds_checkpoint(instance_path, key: str) -> bool:
    """Does working copy *key* have a tray checkpoint with undecided rows?
    (The working-copy GC keeps such a copy, whatever its files say.)"""
    return bool(_checkpoints(working_copy.working_state_root(instance_path), key))


def _offer_path(root: Path, key: str) -> Path:
    return root / f"{key}{_OFFER_SUFFIX}"


def _read_offer(root: Path, key: str) -> dict | None:
    try:
        doc = json.loads(_offer_path(root, key).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return doc if isinstance(doc, dict) else None


def _orphan_keys(root: Path, keys_with_meta: set) -> dict:
    """R2 MINOR 7: {key: [checkpoints]} for tray checkpoints whose working
    copy is gone (no meta, no working folder) -- an older GC deleted the copy
    and left them."""
    out: dict = {}
    try:
        names = [p.name for p in root.iterdir() if p.is_file()]
    except OSError:
        return out
    for name in names:
        i = name.find(".pending_tray")
        if i <= 0:
            continue
        key = name[:i]
        if key in keys_with_meta or key in out or (root / key).exists():
            continue
        cps = _checkpoints(root, key)
        if cps:
            out[key] = cps
    return out


def find(instance_path, live_folder, state: dict, wiring: dict) -> list[dict]:
    """The working copies (and orphaned checkpoints) of this chip whose live
    folder cannot be found and that hold unapplied work, oldest first. Each
    carries ``evidence``: the new folder holds the content the old copy was
    last synced with (only then is anything brought over). Cheap when there
    are none: one directory listing and the small meta files; working files
    are read only for a copy whose folder cannot be found."""
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
    here_hash = None

    def _here() -> str | None:
        nonlocal here_hash
        if here_hash is None:
            try:
                here_hash = working_copy.content_hash(state, wiring)
            except Exception:  # noqa: BLE001
                here_hash = ""
        return here_hash or None

    cands = []                               # (key, meta path, meta, old folder)
    keys_with_meta = set()
    for meta_path in metas:
        key = meta_path.name[: -len(".meta.json")]
        keys_with_meta.add(key)
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
        if not old or meta.get("moved_to") or _offer_path(root, key).exists():
            continue                         # offered once already
        cands.append((key, meta_path, meta, old, skip))
    orphans = {k: v for k, v in _orphan_keys(root, keys_with_meta).items()
               if k != me and not _offer_path(root, k).exists()}
    orphan_docs = {}
    for key, cps in orphans.items():
        doc = None
        for f in cps:
            try:
                doc = safe_io.read_json(f, attempts=1)
            except (OSError, ValueError):
                continue
            if isinstance(doc, dict) and doc.get("live_folder"):
                break
        if isinstance(doc, dict) and doc.get("live_folder"):
            orphan_docs[key] = doc
    present = folders_present([c[3] for c in cands]
                              + [d["live_folder"] for d in orphan_docs.values()])

    want = history.fingerprint_from_dicts(state, wiring)
    out = []
    for key, meta_path, meta, old, skip in cands:
        if present.get(str(old)) is not False:
            continue                         # there, or no answer: not moved
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
                    "state": w_state, "wiring": w_wiring, "saved": saved, "tray": tray,
                    "evidence": isinstance(synced, str) and synced == _here()})
    for key, doc in orphan_docs.items():
        old = doc["live_folder"]
        synced = doc.get("synced_live_hash")
        if present.get(str(old)) is not False:
            continue
        if not (isinstance(synced, str) and synced == _here()):
            continue                         # no copy to compare a design with: evidence only
        out.append({"key": key, "live_folder": old, "working_folder": None,
                    "meta_path": None, "meta": {}, "synced": synced, "state": None,
                    "wiring": None, "saved": False, "tray": orphans[key],
                    "evidence": True, "orphan": True})
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
    """COPY *orphan*'s unapplied work into a refused checkpoint of *wc* (the
    sync panel lists it with Restore / Discard) when ``orphan["evidence"]``
    holds, and record the offer under both keys. Without evidence nothing is
    copied: the result names where the work is kept. The old checkpoints are
    never moved or archived. Returns ``{"rows": [paths], "saved_unmatched":
    bool, "file": str, "copied": bool}``."""
    out = {"rows": [], "saved_unmatched": False, "file": "", "copied": False}
    if not orphan.get("evidence"):
        out["saved_unmatched"] = bool(orphan.get("saved"))
        out["rows"] = [r.dot_path for f in orphan["tray"] for r in _tray_rows(f)]
        return out
    rows: list[ChangeEntry] = []
    if orphan["saved"]:
        rows += _saved_rows(orphan, store)
    for f in orphan["tray"]:
        rows += _tray_rows(f)
    out["rows"] = [r.dot_path for r in rows]
    if not rows:
        return out
    reason = (f"unapplied work copied from {orphan['live_folder']}, which could not be "
              "found when this chip was opened (moved, renamed or offline); that "
              "folder's own copy stays with it")
    target = pending_tray.set_aside(wc, rows, reason=reason)
    try:
        doc = safe_io.read_json(target, attempts=1)
        doc["moved_from"] = {"key": orphan["key"], "folder": str(orphan["live_folder"])}
        safe_io.atomic_write_json(target, doc, compact=True)
    except (OSError, ValueError):
        logger.warning("moved work: could not record the source in %s", target, exc_info=True)
    out["file"] = str(target)
    out["copied"] = True
    root = Path(wc.working_folder).parent
    rec = _read_offer(root, orphan["key"]) or {"to": {}}
    rec.setdefault("to", {})[wc.key] = {
        "folder": str(wc.live_folder), "file": target.name, "rows": out["rows"][:50],
        "at": datetime.now().astimezone().isoformat(timespec="seconds"), "noticed": False}
    rec["from_folder"] = str(orphan["live_folder"])
    try:
        safe_io.atomic_write_json(_offer_path(root, orphan["key"]), rec)
    except OSError:
        logger.warning("moved work: could not record the offer of %s", orphan["key"],
                       exc_info=True)
    return out


def said_to_original(instance_path, wc) -> str | None:
    """R2 M5: the original folder is back. Its work was also copied to another
    chip while it could not be found: say so once (where, how many rows).
    None when there is nothing to say."""
    root = working_copy.working_state_root(instance_path)
    rec = _read_offer(root, wc.key)
    if not rec:
        return None
    fresh = [(k, v) for k, v in (rec.get("to") or {}).items()
             if isinstance(v, dict) and not v.get("noticed")]
    if not fresh:
        return None
    parts = []
    for _k, v in fresh:
        n = len(v.get("rows") or [])
        parts.append(f"{n} edit{'s' if n != 1 else ''} to {v.get('folder')}")
        v["noticed"] = True
    try:
        safe_io.atomic_write_json(_offer_path(root, wc.key), rec)
    except OSError:
        pass
    return ("While this folder could not be found, its unapplied work was also copied: "
            + "; ".join(parts) + ". That chip lists it in its sync panel; this chip "
            "keeps its own.")


def note_once(old_key: str, new_key: str) -> bool:
    """True the first time this process names *old_key*'s kept work to the
    chip *new_key* (a note without evidence is said once per process)."""
    k = (old_key, new_key)
    if k in _NOTED:
        return False
    _NOTED.add(k)
    return True


#: (meta mtime, working state mtime, working wiring mtime) -> saved-unapplied
_SAVED_MEMO: dict = {}


def pending_work(instance_path, live_folder) -> dict:
    """v1.5.1 (SB-P2-5, U P2-31): the unapplied work SM keeps for the chip at
    *live_folder*, read from the instance only (the folder itself may be
    gone): ``{"rows": fields staged in its tray checkpoints, "saved": the
    working copy holds saved-but-unapplied changes, "where": the folder that
    keeps them}``. Reads the small checkpoint files; the working pair is
    hashed once per (meta, working files) mtimes."""
    out = {"rows": 0, "saved": False, "where": ""}
    try:
        root = working_copy.working_state_root(instance_path)
        key = working_copy.key_for(live_folder)
    except Exception:  # noqa: BLE001 -- a notice never breaks a page
        return out
    out["where"] = str(root)
    # the FIELDS staged (two commits to one field are one edit, as the top bar counts)
    out["rows"] = len({r.dot_path for f in working_copy.tray_checkpoints(root, key)
                       for r in _tray_rows(f)})
    meta_path, working = root / f"{key}.meta.json", root / key
    try:
        stamp = (meta_path.stat().st_mtime_ns, (working / "state.json").stat().st_mtime_ns,
                 (working / "wiring.json").stat().st_mtime_ns)
    except OSError:
        return out
    memo = _SAVED_MEMO.get(str(meta_path))
    if memo is not None and memo[0] == stamp:
        out["saved"] = memo[1]
        return out
    saved = False
    try:
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        synced = meta.get("synced_live_hash")
        if isinstance(synced, str):
            w_state, w_wiring = safe_io.read_state_wiring(working)
            saved = working_copy.content_hash(w_state, w_wiring) != synced
    except (OSError, ValueError):
        return out
    _SAVED_MEMO[str(meta_path)] = (stamp, saved)
    out["saved"] = saved
    return out
