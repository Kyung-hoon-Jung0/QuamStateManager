"""A qubit renamed by Re-generate keeps its history (docs/296).

docs/295 made a rebuild carry each qubit's calibration under its new id. Every
history surface keys a value by its PATH, so without more the history of
``qubits.q1.f_01`` joins the qubit that was q1 before the rename with the one
that is q1 after it. This module is the lineage: the record a rebuild writes,
the era a saved state belongs to, and the translation of an id, a dot-path or a
whole state from one era into another -- by the one rule the rebuild itself
used (``regen_merge.rename_source_qubits`` and ``regen_merge.token_pattern``).

The record travels inside the state, so every copy of the chip carries it (each
run's saved state, every snapshot, the working copy): ``extras.qubit_renames``,
a list, oldest first. A saved state's ERA is the ids of the records it holds;
a state saved before a rename holds fewer. Nothing is dated by a clock, so a
run made on the old chip after the rebuild was written is still the old era.

Translation is exact in one direction and honest in the other:

* forward (older era -> newer): every id has a name after the rename (an id the
  rebuild did not keep gets the rebuild's own ``_removed`` / ``_stale`` /
  ``_source`` label, which names nothing on the new chip);
* backward (newer -> older): a renamed qubit or pair maps to its source id; a
  qubit or pair the source did not have under any name has no history before
  the rename, and its path translates to ``None`` -- never to whatever the old
  chip kept under the same name.
"""

from __future__ import annotations

import json
import os
import secrets
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from quam_state_manager.core import regen_merge

#: ``extras`` key of the rename records (a list, oldest first)
EXTRAS_KEY = "qubit_renames"
#: the chip-level copy of every record seen (history/<chip>/<this file>):
#: lets a state restored to before a rename still read the newer eras
REGISTRY_NAME = "qubit_renames.json"


# ---------------------------------------------------------------------------
# the record
# ---------------------------------------------------------------------------

def new_record(*, renames: dict, tokens: dict, pairs: dict, source_qubits: Iterable[str],
               source_pairs: Iterable[str], qubits_after: Iterable[str],
               pairs_after: dict, at: datetime | None = None) -> dict:
    """One rebuild's rename record (JSON-safe, sorted for a stable diff).

    ``renames`` ``{source id: new id}``; ``tokens`` / ``pairs`` the full token
    and pair maps the rebuild applied to the source (labels included);
    ``pairs_after`` ``{pair id: (control, target)}`` of the rebuilt chip."""
    when = (at or datetime.now(timezone.utc)).astimezone(timezone.utc)
    return {
        "id": secrets.token_hex(8),
        "at": when.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "by": "re-generate",
        "qubits": dict(sorted(renames.items())),
        "pairs": {o: n for o, n in sorted(pairs.items())
                  if not n.endswith(regen_merge._SOURCE_SUFFIX)},
        "tokens": dict(sorted(tokens.items())),
        "pair_map": dict(sorted(pairs.items())),
        "source_qubits": sorted(source_qubits),
        "source_pairs": sorted(source_pairs),
        "qubits_after": sorted(qubits_after),
        "pairs_after": {p: list(m) for p, m in sorted(pairs_after.items())},
    }


def _str_map(v: Any) -> bool:
    return isinstance(v, dict) and all(isinstance(k, str) and isinstance(x, str) for k, x in v.items())


def _str_list(v: Any) -> bool:
    return isinstance(v, list) and all(isinstance(x, str) for x in v)


def valid(rec: Any) -> bool:
    """A record this module can translate with (anything else is ignored --
    ``extras`` is free-form, a hand edit must not crash a history read)."""
    if not isinstance(rec, dict) or not isinstance(rec.get("id"), str) or not rec["id"]:
        return False
    if not all(_str_map(rec.get(k)) for k in ("qubits", "tokens", "pair_map")):
        return False
    if not all(_str_list(rec.get(k)) for k in ("source_qubits", "source_pairs", "qubits_after")):
        return False
    pa = rec.get("pairs_after")
    return isinstance(pa, dict) and all(
        isinstance(m, list) and len(m) == 2 and all(isinstance(x, str) for x in m) for m in pa.values())


