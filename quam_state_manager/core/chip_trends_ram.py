"""Shared family search for ledger Trends."""
from __future__ import annotations

import re
import threading
from collections import OrderedDict
from typing import Any, Iterable

from quam_state_manager.core.loader import natural_key

# S10 C7: old -> new, only the shared ledger family index has callers.
__all__ = ["FamilyTable"]

_ASCII_LOWER = {c: c + 32 for c in range(ord("A"), ord("Z") + 1)}


def _alower(s: str) -> str:
    """SQLite's LIKE folds case for ASCII letters only. For an ASCII string
    that is exactly ``str.lower()`` (the C fast path); only a string with a
    non-ASCII character needs the letter-by-letter table."""
    return s.lower() if s.isascii() else s.translate(_ASCII_LOWER)


def _like_regex(pattern: str, escape: str | None) -> "re.Pattern[str]":
    """A SQLite ``LIKE`` pattern as a regex: ``%`` any run, ``_`` one char,
    the escape char makes the next one literal, ASCII-only case folding."""
    out = []
    i = 0
    while i < len(pattern):
        ch = pattern[i]
        if escape is not None and ch == escape:
            i += 1
            if i >= len(pattern):
                # SQLite: an escape char with nothing after it matches nothing
                return re.compile(r"(?!)")
            out.append(re.escape(pattern[i]))
        elif ch == "%":
            out.append(".*")
        elif ch == "_":
            out.append(".")
        else:
            out.append(re.escape(ch))
        i += 1
    return re.compile("".join(out), re.IGNORECASE | re.ASCII | re.DOTALL)


class _Term:
    """One search term as ``path LIKE '%<escaped term>%' ESCAPE '\\'``."""

    __slots__ = ("sub", "rx")

    def __init__(self, term: str):
        if "\\" in term:
            # The SQL escapes only % and _, so a backslash in the term acts as
            # an escape itself -- match through the exact LIKE semantics.
            pat = "%" + term.replace("%", r"\%").replace("_", r"\_") + "%"
            self.sub = None
            self.rx = _like_regex(pat, "\\")
        else:
            self.sub = _alower(term)
            self.rx = None

    def hit(self, lowered: str, raw: str) -> bool:
        if self.sub is not None:
            return self.sub in lowered
        return self.rx.fullmatch(raw) is not None


class _Family:
    __slots__ = ("scope", "tail", "members", "n", "changes", "blob",
                 "scope_l", "tail_l", "_sk")

    def __init__(self, scope: str, tail: str):
        self.scope = scope
        self.tail = tail
        self.members: list[tuple[str, str, str, int, Any]] = []   # (lower, raw, ent, changes, path id)
        self._sk = None

    def finish(self) -> None:
        m = self.members
        if len(m) == 1:
            self.n, self.changes, self.blob = 1, m[0][3], m[0][0]
        else:
            self.n = len({x[2] for x in m})
            self.changes = sum(x[3] for x in m)
            self.blob = "\n".join(x[0] for x in m)
        self.scope_l = _alower(self.scope)
        self.tail_l = _alower(self.tail)

    @property
    def sort_key(self):
        # natural_key over every tail was the single largest build cost
        # (~40k calls on a new token); only families that reach a result
        # are ever sorted, so compute it on first use.
        if self._sk is None:
            self._sk = natural_key(self.tail)
        return self._sk


