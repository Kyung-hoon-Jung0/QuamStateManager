"""Datasets > Trends served from RAM (design ram_design.md §2b, package P3).

The customer's complaint: once thousands of runs accumulate, picking an
experiment on Datasets > Trends froze the browser for seconds. The server
rebuilt the whole answer on every pick (cheap), but shipped it as DOM: one
<img> per run per figure key (3,848 on 16_iq_blobs), a Parameter Differences
table with one column per run (962 columns, 6,741 cells) and 962 categorical
x labels -- 31,504 nodes that every Plotly layout pass then reflowed.

What lives in RAM here, and what keeps it correct:

``INDEX_MEMO``  one :class:`ExperimentTrend` per (DatasetStore, experiment):
    the complete runs in run-id order with their instants, qubit sets,
    figure names, parameters and one value array per (qubit, metric).
    Keyed on the store's identity (``instance_seq``) and validated on read
    against ``store.exp_gen[experiment]``, which moves at every insert,
    replace or pop of a run of that experiment. When the only changes since
    the stored generation are runs NEWER than every indexed one (the normal
    "a run landed" case), the new index is the old one plus those runs --
    copy-on-write, so a reader still holding the old object is never handed
    a half-appended one. Anything else (an older id, a vanished or replaced
    run, a log that no longer reaches back) rebuilds that experiment only.

``SERIES_MEMO``  the gzipped JSON the charts are drawn from, per
    (folders, experiment, qubit), validated against the per-store
    generations it was built from. Other experiments' entries stay valid
    when a run of X lands, because only ``exp_gen[X]`` moved.

``PARAMS_MEMO``  the rendered Parameter Differences fragment per
    (folders, experiment, qubit, window).

Nothing is served for a token it was not computed at (``core/ramcache``).
Every key is built by :func:`_key` from NAMED components, so the mutation
sweep in tests/test_trend_index.py can drop each one and watch a pin fail.

TODO(M4 downsampling -- deliberately NOT implemented). At the measured
scales (<= ~1,000 runs per experiment, 962 on the 4,121-run KH archive) no
series exceeds 4*W points for a ~1,000 px chart, so nothing needs reducing
and nothing is reduced: every point is sent and drawn. If an experiment ever
exceeds that, the design (ram_design.md §2a/§2b) prescribes M4 (first, min,
max, last per x-bucket; never LTTB, which destroys flat runs) with a
citation requirement that must be met BEFORE any code: [paper: Jugel et al.,
"M4: A Visualization-Oriented Time Series Data Aggregation", PVLDB 7(10),
2014] -- the "pixel-perfect at width w" claim quoted verbatim and checked
against our 'lines' rendering, plus an executable raster-equality pin (the
full and the reduced series rasterized at W px must be pixel-identical over
every real series). The statistics traces must then be computed on the full
series server-side, before the reduction; today the client computes them
from the full series it receives, which is the same thing while nothing is
reduced.
"""
from __future__ import annotations

import calendar
import gzip
import hashlib
import json
import secrets
import weakref
from datetime import datetime
from typing import Any, Callable, Iterable, Sequence

from quam_state_manager.core.dataset import trend_point
from quam_state_manager.core.differ import Differ, compare_equal
from quam_state_manager.core.experiment_data import ExperimentContext
from quam_state_manager.core.loader import natural_key
from quam_state_manager.core.ramcache import Keyed, KeyedMemo

#: Bumped whenever the payload's shape or content rules change, so an entry
#: built by older code can never be served (it is part of every key).
PAYLOAD_VER = 1

#: Parameter Differences shows the newest this-many runs unless asked for all.
PARAM_WINDOW = 20

#: design §3: a request waits this long for a concurrent computation of the
#: same key before answering "warming".
WAIT_S = 0.25

_MiB = 1024 * 1024

#: a counted entry with nothing to plot (NaN / inf): keeps its series in the
#: view, serialized as null. A module-level singleton so ``is`` finds it.
_NONFINITE = float("nan")


