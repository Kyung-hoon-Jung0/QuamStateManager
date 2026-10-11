"""Reference model of SM's state-update machine (VPLAN A.5). Pure Python, no SM imports.

The model holds what a user can know about one chip:

* ``L`` the live pair (merged dict), or :data:`UNREADABLE` / :data:`GONE`;
* ``B`` the live content at the last sync point;
* ``W`` the working content the user sees (working folder + tray);
* ``Ws`` the working folder (saved content);
* ``T`` the tray, a flat list of :class:`Entry` (``gid`` groups contiguous runs,
  ``None`` is a singleton -- the segmentation the undo journal uses);
* ``J`` the undo journal (:class:`Unit` list), ``cursor`` (RAM) and
  ``pcursor`` (persisted, moved only by a live walk step);
* ``dirty`` (saved-but-unapplied), ``staged`` (a wholesale load not on live),
  ``refused_at`` (the sync point an HTML Apply was refused at), ``undo_live``,
  the redo frames ``R``, the Take-live ``backup``.

Transitions decide (refuse / stage / land) from the model alone; structural
ops whose exact entries are SM's business (pulse rename, FSP bundle) are fed in
as entries the harness has already checked against their own contract.
Where SM is allowed to differ (snapshot throttles, caches) the harness asserts
the weaker form; the model is deliberately simpler than SM.
"""
from __future__ import annotations

import copy
import hashlib
import json
import math
from dataclasses import dataclass, field
from typing import Any


class _Sentinel:
    def __init__(self, name: str):
        self.name = name

    def __repr__(self) -> str:
        return self.name

    def __copy__(self):
        return self

    def __deepcopy__(self, memo):
        return self


MISSING = _Sentinel("MISSING")          # a path the document does not hold
UNREADABLE = _Sentinel("UNREADABLE")    # live pair present but not parseable
GONE = _Sentinel("GONE")                # live files / folder deleted

MAX_UNITS = 200


# ----------------------------------------------------------------------
# document helpers (dot paths; numeric segments index lists)
# ----------------------------------------------------------------------

def segs(path: str) -> list[str]:
    return [s for s in str(path).split(".") if s != ""]


def get(doc: Any, path: str, default: Any = MISSING) -> Any:
    cur = doc
    for s in segs(path):
        if isinstance(cur, dict):
            if s not in cur:
                return default
            cur = cur[s]
        elif isinstance(cur, list) and s.isdigit() and int(s) < len(cur):
            cur = cur[int(s)]
        else:
            return default
    return cur


def put(doc: dict, path: str, value: Any) -> None:
    parts = segs(path)
    cur = doc
    for s in parts[:-1]:
        if isinstance(cur, list):
            cur = cur[int(s)]
        else:
            cur = cur.setdefault(s, {})
    last = parts[-1]
    if isinstance(cur, list):
        cur[int(last)] = copy.deepcopy(value)
    else:
        cur[last] = copy.deepcopy(value)


def remove(doc: dict, path: str) -> None:
    parts = segs(path)
    parent = get(doc, ".".join(parts[:-1])) if len(parts) > 1 else doc
    if isinstance(parent, dict):
        parent.pop(parts[-1], None)
    elif isinstance(parent, list) and parts[-1].isdigit() and int(parts[-1]) < len(parent):
        parent.pop(int(parts[-1]))


def same(a: Any, b: Any) -> bool:
    """Type-strict deep equality: 1 != 1.0 != True, NaN == NaN (I11)."""
    if type(a) is not type(b):
        return False
    if isinstance(a, float):
        return (math.isnan(a) and math.isnan(b)) or a == b
    if isinstance(a, dict):
        return a.keys() == b.keys() and all(same(a[k], b[k]) for k in a)
    if isinstance(a, list):
        return len(a) == len(b) and all(same(x, y) for x, y in zip(a, b))
    return a == b


def leaves(doc: Any, prefix: str = "", *, empty_dicts: bool = True) -> dict[str, Any]:
    """Every leaf (scalars and empty containers) by dot path. With
    ``empty_dicts=False`` an empty dict is no leaf: an emptied container and
    a removed key then read alike (journal effects compare that way, as SM
    records a subtree delete without the container it leaves empty)."""
    out: dict[str, Any] = {}
    if isinstance(doc, dict) and doc:
        for k, v in doc.items():
            out.update(leaves(v, f"{prefix}.{k}" if prefix else str(k), empty_dicts=empty_dicts))
    elif isinstance(doc, list) and doc:
        for i, v in enumerate(doc):
            out.update(leaves(v, f"{prefix}.{i}" if prefix else str(i), empty_dicts=empty_dicts))
    elif isinstance(doc, dict) and not empty_dicts:
        pass
    else:
        out[prefix] = doc
    return out