def records(state: Any) -> list[dict]:
    """The state's chain: its valid rename records, oldest first. A state
    whose ``extras`` holds a broken entry has no chain past it."""
    extras = state.get("extras") if isinstance(state, dict) else None
    chain = extras.get(EXTRAS_KEY) if isinstance(extras, dict) else None
    out: list[dict] = []
    for rec in chain if isinstance(chain, list) else ():
        if not valid(rec):
            break
        out.append(rec)
    return out


def era(state: Any) -> tuple[str, ...]:
    """The ids of the state's chain -- which renames it was saved after."""
    return tuple(r["id"] for r in records(state))


def can_mark(state: Any, class_schemas: dict | None = None) -> bool:
    """Whether a rebuilt state can carry the record: its root already holds an
    ``extras`` object, or the build env's schema of the root class has an
    ``extras`` field (quam_builder's ``BaseQuam`` does; a key a root class does
    not know kills ``Quam.load``)."""
    if not isinstance(state, dict):
        return False
    if isinstance(state.get("extras"), dict):
        return True
    cls = state.get("__class__")
    fields = (class_schemas or {}).get(cls) if isinstance(cls, str) else None
    if isinstance(fields, dict):
        fields = fields.get("fields", fields)
    return isinstance(fields, (dict, list, tuple, set)) and "extras" in fields


def with_chain(state: dict, chain: list[dict]) -> dict:
    """``state`` (a copy) whose ``extras.qubit_renames`` is ``chain``."""
    out = dict(state)
    extras = dict(out.get("extras") or {}) if isinstance(out.get("extras"), dict) else {}
    if chain:
        extras[EXTRAS_KEY] = [dict(r) for r in chain]
    else:
        extras.pop(EXTRAS_KEY, None)
    if extras or isinstance(out.get("extras"), dict):
        out["extras"] = extras
    return out


# ---------------------------------------------------------------------------
# one rename, both directions
# ---------------------------------------------------------------------------

