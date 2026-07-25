"""Scan experiment data folders for quam_state directories and build a navigable tree.

The Workspace class discovers quam_state folders (containing state.json + wiring.json)
under one or more root directories, parses experiment metadata from sibling node.json
files, and organises the results into date-grouped trees.  Full QuamStore loading is
deferred until the user explicitly selects an entry (lazy loading with bounded cache).
"""

from __future__ import annotations

import logging
import os
import re
import threading
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from quam_state_manager.core import safe_io
from quam_state_manager.core.loader import QuamStore

# Phase 3 §2.1 — cold-scan parallelism. The per-folder ``node.json`` parse
# is pure I/O on local disk; running it across a small ThreadPoolExecutor
# turns a 10⁴-folder cold scan from a ~15-30 s UI freeze into seconds.
# Workers cap is generous: file I/O scales with parallelism even past
# CPU count.
_SCAN_DIR_CAP = 50_000   # discovery walk bound: cycles are inode-guarded, scope is not
_SCAN_PARSE_WORKERS = min(32, (os.cpu_count() or 4) * 4)

logger = logging.getLogger(__name__)

_DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}")
_FOLDER_RE = re.compile(r"^#?(\d+)_(.+?)_(\d{6})$")

MAX_CACHED_STORES = 10


@dataclass
class ExperimentEntry:
    """Metadata for one discovered quam_state folder."""

    folder_path: Path
    quam_state_path: Path
    run_id: int | None
    experiment_name: str
    timestamp: str
    status: str
    qubits: list[str]
    qubit_pairs: list[str]
    outcomes: dict[str, str]
    parent_ids: list[int]
    date_str: str
    is_standalone: bool

    @property
    def short_label(self) -> str:
        """Human-readable label for sidebar display."""
        if self.is_standalone:
            return self.experiment_name
        prefix = f"#{self.run_id}" if self.run_id is not None else ""
        time_part = ""
        if self.timestamp and "T" in self.timestamp:
            try:
                time_part = self.timestamp.split("T")[1][:5]
            except (IndexError, ValueError):
                pass
        qubit_summary = ",".join(self.qubits[:4])
        if len(self.qubits) > 4:
            qubit_summary += f"..+{len(self.qubits) - 4}"
        parts = [p for p in [prefix, self.experiment_name, time_part, qubit_summary, self.status] if p]
        return "  ".join(parts)


def _normalize_pair_members(pair: str) -> list[str]:
    """Split a pair name into member qubit names, re-prefixing bare suffixes.

    QM names a 2Q pair compactly as ``q0-1`` (= qubits ``q0`` & ``q1``), sharing
    the ``q`` prefix; a plain ``split("-")`` yields ``["q0", "1"]`` and the second
    member loses its prefix. Re-prefix any token that starts with a digit (i.e. has
    no alpha prefix of its own) with the first member's leading alpha prefix.
    Fully-qualified forms like ``qA2-qA1`` are untouched (each token already carries
    its own prefix)."""
    tokens = [t.strip() for t in str(pair).split("-") if t.strip()]
    if not tokens:
        return []
    m0 = re.match(r"^([A-Za-z_]+)", tokens[0])
    prefix = m0.group(1) if m0 else ""
    members = []
    for tok in tokens:
        if prefix and re.match(r"^[0-9]", tok):  # bare numeric suffix -> re-prefix
            tok = prefix + tok
        members.append(tok)
    return members


def _with_pair_qubits(qubits: list, raw_pairs) -> tuple[list, list]:
    """Normalize `qubit_pairs` and fold their member qubits into `qubits`.

    A 2-qubit run carries `qubit_pairs` (e.g. ["qA2-qA1"] or compact ["q0-1"]) and
    no `qubits`, so we keep the pairs as their own list AND add each pair's member
    qubits (normalized via :func:`_normalize_pair_members`, deduped + order-
    preserving) to `qubits`, so a qubit search/filter surfaces 2Q runs too — by the
    real member names (``q0``, ``q1``), not the bare ``1``. Returns (qubits,
    qubit_pairs); the pair strings themselves are returned UNCHANGED for display."""
    pairs = raw_pairs or []
    if isinstance(pairs, str):
        pairs = [pairs]
    if isinstance(pairs, list) and pairs and isinstance(pairs[0], list):
        pairs = [p for sub in pairs for p in sub]
    if not isinstance(pairs, list):
        pairs = []
    pairs = [str(p) for p in pairs]
    seen = set(qubits)
    for pair in pairs:
        for m in _normalize_pair_members(pair):
            if m and m not in seen:
                qubits.append(m)
                seen.add(m)
    return qubits, pairs


