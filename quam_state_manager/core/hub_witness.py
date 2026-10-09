"""P0-1: did a run's saved value ever reach the chip? (the witness rule)

A run saves its OWN fit result in its folder; whether that value reached the
live chip is a separate fact (a lab may not accept it). The ledger records
every difference between consecutive saved states as a change, so a value the
lab did not keep shows up twice: run R ``old -> new`` and the next run R'
``new -> old`` -- R' often a run that never measured that qubit.

**The witness** W of a run change (R, p, ``old -> new``) [derived]: walking
forward from R in the index's order (a folder view's lane order), the first
event that READ THE CHIP without re-measuring p's entity --

* a run whose targets do not include p's entity (a run starts from the live
  chip, so its saved value of p is the live value);
* an ``observed`` event (SM's own capture of the live files);
* an SM write that landed (its state is the live content it wrote over plus
  its own entries).

A run that targets p's entity AND changes p first ends the walk with no
witness (a re-measurement: its value is the next claim) -- unless that
re-measurement is itself contradicted (its value never reached the chip, so
it did not replace R's): the walk passes it and its restoring row, and that
row is R's witness [derived; measured on the same backups: 455 of 458 such
changes were live]. A run that targets the entity without changing p is
passed over. A pair target covers the pair id and both of its qubits. A
change the run's own node.json patch proves (``proven``) is confirmed by that
patch, never walked: the node wrote it to the chip (spec section 1: every one of
the 297 proven changes was live). These two refinements are the default
(``refined``); the spec's section 2 rule is ``refined=False``.

Verdicts (``verdicts(index, conn)``, keyed ``(eid, pid)``):

* ``confirmed``   -- W carries ``new`` at p;
* ``contradicted`` -- W carries ``old`` at p: the value was never on the chip
  (or was undone before anything read it). W's own row ``new -> old`` is its
  RESTORING row (:meth:`Verdicts.restores`); the pair is left out of every
  value series (the value simply stays ``old`` across them -- exact, since W
  carries old). The restoring row keeps its own verdict in the mapping;
* ``open``        -- the newest change of p with no witness yet (a reader with
  the chip's value now decides it: :func:`live_tail`);
* ``remeasured``  -- the walk ended at a run that measured the entity again
  and changed p before anything read the chip;
* ``differs``     -- W carries a third value (neither ``new`` nor ``old``).

Measured on a real chip's ledger against the lab's own timestamped live
backups (17,270 judgeable run changes): ``confirmed`` 98.8 % precision,
``contradicted`` 96.1 % (spec P0-1 section 2; the replay harness calls THIS
function). Only run changes are judged: an SM write is exact and an observed
state is the live content; the ledger's first state is not a change.

**Excursions** (:func:`excursions`, [derived]): a stretch of unconfirmed
saves after a value the chip held that comes back EXACTLY to it is not chip
history either -- on the same backups 400 of 434 such saves (92 %) were never
live, while 1,208 of 1,212 returns were. Its saves and its return leave every
value series with the contradicted pairs (:meth:`Verdicts.drop`), so every
"current", "since" and "last changed" is :func:`since`.

**Renames** (docs/295-296): a value is read only in the rename era it was
saved in -- a walk ends at the next era, and no excursion spans one.

One implementation: every reader (``hub_query``'s series and timeline,
``value_history``, ``hub_status``) asks :func:`of`; nothing else implements
the rule. Built once per index (a folder view's lane, or the ledger's own) and
kept on it, so it is dropped with the index's zone / folder views when the
ledger grows. Nothing here writes.
"""

from __future__ import annotations

import hashlib
import json
from array import array
from bisect import bisect_right
from collections.abc import Mapping
from typing import Any, Iterator

from quam_state_manager.core.hub_store import CHIP_UNCERTAIN, SM_KINDS, segments

CONFIRMED = "confirmed"
CONTRADICTED = "contradicted"
OPEN = "open"
REMEASURED = "remeasured"
DIFFERS = "differs"

#: every verdict a judged run change can have
VERDICTS = (CONFIRMED, CONTRADICTED, OPEN, REMEASURED, DIFFERS)
#: the verdicts that leave a run's saved value unconfirmed on the chip
UNCONFIRMED = frozenset((OPEN, REMEASURED, CONTRADICTED, DIFFERS))

