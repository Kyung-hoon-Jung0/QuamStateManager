"""DatasetStore — in-memory index of experiment runs from a data folder.

Scans the folder tree, reads node.json + data.json for each run,
and builds a searchable/filterable index. HDF5 datasets and figures
are loaded on-demand via accessor methods.
"""
from __future__ import annotations

import bisect
import json
import logging
import math
import os
import re
import threading
import time as _time
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from quam_state_manager.core import safe_io
from quam_state_manager.core import units
from quam_state_manager.core.scanner import _with_pair_qubits

logger = logging.getLogger(__name__)

# Max parsed data.json files to keep in memory per DatasetStore. Each
# data.json can be MB-sized (fit arrays); without a bound, a 2 000-run
# workspace pins multi-GB of JSON in memory (red-team Phase 2 finding §3.2).
# 200 covers the user's active browsing window comfortably; cache misses
# fall through to the source file on disk.
_DATA_JSON_CACHE_MAX = 200

# Phase 3 §2.2 — cold-scan parallelism. The per-run parse is dominated by
# ``safe_io.read_json`` for node.json + data.json; fanning across a
# ThreadPoolExecutor turns a 10⁴-run cold scan from a ~30 s freeze into
# a few seconds. Workers cap is generous: I/O scales with parallelism
# even past CPU count.
_SCAN_PARSE_WORKERS = min(32, (os.cpu_count() or 4) * 4)

# Phase 4 §4 — whitelist for the HDF5 ``which`` query parameter. Without
# this, a crafted ``?which=../somewhere`` joined into ``f"{which}.h5"``
# resolves to a Path outside ``run.folder_path``; ``h5py.File`` would
# reject non-HDF5 content but the resolved path can still land on
# arbitrary ``.h5`` files elsewhere on disk if the layout invites it.
_H5_WHICH_WHITELIST = frozenset({"ds_raw", "ds_fit", "ds_iq_blobs"})

# Regex for run folder names: #{id}_{node_name}_{HHMMSS}
# The node_name can contain underscores, but the last 6 digits are always HHMMSS.
_RUN_FOLDER_RE = re.compile(r"^#(\d+)_(.+?)_(\d{6})$")

# Date folder pattern
_DATE_FOLDER_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

# Reserved tag that backs the one-click ⭐ "favorite" (bookmarks are now just
# this tag, so the star and the tag system share one store). A run is
# "bookmarked" iff it carries this tag. The star glyph + special chip styling
# live at the render layer; the stored value stays plain, searchable text.
# NOTE: keep this value in sync with FAVORITE_TAG in web/static/dataset-virtual.js
# and web/static/app.js.
FAVORITE_TAG = "favorite"

# Per-file locks for h5py reads (h5py is NOT thread-safe).
# A global lock serialises ALL reads; per-file locks allow concurrent
# reads of different HDF5 files while still protecting each file.
_h5_locks: dict[str, threading.Lock] = {}
_h5_locks_guard = threading.Lock()


def _h5_lock_for(path: str) -> threading.Lock:
    """Return a per-file lock, creating one if needed."""
    if path not in _h5_locks:
        with _h5_locks_guard:
            if path not in _h5_locks:
                _h5_locks[path] = threading.Lock()
    return _h5_locks[path]


@dataclass
class RunInfo:
    """Metadata for a single experiment run."""

    run_id: int
    experiment_name: str  # full node name, e.g. "08_qubit_spectroscopy"
    date: str  # e.g. "2026-03-03"
    time: str  # e.g. "02:10:48" (parsed from HHMMSS)
    folder_path: Path

    # From node.json
    description: str = ""
    qubits: list[str] = field(default_factory=list)
    qubit_pairs: list[str] = field(default_factory=list)
    outcomes: dict[str, str] = field(default_factory=dict)
    parameters: dict[str, Any] = field(default_factory=dict)
    parent_id: int | None = None
    run_start: str | None = None
    run_end: str | None = None
    run_duration_s: float | None = None
    status: str = ""

    # From data.json
    fit_results: dict[str, Any] = field(default_factory=dict)
    figure_names: list[str] = field(default_factory=list)
    has_ds_raw: bool = False
    has_ds_fit: bool = False
    has_quam_state: bool = False

    # Pre-computed key-metric string for the dataset-table "metric"
    # column (Phase 3 §4.1). Deterministic given ``fit_results`` +
    # ``experiment_name``, both set at parse time and never mutated
    # afterwards, so this is computed once in ``_parse_run_folder``
    # and ``list_runs_compact`` / ``changes_since`` read it as a
    # plain attribute instead of re-running the extraction per row.
    key_metric: str = ""

    # Per-fit-key sortable scalar(s) for the run-list Sort banner, precomputed at
    # parse time (see DatasetStore._extract_sort_scalars). Sparse: only the keys
    # this run actually has a numeric value for. Value is a bare float, or
    # [first, max, min] when qubits disagree (for the client's first/max/min sort).
    sort_scalars: dict[str, Any] = field(default_factory=dict)

    # Sparse map of categorical/low-cardinality experiment parameters for the
    # Sort-banner "Parameters" facet filter (see _extract_filter_params). Only
    # bool / short-string / int values (skips float sweeps, None, lists, dicts).
    # Shipped as "pm" in _compact_row; the client builds key=value facets from it.
    filter_params: dict[str, Any] = field(default_factory=dict)

    # Bookmarks/tags (loaded from quashboard_tags.json)
    bookmarked: bool = False
    tags: list[str] = field(default_factory=list)
    note: str = ""

    # Wall-clock time the scanner most recently parsed this folder.
    # Used by ``DatasetStore.changes_since`` for delta polling.
    last_parsed: float = 0.0


def _parse_time(hhmmss: str) -> str:
    """Convert '021048' to '02:10:48'."""
    if len(hhmmss) == 6:
        return f"{hhmmss[:2]}:{hhmmss[2:4]}:{hhmmss[4:6]}"
    return hhmmss


def _compact_row(run: "RunInfo") -> dict:
    """Slim table row for the virtual scroller — abbreviated keys keep the JSON
    small. Shared by ``list_runs_compact`` + ``changes_since`` so the two payloads
    can never drift. The client column registry (dataset-virtual.js) renders from
    these keys; ``status``/``dur`` power the Status chip + Duration column, and the
    relative "When" column is derived client-side from ``date``+``time``.
    """
    return {
        "id": run.run_id,
        "exp": run.experiment_name,
        "date": run.date,
        "time": run.time,
        "q": run.qubits,
        "p": run.qubit_pairs,   # qubit-pair names for 2Q runs (Pairs picker + pair search)
        "oc": run.outcomes,
        "metric": run.key_metric,
        "bm": run.bookmarked,
        "tags": run.tags,
        "status": run.status,
        "dur": run.run_duration_s,
        "note": run.note,
        "parent": run.parent_id,
        "hs": run.has_quam_state,
        "sm": run.sort_scalars,   # sparse {fit_key: scalar | [first,max,min]} for the Sort banner
        "pm": run.filter_params,  # sparse {param_key: bool|str|int} for the Parameters facet filter
    }


def build_trend_data(runs, qubit=None, metrics=None, folder_key_of=None) -> dict:
    """Build a trend payload from an explicit, already-sorted list of RunInfo.

    Mirrors ``DatasetStore.get_trend_data`` but works over an arbitrary run list
    (one folder, or several merged for the same chip), and stamps a folder-aware
    ``uid`` on each run when ``folder_key_of`` (a ``RunInfo -> folder_key``
    callable) is given — so the trend figure strip can link to ``/dataset/<uid>``
    even when run_ids collide across folders.
    """
    matching = list(runs)
    all_qubits: set[str] = set()
    all_metrics: set[str] = set()
    for r in matching:
        for qname, qvals in r.fit_results.items():
            if qubit and qname != qubit:
                continue
            all_qubits.add(qname)
            if isinstance(qvals, dict):
                all_metrics.update(qvals.keys())
    target_metrics = metrics or sorted(all_metrics - {"success"})
    target_qubits = [qubit] if qubit else sorted(all_qubits)

    runs_info = []
    for r in matching:
        info = {"run_id": r.run_id, "date": r.date, "time": r.time}
        if folder_key_of is not None:
            info["uid"] = f"{folder_key_of(r)}:{r.run_id}"
        runs_info.append(info)

    series = []
    for q in target_qubits:
        for m in target_metrics:
            values = []
            for r in matching:
                qvals = r.fit_results.get(q, {})
                val = qvals.get(m) if isinstance(qvals, dict) else None
                values.append(val if isinstance(val, (int, float)) else None)
            if any(v is not None for v in values):
                series.append({"qubit": q, "metric": m, "values": values})

    fig_keys: list[str] = []
    for r in matching:
        for fn in r.figure_names:
            if fn not in fig_keys:
                fig_keys.append(fn)

    return {
        "runs": runs_info,
        "series": series,
        "figure_keys": fig_keys,
        "matching_run_ids": [r.run_id for r in matching],
    }


