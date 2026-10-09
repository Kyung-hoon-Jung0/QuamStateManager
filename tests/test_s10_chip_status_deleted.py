"""S10 C5: the Chip Status / Param History snapshot paths stay deleted.

Trends, the typeahead, the metric meta, the sparkline popup, the chip report's
Trends, the Param History grid, its cell drawer, Changes and its typeahead read
the chip's change ledger only. This static pin fails if any name the deletion
removed comes back anywhere in the package (code, templates, JS, CSS).
"""
from __future__ import annotations

# S10 C7: old -> new, remove callerless snapshot hooks and retain ledger behavior.

import inspect
import re
from pathlib import Path

from quam_state_manager.core import metric_meta
from quam_state_manager.web import routes

_PKG = Path(__file__).resolve().parents[1] / "quam_state_manager"

# names that existed only for the deleted paths
_UNIQUE = ("_trend_point_writers", "install_trends_prewarm", "_METRIC_META_SNAP_CACHE",
           "_legacy_topology_metric_meta", "_PH_CHANGES_MEMO", "_legacy_param_history_changes",
           "_WRITER_BUDGET_S", "TRUNCATED_VERIFY_SNAPS", "_capturedLine", "hub_fallback",
           "writers_pending", "_HIST_VALUE_PATHS")
# metric_meta's snapshot fold: generic words, so only a metric_meta reference counts
_FOLD = ("current_values", "_num_eq", "verify_truncated", "snapshot_values", "newest_change")
# the tripwire reasons of the deleted arms
_SURFACES = ("trends", "trends_paths", "metric_meta", "report_trends", "sparklines",
             "param_history_grid", "changes", "changes_paths", "param_history_expand")


def _sources():
    for f in sorted(_PKG.rglob("*")):
        if f.suffix not in (".py", ".html", ".js", ".css") or "vendor" in f.parts \
                or "plotly" in f.name:
            continue
        yield f.relative_to(_PKG).as_posix(), f.read_text(encoding="utf-8", errors="replace")


def test_the_chip_status_and_param_history_snapshot_paths_stay_deleted():
    hits = []
    for rel, text in _sources():
        hits += [(rel, n) for n in _UNIQUE if re.search(rf"\b{re.escape(n)}\b", text)]
        hits += [(rel, n) for n in _FOLD
                 if re.search(rf"\b(?:_?mm|metric_meta)\.{re.escape(n)}\b", text)]
        hits += [(rel, f"tripwire:{s}") for s in _SURFACES
                 if re.search(rf"_hub_fallback_" rf"reached\(\s*[\"']{s}[\"']", text)]
    assert hits == [], hits
    assert [n for n in _FOLD + ("TRUNCATED_VERIFY_SNAPS",) if hasattr(metric_meta, n)] == []
    params = inspect.signature(routes._topology_trends_html).parameters
    assert "fallback_note" not in params and "volatile" not in params
