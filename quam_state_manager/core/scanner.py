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

import hashlib
import json

from quam_state_manager.core import safe_io
from quam_state_manager.core.loader import QuamStore

# Phase 3 §2.1 — cold-scan parallelism. The per-folder ``node.json`` parse
# is pure I/O on local disk; running it across a small ThreadPoolExecutor
# turns a 10⁴-folder cold scan from a ~15-30 s UI freeze into seconds.
# Workers cap is generous: file I/O scales with parallelism even past
# CPU count.
_SCAN_DIR_CAP = 50_000   # discovery walk bound: cycles are inode-guarded, scope is not

# docs/105 #9: roots whose LAST discovery walk hit _SCAN_DIR_CAP. The cap
# used to be a log line only — runs silently absent from the sidebar with no
# UI trace (the docs/94 rule: a silent cap must surface an honest line).
# Keyed by str(resolved root); read by the sidebar tree render.
_TRUNCATED_ROOTS: set = set()


def root_scan_truncated(root) -> bool:
    """True when *root*'s last discovery walk stopped at _SCAN_DIR_CAP."""
    return str(root) in _TRUNCATED_ROOTS
_SCAN_PARSE_WORKERS = min(32, (os.cpu_count() or 4) * 4)

# docs/142: did the LAST _discover walk cross any symlink/junction? When
# False, every discovered path under a pre-resolved root is already
# canonical and per-entry resolve() can be skipped wholesale. Best-effort
# (concurrent scans may interleave; a stale True only costs speed, and a
# stale False cannot happen for the reading caller because each caller reads
# it immediately after its own walk on the same thread).
_DISCOVER_LINKS_SEEN = False

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
    # resolve() result cache — rescan_root keys _entries_by_path by resolved
    # path, and resolving 2x2,652 entries measured 3.3 s of a 3.5 s refresh
    # (nt._getfinalpathname walks the filesystem per component). Stamped
    # lazily on first use; reused entries carry it across rescans.
    qs_resolved: object = None
    # the RUN FOLDER mtime at parse time — what lets an incremental rescan
    # re-parse only entries whose folder actually moved (docs/126 r3: the
    # manual Refresh re-walked and re-parsed all 2,655 runs to discover that
    # nothing changed). Any write inside the run — node.json rewritten on
    # completion, quam_state created or deleted, figures landing — bumps it.
    run_mtime: float = 0.0
    # docs/142 listing-first scan: True on a STUB entry built from folder
    # names alone (run_id/name/time/date) whose node.json has not been read
    # yet -- status/qubits/outcomes are placeholders until the background
    # hydration pass replaces the object. Never True on a parsed entry.
    needs_parse: bool = False

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
        # Staleness bookkeeping (r13 — "structure spine" probe): per root we
        # remember the DIRECTORY SPINE observed at scan time (every run
        # folder's parent + all its ancestors up to the root — the dirs whose
        # mtime moves when a run/date/chip appears at ANY depth) and the
        # {dir → mtime} map observed for it. Staleness compares maps
        # mtime-to-mtime, never against local time.time(): a clock-skewed
        # network mount would freeze (server behind) or thrash (server ahead)
        # the sidebar otherwise. The map compare also survives one
        # future-dated sibling pinning a max() forever (the old aggregate
        # probe's blind spot) and depth-2+ layouts (root/<chip>/<date>/#N —
        # the old probe statted only root+children, so a new run inside an
        # existing date dir of a grandparent-registered root was invisible
        # until the next new DAY).
        self._scan_spines: dict[str, list[str]] = {}
        self._scan_probes: dict[str, dict[str, float]] = {}
        self._version = 0  # bumped on any tree change; drives the sidebar's version-gated refresh
        # docs/142: root keys whose background node.json hydration is still
        # running (listing-first add_root). Read by the sidebar to render an
        # honest "still indexing" note; emptied (with a version bump) when
        # the hydration thread finishes.
        self._hydrating: set[str] = set()
        # docs/142: optional directory for the persistent per-root listing
        # cache (set by the web app to instance/workspace_cache). None (the
        # default, and what every test gets) disables caching entirely.
        self.cache_dir: Path | None = None

    @property
    def version(self) -> int:
        """Monotonic counter bumped whenever the workspace tree changes
        (root added/removed or a stale root rescanned). The sidebar polls this
        cheaply and re-fetches the tree only when it actually changed, instead
        of rebuilding the DOM every 60 s regardless."""
        return self._version

    def hydrating_roots(self) -> set[str]:
        """docs/142: roots whose listing-first scan is still parsing
        node.json in the background. While non-empty, entry status/qubit
        fields are placeholders and qubit:/status: sidebar filters are
        incomplete -- render an honest note, never pretend."""
        with self._lock:
            return set(self._hydrating)

    # ------------------------------------------------------------------
    # Root management
    # ------------------------------------------------------------------

    def add_root(self, path: str | Path, *,
                 defer_parse: bool = False) -> list[ExperimentEntry]:
        """Add a root folder and scan it for quam_state directories.

        Returns the list of discovered ExperimentEntry objects.

        docs/142 ``defer_parse=True`` (listing-first): only the directory
        walk runs before this returns -- entries are stubs built from folder
        names (run id, experiment name, time, date all live in the
        ``#<id>_<name>_<HHMMSS>`` convention), and the ~O(N) node.json parse
        happens on a daemon thread that swaps parsed entries in and bumps
        ``version`` when done (the sidebar's version-gated poll re-renders).
        At a customer's 5,000-run archive the parse pass alone was ~3.6 s
        warm -- and every first paint of every session paid it.
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

            # Sample a shallow probe BEFORE the walk (same reasoning as
            # DatasetStore's pre-walk cursor): a folder landing mid-scan bumps
            # an mtime above the recorded value, so the next rescan_if_stale
            # catches it instead of swallowing it as already-seen.
            pre_probe = _probe_dirs(_shallow_dirs(path))
            self.root_folders.append(path)
            deferred_candidates: list[Path] | None = None
            cached = None
            if defer_parse and not _is_quam_state_folder(path):
                cached = self._load_listing_cache(path)
                if cached is not None:
                    entries, cached_spine, cached_probe = cached
                else:
                    _TRUNCATED_ROOTS.discard(str(path))
                    deferred_candidates = _discover(path)
                    entries = [_stub_entry(c) for c in deferred_candidates]
                    if not _DISCOVER_LINKS_SEEN:
                        # link-free walk under a resolved root: paths are
                        # already canonical -- resolve() would be 5,000 no-ops
                        for e in entries:
                            e.qs_resolved = e.quam_state_path
            else:
                entries = _scan_root(path)
            groups = _group_by_date(entries)
            # Rebind self.tree to a FRESH dict instead of mutating in place: the sidebar
            # poll / manual refresh mutate the tree while every page render iterates it
            # (all_entries + _sidebar_tree.html) with no lock. An in-place key insert/pop
            # during iteration raises 'dict changed size'. A single attribute rebind is
            # atomic, so a concurrent reader keeps iterating the OLD dict unharmed.
            self.tree = {**self.tree, str(path): groups}
            if cached is not None:
                # the RECORDED probe, not a fresh one: staleness must compare
                # today's disk against what the cached listing actually saw
                spine, probe = cached_spine, cached_probe
            else:
                spine = _spine_of(path, entries)
                probe = _probe_dirs(spine)
                for d, mt in pre_probe.items():    # keep the pre-walk guarantee
                    if d in probe:
                        probe[d] = mt
            self._scan_spines[str(path)] = spine
            self._scan_probes[str(path)] = probe
            memo: dict = {}
            for entry in entries:
                if entry.qs_resolved is None:
                    entry.qs_resolved = _fast_resolve(entry.quam_state_path, memo)
                self._entries_by_path[entry.qs_resolved] = entry

            self._version += 1
            if deferred_candidates is not None:
                self._hydrating.add(str(path))
                threading.Thread(
                    target=self._hydrate_root,
                    args=(path, list(entries)),
                    name=f"ws-hydrate-{path.name}", daemon=True).start()
            elif cached is not None:
                threading.Thread(
                    target=self._verify_cached_root, args=(path,),
                    name=f"ws-verify-{path.name}", daemon=True).start()
            mode = (" (listing-first, hydrating)" if deferred_candidates is not None
                    else " (from listing cache, verifying)" if cached is not None
                    else "")
            logger.info("Scanned %s: found %d quam_state folders%s", path,
                        len(entries), mode)
            return entries

    _LISTING_CACHE_V = 1
    _ENTRY_FIELDS = ("run_id", "experiment_name", "timestamp", "status",
                     "qubits", "qubit_pairs", "outcomes", "parent_ids",
                     "date_str", "is_standalone", "run_mtime")

    def _cache_path(self, root: Path) -> Path | None:
        if self.cache_dir is None:
            return None
        key = hashlib.sha1(str(root).lower().encode("utf-8")).hexdigest()[:16]
        return self.cache_dir / f"ws_{key}.json"

    def _load_listing_cache(self, root: Path):
        """docs/142: parsed listing of *root* from a previous session, or
        ``None``. Returns ``(entries, spine, probe)`` -- entries fully parsed
        (never stubs). Any shape problem or the slightest doubt reads as a
        miss; the cache is an accelerator, never a source of truth (the
        background staleness verify runs immediately after a hit)."""
        p = self._cache_path(root)
        if p is None:
            return None
        try:
            raw = json.loads(p.read_text(encoding="utf-8"))
            if raw.get("v") != self._LISTING_CACHE_V:
                return None
            if raw.get("root") != str(root):
                return None
            entries = []
            for row in raw["entries"]:
                fp = Path(row["folder"])
                entries.append(ExperimentEntry(
                    folder_path=fp,
                    quam_state_path=Path(row["qs"]),
                    **{f: row[f] for f in self._ENTRY_FIELDS}))
            spine = [str(d) for d in raw["spine"]]
            probe = {str(k): float(v) for k, v in raw["probe"].items()}
            if raw.get("truncated"):
                _TRUNCATED_ROOTS.add(str(root))
            return entries, spine, probe
        except Exception:
            return None

    def _save_listing_cache(self, root_key: str) -> None:
        """Persist the CURRENT parsed listing of one root. Skipped while any
        entry is still a stub (a cache of placeholders would poison the next
        session). Failures are logged and ignored -- the cache is optional."""
        try:
            with self._lock:
                p = self._cache_path(Path(root_key))
                if p is None:
                    return
                groups = self.tree.get(root_key)
                if groups is None:
                    return
                entries = [e for g in groups for e in g.entries]
                if any(e.needs_parse for e in entries):
                    return
                payload = {
                    "v": self._LISTING_CACHE_V,
                    "root": root_key,
                    "truncated": root_key in _TRUNCATED_ROOTS,
                    "spine": self._scan_spines.get(root_key, []),
                    "probe": self._scan_probes.get(root_key, {}),
                    "entries": [dict(
                        folder=str(e.folder_path), qs=str(e.quam_state_path),
                        **{f: getattr(e, f) for f in self._ENTRY_FIELDS})
                        for e in entries],
                }
            p.parent.mkdir(parents=True, exist_ok=True)
            safe_io.atomic_write_json(p, payload)
        except Exception:
            logger.warning("listing cache save for %s failed", root_key,
                           exc_info=True)

    def _verify_cached_root(self, root: Path) -> None:
        """docs/142: background half of a cache-served ``add_root`` -- one
        ordinary staleness check. Unchanged disk (the common case) costs a
        handful of stats; anything moved takes the existing incremental
        rescan path, which republishes and bumps ``version``."""
        try:
            if self._is_root_stale(root):
                self.rescan_root(root)
                self._save_listing_cache(str(root))
        except Exception:
            logger.warning("cached-root verify of %s failed", root, exc_info=True)

    def _hydrate_root(self, root: Path, stubs: list[ExperimentEntry]) -> None:
        """docs/142: background half of a listing-first ``add_root``.

        Parses node.json for every stub (same thread pool as the cold scan),
        then -- under the lock, in ONE atomic tree rebind -- replaces each
        entry that is still the very stub object this scan created. An entry
        replaced meanwhile by a rescan is NEWER than our parse: leave it.
        Regrouped by date afterwards because node.json's created_at can move
        an entry to a different date group than its folder name implied.
        Always clears the hydrating flag and bumps version, even on failure
        (the honest note must not stick forever)."""
        key = str(root)
        try:
            todo = [e for e in stubs if e.needs_parse]
            workers = min(_SCAN_PARSE_WORKERS, max(1, len(todo)))
            with ThreadPoolExecutor(max_workers=workers) as ex:
                parsed = list(ex.map(
                    lambda e: _parse_experiment_folder(e.quam_state_path), todo))
            by_stub = {id(stub): p for stub, p in zip(todo, parsed)}
            with self._lock:
                if self._find_registered_root(root) is None:
                    return                      # root removed mid-hydration
                current = self.tree.get(key, [])
                flat: list[ExperimentEntry] = []
                changed = False
                memo: dict = {}
                for g in current:
                    for e in g.entries:
                        p = by_stub.get(id(e))
                        if p is not None:
                            p.qs_resolved = (e.qs_resolved
                                             or _fast_resolve(p.quam_state_path, memo))
                            self._entries_by_path[p.qs_resolved] = p
                            flat.append(p)
                            changed = True
                        else:
                            flat.append(e)
                if changed:
                    self.tree = {**self.tree, key: _group_by_date(flat)}
            self._save_listing_cache(key)
        except Exception:
            logger.warning("hydration of %s failed -- entries stay listing-only",
                           root, exc_info=True)
        finally:
            with self._lock:
                self._hydrating.discard(key)
                self._version += 1

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
            self._scan_spines.pop(key, None)
            self._scan_probes.pop(key, None)
            for entry in removed_entries:
                resolved = entry.quam_state_path.resolve()
                self._entries_by_path.pop(resolved, None)
                self._loaded_stores.pop(resolved, None)
            self._version += 1

    def rescan_root(self, path: str | Path, *,
                    full: bool = False) -> list[ExperimentEntry]:
        """Re-scan a root folder (e.g. after new experiments are added).

        r13 (audit D2): this used to be remove_root + add_root, which rebound
        ``self.tree`` WITHOUT the root for the whole os.walk — lock-free
        readers rendering in that window showed an empty sidebar ("No
        workspace roots added yet") until the NEXT version bump. Now the slow
        scan runs outside the lock and the tree is swapped in ONE rebind, so
        readers always see either the old or the new state of the root, never
        its absence.
        """
        path = Path(path).expanduser().resolve()
        with self._lock:
            registered = self._find_registered_root(path)
            if registered is None:
                return self.add_root(path)
            key = str(registered)
            old_probe = dict(self._scan_probes.get(key, {}))
            old_probe_dirs = list(old_probe)
            old_entries = [e for g in self.tree.get(key, [])
                           for e in g.entries]
        # Pre-walk sample of the KNOWN spine — a run landing mid-scan in one
        # of these dirs bumps its mtime above the recorded value, so the next
        # rescan_if_stale catches it (same guarantee add_root's shallow
        # pre-probe gives a first scan).
        pre_probe = _probe_dirs(old_probe_dirs) if old_probe_dirs else {}
        # docs/126 r3: incremental by default — re-walk only the changed
        # subtrees and verify the reused entries — because a refresh is
        # almost always "the same archive plus a few new runs". Full stays
        # for the first scan, a standalone root, and callers that ask.
        if (full or not old_probe
                or any(e.is_standalone for e in old_entries)):
            entries = _scan_root(registered)           # the slow part — no lock
        else:
            entries = _incremental_rescan(registered, old_entries, old_probe)
        groups = _group_by_date(entries)
        spine = _spine_of(registered, entries)
        probe = _probe_dirs(spine)
        for d, mt in pre_probe.items():
            if d in probe:
                probe[d] = mt
        # r16 ⑦ D-B: newly-discovered spine dirs (not in the OLD spine) were
        # stamped post-walk — a run landing there mid-walk was swallowed
        # forever. 0.0 forces exactly one healing rescan on the next poll,
        # which then records the real mtimes. Only fires when structure
        # actually appeared, so steady-state polls stay rescan-free.
        if pre_probe:
            for d in probe:
                if d not in pre_probe:
                    probe[d] = 0.0
        def _rp(e: ExperimentEntry) -> Path:
            if e.qs_resolved is None:
                e.qs_resolved = e.quam_state_path.resolve()
            return e.qs_resolved

        # An incremental rescan that changed NOTHING returns the very same
        # entry objects in the same order. Publishing it as a new version
        # would invalidate the tree HTML memo and make every poller refetch
        # a byte-identical sidebar — so a no-op rescan refreshes only the
        # staleness bookkeeping (docs/126 r3: this is what turns the
        # Refresh round-trip from ~1.1 s into the ~0.45 s scan itself).
        unchanged_scan = (len(entries) == len(old_entries)
                          and all(a is b for a, b in zip(entries, old_entries)))
        with self._lock:
            if self._find_registered_root(registered) is None:
                return entries                         # removed mid-scan — discard
            if unchanged_scan and key in self.tree:
                self._scan_spines[key] = spine
                self._scan_probes[key] = probe
                logger.info("Rescanned %s: unchanged (%d quam_state folders)",
                            registered, len(entries))
                return entries
            old_paths = set()
            for group in self.tree.get(key, []):
                for e in group.entries:
                    old_paths.add(_rp(e))
            new_paths = set()
            for e in entries:
                rp = _rp(e)
                new_paths.add(rp)
                self._entries_by_path[rp] = e
            for gone in old_paths - new_paths:
                self._entries_by_path.pop(gone, None)
                self._loaded_stores.pop(gone, None)
                self._loaded_store_mtimes.pop(gone, None)
            self.tree = {**self.tree, key: groups}     # ONE atomic rebind
            self._scan_spines[key] = spine
            self._scan_probes[key] = probe
            self._version += 1
            logger.info("Rescanned %s: %d quam_state folders", registered, len(entries))
        self._save_listing_cache(key)
        return entries

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
        """True if any spine directory's mtime moved since the last scan.

        Compares the CURRENT {dir → mtime} map over the recorded spine against
        the one observed at scan time — mtime-to-mtime, never against this
        machine's ``time.time()`` (mirrors DatasetStore's ``_current_mtime``
        design). A network mount whose server clock runs behind ours would
        otherwise never look stale (new-run mtimes forever below our wall
        clock); one running ahead would look stale on every poll and thrash
        full rescans. The per-dir map (not an aggregate max) means one
        future-dated sibling can never mask later changes, and a vanished or
        newly-unreadable spine dir reads as a difference → rescan heals.
        """
        key = str(root)
        try:
            root.stat()
        except OSError:
            return False   # root transiently unreadable — keep the current tree
        spine = self._scan_spines.get(key)
        if spine is None:
            return True    # never probed (legacy state) — one rescan seeds it
        cur = _probe_dirs(spine)
        return cur != self._scan_probes.get(key)

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


# Staleness-probe spine bound: a spine larger than this keeps the root plus
# the most-recently-modified dirs (the ones most likely to change next).
_SPINE_CAP = 3000


def _probe_dirs(dirs: list[str] | list[Path]) -> dict[str, float]:
    """{dir → mtime} for every statable directory in *dirs* — the staleness
    fingerprint. Unstatable dirs are simply absent (their disappearance reads
    as a map difference → stale → rescan heals). Never raises."""
    out: dict[str, float] = {}
    for d in dirs:
        try:
            out[str(d)] = os.stat(d).st_mtime
        except OSError:
            continue
    return out


def _shallow_dirs(root: Path) -> list[Path]:
    """*root* + its immediate subdirectories (the pre-walk sample set — cheap,
    and available before any scan has discovered the real spine)."""
    dirs: list[Path] = [root]
    try:
        for child in root.iterdir():
            if child.is_dir():
                dirs.append(child)
    except OSError:
        pass
    return dirs


def _spine_of(root: Path, entries: list[ExperimentEntry]) -> list[str]:
    """The directory SPINE of a scanned root: every run folder's parent plus
    all its ancestors up to (and including) the root, PLUS every spine
    member's immediate child directories (r16 (7) D-A).

    A new run bumps its (known) parent dir's mtime; a new date dir bumps the
    chip dir; a new chip dir bumps the root -- so statting exactly this set
    detects additions at ANY depth without walking. The child-dir expansion
    closes the D-A hole: a date dir that held NO valid run at scan time
    (created moments before the scan fired -- the root-mtime bump races the
    day's first save by well under a second on real archives -- or whose runs
    never write quam_state) was never watched, so every later run bumped
    only ITS mtime, which nothing statted, and the sidebar stayed frozen
    until the manual Refresh. Children are enumerated ONCE PER SCAN (the
    poll stays pure stats); a dir created after the scan is caught
    transitively -- its creation bumps its parent's (watched) mtime -> rescan
    -> it joins the spine. Capped at ``_SPINE_CAP`` keeping the root + the
    most-recently-modified dirs (old days stop changing).

    docs/142: computed on STRINGS. The Path-based version hashed and
    compared ~5,000 Path objects (each hash case-folds the whole string) and
    raised/caught ValueError per out-of-root ancestor -- measured ~2.4 s of
    an 8.5 s cold add_root at 5,000 runs, all interpreter overhead."""
    sep = os.sep
    root_s = str(root)
    root_prefix = root_s if root_s.endswith(sep) else root_s + sep
    spine: set[str] = {root_s}
    for e in entries:
        d = os.path.dirname(str(e.folder_path))
        while d:
            if d in spine:
                break                        # ancestors already recorded
            spine.add(d)
            if d == root_s or not d.startswith(root_prefix):
                break                        # reached root / walked outside it
            parent = os.path.dirname(d)
            if parent == d:
                break
            d = parent
    run_folders = {str(e.folder_path) for e in entries}
    for d in list(spine):                  # r16 (7): watch empty/invalid dirs too
        try:
            # scandir, not iterdir+is_dir(): the enumeration already carries
            # each child's kind, where Path.is_dir() is one stat per child
            # (~2,700 stats on a real archive -- docs/126 r3 profile).
            with os.scandir(d) as it:
                for de in it:
                    try:
                        if not de.is_dir():
                            continue
                    except OSError:
                        continue
                    child = os.path.join(d, de.name)
                    # Discovered run folders are LEAVES (already listed;
                    # their internal churn is not tree structure) --
                    # including them would balloon the probe with every run
                    # and make each new run cost a second healing rescan
                    # via the D-B stamp.
                    if child not in run_folders:
                        spine.add(child)
        except OSError:
            continue
    dirs = list(spine)
    if len(dirs) > _SPINE_CAP:
        probed = _probe_dirs(dirs)
        dirs.sort(key=lambda p: probed.get(p, 0.0), reverse=True)
        dirs = dirs[:_SPINE_CAP]
        if root_s not in dirs:
            dirs.append(root_s)
    return dirs


def build_nested_tree(root: Path, entries: list[ExperimentEntry]) -> list[dict]:
    """VS-Code-style nested render model for one root (r13).

    The flat ``Workspace.tree`` groups by date_str only, which MERGES
    same-date runs of different chips and hides intermediate folder levels
    entirely (``root/<chip>/<date>/#N`` rendered as one date group). This
    builds nested container nodes from each entry's real path:

        {"name", "tpath", "is_date", "children": [...], "entries": [...],
         "n_total"}

    - Container chain = ``entry.folder_path.parent`` relative to *root*;
      entries whose parent IS the root (or falls outside it via a symlink)
      group under a pseudo date container named ``date_str`` — byte-identical
      to the legacy view for the flat ``root/<date>/#N`` layout.
    - Sort: date-like children DESCENDING first (today on top — user-chosen),
      then other names ascending; leaf entries run_id-DESCENDING (newest
      first; the sidebar cap keeps the newest N visible).
    - ``tpath`` is the container's /-joined relative path — the stable key
      for the "Show all" endpoint and the client's sticky open-state.
    """
    root_node: dict = {"children": {}, "entries": []}

    def _child(node: dict, name: str, tpath: str) -> dict:
        ch = node["children"]
        if name not in ch:
            ch[name] = {"name": name, "tpath": tpath,
                        "is_date": bool(_DATE_RE.search(name)),
                        "children": {}, "entries": []}
        return ch[name]

    for e in entries:
        try:
            rel = e.folder_path.parent.relative_to(root)
            parts = [p for p in rel.parts if p not in (".",)]
        except ValueError:
            parts = []
        if not parts:
            parts = [e.date_str]           # flat/outside layout → pseudo date
        node = root_node
        tpath = ""
        for part in parts:
            tpath = f"{tpath}/{part}" if tpath else part
            node = _child(node, part, tpath)
        node["entries"].append(e)

    def _finish(node: dict) -> tuple[list[dict], int]:
        kids = list(node["children"].values())
        dates = sorted((k for k in kids if k["is_date"]),
                       key=lambda n: n["name"], reverse=True)
        others = sorted((k for k in kids if not k["is_date"]),
                        key=lambda n: n["name"].lower())
        ordered = dates + others
        total = len(node["entries"])
        # Newest first: run_id desc, timestamp desc; run-less (standalone)
        # entries sort last (they were float("inf")-last in the asc view too).
        node["entries"].sort(
            key=lambda e: (e.run_id if e.run_id is not None else float("-inf"),
                           e.timestamp))
        node["entries"].reverse()
        for k in ordered:
            _, sub_total = _finish(k)
            total += sub_total
        node["children"] = ordered
        node["n_total"] = total
        return ordered, total

    _finish(root_node)
    return root_node["children"]


def _incremental_rescan(root: Path, old_entries: list[ExperimentEntry],
                        old_probe: dict[str, float]) -> list[ExperimentEntry]:
    """Rescan *root* by re-walking ONLY what moved (docs/126 r3).

    A refresh is almost always "the same archive plus a few new runs", yet
    the manual button re-walked and re-parsed every run (3.7 s over 2,655
    runs, measured). The recorded spine probe already names every directory
    whose mtime moves when structure changes anywhere, so:

      1. re-stat the recorded spine — CHANGED dirs (moved, new, vanished)
         get their subtrees re-walked and re-parsed, pruned at unchanged
         spine dirs so a bumped ancestor never cascades into a full walk;
      2. entries outside every re-walked subtree are REUSED — verified
         cheaply (state.json still there, node.json mtime unmoved; a moved
         node.json re-parses, so a run finishing still updates its status);
      3. unchanged spine dirs are re-listed once (pure iterdir) to catch the
         one structural case mtimes cannot see from above: a run folder that
         existed at scan time but only later gained its quam_state (creating
         it bumps the RUN dir, which the spine deliberately does not watch).

    Falls back to nothing here — the CALLER chooses this path only when a
    previous probe exists and the root is not a standalone quam_state.
    """
    cur = _probe_dirs(list(old_probe))
    changed = {d for d in old_probe if cur.get(d) != old_probe.get(d)}
    unchanged = frozenset(old_probe) - changed

    # top-most changed dirs only — a changed date dir under a changed chip
    # dir is covered by the chip dir's walk
    tops = [d for d in sorted(changed)
            if not any(d != o and d.startswith(o + os.sep) for o in changed)]

    fresh: dict[Path, ExperimentEntry] = {}
    candidates: list[Path] = []
    for t in tops:
        tp = Path(t)
        if tp.is_dir():
            candidates.extend(_discover(tp, prune=unchanged))

    # An entry is REPLACED by the re-walk only if the walk actually reaches
    # it: from its folder upward, meeting an UNCHANGED spine dir first means
    # the walk prunes there (the entry is reused), meeting a walked top first
    # means it is re-visited. A bare startswith(top) check dropped every
    # entry whenever the ROOT was the changed top (any new date dir bumps
    # it), and the sweep then re-parsed the whole archive to rescue them.
    tops_set = set(tops)

    def _rewalked(folder: Path) -> bool:
        d = folder
        while True:
            sd = str(d)
            if sd in unchanged:
                return False               # pruned above the entry
            if sd in tops_set:
                return True                # reached without a prune
            nd = d.parent
            if nd == d:
                return False               # outside every walked top
            d = nd

    kept: list[ExperimentEntry] = []
    for e in old_entries:
        if _rewalked(e.folder_path):
            continue                       # replaced (or vanished) by a re-walk
        kept.append(e)

    # Verify pass — ONE os.scandir per run-parent directory. A directory
    # enumeration on Windows carries every child entry attributes, so this
    # reads thousands of run mtimes in a handful of syscalls where per-entry
    # stat/is_file calls measured ~4 s (the first cut of this function). A
    # kept entry whose run folder mtime moved re-parses (a finishing run
    # rewrites node.json, quam_state can appear or vanish, figures land —
    # all bump it); one that vanished from its listing is dropped; an
    # unmoved one is reused untouched. The same listings also name child
    # dirs with NO entry — the late-quam_state case — which get one
    # validity check each.
    by_parent: dict[Path, list[ExperimentEntry]] = {}
    for e in kept:
        by_parent.setdefault(e.folder_path.parent, []).append(e)
    listing_dirs = set(by_parent) | {Path(d) for d in unchanged}
    listings: dict[Path, dict[str, tuple[bool, float]]] = {}
    for d in listing_dirs:
        lst: dict[str, tuple[bool, float]] = {}
        try:
            with os.scandir(d) as it:
                for de in it:
                    try:
                        lst[de.name] = (de.is_dir(), de.stat().st_mtime)
                    except OSError:
                        continue
        except OSError:
            listings[d] = {}
            continue
        listings[d] = lst

    verified: list[ExperimentEntry] = []
    reparse: list[Path] = []
    for parent, es in by_parent.items():
        lst = listings.get(parent, {})
        for e in es:
            info = lst.get(e.folder_path.name)
            if info is None or not info[0]:
                continue                   # the run folder is gone
            if info[1] != e.run_mtime:
                reparse.append(e.quam_state_path)
            else:
                verified.append(e)
    kept = verified

    # the late-quam_state sweep, from the SAME listings (no new I/O)
    known_runs = {e.folder_path for e in kept} | {qs.parent for qs in reparse}
    for d, lst in listings.items():
        for name, (is_dir, _mt) in lst.items():
            child = d / name
            if not is_dir or child in known_runs or str(child) in old_probe:
                continue
            qs = child / "quam_state"
            if _is_quam_state_folder(qs):
                candidates.append(qs)
    candidates.extend(reparse)

    if candidates:
        seen: set[Path] = set()
        todo = [c for c in candidates
                if _is_quam_state_folder(c) and not (c in seen or seen.add(c))]
        if todo:
            workers = min(_SCAN_PARSE_WORKERS, len(todo))
            with ThreadPoolExecutor(max_workers=workers) as ex:
                for e in ex.map(_parse_experiment_folder, todo):
                    fresh[e.folder_path] = e

    kept = [e for e in kept if e.folder_path not in fresh]
    return kept + list(fresh.values())


def _stub_entry(quam_state_path: Path) -> ExperimentEntry:
    """A listing-only entry from FOLDER NAMES alone (docs/142) -- no file
    content is read. run_id / experiment name / time come from the
    ``#<id>_<name>_<HHMMSS>`` convention, the date from the parent date dir;
    status/qubits/outcomes stay empty until hydration parses node.json."""
    folder = quam_state_path.parent
    try:
        run_mtime = folder.stat().st_mtime
    except OSError:
        run_mtime = 0.0
    m = _FOLDER_RE.match(folder.name)
    date_str = _extract_date("", folder)
    if m:
        rid = int(m.group(1))
        name = m.group(2)
        hms = m.group(3)
        ts = ""
        if _DATE_RE.fullmatch(date_str):
            ts = f"{date_str}T{hms[:2]}:{hms[2:4]}:{hms[4:6]}"
        return ExperimentEntry(
            folder_path=folder, quam_state_path=quam_state_path,
            run_id=rid, experiment_name=name, timestamp=ts, status="",
            qubits=[], qubit_pairs=[], outcomes={}, parent_ids=[],
            date_str=date_str, is_standalone=False, run_mtime=run_mtime,
            needs_parse=True)
    stub = _make_standalone_entry(quam_state_path)
    stub.needs_parse = True
    stub.run_mtime = run_mtime
    return stub


def _fast_resolve(p: Path, memo: dict) -> Path:
    """``Path.resolve()`` with a per-scan ancestor memo.

    On Windows every ``resolve()`` walks the final path component-by-component
    (measured 3.3 s for 2x2,652 entries -- the comment on ``qs_resolved``).
    Paths coming out of ``os.walk(resolved_root)`` already carry true on-disk
    name casing, so a NON-link component's resolution is just its parent's
    resolution plus its own name: full ``resolve()`` is needed only for the
    few distinct ancestors and for actual links/junctions (detected by one
    lstat via ``os.path.islink`` + ``os.path.isjunction`` where available)."""
    key = str(p)
    hit = memo.get(key)
    if hit is not None:
        return hit
    parent = p.parent
    if parent == p:
        r = p
    else:
        try:
            _isjunction = getattr(os.path, "isjunction", None)
            if os.path.islink(key) or (_isjunction and _isjunction(key)):
                r = p.resolve()
            else:
                r = _fast_resolve(parent, memo) / p.name
        except OSError:
            r = p.resolve()
    memo[key] = r
    return r


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
    _TRUNCATED_ROOTS.discard(str(root))     # re-decided by THIS walk (docs/105 #9)
    candidates = _discover(root)

    if not candidates:
        return []

    # Parse pass — bounded parallelism. ``ThreadPoolExecutor.map``
    # preserves input order so the resulting list is reproducible.
    workers = min(_SCAN_PARSE_WORKERS, len(candidates))
    with ThreadPoolExecutor(max_workers=workers) as ex:
        return list(ex.map(_parse_experiment_folder, candidates))


def _discover(root: Path, prune: frozenset[str] = frozenset()) -> list[Path]:
    """The walk half of a scan: every quam_state dir under *root*.

    ``prune`` (docs/126 r3, incremental rescan) names spine directories whose
    mtime did NOT move -- their contents are provably what the previous scan
    recorded, so the walk skips their subtrees instead of re-visiting
    thousands of run folders to rediscover what it already knows.

    docs/142: hand-rolled scandir traversal instead of ``os.walk``. Windows'
    scandir already carries each child's kind and reparse-ness, so the walk
    (a) never pays the old one-``os.stat``-per-dir cycle guard on ordinary
    dirs -- only an actual symlink/junction crossing stats for the
    ``(st_dev, st_ino)`` visited-set (cycles and duplicate routes still
    terminate exactly as before, r16 (7) D-E zero-inode rule included);
    (b) reads quam_state membership from the enumeration itself (no
    ``is_file()`` stats); (c) treats a ``#<id>_<name>_<HHMMSS>`` run folder
    as a leaf, descending only into its ``quam_state``. Measured: 5,000-run
    cold discovery 2.4 s -> ~1 s, and the whole cold ``add_root`` at that
    scale ~10.7 s -> under 2 s with ``defer_parse``.

    ``_DISCOVER_LINKS_SEEN`` (module-level, last-walk flag) records whether
    ANY link/junction was crossed: when none was, every discovered path is
    already canonical (the root itself is pre-resolved), so callers may skip
    per-entry ``resolve()`` entirely.
    """
    global _DISCOVER_LINKS_SEEN
    candidates: list[Path] = []
    visited: set[tuple[int, int]] = set()
    links_seen = False
    ndirs = 0
    root_s = str(root)
    stack: list[str] = [root_s]
    truncated = False
    while stack:
        if ndirs >= _SCAN_DIR_CAP:
            truncated = True
            break
        d = stack.pop()
        if prune and d != root_s and d in prune:
            continue
        ndirs += 1
        subdirs: list[tuple[str, object]] = []   # (path, DirEntry)
        names: set[str] | None = None
        base = os.path.basename(d)
        want_files = base == "quam_state"
        if want_files:
            names = set()
        try:
            with os.scandir(d) as it:
                for de in it:
                    try:
                        is_dir = de.is_dir()          # cached attr; stat only for links
                    except OSError:
                        continue
                    if is_dir:
                        subdirs.append((de.path, de))
                    elif want_files:
                        names.add(de.name)
        except OSError:
            continue
        if want_files and "state.json" in names and "wiring.json" in names:
            candidates.append(Path(d))
            continue                               # a quam_state dir is a leaf
        if _FOLDER_RE.match(base):
            # a run folder is a leaf by convention -- descend only into its
            # quam_state (figures/data exports are not tree structure)
            subdirs = [sd for sd in subdirs
                       if os.path.basename(sd[0]) == "quam_state"]
        for sub_path, de in subdirs:
            try:
                lst = de.stat(follow_symlinks=False)
            except OSError:
                continue
            is_link = de.is_symlink() or getattr(lst, "st_reparse_tag", 0) != 0
            if is_link:
                # A link/junction can route back into (or out of) the tree:
                # this is the ONLY case that needs the physical-identity
                # visited-set (and its stat).
                links_seen = True
                try:
                    st = os.stat(sub_path)         # follows the link
                except OSError:
                    continue
                key = (st.st_dev, st.st_ino)
                if st.st_ino and key in visited:
                    continue                       # cycle / duplicate route
                if st.st_ino:
                    visited.add(key)
            stack.append(sub_path)
    if truncated:
        # The visited-set stops CYCLES, not scope -- a symlink escaping to a
        # huge tree (/, $HOME) would otherwise walk the whole filesystem.
        logger.warning(
            "workspace scan of %s stopped at %d directories -- a symlink may "
            "point at a very large tree; %d quam_state folders found so far",
            root, _SCAN_DIR_CAP, len(candidates))
        _TRUNCATED_ROOTS.add(root_s)               # docs/105 #9 -- surfaced in the tree
    _DISCOVER_LINKS_SEEN = links_seen
    return candidates


def _is_quam_state_folder(path: Path) -> bool:
    """Check if a folder contains both state.json and wiring.json."""
    return (path / "state.json").is_file() and (path / "wiring.json").is_file()


def _parse_experiment_folder(quam_state_path: Path) -> ExperimentEntry:
    """Parse metadata from node.json (if present) in the parent folder."""
    experiment_folder = quam_state_path.parent
    node_json_path = experiment_folder / "node.json"

    try:
        run_mtime = experiment_folder.stat().st_mtime
    except OSError:
        run_mtime = 0.0
    if not node_json_path.is_file():
        return _make_standalone_entry(quam_state_path)

    # Workspace experiment folders can include a chip whose state.json is
    # currently being written by an active experiment program. ``safe_io``
    # opens the file with FILE_SHARE_DELETE on Windows so our read never
    # blocks the writer's atomic save (the same defence applied to live
    # quam_state in core.safe_io).
    try:
        # scan_json, not read_json: the bulk scan must never ride the retry
        # ladder (0.9 s worst-case sleep PER mid-write file -- docs/80's
        # DatasetStore reasoning applies identically here). A node.json being
        # written right now degrades to a standalone entry for one poll cycle
        # and re-parses when its folder mtime moves.
        node = safe_io.scan_json(node_json_path)
    except (safe_io.LiveFileError, FileNotFoundError, ValueError, OSError) as exc:
        logger.warning("Failed to parse %s: %s", node_json_path, exc)
        return _make_standalone_entry(quam_state_path)
    if node is None:                    # mid-write / truncated -- "come back later"
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
        run_mtime=run_mtime,
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