@dataclass
class DateGroup:
    """All experiments for a single date, sorted by run_id or timestamp."""

    date_str: str
    entries: list[ExperimentEntry] = field(default_factory=list)

    @property
    def count(self) -> int:
        return len(self.entries)

    def sorted_entries(self) -> list[ExperimentEntry]:
        """Return entries sorted by run_id (nulls last), then timestamp."""
        return sorted(
            self.entries,
            key=lambda e: (e.run_id if e.run_id is not None else float("inf"), e.timestamp),
        )


class Workspace:
    """Manages multiple root folders and their experiment trees.

    Usage::

        ws = Workspace()
        ws.add_root(Path("data/project_name"))
        ws.add_root(Path("quam_states_arv/quam_state_examplechip_variantb"))

        for entry in ws.get_flat_list(experiment_filter="spectroscopy"):
            print(entry.short_label)

        store = ws.load_store(entry.quam_state_path)
    """

    def __init__(self) -> None:
        # Serialises the root-mutating methods so the dedup check-then-append in
        # add_root is atomic (two concurrent adds of one path used to both pass
        # the `path in root_folders` check and both append a duplicate root).
        # Readers stay lock-free: they iterate self.tree, which mutators only ever
        # replace by atomic attribute rebind, never mutate in place.
        self._lock = threading.RLock()
        self.root_folders: list[Path] = []
        self.tree: dict[str, list[DateGroup]] = {}
        self._entries_by_path: dict[Path, ExperimentEntry] = {}
        self._loaded_stores: OrderedDict[Path, QuamStore] = OrderedDict()
        # (state_mtime, wiring_mtime) at store-load time, per cached path —
        # lets a cache hit detect an out-of-band file replacement with two
        # os.stat calls and reload instead of serving stale content.
        self._loaded_store_mtimes: dict[Path, tuple[float, float] | None] = {}
        # root path str → max root/immediate-subdir mtime OBSERVED at last scan.
        # Staleness compares this mtime-to-mtime (see _is_root_stale), never
        # against local time.time(): a clock-skewed network mount would freeze
        # (server behind) or thrash (server ahead) the sidebar otherwise.
        self._scan_times: dict[str, float] = {}
        self._version = 0  # bumped on any tree change; drives the sidebar's version-gated refresh

    @property
    def version(self) -> int:
        """Monotonic counter bumped whenever the workspace tree changes
        (root added/removed or a stale root rescanned). The sidebar polls this
        cheaply and re-fetches the tree only when it actually changed, instead
        of rebuilding the DOM every 60 s regardless."""
        return self._version

    # ------------------------------------------------------------------
    # Root management
    # ------------------------------------------------------------------

    def add_root(self, path: str | Path) -> list[ExperimentEntry]:
        """Add a root folder and scan it for quam_state directories.

        Returns the list of discovered ExperimentEntry objects.
        """
        # expanduser BEFORE resolve: a literal "~/data" otherwise becomes
        # $CWD/~/data, gets persisted to workspace_roots.json, and fails on
        # every later session.
        path = Path(path).expanduser().resolve()
        with self._lock:
            existing = self._find_registered_root(path)
            if existing is not None:
                logger.warning("Root folder already added: %s", existing)
                return self._entries_for_root(existing)

            # Sample the staleness probe BEFORE the walk (same reasoning as
            # DatasetStore's pre-walk cursor): a folder landing mid-scan bumps
            # an mtime above this value, so the next rescan_if_stale catches it
            # instead of swallowing it as already-seen.
            pre_scan_probe = _root_mtime_probe(path)
            self.root_folders.append(path)
            entries = _scan_root(path)
            groups = _group_by_date(entries)
            # Rebind self.tree to a FRESH dict instead of mutating in place: the sidebar
            # poll / manual refresh mutate the tree while every page render iterates it
            # (all_entries + _sidebar_tree.html) with no lock. An in-place key insert/pop
            # during iteration raises 'dict changed size'. A single attribute rebind is
            # atomic, so a concurrent reader keeps iterating the OLD dict unharmed.
            self.tree = {**self.tree, str(path): groups}
            self._scan_times[str(path)] = (
                pre_scan_probe if pre_scan_probe is not None else 0.0
            )
            for entry in entries:
                self._entries_by_path[entry.quam_state_path.resolve()] = entry

            self._version += 1
            logger.info("Scanned %s: found %d quam_state folders", path, len(entries))
            return entries

    def _find_registered_root(self, path: Path) -> Path | None:
        """The already-registered root that IS *path* — exact match, or same
        ``(st_dev, st_ino)`` when both exist. On a case-insensitive FS (macOS
        default), two case-variant spellings of ONE folder would otherwise
        register as two roots: duplicate entries, and downstream two separate
        per-root caches/locks for one physical directory (last-writer-wins).
        Exact-path dedup stays as the fallback for missing/unstatable paths.
        Caller holds ``self._lock``."""
        if path in self.root_folders:
            return path
        try:
            st = os.stat(path)
            key = (st.st_dev, st.st_ino)
        except OSError:
            return None
        for existing in self.root_folders:
            try:
                est = os.stat(existing)
            except OSError:
                continue
            if (est.st_dev, est.st_ino) == key:
                return existing
        return None

    def remove_root(self, path: str | Path) -> None:
        """Remove a root folder and evict all its cached stores."""
        path = Path(path).expanduser().resolve()
        with self._lock:
            registered = self._find_registered_root(path)
            if registered is None:
                return
            self.root_folders.remove(registered)
            key = str(registered)
            removed_entries = []
            for group in self.tree.get(key, []):
                removed_entries.extend(group.entries)
            # Atomic rebind (see add_root) — never pop in place while readers iterate.
            self.tree = {k: v for k, v in self.tree.items() if k != key}
            for entry in removed_entries:
                resolved = entry.quam_state_path.resolve()
                self._entries_by_path.pop(resolved, None)
                self._loaded_stores.pop(resolved, None)
            self._version += 1

    def rescan_root(self, path: str | Path) -> list[ExperimentEntry]:
        """Re-scan a root folder (e.g. after new experiments are added)."""
        path = Path(path).resolve()
        with self._lock:   # remove+add as one unit (RLock: nested calls re-enter)
            self.remove_root(path)
            return self.add_root(path)

    def rescan_all(self) -> None:
        """Force-rescan every root regardless of mtime (used by the manual Refresh button)."""
        for root in list(self.root_folders):
            self.rescan_root(root)

    def rescan_if_stale(self) -> bool:
        """Check each root for new/modified subdirectories; rescan only if stale.

        Cost when nothing changed: ~(1 + N_date_folders) stat() calls per root — <1 ms.
        Cost when stale: full os.walk() + JSON parse for that root (~50–200 ms typical).
        Returns True if any root was rescanned.
        """
        rescanned = False
        for root in list(self.root_folders):
            if self._is_root_stale(root):
                logger.debug("Stale root detected, rescanning: %s", root)
                self.rescan_root(root)
                rescanned = True
        return rescanned

    def _is_root_stale(self, root: Path) -> bool:
        """True if the observed root/subdir mtimes moved since the last scan.

        Compares the CURRENT max root/immediate-subdir mtime against the one
        observed at scan time — mtime-to-mtime, never against this machine's
        ``time.time()`` (mirrors DatasetStore's ``_current_mtime`` design). A
        network mount whose server clock runs behind ours would otherwise
        never look stale (new-run mtimes forever below our wall clock); one
        running ahead would look stale on every poll and thrash full rescans.
        """
        cur = _root_mtime_probe(root)
        if cur is None:
            return False   # transiently unreadable — keep the current tree
        last = self._scan_times.get(str(root))
        return last is None or cur != last

    # ------------------------------------------------------------------
    # Entry lookup
    # ------------------------------------------------------------------

    def get_entry(self, quam_state_path: str | Path) -> ExperimentEntry | None:
        """Look up metadata for a specific quam_state folder."""
        return self._entries_by_path.get(Path(quam_state_path).resolve())

    @property
    def all_entries(self) -> list[ExperimentEntry]:
        """All discovered entries across all roots, sorted by date then run_id."""
        result = []
        for groups in self.tree.values():
            for group in groups:
                result.extend(group.sorted_entries())
        return result

    # ------------------------------------------------------------------
    # Lazy QuamStore loading
    # ------------------------------------------------------------------

    def load_store(self, quam_state_path: str | Path) -> QuamStore:
        """Lazy-load a QuamStore.  Cached with LRU eviction (max 10 stores).

        A cache hit re-stats the folder (two ``os.stat`` calls) and refreshes
        the entry if the files were replaced out-of-band since it was
        cached — the cache is keyed by path, so without the check it would
        keep serving the old content for the whole session. The refresh
        SWAPS in a freshly-built store rather than ``reload()``-ing the
        cached one in place: cached stores are read lock-free by concurrent
        render threads (e.g. two /compare requests), and an in-place reload
        would mutate state/wiring under a mid-render reader.
        """
        resolved = Path(quam_state_path).resolve()
        if resolved in self._loaded_stores:
            self._loaded_stores.move_to_end(resolved)
            store = self._loaded_stores[resolved]
            try:
                cur = safe_io.state_wiring_mtimes(resolved)
            except OSError:
                cur = None   # transiently unreadable — serve the cached store
            if cur is not None and cur != self._loaded_store_mtimes.get(resolved):
                try:
                    fresh = QuamStore(resolved)
                except (OSError, ValueError):
                    logger.warning("Stale-store refresh failed for %s", resolved,
                                   exc_info=True)
                else:
                    self._loaded_stores[resolved] = fresh
                    self._loaded_store_mtimes[resolved] = cur
                    store = fresh
                    logger.info("Refreshed cached store after live change: %s", resolved)
            return store

        try:
            mtimes = safe_io.state_wiring_mtimes(resolved)
        except OSError:
            mtimes = None
        store = QuamStore(resolved)
        self._loaded_stores[resolved] = store
        self._loaded_store_mtimes[resolved] = mtimes
        self._loaded_stores.move_to_end(resolved)

        while len(self._loaded_stores) > MAX_CACHED_STORES:
            evicted_path, _ = self._loaded_stores.popitem(last=False)
            self._loaded_store_mtimes.pop(evicted_path, None)
            logger.debug("Evicted cached store: %s", evicted_path)

        return store

    def evict_store(self, quam_state_path: str | Path) -> None:
        """Manually evict a cached store (e.g. after external file change)."""
        resolved = Path(quam_state_path).resolve()
        self._loaded_stores.pop(resolved, None)
        self._loaded_store_mtimes.pop(resolved, None)

    # ------------------------------------------------------------------
    # Filtering
    # ------------------------------------------------------------------

    def get_flat_list(
        self,
        root: str | Path | None = None,
        date_filter: str | None = None,
        experiment_filter: str | None = None,
        qubit_filter: str | None = None,
        status_filter: str | None = None,
    ) -> list[ExperimentEntry]:
        """Return a filtered list of entries for the sidebar tree.

        All filters are case-insensitive. Multiple filters are AND-combined.
        """
        if root is not None:
            keys = [str(Path(root).resolve())]
        else:
            keys = list(self.tree.keys())

        results: list[ExperimentEntry] = []
        for key in keys:
            for group in self.tree.get(key, []):
                for entry in group.sorted_entries():
                    if not _matches(entry, date_filter, experiment_filter, qubit_filter, status_filter):
                        continue
                    results.append(entry)
        return results

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _entries_for_root(self, path: Path) -> list[ExperimentEntry]:
        key = str(path)
        result = []
        for group in self.tree.get(key, []):
            result.extend(group.entries)
        return result

    def __repr__(self) -> str:
        total = sum(g.count for groups in self.tree.values() for g in groups)
        return f"Workspace(roots={len(self.root_folders)}, entries={total}, cached_stores={len(self._loaded_stores)})"


