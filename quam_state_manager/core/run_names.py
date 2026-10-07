"""A run's qubit and pair names in the loaded chip's names (docs/296).

A run saved before a Re-generate rename names its qubits by the ids they had
then: node.json's ``qubits``, the ``fit_results`` keys, the run's own saved
state. After a shift rename (q1 -> q0, q2 -> q1) that run's "q1" is today's q0.
Every Datasets surface that turns a run's name into a path of the loaded chip
(a write target) or into a series key goes through this module: the run's era
(:func:`value_history.run_era` -- the ledger first, else the run's own saved
state), the chip's lineage (:func:`rename_lineage.lineage_for`) and one
translation (:class:`rename_lineage.Lineage`).

Two rules:

* never guess -- a run whose era cannot be told, or a name with no qubit or
  pair on the loaded chip, translates to ``None`` and a write surface REFUSES
  it with a sentence (:meth:`RunNames.refusal`);
* a chip that was never renamed is inactive here -- every translation is the
  identity, nothing is read, and the surfaces answer exactly as before.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Iterable

from quam_state_manager.core import rename_lineage


def _ids(merged: Any, key: str) -> frozenset:
    block = merged.get(key) if isinstance(merged, dict) else None
    return frozenset(block) if isinstance(block, dict) else frozenset()


class RunNames:
    """One run's names spelled in the loaded chip's names.

    ``src`` is the run's era (None: it cannot be told), ``dst`` the loaded
    chip's. ``qubits_now`` / ``pairs_now`` are the loaded chip's ids: a write
    target is only ever a qubit or pair the chip has."""

    __slots__ = ("lineage", "src", "dst", "qubits_now", "pairs_now")

    def __init__(self, lineage: rename_lineage.Lineage, src: tuple | None, dst: tuple,
                 qubits_now: Iterable[str] = (), pairs_now: Iterable[str] = ()):
        self.lineage = lineage
        self.src = tuple(src) if src is not None else None
        self.dst = tuple(dst)
        self.qubits_now = frozenset(qubits_now)
        self.pairs_now = frozenset(pairs_now)

    @property
    def identity(self) -> bool:
        """The run is spelled in today's names (nothing to translate)."""
        return self.src is not None and self.src == self.dst

    @property
    def known(self) -> bool:
        return self.src is not None

    def value(self, v: Any, path: str = "") -> Any:
        """A stored value of the run in today's spelling (docs/296 review
        P1-4): a pointer, a qubit / pair id or a name holding one follows the
        renames, element by element in a list; below ``extras`` only a pointer
        does. Anything else is returned as it is."""
        if self.identity or self.src is None:
            return v
        free = "extras" in str(path).split(".")
        if isinstance(v, str):
            return self.lineage.value(v, self.src, self.dst, free)
        if isinstance(v, list):
            return [self.value(x, path) for x in v]
        if isinstance(v, dict):
            return {k: self.value(x, path) for k, x in v.items()}
        return v

    # -- one name -------------------------------------------------------------
    def _first_step(self):
        route = self.lineage._route(self.src, self.dst)
        if route is None:
            return None, False
        back, fwd = route
        if back:
            return back[0], True
        return (fwd[0], False) if fwd else (None, False)

    def is_pair(self, name: str) -> bool:
        """Whether *name* is a PAIR id in the run's era: the first rename out
        of that era says which ids it held as pairs (its source pairs going
        forward, its rebuilt pairs going back)."""
        if self.src is None or self.identity:
            return False
        step, back = self._first_step()
        if step is None:
            return False
        if back:
            return name in step.after_p
        return name in step.src_p or name in step.pmap

    def _qubit(self, qid: str) -> str | None:
        """:meth:`Lineage.qubit`, plus the one case a step's maps cannot see:
        an id the rebuild's SOURCE never had (a qubit removed before it) whose
        name the rebuilt chip gives a brand-new qubit. Read through the maps
        alone the two physical qubits would share one name; here the older
        one has no name in the newer era, and the newer one none before."""
        route = self.lineage._route(self.src, self.dst)
        if route is None:
            return None
        back, fwd = route
        q: str | None = qid
        for s in back:
            if q in s.after_q and q not in s.src_q and q not in s.inv:
                return None          # new in that rebuild: no name before it
            q = s.back_id(q)
            if q is None:
                return None
        for s in fwd:
            if q not in s.src_q and q not in s.tok and q in s.after_q:
                return None          # the source never had it: that name is another qubit now
            q = s.fwd_id(q)
        return q

    def entity(self, name: str, *, pair: bool | None = None) -> str | None:
        """*name* (a qubit or pair id of the run) in today's names; None when
        the run's era is unknown or the id names nothing in today's era. The
        answer may be a rebuild label (``q2_removed``): a physical qubit the
        chip no longer has, never one of its qubits."""
        if self.identity:
            return name
        if self.src is None or not isinstance(name, str):
            return None
        if pair is None:
            pair = self.is_pair(name)
        if pair:
            return self.lineage.pair(name, self.src, self.dst)
        return self._qubit(name)

    def current(self, name: str, *, pair: bool | None = None) -> str | None:
        """:meth:`entity`, only when the loaded chip has that qubit or pair."""
        if self.identity:
            return name
        now = self.entity(name, pair=pair)
        if now is None or (now not in self.qubits_now and now not in self.pairs_now):
            return None
        return now

    # -- one dot-path --------------------------------------------------------
    @staticmethod
    def _entity_at(parts: list[str]) -> tuple[str | None, int]:
        """``("qubits" | "qubit_pairs", index of the id)`` in a state path or
        a wiring path (``wiring.qubits.<id>...``); ``(None, -1)`` else."""
        off = 1 if parts and parts[0] == "wiring" else 0
        if len(parts) >= off + 2 and parts[off] in ("qubits", "qubit_pairs"):
            return parts[off], off + 1
        return None, -1

    def entity_of(self, dot_path: str) -> str | None:
        """The qubit or pair id a dot-path names (as it spells it)."""
        parts = dot_path.split(".")
        _kind, i = self._entity_at(parts)
        return parts[i] if i >= 0 else None

    def target(self, dot_path: str) -> str | None:
        """A write target spelled in the run's names, in today's names; None
        when it cannot be translated or names a qubit / pair the loaded chip
        does not have."""
        if self.identity:
            return dot_path
        if self.src is None:
            return None
        now = self.lineage.path(dot_path, self.src, self.dst)
        if now is None:
            return None
        parts, was = now.split("."), dot_path.split(".")
        kind, i = self._entity_at(parts)
        if kind == "qubits" and (parts[i] not in self.qubits_now
                                 or len(was) <= i or self._qubit(was[i]) != parts[i]):
            return None
        if kind == "qubit_pairs" and parts[i] not in self.pairs_now:
            return None
        return now

    # -- what a surface says -------------------------------------------------
    def renames_text(self) -> str:
        """``q1 -> q0, q2 -> q1`` (drawn with an arrow) for the renames between
        the run and today."""
        if self.src is None:
            return ""
        out = []
        for step in self.lineage.label(self.src, self.dst):
            for old, new in step["qubits"].items():
                out.append(f"{old} → {new}")
        return ", ".join(out)

    def refusal(self, name: str | None = None) -> str:
        """The sentence a write surface shows instead of a guessed target."""
        if self.src is None:
            return ("Not applied: the chip's qubits were renamed, and this run's "
                    "saved state does not say which names it used.")
        what = "pair" if name is not None and self.is_pair(name) else "qubit"
        text = self.renames_text()
        head = (f"{name} of this run has no {what} on the loaded chip" if name
                else f"this {what} of the run has no name on the loaded chip")
        return f"Not applied: {head} since the rename" + (f" ({text})." if text else ".")

    def note(self, name: str) -> str | None:
        """``now q0`` beside a run's name that today's chip spells otherwise."""
        if self.identity:
            return None
        now = self.current(name)
        return f"now {now}" if now is not None and now != name else None