class ExperimentTrend:
    """One experiment's COMPLETE runs in one DatasetStore, run id ascending.

    ``series[(qubit, metric)]`` is aligned with ``run_ids``; a (qubit, metric)
    exists only when at least one run COUNTS for it under
    :func:`dataset.trend_point` (the historical rule: a number or a bool).
    A counted non-finite value is held as :data:`_NONFINITE` -- it still
    makes the series exist in a view that includes that run -- and is sent
    as ``null``.
    ``incomplete`` maps the ids of runs whose files could not be read at their
    last parse (mid-write, or unreadable) to their qubit sets: they are not
    drawn, and the view says how many there are.
    """

    __slots__ = ("experiment", "exp_gen", "run_ids", "dates", "times", "t_ms",
                 "qubits", "figs", "params", "series", "incomplete", "how")

    def __init__(self, experiment: str, exp_gen: int):
        self.experiment = experiment
        self.exp_gen = exp_gen
        self.run_ids: list[int] = []
        self.dates: list[str] = []
        self.times: list[str] = []
        self.t_ms: list[int | None] = []
        self.qubits: list[frozenset] = []
        self.figs: list[tuple] = []
        self.params: list[dict] = []
        self.series: dict[tuple[str, str], list] = {}
        self.incomplete: dict[int, frozenset] = {}
        self.how = "build"

    @property
    def max_run_id(self) -> int:
        return self.run_ids[-1] if self.run_ids else -1

    _FIELDS = ("experiment", "exp_gen", "run_ids", "dates", "times", "t_ms", "qubits",
               "figs", "params", "series", "incomplete")

    def __eq__(self, other: object) -> bool:
        """Field-wise (shadow mode and the pins compare an appended index with
        a cold build). ``how`` is deliberately not compared -- it records which
        path produced the index, not what it holds. A non-finite value is the
        :data:`_NONFINITE` singleton in both, so list equality (identity first)
        holds for it."""
        if not isinstance(other, ExperimentTrend):
            return NotImplemented
        return all(getattr(self, f) == getattr(other, f) for f in self._FIELDS)

    __hash__ = None  # type: ignore[assignment]

    @classmethod
    def build(cls, experiment: str, exp_gen: int, runs: Iterable[Any]) -> "ExperimentTrend":
        idx = cls(experiment, exp_gen)
        complete = []
        for r in runs:
            if getattr(r, "incomplete", False):
                idx.incomplete[r.run_id] = frozenset(r.qubits or ())
            else:
                complete.append(r)
        complete.sort(key=lambda r: r.run_id)
        idx._append(complete)
        return idx

    def extended(self, exp_gen: int, touched: set, runs: Iterable[Any]) -> "ExperimentTrend":
        """A NEW index: this one plus ``runs`` (every id greater than
        ``max_run_id``). Copy-on-write -- ``self`` is never modified, so a
        request still reading it sees a consistent object."""
        new = ExperimentTrend(self.experiment, exp_gen)
        new.run_ids = list(self.run_ids)
        new.dates = list(self.dates)
        new.times = list(self.times)
        new.t_ms = list(self.t_ms)
        new.qubits = list(self.qubits)
        new.figs = list(self.figs)
        new.params = list(self.params)
        new.series = {k: list(v) for k, v in self.series.items()}
        new.incomplete = {rid: q for rid, q in self.incomplete.items() if rid not in touched}
        complete = []
        for r in runs:
            if getattr(r, "incomplete", False):
                new.incomplete[r.run_id] = frozenset(r.qubits or ())
            else:
                complete.append(r)
        complete.sort(key=lambda r: r.run_id)
        new._append(complete)
        new.how = "append"
        return new

    def _append(self, runs_sorted: Sequence[Any]) -> None:
        series = self.series
        for r in runs_sorted:
            i = len(self.run_ids)
            self.run_ids.append(r.run_id)
            self.dates.append(r.date)
            self.times.append(r.time)
            self.t_ms.append(run_instant_ms(r.date, r.time))
            self.qubits.append(frozenset(r.qubits or ()))
            self.figs.append(tuple(r.figure_names or ()))
            self.params.append(r.parameters if isinstance(r.parameters, dict) else {})
            fr = r.fit_results if isinstance(r.fit_results, dict) else {}
            for q, qv in fr.items():
                if not isinstance(qv, dict):
                    continue
                for m, raw in qv.items():
                    if m == "success":
                        continue
                    counts, v = trend_point(raw)
                    if not counts:
                        continue
                    if v is None:
                        v = _NONFINITE        # counted, drawn as a gap
                    arr = series.get((q, m))
                    if arr is None:
                        arr = series[(q, m)] = []
                    if len(arr) < i:
                        arr.extend([None] * (i - len(arr)))
                    arr.append(v)
        n = len(self.run_ids)
        for arr in series.values():
            if len(arr) < n:
                arr.extend([None] * (n - len(arr)))

    def ram_bytes(self) -> int:
        """An estimate of what this index pins (the value objects themselves
        are shared with the RunInfo that holds them, so only the slots count)."""
        n = len(self.run_ids)
        slots = sum(len(a) for a in self.series.values())
        return 512 + n * 360 + slots * 8 + len(self.series) * 160 + len(self.incomplete) * 120