# ======================================================================
# Scanning logic (module-level, stateless)
# ======================================================================


def _root_mtime_probe(root: Path) -> float | None:
    """Newest mtime of *root* + its immediate subdirs, as observed right now.

    Stored per-root at scan time and compared mtime-to-mtime by
    :meth:`Workspace._is_root_stale`, so staleness detection is immune to
    clock skew between this machine and a network mount's server. ``None``
    when the root itself is unreadable (transient blip ≠ stale).
    """
    try:
        best = root.stat().st_mtime
        for child in root.iterdir():
            if not child.is_dir():
                continue
            try:
                mt = child.stat().st_mtime
            except OSError:
                continue
            if mt > best:
                best = mt
        return best
    except OSError:
        return None


def _scan_root(root: Path) -> list[ExperimentEntry]:
    """Recursively find all quam_state folders under *root* and parse metadata.

    Two-pass to keep the parse hot loop parallel (Phase 3 §2.1):

    1. Discovery (single-threaded ``os.walk``) collects every
       ``quam_state`` folder under *root*. This is cheap — directory
       iteration only, no per-file I/O beyond ``state.json`` +
       ``wiring.json`` existence checks via ``_is_quam_state_folder``.
       ``followlinks=True``: symlinked date/run archives are normal on
       POSIX and DatasetStore's ``iterdir``-based walk already follows
       them — ``followlinks=False`` silently hid the same folders from
       the workspace sidebar. Loop safety (the reason links used to be
       pinned off, Phase 5 §4.3) now comes from a visited set keyed on
       each dir's resolved ``(st_dev, st_ino)``: a symlink or NTFS
       junction cycle terminates at its first revisit, and two paths
       reaching one physical dir are discovered only once.
    2. Parse (``ThreadPoolExecutor``) reads ``node.json`` per folder in
       parallel. Per-folder cost is dominated by ``safe_io.read_json``;
       fanning across ~32 workers turns a 10⁴-folder cold scan from a
       ~30 s UI freeze into a few seconds.
    """
    if _is_quam_state_folder(root):
        return [_make_standalone_entry(root)]

    # Discovery pass.
    candidates: list[Path] = []
    visited: set[tuple[int, int]] = set()
    for dirpath, dirnames, _filenames in os.walk(root, followlinks=True):
        if len(visited) >= _SCAN_DIR_CAP:
            # The inode guard stops CYCLES, not scope — a symlink escaping to a
            # huge tree (/, $HOME) would otherwise walk the whole filesystem.
            logger.warning(
                "workspace scan of %s stopped at %d directories — a symlink may "
                "point at a very large tree; %d quam_state folders found so far",
                root, _SCAN_DIR_CAP, len(candidates))
            break
        dp = Path(dirpath)
        try:
            st = os.stat(dirpath)   # follows symlinks → the physical dir's identity
        except OSError:
            dirnames.clear()
            continue
        key = (st.st_dev, st.st_ino)
        if key in visited:
            dirnames.clear()   # cycle / duplicate route to a dir already walked
            continue
        visited.add(key)
        if dp.name == "quam_state" and _is_quam_state_folder(dp):
            candidates.append(dp)
            dirnames.clear()

    if not candidates:
        return []

    # Parse pass — bounded parallelism. ``ThreadPoolExecutor.map``
    # preserves input order so the resulting list is reproducible.
    workers = min(_SCAN_PARSE_WORKERS, len(candidates))
    with ThreadPoolExecutor(max_workers=workers) as ex:
        return list(ex.map(_parse_experiment_folder, candidates))