#: what every surface says beside a change whose value is not confirmed on
#: the chip: ``{verdict: (short, sentence)}`` (one wording: the drawer, the
#: Calibration log, Param History Changes, the agent)
DOUBT_TEXT = {
    OPEN: ("no later run has read the chip yet",
           "No later run has read the chip yet, so whether the chip kept this value is not known."),
    REMEASURED: ("changed again before the chip was read",
                 "A later run that may have measured it saved another value before anything read "
                 "the chip, so whether the chip ever held this value is not known."),
    DIFFERS: ("the next read of the chip found another value",
              "The next event that read the chip (a run that did not measure it, a state SM saw, or "
              "an SM write) found another value there, so whether the chip kept this value is not "
              "known."),
    CONTRADICTED: ("not kept on the chip",
                   "The next event that read the chip still found the earlier value: this value was "
                   "saved in the run's own folder and never reached the chip."),
}

#: the event classes that read the chip (the ``witnesses`` argument)
RUN, OBSERVED, SM = "run", "observed", "sm"
ALL_WITNESSES = frozenset((RUN, OBSERVED, SM))

_ABSENT = ("\x00absent",)


def _class_of(kind: str | None) -> str | None:
    if kind == "run":
        return RUN
    if kind == OBSERVED:
        return OBSERVED
    if kind in SM_KINDS:
        return SM
    return None


class _Every:
    """The targets of a run that names none (``{"qubits": null}``, nothing,
    an empty list): it may have measured every entity."""

    def __contains__(self, _entity) -> bool:
        return True

    def __repr__(self) -> str:
        return "EVERY"


#: :func:`target_tokens` of a run whose targets are unknown
EVERY = _Every()

#: the target keys that name entities (``hub_build.read_node`` keeps these)
_ENTITY_KEYS = ("qubits", "qubit_pairs", "pairs")


def target_tokens(text: Any):
    """Every entity a run's ``targets`` names (``events.targets`` JSON:
    ``{"qubits": [...]}``, ``{"qubit_pairs": ["qB3-qA4"]}``): each item, and
    each part of a pair id -- a pair target covers both of its qubits.
    :data:`EVERY` when the run names no entity (``{"qubits": null}``, no
    targets, empty lists): such a run may have measured every one."""
    try:
        raw = json.loads(text) if isinstance(text, str) and text else text
    except ValueError:
        raw = None
    named = (any(isinstance(v, list) and any(x is not None for x in v) or isinstance(v, str) and v
                 for k, v in raw.items() if k in _ENTITY_KEYS) if isinstance(raw, dict)
             else isinstance(raw, list) and any(x is not None for x in raw))
    if not named:
        return EVERY
    items: list = []
    if isinstance(raw, dict):
        for v in raw.values():
            items.extend(v if isinstance(v, list) else [v])
    elif isinstance(raw, list):
        items = list(raw)
    out = set()
    for x in items:
        s = str(x)
        out.add(s)
        out.update(s.split("-"))
    return frozenset(out)


def target_list(text: Any) -> list[str]:
    """The ids a run's ``targets`` names, as recorded (qubits, then pairs)."""
    try:
        raw = json.loads(text) if isinstance(text, str) and text else text
    except ValueError:
        return []
    if not isinstance(raw, dict):
        return [str(x) for x in raw] if isinstance(raw, list) else []
    out: list[str] = []
    for k in ("qubits", "qubit_pairs", "pairs"):
        v = raw.get(k)
        for x in (v if isinstance(v, list) else [v] if v is not None else []):
            if str(x) not in out:
                out.append(str(x))
    return out


def entity_of(path: str) -> str | None:
    """The entity a holder path belongs to (``paths.entity``'s rule)."""
    parts = segments(path)
    return parts[1] if len(parts) > 1 and parts[0] in ("qubits", "qubit_pairs") else None


def _raw(op: int, num, txt, side: str):
    """One side of a stored row as a comparable token (``_ABSENT`` when the
    op says that side does not exist). Stored rows are canonical (a number in
    ``num``, everything else as compact sorted JSON text), so equal tokens are
    equal values under ``hub_rules.same``."""
    if side == "old" and op == 1:          # add
        return _ABSENT
    if side == "new" and op == 2:          # gone
        return _ABSENT
    return (num, txt)