class Step:
    """One record, ready to translate ids, pair ids, dot-paths and states."""

    def __init__(self, rec: dict):
        self.rec = rec
        self.renames: dict[str, str] = rec["qubits"]
        self.tok: dict[str, str] = rec["tokens"]
        self.pmap: dict[str, str] = rec["pair_map"]
        self.src_q = set(rec["source_qubits"])
        self.src_p = set(rec["source_pairs"])
        self.after_q = set(rec["qubits_after"])
        self.after_p = {p: tuple(m) for p, m in rec["pairs_after"].items()}
        self.by_mem = {m: p for p, m in self.after_p.items()}
        self.inv = {cur: src for src, cur in self.renames.items()}
        self.inv_p = {n: o for o, n in self.pmap.items() if n in self.after_p}
        # ids the rebuild ADDED (no source qubit under any name): an older
        # state's qubit of that id is another qubit -- it moves forward as
        # "<id>_removed", like a source qubit whose id a rename took
        self.new_only = self.after_q - self.src_q - set(self.inv)
        self._fwd_pat = regen_merge.token_pattern(set(self.tok) | self.src_q | self.new_only)
        self._back_pat = regen_merge.token_pattern(set(self.inv) | self.after_q | set(self.tok))

    # -- ids -----------------------------------------------------------------
    def fwd_id(self, qid: str) -> str:
        if qid in self.tok:
            return self.tok[qid]
        return qid + regen_merge._DISPLACED_SUFFIX if qid in self.new_only else qid

    def back_id(self, qid: str) -> str | None:
        if qid in self.inv:
            return self.inv[qid]
        # the old qubit of that name was renamed away (or displaced), or the
        # source had no qubit of that name at all (the rebuild added it, or
        # it came later): the qubit holding it now has no history under it
        return None if qid in self.tok or qid not in self.src_q else qid

    def fwd_pair(self, pid: str) -> str:
        if pid in self.pmap:
            return self.pmap[pid]
        if pid in self.src_p:
            return pid                          # untouched by the rename
        # a pair the source no longer had (removed before the rebuild): named
        # "control-target", it is the rebuilt pair of the same members, else
        # it names nothing on the rebuilt chip
        c, sep, t = pid.partition("-")
        if sep and c and t:
            mem = (self.fwd_id(c), self.fwd_id(t))
            moved = set(self.tok.values())
            if mem[0] in moved or mem[1] in moved or pid in self.after_p:
                hit = self.by_mem.get(mem)
                return hit if hit is not None else pid + regen_merge._SOURCE_SUFFIX
            return pid
        return pid + regen_merge._SOURCE_SUFFIX if pid in self.after_p else pid

    def back_pair(self, pid: str) -> str | None:
        if pid in self.inv_p:
            return self.inv_p[pid]
        if pid in self.pmap:
            return None                         # the old pair of that id moved away
        if pid in self.after_p and pid not in self.src_p:
            return None                         # a pair the source did not have
        return pid

    # -- names inside a key / string ----------------------------------------
    def fwd_text(self, text: str) -> str:
        if self._fwd_pat is None:
            return text
        return self._fwd_pat.sub(lambda m: self.fwd_id(m.group(0)), text)

    def back_text(self, text: str) -> str | None:
        if self._back_pat is None:
            return text
        dead = False

        def sub(m):
            nonlocal dead
            # a renamed-away source id names no qubit after the rename: None
            got = self.back_id(m.group(0))
            if got is None:
                dead = True
                return m.group(0)
            return got
        out = self._back_pat.sub(sub, text)
        return None if dead else out

    # -- dot-paths -----------------------------------------------------------
    def _keys(self, keys: list[str], pair, text, ident) -> list[str] | None:
        out, free = [], False
        for i, seg in enumerate(keys):
            if free:
                out.append(seg)
                continue
            prev = keys[i - 1] if i else None
            if prev == "qubit_pairs":
                got = pair(seg)
            elif prev == "qubits" and (i == 1 or keys[i - 2] == "wiring"):
                got = ident(seg)               # an entity segment is an id, whole
            else:
                got = text(seg)
            if got is None:
                return None
            out.append(got)
            free = seg == "extras"
        return out

    def fwd_keys(self, keys: list[str]) -> list[str]:
        return self._keys(keys, self.fwd_pair, self.fwd_text, self.fwd_id)

    def back_keys(self, keys: list[str]) -> list[str] | None:
        return self._keys(keys, self.back_pair, self.back_text, self.back_id)

    def fwd_path(self, dot_path: str) -> str:
        return ".".join(self.fwd_keys(dot_path.split(".")))

    def back_path(self, dot_path: str) -> str | None:
        got = self.back_keys(dot_path.split("."))
        return None if got is None else ".".join(got)

    # -- string values -------------------------------------------------------
    def _value(self, v: Any, free: bool, pair, text) -> Any:
        """A string VALUE by the rebuild's own rule (``regen_merge._rewrite_ids``):
        a pointer segment by segment (the pair after ``qubit_pairs``, every other
        segment by its tokens; verbatim after ``extras``), a pair id whole, any
        other string by its qubit-id tokens -- except below ``extras``, where
        only pointers change. None: the value names something with no name in
        the other era."""
        if not isinstance(v, str):
            return v
        if v.startswith("#"):
            head, _, rest = v.partition("/")
            if not rest:
                return v
            keys = rest.split("/")
            out, open_ = [], True
            for i, seg in enumerate(keys):
                if not open_:
                    out.append(seg)
                    continue
                got = pair(seg) if i and keys[i - 1] == "qubit_pairs" else text(seg)
                if got is None:
                    return None
                out.append(got)
                open_ = seg != "extras"
            return head + "/" + "/".join(out)
        if free:
            return v
        whole = self.pmap if pair == self.fwd_pair else self.inv_p
        return whole[v] if v in whole else text(v)

    def fwd_value(self, v: Any, free: bool = False) -> Any:
        return self._value(v, free, self.fwd_pair, self.fwd_text)

    def back_value(self, v: Any, free: bool = False) -> Any:
        return self._value(v, free, self.back_pair, self.back_text)

    # -- whole states --------------------------------------------------------
    def fwd_state(self, state: dict, wiring: dict | None):
        """``(state, wiring)`` saved before this rename, in the ids after it --
        :func:`regen_merge.rename_source_qubits`, the rebuild's own rule."""
        own = state.get("qubits") if isinstance(state, dict) else None
        renames = dict(self.renames)
        for q in own if isinstance(own, dict) else ():
            if q in self.new_only and q not in renames:
                # an older state's qubit under an id the rebuild gave a NEW
                # qubit: held as "<id>_removed", never joined with it
                renames[q] = q + regen_merge._DISPLACED_SUFFIX
        s, w, _q, _p = regen_merge.rename_source_qubits(
            state, wiring, renames, new_ids=self.after_q,
            new_pairs={p: m for p, m in self.after_p.items()})
        return s, w