def run_instant_ms(date: Any, time: Any) -> int | None:
    """The run's instant as its folders name it (date dir + HHMMSS), in ms,
    or ``None`` when either part does not parse -- such a run is counted as
    undated and never placed at an invented position.

    The folder clock carries no zone: it is the acquisition host's wall
    clock. The ms value encodes those digits as if they were UTC, and a
    Plotly date axis renders a ms value as UTC digits -- so the axis shows
    exactly the clock the folder names. No zone is guessed (the same refusal
    as ChipTrends._iso).
    """
    if not isinstance(date, str) or not isinstance(time, str):
        return None
    if len(date) != 10 or date[4] != "-" or date[7] != "-":
        return None
    if len(time) != 8 or time[2] != ":" or time[5] != ":":
        return None
    parts = (date[0:4], date[5:7], date[8:10], time[0:2], time[3:5], time[6:8])
    if not all(p.isdigit() and p.isascii() for p in parts):
        return None
    try:
        dt = datetime(*(int(p) for p in parts))
    except ValueError:
        return None
    return calendar.timegm(dt.timetuple()) * 1000


# ---------------------------------------------------------------------------
# keys + memos
# ---------------------------------------------------------------------------

def _key(**components: Any) -> tuple:
    """Every key in this module (and the routes' Trends memo), from NAMED
    components, sorted by name. Each name is used by exactly one kind of key,
    so the mutation sweep (tests/test_trend_index.py) can monkeypatch this
    function to drop ONE component and show that a pin goes red:

    * index slot ``store`` + ``index_exp``, token ``exp_gen``;
    * series / params slot ``folders`` + ``exp`` + ``qubit`` (+ ``window``),
      token ``ver`` + ``stores``, each store ``seq`` + ``gen`` + ``truncated``.
    """
    return tuple(sorted(components.items()))


#: This process's identity in every version string (the series ETag and
#: the ``v`` the view compares). The tokens under it -- ``instance_seq``,
#: ``generation`` -- are process-local counters that start again at 1 after
#: an SM restart, so without this a browser holding an ETag from the previous
#: process could be told 304 for different data whose counters happen to line
#: up. The RAM memos need no such token: they die with the process.
_BOOT = secrets.token_hex(8)


def _digest(*parts: Any) -> str:
    return hashlib.sha1(repr((_BOOT,) + parts).encode("utf-8")).hexdigest()[:16]


class SeriesBlob:
    """The cached /trends/series answer: gzipped JSON plus its version."""

    __slots__ = ("gz", "v", "raw_len")

    def __init__(self, gz: bytes, v: str, raw_len: int):
        self.gz = gz
        self.v = v
        self.raw_len = raw_len

    def ram_bytes(self) -> int:
        return len(self.gz) + 64

    def json_bytes(self) -> bytes:
        return gzip.decompress(self.gz)

    def __eq__(self, other: object) -> bool:     # shadow-mode comparison
        return isinstance(other, SeriesBlob) and other.gz == self.gz and other.v == self.v

    __hash__ = None  # type: ignore[assignment]


class ParamsBlob:
    """The cached Parameter Differences fragment."""

    __slots__ = ("html", "v")

    def __init__(self, html: str, v: str):
        self.html = html
        self.v = v

    def ram_bytes(self) -> int:
        return len(self.html.encode("utf-8")) + 64

    def __eq__(self, other: object) -> bool:
        return isinstance(other, ParamsBlob) and other.html == self.html and other.v == self.v

    __hash__ = None  # type: ignore[assignment]


INDEX_MEMO = KeyedMemo("trends.index", max_bytes=32 * _MiB,
                       sizeof=lambda idx: idx.ram_bytes())
SERIES_MEMO = KeyedMemo("trends.series", max_bytes=64 * _MiB, max_entries=256)
PARAMS_MEMO = KeyedMemo("trends.param_diff", max_bytes=16 * _MiB, max_entries=128)

# A store that leaves the route's LRU is never asked for again (its
# instance_seq is never reused); free its indexes when it is collected.
# Memory only -- correctness never depended on it.
_finalized: set[int] = set()