class Verdicts(Mapping):
    """``{(eid, pid): code}`` for the run changes of one index, plus what the
    readers need: the pairs to leave out of a series, the witness of each
    judged change and the newest change of each holder.

    Built per holder on first use (a value drawer asks about one holder; a
    full build reads every row of the ledger): the events' facts are read
    once, each holder's rows when it is first asked about, or every holder at
    once by :meth:`prime` (a day of the Calibration log, the calibration age,
    the Mapping interface). The answer for a holder is the same either way.
    Every access happens inside the read snapshot of the index it was built
    for (``of`` hands it that snapshot's connection)."""

    def __init__(self, index, conn, witnesses: frozenset, refined: bool, unknown_targets: str = "every"):
        self.index, self.conn = index, conn
        self.refined = refined
        n = len(index.eids)
        self.n_events = n
        facts = _event_facts(conn, index)
        self._kinds = kinds = [None] * n          # event class per position
        self._judged = judged = bytearray(n)      # a run change at this position is judged
        capable = bytearray(n)                    # this event can be a witness
        self._targets = targets = [None] * n      # a run's target tokens
        for i, eid in enumerate(index.eids):
            f = facts.get(eid)
            if f is None:
                continue
            kind, has, text = f
            cls = _class_of(kind)
            kinds[i] = cls
            if not has:
                continue
            uncertain = cls == RUN and bool(int(index.flags[i]) & CHIP_UNCERTAIN)
            if cls == RUN and not uncertain:
                judged[i] = 1
                targets[i] = target_tokens(text)
                if targets[i] is EVERY and unknown_targets != "every":
                    targets[i] = frozenset()      # the spec section 2 reading: it names nothing
            if cls in witnesses and not uncertain and targets[i] is not EVERY:
                capable[i] = 1                     # a run that may have measured everything reads nothing
        # the first state is the starting state, not a change a run claims: it
        # is never judged (and anchors what follows it)
        first = next((i for i, eid in enumerate(index.eids) if (facts.get(eid) or (None, False))[1]), None)
        if first is not None:
            judged[first] = 0
        # docs/295-296: a value is read only in the rename era it was saved in.
        # Across a Re-generate rename a holder and a run's targets are spelled
        # in other names (a qubit renamed q2 -> q1 is "gone" from q2 and "new"
        # in q1 at the event where the rename came into force), so a walk ends
        # at the next era (no witness there) and no excursion spans one.
        # ``_era_end[i]``: the first position after *i* in another era.
        self._era_end = None
        from quam_state_manager.core.hub_eras import EraTimeline
        timeline = EraTimeline(conn, index)
        if timeline.any:
            eras = [timeline.at(i) for i in range(n)]
            end = [n] * n
            for i in range(n - 2, -1, -1):
                end[i] = i + 1 if eras[i + 1] != eras[i] else end[i + 1]
            self._era_end = end
        self._every = [i for i in range(n) if capable[i]]
        self._lanes: dict[Any, array] = {}
        self._entities: dict[int, str | None] = {}
        self._all = False
        self._done: set[int] = set()
        #: pid -> {eid: code}
        self._codes: dict[int, dict[int, str]] = {}
        #: (eid, pid) -> the witness event's eid (confirmed / contradicted / differs)
        self._witness: dict[tuple, int] = {}
        #: pid -> {eid of a contradicted change: eid of its restoring row}
        self._pairs: dict[int, dict[int, int]] = {}
        #: pid -> (eid, code) of its newest row (code None: not a run change)
        self._newest: dict[int, tuple] = {}
        #: the entity each judged holder belongs to
        self._entity: dict[int, str | None] = {}
        #: pid -> [(anchor eid, [eids of the excursion], eid of the return)]
        self._excursions: dict[int, list] = {}
        self._drop: dict[int, frozenset] = {}
        self._back: dict[int, dict] = {}
        self._sig: dict[int, str] = {}

    # ------------------------------------------------------------- reading
    def prime(self, pids=None) -> "Verdicts":
        """Judge every holder (*pids* None: one read of every row) or the
        given ones (one read of theirs) not judged yet."""
        if self._all:
            return self
        if pids is None:
            got = _rows(self.conn, self.index, None)
            if not self._entities:
                self._entities = dict(self.conn.execute("SELECT pid, entity FROM paths"))
            for pid, seq in got.items():
                if pid not in self._done:
                    self._judge(pid, seq)
            self._done.update(got)
            self._all = True
            return self
        want = [p for p in set(pids) if p is not None and p not in self._done]
        if len(want) > _PRIME_ALL_FROM:
            return self.prime()             # one read of every row is cheaper
        if want:
            got = _rows(self.conn, self.index, want)
            for pid in want:
                self._judge(pid, got.get(pid) or [])
            self._done.update(want)
        return self

    def _ensure(self, pid) -> bool:
        if pid is None:
            return False
        if pid not in self._done and not self._all:
            self.prime((pid,))
        return True

    def _entity_of(self, pid: int) -> str | None:
        if pid in self._entities:
            return self._entities[pid]
        row = self.conn.execute("SELECT entity FROM paths WHERE pid=?", (pid,)).fetchone() if pid >= 0 else None
        if row is not None:
            ent = row[0]
        else:
            # a lane's seam path the ledger has no row for (negative pid)
            lane = getattr(self.index, "lane", None)
            path = next((r["path"] for rows in (lane.seams.values() if lane else ())
                         for p, r in rows.items() if p == pid), "")
            ent = entity_of(path)
        self._entities[pid] = ent
        return ent

    def _lane_of(self, entity) -> array:
        """The positions that can witness a change of *entity*'s values:
        every capable event but a run that targets it."""
        got = self._lanes.get(entity)
        if got is None:
            kinds, targets = self._kinds, self._targets
            if entity is None:
                got = array("I", self._every)
            else:
                got = array("I", (i for i in self._every
                                  if not (kinds[i] == RUN and entity in targets[i])))
            self._lanes[entity] = got
        return got

    def _judge(self, pid: int, seq: list) -> None:
        """THE rule for one holder's rows (oldest first)."""
        if not seq:
            return
        index, judged, kinds, targets = self.index, self._judged, self._kinds, self._targets
        refined = self.refined
        last_i, last_eid = seq[-1][0], seq[-1][1]
        if not any(judged[r[0]] for r in seq):
            self._newest[pid] = (last_eid, None)
            return
        ent = self._entity[pid] = self._entity_of(pid)
        wit = self._lane_of(ent)
        codes = self._codes.setdefault(pid, {})
        pairs: dict[int, int] = {}
        # newest first: a re-measurement's own verdict is known before the
        # walk of an earlier change reaches it
        for k in range(len(seq) - 1, -1, -1):
            i, eid, old, new, proven = seq[k]
            if not judged[i]:
                continue
            if refined and proven:
                codes[eid] = CONFIRMED        # its own patch put it on the chip
                continue
            j = bisect_right(wit, i)
            w = wit[j] if j < len(wit) else None
            stop = self._era_end[i] if self._era_end is not None else None
            if stop is not None and w is not None and w >= stop:
                w = None                 # the next read is in another era: none in this one
            code = None
            wrow = None
            last = k                     # the newest row at or before the witness
            for kk in range(k + 1, len(seq)):
                i2 = seq[kk][0]
                if stop is not None and i2 >= stop:
                    break                # the era ended before anything read the chip
                if w is not None and i2 > w:
                    break
                if w is not None and i2 == w:
                    wrow = kk
                    break
                t2 = targets[i2]
                if kinds[i2] == RUN and t2 is not None and (t2 is EVERY or (ent is not None and ent in t2)):
                    if not (refined and seq[kk][1] in pairs):
                        code = REMEASURED    # measured again before anything read the chip
                        break
                    # a re-measurement the chip never kept did not replace R's value
                last = kk                # a row passed on the way to the witness
            if code is None:
                if w is None:
                    code = OPEN
                else:
                    held = seq[wrow][3] if wrow is not None else seq[last][3]
                    self._witness[(eid, pid)] = index.eids[w]
                    if held == new:
                        code = CONFIRMED
                    elif held == old:
                        code = CONTRADICTED
                        if wrow is not None and wrow == k + 1 and seq[wrow][2] == new:
                            # W's own row is new -> old right after R: the pair
                            # leaves the series together, the fold stays exact
                            pairs[eid] = seq[wrow][1]
                    else:
                        code = DIFFERS
            codes[eid] = code
        if pairs:
            self._pairs[pid] = pairs
        # excursions of the series the pairs leave: unconfirmed saves that came
        # back EXACTLY to the value the chip held before them
        gone = set(pairs) | set(pairs.values())
        kept = [r for r in seq if r[1] not in gone]
        groups = [kept]
        if self._era_end is not None:
            groups, cur, until = [], [], -1
            for r in kept:                   # era by era: no excursion spans a rename
                if cur and r[0] >= until:
                    groups.append(cur)
                    cur = []
                if not cur:
                    until = self._era_end[r[0]]
                cur.append(r)
            if cur:
                groups.append(cur)
        found = []
        for g in groups:
            for a, pts, r in excursions([(x[3], codes.get(x[1]) if judged[x[0]] else None) for x in g], _equal):
                found.append((g[a][1], [g[x][1] for x in pts], g[r][1]))
        if found:
            self._excursions[pid] = found
        self._newest[pid] = (last_eid, codes.get(last_eid) if judged[last_i] else None)

    # ------------------------------------------------------------- Mapping
    def __getitem__(self, key):
        eid, pid = key
        self._ensure(pid)
        return self._codes[pid][eid]

    def __iter__(self) -> Iterator:
        self.prime()
        for pid, codes in self._codes.items():
            for eid in codes:
                yield (eid, pid)

    def __len__(self) -> int:
        self.prime()
        return sum(len(c) for c in self._codes.values())

    # ------------------------------------------------------------- answers
    def code(self, eid: int, pid: int | None) -> str | None:
        """The verdict of run change (eid, pid); None when it is not one."""
        if not self._ensure(pid):
            return None
        codes = self._codes.get(pid)
        return codes.get(eid) if codes else None

    def witness_of(self, eid: int, pid: int | None) -> int | None:
        """The eid of the event that read the chip after run change (eid, pid)."""
        return self._witness.get((eid, pid)) if self._ensure(pid) else None

    def pairs_of(self, pid: int | None) -> dict:
        """``{eid of a contradicted change: eid of its restoring row}`` of *pid*."""
        return (self._pairs.get(pid) or {}) if self._ensure(pid) else {}

    def entity(self, pid: int | None) -> str | None:
        """The entity a judged holder belongs to (None: not judged, or no entity)."""
        return self._entity.get(pid) if self._ensure(pid) else None

    def restores(self, eid: int, pid: int | None) -> int | None:
        """The contradicted change whose value row (eid, pid) put back (its
        witness's own row), or None."""
        if not self._ensure(pid):
            return None
        back = self._back.get(pid)
        if back is None:
            back = self._back[pid] = {w: c for c, w in (self._pairs.get(pid) or {}).items()}
        return back.get(eid)

    def excursions_of(self, pid: int | None) -> list:
        """``[(anchor eid, [eids of its points], eid of the return)]`` of
        *pid*: every excursion (:func:`excursions`) of its series."""
        return (self._excursions.get(pid) or []) if self._ensure(pid) else []

    def drop(self, pid: int | None) -> frozenset:
        """The eids of *pid*'s rows a value series leaves out -- they are not
        chip history: every contradicted change that has its restoring row,
        and that row; every point of an excursion, and its return."""
        if not self._ensure(pid):
            return frozenset()
        got = self._drop.get(pid)
        if got is None:
            pairs = self._pairs.get(pid) or {}
            out = set(pairs) | set(pairs.values())
            for _a, pts, r in self._excursions.get(pid) or ():
                out.update(pts)
                out.add(r)
            got = self._drop[pid] = frozenset(out)
        return got

    def left_out_as(self, eid: int, pid: int | None) -> str | None:
        """Why row (eid, pid) leaves the series: ``"contradicted"`` (saved,
        and the next read of the chip still found the old value),
        ``"restores"`` (that read's own row), ``"excursion"`` (saved, never
        confirmed, and the value came back), ``"returns"`` (the row that came
        back) -- or None (it stays)."""
        if eid not in self.drop(pid):
            return None
        pairs = self._pairs.get(pid) or {}
        if eid in pairs:
            return "contradicted"
        if self.restores(eid, pid) is not None:
            return "restores"
        for _a, pts, r in self._excursions.get(pid) or ():
            if eid in pts:
                return "excursion"
            if eid == r:
                return "returns"
        return None

    def signature(self, pid: int | None) -> str:
        """What a series of *pid* derived from the verdicts depends on (the
        pairs it leaves out and their witnesses): a kept answer is valid while
        this is unchanged (``value_history._Reuse``)."""
        if not self._ensure(pid):
            return ""
        got = self._sig.get(pid)
        if got is None:
            pairs = sorted((self._pairs.get(pid) or {}).items())
            exc = self._excursions.get(pid) or []
            got = self._sig[pid] = (hashlib.sha1(repr((pairs, exc)).encode()).hexdigest()[:16]
                                    if pairs or exc else "")
        return got

    def tail(self, pid: int | None) -> tuple | None:
        """``(eid, code)`` of *pid*'s newest row, or None."""
        return self._newest.get(pid) if self._ensure(pid) else None