# ---------------------------------------------------------------------------
# between any two eras
# ---------------------------------------------------------------------------

class Lineage:
    """Every record known for a chip, and translation between its eras.

    An era is a tuple of record ids (a chain). Between two eras the path goes
    back to their common prefix, then forward: exact when both chains are
    known; ``None`` when a record is unknown or a step back names nothing."""

    def __init__(self, recs: Iterable[dict] = ()):
        self.recs: dict[str, dict] = {}
        self._steps: dict[str, Step] = {}
        self.add(recs)

    @property
    def active(self) -> bool:
        """Whether the chip was ever renamed (else every translation is the
        identity and a caller can skip the work)."""
        return bool(self.recs)

    def add(self, recs: Iterable[dict]) -> bool:
        grew = False
        for r in recs:
            if valid(r) and r["id"] not in self.recs:
                self.recs[r["id"]] = r
                grew = True
        return grew

    def step(self, rid: str) -> Step | None:
        if rid not in self._steps:
            rec = self.recs.get(rid)
            if rec is None:
                return None
            self._steps[rid] = Step(rec)
        return self._steps[rid]

    def _route(self, src: tuple, dst: tuple):
        k = 0
        while k < len(src) and k < len(dst) and src[k] == dst[k]:
            k += 1
        back = [self.step(r) for r in reversed(src[k:])]
        fwd = [self.step(r) for r in dst[k:]]
        if any(s is None for s in back + fwd):
            return None
        return back, fwd

    def keys(self, keys: list[str], src: tuple, dst: tuple) -> list[str] | None:
        """A path as a list of keys (a ledger holder's decoded segments),
        spelled in era ``src``, as spelled in era ``dst``."""
        if src == dst:
            return list(keys)
        route = self._route(src, dst)
        if route is None:
            return None
        k: list[str] | None = list(keys)
        for s in route[0]:
            k = s.back_keys(k)
            if k is None:
                return None
        for s in route[1]:
            k = s.fwd_keys(k)
        return k

    def path(self, dot_path: str, src: tuple, dst: tuple) -> str | None:
        """``dot_path`` spelled in era ``src``, spelled in era ``dst``."""
        if src == dst:
            return dot_path
        got = self.keys(dot_path.split("."), src, dst)
        return None if got is None else ".".join(got)

    def value(self, v: Any, src: tuple, dst: tuple, free: bool = False) -> Any:
        """A stored value of era ``src`` as era ``dst`` spells it: a string
        that names a qubit, a pair or an operation follows the renames (a
        pointer always; below ``extras`` -- ``free`` -- nothing else). Any
        other value is returned as is. A string with no name in ``dst`` is
        returned as recorded: a value is never dropped, and never re-pointed
        at another qubit."""
        if src == dst or not isinstance(v, str):
            return v
        route = self._route(src, dst)
        if route is None:
            return v
        out: Any = v
        for s in route[0]:
            out = s.back_value(out, free)
            if out is None:
                return v
        for s in route[1]:
            out = s.fwd_value(out, free)
        return out

    def qubit(self, qid: str, src: tuple, dst: tuple) -> str | None:
        """A qubit id of era ``src`` in era ``dst`` (``None``: no such qubit)."""
        if src == dst:
            return qid
        route = self._route(src, dst)
        if route is None:
            return None
        q: str | None = qid
        for s in route[0]:
            q = s.back_id(q)
            if q is None:
                return None
        for s in route[1]:
            q = s.fwd_id(q)
        return q

    def pair(self, pid: str, src: tuple, dst: tuple) -> str | None:
        if src == dst:
            return pid
        route = self._route(src, dst)
        if route is None:
            return None
        p: str | None = pid
        for s in route[0]:
            p = s.back_pair(p)
            if p is None:
                return None
        for s in route[1]:
            p = s.fwd_pair(p)
        return p

    def forward_state(self, state: dict, wiring: dict | None, dst: tuple):
        """A saved ``(state, wiring)`` re-expressed in era ``dst`` -- only
        forward (its own chain must be a prefix of ``dst``); ``None`` when it
        is not, or a record is unknown. The result carries ``dst``'s chain."""
        src = era(state)
        if src == dst:
            return state, wiring
        if dst[:len(src)] != src:
            return None
        steps = [self.step(r) for r in dst[len(src):]]
        if any(s is None for s in steps):
            return None
        for s in steps:
            state, wiring = s.fwd_state(state, wiring)
        return with_chain(state, [self.recs[r] for r in dst]), wiring

    def label(self, src: tuple, dst: tuple) -> list[dict]:
        """The renames between two eras, oldest first, for a surface to show:
        ``[{"id", "at", "qubits": {old: new}}]`` (forward, or reversed when
        ``src`` is the newer era)."""
        route = self._route(src, dst)
        if route is None:
            return []
        out = [{"id": s.rec["id"], "at": s.rec.get("at"), "back": True,
                "qubits": {cur: old for old, cur in s.renames.items()}} for s in route[0]]
        out += [{"id": s.rec["id"], "at": s.rec.get("at"), "back": False,
                 "qubits": dict(s.renames)} for s in route[1]]
        return out