class ChipNames:
    """The loaded chip's rename lineage, and where each run's era comes from.

    ``active`` is False for a chip that was never renamed: then every run is
    the identity and :meth:`era_of` reads nothing."""

    def __init__(self, chip_dir, merged: Any, *, binding: Any = None):
        self.chip_dir = Path(chip_dir) if chip_dir else None
        merged = merged if isinstance(merged, dict) else {}
        self.current = rename_lineage.era(merged)
        self.lineage = rename_lineage.lineage_for(self.chip_dir, merged)
        self.qubits_now = _ids(merged, "qubits")
        self.pairs_now = _ids(merged, "qubit_pairs")
        extras = merged.get("extras")
        df = extras.get("data_folder") if isinstance(extras, dict) else None
        self._data_folder = df.strip() if isinstance(df, str) and df.strip() else None
        self._binding = binding
        self._eras: tuple[dict, set | None] | None = None
        self._roots: list[str] | None = None
        self._merged = merged
        self._identity: dict | None = None
        self._uncertain: frozenset = frozenset()

    @property
    def active(self) -> bool:
        return self.lineage.active

    @property
    def version(self) -> tuple | None:
        """What a cache of translated names is valid for (None: inactive)."""
        if not self.active:
            return None
        return (self.current, tuple(sorted(self.lineage.recs)))

    # -- a run's era ---------------------------------------------------------
    def _ledger_eras(self) -> tuple[dict, set | None]:
        if self._eras is None:
            from quam_state_manager.core import value_history as vh
            eras: dict = {}
            known: set | None = None
            self._uncertain = frozenset()
            if self.chip_dir is not None:
                try:
                    eras, held = vh.run_eras_known(self.chip_dir, self._binding)
                    known = set(held)
                    self._uncertain = vh.run_chip_uncertain(self.chip_dir, self._binding)
                except Exception:  # noqa: BLE001 -- no ledger (or building): the files decide
                    eras, known = {}, None
            self._eras = (eras, known)
        return self._eras

    def era_of(self, folder: Any) -> tuple | None:
        """The rename era *folder* (a run folder) was saved in: the ledger's
        answer, else the run's own saved state; None when neither tells."""
        if not self.active:
            return self.current
        from quam_state_manager.core import value_history as vh
        eras, known = self._ledger_eras()
        key = vh.norm_folder(folder)
        if key in eras:
            return eras[key]
        if known is not None and key in known:
            return ()
        return rename_lineage.folder_era(folder)

    def for_era(self, src: tuple | None) -> RunNames:
        return RunNames(self.lineage, src, self.current, self.qubits_now, self.pairs_now)

    def for_run(self, folder: Any) -> RunNames:
        """*folder*'s names in today's -- the identity for a run of ANOTHER
        chip (docs/296 review P1-5): only this chip's runs follow its renames."""
        if not self.active or not self.same_chip(folder):
            return self.for_era(self.current)
        return self.for_era(self.era_of(folder))

    def same_chip(self, folder: Any = None, state: Any = None, wiring: Any = None) -> bool:
        """Whether a run (its folder, or its saved state) is this chip's: one
        of the chip's data folders, else the same chip by the identity ladder
        (``hub_build.identity_disagrees`` over names in the base era -- a
        rename does not make another chip). Unknown is not the same."""
        if not self.active:
            return False
        from quam_state_manager.core import hub_build
        from quam_state_manager.core import value_history as vh
        if folder is not None:
            # the ledger already judged every run it holds (a run of another
            # chip in this chip's data folder is marked chip-uncertain there)
            _eras, known = self._ledger_eras()
            key = vh.norm_folder(folder)
            if known is not None and key in known:
                return key not in self._uncertain
        if state is None and folder is not None:
            pair = _saved_pair(folder)
            if pair is None:
                return bool(self.owns(folder))
            state, wiring = pair
        if not isinstance(state, dict):
            return bool(folder is not None and self.owns(folder))
        here = Path(folder) if folder is not None else Path(".")
        theirs = hub_build._chip_identity(state, wiring if isinstance(wiring, dict) else {}, here)
        if self._identity is None:
            self._identity = hub_build._chip_identity(self._merged, self._merged, Path(".")) or {}
        mine = self._identity
        if not theirs or not mine:
            return False
        return not hub_build.identity_disagrees(mine, theirs)

    def for_state(self, state: Any) -> RunNames:
        return self.for_era(rename_lineage.era(state) if self.active else self.current)

    # -- which data folders are this chip's ----------------------------------
    def _root_list(self) -> list[str]:
        if self._roots is None:
            from quam_state_manager.core import hub_sync
            from quam_state_manager.core import value_history as vh
            roots: set[str] = set()
            if self._data_folder:
                roots.add(vh.norm_folder(self._data_folder))
            if self.chip_dir is not None:
                try:
                    for r in hub_sync.status(self.chip_dir).get("roots") or ():
                        if r.get("path"):
                            roots.add(vh.norm_folder(r["path"]))
                except Exception:  # noqa: BLE001 -- no sync in this window
                    pass
                for r in _ledger_roots(self.chip_dir, self._binding):
                    roots.add(vh.norm_folder(r))
            self._roots = sorted(roots)
        return self._roots

    def owns(self, folder: Any) -> bool:
        """Whether *folder* (a data folder or a run folder) is one of this
        chip's data folders -- runs elsewhere are another chip's, and are
        never translated by this chip's renames."""
        if not self.active:
            return False
        from quam_state_manager.core import value_history as vh
        f = vh.norm_folder(folder)
        for r in self._root_list():
            if f == r or f.startswith(r.rstrip("\\/") + os.sep):
                return True
        return False


