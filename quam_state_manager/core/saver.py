"""Persist QUAM state to disk with atomic writes, auto-backup, and exports.

Saver wraps a QuamStore and provides:
  - Atomic save through :func:`core.safe_io.atomic_write_json`'s two halves
    (``_write_tmp_json`` + ``_replace_into_place``) — the same
    ``ReplaceFileW``-backed code path the live-file ``apply-to-live`` flow
    uses, so a save never fails because another process has the target
    file open for reading on Windows
  - Timestamped .bak files before every save, with rotation
  - CSV export via stdlib csv.DictWriter (no pandas)
  - Markdown table export via string formatting
"""

from __future__ import annotations

import csv
import logging
import marshal
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from quam_state_manager.core import safe_io
from quam_state_manager.core import units
from quam_state_manager.core.loader import QuamStore
from quam_state_manager.core.query import QueryEngine

logger = logging.getLogger(__name__)

# Backup retention: keep the most recent N .bak files per source file.
# Long calibration sessions can otherwise accumulate GBs of timestamped backups.
DEFAULT_BACKUP_RETENTION = 20

# Matches both backup stamp forms:
#   old  "state.json.bak.20260522_174501"          (second granularity)
#   new  "state.json.bak.20260522_174501_123456"   (+ microseconds)
# so pre-existing old-form .baks keep counting toward / rotating out of the
# retention budget. Lexicographic order on the stamp stays chronological
# across both forms (the shorter old form sorts before a same-second new one).
_BACKUP_RE = re.compile(r"\.bak\.(\d{8}_\d{6}(?:_\d{6})?)$")

# w8/locks: one save of a folder at a time (its .bak copies, rotation and
# swap), keyed by the folder through safe_io.path_lock -- the key names no
# real file. A save whose snapshot went stale before the swap (not by edits
# alone) snapshots again at most this many times, then holds the store lock
# throughout. Format 2: no back-references (json_pieces' reason, docs/2xx).
_SAVE_LOCK_NAME = ".sm-save.lock"
_SAVE_TRIES = 3
_MARSHAL_V = 2


def _unlink(p: Path) -> None:
    try:
        p.unlink(missing_ok=True)
    except OSError:
        pass

DEFAULT_PROPERTIES = [
    "id",
    "f_01",
    "readout_frequency",
    "T1",
    "T2ramsey",
    "readout_amplitude",
    "readout_threshold",
    "anharmonicity",
    "gate_fidelity_avg",
    "x180_amplitude",
    "z_joint_offset",
    "grid_location",
]

# docs/136 — the bias column above is `z_joint_offset`, which a QDAC-biased
# qubit does not have: on the customer's 20-qubit chip the export printed a
# blank flux value for 11 of 20 rows with nothing saying why. These columns are
# appended only when the chip actually has a QDAC, so an export from any other
# chip is byte-identical to before.
QDAC_PROPERTIES = ["bias_mode", "qdac_channel", "qdac_dc_offset"]


def default_properties(store) -> list[str]:
    """`DEFAULT_PROPERTIES`, plus the QDAC columns when the chip has a QDAC."""
    from quam_state_manager.core import qdac as _qdac

    try:
        has = bool(_qdac.biased_qubits(store.merged))
    except Exception:  # noqa: BLE001 — an export never fails over a column choice
        has = False
    return DEFAULT_PROPERTIES + (QDAC_PROPERTIES if has else [])