def _is_quam_state_folder(path: Path) -> bool:
    """Check if a folder contains both state.json and wiring.json."""
    return (path / "state.json").is_file() and (path / "wiring.json").is_file()


def _parse_experiment_folder(quam_state_path: Path) -> ExperimentEntry:
    """Parse metadata from node.json (if present) in the parent folder."""
    experiment_folder = quam_state_path.parent
    node_json_path = experiment_folder / "node.json"

    if not node_json_path.is_file():
        return _make_standalone_entry(quam_state_path)

    # Workspace experiment folders can include a chip whose state.json is
    # currently being written by an active experiment program. ``safe_io``
    # opens the file with FILE_SHARE_DELETE on Windows so our read never
    # blocks the writer's atomic save (the same defence applied to live
    # quam_state in core.safe_io).
    try:
        node = safe_io.read_json(node_json_path)
    except (safe_io.LiveFileError, FileNotFoundError, ValueError, OSError) as exc:
        logger.warning("Failed to parse %s: %s", node_json_path, exc)
        return _make_standalone_entry(quam_state_path)

    metadata = node.get("metadata", {})
    data = node.get("data", {})
    params_model = data.get("parameters", {}).get("model", {})

    run_id = node.get("id")
    experiment_name = metadata.get("name", experiment_folder.name)
    timestamp = node.get("created_at", "")
    status = metadata.get("status", "unknown")
    qubits = params_model.get("qubits", [])
    if qubits is None:
        qubits = []
    if isinstance(qubits, list) and qubits and isinstance(qubits[0], list):
        qubits = [q for sublist in qubits for q in sublist]
    if not isinstance(qubits, list):
        qubits = [qubits]
    # Qubit pairs (2Q experiments) — kept as their own field AND their member
    # qubits are folded into `qubits` so a qubit search (qA2) also finds 2Q runs
    # on the pair qA2-qA1 (and the row stops showing "–").
    qubits, qubit_pairs = _with_pair_qubits(qubits, params_model.get("qubit_pairs"))
    outcomes = data.get("outcomes", {})
    if not isinstance(outcomes, dict):
        outcomes = {}
    parent_ids = node.get("parents", [])
    if not isinstance(parent_ids, list):
        parent_ids = []
    date_str = _extract_date(timestamp, experiment_folder)

    return ExperimentEntry(
        folder_path=experiment_folder,
        quam_state_path=quam_state_path,
        run_id=int(run_id) if run_id is not None else None,
        experiment_name=experiment_name,
        timestamp=timestamp,
        status=status,
        qubits=qubits,
        qubit_pairs=qubit_pairs,
        outcomes=outcomes,
        parent_ids=[int(p) for p in parent_ids if isinstance(p, (int, float))],
        date_str=date_str,
        is_standalone=False,
    )