def leaf_diff(a: Any, b: Any) -> dict[str, tuple[Any, Any]]:
    """{leaf path: (a value, b value)} wherever the two documents differ."""
    la, lb = leaves(a), leaves(b)
    out = {}
    for p in set(la) | set(lb):
        va, vb = la.get(p, MISSING), lb.get(p, MISSING)
        if va is MISSING or vb is MISSING or not same(va, vb):
            out[p] = (va, vb)
    return out


def canon(doc: Any) -> str:
    return json.dumps(doc, sort_keys=True, separators=(",", ":"), allow_nan=True)


def digest(doc: Any) -> str | None:
    if isinstance(doc, _Sentinel):
        return None
    return hashlib.sha1(canon(doc).encode("utf-8")).hexdigest()


def change_sig(paths: list[str]) -> str:
    """The tray's change-set signature (sha1 of the ordered dot paths)."""
    return hashlib.sha1("\n".join(paths).encode("utf-8")).hexdigest()[:12]


# ----------------------------------------------------------------------
# tray entries, journal units
# ----------------------------------------------------------------------

@dataclass
class Entry:
    path: str
    old: Any
    new: Any
    gid: str | None = None
    created: bool = False
    deleted: bool = False

    def effect(self) -> dict[str, tuple[Any, Any]]:
        """Leaf-level (old, new) of this entry."""
        old = MISSING if self.created else self.old
        new = MISSING if self.deleted else self.new
        return effect_of(self.path, old, new)


def effect_of(path: str, old: Any, new: Any) -> dict[str, tuple[Any, Any]]:
    lo = ({f"{path}.{k}" if k else path: v for k, v in leaves(old, empty_dicts=False).items()}
          if old is not MISSING else {})
    ln = ({f"{path}.{k}" if k else path: v for k, v in leaves(new, empty_dicts=False).items()}
          if new is not MISSING else {})
    out = {}
    for p in set(lo) | set(ln):
        a, b = lo.get(p, MISSING), ln.get(p, MISSING)
        if a is MISSING or b is MISSING or not same(a, b):
            out[p] = (a, b)
    return out


def segment(entries: list[Entry]) -> list[list[Entry]]:
    out: list[list[Entry]] = []
    for e in entries:
        if out and e.gid is not None and out[-1][-1].gid == e.gid:
            out[-1].append(e)
        else:
            out.append([e])
    return out


@dataclass
class Unit:
    entries: list[Entry]
    kind: str = "edit"          # edit | wholesale
    foreign: bool = False

    def effect(self) -> dict[str, tuple[Any, Any]]:
        """Net leaf-level effect, first old / last new per leaf."""
        out: dict[str, tuple[Any, Any]] = {}
        for e in self.entries:
            for p, (a, b) in e.effect().items():
                out[p] = (out[p][0], b) if p in out else (a, b)
        return {p: v for p, v in out.items()
                if v[0] is MISSING or v[1] is MISSING or not same(v[0], v[1])}


def apply_entry(doc: dict, e: Entry, forward: bool = True) -> None:
    if forward:
        if e.deleted:
            remove(doc, e.path)
        else:
            put(doc, e.path, e.new)
    else:
        if e.created:
            remove(doc, e.path)
        else:
            put(doc, e.path, e.old)


# ----------------------------------------------------------------------
# the model
# ----------------------------------------------------------------------

@dataclass
class Outcome:
    """What the model says a press must do."""
    kind: str                     # landed | refused | noop | staged | live | rolled_back | ...
    reason: str = ""
    writes_live: bool = False
    events: int = 0               # landed hub events this press must add
    extra: dict = field(default_factory=dict)