def _watch_store(store: Any) -> None:
    seq = store.instance_seq
    if seq in _finalized:
        return
    _finalized.add(seq)
    try:
        weakref.finalize(store, _drop_store, seq)
    except TypeError:
        pass


def _drop_store(seq: int) -> None:
    _finalized.discard(seq)
    INDEX_MEMO.drop_where(lambda slot: dict(slot).get("store") == seq)


Selection = Sequence[tuple[str, Any]]      # [(folder_key, DatasetStore), ...]


def _locks(selection: Selection, extra: Iterable[Any] = ()) -> list[Any]:
    return [s._scan_lock for _fk, s in selection] + [lk for lk in extra if lk is not None]


def _index_token(gen: int) -> tuple:
    return _key(exp_gen=gen)


def experiment_index(store: Any, experiment: str, *, forbid_held: Iterable[Any] = ()
                     ) -> ExperimentTrend:
    """The RAM index of one experiment in one store, current as of this call."""
    _watch_store(store)

    def compute(prev: ExperimentTrend | None) -> Keyed:
        if prev is not None:
            gen, touched, runs = store.experiment_snapshot(
                experiment, since_gen=prev.exp_gen, append_above=prev.max_run_id)
        else:
            gen, touched, runs = store.experiment_snapshot(experiment)
        if prev is not None and touched is not None:
            idx = prev.extended(gen, touched, runs)
        else:
            idx = ExperimentTrend.build(experiment, gen, runs)
        return Keyed(idx, _index_token(gen))

    return INDEX_MEMO.get(_key(store=store.instance_seq, index_exp=experiment),
                          _index_token(store.exp_gen.get(experiment, 0)),
                          compute, incremental=True, wait_s=WAIT_S,
                          forbid_held=list(forbid_held) + [store._scan_lock])


def _data_token(selection: Selection, experiment: str, gens: Sequence[int],
                truncated: Sequence[bool]) -> tuple:
    return _key(ver=PAYLOAD_VER,
                stores=tuple(_key(seq=s.instance_seq, gen=g, truncated=t)
                             for (_fk, s), g, t in zip(selection, gens, truncated)))


def _slot(selection: Selection, experiment: str, qubit: str | None,
          **more: Any) -> tuple:
    return _key(folders=tuple(fk for fk, _s in selection),
                exp=experiment, qubit=qubit, **more)


def data_version(selection: Selection, experiment: str, qubit: str | None) -> str:
    """The version string both /trends/series and /trends/param-diff stamp on
    their answers for the CURRENT store state (the client compares them, and
    it is the series ETag). Computed without building anything."""
    gens = [s.exp_gen.get(experiment, 0) for _fk, s in selection]
    trunc = [bool(getattr(s, "scan_truncated", False)) for _fk, s in selection]
    return _digest(_slot(selection, experiment, qubit),
                   _data_token(selection, experiment, gens, trunc))


# ---------------------------------------------------------------------------
# the view: which runs, in which order, and their series
# ---------------------------------------------------------------------------

def _rows(parts: Sequence[tuple[str, ExperimentTrend]], qubit: str | None,
          merged: bool) -> list[tuple[int, int]]:
    """(part, run) pairs in display order -- the old builder's order exactly:
    one folder -> run id; a same-chip merge -> (date, time, run id), ties in
    folder order (a stable sort over the folder-by-folder concatenation)."""
    rows: list[tuple[int, int]] = []
    for pi, (_fk, idx) in enumerate(parts):
        for ri in range(len(idx.run_ids)):
            if qubit is not None and qubit not in idx.qubits[ri]:
                continue
            rows.append((pi, ri))
    if merged:
        rows.sort(key=lambda pr: (parts[pr[0]][1].dates[pr[1]] or "",
                                  parts[pr[0]][1].times[pr[1]] or "",
                                  parts[pr[0]][1].run_ids[pr[1]]))
    return rows