def _calc_duration(start_str: str | None, end_str: str | None) -> float | None:
    """Calculate duration in seconds between two ISO datetime strings."""
    if not start_str or not end_str:
        return None
    try:
        fmt_options = ["%Y-%m-%dT%H:%M:%S.%f%z", "%Y-%m-%dT%H:%M:%S%z"]
        start = end = None
        for fmt in fmt_options:
            try:
                start = datetime.strptime(start_str, fmt)
                break
            except ValueError:
                continue
        for fmt in fmt_options:
            try:
                end = datetime.strptime(end_str, fmt)
                break
            except ValueError:
                continue
        if start and end:
            return (end - start).total_seconds()
    except Exception:
        pass
    return None


def _extract_figure_names(data: dict) -> list[str]:
    """Extract all figure references from data.json.

    Figures can appear as:
    - "figures": {"amplitude": "./figures.amplitude.png"}
    - "figure_flux": "./figure_flux.png"
    - "fir_figures": {"raw_q6": "./fir_figures.raw_q6.png", ...}
    """
    names = []
    for key, val in data.items():
        if key in ("ds_raw", "ds_fit", "fit_results"):
            continue
        if isinstance(val, str) and val.endswith(".png"):
            names.append(key)
        elif isinstance(val, dict):
            for sub_key, sub_val in val.items():
                if isinstance(sub_val, str) and sub_val.endswith(".png"):
                    names.append(f"{key}.{sub_key}")
    return names


def _resolve_figure_path(run_folder: Path, data: dict, figure_name: str) -> Path | None:
    """Resolve a figure name to its file path on disk."""
    parts = figure_name.split(".", 1)
    if len(parts) == 2:
        # Nested: "fir_figures.raw_q6" → data["fir_figures"]["raw_q6"]
        container = data.get(parts[0])
        if isinstance(container, dict):
            rel = container.get(parts[1])
            if rel:
                return run_folder / rel.lstrip("./")
    else:
        # Top-level: "figure_flux" → data["figure_flux"]
        rel = data.get(figure_name)
        if isinstance(rel, str):
            return run_folder / rel.lstrip("./")
    return None