class StoreNames:
    """The runs of one of the open chip's data folders in today's names: the
    translation a Trends index is built under (``trend_index.set_names``).
    ``version`` is what a cache over it is valid for."""

    __slots__ = ("chip", "version")

    def __init__(self, chip: ChipNames):
        self.chip = chip
        self.version = chip.version

    def __call__(self, run: Any) -> RunNames:
        return self.chip.for_run(getattr(run, "folder_path", None))


_PAIRS: dict[tuple, tuple] = {}


def _saved_pair(folder: Any) -> tuple[dict, dict] | None:
    """A run folder's saved ``(state, wiring)``, read once per file version."""
    import json
    from quam_state_manager.core import hub_build
    try:
        sp, wp = hub_build.state_paths(Path(folder))
        st = sp.stat()
        key = (os.path.normcase(str(sp)), st.st_mtime_ns, st.st_size)
    except OSError:
        return None
    hit = _PAIRS.get(key)
    if hit is not None:
        return hit
    try:
        state = json.loads(hub_build._read_shared(sp))
        wiring = json.loads(hub_build._read_shared(wp)) if wp.exists() else {}
    except (OSError, ValueError):
        return None
    if len(_PAIRS) > 256:
        _PAIRS.clear()
    _PAIRS[key] = (state, wiring)
    return state, wiring