def build_payload(parts: Sequence[tuple[str, ExperimentTrend]], experiment: str,
                  qubit: str | None, *, merged: bool, indexing: bool, v: str) -> dict:
    """The /trends/series JSON (design §2b):
    ``{v, runs: [[run_id, t_ms|null, uid]], series: [{q, m, v}], undated, ...}``."""
    rows = _rows(parts, qubit, merged)
    runs = []
    undated = 0
    for pi, ri in rows:
        fk, idx = parts[pi]
        t = idx.t_ms[ri]
        if t is None:
            undated += 1
        runs.append([idx.run_ids[ri], t, f"{fk}:{idx.run_ids[ri]}"])

    keys: set[tuple[str, str]] = set()
    for _fk, idx in parts:
        for k in idx.series:
            if qubit is None or k[0] == qubit:
                keys.add(k)
    if qubit is not None:
        ordered = sorted(keys, key=lambda k: k[1])
    else:
        ordered = sorted(keys, key=lambda k: (natural_key(k[0]), k[0], k[1]))
    series = []
    for k in ordered:
        arrs = [idx.series.get(k) for _fk, idx in parts]
        vals = [(arrs[pi][ri] if arrs[pi] is not None else None) for pi, ri in rows]
        if any(x is not None for x in vals):
            series.append({"q": k[0], "m": k[1],
                           "v": [None if x is _NONFINITE else x for x in vals]})

    fig_keys: list[str] = []
    fig_pos: dict[str, int] = {}
    fig_runs: list[list[int]] = []
    for n, (pi, ri) in enumerate(rows):
        for fn in parts[pi][1].figs[ri]:
            j = fig_pos.get(fn)
            if j is None:
                j = fig_pos[fn] = len(fig_keys)
                fig_keys.append(fn)
                fig_runs.append([])
            if not fig_runs[j] or fig_runs[j][-1] != n:
                fig_runs[j].append(n)

    incomplete = sum(1 for _fk, idx in parts for _rid, qs in idx.incomplete.items()
                     if qubit is None or qubit in qs)
    return {
        "v": v,
        "ver": PAYLOAD_VER,
        "experiment": experiment,
        "qubit": qubit,
        "n_runs": len(rows),
        "undated": undated,
        "incomplete": incomplete,
        "indexing": indexing,
        "runs": runs,
        "series": series,
        "fig_keys": fig_keys,
        "fig_runs": fig_runs,
    }


def _encode(payload: dict) -> tuple[bytes, int]:
    raw = json.dumps(payload, separators=(",", ":"), allow_nan=False).encode("utf-8")
    # mtime=0: the same payload always gzips to the same bytes, so equal
    # answers compare equal (shadow mode, the staleness pins). Level 4, not
    # 6: on 16_iq_blobs (320 KB of JSON) level 6 costs 17 ms for 119.5 KB,
    # level 4 6 ms for 122.5 KB -- the append path's budget is 30 ms and the
    # payload budget 150 KB (measured best-of-5, docs in the P3 report).
    return gzip.compress(raw, compresslevel=4, mtime=0), len(raw)


def _parts(selection: Selection, experiment: str, forbid: Iterable[Any]
           ) -> tuple[list[tuple[str, ExperimentTrend]], list[bool]]:
    forbid = list(forbid)
    parts = [(fk, experiment_index(s, experiment, forbid_held=forbid)) for fk, s in selection]
    trunc = [bool(getattr(s, "scan_truncated", False)) for _fk, s in selection]
    return parts, trunc


def series_blob(selection: Selection, experiment: str, qubit: str | None, *,
                forbid_held: Iterable[Any] = ()) -> SeriesBlob:
    """The gzipped /trends/series answer for the current store state."""
    forbid = _locks(selection, forbid_held)
    gens = [s.exp_gen.get(experiment, 0) for _fk, s in selection]
    trunc0 = [bool(getattr(s, "scan_truncated", False)) for _fk, s in selection]
    slot = _slot(selection, experiment, qubit)

    def compute() -> Keyed:
        parts, trunc = _parts(selection, experiment, forbid)
        token = _data_token(selection, experiment, [idx.exp_gen for _fk, idx in parts], trunc)
        v = _digest(_slot(selection, experiment, qubit), token)
        payload = build_payload(parts, experiment, qubit, merged=len(selection) > 1,
                                indexing=any(trunc), v=v)
        gz, raw_len = _encode(payload)
        return Keyed(SeriesBlob(gz, v, raw_len), token)

    return SERIES_MEMO.get(slot, _data_token(selection, experiment, gens, trunc0), compute,
                           wait_s=WAIT_S, forbid_held=forbid)