def _event_facts(conn, index) -> dict[int, tuple]:
    """``{eid: (kind, has a state, targets text)}`` for the index's events."""
    from quam_state_manager.core.hub_lanes import _has_state
    out = {}
    wanted = index.positions
    for eid, kind, error, status, state_hash, targets in conn.execute(
            "SELECT eid, kind, error, status, state_hash, targets FROM events"):
        if eid not in wanted:
            continue
        has = _has_state({"error": error, "kind": kind, "status": status, "state_hash": state_hash})
        out[eid] = (kind, has, targets)
    return out


_ROW_SQL = "SELECT pid, eid, op, num, txt, old_num, old_txt, proven FROM changes"
#: past this many holders asked at once, :meth:`Verdicts.prime` reads every row
_PRIME_ALL_FROM = 1000


def _rows(conn, index, pids) -> dict[int, list]:
    """``{pid: [(position, eid, old token, new token, proven), ...]}`` in the
    index's order -- every holder (*pids* None) or the given ones: every
    stored row of an event in the index, a seam's own rows in place of its
    stored ones (``hub_lanes``)."""
    lane = getattr(index, "lane", None)
    seams = lane.seams if lane is not None else {}
    pos = index.positions
    out: dict[int, list] = {}

    def add(found):
        for pid, eid, op, num, txt, onum, otxt, proven in found:
            i = pos.get(eid)
            if i is None or eid in seams:
                continue
            out.setdefault(pid, []).append((i, eid, (_ABSENT if op == 1 else (onum, otxt)),
                                            (_ABSENT if op == 2 else (num, txt)), bool(proven)))
    if pids is None:
        add(conn.execute(_ROW_SQL))
    else:
        stored = [p for p in pids if p >= 0]
        for start in range(0, len(stored), 500):
            chunk = stored[start:start + 500]
            add(conn.execute(_ROW_SQL + " WHERE pid IN (" + ",".join("?" * len(chunk)) + ")", chunk))
    want = None if pids is None else set(pids)
    for eid, rows in seams.items():
        i = pos.get(eid)
        if i is None:
            continue
        for pid, r in rows.items():
            if want is not None and pid not in want:
                continue
            op = r["op"]
            out.setdefault(pid, []).append(
                (i, eid, _raw(op, r["old_num"], r["old_txt"], "old"), _raw(op, r["num"], r["txt"], "new"),
                 bool(r.get("proven"))))
    for seq in out.values():
        seq.sort(key=_first)
    return out