_STEP_MEMO: dict[str, Step] = {}
_STEP_MEMO_MAX = 256


def _memo_step(rec: dict) -> Step:
    # review: no JSON dump per call (a renamed chip fingerprints often); a
    # record's id is random and its maps are written once with it
    key = (rec["id"], rec.get("at"), len(rec["tokens"]), len(rec["pair_map"]),
           len(rec["qubits_after"]), len(rec["pairs_after"]))
    step = _STEP_MEMO.get(key)
    if step is None:
        if len(_STEP_MEMO) >= _STEP_MEMO_MAX:
            _STEP_MEMO.clear()
        step = _STEP_MEMO[key] = Step(rec)
    return step


#: a base name for a qubit or pair that did not exist before a rename: it can
#: never equal a real id (a wizard id never starts with "+")
NEW_PREFIX = "+"


def base_names(state: Any) -> tuple[frozenset, frozenset]:
    """The state's qubit and pair ids as they were named BEFORE its first
    rename -- the labels of the chip's identity (``history.ChipFingerprint``):
    a rename by Re-generate does not make another chip. A qubit or pair with
    no name before is ``"+<id>"``, never mistaken for an old one."""
    s = state if isinstance(state, dict) else {}
    qubits = list((s.get("qubits") or {}).keys()) if isinstance(s.get("qubits"), dict) else []
    pairs = list((s.get("qubit_pairs") or {}).keys()) if isinstance(s.get("qubit_pairs"), dict) else []
    chain = records(s)
    if not chain:
        return frozenset(qubits), frozenset(pairs)
    steps = [_memo_step(r) for r in reversed(chain)]

    def back(x: str, fn) -> str:
        cur: str | None = x
        for st in steps:
            cur = fn(st, cur)
            if cur is None:
                return NEW_PREFIX + x
        return cur
    return (frozenset(back(q, Step.back_id) for q in qubits),
            frozenset(back(p, Step.back_pair) for p in pairs))


# ---------------------------------------------------------------------------
# the chip registry (every record a chip's history has seen)
# ---------------------------------------------------------------------------

_REG_LOCK = threading.Lock()


class _Unreadable(Exception):
    """The registry file exists and cannot be read as one (never overwritten)."""


def _read_registry(chip_dir) -> list[dict]:
    path = Path(chip_dir) / REGISTRY_NAME
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return []
    except OSError as exc:
        raise _Unreadable(str(exc)) from exc
    try:
        data = json.loads(text)
    except ValueError as exc:
        raise _Unreadable(str(exc)) from exc
    if not isinstance(data, dict) or not isinstance(data.get("records", []), list):
        raise _Unreadable("not a registry")
    return [r for r in data.get("records", []) if valid(r)]


def load_registry(chip_dir: str | Path | None) -> list[dict]:
    if not chip_dir:
        return []
    try:
        return _read_registry(chip_dir)
    except _Unreadable:
        return []


def remember(chip_dir: str | Path | None, chain: Iterable[dict]) -> list[dict]:
    """Add ``chain``'s records to the chip registry (written only when one is
    new); returns every record known. Best-effort: a registry that cannot be
    written loses nothing the states themselves do not carry."""
    chain = [r for r in chain if valid(r)]
    known = load_registry(chip_dir)
    if not chip_dir or not chain:
        return known
    have = {r["id"] for r in known}
    fresh = [r for r in chain if r["id"] not in have]
    if not fresh:
        return known
    with _REG_LOCK:
        try:
            known = _read_registry(chip_dir)
        except _Unreadable:
            # review: a registry that exists but cannot be read now (a torn
            # copy, a lock) is never rewritten from nothing -- that would drop
            # the records it holds for good
            return known + [r for r in fresh if r["id"] not in have]
        have = {r["id"] for r in known}
        known += [r for r in fresh if r["id"] not in have]
        path = Path(chip_dir) / REGISTRY_NAME
        tmp = path.with_name(path.name + f".{os.getpid()}.tmp")
        try:
            tmp.write_text(json.dumps({"records": known}, indent=1, sort_keys=True), encoding="utf-8")
            os.replace(tmp, path)
        except OSError:
            try:
                tmp.unlink()
            except OSError:
                pass
    return known