def cold_series_payload(selection: Selection, experiment: str, qubit: str | None) -> dict:
    """The reference answer with NO cache anywhere: every index rebuilt from
    the store's runs right now. The staleness pins compare every served
    payload with this."""
    parts = []
    gens = []
    for fk, s in selection:
        gen, _t, runs = s.experiment_snapshot(experiment)
        parts.append((fk, ExperimentTrend.build(experiment, gen, runs)))
        gens.append(gen)
    trunc = [bool(getattr(s, "scan_truncated", False)) for _fk, s in selection]
    v = _digest(_slot(selection, experiment, qubit),
                _data_token(selection, experiment, gens, trunc))
    return build_payload(parts, experiment, qubit, merged=len(selection) > 1,
                         indexing=any(trunc), v=v)


# ---------------------------------------------------------------------------
# Parameter Differences
# ---------------------------------------------------------------------------

def param_diff_data(parts: Sequence[tuple[str, ExperimentTrend]], qubit: str | None, *,
                    merged: bool, window: str, v: str) -> dict:
    """Only the parameter rows that DIFFER across the shown runs, with exact
    counts of what is not shown. ``window`` is ``"20"`` (the newest
    :data:`PARAM_WINDOW` runs) or ``"all"``. A highlighted cell differs from
    the first shown run by :func:`differ.compare_equal`, the one comparison
    rule (docs/118) -- the same rule that decides whether the row differs."""
    rows = _rows(parts, qubit, merged)
    total = len(rows)
    shown = rows if window == "all" else rows[-PARAM_WINDOW:]
    contexts, labels, titles = [], [], []
    for pi, ri in shown:
        idx = parts[pi][1]
        contexts.append(ExperimentContext(parameters=idx.params[ri] or {},
                                          experiment_name=idx.experiment, has_data=True))
        labels.append(f"#{idx.run_ids[ri]}")
        titles.append(f"#{idx.run_ids[ri]} {idx.dates[ri]} {idx.times[ri]}".strip())
    table: list[dict] = []
    identical = 0
    if len(shown) >= 2:
        for row in Differ.compare_parameters(contexts, labels, include_equal=True):
            if row["same"]:
                identical += 1
                continue
            vals = [c["value"] for c in row["values"]]
            ref = vals[0]
            table.append({"key": row["key"], "cells": [
                {"value": val, "diff": i > 0 and not compare_equal(val, ref)}
                for i, val in enumerate(vals)]})
    return {"v": v, "window": window, "total_runs": total, "shown_runs": len(shown),
            "identical_rows": identical, "labels": labels, "titles": titles,
            "rows": table, "param_window": PARAM_WINDOW}


def params_blob(selection: Selection, experiment: str, qubit: str | None, window: str,
                render: Callable[[dict], str], *,
                forbid_held: Iterable[Any] = ()) -> ParamsBlob:
    """The rendered Parameter Differences fragment for the current state.
    ``render(data) -> html`` is the route's template call."""
    window = "all" if window == "all" else str(PARAM_WINDOW)
    forbid = _locks(selection, forbid_held)
    gens = [s.exp_gen.get(experiment, 0) for _fk, s in selection]
    trunc0 = [bool(getattr(s, "scan_truncated", False)) for _fk, s in selection]
    slot = _slot(selection, experiment, qubit, window=window)

    def compute() -> Keyed:
        parts, trunc = _parts(selection, experiment, forbid)
        token = _data_token(selection, experiment, [idx.exp_gen for _fk, idx in parts], trunc)
        v = _digest(_slot(selection, experiment, qubit), token)
        data = param_diff_data(parts, qubit, merged=len(selection) > 1, window=window, v=v)
        return Keyed(ParamsBlob(render(data), v), token)

    return PARAMS_MEMO.get(slot, _data_token(selection, experiment, gens, trunc0), compute,
                           wait_s=WAIT_S, forbid_held=forbid)


def cold_param_diff_data(selection: Selection, experiment: str, qubit: str | None,
                         window: str) -> dict:
    """The no-cache reference for the Parameter Differences data."""
    window = "all" if window == "all" else str(PARAM_WINDOW)
    parts = []
    gens = []
    for fk, s in selection:
        gen, _t, runs = s.experiment_snapshot(experiment)
        parts.append((fk, ExperimentTrend.build(experiment, gen, runs)))
        gens.append(gen)
    trunc = [bool(getattr(s, "scan_truncated", False)) for _fk, s in selection]
    v = _digest(_slot(selection, experiment, qubit),
                _data_token(selection, experiment, gens, trunc))
    return param_diff_data(parts, qubit, merged=len(selection) > 1, window=window, v=v)