class Model:
    def __init__(self, doc: dict):
        self.L: Any = copy.deepcopy(doc)
        self.B: dict = copy.deepcopy(doc)
        self.W: dict = copy.deepcopy(doc)
        self.Ws: dict = copy.deepcopy(doc)
        self.T: list[Entry] = []
        self.J: list[Unit] = []
        self.cursor = 0
        self.pcursor = 0
        self.dirty = False
        self.stash: dict[str, Any] = {}      # pending_reapply: saved edits by field
        self.stash_orig: dict[str, Any] = {}
        self.stash_gid: dict[str, Any] = {}
        self._gids: dict[str, Any] = {}
        self.staged = False
        self.staged_doc: dict | None = None
        self.refused_at: str | None = None
        self.refused_land = 0          # land_count when the refusal was made
        self.land_count = 0            # sync-point moves (apply, pull, restore, walk)
        self.undo_live = True
        self.R: list[tuple] = []
        self.backup: list[tuple[str, Any]] | None = None
        self.last_apply_pre: dict | None = None
        self.push_armed = False
        self.pull_armed = False
        self.notes: list = []        # recorded-finding notes the harness drains per step
        self.merge_tries = 0         # Auto-Sync merges since the last landed write
        self.touched = False         # live mtimes moved since the sync point (content or not)
        self.typefixed: set[str] = set()   # fields whose type a type-fix assigned
        self.folder_gone = False           # GONE because the whole live folder was deleted

    # -- facts ---------------------------------------------------------
    def readable(self) -> bool:
        return not isinstance(self.L, _Sentinel)

    def moved(self) -> bool:
        return self.readable() and digest(self.L) != digest(self.B)

    def tray_paths(self) -> list[str]:
        return [e.path for e in self.T]

    def fields(self) -> list[str]:
        out = list(dict.fromkeys(self.tray_paths()))
        return out + [p for p in self.stash if p not in out]

    def sig(self) -> str:
        return change_sig(self.tray_paths())

    def originals(self) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for e in self.T:
            out.setdefault(e.path, MISSING if e.created else e.old)
        out.update(self.stash_orig)          # the stash's originals are earlier
        return out

    def conflicts(self) -> list[str]:
        """Fields the user edited that the live chip moved away from their
        original (sync_conflict.classify, leaf edits)."""
        if not self.moved():
            return []
        out = []
        created = [e.path for e in self.T if e.created]
        for p, orig in self.originals().items():
            lv, wv = get(self.L, p), get(self.W, p)
            if lv is MISSING and any(p.startswith(c + ".") for c in created):
                continue          # inside a subtree the user created: live never had it
            if not _eq(lv, wv) and not _eq(lv, orig):
                out.append(p)
        return sorted(out)

    def refused_revived(self) -> bool:
        """SU-08: a refusal made at a sync point the chip later returned to
        (A-B-A) counts again, although writes landed since."""
        return (self.refused_at is not None and self.refused_at == digest(self.B)
                and self.land_count > self.refused_land)

    def verdict(self) -> str:
        """sync_status.derive_state over the model's facts (I12)."""
        if not self.readable():
            return "unreadable"
        moved = self.moved()
        unapplied = len(self.fields())
        if moved and self.conflicts():
            return "collide"
        refused = self.refused_at is not None and self.refused_at == digest(self.B)
        if refused and (unapplied or self.dirty):
            return "refused"
        if moved:
            return "both" if (unapplied or self.dirty) else "live"
        if unapplied:
            return "mine"
        if self.dirty:
            return "staged"
        return "synced"

    # -- tray ------------------------------------------------------------
    def stage_entries(self, entries: list[Entry]) -> None:
        """A staging door's entries (already checked by the harness)."""
        for e in entries:
            apply_entry(self.W, e, True)
            self.T.append(copy.deepcopy(e))
        if entries:
            self.R.clear()

    def _pop_group(self) -> list[Entry]:
        seg = segment(self.T)[-1]
        del self.T[len(self.T) - len(seg):]
        for e in reversed(seg):
            apply_entry(self.W, e, False)
        return seg

    def _top_is_jrn(self) -> bool:
        return bool(self.T) and str(self.T[-1].gid or "").startswith("jrn:")

    def undo_press(self, n: int = 1) -> Outcome:
        """``/undo?n=k``: pop ordinary groups; the journal boundary ends the
        burst; a press that starts at it walks ONE journal unit."""
        groups = 0
        walk = False
        for _ in range(max(1, n)):
            if not self.T or self._top_is_jrn():
                if not self.T and self.staged:
                    return Outcome("noop", "loaded version") if not groups else Outcome("tray")
                if groups == 0:
                    walk = True
                break
            seg = self._pop_group()
            self.R.append(("tray", seg))
            groups += 1
        if walk:
            return self.walk_undo()
        return Outcome("tray" if groups else "noop")

    def redo_press(self, n: int = 1) -> Outcome:
        if self._top_is_jrn():
            self._pop_group()
            self.cursor = min(self.cursor + 1, len(self.J))
            return Outcome("unstaged")
        if self.R and self.R[-1][0] == "jrn_live":
            idx = self.R.pop()[1]
            if idx != self.cursor or idx >= len(self.J):
                return Outcome("noop", "dead frame")
            return self.walk_redo_live(idx)
        if not self.R and not self.T and self.undo_live and self.cursor < len(self.J):
            return self.walk_redo_live(self.cursor)
        done = 0
        for _ in range(max(1, n)):
            if self._top_is_jrn() or not self.R or self.R[-1][0] != "tray":
                break
            _, seg = self.R.pop()
            is_jrn = bool(seg) and str(seg[0].gid or "").startswith("jrn:")
            for e in seg:
                e = copy.deepcopy(e)
                if not e.created:
                    now = get(self.W, e.path)
                    e.old = None if now is MISSING else copy.deepcopy(now)
                apply_entry(self.W, e, True)
                self.T.append(e)
            if is_jrn:
                self.cursor = max(self.cursor - 1, 0)
            done += 1
        return Outcome("tray" if done else "noop")

    def discard(self, index: int) -> Entry:
        e = self.T.pop(index)
        apply_entry(self.W, e, False)
        if str(e.gid or "").startswith("jrn:") and not any(x.gid == e.gid for x in self.T):
            self.cursor = min(self.cursor + 1, len(self.J))
        self.R.append(("tray", [e]))
        return e

    def discard_all(self) -> None:
        while self.T:
            jrn = self._top_is_jrn()
            seg = self._pop_group()
            if jrn:
                self.cursor = min(self.cursor + 1, len(self.J))
            self.R.append(("tray", seg))

    # -- journal ---------------------------------------------------------
    def _journal_append(self, units: list[Unit]) -> None:
        if not units:
            return
        self.J = self.J[:self.pcursor] + units
        if len(self.J) > MAX_UNITS:
            self.J = self.J[-MAX_UNITS:]
        self.cursor = self.pcursor = len(self.J)

    def _journal_reset(self) -> None:
        self.cursor = self.pcursor

    def _units_of_tray(self) -> list[Unit]:
        units = [Unit(entries=copy.deepcopy(seg)) for seg in segment(self.T)]
        # SU-09: a created entry's value is the live subtree itself (aliased),
        # so the unit records what the subtree holds NOW, edits inside included
        for u in units:
            for e in u.entries:
                if e.created:
                    now = get(self.W, e.path)
                    if now is not MISSING and not same(now, e.new):
                        self.notes.append(("K:SU-09", f"the created {e.path} is journaled with later "
                                                      "edits inside it (the entry aliases the subtree)"))
                        e.new = copy.deepcopy(now)
        return units

    # -- working-copy writers --------------------------------------------
    def save(self) -> Outcome:
        if not self.T:
            return Outcome("noop", "no unsaved changes")
        self._journal_append(self._units_of_tray())
        orig = {}
        for e in self.T:
            orig.setdefault(e.path, MISSING if e.created else e.old)
        orig.update(self.stash_orig)
        for p in dict.fromkeys(self.tray_paths()):
            self.stash[p] = copy.deepcopy(get(self.W, p))
            self.stash_gid[p] = self._gid_of(p)
        self.stash_orig = {p: v for p, v in orig.items() if p in self.stash}
        self.Ws = copy.deepcopy(self.W)
        self.T = []
        self.dirty = True
        return Outcome("saved")

    def _covered_by_delete(self, p: str, fields: list[str]) -> bool:
        """An edit under a field the user deleted is superseded by the delete."""
        return any(p.startswith(q + ".") and get(self.W, q) is MISSING for q in fields)

    def _take_backup(self) -> None:
        fields = self.fields()
        ups = [(p, get(self.W, p)) for p in fields if not self._covered_by_delete(p, fields)]
        self.backup = ups or None

    def take_live(self, *, force: bool) -> Outcome:
        if self.dirty and (self.staged or not self.stash) and not force and self.readable():
            return Outcome("needs_confirm")
        if self.T or self.dirty or self.stash:
            self._take_backup()
        if not self.readable():
            # SU-17: the backup is written before the live read that refuses
            if self.backup:
                self.notes.append(("K:SU-17", "a Take live refused over an unreadable live file "
                                              "still left a Take-live backup"))
            return Outcome("refused", "live unreadable")
        self._adopt_live()
        return Outcome("pulled")

    def _adopt_live(self) -> None:
        self.W = copy.deepcopy(self.L)
        self.Ws = copy.deepcopy(self.L)
        self.B = copy.deepcopy(self.L)
        self.T = []
        self.dirty = self.staged = False
        self.stash, self.stash_orig, self.stash_gid = {}, {}, {}
        self.staged_doc = None
        self.land_count += 1
        self.touched = False
        self.R.clear()
        self._journal_reset()

    def replay_target(self, picks: dict[str, str] | None = None) -> dict:
        """L with every field the user holds set to the user's value (mine
        wins), except the fields picked ``live``."""
        out = copy.deepcopy(self.L)
        for p in self.fields():
            if _picked_live(p, picks):
                continue
            v = get(self.W, p)
            if v is MISSING:
                remove(out, p)
            else:
                put(out, p, v)
        return out

    def _gid_of(self, p: str):
        last = None
        for e in self.T:
            if e.path == p:
                last = e.gid
        if last is None:
            last = self.stash_gid.get(p)
        return last

    def _replayed(self, fields: list[str], target: dict) -> list[Entry]:
        out = []
        for p in fields:
            if self._covered_by_delete(p, fields):
                continue
            old, new = get(self.L, p), get(target, p)
            if old is MISSING and new is MISSING:
                continue
            out.append(Entry(p, None if old is MISSING else old, None if new is MISSING else new,
                             gid=self._gids.get(p), created=old is MISSING, deleted=new is MISSING))
        return out

    def replay_fields(self) -> list[str]:
        """The replay map's order: the stash's fields, then the log's new ones."""
        out = list(self.stash)
        return out + [p for p in dict.fromkeys(self.tray_paths()) if p not in self.stash]

    def reapply(self, *, force: bool) -> Outcome:
        if not self.readable():
            return Outcome("refused", "live unreadable")
        if self.dirty and (self.staged or not self.stash) and not force:
            return Outcome("needs_confirm")
        target = self.replay_target()
        fields = self.replay_fields()
        self._gids = {p: self._gid_of(p) for p in fields}
        self._adopt_live()
        self.W = target
        self.T = self._replayed(fields, target)
        return Outcome("reapplied", extra={"fields": fields})

    def bring_back(self) -> Outcome:
        if not self.backup:
            return Outcome("noop", "nothing to bring back")
        ents = []
        failed = []
        for p, v in self.backup:
            parent = p.rsplit(".", 1)[0] if "." in p else ""
            if parent and get(self.W, parent) is MISSING:
                failed.append(p)          # its container is gone now: cannot be re-staged
                continue
            cur = get(self.W, p)
            if not (cur is MISSING and v is MISSING):
                ents.append(Entry(p, None if cur is MISSING else cur, None if v is MISSING else v,
                                  gid="replay", created=cur is MISSING, deleted=v is MISSING))
        self.backup = None
        for e in ents:
            apply_entry(self.W, e, True)
        self.T.extend(ents)
        return Outcome("brought_back", extra={"n": len(ents), "failed": failed})

    def stage(self, doc: dict, *, force: bool) -> Outcome:
        if (self.T or self.dirty) and not force:
            return Outcome("needs_confirm")
        self.W = copy.deepcopy(doc)
        self.Ws = copy.deepcopy(doc)
        self.staged_doc = copy.deepcopy(doc)
        self.T = []
        self.stash, self.stash_orig, self.stash_gid = {}, {}, {}
        self.dirty = self.staged = True
        self.R.clear()
        self._journal_reset()
        return Outcome("staged_doc")

    # -- live writers ------------------------------------------------------
    def _wholesale_entries(self, before: dict | None = None, staged_over: bool = True) -> list[Entry]:
        """The docs/160 B wholesale unit with the docs/65 mixed-state rule:
        live before the write -> the payload, a tray subtree add/delete left
        to its own unit (excluded), a tray leaf edit recorded at its STAGED
        value (the oldest entry's old) so the units compose back."""
        after = copy.deepcopy(self.W)
        excl: list[str] = []
        staged_vals: dict[str, Any] = {}
        for e in self.T:
            if e.created or e.deleted:
                excl.append(e.path)
            else:
                staged_vals.setdefault(e.path, MISSING if e.old is None and get(self.L, e.path) is MISSING
                                       else e.old)
        for p, v in (staged_vals.items() if staged_over else []):
            if v is MISSING:
                remove(after, p)
            else:
                put(after, p, v)
        base = self.L if before is None else before
        return [e for e in _entries_between(base, after)
                if not any(e.path == x or e.path.startswith(x + ".") for x in excl)]

    def _dup_note(self, door: str, unit: Unit | None) -> None:
        """SU-04: a wholesale unit computed against the saved working copy
        re-records edits a Save already journaled."""
        if unit is None or not self.stash:
            return
        dup = sorted(set(self.stash) & {e.path for e in unit.entries})
        if dup:
            self.notes.append(("K:SU-04", f"{door} journaled saved edits twice: {dup[:3]}"))

    def _land(self, *, kind: str, wholesale: Unit | None = None, wholesale_below: bool = True,
              snapshot: bool = True) -> Outcome:
        before = copy.deepcopy(self.L)
        units = self._units_of_tray()
        if wholesale is not None and not wholesale.entries:
            wholesale = None
        if self.readable() and same(self.L, self.W):
            # docs/116: live already holds the payload -- a provable no-op
            # write advances the sync point; nothing is written or recorded
            self._journal_append(units + ([wholesale] if wholesale is not None else []))
            self.Ws = copy.deepcopy(self.W)
            self.B = copy.deepcopy(self.L)
            self.T = []
            self.dirty = self.staged = False
            self.stash, self.stash_orig, self.stash_gid = {}, {}, {}
            self.staged_doc = None
            self.land_count += 1
            self.touched = False
            return Outcome("converged", kind)
        if wholesale is not None:
            units = ([wholesale] + units) if wholesale_below else (units + [wholesale])
        self._journal_append(units)
        if snapshot:
            self.last_apply_pre = before
        self.L = copy.deepcopy(self.W)
        self.Ws = copy.deepcopy(self.W)
        self.B = copy.deepcopy(self.W)
        self.T = []
        self.dirty = self.staged = False
        self.stash, self.stash_orig, self.stash_gid = {}, {}, {}
        self.staged_doc = None
        self.land_count += 1
        self.touched = False
        return Outcome("landed", kind, writes_live=True, events=1, extra={"before": before})

    def apply(self, *, force: bool = False, expect_fresh: bool | None = None,
              unseen: bool = False, html: bool = True) -> Outcome:
        """``/state/apply-to-live``. ``unseen``: the presser's declared change
        set differs from the log (and no ack). ``expect_fresh``: the forced
        press named the live hash as it is now."""
        if unseen:
            return Outcome("refused", "unseen_changes")
        pending = bool(self.T or self.dirty or self.stash)
        if force:
            if expect_fresh is False:
                if html:
                    self.refused_at = digest(self.B)
                    self.refused_land = self.land_count
                return Outcome("refused", "live_moved")
            if self.L is UNREADABLE or (self.L is GONE and not self.folder_gone):
                # no backup can be taken of a pair that is there but unreadable
                if html:
                    self.refused_at = digest(self.B)
                    self.refused_land = self.land_count
                return Outcome("refused", "no_backup")
            if not pending and not self.moved() and self.L is not GONE:
                return Outcome("noop", "nothing to apply")
        else:
            if self.L is UNREADABLE or (not pending and self.moved() and not same(self.L, self.W)):
                # the staleness preflight runs before "nothing to apply"
                if html:
                    self.refused_at = digest(self.B)
                    self.refused_land = self.land_count
                return Outcome("refused", "live unreadable" if self.L is UNREADABLE else "stale_live")
            if not pending:
                return Outcome("noop", "nothing to apply")
            if not self.readable():
                return Outcome("refused", "live unreadable")
        moved = self.moved()
        if moved and not force:
            if self.readable() and same(self.L, self.W):
                return self._converged()
            if html:
                self.refused_at = digest(self.B)
                self.refused_land = self.land_count
            return Outcome("refused", "stale_live")
        wholesale = None
        if self.staged and self.staged_doc is not None:
            wholesale = Unit(entries=self._wholesale_entries(), kind="wholesale")
            self._dup_note("Apply after a stage", wholesale)
        elif force and self.L is GONE:
            # nothing could be read before the write: the unit is the payload
            # itself, tray leaf edits at their NEW values (no staged-over)
            ents = self._wholesale_entries(before={}, staged_over=False)
            wholesale = Unit(entries=ents, kind="wholesale") if ents else None
        elif force and self.readable():
            # the overwritten live content, up to the working copy the tray
            # edits sit on (the edits are their own units above it). SU-04:
            # SM records it whether or not live moved, so saved edits that
            # already have their own unit are journaled a second time.
            ents = self._wholesale_entries()
            wholesale = Unit(entries=ents, kind="wholesale") if ents else None
            self._dup_note("Keep mine", wholesale)
        return self._land(kind="keep_mine" if force else "apply", wholesale=wholesale,
                          wholesale_below=True)

    def _converged(self) -> Outcome:
        """docs/116: live already holds exactly the payload -- not a conflict."""
        self._journal_append(self._units_of_tray())
        self.Ws = copy.deepcopy(self.W)
        self.B = copy.deepcopy(self.L)
        self.T = []
        self.dirty = self.staged = False
        self.stash, self.stash_orig, self.stash_gid = {}, {}, {}
        self.land_count += 1
        self.touched = False
        return Outcome("converged")

    def merge_apply(self, *, picks: dict | None = None, check: bool = True,
                    ack: bool = False, unseen: bool = False) -> Outcome:
        """``/state/sync mode=apply`` (Pull & apply)."""
        if unseen:
            return Outcome("refused", "unseen_changes")
        if not self.readable():
            return Outcome("refused", "live unreadable")
        if self.dirty and (self.staged or not self.stash):
            # docs/65 carve-out: saved/staged content pushes whole, no pull
            if self.moved():
                self.refused_at = digest(self.B)
                self.refused_land = self.land_count
                return Outcome("refused", "conflict")
            unit = (Unit(entries=self._wholesale_entries(), kind="wholesale")
                    if self.staged and self.staged_doc is not None else None)
            self._dup_note("Pull & apply after a stage", unit)
            return self._land(kind="apply", wholesale=unit)
        if check and not ack:
            open_ = [p for p in self.conflicts() if p not in (picks or {})]
            if open_:
                # the collision answer carries the conflict tray (expect_chip
                # sent), which records the refusal like a stale Apply
                self.refused_at = digest(self.B)
                self.refused_land = self.land_count
                return Outcome("collision", extra={"paths": open_})
        if (not self.moved() and not self.touched and not picks and not self.stash
                and not self.dirty and not self.staged):
            # fast path: nothing to pull
            if not self.T:
                return Outcome("noop", "nothing to apply")
            return self._land(kind="apply")
        target = self.replay_target(picks)
        fields = [p for p in self.replay_fields() if not _picked_live(p, picks)]
        self._gids = {p: self._gid_of(p) for p in fields}
        self._adopt_live()
        self.W = target
        self.T = self._replayed(fields, target)
        if not self.T:
            return Outcome("pulled_only")
        return self._land(kind="pull_apply")

    def restore_live(self, doc: dict, *, force_pending: bool) -> Outcome:
        if (self.T or self.dirty) and not force_pending:
            return Outcome("needs_confirm")
        if not self.readable():
            return Outcome("refused", "live unreadable")
        before = copy.deepcopy(self.L)
        unit = Unit(entries=_entries_between(self.L, doc), kind="wholesale")
        if same(self.L, doc):
            # nothing to write: the backup and last_apply still move
            self.T = []
            self.W = copy.deepcopy(doc)
            self.Ws = copy.deepcopy(doc)
            self.B = copy.deepcopy(doc)
            self.dirty = self.staged = False
            self.stash, self.stash_orig, self.stash_gid = {}, {}, {}
            self.staged_doc = None
            self.last_apply_pre = before
            self.land_count += 1
            self.touched = False
            self.R.clear()
            return Outcome("converged", "restore", extra={"before": before})
        self.T = []
        self.W = copy.deepcopy(doc)
        self.dirty = self.staged = False
        self.stash, self.stash_orig, self.stash_gid = {}, {}, {}
        self.staged_doc = None
        self._journal_append([unit] if unit.entries else [])
        self.last_apply_pre = before
        self.L = copy.deepcopy(doc)
        self.Ws = copy.deepcopy(doc)
        self.B = copy.deepcopy(doc)
        self.land_count += 1
        self.touched = False
        self.R.clear()
        return Outcome("landed", "restore", writes_live=True, events=1, extra={"before": before})

    # -- the journal walk (Ctrl+Z on an empty tray / a jrn: top) -------------
    def walk_undo(self) -> Outcome:
        if self.cursor <= 0 or self.cursor > len(self.J):
            return Outcome("noop", "exhausted")
        unit = self.J[self.cursor - 1]
        if not unit.entries:
            self.cursor -= 1
            self.R.append(("jrn_live", self.cursor))
            return Outcome("noop", "empty unit")
        log_was_empty = not self.T
        if any((e.created and get(self.W, e.path) is MISSING)
               or (e.deleted and get(self.W, e.path) is not MISSING) for e in unit.entries):
            # SU-19: the unit is no longer in effect (a dropped save): its inverse
            # cannot be staged, the press answers 409 and the walk stays here
            self.notes.append(("K:SU-19", "Ctrl+Z met a unit the working copy no longer holds "
                                          "(Undo (staged) failed, 409)"))
            return Outcome("refused", "inverse not stageable")
        if any(e.path in self.typefixed and isinstance(e.old, str) and not isinstance(e.new, str)
               for e in unit.entries):
            self.notes.append(("K:SU-14", "Ctrl+Z cannot walk past a type-fix: staging the old text "
                                          "into the now-typed field fails (409), every later press too"))
            return Outcome("refused", "type assignment")
        drift = any(not _eq(get(self.W, p), new) for p, (_old, new) in unit.effect().items())
        inv = [Entry(e.path, e.new, e.old, gid=f"jrn:{id(unit)}", created=e.deleted, deleted=e.created)
               for e in reversed(unit.entries)]
        why = None
        if not self.undo_live:
            why = "off"
        elif not log_was_empty:
            why = "unapplied edits"
        elif self.staged:
            why = "staged snapshot"
        elif self.dirty or self.stash:
            why = "saved-but-unapplied"
        elif digest(self.Ws) != digest(self.B):
            why = "working changed since sync"
        elif drift:
            why = "moved since"
        elif unit.foreign:
            why = "another window"
        if why is not None:
            for e in inv:
                e.old = copy.deepcopy(get(self.W, e.path)) if not e.created else None
                apply_entry(self.W, e, True)
                self.T.append(e)
            self.cursor -= 1
            return Outcome("staged", why)
        if not self.readable():
            return Outcome("rolled_back", "live unreadable")
        if self.moved():
            # the door refused (stale): the walk is rolled back, the refusal is shown
            self.refused_at = digest(self.B)
            self.refused_land = self.land_count
            return Outcome("rolled_back", "changed since")
        before = copy.deepcopy(self.L)
        for e in inv:
            apply_entry(self.W, e, True)
        self.cursor -= 1
        self.pcursor = self.cursor
        self.R.append(("jrn_live", self.cursor))
        if self.W == {}:
            self.notes.append(("K:SU-13", "one Ctrl+Z wrote an EMPTY state to the live chip "
                                          "(the undone unit 'created' every top-level key)"))
        if same(self.L, self.W):
            self.Ws = copy.deepcopy(self.W)
            return Outcome("live", extra={"before": before, "unit": self.cursor, "noop_write": True})
        self.L = copy.deepcopy(self.W)
        self.Ws = copy.deepcopy(self.W)
        self.B = copy.deepcopy(self.W)
        self.land_count += 1
        self.touched = False
        return Outcome("live", writes_live=True, events=1, extra={"before": before, "unit": self.cursor})

    def walk_redo_live(self, idx: int) -> Outcome:
        """Ctrl+Shift+Z over a live-undone unit: forward, CAS on ``old``."""
        unit = self.J[idx]
        if any(not _eq(get(self.W, p), old) for p, (old, _new) in unit.effect().items()):
            return Outcome("refused", "moved since")
        if self.readable() and self.moved() and not (self.T or self.dirty or self.stash or self.staged):
            self.refused_at = digest(self.B)
            self.refused_land = self.land_count
            return Outcome("refused", "moved")
        if self.T or self.dirty or self.stash or self.staged or not self.readable() or self.moved():
            return Outcome("refused", "not at sync point")
        before = copy.deepcopy(self.L)
        for e in unit.entries:
            apply_entry(self.W, e, True)
        self.cursor = self.pcursor = idx + 1
        if same(self.L, self.W):
            self.Ws = copy.deepcopy(self.W)
            return Outcome("live", extra={"before": before, "noop_write": True})
        self.L = copy.deepcopy(self.W)
        self.Ws = copy.deepcopy(self.W)
        self.B = copy.deepcopy(self.W)
        self.land_count += 1
        self.touched = False
        return Outcome("live", writes_live=True, events=1, extra={"before": before})

    def restart(self) -> list[str]:
        """A new process on the same instance: RAM state dies; the tray rows
        come back from their sidecar only while the live chip still holds the
        sync point (pending_tray._restore). Returns the paths NOT restored."""
        self.R.clear()
        self.backup = None
        self.refused_at = None
        self.push_armed = self.pull_armed = False
        self.cursor = self.pcursor
        if not self.T:
            # the recovery sidecar carries the flags only with rows
            self.staged = False
            self.staged_doc = None
            self.stash, self.stash_orig, self.stash_gid = {}, {}, {}
        lost: list[str] = []
        if self.T and (not self.readable() or digest(self.L) != digest(self.B)):
            lost = list(dict.fromkeys(self.tray_paths()))
            self.T = []
            self.W = copy.deepcopy(self.Ws)
            self.stash, self.stash_orig, self.stash_gid = {}, {}, {}
            self.staged = False
            self.staged_doc = None
        if not (self.T and self.staged):
            self.dirty = digest(self.Ws) != digest(self.B)
        return lost

    # -- outside the app ---------------------------------------------------
    def outside(self, doc: Any) -> None:
        self.L = copy.deepcopy(doc)
        self.touched = True