class Saver:
    """Persist a QuamStore back to disk and export summaries."""

    def __init__(self, store: QuamStore, backup_retention: int = DEFAULT_BACKUP_RETENTION) -> None:
        self.store = store
        self.backup_retention = max(1, int(backup_retention))
        #: docs/271: the change-log entries the LAST save wrote (and cleared) --
        #: exactly what the saved files carry beyond the content before them,
        #: which is what a live-write door records. Set under the store lock
        #: at the swap, so an edit landing during the save never enters it.
        self.last_cleared: list = []

    # ------------------------------------------------------------------
    # Save
    # ------------------------------------------------------------------

    def save(self, folder_path: Path | str | None = None) -> Path:
        """Write state.json and wiring.json back to disk atomically.

        1. Resolve target folder (default: original folder_path).
        2. Create timestamped ``.bak`` copies of existing files.
        3. Write to ``.tmp`` files, then ``os.replace`` for atomicity.
        4. Clear the change log on success.

        The store holds raw ``#/`` pointer strings (never resolved in-place),
        so ``json.dump`` preserves pointer semantics as-is.

        w8/locks: the store lock is held for a SNAPSHOT and for the swap, never
        for disk I/O or the render. Measured on big30x: 0.26-0.7 s per save
        with every other request waiting (a save after a pull renders its
        pieces cold, ~0.5 s; the .tmp write + fsync is ~65 ms of it warm). Now
        (the LazySearchIndex pattern -- build from a consistent snapshot,
        install only if the store did not move):

        * under the store lock, first: the content token, the change-log
          entries this save stands for, and ``marshal.dumps`` of both
          documents (~15-25 ms for the 19 MB big30x state -- the documents are
          plain JSON data, which marshal copies exactly: key order, types,
          float bits);
        * the ``.bak`` copies and the rotation run under this folder's save
          lock only -- they copy the files ON DISK, which only a save of this
          folder (serialised here) or a build-lock writer replaces;
        * with the lock free: the copy is rendered and written to the ``.tmp``
          files by the same ``_write_tmp_json`` as before -- the same bytes;
        * under the store lock again, the swap and the log clear, when
          - nothing moved: the whole log is cleared, as always;
          - only EDITS landed meanwhile (entries appended after the ones
            snapshotted, one journaled step each): the file holds the content
            before them and only the snapshotted entries are cleared -- the
            edits stay pending, exactly as when they waited for a single-hold
            save to finish;
          - anything else (an undo, a discard, a reload): the tmps are dropped
            and it snapshots again, so the file never differs from memory
            with a clean log.

        A caller that already holds the store lock, a document marshal refuses
        and a chip that keeps moving get the single-hold save it always was.
        """
        store = self.store
        with store._lock:
            target = Path(folder_path) if folder_path else store.folder_path
        target.mkdir(parents=True, exist_ok=True)

        state_path = target / "state.json"
        wiring_path = target / "wiring.json"

        own = getattr(store._lock, "_is_owned", None)
        if own is not None and own():
            # the caller's own hold: nothing here may let go of it, and no save
            # lock may be waited for under it (a save holding that one waits
            # for the store lock), so this is the single-hold save
            self._backup_and_rotate(state_path, wiring_path)
            self._write_locked(state_path, wiring_path)
            logger.info("Saved quam_state to %s", target)
            return target

        with safe_io.path_lock(target / _SAVE_LOCK_NAME):
            backed_up = False
            for _ in range(_SAVE_TRIES):
                # the snapshot FIRST: it is the moment this save stands for,
                # so an edit that lands during the .bak copies below stays
                # pending instead of being saved (and its entry cleared) past
                # a caller that journaled the log just before calling us
                with store._lock:
                    token = self._token()
                    logged = list(store.change_log)
                    try:
                        snap = marshal.dumps((store.state, store.wiring), _MARSHAL_V)
                    except (ValueError, TypeError):
                        break           # not plain JSON data: the single-hold save
                if not backed_up:
                    self._backup_and_rotate(state_path, wiring_path)
                    backed_up = True
                state_c, wiring_c = marshal.loads(snap)
                del snap
                s_tmp = safe_io._write_tmp_json(state_path, state_c)
                try:
                    w_tmp = safe_io._write_tmp_json(wiring_path, wiring_c)
                except BaseException:
                    _unlink(s_tmp)
                    raise
                del state_c, wiring_c
                with store._lock:
                    if self._token() == token:
                        self._install(s_tmp, state_path, w_tmp, wiring_path)
                        logger.info("Saved quam_state to %s", target)
                        return target
                    if self._only_edits_since(token, logged):
                        self._install(s_tmp, state_path, w_tmp, wiring_path,
                                      clear=len(logged))
                        logger.info("Saved quam_state to %s (%d later edit(s) still pending)",
                                    target, len(store.change_log))
                        return target
                # an undo / discard / reload landed while the bytes were
                # rendered: this snapshot no longer describes memory
                _unlink(s_tmp)
                _unlink(w_tmp)
            if not backed_up:
                self._backup_and_rotate(state_path, wiring_path)
            with store._lock:
                self._write_locked(state_path, wiring_path)
            logger.info("Saved quam_state to %s", target)
            return target

    # w8/locks helpers ---------------------------------------------------

    def _token(self) -> tuple:
        """The store's content token: every edit, undo and reload moves
        ``mutation_seq`` under the store lock; a reload also swaps the
        documents."""
        st = self.store
        return (st.mutation_seq, id(st.merged), id(st.state), id(st.wiring))

    def _only_edits_since(self, token: tuple, logged: list) -> bool:
        """Is everything that happened since *token* an edit APPENDED to the
        change log after the *logged* entries -- one journaled step per new
        entry, on its path, the documents themselves unswapped? Then the
        snapshot is the content before those edits. Under the store lock."""
        st = self.store
        if (id(st.merged), id(st.state), id(st.wiring)) != token[1:]:
            return False
        log = st.change_log
        n = len(logged)
        if len(log) <= n or any(a is not b for a, b in zip(log, logged)):
            return False
        since = getattr(st, "mutations_since", None)
        steps = since(token[0]) if since is not None else None
        new = log[n:]
        return (steps is not None and len(steps) == len(new)
                and all(step[1] == e.dot_path for step, e in zip(steps, new)))

    def _install(self, s_tmp: Path, state_path: Path, w_tmp: Path, wiring_path: Path,
                 clear: int | None = None) -> None:
        """Swap both staged files in (state first, as the save always wrote
        them) and clear the change log -- all of it, or its first *clear*
        entries. Under the store lock. A failed state swap drops the wiring
        tmp too; a failed wiring swap leaves the new state, exactly as the
        sequential write did -- and the log intact."""
        safe_io._replace_state_or_drop_wiring_tmp(s_tmp, state_path, w_tmp)
        safe_io._replace_into_place(w_tmp, wiring_path)
        if clear is None:
            self.last_cleared = list(self.store.change_log)
            self.store.change_log.clear()
        else:
            self.last_cleared = list(self.store.change_log[:clear])
            del self.store.change_log[:clear]

    def _write_locked(self, state_path: Path, wiring_path: Path) -> None:
        """The single-hold save: render, stage and swap the live documents,
        under the store lock the caller holds."""
        s_tmp = safe_io._write_tmp_json(state_path, self.store.state)
        try:
            w_tmp = safe_io._write_tmp_json(wiring_path, self.store.wiring)
        except BaseException:
            _unlink(s_tmp)
            raise
        self._install(s_tmp, state_path, w_tmp, wiring_path)

    def _backup_and_rotate(self, state_path: Path, wiring_path: Path) -> None:
        # Microseconds in the stamp: two saves inside one second used to
        # produce the SAME .bak name — the second save's copy2 overwrote
        # the first save's backup, silently losing the older pre-image.
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_%f")
        self._backup(state_path, stamp)
        self._backup(wiring_path, stamp)
        self._rotate_backups(state_path)
        self._rotate_backups(wiring_path)

    # ------------------------------------------------------------------
    # CSV export
    # ------------------------------------------------------------------

    def export_csv(
        self, path: Path | str, properties: list[str] | None = None,
        with_units: bool = True,
    ) -> Path:
        """Export a qubit summary table as CSV.

        Uses ``csv.DictWriter`` from the standard library (no pandas).
        If *properties* is None, uses a sensible default set. When *with_units*
        is True (default), dimensioned columns are unit-labeled and converted to
        display units (``f_01_GHz``, ``T1_us``); pass False for raw-SI columns
        with bare headers (legacy pipelines).
        """
        path = Path(path)
        props = (properties if properties is not None
                 else default_properties(self.store)[1:])

        engine = QueryEngine(self.store)
        rows = engine.summary_table(props)

        fieldnames, rows = _labeled_columns(props, rows, with_units)

        # Neutralize spreadsheet formula injection in both headers and cells — chip
        # string values (and any attacker-influenced column) starting with = @ + -
        # would execute as a formula when the CSV is opened in Excel/Sheets.
        from quam_state_manager.core.report_card import csv_safe_cell
        fieldnames = [csv_safe_cell(fn) for fn in fieldnames]
        rows = [{csv_safe_cell(k): csv_safe_cell(v) for k, v in row.items()} for row in rows]

        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(rows)

        logger.info("Exported CSV to %s (%d rows, %d columns)", path, len(rows), len(fieldnames))
        return path

    # ------------------------------------------------------------------
    # Markdown export
    # ------------------------------------------------------------------

    def export_markdown(
        self, path: Path | str, properties: list[str] | None = None,
        with_units: bool = True,
    ) -> Path:
        """Export a qubit summary table as a Markdown table.

        Uses plain string formatting (no external dependencies). *with_units*
        behaves as in :meth:`export_csv`.
        """
        path = Path(path)
        props = (properties if properties is not None
                 else default_properties(self.store)[1:])

        engine = QueryEngine(self.store)
        rows = engine.summary_table(props)

        fieldnames, rows = _labeled_columns(props, rows, with_units)

        col_widths = {col: len(col) for col in fieldnames}
        formatted_rows: list[dict[str, str]] = []
        for row in rows:
            fmt: dict[str, str] = {}
            for col in fieldnames:
                val = row.get(col)
                text = _format_value(val)
                fmt[col] = text
                col_widths[col] = max(col_widths[col], len(text))
            formatted_rows.append(fmt)

        lines: list[str] = []

        header = "| " + " | ".join(col.ljust(col_widths[col]) for col in fieldnames) + " |"
        separator = "| " + " | ".join("-" * col_widths[col] for col in fieldnames) + " |"
        lines.append(header)
        lines.append(separator)

        for fmt in formatted_rows:
            line = "| " + " | ".join(fmt[col].ljust(col_widths[col]) for col in fieldnames) + " |"
            lines.append(line)

        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")

        logger.info("Exported Markdown to %s (%d rows)", path, len(rows))
        return path

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _backup(file_path: Path, stamp: str) -> None:
        """Create a timestamped backup of an existing file."""
        if not file_path.exists():
            return
        bak_path = file_path.parent / f"{file_path.name}.bak.{stamp}"
        shutil.copy2(file_path, bak_path)
        logger.debug("Backup: %s -> %s", file_path.name, bak_path.name)

    def _rotate_backups(self, file_path: Path) -> None:
        """Prune timestamped backups beyond the retention limit.

        Keeps the *backup_retention* most recent ``.bak.<timestamp>`` files
        for *file_path*; deletes older ones. Order is by the embedded
        timestamp so out-of-order mtimes (e.g. after a folder copy) don't
        cause the wrong files to be pruned.
        """
        parent = file_path.parent
        if not parent.is_dir():
            return
        prefix = f"{file_path.name}.bak."
        backups: list[tuple[str, Path]] = []
        for entry in parent.iterdir():
            if not entry.name.startswith(prefix):
                continue
            m = _BACKUP_RE.search(entry.name)
            if m:
                backups.append((m.group(1), entry))
        if len(backups) <= self.backup_retention:
            return
        backups.sort(key=lambda b: b[0], reverse=True)  # newest first
        for _stamp, old in backups[self.backup_retention:]:
            try:
                old.unlink()
                logger.debug("Rotated old backup: %s", old.name)
            except OSError as exc:
                logger.warning("Could not delete old backup %s: %s", old, exc)