class DatasetStore:
    """In-memory index of all experiment runs in a data folder."""

    def __init__(self, folder_path: str | Path):
        self.folder_path = Path(folder_path)
        self.runs: dict[int, RunInfo] = {}
        self.dates: list[str] = []
        self.experiment_types: list[str] = []
        self._run_ids_sorted: list[int] = []  # ascending; rebuilt in _scan
        # LRU-bounded cache of parsed data.json content, keyed by run_id.
        # Bounded so a workspace with thousands of runs doesn't pin multi-GB
        # of JSON in memory (red-team Phase 2 finding §3.2). Guarded by
        # ``_data_cache_lock`` so the parallel scan (Phase 3 §2.2) can
        # populate it safely from multiple worker threads.
        self._data_json_cache: OrderedDict[int, dict] = OrderedDict()
        self._data_cache_lock = threading.Lock()
        self._tags_path = self.folder_path / "quashboard_tags.json"
        self._tags_data: dict[str, Any] = {"bookmarks": [], "tags": {}, "notes": {}}
        # Guards tag/bookmark/note mutations + their atomic-write to disk
        # (red-team Phase 2 finding §3.1). Two concurrent HTMX requests
        # editing the same tags file would otherwise race on the in-memory
        # dict mutation and silently drop one of the writes.
        self._tags_lock = threading.Lock()
        # Serialises ``_scan`` so concurrent rescans never overlap (the
        # background Scheduler worker rescans after each node while the live
        # Datasets delta-poll runs in request threads — both call
        # ``rescan_if_stale``; without this they'd race on the in-place
        # mutation of ``self.runs`` / ``self._folder_fp``). Reentrant so a
        # reader that snapshots under it can also trigger a rescan. See
        # docs/40_scheduler.md §dataset-integration.
        self._scan_lock = threading.RLock()
        self._last_mtime: float = 0.0
        # Per-folder fingerprint cache for incremental rescans.
        # path → (folder_mtime, node_mtime, data_mtime, run_id)
        # On rescan we re-parse only folders whose mtimes changed; identical
        # folders are skipped, vanished folders drop their run_id from runs.
        self._folder_fp: dict[Path, tuple[float, float, float, int]] = {}
        # Per-date-dir fingerprint cache (B27). date_path → (date_mtime, run_paths).
        # A run folder can only be added to / removed from a date dir by moving
        # the date dir's own mtime, so a date dir whose mtime is unchanged since
        # the last scan cannot have gained/lost runs. We skip ``iterdir`` (and
        # the 3 stats/run inside) on such dirs entirely and re-serve their runs
        # from this cache, turning a steady-state poll on a 10⁴-run workspace
        # from ~30k stat syscalls into a handful (root + date-dir mtimes).
        self._date_fp: dict[Path, tuple[float, frozenset[Path]]] = {}
        # Vanished-since log for delta polling — newest entries pushed at the
        # end. Each entry is (run_id, deletion_unix_ts). Old entries past the
        # retention window are discarded on every scan to bound memory.
        self._vanished: list[tuple[int, float]] = []
        self._VANISHED_RETENTION_S = 30 * 60  # 30 min
        self._scan()
        self._load_tags()

    def _cache_data_json(self, run_id: int, data: dict) -> None:
        """Insert *data* into the LRU cache, evicting the oldest if over cap.

        Thread-safe — the parallel scan (Phase 3 §2.2) calls this from
        worker threads. The lock-hold is tiny (dict moves + at most one
        ``popitem``) so contention is negligible.
        """
        with self._data_cache_lock:
            if run_id in self._data_json_cache:
                self._data_json_cache.move_to_end(run_id)
            self._data_json_cache[run_id] = data
            while len(self._data_json_cache) > _DATA_JSON_CACHE_MAX:
                self._data_json_cache.popitem(last=False)

    def _get_data_json(self, run_id: int) -> dict:
        """Return cached data.json for *run_id*, loading from disk on miss.

        A miss is normal when the LRU has evicted this run's entry; we re-
        read from the source folder via :mod:`safe_io` and re-cache.
        Returns ``{}`` if the file is missing or unreadable.
        """
        with self._data_cache_lock:
            cached = self._data_json_cache.get(run_id)
            if cached is not None:
                self._data_json_cache.move_to_end(run_id)
                return cached
        run = self.runs.get(run_id)
        if run is None:
            return {}
        data_path = run.folder_path / "data.json"
        if not data_path.exists():
            return {}
        try:
            data = safe_io.read_json(data_path)
        except (OSError, ValueError):
            return {}
        self._cache_data_json(run_id, data)
        return data

    # ------------------------------------------------------------------
    # Scanning
    # ------------------------------------------------------------------

    @staticmethod
    def _stat_mtime(path: Path) -> float:
        try:
            return path.stat().st_mtime
        except OSError:
            return 0.0

    def _parse_run_folder(
        self, run_entry: Path, date_str: str, run_id: int, time_str: str, experiment_name: str
    ) -> RunInfo | None:
        """Parse a single run folder (node.json + data.json) into a RunInfo.

        Reads route through :mod:`safe_io` so a still-active experiment
        writing into this folder (fit-result writeback, etc.) is never
        blocked by our read on Windows (red-team Phase 2 finding §3.4).
        """
        # Read node.json
        node_path = run_entry / "node.json"
        node_data: dict = {}
        if node_path.exists():
            try:
                node_data = safe_io.read_json(node_path)
            except (OSError, ValueError) as e:
                logger.warning("Failed to read %s: %s", node_path, e)

        # Read data.json
        data_path = run_entry / "data.json"
        data_json: dict = {}
        if data_path.exists():
            try:
                data_json = safe_io.read_json(data_path)
                self._cache_data_json(run_id, data_json)
            except (OSError, ValueError) as e:
                logger.warning("Failed to read %s: %s", data_path, e)

        # Extract metadata from node.json
        metadata = node_data.get("metadata", {})
        data_section = node_data.get("data", {})
        params_raw = data_section.get("parameters", {})
        if isinstance(params_raw, dict):
            params_model = params_raw.get("model", {})
            parameters = dict(params_model) if params_model else dict(params_raw)
        else:
            params_model = {}
            parameters = {}

        qubits = params_model.get("qubits") or parameters.get("qubits") or []
        if isinstance(qubits, str):
            qubits = [qubits]
        if not isinstance(qubits, list):
            qubits = [qubits] if qubits else []
        # 2Q runs carry `qubit_pairs` (e.g. ["qA2-qA1"]) and no `qubits`; keep the
        # pairs AND fold their member qubits into `qubits` (shared helper) so a
        # qubit filter/search surfaces 2Q runs and the QUBITS column isn't "–".
        qubits, qubit_pairs = _with_pair_qubits(
            qubits, params_model.get("qubit_pairs") or parameters.get("qubit_pairs"))

        outcomes = data_section.get("outcomes", {})
        description = (metadata.get("description") or "").strip()
        run_start = metadata.get("run_start")
        run_end = metadata.get("run_end")
        parents = node_data.get("parents", [])
        parent_id = parents[0] if parents else None

        fit_results = data_json.get("fit_results", {})
        figure_names = _extract_figure_names(data_json)

        info = RunInfo(
            run_id=run_id,
            experiment_name=experiment_name,
            date=date_str,
            time=time_str,
            folder_path=run_entry,
            description=description,
            qubits=qubits,
            qubit_pairs=qubit_pairs,
            outcomes=outcomes,
            parameters=parameters,
            parent_id=parent_id,
            run_start=run_start,
            run_end=run_end,
            run_duration_s=_calc_duration(run_start, run_end),
            status=metadata.get("status", ""),
            fit_results=fit_results,
            figure_names=figure_names,
            has_ds_raw=(run_entry / "ds_raw.h5").exists(),
            has_ds_fit=(run_entry / "ds_fit.h5").exists(),
            has_quam_state=(run_entry / "quam_state").is_dir(),
        )
        # Pre-compute the dataset-table key metric so list_runs_compact /
        # changes_since don't repeat the same work per row at request time
        # (Phase 3 §4.1). fit_results + experiment_name are set above and
        # never mutated, so this is correct for the lifetime of RunInfo.
        info.key_metric = self._extract_key_metric(info)
        info.sort_scalars = self._extract_sort_scalars(info)
        info.filter_params = self._extract_filter_params(info)
        return info

    def _scan(self):
        """Walk date/run folders incrementally.

        First call: parses every run folder.  Subsequent calls: re-parses
        only folders whose mtime changed since the last scan, drops folders
        that vanished, and adds new ones. Lets ``rescan_if_stale`` finish
        in tens of milliseconds even with thousands of unchanged runs.
        """
        dates_set: set[str] = set()
        experiments_set: set[str] = set()
        seen_paths: set[Path] = set()
        parsed = 0
        reused = 0

        root = self.folder_path
        if not root.is_dir():
            logger.warning("Dataset folder not found: %s", root)
            self.runs.clear()
            self._data_json_cache.clear()
            self._folder_fp.clear()
            self._date_fp.clear()
            self.dates = []
            self.experiment_types = []
            self._last_mtime = 0.0
            return

        # Discovery pass — walks the date/run hierarchy, classifies each
        # run folder as either "reuse from fingerprint cache" or "needs
        # parse". The parse pass below fans out via a ThreadPoolExecutor
        # (Phase 3 §2.2). Single-threaded discovery is fine: it's
        # ``iterdir`` + ``stat`` only.
        #
        # B27 — date-dir-granular scoping. A run folder can only be created
        # or deleted under a date dir by mutating that date dir's own mtime,
        # so a date dir whose mtime matches ``self._date_fp`` cannot have
        # gained or lost children since the last scan. We skip ``iterdir``
        # (and the 3 stats per run inside) on such dirs entirely, re-serving
        # their runs from the fingerprint caches. Only date dirs whose mtime
        # actually moved are walked. This keeps a steady-state poll on a
        # 10⁴-run workspace at O(date dirs) stats instead of O(runs).
        to_parse: list[tuple[Path, str, int, str, str, float, float, float]] = []
        fresh_date_fp: dict[Path, tuple[float, frozenset[Path]]] = {}
        for date_entry in sorted(root.iterdir()):
            if not date_entry.is_dir():
                continue
            if not _DATE_FOLDER_RE.match(date_entry.name):
                continue

            date_str = date_entry.name
            dates_set.add(date_str)
            date_mt = self._stat_mtime(date_entry)

            cached_date = self._date_fp.get(date_entry)
            if (
                cached_date is not None
                and cached_date[0] == date_mt
                and date_mt != 0.0
            ):
                # Unchanged date dir — re-serve its runs without iterdir.
                # Every cached run path must still carry a live RunInfo +
                # fingerprint; if any is missing (e.g. evicted out-of-band)
                # we fall through to a full walk of this dir for safety.
                cached_paths = cached_date[1]
                all_live = True
                for run_entry in cached_paths:
                    fp = self._folder_fp.get(run_entry)
                    if fp is None or fp[3] not in self.runs:
                        all_live = False
                        break
                if all_live:
                    for run_entry in cached_paths:
                        m = _RUN_FOLDER_RE.match(run_entry.name)
                        if not m:
                            continue
                        experiments_set.add(m.group(2))
                        seen_paths.add(run_entry)
                        reused += 1
                    fresh_date_fp[date_entry] = (date_mt, cached_paths)
                    continue

            # Changed (or first-seen / unverifiable) date dir — full walk.
            date_run_paths: set[Path] = set()
            for run_entry in date_entry.iterdir():
                if not run_entry.is_dir():
                    continue
                m = _RUN_FOLDER_RE.match(run_entry.name)
                if not m:
                    continue

                run_id = int(m.group(1))
                experiment_name = m.group(2)
                time_str = _parse_time(m.group(3))
                experiments_set.add(experiment_name)
                seen_paths.add(run_entry)
                date_run_paths.add(run_entry)

                folder_mt = self._stat_mtime(run_entry)
                node_mt = self._stat_mtime(run_entry / "node.json")
                data_mt = self._stat_mtime(run_entry / "data.json")

                cached = self._folder_fp.get(run_entry)
                if (
                    cached is not None
                    and cached[0] == folder_mt
                    and cached[1] == node_mt
                    and cached[2] == data_mt
                    and cached[3] == run_id
                    and run_id in self.runs
                ):
                    reused += 1
                    continue

                to_parse.append((
                    run_entry, date_str, run_id, time_str, experiment_name,
                    folder_mt, node_mt, data_mt,
                ))
            # Record the date dir's mtime + run-path set so the next scan can
            # skip it if untouched. Only safe to short-circuit once all its
            # runs are committed to self.runs, so freshly-parsed dirs are
            # finalised after the parse pass below.
            fresh_date_fp[date_entry] = (date_mt, frozenset(date_run_paths))

        # Parse pass — bounded parallelism. ``_parse_run_folder`` is
        # thread-safe for distinct folders (it mutates ``self._data_json_cache``
        # via ``_cache_data_json``, so we serialise the writeback on the
        # main thread below). Without parallelism a 10⁴-run cold scan
        # freezes the UI for ~30s; with it, seconds.
        if to_parse:
            now = _time.time()
            workers = min(_SCAN_PARSE_WORKERS, len(to_parse))

            def _parse_one(task):
                run_entry, date_str, run_id, time_str, experiment_name, *_mts = task
                return run_id, self._parse_run_folder(
                    run_entry, date_str, run_id, time_str, experiment_name,
                )

            with ThreadPoolExecutor(max_workers=workers) as ex:
                results = list(ex.map(_parse_one, to_parse))

            # Serialise writebacks so we don't race on self.runs /
            # self._data_json_cache / self._folder_fp.
            for (run_entry, _date_str, run_id, _time_str, _exp_name,
                 folder_mt, node_mt, data_mt), (_rid, run_info) in zip(to_parse, results):
                if run_info is None:
                    continue
                run_info.last_parsed = now
                self.runs[run_id] = run_info
                self._folder_fp[run_entry] = (folder_mt, node_mt, data_mt, run_id)
                parsed += 1

        # Drop runs whose folders vanished
        now_ts = _time.time()
        vanished = [p for p in self._folder_fp.keys() if p not in seen_paths]
        for p in vanished:
            _, _, _, vanished_id = self._folder_fp.pop(p)
            self.runs.pop(vanished_id, None)
            self._data_json_cache.pop(vanished_id, None)
            self._vanished.append((vanished_id, now_ts))
        # Drop entries older than the retention window
        cutoff = now_ts - self._VANISHED_RETENTION_S
        if self._vanished and self._vanished[0][1] < cutoff:
            self._vanished = [v for v in self._vanished if v[1] >= cutoff]

        # Commit the date-dir fingerprint cache (B27). Replacing it wholesale
        # drops entries for date dirs that vanished this scan. Dirs that were
        # walked this round are now fully reflected in self.runs / _folder_fp,
        # so the next scan can legitimately short-circuit any whose mtime holds.
        self._date_fp = fresh_date_fp

        self.dates = sorted(dates_set, reverse=True)
        self.experiment_types = sorted(experiments_set)
        # Sorted run-id index for O(log n) previous/next-run lookups (the
        # prev-state diff). Rebuilt here whenever self.runs changes.
        self._run_ids_sorted = sorted(self.runs.keys())
        self._last_mtime = self._current_mtime()
        if parsed or vanished:
            logger.info(
                "DatasetStore scan: %d parsed, %d reused, %d removed (total %d runs, %d dates)",
                parsed,
                reused,
                len(vanished),
                len(self.runs),
                len(self.dates),
            )

    def _current_mtime(self) -> float:
        """Return the newest mtime of the data root and its latest date subfolder."""
        try:
            best = self.folder_path.stat().st_mtime
        except OSError:
            return 0.0
        # Also check the latest date subfolder — new runs land inside it
        for entry in self.folder_path.iterdir():
            if entry.is_dir() and _DATE_FOLDER_RE.match(entry.name):
                try:
                    mt = entry.stat().st_mtime
                    if mt > best:
                        best = mt
                except OSError:
                    pass
        return best

    def rescan_if_stale(self) -> bool:
        """Re-scan if folder mtimes changed.  Returns True if new runs found.

        Serialised on ``_scan_lock`` so the Scheduler worker's post-node rescan
        and the live Datasets delta-poll can't both mutate ``self.runs`` at once.
        The mtime is re-checked inside the lock so a thread that blocked behind
        another's just-finished rescan does no redundant work.
        """
        if self._current_mtime() == self._last_mtime:
            return False
        with self._scan_lock:
            if self._current_mtime() == self._last_mtime:
                return False
            old_max = max(self.runs.keys()) if self.runs else -1
            self._scan()
            self._load_tags()
            new_max = max(self.runs.keys()) if self.runs else -1
            return new_max > old_max

    # ------------------------------------------------------------------
    # Querying
    # ------------------------------------------------------------------

    def changes_since(self, ts: float, date: str | None = None) -> dict:
        """Return rows added/updated since ``ts`` plus run_ids that vanished.

        Used by the JS auto-poll to merge deltas into the in-memory row store
        without re-fetching the full dataset payload every minute.

        Triggers an incremental rescan first so newly-arrived folders show up.
        Caller passes the previous response's ``now`` field as ``ts``.
        """
        try:
            self.rescan_if_stale()
        except Exception:
            logger.exception("changes_since: incremental rescan failed")
        updated = []
        # Snapshot under the scan lock so a concurrent worker rescan can't
        # mutate self.runs mid-iteration ("dictionary changed size").
        with self._scan_lock:
            runs = list(self.runs.values())
        for run in runs:
            if run.last_parsed <= ts:
                continue
            if date and run.date != date:
                continue
            updated.append(_compact_row(run))
        vanished = [run_id for run_id, vts in self._vanished if vts > ts]
        return {
            "updated": updated,
            "vanished": vanished,
            "now": _time.time(),
        }

    def list_runs_compact(self, date: str | None = None) -> list[dict]:
        """Slim row payload for the dataset table (virtual scroller).

        Returns only fields the table actually displays, with abbreviated
        keys to keep the JSON payload small (~60% the size of ``list_runs``).
        Sorted newest-first by run id.

        Field map: id, exp, date, time, q (qubits), oc (outcomes),
        metric (key metric), bm (bookmarked), tags, status, dur (duration s),
        note, parent (parent_id), hs (has_quam_state). See ``_compact_row``.
        """
        results = []
        with self._scan_lock:
            runs = list(self.runs.values())
        for run in runs:
            if date and run.date != date:
                continue
            results.append(_compact_row(run))
        results.sort(key=lambda r: r["id"], reverse=True)
        return results

    def list_runs(
        self,
        experiment: str | None = None,
        date: str | None = None,
        qubit: str | None = None,
        bookmarked_only: bool = False,
        tag: str | None = None,
        sort: str = "id",
        desc: bool = True,
    ) -> list[dict]:
        """Return filtered/sorted list of run summaries."""
        results = []
        with self._scan_lock:
            runs = list(self.runs.values())
        for run in runs:
            if experiment and run.experiment_name != experiment:
                continue
            if date and run.date != date:
                continue
            if qubit and qubit not in run.qubits:
                continue
            if bookmarked_only and not run.bookmarked:
                continue
            if tag and tag not in run.tags:
                continue

            # Pre-computed at parse time (Phase 3 §4.1).
            key_metric = run.key_metric

            results.append(
                {
                    "run_id": run.run_id,
                    "experiment_name": run.experiment_name,
                    "date": run.date,
                    "time": run.time,
                    "qubits": run.qubits,
                    "outcomes": run.outcomes,
                    "key_metric": key_metric,
                    "status": run.status,
                    "duration_s": run.run_duration_s,
                    "bookmarked": run.bookmarked,
                    "tags": run.tags,
                    "note": run.note,
                    "has_quam_state": run.has_quam_state,
                }
            )

        # Sort
        if sort == "id":
            results.sort(key=lambda r: r["run_id"], reverse=desc)
        elif sort == "date":
            results.sort(key=lambda r: (r["date"], r["time"]), reverse=desc)
        elif sort == "experiment":
            results.sort(key=lambda r: r["experiment_name"], reverse=desc)

        return results

    # ------------------------------------------------------------------
    # Fit-result reference resolution (detail view only)
    # ------------------------------------------------------------------

    @staticmethod
    def _npval(arr):
        """A single npz/h5 entry → a display value.

        Finite scalars (0-d or size-1) become a Python float so the Results
        tab formats them like any inline fit value; non-finite → ``None``;
        non-numeric scalars pass through; multi-element arrays become a
        compact ``[array <dtype> <shape>]`` marker (we never inline a vector
        into the table).
        """
        import numpy as np

        a = np.asarray(arr)
        if a.size == 1:
            v = a.item()
            if isinstance(v, bool):
                return v
            if isinstance(v, (int, float)):
                return v if math.isfinite(v) else None
            return v
        return f"[array {a.dtype} {tuple(a.shape)}]"

    @staticmethod
    def _h5_lookup(f, key: str):
        """Best-effort lookup of ``key`` in an open h5py file.

        ``data.json`` references use a dotted key (``fit_results.q.metric``).
        The on-disk dataset may be named with that exact dotted string or laid
        out as a nested group path; try both. Returns the dataset or ``None``.
        """
        for candidate in (key, "/" + key.replace(".", "/")):
            try:
                if candidate in f:
                    return f[candidate]
            except (KeyError, TypeError, ValueError):
                continue
        return None

    def _resolve_fit_ref(self, folder_path: Path, ref: str):
        """Resolve a ``./<file>#<key>`` fit-result reference to its real value.

        ``data.json`` sometimes stores a fitted value not inline but as a
        relative reference into a companion array file, e.g.
        ``"./arrays.npz#fit_results.qA2-qA1.control_phase_correction"``. Left
        raw, the Results tab shows that string instead of the number. We open
        the referenced file (inside the run folder only), pull the entry, and
        return a finite scalar / array-summary via :meth:`_npval`. Any failure
        (missing file or key, path escape, numpy/h5py absent) returns the raw
        string unchanged so nothing is silently dropped.
        """
        if "#" not in ref:
            return ref
        rel_file, _, key = ref.partition("#")
        try:
            target = (folder_path / rel_file).resolve()
            if not target.is_relative_to(folder_path.resolve()):
                logger.warning("fit-ref path traversal blocked: %s", ref)
                return ref
        except (OSError, ValueError):
            return ref
        if not target.exists():
            return ref

        suffix = target.suffix.lower()
        try:
            if suffix == ".npz":
                import numpy as np

                with np.load(target, allow_pickle=True) as npz:
                    if key not in npz.files:
                        return ref
                    arr = npz[key]
                return self._npval(arr)
            if suffix in (".h5", ".hdf5"):
                import h5py

                with _h5_lock_for(str(target)):
                    with h5py.File(target, "r") as f:
                        node = self._h5_lookup(f, key)
                        if node is None:
                            return ref
                        arr = node[()]
                return self._npval(arr)
        except Exception as e:  # noqa: BLE001 — never let a bad ref break the view
            logger.debug("fit-ref resolve failed for %r: %s", ref, e)
            return ref
        return ref

    def _resolve_fit_refs(self, run) -> dict:
        """``run.fit_results`` with any ``./file#key`` string references resolved.

        Detail-view only (called from :meth:`get_run`) so the per-run bulk scan
        stays I/O-light — the npz/h5 read happens once, when a run is opened.
        """
        fit = run.fit_results
        if not isinstance(fit, dict):
            return fit
        out: dict[str, Any] = {}
        for qname, qres in fit.items():
            if isinstance(qres, dict):
                out[qname] = {
                    k: (self._resolve_fit_ref(run.folder_path, v)
                        if isinstance(v, str) and v.startswith("./") and "#" in v
                        else v)
                    for k, v in qres.items()
                }
            else:
                out[qname] = qres
        return out

    def get_run(self, run_id: int) -> dict | None:
        """Return full metadata + fit_results for a single run."""
        run = self.runs.get(run_id)
        if not run:
            return None
        return {
            "run_id": run.run_id,
            "experiment_name": run.experiment_name,
            "date": run.date,
            "time": run.time,
            "folder_path": str(run.folder_path),
            "description": run.description,
            "qubits": run.qubits,
            "qubit_pairs": run.qubit_pairs,
            "outcomes": run.outcomes,
            "parameters": run.parameters,
            "parent_id": run.parent_id,
            "run_start": run.run_start,
            "run_end": run.run_end,
            "duration_s": run.run_duration_s,
            "status": run.status,
            "fit_results": self._resolve_fit_refs(run),
            "figure_names": run.figure_names,
            "has_ds_raw": run.has_ds_raw,
            "has_ds_fit": run.has_ds_fit,
            "has_quam_state": run.has_quam_state,
            # Nearest earlier run carrying a state snapshot — gates the
            # "Prev State" tab / Full-View diff summary. None when none exists.
            "prev_run_id": (
                self.get_previous_run_id(run_id) if run.has_quam_state else None
            ),
            "bookmarked": run.bookmarked,
            "tags": run.tags,
            "note": run.note,
        }

    # ------------------------------------------------------------------
    # Trend data extraction
    # ------------------------------------------------------------------

    def get_trend_data(
        self,
        experiment: str,
        qubit: str | None = None,
        metrics: list[str] | None = None,
    ) -> dict:
        """Extract time-series trend data for plotting.

        Returns a dict with:
          runs: [{run_id, date, time}, ...] sorted by run_id
          series: [{qubit, metric, values: [float|None, ...]}, ...]
          figure_keys: [str, ...]
          matching_run_ids: [int, ...]
        """
        with self._scan_lock:
            runs = list(self.runs.values())
        matching = [
            r for r in runs
            if r.experiment_name == experiment
            and (qubit is None or qubit in r.qubits)
        ]
        matching.sort(key=lambda r: r.run_id)

        all_qubits: set[str] = set()
        all_metrics: set[str] = set()
        for r in matching:
            for qname, qvals in r.fit_results.items():
                if qubit and qname != qubit:
                    continue
                all_qubits.add(qname)
                if isinstance(qvals, dict):
                    all_metrics.update(qvals.keys())

        target_metrics = metrics or sorted(all_metrics - {"success"})
        target_qubits = [qubit] if qubit else sorted(all_qubits)

        runs_info = [
            {"run_id": r.run_id, "date": r.date, "time": r.time}
            for r in matching
        ]

        series = []
        for q in target_qubits:
            for m in target_metrics:
                values = []
                for r in matching:
                    qvals = r.fit_results.get(q, {})
                    val = qvals.get(m) if isinstance(qvals, dict) else None
                    # Only include numeric values
                    if isinstance(val, (int, float)):
                        values.append(val)
                    else:
                        values.append(None)
                if any(v is not None for v in values):
                    series.append({"qubit": q, "metric": m, "values": values})

        fig_keys: list[str] = []
        for r in matching:
            for fn in r.figure_names:
                if fn not in fig_keys:
                    fig_keys.append(fn)

        return {
            "runs": runs_info,
            "series": series,
            "figure_keys": fig_keys,
            "matching_run_ids": [r.run_id for r in matching],
        }

    # ------------------------------------------------------------------
    # Figure serving
    # ------------------------------------------------------------------

    def get_figure_path(self, run_id: int, figure_name: str) -> Path | None:
        """Return absolute path to a figure PNG file (path-traversal safe)."""
        run = self.runs.get(run_id)
        if not run:
            return None

        # Source data.json from the LRU cache; on a miss we re-read from
        # disk so an evicted entry isn't a silent "figure not found".
        data_json = self._get_data_json(run_id)
        fig_path = _resolve_figure_path(run.folder_path, data_json, figure_name)

        if fig_path is None:
            return None

        # Security: ensure resolved path is within the run folder. Use
        # Path.is_relative_to (3.9+) so prefix-substring confusion can't
        # leak (e.g. /data/run vs /data/run-evil/...); see red-team
        # Phase 2 finding §3.3.
        try:
            fig_path = fig_path.resolve()
            run_folder_resolved = run.folder_path.resolve()
            if not fig_path.is_relative_to(run_folder_resolved):
                logger.warning("Path traversal attempt: %s", fig_path)
                return None
        except (OSError, ValueError):
            return None

        return fig_path if fig_path.exists() else None

    # ------------------------------------------------------------------
    # HDF5 summary (lazy-loaded)
    # ------------------------------------------------------------------

    def get_h5_summary(self, run_id: int, which: str = "ds_raw") -> dict | None:
        """Open HDF5 file and return dimensions, shapes, coordinate arrays."""
        try:
            import h5py
        except ImportError:
            return {"error": "h5py not installed"}

        if which not in _H5_WHICH_WHITELIST:
            # Phase 4 §4 — refuse path-traversal-shaped values up front.
            return None

        run = self.runs.get(run_id)
        if not run:
            return None

        h5_path = run.folder_path / f"{which}.h5"
        if not h5_path.exists():
            return None

        with _h5_lock_for(str(h5_path)):
            try:
                with h5py.File(h5_path, "r") as f:
                    return self._parse_h5_structure(f)
            except Exception as e:
                logger.warning("Failed to read %s: %s", h5_path, e)
                return {"error": str(e)}

    def _parse_h5_structure(self, f) -> dict:
        """Parse an xarray/NetCDF4-saved HDF5 file structure.

        Coordinates are identified by the DIMENSION_SCALE class attribute.
        Data variables are everything else (they reference coordinates via
        DIMENSION_LIST).
        """
        dimensions = {}
        coordinates = {}
        data_vars = {}

        # First pass: identify coordinate dimensions (DIMENSION_SCALE)
        coord_names = set()
        for name in f:
            ds = f[name]
            attrs = dict(ds.attrs)
            cls = attrs.get("CLASS", b"")
            if isinstance(cls, bytes):
                cls = cls.decode()
            if cls == "DIMENSION_SCALE" and len(ds.shape) == 1:
                coord_names.add(name)
                data = ds[()]
                if hasattr(data, "tolist"):
                    data = data.tolist()
                if data and isinstance(data[0], bytes):
                    data = [x.decode() for x in data]
                coordinates[name] = data
                dimensions[name] = len(data)

        # Second pass: data variables (everything that's not a coordinate)
        for name in f:
            if name in coord_names:
                continue
            ds = f[name]
            attrs = dict(ds.attrs)

            # Try to figure out dimension names from the dataset
            dim_names = []
            # Check _ARRAY_DIMENSIONS first (some xarray versions use this)
            array_dims = attrs.get("_ARRAY_DIMENSIONS")
            if array_dims is not None:
                if hasattr(array_dims, "tolist"):
                    array_dims = array_dims.tolist()
                if isinstance(array_dims, (list, tuple)):
                    dim_names = [d.decode() if isinstance(d, bytes) else d for d in array_dims]
                elif isinstance(array_dims, bytes):
                    dim_names = [array_dims.decode()]

            # If no _ARRAY_DIMENSIONS, infer from shape matching coordinates
            if not dim_names:
                for i, size in enumerate(ds.shape):
                    matched = False
                    for cname, cvals in coordinates.items():
                        if len(cvals) == size:
                            dim_names.append(cname)
                            matched = True
                            break
                    if not matched:
                        dim_names.append(f"dim_{i}")

            # Get additional metadata
            long_name = attrs.get("long_name", "")
            if isinstance(long_name, bytes):
                long_name = long_name.decode()
            units = attrs.get("units", "")
            if isinstance(units, bytes):
                units = units.decode()

            data_vars[name] = {
                "shape": list(ds.shape),
                "dtype": str(ds.dtype),
                "dimensions": dim_names,
                "long_name": long_name,
                "units": units,
            }

        return {
            "dimensions": dimensions,
            "coordinates": coordinates,
            "data_vars": data_vars,
        }

    # ------------------------------------------------------------------
    # HDF5 plot data (lazy-loaded)
    # ------------------------------------------------------------------

    def get_h5_plot_data(
        self,
        run_id: int,
        which: str,
        var_name: str,
        qubit_idx: int | None = None,
    ) -> dict | None:
        """Read a data variable from HDF5 and return Plotly-ready traces + layout."""
        try:
            import h5py
            import numpy as np
        except ImportError:
            return {"error": "h5py not installed"}

        if which not in _H5_WHICH_WHITELIST:
            # Phase 4 §4 — refuse path-traversal-shaped values up front.
            return None

        run = self.runs.get(run_id)
        if not run:
            return None

        h5_path = run.folder_path / f"{which}.h5"
        if not h5_path.exists():
            return None

        with _h5_lock_for(str(h5_path)):
            try:
                with h5py.File(h5_path, "r") as f:
                    if var_name not in f:
                        return {"error": f"Variable '{var_name}' not found"}

                    ds = f[var_name]
                    data = ds[()]
                    attrs = dict(ds.attrs)

                    # Get structure to determine dimension names
                    structure = self._parse_h5_structure(f)
                    var_info = structure.get("data_vars", {}).get(var_name, {})
                    dim_names = var_info.get("dimensions", [])

                    # Load coordinate arrays
                    coords = {}
                    for dim in dim_names:
                        if dim in f:
                            c = f[dim][()]
                            if hasattr(c, "tolist"):
                                c = c.tolist()
                            if c and isinstance(c[0], bytes):
                                c = [x.decode() for x in c]
                            coords[dim] = c

                    return self._build_plotly_traces(
                        data, dim_names, coords, var_name, qubit_idx,
                        long_name=var_info.get("long_name", ""),
                        units=var_info.get("units", ""),
                        parameters=run.parameters,
                    )
            except Exception as e:
                logger.warning("Failed to read plot data from %s: %s", h5_path, e)
                return {"error": str(e)}

    def _build_plotly_traces(
        self,
        data,
        dims: list[str],
        coords: dict[str, list],
        var_name: str,
        qubit_idx: int | None = None,
        long_name: str = "",
        units: str = "",
        parameters: dict | None = None,
    ) -> dict:
        """Build Plotly traces + layout from data array + coordinates."""
        import numpy as np

        data = np.array(data)
        traces = []
        qubit_names: list[str] = []
        layout = {"title": var_name, "template": "plotly_dark"}

        # Convert IF-range frequency coordinates to full frequency using LO
        coords = self._apply_full_freq(coords, parameters)

        if data.ndim == 1:
            # 1D line plot
            x_dim = dims[0] if dims else "index"
            x_vals = coords.get(x_dim, list(range(len(data))))
            x_label, x_vals_scaled = self._scale_axis(x_dim, x_vals)
            traces.append(
                {
                    "x": x_vals_scaled,
                    "y": data.tolist(),
                    "type": "scatter",
                    "mode": "lines+markers",
                    "name": var_name,
                }
            )
            layout["xaxis"] = {"title": x_label}
            layout["yaxis"] = {"title": var_name}

        elif data.ndim == 2:
            # Could be multi-qubit 1D or single-qubit 2D
            dim0, dim1 = dims[0], dims[1] if len(dims) >= 2 else ("dim0", "dim1")
            coord0 = coords.get(dim0, list(range(data.shape[0])))
            coord1 = coords.get(dim1, list(range(data.shape[1])))

            # Check if dim0 is a "qubit" dimension (small categorical)
            is_qubit_dim = dim0 == "qubit" or (
                len(coord0) <= 10 and all(isinstance(c, str) for c in coord0)
            )

            if is_qubit_dim:
                qubit_names = [str(c) for c in coord0]
                if qubit_idx is not None and 0 <= qubit_idx < data.shape[0]:
                    # Single qubit selected → 1D line
                    q_name = str(coord0[qubit_idx])
                    x_label, x_vals_scaled = self._scale_axis(dim1, coord1)
                    traces.append(
                        {
                            "x": x_vals_scaled,
                            "y": data[qubit_idx].tolist(),
                            "type": "scatter",
                            "mode": "lines+markers",
                            "name": q_name,
                            "customdata": [q_name] * len(x_vals_scaled),
                        }
                    )
                    layout["xaxis"] = {"title": x_label}
                    layout["yaxis"] = {"title": var_name}
                else:
                    # All qubits → multi-line plot
                    x_label, x_vals_scaled = self._scale_axis(dim1, coord1)
                    for i, qname in enumerate(coord0):
                        q_name = str(qname)
                        traces.append(
                            {
                                "x": x_vals_scaled,
                                "y": data[i].tolist(),
                                "type": "scatter",
                                "mode": "lines+markers",
                                "name": q_name,
                                "customdata": [q_name] * len(x_vals_scaled),
                            }
                        )
                    layout["xaxis"] = {"title": x_label}
                    layout["yaxis"] = {"title": var_name}
            else:
                # True 2D → heatmap
                if qubit_idx is not None:
                    # Slice if needed
                    pass
                x_label, x_vals_scaled = self._scale_axis(dim1, coord1)
                y_label, y_vals_scaled = self._scale_axis(dim0, coord0)
                traces.append(
                    {
                        "x": x_vals_scaled,
                        "y": y_vals_scaled,
                        "z": data.tolist(),
                        "type": "heatmap",
                        "colorscale": "Viridis",
                    }
                )
                layout["xaxis"] = {"title": x_label}
                layout["yaxis"] = {"title": y_label}

        elif data.ndim >= 3:
            # 3D+: slice qubit dimension (always first), then squeeze singletons.
            # Default to the FIRST qubit when none is given — there's always a
            # sensible first qubit, so a 3D var "just works" and a run-switch replay
            # that drops the qubit_idx no longer errors (matches the UI's
            # first-qubit-default intent).
            if qubit_idx is None and data.shape[0] >= 1:
                qubit_idx = 0
            if qubit_idx is not None and 0 <= qubit_idx < data.shape[0]:
                raw = data[qubit_idx]  # e.g. [1, 398] or [1, 4]
                remaining_dims = dims[1:] if len(dims) > 1 else []
                singleton = [s == 1 for s in raw.shape]
                squeezed = raw.squeeze()
                kept_dims = [d for d, is_one in zip(remaining_dims, singleton) if not is_one]
                if squeezed.ndim == 0:
                    squeezed = raw.reshape(1)
                    kept_dims = remaining_dims[:1] if remaining_dims else ["index"]
                # resolve qubit label for trace name
                qcoord = coords.get(dims[0] if dims else "qubit", [])
                q_label = str(qcoord[qubit_idx]) if qubit_idx < len(qcoord) else f"Q{qubit_idx}"
                qubit_names = [str(c) for c in qcoord] if qcoord else []
                if squeezed.ndim == 1:
                    x_dim = kept_dims[0] if kept_dims else "index"
                    x_vals = coords.get(x_dim, list(range(squeezed.shape[0])))
                    x_label, x_vals_scaled = self._scale_axis(x_dim, x_vals)
                    traces.append(
                        {
                            "x": x_vals_scaled,
                            "y": squeezed.tolist(),
                            "type": "scatter",
                            "mode": "lines+markers",
                            "name": q_label,
                            "customdata": [q_label] * len(x_vals_scaled),
                        }
                    )
                    layout["xaxis"] = {"title": x_label}
                    layout["yaxis"] = {"title": var_name}
                elif squeezed.ndim == 2:
                    d0, d1 = (kept_dims + ["dim0", "dim1"])[:2]
                    c0 = coords.get(d0, list(range(squeezed.shape[0])))
                    c1 = coords.get(d1, list(range(squeezed.shape[1])))
                    xl, xv = self._scale_axis(d1, c1)
                    yl, yv = self._scale_axis(d0, c0)
                    traces.append(
                        {"x": xv, "y": yv, "z": squeezed.tolist(),
                         "type": "heatmap", "colorscale": "Viridis"}
                    )
                    layout["xaxis"] = {"title": xl}
                    layout["yaxis"] = {"title": yl}
                else:
                    return {"error": "Data too high-dimensional to plot after qubit selection",
                            "traces": [], "layout": layout}
            else:
                return {"error": "3D+ data requires qubit_idx selection", "traces": [], "layout": layout}

        result = {"traces": traces, "layout": layout}
        if qubit_names:
            result["qubit_names"] = qubit_names
        return result

    @staticmethod
    def _apply_full_freq(
        coords: dict[str, list], parameters: dict | None
    ) -> dict[str, list]:
        """Convert intermediate-frequency coordinates to full frequency.

        If a coordinate looks like an IF (all values < 1 GHz) and the
        experiment parameters contain a LO_frequency, add the LO offset
        so the axis shows the physical RF frequency.
        """
        if not parameters:
            return coords

        # Find LO_frequency from parameters (may be nested)
        lo = None
        for key in ("LO_frequency", "lo_frequency", "LO_freq"):
            if key in parameters:
                try:
                    lo = float(parameters[key])
                except (TypeError, ValueError):
                    pass
                break

        if lo is None or lo < 1e9:
            return coords

        out = dict(coords)
        for name, vals in coords.items():
            if not vals or not isinstance(vals[0], (int, float)):
                continue
            # Check if this looks like a frequency coordinate in IF range
            is_freq = "freq" in name.lower() or "f_" in name.lower() or "if" in name.lower()
            if not is_freq:
                continue
            max_abs = max(abs(v) for v in vals)
            if max_abs < 1e9:  # IF range — add LO to get full frequency
                out[name] = [v + lo for v in vals]
        return out

    @staticmethod
    def _scale_axis(dim_name: str, values: list) -> tuple[str, list]:
        """Auto-scale axis values and return (label, scaled_values).

        Converts Hz→GHz, ns→µs, etc. for readability.
        """
        if not values or not isinstance(values[0], (int, float)):
            return dim_name, values

        vals = [float(v) for v in values]
        max_abs = max(abs(v) for v in vals) if vals else 0

        # Pick the dimension from the axis name, then source the (factor, suffix)
        # from the shared SI ladder in core.units so this auto-scaling can never
        # drift from _format_metric again. The ladder adds the kHz/Hz and s/ns
        # tiers the old hand-rolled version was missing.
        name = dim_name.lower()
        if "freq" in name or "f_" in name:
            dimension = "freq"
        elif "time" in name or name in ("t", "tau", "delay"):
            dimension = "time"
        else:
            return dim_name, values

        factor, suffix = units.pick_axis_scale(dimension, max_abs)
        return f"{dim_name} [{suffix}]", [v * factor for v in vals]

    # ------------------------------------------------------------------
    # State path
    # ------------------------------------------------------------------

    def get_quam_state_path(self, run_id: int) -> Path | None:
        """Return path to run's quam_state/ folder."""
        run = self.runs.get(run_id)
        if not run or not run.has_quam_state:
            return None
        state_dir = run.folder_path / "quam_state"
        return state_dir if state_dir.is_dir() else None

    def get_previous_run_id(
        self, run_id: int, *, require_state: bool = True
    ) -> int | None:
        """Return the run id immediately *before* ``run_id`` in run-id order.

        "Previous" is the largest run id strictly less than ``run_id`` — not
        ``run_id - 1``, which may not exist. With ``require_state`` (the default)
        only runs that carry a ``quam_state/`` snapshot are considered, so the
        prev-state diff always lands on a run it can actually load. O(log n) via
        the sorted index; returns ``None`` when there is no such run.
        """
        idx = bisect.bisect_left(self._run_ids_sorted, run_id)
        for i in range(idx - 1, -1, -1):
            cand = self._run_ids_sorted[i]
            if not require_state:
                return cand
            run = self.runs.get(cand)
            if run and run.has_quam_state:
                return cand
        return None

    def get_previous_same_experiment_id(self, run_id: int) -> int | None:
        """The nearest EARLIER run of the SAME experiment (node type), or None.

        The calibration workflow's core question — "how does this run compare
        to the last time this node ran?" — walks run-id order backwards until
        the experiment_name matches. In-memory walk over the sorted index."""
        cur = self.runs.get(run_id)
        if cur is None or not cur.experiment_name:
            return None
        idx = bisect.bisect_left(self._run_ids_sorted, run_id)
        for i in range(idx - 1, -1, -1):
            cand = self.runs.get(self._run_ids_sorted[i])
            if cand is not None and cand.experiment_name == cur.experiment_name:
                return cand.run_id
        return None

    def get_next_run_id(
        self, run_id: int, *, require_state: bool = True
    ) -> int | None:
        """Return the run id immediately *after* ``run_id`` in run-id order.

        Mirror of :meth:`get_previous_run_id` for the prev-state stepper's
        "newer" direction.
        """
        idx = bisect.bisect_right(self._run_ids_sorted, run_id)
        for i in range(idx, len(self._run_ids_sorted)):
            cand = self._run_ids_sorted[i]
            if not require_state:
                return cand
            run = self.runs.get(cand)
            if run and run.has_quam_state:
                return cand
        return None

    # ------------------------------------------------------------------
    # Tags & Bookmarks
    # ------------------------------------------------------------------

    def _load_tags(self):
        """Load quashboard_tags.json and apply to RunInfo objects.

        Reads via :mod:`safe_io` so the load is share-delete-safe on Windows
        (the tags file lives next to user-touchable data; a concurrent
        editor or sync tool that holds it open should not block our read).
        """
        if self._tags_path.exists():
            try:
                self._tags_data = safe_io.read_json(self._tags_path)
            except (OSError, ValueError) as e:
                logger.warning("Failed to read tags: %s", e)
                self._tags_data = {"bookmarks": [], "tags": {}, "notes": {}}
        else:
            self._tags_data = {"bookmarks": [], "tags": {}, "notes": {}}

        # One-time migration: legacy bookmarks → the reserved FAVORITE_TAG so the
        # ⭐ star and the tag system share one store. Write back ONLY when there's
        # something to migrate — this runs on every rescan_if_stale (line ~508),
        # so a steady-state load must not rewrite the file and thrash its mtime.
        # Lock-guarded because the load path itself doesn't hold _tags_lock.
        legacy_bookmarks = self._tags_data.get("bookmarks") or []
        if legacy_bookmarks:
            with self._tags_lock:
                tags_dict = self._tags_data.setdefault("tags", {})
                for rid in legacy_bookmarks:
                    lst = tags_dict.setdefault(str(rid), [])
                    if FAVORITE_TAG not in lst:
                        lst.append(FAVORITE_TAG)
                self._tags_data["bookmarks"] = []
                try:
                    self._save_tags()
                except OSError as e:
                    logger.warning("Favorite-tag migration write failed: %s", e)

        # Apply to RunInfo objects. `bookmarked` is now DERIVED from the favorite
        # tag (the star reflects whether the run carries FAVORITE_TAG).
        for rid_str, tags in self._tags_data.get("tags", {}).items():
            rid = int(rid_str)
            if rid in self.runs:
                self.runs[rid].tags = list(tags)
                self.runs[rid].bookmarked = FAVORITE_TAG in tags

        for rid_str, note in self._tags_data.get("notes", {}).items():
            rid = int(rid_str)
            if rid in self.runs:
                self.runs[rid].note = note

    def _save_tags(self):
        """Atomically save quashboard_tags.json.

        Uses :func:`safe_io.atomic_write_json` so the write goes through the
        shared atomic-write code path (``ReplaceFileW`` on Windows, retried
        on transient locks, fsync'd, .tmp cleaned up on failure). Raises
        :class:`OSError` on hard write failure — callers hold the
        ``_tags_lock``, so an exception unwinds the in-memory mutation
        cleanly via a try/except in the mutator.
        """
        safe_io.atomic_write_json(self._tags_path, self._tags_data)

    def toggle_bookmark(self, run_id: int) -> bool:
        """Toggle the run's "favorite" state. Returns the new state.

        Favoriting is just the reserved :data:`FAVORITE_TAG` now (the ⭐ is a
        one-click shortcut for that tag), so the star and the tag system share
        one store. RMW + persist under ``_tags_lock`` so two concurrent HTMX
        requests can't lose a toggle via interleaved mutation (red-team Phase 2
        finding §3.1).
        """
        with self._tags_lock:
            tags_dict = self._tags_data.setdefault("tags", {})
            rid_str = str(run_id)
            lst = tags_dict.setdefault(rid_str, [])
            was_fav = FAVORITE_TAG in lst
            if was_fav:
                lst.remove(FAVORITE_TAG)
                if not lst:
                    del tags_dict[rid_str]
                new_state = False
            else:
                lst.append(FAVORITE_TAG)
                new_state = True
            if run_id in self.runs:
                self.runs[run_id].tags = list(tags_dict.get(rid_str, []))
                self.runs[run_id].bookmarked = new_state
            try:
                self._save_tags()
            except (OSError, ValueError, TypeError):
                # Roll back to the prior state so the user sees the failure
                # rather than a half-applied toggle. Catch any write failure
                # (not just OSError) so a serialization error can't leave the
                # in-memory dict ahead of disk (C34).
                cur = tags_dict.setdefault(rid_str, [])
                if was_fav and FAVORITE_TAG not in cur:
                    cur.append(FAVORITE_TAG)
                elif not was_fav and FAVORITE_TAG in cur:
                    cur.remove(FAVORITE_TAG)
                if not cur:
                    tags_dict.pop(rid_str, None)
                if run_id in self.runs:
                    self.runs[run_id].tags = list(tags_dict.get(rid_str, []))
                    self.runs[run_id].bookmarked = was_fav
                raise
            return new_state

    def add_tag(self, run_id: int, tag: str) -> list[str]:
        """Add a tag to a run. Returns updated tag list."""
        with self._tags_lock:
            tags_dict = self._tags_data.setdefault("tags", {})
            rid_str = str(run_id)
            if rid_str not in tags_dict:
                tags_dict[rid_str] = []
            already_present = tag in tags_dict[rid_str]
            if not already_present:
                tags_dict[rid_str].append(tag)
            if run_id in self.runs:
                self.runs[run_id].tags = list(tags_dict[rid_str])
                self.runs[run_id].bookmarked = FAVORITE_TAG in tags_dict[rid_str]
            try:
                self._save_tags()
            except (OSError, ValueError, TypeError):
                # Any write failure rolls back the in-memory mutation (C34).
                if not already_present:
                    tags_dict[rid_str].remove(tag)
                    if run_id in self.runs:
                        self.runs[run_id].tags = list(tags_dict[rid_str])
                        self.runs[run_id].bookmarked = FAVORITE_TAG in tags_dict[rid_str]
                raise
            return list(tags_dict[rid_str])

    def remove_tag(self, run_id: int, tag: str) -> list[str]:
        """Remove a tag from a run. Returns updated tag list."""
        with self._tags_lock:
            tags_dict = self._tags_data.setdefault("tags", {})
            rid_str = str(run_id)
            removed = False
            if rid_str in tags_dict and tag in tags_dict[rid_str]:
                tags_dict[rid_str].remove(tag)
                removed = True
                if not tags_dict[rid_str]:
                    del tags_dict[rid_str]
            if run_id in self.runs:
                self.runs[run_id].tags = list(tags_dict.get(rid_str, []))
                self.runs[run_id].bookmarked = FAVORITE_TAG in self.runs[run_id].tags
            try:
                self._save_tags()
            except (OSError, ValueError, TypeError):
                # Any write failure rolls back the in-memory mutation (C34).
                if removed:
                    tags_dict.setdefault(rid_str, []).append(tag)
                    if run_id in self.runs:
                        self.runs[run_id].tags = list(tags_dict[rid_str])
                        self.runs[run_id].bookmarked = FAVORITE_TAG in tags_dict[rid_str]
                raise
            return list(tags_dict.get(rid_str, []))

    def set_note(self, run_id: int, note: str):
        """Set a note on a run."""
        with self._tags_lock:
            notes = self._tags_data.setdefault("notes", {})
            rid_str = str(run_id)
            previous = notes.get(rid_str)
            if note:
                notes[rid_str] = note
            elif rid_str in notes:
                del notes[rid_str]
            if run_id in self.runs:
                self.runs[run_id].note = note
            try:
                self._save_tags()
            except (OSError, ValueError, TypeError):
                # Any write failure rolls back the in-memory mutation (C34).
                if previous is None:
                    notes.pop(rid_str, None)
                else:
                    notes[rid_str] = previous
                if run_id in self.runs:
                    self.runs[run_id].note = previous or ""
                raise

    def list_all_tags(self) -> list[str]:
        """Return sorted list of all unique tags across all runs."""
        all_tags = set()
        for tags in self._tags_data.get("tags", {}).values():
            all_tags.update(tags)
        return sorted(all_tags)

    # ------------------------------------------------------------------
    # Key metric extraction
    # ------------------------------------------------------------------

    @staticmethod
    def _extract_key_metric(run: RunInfo) -> str:
        """Pick the most relevant fit_result value for the browser table."""
        if not run.fit_results:
            return ""

        # Get first qubit's results
        first_qubit_results = None
        for qname in sorted(run.fit_results.keys()):
            val = run.fit_results[qname]
            if isinstance(val, dict):
                first_qubit_results = val
                break

        if not first_qubit_results:
            return ""

        exp = run.experiment_name.lower()

        # Map experiment types to key metric fields
        metric_map = {
            "spectroscopy": ("frequency", "Hz"),
            "power_rabi": ("pi_amp", "V"),
            "ramsey": ("T2_star", "s"),
            "t1": ("T1", "s"),
            "iq_blob": ("readout_fidelity", "%"),
            "readout_frequency": ("frequency", "Hz"),
            "readout_amplitude": ("amplitude", "V"),
        }

        for pattern, (field_name, unit) in metric_map.items():
            if pattern in exp:
                val = first_qubit_results.get(field_name)
                if val is not None and isinstance(val, (int, float)):
                    return _format_metric(val, unit)

        # Fallback: find first numeric value that isn't "success"
        for key, val in first_qubit_results.items():
            if key == "success":
                continue
            if isinstance(val, (int, float)):
                return _format_metric(val, "")

        return ""

    @staticmethod
    def _extract_sort_scalars(run: "RunInfo") -> dict:
        """Per-fit-key sortable scalar(s) for the run-list Sort banner, computed
        ONCE at parse time (next to key_metric). For each numeric fit key we keep:
          * ``first`` — the first dict-qubit (sorted order) that has a finite value,
            so "sort by T1" agrees with the first-qubit Key Metric column; plus
          * ``max``/``min`` over all qubits, but ONLY when they differ from first,
            so the client's first/max/min toggle is instant with no round-trip.

        Wire form per key: a bare ``float`` when first==max==min (the common
        single-qubit case → smallest payload), else ``[first, max, min]``.

        INLINE numeric values only. ``bool``/NaN/inf are rejected (the gap in
        get_trend_data's plain ``isinstance(v,(int,float))``). String ``./file#key``
        refs are deliberately NOT resolved here — that npz/h5 I/O stays deferred to
        ``get_run`` so the bulk scan keeps its tens-of-ms budget; a key whose values
        are all refs simply gets no scalar (shown as a disabled badge in the UI).
        """
        fit = run.fit_results
        if not isinstance(fit, dict):
            return {}
        first_val: dict[str, float] = {}
        all_vals: dict[str, list[float]] = {}
        for qname in sorted(fit.keys()):
            qvals = fit[qname]
            if not isinstance(qvals, dict):
                continue
            for k, v in qvals.items():
                if k == "success":
                    continue
                # reject bool (True==1), non-numeric, and NaN/inf
                if type(v) is bool or not isinstance(v, (int, float)) or not math.isfinite(v):
                    continue
                fv = float(v)
                all_vals.setdefault(k, []).append(fv)
                if k not in first_val:
                    first_val[k] = fv   # first qubit (sorted) carrying this key
        out: dict[str, Any] = {}
        for k, vals in all_vals.items():
            mx, mn = max(vals), min(vals)
            out[k] = first_val[k] if mx == mn else [first_val[k], mx, mn]
        return out

    # Param keys never useful as facets — orchestration/sim plumbing, not physics
    # knobs. Dropped from filter_params so the Parameters picker stays focused.
    _PARAM_SKIP_KEYS = frozenset({
        "simulate", "simulation_duration_ns", "use_waveform_report", "timeout",
        "load_data_id", "update_state_from_GUI",
    })

    @staticmethod
    def _extract_filter_params(run: "RunInfo") -> dict:
        """Sparse map of categorical / low-cardinality params for the Sort-banner
        Parameters facet filter. Keeps only scalar bool / short-string / int values
        (e.g. reset_type, use_state_discrimination, operation, multiplexed,
        num_shots); skips floats (high-cardinality sweep knobs — exact-match
        useless), None, lists (qubits/sweeps) and dicts. The client builds key=value
        facets from this and applies its own cardinality cap, so shipping every
        qualifying int is fine. Cheap + per-run (mirrors _extract_sort_scalars)."""
        params = run.parameters
        if not isinstance(params, dict):
            return {}
        out: dict[str, Any] = {}
        for k, v in params.items():
            if k in DatasetStore._PARAM_SKIP_KEYS:
                continue
            if type(v) is bool:
                out[k] = v
            elif isinstance(v, str):
                if 0 < len(v) <= 40:        # enum-like; skip empty + long free text
                    out[k] = v
            elif isinstance(v, (int, float)):   # bool already handled above
                # numeric → client shows a min/max RANGE filter (not per-value
                # facets) for high-cardinality keys; float kept for that.
                if isinstance(v, float) and not math.isfinite(v):
                    continue
                out[k] = v
            # None / list / dict → skipped
        return out

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def run_count(self) -> int:
        return len(self.runs)

    @property
    def summary_stats(self) -> dict:
        """Return summary statistics."""
        all_qubits = set()
        with self._scan_lock:
            runs = list(self.runs.values())
        for run in runs:
            if run.qubits:
                all_qubits.update(run.qubits)
        return {
            "total_runs": len(self.runs),
            "date_range": f"{self.dates[-1]} - {self.dates[0]}" if self.dates else "",
            "experiment_types": len(self.experiment_types),
            "unique_qubits": sorted(all_qubits),
        }

    def categorize_experiments(self) -> list[dict]:
        """Group experiment types into categories for UI display.

        Returns a list of dicts: [{"label": "Readout", "experiments": [...]}, ...]
        Only includes categories that have at least one experiment.

        Categories are checked in order; the first match wins.  Put more
        specific categories (2Q, Coupler, Qubit Flux) before broader ones
        (1Q) so that e.g. ``coupler_flux`` lands in Coupler rather than 1Q.
        """
        _CATEGORIES = [
            ("Readout", [
                "readout", "iq_blob", "iq_blobs", "discriminator",
                "classification", "ro_fidelity", "resonator", "gef_readout",
                "time_of_flight",
            ]),
            ("2Q", [
                "cz", "swap", "cross_resonance", "zz_", "cnot", "iswap",
                "two_qubit", "2q", "bell_state", "confusion_matrix",
                "conditional_phase", "phase_compensation",
                "interleaved_rb", "standard_rb",
            ]),
            ("Coupler", [
                "coupler", "vs_coupler",
            ]),
            ("Qubit Flux", [
                "vs_flux", "flux_long", "flux_short", "flux_calibrat",
                "fluxtunable", "flux_tunable", "qubit_flux",
            ]),
            ("1Q", [
                "spectroscopy", "rabi", "ramsey", "t1", "t2", "echo",
                "drag", "x180", "x90", "allxy", "all_xy", "power_rabi",
                "freq_tuning", "qubit_spec", "ef_spec",
                "randomized_benchmarking", "single_qubit", "leakage",
                "charge_stabilized", "xeb",
            ]),
        ]
        categorized: dict[str, list[str]] = {cat: [] for cat, _ in _CATEGORIES}
        categorized["Other"] = []

        for exp in self.experiment_types:
            exp_lower = exp.lower()
            placed = False
            for cat_label, keywords in _CATEGORIES:
                if any(kw in exp_lower for kw in keywords):
                    categorized[cat_label].append(exp)
                    placed = True
                    break
            if not placed:
                categorized["Other"].append(exp)

        result = []
        for cat_label, _ in _CATEGORIES:
            if categorized[cat_label]:
                result.append({"label": cat_label, "experiments": categorized[cat_label]})
        if categorized["Other"]:
            result.append({"label": "Other", "experiments": categorized["Other"]})
        return result


def _format_metric(val: float, unit: str) -> str:
    """Format a numeric value with appropriate SI prefix.

    Thin wrapper over :func:`units.format_metric` (single source of truth for
    the SI ladders, shared with the qubit/pair surfaces). *unit* is one of
    ``"Hz"``, ``"s"``, ``"V"``, ``"%"`` or ``""`` (generic).
    """
    return units.format_metric(val, unit)