def _first(row):
    return row[0]


def verdicts(index, conn, *, witnesses: frozenset = ALL_WITNESSES,
             refined: bool = True, unknown_targets: str = "every", lazy: bool = False) -> Verdicts:
    """The witness verdict of every run change of *index* (see the module
    docstring). *witnesses*: which event classes may read the chip (all by
    default). *refined* (default): a walk passes a re-measurement that was
    itself contradicted, and a change the run's own node.json patch proves is
    ``confirmed`` by that patch (the node wrote it to the chip; spec P0-1 section 1:
    every proven change was live). The replay harness scores the spec's section 2
    rule with runs alone, ``refined=False`` and ``unknown_targets="none"``.
    *unknown_targets*: a run that names no target may have measured every
    entity (``"every"``, default: never a witness, and a re-measurement of
    whatever it changes) or names nothing (``"none"``: a witness for all).
    *lazy*: judge each holder when it is first asked about (:func:`of`); else
    every holder now."""
    out = Verdicts(index, conn, witnesses, refined, unknown_targets)
    return out if lazy else out.prime()


def of(conn, index) -> Verdicts:
    """THE verdicts every reader uses: one per lane of the ledger (a folder
    view's lane, or the ledger's own events), kept on the zone-free index and
    shared by every zone view of it -- the drawer, Chip Status and the
    Calibration log read in different zones, never in different lanes. Dropped
    with the views when the ledger grows (``hub_index.extend_index``). Each
    holder is judged when first asked about, through this snapshot's
    connection."""
    lane = getattr(index, "lane", None)
    key = (len(index.eids), index.eids[-1] if index.eids else 0,
           lane.digest if lane is not None else None)
    got = index.__dict__.get("_witness")
    if got is None or got[0] != key:
        zone_view = lane.base if lane is not None else index
        root = zone_view.__dict__.get("_base", zone_view)
        shared = root.__dict__.setdefault("_witness_shared", {})
        v = shared.get(key)
        if v is None:
            v = shared[key] = verdicts(index, conn, lazy=True)
        got = (key, v)
        index.__dict__["_witness"] = got
    got[1].conn = conn
    return got[1]