class FamilyTable:
    """Every indexed path, split and grouped ONCE, queried in RAM with the
    same semantics as ``leaf_index.path_families`` (the pins compare the two
    row for row, order included)."""

    def __init__(self, rows: Iterable[tuple], roots: tuple[str, ...]):
        """Group path rows with counts and optional lowered paths and ids."""
        self.roots = tuple(roots)
        splitters = []
        for r in self.roots:
            if not re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", str(r or "")):
                raise ValueError(f"not a path root: {r!r}")
            pre = _alower(r.split("_", 1)[0])       # the literal head before any `_` wildcard
            splitters.append((r, _like_regex(f"{r}.%.%", None), len(r) + 1, pre))
        self._splitters = splitters
        fams: dict[tuple[str, str], _Family] = {}
        n_paths = 0
        for row in rows:
            path, changes = row[0], row[1]
            low = row[2] if len(row) > 2 else _alower(path)
            pid = row[3] if len(row) > 3 else None
            n_paths += 1
            scope, ent, tail = self._split(path, low)
            f = fams.get((scope, tail))
            if f is None:
                f = fams[(scope, tail)] = _Family(scope, tail)
            f.members.append((low, path, ent, int(changes or 0), pid))
        for f in fams.values():
            f.finish()
        self._fams = list(fams.values())
        # Parallel lists for the query's first pass: one list comprehension
        # per term over C-level `in` tests (the per-family Python loop cost
        # ~50 ms per keystroke over 22k families).
        self._blobs = [f.blob for f in self._fams]
        self.n_paths = n_paths
        self._qcache: "OrderedDict[tuple, list[dict]]" = OrderedDict()
        self._qlock = threading.Lock()

    def _split(self, path: str, low: str) -> tuple[str, str, str]:
        """``(scope, entity, tail)`` -- the SQL's ``_family_split_sql``."""
        for r, rx, off, pre in self._splitters:
            if not low.startswith(pre) or rx.fullmatch(path) is None:
                continue
            rest = path[off:]
            dot = rest.find(".")
            if dot >= 1:          # SQL: instr(rest, '.') > 1
                return r, rest[:dot], rest[dot + 1:]
        return "", "", path

    # S10 C7: old -> new, ledger family tables are rebuilt; append derivation has no caller.
    def _signature(self) -> tuple:
        return (self.roots, self.n_paths,
                tuple((f.scope, f.tail, f.n, f.changes, f.blob) for f in self._fams))

    def __eq__(self, other: Any) -> bool:
        # Content equality, so the shadow check (SM_RAM_VERIFY) can compare a
        # served table with a cold rebuild -- identity would always differ.
        return isinstance(other, FamilyTable) and other._signature() == self._signature()

    __hash__ = object.__hash__

    def ram_bytes(self) -> int:
        # ~ 4 strings + a tuple per member, the blob, the sorted path list
        return 220 * self.n_paths + 96 * len(self._fams)

    def query(self, query: str = "", *, limit: int | None = None) -> list[dict]:
        key = (query or "", limit)
        with self._qlock:
            hit = self._qcache.get(key)
            if hit is not None:
                self._qcache.move_to_end(key)
                return [dict(r) for r in hit]
        out = self._query(query, limit)
        with self._qlock:
            self._qcache[key] = out
            while len(self._qcache) > 256:
                self._qcache.popitem(last=False)
        return [dict(r) for r in out]

    def _query(self, query: str, limit: int | None) -> list[dict]:
        from quam_state_manager.core.search_query import groups as _sq_groups

        grps = [[_Term(t) for t in g] for g in _sq_groups(query or "")]
        if (query or "").strip() and not grps:
            return []
        fams = self._fams
        if grps:
            # First pass: the families whose members could match at all --
            # for each group, the union over its terms of "the term occurs
            # somewhere in this family's member paths"; AND over groups. A
            # backslash term (LIKE-escape semantics) cannot be prefiltered by
            # a plain substring and keeps every family.
            cand: set[int] | None = None
            blobs = self._blobs
            for g in grps:
                hit: set[int] = set()
                for t in g:
                    if t.sub is None:
                        hit = set(range(len(fams)))
                        break
                    sub = t.sub
                    hit.update(i for i, b in enumerate(blobs) if sub in b)
                cand = hit if cand is None else (cand & hit)
                if not cand:
                    return []
            order = sorted(cand)
        else:
            order = range(len(fams))
        out = []
        for i in order:
            f = fams[i]
            if grps:
                whole = all(any(t.sub is not None and (t.sub in f.tail_l or t.sub in f.scope_l)
                                for t in g) for g in grps)
                if whole:
                    n, changes = f.n, f.changes     # every member contains every group
                else:
                    ents: set[str] = set()
                    changes = 0
                    hit_any = False
                    for low, raw, ent, ch, _pid in f.members:
                        if all(any(t.hit(low, raw) for t in g) for g in grps):
                            hit_any = True
                            ents.add(ent)
                            changes += ch
                    if not hit_any:
                        continue
                    n = len(ents)
            else:
                n, changes = f.n, f.changes
            out.append((f, n, changes))
        # Ties (equal change count AND equal natural key, e.g. one tail under
        # two scopes) fall back to (scope, tail) -- the SQL's GROUP BY order,
        # which its stable sort keeps -- so the answer never depends on the
        # input row order.
        out.sort(key=lambda t: (-t[2], t[0].sort_key, t[0].scope, t[0].tail))
        if limit:
            out = out[:int(limit)]
        return [{"path": (f"{f.scope}.*.{f.tail}" if f.scope else f.tail),
                 "label": f.tail, "scope": f.scope, "n": int(n),
                 "changes": int(changes)} for f, n, changes in out]