def run_qubits_now(runs: Iterable[Any], names: StoreNames) -> set[str]:
    """Every qubit id the runs name, in today's names (a name with none is
    left out): the Trends qubit picker of a renamed chip's data folder."""
    out: set[str] = set()
    for r in runs:
        rn = names(r)
        pairs = set(getattr(r, "qubit_pairs", None) or ())
        for q in getattr(r, "qubits", None) or ():
            now = rn.entity(q, pair=True if q in pairs else None)
            if now is not None and not rename_lineage.is_label(now):
                out.add(now)
    return out


def _ledger_roots(chip_dir, binding=None) -> list[str]:
    """The data folders the chip's ledger reads runs from (empty when there
    is no ledger, or it is being built)."""
    if not (Path(chip_dir) / "ledger.sqlite").exists():
        return []
    try:
        from types import SimpleNamespace
        from quam_state_manager.core import hub_index
        reader = binding if binding is not None else SimpleNamespace(directory=Path(chip_dir))
        with hub_index.snapshot(reader) as (conn, _index):
            return [r[0] for r in conn.execute("SELECT path FROM roots") if r[0]]
    except Exception:  # noqa: BLE001 -- the other sources still answer
        return []


def translate_clickable(clickable: Any, names: RunNames | None,
                        entities: Iterable[str] = ()) -> Any:
    """An interactive figure's ``clickable`` block with its targets in today's
    names.

    A literal target path is translated here; a ``{q}`` path is filled by the
    client from ``names`` (``{run name: today's name or null}``, with the
    sentence for each null in ``refusals``). Any target that cannot be
    translated refuses the whole click (a coupled update -- f_01 with
    RF_frequency -- is never staged in part): ``targets`` is empty and
    ``refused`` says why."""
    if not isinstance(clickable, dict) or names is None or names.identity:
        return clickable
    out = dict(clickable)
    q = clickable.get("qubit") if isinstance(clickable.get("qubit"), str) else None
    ents = {e for e in entities if isinstance(e, str)}
    if q:
        ents.add(q)
    out["names"] = {e: names.current(e) for e in sorted(ents)}
    out["refusals"] = {e: names.refusal(e) for e, now in out["names"].items() if now is None}
    targets: list = []
    refused = not names.known or bool(q and out["names"].get(q) is None)
    who = q
    for t in clickable.get("targets") or ():
        if refused:
            break
        path = t.get("path") if isinstance(t, dict) else None
        if not isinstance(path, str):
            continue
        if "{" in path:
            targets.append(t)
            continue
        now = names.target(path)
        if now is None:
            refused = True
            who = names.entity_of(path) or q
            break
        targets.append(dict(t, path=now))
    out["targets"] = [] if refused else targets
    out["refused"] = names.refusal(who) if refused else None
    return out


def names_map(names: RunNames | None, entities: Iterable[str]) -> dict | None:
    """``{run name: today's name or None}`` for a client that fills ``{q}`` /
    ``{p}`` itself (the Data tab's value chip); None when nothing changes."""
    if names is None or names.identity:
        return None
    ents = {e for e in entities if isinstance(e, str)}
    # review: a run whose node.json names no qubits still labels its data by
    # them -- every id of the run's era (the first rename out of it names
    # them all) is in the map, so a click on any of them is translated
    step, back = names._first_step()
    if step is not None:
        if back:
            ents |= set(step.after_q) | set(step.after_p)
        else:
            ents |= set(step.src_q) | set(step.src_p)
    return {e: names.current(e) for e in sorted(ents)}
