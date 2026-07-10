"""Load and merge QUAM state/wiring JSON files into a single in-memory store.

QuamStore is the central data object: it owns the raw dicts, the merged view,
a change log for tracking edits, the search index, and a *per-store* pointer
cache. All mutations are guarded by a threading.RLock so that Flask request
threads and the pywebview UI thread never corrupt shared state.

All file reads go through :mod:`core.safe_io`. On Windows, that means a
``state.json`` an experiment program may also be writing is opened with
``FILE_SHARE_DELETE``, so our read never blocks the experiment's atomic save.
The :func:`safe_io.read_state_wiring` call additionally double-checks the
files' mtimes around the pair read, so a writer that lands between the two
reads can't hand us a torn snapshot.
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from quam_state_manager.core import safe_io
from quam_state_manager.core.pointer_resolver import (
    PointerCache,
    is_pointer,
    is_self_ref,
    resolve_pointer,
)

logger = logging.getLogger(__name__)


@dataclass
class ChangeEntry:
    """One recorded edit (used by modifier.py and saver.py later)."""

    dot_path: str
    old_value: Any
    new_value: Any
    source_file: str  # "state" or "wiring"
    created: bool = False  # True if this entry created a new key/subtree (undo deletes)
    deleted: bool = False  # True if this entry deleted a key/subtree (undo restores old_value)
    # Groups entries that form ONE user action (a batch edit, a rename's
    # create+delete+retargets) so a single Ctrl+Z undoes them atomically.
    # None ⇒ a standalone edit that undoes on its own.
    group_id: str | None = None


@dataclass
class PointerWarning:
    """A pointer that could not be resolved at load time.

    ``soft`` marks the benign case where the pointer's *parent object* resolves
    but only the final field is absent — i.e. a quam-optional field (e.g.
    ``DragCosinePulse.detuning`` / ``digital_marker``) omitted from JSON because
    it's at its default. quam resolves it to that default at runtime, so it is
    NOT a crash-class breakage; only a genuinely missing parent path is.
    """

    dot_path: str
    pointer: str
    message: str
    soft: bool = False


_SENTINEL = object()


def _deep_merge(a: dict, b: dict) -> dict:
    """Recursively merge *b* into a shallow copy of *a*.

    Dict+dict values recurse so neither side's keys are lost; any other
    collision is resolved in favour of *b* (wiring shadows state — the
    documented precedence). Used by :meth:`QuamStore._merge` only on a
    top-level key present in both state and wiring.
    """
    out = dict(a)
    for k, v in b.items():
        cur = out.get(k, _SENTINEL)
        if cur is not _SENTINEL and isinstance(cur, dict) and isinstance(v, dict):
            out[k] = _deep_merge(cur, v)
        else:
            out[k] = v
    return out


class QuamStore:
    """Central in-memory store for one ``quam_state`` folder.

    Holds:
      - ``state``  -- raw ``state.json`` dict (source of truth for saving)
      - ``wiring`` -- raw ``wiring.json`` dict (source of truth for saving)
      - ``merged`` -- combined view used for pointer resolution and querying
      - ``change_log`` -- list of :class:`ChangeEntry` since last save

    Thread-safe: all mutations go through :attr:`_lock`.
    """

    def __init__(self, folder_path: str | Path, *, validate: bool = False):
        self.folder_path = Path(folder_path)
        self.state: dict = {}
        self.wiring: dict = {}
        self.merged: dict = {}
        self.change_log: list[ChangeEntry] = []
        self.pointer_warnings: list[PointerWarning] = []
        self.search_index: Any = None  # will be set by search_index.py later
        # Generated QM config (Config Viewer). Populated on demand by
        # POST /config/regenerate; reset on reload. Button-only refresh.
        self.generated_config: dict | None = None
        self.generated_config_meta: dict | None = None  # {"at", "versions", "warnings"}
        # Monotonic mutation counter (never reset, not even on reload) —
        # lets surfaces like the Config Viewer / pulse Verify overlay tell
        # whether a cached artifact predates the latest edit.
        self.mutation_seq: int = 0
        self._lock = threading.RLock()
        # Per-store pointer cache. Keyed on (pointer, current_path); the
        # lock protects concurrent reads/writes from Flask workers + the
        # pywebview UI thread. Held PER STORE — never shared across chips,
        # so two QuamStore instances with same-named qubits can't poison
        # each other's resolutions.
        self._pointer_cache: PointerCache = {}
        self._pointer_cache_lock = threading.Lock()
        self._validate = validate
        self._load()

    @classmethod
    def from_dicts(cls, state: dict, wiring: dict) -> "QuamStore":
        """Build a store from in-memory dicts, never touching disk.

        Used for read-only previews of a chip dropped into the UI (we only
        have the file *contents*, never a real folder path). The instance is
        fully usable for querying (merge + pointer resolution) but is NOT a
        live folder: ``folder_path`` is ``None`` and it must never be
        registered as a context, saved, reloaded, or have its config
        regenerated. Bypasses ``__init__``/``_load`` so no ``state.json`` /
        ``wiring.json`` read happens.
        """
        if not isinstance(state, dict) or not isinstance(wiring, dict):
            raise TypeError("from_dicts requires dict state and dict wiring")
        self = cls.__new__(cls)
        self.folder_path = None
        self.state = state
        self.wiring = wiring
        self.merged = {}
        self.change_log = []
        self.pointer_warnings = []
        self.search_index = None
        self.generated_config = None
        self.generated_config_meta = None
        self.mutation_seq = 0
        self._lock = threading.RLock()
        self._pointer_cache = {}
        self._pointer_cache_lock = threading.Lock()
        self._validate = False
        self._merge()
        self._clear_pointer_cache()
        return self

    # ------------------------------------------------------------------
    # Loading
    # ------------------------------------------------------------------

    def _load(self) -> None:
        state_path = self.folder_path / "state.json"
        wiring_path = self.folder_path / "wiring.json"

        if not state_path.exists():
            raise FileNotFoundError(f"state.json not found in {self.folder_path}")
        if not wiring_path.exists():
            raise FileNotFoundError(f"wiring.json not found in {self.folder_path}")

        # safe_io.read_state_wiring opens both files share-delete on Windows
        # and brackets the pair with mtime checks, so a writer landing
        # between the two reads can't hand us a torn snapshot. A bad-JSON
        # file is surfaced as LiveFileError (an OSError subclass).
        try:
            self.state, self.wiring = safe_io.read_state_wiring(self.folder_path)
        except safe_io.LiveFileError as exc:
            raise ValueError(str(exc)) from exc

        self._merge()

        if self._validate:
            self._validate_pointers()

        self._clear_pointer_cache()

        logger.info(
            "Loaded quam_state from %s (%d state keys, %d wiring keys)",
            self.folder_path,
            len(self.state),
            len(self.wiring),
        )

    def _merge(self) -> None:
        """Build ``self.merged`` by combining state + wiring top-level keys.

        Normally wiring.json has only ``"wiring"`` and ``"network"`` (absent from
        state.json), so there is no collision and this is a plain flat union —
        absolute pointers like ``#/wiring/qubits/qA1/xy/opx_output`` resolve from
        the merged root.

        Some chips, though, put connectivity at the TOP level of wiring.json
        (``qubits``/``qubit_pairs``/``ports``) instead of nesting it under
        ``wiring``. There the keys collide with state.json. The old code
        REPLACED the state value wholesale — silently wiping the entire state
        component subtree (all cross_resonance / operations / macros). We now
        DEEP-MERGE colliding dict keys so wiring connectivity combines with the
        state component instead of destroying it. (No effect on the common case:
        with no key collision, nothing is deep-merged.)
        """
        self.merged = {**self.state}
        for key, value in self.wiring.items():
            existing = self.merged.get(key, _SENTINEL)
            if existing is _SENTINEL:
                self.merged[key] = value
            elif isinstance(existing, dict) and isinstance(value, dict):
                self.merged[key] = _deep_merge(existing, value)
            else:
                logger.warning(
                    "Key %r exists in both state.json and wiring.json; wiring "
                    "value will shadow state value", key,
                )
                self.merged[key] = value

    def _validate_pointers(self) -> None:
        """Walk the merged dict and attempt to resolve every pointer.

        Failures are collected in ``self.pointer_warnings`` (never raises).
        """
        self.pointer_warnings.clear()
        for dot_path, value, path_tuple in _walk(self.merged):
            if not is_pointer(value) or is_self_ref(value):
                continue
            resolved = self.resolve_pointer(value, path_tuple)
            if resolved == value:
                warning = PointerWarning(
                    dot_path=dot_path,
                    pointer=value,
                    message=f"Could not resolve pointer at {dot_path}",
                    soft=self._pointer_parent_resolves(value, path_tuple),
                )
                self.pointer_warnings.append(warning)
                logger.debug("Unresolvable pointer at %s: %s", dot_path, value)
        if self.pointer_warnings:
            logger.info(
                "%d pointer(s) could not be resolved (enable DEBUG logging for details)",
                len(self.pointer_warnings),
            )

    def _pointer_parent_resolves(self, pointer: str, path_tuple: tuple) -> bool:
        """True if the pointer's PARENT resolves but only the final field is absent.

        That's the benign "optional field omitted at its default" case (quam
        resolves to the default at runtime) — not a crash-class dangling pointer.
        ``#/ports/...`` are excluded (handled as missing-port errors elsewhere).
        """
        if not isinstance(pointer, str) or "/" not in pointer or pointer.startswith("#/ports/"):
            return False
        parent, _, leaf = pointer.rpartition("/")
        # A bare prefix ("#", "#.", "#..") isn't a real object reference.
        if not leaf or parent.rstrip("./") in ("", "#"):
            return False
        try:
            resolved = self.resolve_pointer(parent, path_tuple)
        except Exception:  # noqa: BLE001 - never let classification crash a load
            return False
        return resolved != parent and isinstance(resolved, dict)

    def validate_pointers(self) -> list[PointerWarning]:
        """Run pointer validation on demand and return the warnings.

        Public entry point for the diagnostics linter, which wants the
        unresolvable-pointer list without constructing the store with
        ``validate=True``. Acquires :attr:`_lock`.
        """
        with self._lock:
            self._validate_pointers()
            return list(self.pointer_warnings)

    # ------------------------------------------------------------------
    # Reload
    # ------------------------------------------------------------------

    def reload(self) -> None:
        """Re-read files from disk and rebuild everything. Acquires _lock."""
        with self._lock:
            self._load()
            self.generated_config = None
            self.generated_config_meta = None
            self.change_log.clear()
            # A reload IS an in-memory content change: advance the mutation
            # counter so seq-validated caches (PulseIndex) and staleness
            # checks (Verify overlay) can't serve pre-reload conclusions.
            self.mutation_seq += 1

    # ------------------------------------------------------------------
    # Accessors
    # ------------------------------------------------------------------

    def get_value(self, dot_path: str) -> Any:
        """Retrieve a raw value by dot-separated path from the merged dict.

        Returns ``KeyError`` if the path doesn't exist.
        """
        keys = dot_path.split(".")
        current: Any = self.merged
        for key in keys:
            if isinstance(current, dict):
                if key not in current:
                    raise KeyError(f"Key {key!r} not found at path {dot_path!r}")
                current = current[key]
            elif isinstance(current, list):
                try:
                    current = current[int(key)]
                except (ValueError, IndexError) as exc:
                    raise KeyError(f"Cannot index list with {key!r} at path {dot_path!r}") from exc
            else:
                raise KeyError(f"Cannot traverse into {type(current).__name__} at {key!r} in {dot_path!r}")
        return current

    def resolve_value(self, dot_path: str) -> Any:
        """Like :meth:`get_value`, but if the raw value is a pointer, resolve it."""
        raw = self.get_value(dot_path)
        if is_pointer(raw) and not is_self_ref(raw):
            path_tuple = tuple(dot_path.split("."))
            return self.resolve_pointer(raw, path_tuple)
        return raw

    def resolve_pointer(self, pointer: str, current_path: tuple[str, ...]) -> Any:
        """Resolve *pointer* against this store's merged dict, using its cache.

        Every store keeps its own ``_pointer_cache`` — never share resolutions
        across :class:`QuamStore` instances, even when the pointer string and
        path tuple happen to be identical. See ``docs/32_red_team_phase_2.md``
        finding 0.1.
        """
        return resolve_pointer(
            self.merged, pointer, current_path,
            cache=self._pointer_cache,
            lock=self._pointer_cache_lock,
        )

    def _clear_pointer_cache(self) -> None:
        """Drop every cached resolution for this store. Call after a mutation."""
        with self._pointer_cache_lock:
            self._pointer_cache.clear()

    def source_file_for(self, dot_path: str) -> str:
        """Determine which source file owns a dot_path: ``"state"`` or ``"wiring"``.

        Top-level keys ``"wiring"`` and ``"network"`` come from wiring.json;
        everything else comes from state.json.
        """
        top_key = dot_path.split(".")[0]
        if top_key in self.wiring:
            return "wiring"
        return "state"

    @property
    def qubit_names(self) -> list[str]:
        """Return sorted list of qubit IDs from the merged dict."""
        qubits = self.merged.get("qubits", {})
        return sorted(qubits.keys()) if isinstance(qubits, dict) else []

    @property
    def qubit_pair_names(self) -> list[str]:
        """Return sorted list of qubit pair IDs from the merged dict."""
        pairs = self.merged.get("qubit_pairs", {})
        return sorted(pairs.keys()) if isinstance(pairs, dict) else []

    def __repr__(self) -> str:
        folder = self.folder_path.name if self.folder_path is not None else "<in-memory>"
        return (
            f"QuamStore(folder={folder!r}, "
            f"qubits={len(self.qubit_names)}, "
            f"pairs={len(self.qubit_pair_names)}, "
            f"changes={len(self.change_log)})"
        )


# ------------------------------------------------------------------
# Utilities
# ------------------------------------------------------------------


def _walk(
    obj: Any,
    prefix: str = "",
    path_tuple: tuple[str, ...] = (),
) -> list[tuple[str, Any, tuple[str, ...]]]:
    """Recursively flatten a nested dict/list into (dot_path, value, path_tuple) triples.

    Only yields leaf values (scalars and strings), not intermediate dicts/lists.
    """
    results: list[tuple[str, Any, tuple[str, ...]]] = []
    if isinstance(obj, dict):
        for key, value in obj.items():
            child_path = f"{prefix}.{key}" if prefix else key
            child_tuple = path_tuple + (key,)
            if isinstance(value, (dict, list)):
                results.extend(_walk(value, child_path, child_tuple))
            else:
                results.append((child_path, value, child_tuple))
    elif isinstance(obj, list):
        for idx, value in enumerate(obj):
            child_path = f"{prefix}.{idx}" if prefix else str(idx)
            child_tuple = path_tuple + (str(idx),)
            if isinstance(value, (dict, list)):
                results.extend(_walk(value, child_path, child_tuple))
            else:
                results.append((child_path, value, child_tuple))
    return results


def flatten(obj: dict) -> dict[str, Any]:
    """Flatten a nested dict into a ``{dot_path: leaf_value}`` dict.

    Convenience wrapper around :func:`_walk` for external use (e.g. differ.py).
    """
    return {dot_path: value for dot_path, value, _ in _walk(obj)}