def excursions(seq, same) -> list[tuple[int, list[int], int]]:
    """THE rule for values a run saved that are not chip history [derived].

    *seq*: a value's points, oldest first, as ``(value, verdict)``
    (``verdict`` None: not a judged run change). An EXCURSION is a stretch of
    points that are all unconfirmed (:data:`UNCONFIRMED`: no later read of the
    chip confirmed them) after an ANCHOR -- the newest point before it that is
    not unconfirmed (a confirmed save, a state SM saw, an SM write, the first
    state) -- that comes back EXACTLY (*same*) to the anchor's value. The
    chip held the anchor's value across it: the excursion's points and the
    point that came back leave the value series (the value stays the
    anchor's -- exact), and are listed apart. A stretch that never comes back,
    or a confirmed point inside it, is no excursion: its points stay, labelled.
    Returns ``[(anchor index, [excursion indices], return index)]``.

    Measured against the lab's own live backups (replay harness): 400 of the
    434 points this leaves out were never on the chip (module docstring)."""
    out: list[tuple[int, list[int], int]] = []
    anchor = None
    i, n = 0, len(seq)
    while i < n:
        if seq[i][1] in UNCONFIRMED and anchor is not None:
            v = seq[anchor][0]
            k, found = i + 1, None
            while k < n:
                if same(seq[k][0], v):
                    found = k
                    break
                if seq[k][1] not in UNCONFIRMED:
                    break
                k += 1
            if found is not None:
                out.append((anchor, list(range(i, found)), found))
                i = found + 1
                continue
            i += 1
            continue
        if seq[i][1] not in UNCONFIRMED:
            anchor = i
        i += 1
    return out