def _labeled_columns(
    props: list[str], rows: list[dict[str, Any]], with_units: bool
) -> tuple[list[str], list[dict[str, Any]]]:
    """Return ``(fieldnames, rows)`` for export.

    When *with_units* is True, dimensioned columns get a unit-suffixed header
    (``f_01_GHz``, ``T1_us``, ``anharmonicity_MHz``, …) and their values are
    converted from raw SI to that display unit — so a shared CSV/Markdown can't
    be misread (a bare ``T1`` of ``2.4e-05`` is exactly the footgun this fixes).
    When False, the legacy raw-SI columns with bare headers are emitted, for
    pipelines that parse the old format. ``id`` and unitless columns are
    unchanged either way.
    """
    field_cols = [p for p in props if p != "id"]
    if not with_units:
        return ["id"] + field_cols, rows

    fieldnames = ["id"] + [units.export_header(p) for p in field_cols]
    out_rows: list[dict[str, Any]] = []
    for row in rows:
        new_row: dict[str, Any] = {"id": row.get("id")}
        for p in field_cols:
            new_row[units.export_header(p)] = units.export_value(p, row.get(p))
        out_rows.append(new_row)
    return fieldnames, out_rows


def _format_value(val: Any) -> str:
    """Format a value for display in a Markdown table cell."""
    if val is None:
        return "-"
    if isinstance(val, float):
        if abs(val) >= 1e6 or (0 < abs(val) < 1e-3):
            return f"{val:.6e}"
        return f"{val:.6f}"
    if isinstance(val, list):
        return "[...]"
    if isinstance(val, dict):
        return "{...}"
    return str(val)