def _make_standalone_entry(quam_state_path: Path) -> ExperimentEntry:
    """Create an entry for a standalone quam_state folder (no node.json)."""
    if quam_state_path.name == "quam_state":
        folder = quam_state_path.parent
        name = folder.name
    else:
        folder = quam_state_path
        name = quam_state_path.name

    try:
        mtime = (quam_state_path / "state.json").stat().st_mtime
        ts = datetime.fromtimestamp(mtime).isoformat()
    except OSError:
        ts = ""

    return ExperimentEntry(
        folder_path=folder,
        quam_state_path=quam_state_path,
        run_id=None,
        experiment_name=name,
        timestamp=ts,
        status="standalone",
        qubits=[],
        qubit_pairs=[],
        outcomes={},
        parent_ids=[],
        date_str=_extract_date(ts, folder),
        is_standalone=True,
    )


def _extract_date(timestamp: str, folder: Path) -> str:
    """Extract a YYYY-MM-DD date string from a timestamp or folder path."""
    if timestamp:
        match = _DATE_RE.search(timestamp)
        if match:
            return match.group()

    for part in folder.parts:
        match = _DATE_RE.search(part)
        if match:
            return match.group()

    return "unknown"


def _group_by_date(entries: list[ExperimentEntry]) -> list[DateGroup]:
    """Group entries by date_str and return a date-sorted DateGroup list.

    Each group's ``entries`` are sorted by run_id (numeric, nulls last) then
    timestamp — the SAME key as :meth:`DateGroup.sorted_entries`. The sidebar
    tree renders ``dg.entries`` directly (capped), so without this the runs were
    ordered by FOLDER NAME ("#45_…"), where a single-digit "#4_…" sorts AFTER
    "#45_…" because '_' (0x5F) > the digits — scattering #4–#9 into the middle
    of the list instead of at the front. Sorting at the source keeps the
    sidebar, the date-filter, and "show all N" all in numeric order."""
    groups_dict: dict[str, DateGroup] = {}
    for entry in entries:
        key = entry.date_str
        if key not in groups_dict:
            groups_dict[key] = DateGroup(date_str=key)
        groups_dict[key].entries.append(entry)
    for group in groups_dict.values():
        group.entries.sort(
            key=lambda e: (e.run_id if e.run_id is not None else float("inf"),
                           e.timestamp))
    return sorted(groups_dict.values(), key=lambda g: g.date_str)


def _matches(
    entry: ExperimentEntry,
    date_filter: str | None,
    experiment_filter: str | None,
    qubit_filter: str | None,
    status_filter: str | None,
) -> bool:
    """Check if an entry passes all filters (case-insensitive, AND logic)."""
    if date_filter and not entry.date_str.startswith(date_filter):
        return False
    if experiment_filter and experiment_filter.lower() not in entry.experiment_name.lower():
        return False
    if qubit_filter:
        qf = qubit_filter.lower()
        if not any(qf == q.lower() for q in entry.qubits):
            return False
    if status_filter and status_filter.lower() != entry.status.lower():
        return False
    return True