def _picked_live(p: str, picks: dict | None) -> bool:
    """A "live" pick covers the picked path and everything under it."""
    return any(v == "live" and (p == k or p.startswith(k + ".")) for k, v in (picks or {}).items())


def _eq(a: Any, b: Any) -> bool:
    if a is MISSING or b is MISSING:
        return a is b
    return same(a, b)


def _entries_between(a: dict, b: dict, prefix: str = "") -> list[Entry]:
    """Entries taking document ``a`` to ``b`` (for wholesale units), as a tree
    difference: a key only one side holds is ONE created/deleted entry for its
    whole subtree (applying it back leaves no empty container), dicts recurse,
    anything else is a leaf set."""
    out: list[Entry] = []
    for k in list(a) + [k for k in b if k not in a]:     # document order, as SM walks it
        path = f"{prefix}.{k}" if prefix else str(k)
        if k not in a:
            out.append(Entry(path, None, copy.deepcopy(b[k]), created=True))
        elif k not in b:
            out.append(Entry(path, copy.deepcopy(a[k]), None, deleted=True))
        elif isinstance(a[k], dict) and isinstance(b[k], dict):
            out.extend(_entries_between(a[k], b[k], path))
        elif not same(a[k], b[k]):
            out.append(Entry(path, copy.deepcopy(a[k]), copy.deepcopy(b[k])))
    return out
