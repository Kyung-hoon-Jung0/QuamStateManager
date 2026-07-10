"""Persist QUAM state to disk with atomic writes, auto-backup, and exports.

Saver wraps a QuamStore and provides:
  - Atomic save via :func:`core.safe_io.atomic_write_json` — the same
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

# Matches files like "state.json.bak.20260522_174501"
_BACKUP_RE = re.compile(r"\.bak\.(\d{8}_\d{6})$")

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


class Saver:
    """Persist a QuamStore back to disk and export summaries."""

    def __init__(self, store: QuamStore, backup_retention: int = DEFAULT_BACKUP_RETENTION) -> None:
        self.store = store
        self.backup_retention = max(1, int(backup_retention))

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
        """
        with self.store._lock:
            target = Path(folder_path) if folder_path else self.store.folder_path
            target.mkdir(parents=True, exist_ok=True)

            state_path = target / "state.json"
            wiring_path = target / "wiring.json"

            stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
            self._backup(state_path, stamp)
            self._backup(wiring_path, stamp)
            self._rotate_backups(state_path)
            self._rotate_backups(wiring_path)

            self._atomic_write(state_path, self.store.state)
            self._atomic_write(wiring_path, self.store.wiring)

            self.store.change_log.clear()

            logger.info("Saved quam_state to %s", target)
            return target

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
        props = properties if properties is not None else DEFAULT_PROPERTIES[1:]

        engine = QueryEngine(self.store)
        rows = engine.summary_table(props)

        fieldnames, rows = _labeled_columns(props, rows, with_units)

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
        props = properties if properties is not None else DEFAULT_PROPERTIES[1:]

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

    @staticmethod
    def _atomic_write(file_path: Path, data: dict) -> None:
        """Write JSON data atomically.

        Delegates to :func:`core.safe_io.atomic_write_json`, which writes a
        ``.tmp`` sibling (flushed + fsync'd), then replaces the target via
        ``ReplaceFileW`` on Windows (or ``os.replace`` on POSIX) with three
        backoff-retried attempts. This is the same chokepoint the
        ``apply-to-live`` flow uses, so save and apply share a single
        well-tested atomic-write path. ``safe_io.LiveFileError`` propagates
        as ``OSError`` (its base class) so existing callers see the same
        error type they did before.
        """
        safe_io.atomic_write_json(file_path, data)


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