_FOLDER_RECORDS: dict[tuple, list] = {}


def folder_records(folder: str | Path) -> list[dict] | None:
    """The rename records of a folder's own saved state (``quam_state/
    state.json``, ``state.json``, or the older layouts), read once per file
    version; None when there is no readable saved state."""
    from quam_state_manager.core import hub_build
    folder = Path(folder)
    # review: the two standard layouts by a stat each (no directory listing on
    # every history read of a chip never renamed); others as the ledger reads
    for sp in (folder / "state.json", folder / "quam_state" / "state.json"):
        try:
            st = sp.stat()
            break
        except OSError:
            continue
    else:
        try:
            sp, _wp = hub_build.state_paths(folder)
            st = sp.stat()
        except OSError:
            return None
    key = (os.path.normcase(str(sp)), st.st_mtime_ns, st.st_size)
    hit = _FOLDER_RECORDS.get(key)
    if hit is not None:
        return hit
    try:
        raw = hub_build._read_shared(sp)     # never blocks a writer's save
    except OSError:
        return None
    if EXTRAS_KEY.encode() not in raw:
        out: list = []                       # no record anywhere in the file
    else:
        try:
            out = records(json.loads(raw))
        except ValueError:
            return None
    if len(_FOLDER_RECORDS) > 4096:
        _FOLDER_RECORDS.clear()
    _FOLDER_RECORDS[key] = out
    return out


def folder_era(folder: str | Path) -> tuple | None:
    """The rename era of a run folder's own saved state; None when there is
    no readable saved state. Used where the ledger does not hold the run
    (yet)."""
    recs = folder_records(folder)
    return None if recs is None else tuple(r["id"] for r in recs)


def earliest_at(recs: Iterable[dict]) -> datetime | None:
    """When the oldest of these records was written (no state saved before
    it can carry any of them)."""
    out = None
    for r in recs:
        try:
            t = datetime.strptime(str(r.get("at")), "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
        except ValueError:
            continue
        out = t if out is None or t < out else out
    return out


def lineage_for(chip_dir: str | Path | None, current_state: Any = None) -> Lineage:
    """The chip's lineage: the registry plus the current state's chain (which
    is remembered when new)."""
    chain = records(current_state)
    known = remember(chip_dir, chain) if chip_dir else []
    return Lineage(list(known) + chain)


def in_one_era(pairs: list) -> list:
    """docs/296: the sides of one comparison -- ``[(state, wiring), ...]`` --
    in ONE rename era: each side saved before a rename moved forward into the
    newest side's era (:meth:`Lineage.forward_state`), so a qubit is compared
    with itself, never with the qubit that holds its old name. Sides whose
    eras are not one line (two different renames of one source) are returned
    as recorded; so is every side when none carries a record. A ``wiring``
    that is empty or None means the state is a merged document (its wiring
    inside it)."""
    recs, eras = [], []
    for state, _wiring in pairs:
        chain = records(state)
        recs += chain
        eras.append(tuple(r["id"] for r in chain))
    if not any(eras):
        return list(pairs)
    target = max(eras, key=len)
    if any(target[:len(e)] != e for e in eras):
        return list(pairs)
    lin = Lineage(recs)
    out = []
    for (state, wiring), e in zip(pairs, eras):
        got = None if e == target else lin.forward_state(state, wiring or None, target)
        out.append((got[0], got[1] or {}) if got is not None else (state, wiring))
    return out


_LABEL_RE = __import__("re").compile(r"_(?:removed|stale|source)_*$")


def is_label(name: Any) -> bool:
    """Whether *name* is one of the rebuild's own labels (``q0_removed``,
    ``q1_stale``, ``q1-q2_source``): a qubit or pair the chip no longer has --
    never a name a surface offers as one of today's."""
    return isinstance(name, str) and bool(_LABEL_RE.search(name))