def since(seq, same) -> tuple[int, list[int]]:
    """Where the current value's stay on the chip began: the newest point
    that is chip history once :func:`excursions` are left out, and the
    excursion points after it. Every "since", "unchanged since", "last
    changed" and the drawer's "current" row is this point. On a series the
    verdicts already left the excursions out of it is the newest point.
    ``(-1, [])`` for no points."""
    if not seq:
        return -1, []
    gone: set[int] = set()
    skipped: list[int] = []
    for _a, pts, r in excursions(seq, same):
        gone.update(pts)
        gone.add(r)
        skipped.extend(pts)
    kept = [i for i in range(len(seq)) if i not in gone]
    if not kept:
        return -1, sorted(skipped)
    j = kept[-1]
    return j, sorted(i for i in skipped if i > j)


def _equal(a, b) -> bool:
    return a == b


def describe(index, ev: dict | None, eid: int) -> dict:
    """What a surface says about a witness event: its kind, run number,
    node, instant and (a run) its targets -- from the event dict when the
    caller has one, else from the index's own arrays."""
    if ev is not None:
        kind = ev.get("kind")
        out = {"eid": eid, "kind": kind, "run_id": ev.get("run_id"),
               "experiment": ev.get("experiment"), "t_us": ev.get("t_utc_us"),
               "src": ev.get("src"), "actor": ev.get("actor")}
        if kind == "run":
            out["targets"] = target_list(ev.get("targets"))
        return out
    pos = index.positions.get(eid)
    if pos is None:
        return {"eid": eid, "kind": None}
    kinds = {v: k for k, v in index.names["kind"].items()}
    exps = {v: k for k, v in index.names["experiment"].items()}
    rid = index.run_id[pos]
    return {"eid": eid, "kind": kinds.get(index.kind[pos]), "run_id": None if rid < 0 else rid,
            "experiment": exps.get(index.experiment[pos]) or None, "t_us": index.t[pos]}


def live_tail(code: str | None, new: Any, live: Any, same) -> str | None:
    """The newest change of a path with no witness yet (``open``), decided by
    the value the chip holds now: equal to ``new`` -> ``confirmed``, else
    ``contradicted``. Any other code is returned as it is."""
    if code != OPEN:
        return code
    return CONFIRMED if same(new, live) else CONTRADICTED
