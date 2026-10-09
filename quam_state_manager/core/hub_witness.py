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
state is the live content; the ledger's first state is not a change. A
re-measurement chain that ends by saving back exactly the value it started
from stays ``remeasured``: on the same backups it was never-live for 302 of
344 changes (88 %), too weak to leave values out.

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


def target_tokens(text: Any) -> frozenset:
    """Every entity a run's ``targets`` names (``events.targets`` JSON:
    ``{"qubits": [...]}``, ``{"qubit_pairs": ["qB3-qA4"]}``): each item, and
    each part of a pair id -- a pair target covers both of its qubits."""
    try:
        raw = json.loads(text) if isinstance(text, str) and text else text
    except ValueError:
        raw = None
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
    judged change, and the newest change of each path."""

    def __init__(self, n_events: int):
        self.n_events = n_events
        #: pid -> {eid: code}
        self.by_pid: dict[int, dict[int, str]] = {}
        #: (eid, pid) -> the witness event's eid (confirmed / contradicted / differs)
        self.witness: dict[tuple, int] = {}
        #: pid -> {eid of a contradicted change: eid of its restoring row}
        self.pairs: dict[int, dict[int, int]] = {}
        #: pid -> (eid, code) of its newest row (code None: not a run change)
        self.newest: dict[int, tuple] = {}
        #: the entity each judged path belongs to (pid -> entity or None)
        self.entity: dict[int, str | None] = {}
        self._drop: dict[int, frozenset] = {}
        self._back: dict[int, dict] = {}
        self._sig: dict[int, str] = {}

    # Mapping
    def __getitem__(self, key):
        eid, pid = key
        return self.by_pid[pid][eid]

    def __iter__(self) -> Iterator:
        for pid, codes in self.by_pid.items():
            for eid in codes:
                yield (eid, pid)

    def __len__(self) -> int:
        return sum(len(c) for c in self.by_pid.values())

    def code(self, eid: int, pid: int | None) -> str | None:
        """The verdict of run change (eid, pid); None when it is not one."""
        codes = self.by_pid.get(pid) if pid is not None else None
        return codes.get(eid) if codes else None

    def restores(self, eid: int, pid: int | None) -> int | None:
        """The contradicted change whose value row (eid, pid) put back (its
        witness's own row), or None."""
        back = self._back.get(pid) if pid is not None else None
        if back is None and pid is not None:
            back = self._back[pid] = {w: c for c, w in (self.pairs.get(pid) or {}).items()}
        return back.get(eid) if back else None

    def drop(self, pid: int | None) -> frozenset:
        """The eids of *pid*'s rows a value series leaves out: every
        contradicted change that has its restoring row, and that row."""
        if pid is None:
            return frozenset()
        got = self._drop.get(pid)
        if got is None:
            pairs = self.pairs.get(pid) or {}
            got = self._drop[pid] = frozenset(pairs) | frozenset(pairs.values())
        return got

    def signature(self, pid: int | None) -> str:
        """What a series of *pid* derived from the verdicts depends on (the
        pairs it leaves out and their witnesses): a kept answer is valid while
        this is unchanged (``value_history._Reuse``)."""
        if pid is None:
            return ""
        got = self._sig.get(pid)
        if got is None:
            pairs = sorted((self.pairs.get(pid) or {}).items())
            got = self._sig[pid] = (hashlib.sha1(repr(pairs).encode()).hexdigest()[:16]
                                    if pairs else "")
        return got

    def tail(self, pid: int | None) -> tuple | None:
        """``(eid, code)`` of *pid*'s newest row, or None."""
        return self.newest.get(pid) if pid is not None else None


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


def _rows_by_pid(conn, index) -> dict[int, list]:
    """``{pid: [(position, eid, old token, new token, proven), ...]}`` in the
    index's order: every stored row of an event in the index, a seam's own
    rows in place of its stored ones (``hub_lanes``)."""
    lane = getattr(index, "lane", None)
    seams = lane.seams if lane is not None else {}
    pos = index.positions
    out: dict[int, list] = {}
    for pid, eid, op, num, txt, onum, otxt, proven in conn.execute(
            "SELECT pid, eid, op, num, txt, old_num, old_txt, proven FROM changes"):
        i = pos.get(eid)
        if i is None or eid in seams:
            continue
        out.setdefault(pid, []).append((i, eid, _raw(op, onum, otxt, "old"), _raw(op, num, txt, "new"),
                                        bool(proven)))
    for eid, rows in seams.items():
        i = pos.get(eid)
        if i is None:
            continue
        for pid, r in rows.items():
            op = r["op"]
            out.setdefault(pid, []).append(
                (i, eid, _raw(op, r["old_num"], r["old_txt"], "old"), _raw(op, r["num"], r["txt"], "new"),
                 bool(r.get("proven"))))
    for seq in out.values():
        seq.sort()
    return out


def verdicts(index, conn, *, witnesses: frozenset = ALL_WITNESSES,
             refined: bool = True) -> Verdicts:
    """The witness verdict of every run change of *index* (see the module
    docstring). *witnesses*: which event classes may read the chip (all by
    default). *refined* (default): a walk passes a re-measurement that was
    itself contradicted, and a change the run's own node.json patch proves is
    ``confirmed`` by that patch (the node wrote it to the chip; spec P0-1 section 1:
    every proven change was live). The replay harness scores the spec's section 2
    rule with runs alone and ``refined=False``."""
    n = len(index.eids)
    out = Verdicts(n)
    facts = _event_facts(conn, index)
    kinds = [None] * n          # event class per position
    judged = bytearray(n)       # a run change at this position is judged
    capable = bytearray(n)      # this event can be a witness
    targets: list = [None] * n  # a run's target tokens
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
        if cls in witnesses and not uncertain:
            capable[i] = 1
    every = [i for i in range(n) if capable[i]]
    lanes: dict[Any, array] = {}

    def lane_of(entity) -> array:
        """The positions that can witness a change of *entity*'s values:
        every capable event but a run that targets it."""
        got = lanes.get(entity)
        if got is None:
            if entity is None:
                got = array("I", every)
            else:
                got = array("I", (i for i in every
                                  if not (kinds[i] == RUN and entity in targets[i])))
            lanes[entity] = got
        return got

    entities = {pid: ent for pid, ent in conn.execute("SELECT pid, entity FROM paths")}
    for pid, seq in _rows_by_pid(conn, index).items():
        last_i, last_eid = seq[-1][0], seq[-1][1]
        if not any(judged[r[0]] for r in seq):
            out.newest[pid] = (last_eid, None)
            continue
        if pid in entities:
            ent = entities[pid]
        else:
            # a lane's seam path the ledger has no row for (negative pid)
            lane = getattr(index, "lane", None)
            path = next((r["path"] for rows in (lane.seams.values() if lane else ())
                         for p, r in rows.items() if p == pid), "")
            ent = entity_of(path)
        out.entity[pid] = ent
        wit = lane_of(ent)
        codes = out.by_pid.setdefault(pid, {})
        pairs = out.pairs.setdefault(pid, {})
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
            code = None
            wrow = None
            last = k                     # the newest row at or before the witness
            for kk in range(k + 1, len(seq)):
                i2 = seq[kk][0]
                if w is not None and i2 > w:
                    break
                if w is not None and i2 == w:
                    wrow = kk
                    break
                if kinds[i2] == RUN and ent is not None and targets[i2] is not None and ent in targets[i2]:
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
                    out.witness[(eid, pid)] = index.eids[w]
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
        if not pairs:
            del out.pairs[pid]
        out.newest[pid] = (last_eid, codes.get(last_eid) if judged[last_i] else None)
    return out


def of(conn, index) -> Verdicts:
    """THE verdicts every reader uses: built once per index (a folder view's
    lane or the ledger's own) and kept on it -- dropped with it when the
    ledger grows (``hub_index.extend_index`` drops every view)."""
    key = (len(index.eids), index.eids[-1] if index.eids else 0)
    got = index.__dict__.get("_witness")
    if got is None or got[0] != key:
        got = (key, verdicts(index, conn))
        index.__dict__["_witness"] = got
    return got[1]


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
