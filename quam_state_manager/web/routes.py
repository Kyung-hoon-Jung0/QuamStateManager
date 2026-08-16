"""Flask routes for the QUAM State Manager web dashboard.

All routes return either full pages (Jinja2) or HTML fragments (for HTMX
partial swaps). The ``/search`` endpoint is designed for 150ms-debounced
keyup triggers, returning a results fragment in <5ms.

Context model
-------------
The app uses a multi-context registry (``app.config["contexts"]``) so that
future data types (HDF5, datasets) can coexist with QUAM.  Each context is
a dict with at least ``{"type": str, "path": str}`` plus type-specific
objects.  Helper functions ``_store()``, ``_engine()``, etc. pull from the
active context transparently — route code never touches ``app.config``
directly.
"""

from __future__ import annotations

import copy
import csv
import gzip
import hashlib
import io
import json
import logging
import math
import os
import re
import shutil
import sys
import tempfile
import threading
import time
from collections import Counter, OrderedDict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlencode

from flask import (
    Blueprint,
    current_app,
    jsonify,
    make_response,
    redirect,
    render_template,
    request,
    send_file,
    url_for,
)
from markupsafe import escape
from werkzeug.utils import secure_filename

from quam_state_manager.core import (
    capabilities,
    chip_health,
    config_generator,
    config_view,
    cr_semantics,
    diagnostics,
    gen_presets,
    json_diff,
    node_scan,
    path_match,
    qualibrate_config,
    regenerate,
    safe_io,
    scheduler,
    undo_journal,
    working_copy,
)
from quam_state_manager.core import compare as compare_engine
from quam_state_manager.core import compare_sources
from quam_state_manager.core.dataset import DatasetStore
from quam_state_manager.core.differ import Differ
from quam_state_manager.core.experiment_data import ExperimentContext, load_experiment_context
from quam_state_manager.core.history import (
    DEFAULT_TRACKED_PROPERTIES,
    HistoryManager,
    chip_name_for,
)
from quam_state_manager.core.loader import QuamStore
from quam_state_manager.core.modifier import Modifier
# Curated property/column specs (pure data) — extracted to core.param_specs
# (docs/49 Compare-hub redesign, A8) so core/compare.py can use them without
# importing this module. Re-imported here as a SHIM: every existing consumer
# (templates via route code, tests importing from routes) keeps working.
# NOTE: _FREQ_TWIN_RULES stays below in this module (edit-route-coupled); the
# future Compare-hub divergence badge must reuse it, not duplicate the rule.
from quam_state_manager.core.param_specs import (  # noqa: F401  (re-export shim)
    _ALL_QUBIT_PROPS,
    _ALL_TABLE_PROPS,
    _BULK_COLUMNS_SPEC,
    _COMPARE_PROPS,
    _PAIR_PROPERTY_MAP,
    _QUBIT_PROPERTY_MAP,
    _TABLE_PROP_GROUPS,
)
from quam_state_manager.core.pointer_resolver import is_pointer, is_self_ref
from quam_state_manager.core.pulse_index import PulseIndex
from quam_state_manager.core.query import QueryEngine
from quam_state_manager.core.saver import Saver
from quam_state_manager.core.scanner import Workspace
from quam_state_manager.core.search_index import SearchIndex
from quam_state_manager.core.units import group_digits

logger = logging.getLogger(__name__)

bp = Blueprint("main", __name__)


@bp.app_errorhandler(500)
def _json_500(err):
    """App-wide net (feedback #5): any unhandled 500 on an explicit JSON XHR returns
    structured JSON, never a Werkzeug HTML error page — so a client doing
    ``fetch().then(r => r.json())`` (the live-sync surfaces) can't mis-parse an HTML
    500 as a generic "network error" with no recourse. HTMX requests are left as HTML
    (HTMX swaps the body), and normal page loads keep the default error page."""
    wants_json = (
        request.headers.get("X-Requested-With") == "XMLHttpRequest"
        or "application/json" in request.headers.get("Accept", "")
    ) and request.headers.get("HX-Request") != "true"
    if wants_json:
        return jsonify(ok=False, transient=False,
                       error="Internal error — please retry"), 500
    return err


# _PAIR_PROPERTY_MAP / _QUBIT_PROPERTY_MAP / _BULK_COLUMNS_SPEC live in
# core/param_specs.py (re-imported above — see the shim note at the import).


def _bulk_display(v: Any) -> str:
    """Format a cell value for the editable input — LOSSLESS full-digit + comma
    grouping (``units.group_digits``). The old ``%.6e`` rounded to 7 sig figs and,
    because that rounded string was reused as the edit baseline, silently dropped
    the sub-kHz tail of real frequencies on round-trip. group_digits round-trips
    exactly with ``type_policy.parse_value`` (the historical cli parser)."""
    from quam_state_manager.core import units
    return units.group_digits(v)


def _is_numeric_string(v: Any) -> bool:
    """True for a str leaf that parses as a number — the "0.13" stored as text
    anomaly (r14). Pointers are their own kind, never flagged."""
    if not isinstance(v, str) or v.startswith("#"):
        return False
    try:
        float(v)
        return True
    except ValueError:
        return False


# LO-coupled MW-FEM port fields — these cells get peer/band metadata in the 2nd pass.
_LO_FIELDS = ("band", "upconverter_frequency", "downconverter_frequency")


def _build_bulk_cell(merged: dict, alias: str, modified: dict,
                     port_info: dict, owner: str) -> dict[str, Any]:
    """Resolve ONE bulk-grid cell from a dot-path *alias* through the QUAM pointer
    system. Shared verbatim by the qubit grid and the pair grid so both render
    identical cell semantics (value, shared-port linking, modified marker) and
    commit through the same ``/field/edit-batch`` path. Accumulates ``port_info``
    for the LO/band 2nd pass (``_attach_lo_meta``); ``owner`` is the qubit/pair id
    that labels a shared port. The returned dict carries a transient ``_port`` key
    the 2nd pass pops."""
    from quam_state_manager.core.pointer_path import resolve_field_target
    from quam_state_manager.core import mw_fem, physical_units
    try:
        ft = resolve_field_target(merged, alias)
    except Exception:
        ft = {}
    resolvable = bool(ft.get("resolvable"))
    resolved = ft.get("resolved_path") or alias
    val = ft.get("resolved_value") if resolvable else None
    # resolved_value is scalar-nulled for containers, so a real LIST leaf reads
    # as null — one cheap walk (only on the resolvable-but-null case) detects it
    # for the ✎ list-cell swap.
    is_list = False
    if resolvable and val is None:
        from quam_state_manager.core.pointer_path import _walk as _walk_abs
        found, node = _walk_abs(merged, resolved.split("."))
        is_list = bool(found) and isinstance(node, list)
    p = mw_fem.port_of_resolved(resolved)
    if p:
        kind, con, fem, port, field = p
        info = port_info.setdefault((kind, con, fem, port),
                                    {"qubit": owner, "band": None, "freq": None})
        info["qubit"] = owner
        if field == "band":
            info["band"] = val
        elif field in ("upconverter_frequency", "downconverter_frequency"):
            info["freq"] = val
    return {
        "dot_path": alias,            # what we POST (edit-batch re-resolves)
        "resolved_path": resolved,    # what the change_log keys on
        "display": _bulk_display(val),
        "is_pointer": bool(ft.get("is_pointer")),
        "missing": (not resolvable) or val is None,
        "linkable": resolvable,
        "modified": resolved in modified,
        "old_display": _bulk_display(modified.get(resolved)),
        # a resolved LIST can't be a text input — the caller swaps in the ✎
        # JSON-preview cell (dynamic listedit columns; curated defensively)
        "is_list": is_list,
        # r14 honesty: a STRING that reads like a number ("0.13") used to be
        # byte-identical to the float in the input — flag it so the cell can
        # show the quotes + a stored-as-text warning (the user literally could
        # not see, let alone fix, an externally string-ified value).
        "str_numeric": _is_numeric_string(val),
        # docs/109: what actually leaves the instrument for this amplitude —
        # MW: FSP + 20·log10|amp| in dBm; LF/flux: the value IS volts. None
        # (blank) whenever the chain doesn't fully resolve — never invented.
        "phys": physical_units.amp_annotation(merged, resolved, val),
        "_port": p,
    }


def _attach_lo_meta(cell: dict, port_info: dict) -> None:
    """2nd pass: attach LO/band peer metadata to a port cell so the client can show
    "shares LO with qX (band N)" and warn when a frequency leaves its band."""
    from quam_state_manager.core import mw_fem
    p = cell.pop("_port", None)
    if p and p[4] in _LO_FIELDS:
        kind, con, fem, port, field = p
        me = port_info.get((kind, con, fem, port), {})
        peer = mw_fem.lo_peer(kind, port)
        peer_info = port_info.get((peer[0], con, fem, peer[1]), {}) if peer else {}
        cell["lo"] = {
            "field": "band" if field == "band" else "freq",
            "band": me.get("band"),
            "freq": me.get("freq"),
            "peer_qubit": peer_info.get("qubit"),
            "peer_band": peer_info.get("band"),
        }


def _bulk_column_groups(columns: list[dict]) -> list[dict[str, Any]]:
    """Build the spanning group-header band AND stamp each column's ``group_start``.
    Contiguous runs of the same ``section`` become one spanning header cell."""
    column_groups: list[dict[str, Any]] = []
    prev_section: str | None = None
    for idx, col in enumerate(columns):
        col["group_start"] = idx > 0 and col["section"] != prev_section
        if not column_groups or column_groups[-1]["section"] != col["section"]:
            column_groups.append({"section": col["section"], "label": col["section"],
                                  "total": 0, "visible": 0, "group_start": idx > 0})
        g = column_groups[-1]
        g["total"] += 1
        if col["default_on"]:
            g["visible"] += 1
        prev_section = col["section"]
    return column_groups


# docs/120 item 4 — quick-filter chips for the Live-Edit search box.
#
# The customer's daily loop is "go to the search box and TYPE x180, amp, ro,
# power ... over and over", and they asked for those to be clickable instead:
# "it's very repetitive and eats time". A real chip carries 314 columns in 22
# sections, so a chip per section would be a wall — but a SUBSTRING keyword
# spans the related bands for free (`xy` covers XY Drive / XY Port / XY+ /
# XY Port+), which is exactly why the client haystack now includes `section`.
#
# Curated for the order and the short words a user actually thinks in; DERIVED
# for the coverage guarantee. Anything the curated list does not already reach
# is appended from the chip's own section names, so the set is complete by
# construction on any chip and never offers a term that matches nothing.
_BULK_CHIP_TERMS: tuple[tuple[str, str], ...] = (
    ("freq", "Freq"),
    ("xy", "XY"),
    ("readout", "Readout"),
    ("resonator", "Resonator"),
    ("flux", "Flux"),
    ("coupler", "Coupler"),
    ("amp", "Amp"),
    ("power", "Power"),
    ("length", "Length"),
    ("delay", "Delay"),
    ("offset", "Offset"),
    ("filter", "Filter"),
    ("phase", "Phase"),
    ("coherence", "Coherence"),
    ("fidelity", "Fidelity"),
    ("port", "Port"),
    ("band", "Band"),
)


def _bulk_filter_chips(*column_sets: list[dict]) -> list[dict[str, Any]]:
    """The chip row, validated against THIS chip's real columns.

    One entry per offered term: ``{term, label, n}``. ``n`` is how many columns
    the term reaches, across every grid that shares the one search box (the
    qubit grid and the pair grid both read ``#bulk-search``), so a chip is only
    rendered when pressing it would do something.

    The haystack mirrors ``_colHay`` in bulk-edit.js — label + key + section +
    search — because a chip that matched here and not there would be a lie.
    """
    hays: list[str] = []
    sections: list[str] = []
    for cols in column_sets:
        for c in cols or []:
            hays.append(" ".join((
                str(c.get("label") or ""), str(c.get("key") or ""),
                str(c.get("section") or ""), str(c.get("search") or ""),
            )).lower())
            sec = str(c.get("section") or "").strip()
            if sec and sec not in sections:
                sections.append(sec)

    def _hits(term: str) -> int:
        return sum(1 for h in hays if term in h)

    chips: list[dict[str, Any]] = []
    taken: set[str] = set()
    for term, label in _BULK_CHIP_TERMS:
        n = _hits(term)
        if n:
            chips.append({"term": term, "label": label, "n": n})
            taken.add(term)

    # Coverage sweep: any band no curated chip already reaches gets its own, so
    # the set is complete on a chip whose architecture nobody has thought of.
    #
    # The term is the section's FIRST WORD. It has to be a single token because
    # the search splits on whitespace — a two-word term would silently become
    # an AND of two tokens — and it usefully collapses siblings: one `cz` chip
    # covers CZ Unipolar / CZ Flattop / CZ Bipolar.
    #
    # Coverage is tested PREFIX-wise against each word, not by substring
    # anywhere in the name. Substring matching looked right and quietly dropped
    # the CZ bands on a real chip: the `Z+` section contributes the term "z",
    # and "z" is a substring of "cz", so every gate band read as already
    # covered. A prefix test keeps "freq" ⊃ "Frequencies" while letting "cz"
    # through.
    def _covered(sec_words: list[str]) -> bool:
        return any(w.startswith(t) for w in sec_words for t in taken)

    for sec in sections:
        words = [w.strip("+*·").lower() for w in sec.split()]
        words = [w for w in words if w]
        if not words or _covered(words):
            continue
        term = words[0]
        if term in taken:
            continue
        n = _hits(term)
        if n:
            chips.append({"term": term, "label": sec.rstrip("+").strip(), "n": n})
            taken.add(term)
    return chips


def _bulk_col_maxlen(columns: list[dict], grid: dict, ids: list[str]) -> None:
    """Per-column display width = the widest value IN THAT COLUMN (uniform cells).

    r11: +4 (not +1) — ~3ch beyond the widest value reserves room for the
    focused cell's 🕘 value-history icon (20px) right after the text, so the
    NATURAL column width is already "value + clock, tight" (the dblclick
    auto-fit resets to this). Cap 26→28 keeps the reserve on long values."""
    for ci, col in enumerate(columns):
        widest = max((len(grid[i][ci]["display"]) for i in ids), default=4)
        col["maxlen"] = min(max(widest + 4, len(col["label"]) // 2 + 4, 6), 28)


# ======================================================================
# Multi-context helpers
# ======================================================================


def _ws() -> Workspace:
    return current_app.config["workspace"]


def _active_ctx() -> dict[str, Any] | None:
    """Return the currently active context dict, or *None*."""
    name = current_app.config["active_context"]
    if name is None:
        return None
    return current_app.config["contexts"].get(name)


def _ctx_obj(key: str) -> Any:
    """Retrieve a named object from the active context."""
    ctx = _active_ctx()
    if ctx is None:
        return None
    return ctx.get(key)


def _context_type() -> str | None:
    """Return the type string of the active context (e.g. ``"quam"``)."""
    ctx = _active_ctx()
    return ctx["type"] if ctx else None


def _active_path() -> str | None:
    """Return the filesystem path of the active context."""
    ctx = _active_ctx()
    return ctx["path"] if ctx else None


def _store() -> QuamStore | None:
    return _ctx_obj("store")


def _engine() -> QueryEngine | None:
    return _ctx_obj("engine")


def _index() -> SearchIndex | None:
    return _ctx_obj("index")


def _modifier() -> Modifier | None:
    return _ctx_obj("modifier")


def _saver() -> Saver | None:
    return _ctx_obj("saver")


def _history() -> HistoryManager:
    return current_app.config["history_manager"]


_DEFAULT_PER_PAGE = 50


def _int_arg(name, default, *, source=None, minimum=None):
    """Safely parse an int request arg/form value.

    Falls back to ``default`` when the value is missing or non-numeric (so a
    fuzzed ``?page=abc`` can't 500 the page — htmx would swap the 500 into the
    pane and the menu reads as "dead"). ``minimum`` floors the result.
    """
    src = request.args if source is None else source
    try:
        val = int(src.get(name, default))
    except (TypeError, ValueError):
        val = default
    if minimum is not None and val < minimum:
        val = minimum
    return val


def _float_arg(name, default, *, source=None):
    """Safely parse a float request arg/form value; ``default`` on bad input."""
    src = request.args if source is None else source
    try:
        return float(src.get(name, default))
    except (TypeError, ValueError):
        return default


def _paginate(items: list, page: int, per_page: int) -> tuple[list, int, int, int]:
    """Slice a list for pagination.

    Returns (page_items, total, current_page, total_pages).
    per_page=0 means "show all" (no slicing).
    """
    total = len(items)
    if per_page <= 0:
        return items, total, 1, 1
    total_pages = max(1, (total + per_page - 1) // per_page)
    page = max(1, min(page, total_pages))
    start = (page - 1) * per_page
    return items[start : start + per_page], total, page, total_pages


def _activate_context(name: str, context_type: str, path: str,
                      **objects: Any) -> None:
    """Register a named context and make it the active one.

    This is the generic entry point.  Type-specific activation helpers
    (e.g. ``_activate_quam``) call this after constructing their objects.
    """
    current_app.config["contexts"][name] = {
        "type": context_type,
        "path": path,
        **objects,
    }
    current_app.config["active_context"] = name


# Cache: live_folder_path_str → context_dict.
# Bounded to _QUAM_CACHE_MAX entries — oldest evicted on overflow.  An entry
# is kept for the whole session: a live-file change does NOT invalidate it
# (the working copy holds the user's edits; a live change is pulled in only
# by an explicit sync).
_quam_cache: "OrderedDict[str, dict]" = OrderedDict()
_QUAM_CACHE_MAX = 10
# Phase 4 §2 — guards ``_quam_cache`` lookups + LRU evictions + insertions
# AND the ``current_app.config["contexts"]`` / ``["active_context"]``
# publication step. Two concurrent ``/load`` requests for the same folder
# used to race: both built a fresh WorkingCopy + QuamStore (~50 ms of
# duplicate work), both tried ``_quam_cache.pop(next(iter(_quam_cache)))``
# with the same victim, the active-context handle flickered to whichever
# landed last, AND both raced on writing the on-disk working folder's
# ``state.json.tmp`` (atomic-write collision).
_quam_cache_lock = threading.Lock()
# Per-folder build locks: parallel builds for DIFFERENT folders proceed
# concurrently (their working dirs don't share); builds for the SAME
# folder serialise so the working-folder atomic write isn't racy.
# Re-entrant: every mutator of a folder's WorkingCopy / working files
# (slow-path build, cached-ctx reconcile, /state/sync, /save, the two
# apply-to-live paths) acquires this same lock — sync routes nest
# (state_sync → _sync_pull_apply_to_live), so a plain Lock would
# self-deadlock.
_quam_build_locks: dict[str, threading.RLock] = {}
_quam_build_locks_guard = threading.Lock()

# Guards the live-drift bookkeeping kept on the shared ctx dict
# (``ctx["_drift"]`` count cache + ``ctx["_drift_baseline"]`` content cache),
# which several Flask worker threads touch at once (every-page drift poll vs an
# apply/reset). Held ONLY around the cheap dict get/set/pop — never around the
# live read or the diff — so it's a leaf lock that can't invert the
# build→store→cache order and never serialises the actual I/O across chips.
_drift_lock = threading.Lock()

# Settle-gate MAX-DEFER CAP (feedback #5): after this many consecutive deferred
# polls (a chip writing faster than the 5s poll, or coarse/jittery WSL mtimes), the
# drift poll forces ONE armored content read regardless of mtime motion — so the
# count can never be pinned at a stale 0 forever while the chip really drifted.
# ~3 polls ≈ 15s before the forced read; the forced read still goes through
# safe_io's torn-pair refusal (serves last-known on raise), so it can't tear the baseline.
_DRIFT_MAX_DEFERS = 3


def _get_quam_build_lock(key: str) -> threading.RLock:
    """Return the per-folder build lock for *key*, creating one if needed."""
    with _quam_build_locks_guard:
        lock = _quam_build_locks.get(key)
        if lock is None:
            lock = threading.RLock()
            _quam_build_locks[key] = lock
    return lock


def _quam_ctx_dirty(ctx: dict) -> bool:
    """True if *ctx* holds edits that live ONLY in memory (not on disk).

    ``change_log`` is the sole record of applied-but-unsaved field/bulk/pulse
    edits; ``working_dirty`` marks saved-but-unapplied edits; ``pending_reapply``
    is the stash kept for a re-apply after a pull. The working FOLDER on disk
    holds only SAVED content, so a context that is dirty in any of these senses
    cannot be reconstructed from disk — evicting it would silently lose the
    edits. Reads ``change_log`` lock-free (a bare list-truthiness read is
    GIL-atomic): acquiring ``store._lock`` here would invert the
    ``store._lock → _quam_cache_lock`` order held in ``_reconcile_cached_quam_ctx``.
    """
    store = ctx.get("store")
    return bool(
        (store is not None and store.change_log)
        or ctx.get("working_dirty")
        or ctx.get("pending_reapply")
    )


def any_unsaved_changes(app) -> bool:
    """True if ANY loaded context has in-memory edits not yet written to disk.

    ONLY counts ``change_log`` — the applied-but-unsaved field/bulk/pulse edits
    that live nowhere on disk until Save, so closing the process loses them.
    ``working_dirty`` / ``pending_reapply`` are on the working copy / recoverable
    on reload, so they are NOT a close-time data-loss and don't trigger the guard.
    Used by the desktop window-close confirmation (main.py) — reads lock-free
    (list truthiness is GIL-atomic), never raises into the close handler.
    """
    try:
        for ctx in (app.config.get("contexts") or {}).values():
            store = ctx.get("store")
            if store is not None and getattr(store, "change_log", None):
                return True
    except Exception:  # noqa: BLE001 — never trap the user in an unclosable window
        pass
    return False


# Throttle for the ground-truth (content-hash) divergence re-check. MUST stay well
# above the ~3s topology live-poll interval (app.js topoLivePollInterval): the cheap
# os.stat mtime check runs every poll and catches normal external writes immediately,
# so this expensive full state+wiring read+sha256 is ONLY the backstop for the rare
# same-mtime rewrite. At 2.0s (< poll) it fired on essentially every poll → a full
# read+hash of a large (21-qubit) chip every 3s, wedging the worker and intermittently
# stalling topology clicks. 30s keeps the self-heal while removing that per-poll cost.
_LIVE_HASH_RECHECK_S = 30.0


def _refresh_live_diverged(ctx) -> None:
    """Throttled ground-truth re-check so a MISSED live change self-heals.

    The cheap mtime-based reconcile triggers (``live_changed``) can false-negative
    when an external writer rewrites content without advancing the float mtime
    (editor save, atomic re-save, coarse / same-second / 9p-Windows mtime). That
    leaves ``live_diverged`` stuck False forever — the "view in SM?" banner never
    reappears. On each chip-surface render + poll we re-derive divergence from a
    content hash (throttled to once / ``_LIVE_HASH_RECHECK_S``) and ONLY escalate
    False→True — never clear it, never touch change_log / working_dirty /
    pending_reapply. Skips dirty contexts (the explicit sync/apply paths own those)
    and never raises into the caller.
    """
    if not ctx or ctx.get("type") != "quam" or ctx.get("live_diverged"):
        return
    if _quam_ctx_dirty(ctx):
        return
    wc = ctx.get("working_copy")
    if wc is None:
        return
    now = time.monotonic()
    last = ctx.get("_live_hash_checked_at")
    if last is not None and (now - last) < _LIVE_HASH_RECHECK_S:
        return
    ctx["_live_hash_checked_at"] = now
    # NON-BLOCKING build lock: if a sync/reconcile is in flight (it holds the lock,
    # order build_lock→store._lock), skip this cycle and re-check in a couple of
    # seconds. A non-blocking acquire can NEVER deadlock — _ctx() runs on every
    # render and must never WAIT on a lock another request could hold while waiting
    # on store._lock (which would invert _reconcile_cached_quam_ctx's order). It
    # also sidesteps reading a half-written sync point mid-reconcile.
    # fs_key: the same key _activate_quam serialises builds by — a spelling
    # variant must not acquire a DIFFERENT lock for the same working folder.
    lock = _get_quam_build_lock(path_match.fs_key(ctx["path"]))
    if not lock.acquire(blocking=False):
        return
    try:
        if working_copy.live_diverged_now(wc):
            ctx["live_diverged"] = True
            # docs/116: this escalation carries no count of its own, and a
            # count left from an EARLIER divergence would be printed as if it
            # described this one. None => the banner says nothing (docs/87).
            ctx.pop("live_drift_count", None)
    except Exception:   # noqa: BLE001 — a probe failure must never break a render
        logger.debug("live-diverged re-check failed", exc_info=True)
    finally:
        lock.release()


def _evict_oldest_quam() -> None:
    """Evict the oldest CLEAN cached context to make room. Caller holds
    ``_quam_cache_lock``.

    The cap is **soft**: a context with unsaved in-memory edits is never
    evicted (the working folder holds only saved content, so dropping it would
    silently lose the edits — the root cause of the reported Bulk-Edit drift).
    Dirty contexts are pinned until the user saves / applies / discards them
    (which makes them clean and evictable). With ``_quam_cache`` as a true LRU
    (``move_to_end`` on every hit), the oldest *clean* entry is the genuinely
    idle one.

    We deliberately do NOT flush-then-evict a dirty victim here: ``saver.save``
    acquires the per-folder build lock, and taking a build lock while holding
    ``_quam_cache_lock`` would invert the ``build_lock → _quam_cache_lock`` order
    used in ``_reconcile_cached_quam_ctx`` (deadlock). Pinning is safe and
    self-correcting; realistically only a handful of chips are dirty at once.
    """
    for k in list(_quam_cache.keys()):          # oldest → newest
        if not _quam_ctx_dirty(_quam_cache[k]):
            _quam_cache.pop(k, None)
            return
    logger.warning(
        "_quam_cache over soft cap (%d entries, all dirty) — none evicted; "
        "unsaved edits are pinned in memory until saved/applied/discarded",
        len(_quam_cache))


def _active_wc_lock(ctx: dict | None = None) -> threading.RLock:
    """The build lock for *ctx*'s folder (default: the active context).

    Routes that mutate the active WorkingCopy or its working folder
    (sync, save, apply-to-live, State History restore) hold this so they
    can't interleave with a concurrent ``_activate_quam`` reconcile
    auto-sync on the same folder — two unserialised ``sync_from_live`` calls
    collide on the same ``state.json.tmp`` and can interleave the
    (mtime, mtime, hash) triplet.

    Callers that captured a context earlier in the request MUST pass it: the
    lock key is derived from the folder being written, so if the active
    context flips between capture and lock-acquisition (a concurrent
    ``/load``), re-reading the *live* active context here would hand back a
    different folder's lock while the caller writes the captured folder —
    leaving that write unserialised. Passing the captured ``ctx`` pins the
    lock to the folder actually being mutated.
    """
    if ctx is None:
        ctx = _active_ctx()
    p = (ctx or {}).get("path") or ""
    # fs_key so this hands back the SAME lock _activate_quam keys builds by;
    # the empty-path sentinel must stay "" (fs_key("") would resolve to cwd).
    return _get_quam_build_lock(path_match.fs_key(p) if p else "")


# Working copies are created one per loaded folder and (by design) never
# deleted on eviction, so they accumulate without bound — hundreds after a
# few weeks of run-snapshot browsing. Past this many, base.html shows a
# one-click "clean up?" banner (deletes only provably-clean copies).
_WC_GC_THRESHOLD = 50
# Directory-count cache, keyed by instance path (test apps run several
# instances in one process): a per-page-render listdir of a possibly huge
# folder is wasteful; the count only needs to be roughly right. Refreshed
# on GC and on process start.
_wc_count_cache: dict[str, int] = {}


def _working_copy_count() -> int:
    """Cheap (cached) count of persisted working-copy folders."""
    inst = str(current_app.instance_path)
    n = _wc_count_cache.get(inst)
    if n is None:
        try:
            root = working_copy.working_state_root(inst)
            n = sum(1 for p in root.iterdir() if p.is_dir()) if root.exists() else 0
        except OSError:
            n = 0
        _wc_count_cache[inst] = n
    return n


def _build_quam_context(folder: Path):
    """Build a fresh QUAM context tuple (wc, store, index, live_diverged)
    for *folder*.

    Pure construction — no cache mutation, no current_app touch.
    Expensive (~50 ms cold), so :func:`_activate_quam` runs this
    OUTSIDE the cache lock and then re-checks the cache under the
    lock before publishing.

    Rehydrating an existing working copy runs
    :func:`working_copy.reconcile_with_live` first, so a live folder whose
    files were *replaced* out-of-band (e.g. a different chip's state.json
    dropped in) is detected by content hash — instead of silently showing the
    old chip (the pre-fix behaviour: the working copy was rehydrated on nothing
    but file existence, surviving even restarts).

    docs/87: this USER-FACING path never auto-adopts (``sync_if_clean=False``).
    A clean working copy used to be pulled over silently, which meant the one
    person most likely to be hurt by a mis-run — someone who had edited nothing
    in SM, so SM held the good state — had it replaced without being asked. The
    stale-chip bug was the SILENCE, not the absence of automation, so a banner
    that offers the pull (and the other direction) closes it just as well. The
    machine callers of :func:`_reconcile_cached_quam_ctx` still adopt; see
    there.
    """
    instance = current_app.instance_path
    live_diverged = False
    drift_count = None
    wc = working_copy.load(instance, folder)
    if wc is None:
        wc = working_copy.create(instance, folder)
    else:
        seen: dict = {}
        result = working_copy.reconcile_with_live(
            wc, sync_if_clean=False, out=seen)
        live_diverged = result == working_copy.RECONCILE_STALE
        drift_count = _drift_count(seen)
    store = QuamStore(wc.working_folder)
    index = SearchIndex.build(store.merged, wiring_keys=set(store.wiring.keys()))
    store.search_index = index
    # Recover the "working copy holds edits not yet on live" state across a
    # restart / LRU re-load: the change-log + in-memory working_dirty flag
    # are process-local, but the working FILES persist. If they no longer
    # match the recorded sync point, the copy carries saved-but-unapplied
    # edits — surface that instead of silently hiding them (a clean apply
    # leaves working == synced, so this never false-positives). docs/87: this
    # path no longer adopts, so the old "skip when auto-synced" guard is gone
    # with it (working == live is then simply not dirty).
    working_dirty = False
    if wc.synced_live_hash is not None:
        try:
            working_dirty = (
                working_copy.content_hash(store.state, store.wiring)
                != wc.synced_live_hash)
        except Exception:
            working_dirty = False
    return wc, store, index, live_diverged, working_dirty, drift_count


def _drift_count(seen: dict) -> int | None:
    """How many values differ, from the contents the reconcile already read.

    ``None`` when it could not be computed — the banner then says that the
    live chip changed without inventing a number. Free by construction: the
    reconcile had to read both sides to reach its verdict, so this never adds
    a live-file read to a surface that renders on every page (docs/28).
    """
    live, work = seen.get("live"), seen.get("working")
    if not live or not work:
        return None
    try:
        return len(Differ().diff(work, live))
    except Exception:       # noqa: BLE001 — a count is never worth an error page
        logger.debug("drift count failed", exc_info=True)
        return None


def _reconcile_cached_quam_ctx(key: str, ctx: dict, *,
                               auto_adopt: bool = True) -> None:
    """Refresh an in-memory cached QUAM context whose live mtimes moved.

    *auto_adopt* is the docs/87 split, and it is a split between ACTORS, not
    two UX behaviours:

    - ``False`` — the user just opened or switched to this chip. Never swap
      what they are looking at underneath them: keep the working copy, set
      ``live_diverged``, and let the banner offer pull / overwrite / review.
    - ``True`` — a machine mid-loop called us: the scheduler worker's
      post-node hook, or the autofit engine's reconcile. Those must stay in
      sync to keep WORKING: autofit's gates judge a fit against the
      **pre-update anchor** (docs/47), so a stale store makes the verdict
      itself wrong. A robot mid-loop does not stop to ask.

    Unlike the slow path, the cached context may hold state that exists
    NOWHERE on disk — unsaved change-log edits, the ``working_dirty`` flag,
    a ``pending_reapply`` stash. Auto-pulling the new live content is only
    safe when both the on-disk working copy AND the in-memory context are
    clean; otherwise the working copy is served unchanged and
    ``live_diverged`` makes the UI prompt for an explicit sync.

    Runs under the per-folder build lock (the reconcile may rewrite the
    working folder — same files the slow path writes).
    """
    build_lock = _get_quam_build_lock(key)
    with build_lock:
        wc = ctx["working_copy"]
        store = ctx["store"]
        with store._lock:
            in_mem_dirty = (bool(store.change_log)
                            or bool(ctx.get("working_dirty"))
                            or bool(ctx.get("pending_reapply")))
            # Snapshot the pre-pull content (clean ctx only) so a clean auto-pull can
            # report HOW MANY params it pulled (feedback #5 — surface the silent pull).
            pre_pull = (None if (in_mem_dirty or not auto_adopt)
                        else (copy.deepcopy(store.state), copy.deepcopy(store.wiring)))
        seen: dict = {}
        result = working_copy.reconcile_with_live(
            wc, sync_if_clean=auto_adopt and not in_mem_dirty, out=seen)
        if result == working_copy.RECONCILE_SYNCED:
            # Mirror the rebuild steps of /state/sync: the working folder
            # now holds the new live content; refresh every derived object.
            with store._lock:
                # Re-check dirtiness NOW: the reconcile above did tens of
                # ms of content I/O with the store lock free, and an edit
                # (/field/edit takes only store._lock) may have landed
                # since the snapshot. reload() would clear the change log
                # — never destroy it silently: keep serving the in-memory
                # state and surface the divergence instead. (The working
                # folder + sync point already advanced to the new live;
                # a later save/sync resolves from the banner.)
                if (store.change_log or ctx.get("pending_reapply")
                        or ctx.get("working_dirty")):
                    # An edit landed during the reconcile's content I/O.
                    # sync_from_live already overwrote the on-disk working folder
                    # with the NEW live + advanced the sync point — but we're
                    # keeping the in-memory store (old live + this edit). Re-persist
                    # the in-memory content back onto the working folder so disk ==
                    # in-memory; otherwise a later LRU evict + rehydrate would read
                    # the new-live working files, compute working_dirty=False, and
                    # SILENTLY DROP the un-applied edit (audit C29). synced_live_hash
                    # stays at new-live, so the rehydrate correctly sees it dirty.
                    try:
                        safe_io.write_state_wiring(
                            wc.working_folder, store.state, store.wiring)
                    except OSError:
                        logger.exception(
                            "C29 working-folder re-persist failed for %s", key)
                    ctx["live_diverged"] = True
                    return
                try:
                    store.reload()
                    index = SearchIndex.build(
                        store.merged, wiring_keys=set(store.wiring.keys()))
                except (OSError, ValueError):
                    # The on-disk sync point is already advanced; serving
                    # the cached OLD content behind a now-clean wc would be
                    # an absorbing stale state (mtime pre-check goes quiet).
                    # Evict so the next activation rebuilds from the
                    # already-synced working folder.
                    with _quam_cache_lock:
                        if _quam_cache.get(key) is ctx:
                            _quam_cache.pop(key, None)
                    raise
                store.search_index = index
                ctx["index"] = index
                ctx["wiring_json"] = json.dumps(store.wiring)
                ctx["working_dirty"] = False
                ctx["live_diverged"] = False
                ctx.pop("live_drift_count", None)   # docs/116
            engine = ctx.get("engine")
            if engine:
                engine.invalidate_cache()
            # Invalidate THIS ctx's pulse_index too (it may not be the active
            # context yet, so the active-scoped _invalidate_engine_cache would
            # miss it) — symmetry with _rebuild_after_working_copy_replaced.
            pidx = ctx.get("pulse_index")
            if pidx is not None:
                pidx.invalidate()
            # docs/78: SM just adopted content an experiment / qualibrate wrote.
            # That is exactly the moment a string-ified or schema-drifted value
            # arrives, so let the next render raise the type popup once.
            _arm_type_alarm(ctx, "node-finished")
            # docs/94: adopted content can also carry NEW __class__ values (an
            # out-of-band class migration) — re-attach the policy and re-probe
            # so validation never judges new content by the old manifest.
            try:
                _inst = current_app.instance_path
                _attach_type_policy(ctx, _inst)
                _warm_state_schema_async(store, _inst,
                                         live_folder=ctx.get("path"))
            except Exception:  # noqa: BLE001 — healing must never break adopt
                logger.warning("post-adopt schema re-attach failed",
                               exc_info=True)
            # The live chip visibly changed and we adopted it — record a
            # Param History snapshot like the explicit /state/sync does.
            try:
                _history().check_and_snapshot(ctx.get("path"), "auto",
                                              project=_scope_for(ctx.get("path"), ctx))
            except Exception:
                logger.warning("History snapshot after auto-sync failed",
                               exc_info=True)
            if pre_pull is not None:
                try:
                    ctx["_auto_pulled"] = {"count": len(
                        Differ().diff(pre_pull, (store.state, store.wiring)))}
                except Exception:  # noqa: BLE001
                    ctx["_auto_pulled"] = {"count": 0}
        elif result != working_copy.RECONCILE_LIVE_UNREADABLE:
            # in_sync / stale are definitive verdicts; a transiently
            # unreadable live folder keeps whatever we knew before.
            ctx["live_diverged"] = result == working_copy.RECONCILE_STALE
            if result == working_copy.RECONCILE_STALE:
                ctx["live_drift_count"] = _drift_count(seen)
            else:
                ctx.pop("live_drift_count", None)


def _probe_readonly(folder) -> bool:
    """True when the live chip's files are NOT writable (docs/114 #16).

    NON-DESTRUCTIVE by construction. Two rejected designs first, because the
    reason matters:

    * ``os.access(dir, W_OK)`` is attribute-only for directories on Windows —
      it reports a read-only share as writable, i.e. it is blind to the exact
      case this exists for.
    * Creating and deleting a probe file inside the folder answers correctly
      but WRITES INTO THE CUSTOMER'S LIVE CHIP FOLDER on every activation.
      That breaks the docs/28 rule (the live files are touched only on an
      explicit Apply), litters a directory labs keep under version control,
      and leaves the file behind if the process dies mid-probe.

    So: open the EXISTING ``state.json`` for update (``r+``) and close it
    immediately. Opening for update needs the same permission the apply
    needs, and — because nothing is written — it changes no content, no
    size and no mtime. Anything unexpected answers False: this is a HINT,
    and a false alarm is worse than no alarm.
    """
    target = Path(folder) / "state.json"
    try:
        with open(target, "r+", encoding="utf-8"):
            pass
        return False
    except PermissionError:
        return True
    except OSError as exc:
        # EACCES/EROFS mean not writable; anything else (missing file, exotic
        # FS) is not evidence of a read-only share.
        return getattr(exc, "errno", None) in (13, 30)
    except Exception:  # noqa: BLE001 — a hint must never break activation
        return False


def _activate_quam(folder_path: str | Path, *, origin: str = "live") -> dict:
    """Load a QUAM state folder and register it as the active context.

    Returns the activated context dict, so a caller that must keep acting on
    THIS chip (e.g. /qualibrate/open's scope pin) can bind to it directly —
    re-reading ``_active_ctx()`` later in the request cross-wires onto
    whatever a concurrent ``/load`` activated meanwhile (the same bug class
    :func:`_active_wc_lock` documents).

    *origin* records provenance — ``"live"`` for a chip the user loaded to
    edit, ``"dataset_archive"`` for a frozen per-run snapshot opened from the
    Datasets view (read-only; mutation routes refuse to write it). It is
    refreshed on every activation so re-opening the same folder via a
    different route updates the intent.

    The folder the user selects is the *live* folder; the State Manager
    never edits it directly.  A private working copy is kept under
    ``instance/working_state/`` (see :mod:`core.working_copy`); the
    QuamStore and Saver operate on that copy, so editing and saving never
    touch the live files an experiment program may be writing.

    Working-copy lifecycle:

    1. In-memory ``_quam_cache`` hit  → reuse the live context as-is. Most
       common path; preserves the user's unsaved in-memory edits.
    2. On-disk working folder + meta exist (from a prior session, or an
       LRU-evicted in-memory context)  → :func:`working_copy.load`
       reconstructs the ``WorkingCopy`` dataclass and we build a fresh
       ``QuamStore`` on top of the existing working folder. Any Save'd
       edits the user made earlier survive — re-seeding from live would
       silently destroy them.
    3. Neither exists  → :func:`working_copy.create` seeds a fresh working
       copy from live.

    Eviction from ``_quam_cache`` (:func:`_evict_oldest_quam`) only ever drops
    a **clean** context, and only the in-memory copy: the working folder on
    disk is preserved so a later re-load takes the persistence path (#2). A
    context with unsaved in-memory edits (``change_log`` /  ``working_dirty`` /
    ``pending_reapply``) is NEVER evicted — those edits, especially the
    applied-but-unsaved ``change_log``, live nowhere on disk, so dropping the
    context would silently lose them (the Bulk-Edit drift root cause). Such
    contexts are pinned (the cap is soft) until the user saves / applies /
    discards them, at which point they become clean and evictable. The cache is
    a true LRU (``move_to_end`` on every hit), so the genuinely idle clean
    chips are evicted first.

    Concurrency (Phase 4 §2): the lookup-then-build-then-insert sequence
    runs under ``_quam_cache_lock`` for cache mutations, but the
    expensive build happens OUTSIDE the lock. A racing second request
    that beats us to the insert wins; the loser discards its build.
    """
    folder = Path(folder_path).resolve()
    # Cache + build-lock key = the per-OS canonical folder identity
    # (path_match.fs_key). A symlink or case-variant spelling of ONE folder
    # used to mint TWO contexts and TWO build locks over one working folder —
    # voiding every serialisation guarantee on it. _active_wc_lock /
    # _refresh_live_diverged derive the SAME key from ctx["path"].
    key = path_match.fs_key(folder)
    ctx_name = folder.parent.name

    # A dataset run's frozen quam_state is read-only no matter which route
    # opened it — classify it as an archive even when a default-origin route
    # (/load, /workspace/select, startup restore) activates it, so it can't be
    # opened as a live-editable copy and overwrite the experiment's record.
    if origin == "live" and _is_run_archive(folder):
        origin = "dataset_archive"

    # Fast path — cache hit. Guarded by a stat-only staleness pre-check:
    # the cache is keyed by *path*, so without it a live folder whose files
    # were replaced out-of-band would keep serving the old chip for the
    # whole session (content-blind cache — the original stale-chip bug).
    # Unchanged mtimes (the overwhelmingly common case) cost two os.stat
    # calls and publish the cached context exactly as before.
    with _quam_cache_lock:
        cached = _quam_cache.get(key)
    if cached is not None:
        wc = cached.get("working_copy")
        try:
            mtime_stale = wc is not None and working_copy.live_changed(wc)
        except OSError:
            mtime_stale = False   # unreadable live is never treated as replaced
        if mtime_stale:
            # docs/87 — the user is opening/switching a chip. Never swap what
            # they are about to look at; surface it and let them choose.
            _reconcile_cached_quam_ctx(key, cached, auto_adopt=False)
        # Publish under the cache lock, RE-VALIDATING the entry: the stat /
        # reconcile above ran outside the lock, during which the key may
        # have been LRU-evicted and rebuilt by a sibling thread — two
        # contexts on one working folder. The cache's current entry wins;
        # an evicted-but-unreplaced entry is re-inserted.
        with _quam_cache_lock:
            current = _quam_cache.get(key)
            if current is None:
                if len(_quam_cache) >= _QUAM_CACHE_MAX:
                    _evict_oldest_quam()
                _quam_cache[key] = cached
                current = cached
            else:
                _quam_cache.move_to_end(key)   # true LRU — mark most-recently used
            # Refresh provenance, but NEVER downgrade a read-only archive back
            # to live: re-opening a cached dataset_archive via a default-origin
            # route (e.g. /workspace/select on the discovered run folder) must
            # keep it read-only. Only live→archive is allowed.
            if origin != "live" or (current.get("origin") or "live") == "live":
                current["origin"] = origin
        # Project lens (docs/63): derive the scope when this context doesn't
        # carry one yet — BEFORE publication (a concurrent /datasets render
        # must never observe an active scoped chip without its memo: the
        # transient data-scope="" identity flip would wipe the user's
        # folder-filter toggles) and outside the cache lock (the reverse
        # index may do a few stats / a rare TOML re-parse). An explicitly
        # pinned name sticks.
        # docs/114 (#16): re-probe on EVERY activation — a cached chip whose
        # share was remounted read-only (or repaired) must not keep the answer
        # from the first open.
        try:
            current["live_readonly_hint"] = _probe_readonly(current["path"])
        except Exception:  # noqa: BLE001 — a hint never blocks activation
            pass
        _acquire_project_scope(current)
        # docs/20 v2: re-evaluate the first-open chip-name banner on every
        # activation (origin can flip live→archive; a staged name dismisses),
        # and adopt the chip's declared data folder(s) as workspace roots.
        _maybe_identity_confirm(current)
        _maybe_chip_name_prompt(current)
        _adopt_extras_data_folders(current)
        _maybe_data_folder_suggest(current)
        _arm_type_alarm(current, "chip-open")   # docs/78
        _publish_instance_chip(current)         # docs/80
        with _quam_cache_lock:
            current_app.config["contexts"][ctx_name] = current
            current_app.config["active_context"] = ctx_name
        return current

    # Slow path. Serialise builds for THIS folder so two threads don't
    # race on the working-folder atomic write. Builds for DIFFERENT
    # folders still run in parallel (their build locks are distinct).
    build_lock = _get_quam_build_lock(key)
    with build_lock:
        # Re-check the cache under the build lock — a sibling thread
        # may have just finished building while we waited.
        with _quam_cache_lock:
            cached = _quam_cache.get(key)
        if cached is not None:
            ctx = cached
        else:
            wc, store, index, live_diverged, working_dirty, drift_count = \
                _build_quam_context(folder)
            ctx = {
                "type": "quam",
                "path": str(folder),        # the LIVE folder (resolved) — context identity
                "live_path": str(folder),   # explicit alias for sync/apply routes
                "origin": origin,           # "live" | "dataset_archive" (read-only)
                "working_copy": wc,
                # Recovered from the persisted working-copy hash — True when a
                # prior session saved edits that were never applied to live
                # (see _build_quam_context). False on a fresh seed / clean copy.
                "working_dirty": working_dirty,
                "pending_reapply": None,    # user edits stashed for re-apply after a pull
                "live_diverged": live_diverged,  # live replaced out-of-band — ask, never adopt
                "live_drift_count": drift_count,  # how many values differ (docs/87; may be None)
                "store": store,             # store reads/saves the working copy
                "engine": QueryEngine(store),
                "index": index,
                "modifier": Modifier(store),
                "saver": Saver(store),
                "wiring_json": json.dumps(store.wiring),  # cached — immutable after load
            }
            # docs/114 (#16): write-permission preflight — a read-only live
            # folder (network share) should be KNOWN at open time, not
            # discovered at apply time after 20 minutes of edits.
            # (see _probe_readonly — a real create+delete, because
            # os.access is attribute-only for directories on Windows)
            ctx["live_readonly_hint"] = _probe_readonly(folder)
            # (docs/87) The "✓ Live chip updated — N params pulled" one-shot that
            # used to be stashed here is gone with the silent pull it announced.
            # A toast that reports a fait accompli is strictly worse than the
            # banner that now asks first — and the banner carries the same count.
            # docs/107: fresh builds (restart, LRU rehydrate) reload the
            # cross-save undo journal from its sidecar; cursor starts at tip.
            _journal_reset(ctx)
        # Project lens (docs/63): fresh builds (first load, LRU-eviction
        # rehydrates, restarts) derive their scope here — BEFORE publication
        # (a concurrent /datasets render must never observe an active scoped
        # chip without its memo); every activation path converges on the
        # reverse index, so a lost memo self-heals.
        _acquire_project_scope(ctx)
        _maybe_identity_confirm(ctx)
        _maybe_chip_name_prompt(ctx)
        _adopt_extras_data_folders(ctx)
        _maybe_data_folder_suggest(ctx)
        _arm_type_alarm(ctx, "chip-open")   # docs/78
        _publish_instance_chip(ctx)         # docs/80
        with _quam_cache_lock:
            if key not in _quam_cache:
                # Evict the oldest CLEAN in-memory entry if the cache is full.
                # _evict_oldest_quam NEVER drops a context with unsaved
                # in-memory edits: the working FOLDER holds only SAVED content,
                # so evicting a dirty context would silently lose applied-but-
                # unsaved change_log edits (the Bulk-Edit drift root cause).
                # A clean evicted chip rehydrates from its working folder via
                # the load() branch in _build_quam_context on next load.
                if len(_quam_cache) >= _QUAM_CACHE_MAX:
                    _evict_oldest_quam()
                _quam_cache[key] = ctx
            else:
                _quam_cache.move_to_end(key)   # true LRU — mark most-recently used
            current_app.config["contexts"][ctx_name] = ctx
            current_app.config["active_context"] = ctx_name
        # Attach the per-key type policy (stat-cached manifest + sidecar), then
        # background-warm the schema manifest for this chip against the
        # selected env (no-op when the cache is already warm — stat-only check).
        _attach_type_policy(ctx)
        _warm_state_schema_async(ctx.get("store"), current_app.instance_path,
                                 live_folder=ctx.get("path"))
        return ctx


_schema_warm_inflight: set[str] = set()
_schema_warm_lock = threading.Lock()


def _attach_type_policy(ctx, inst=None) -> None:
    """Attach/refresh the per-key type policy on a context's store.

    Cheap and request-path safe: a stat-keyed manifest cache read
    (``cached_only=True`` — never spawns) plus the sidecar read. A cold
    manifest yields a policy with user assignments only; a failure leaves
    the previous policy in place (never bricks activation)."""
    store = (ctx or {}).get("store")
    live = (ctx or {}).get("path")
    if store is None or not live:
        return
    from quam_state_manager.core import state_env_schema, type_policy
    try:
        if inst is None:
            inst = current_app.instance_path
        python_path = config_generator.get_selected_env(inst)
        manifest = (state_env_schema.manifest_for_store(
            store, python_path, inst, cached_only=True) if python_path else None)
        if manifest is None:
            # Keep a warm in-memory manifest across sidecar-only rebuilds
            # (type-assign/unassign) and transient cache colds (pip install
            # flips the stat signature; the warm thread re-probes) — but ONLY
            # for the SAME env; an env switch must drop it.
            prev = getattr(store, "type_policy", None)
            if (prev is not None and prev.manifest is not None
                    and getattr(store, "_type_manifest_env", None) == python_path):
                # Carry the PRISTINE env manifest, never the verdict-overlaid
                # one — re-attaching would otherwise overlay an overlay and
                # bake a revoked verdict in (docs/79).
                carried = getattr(prev, "env_manifest", None) or prev.manifest
                # docs/94 carry GATE: carry only while the previous manifest
                # still COVERS the chip's current class inventory. An
                # out-of-band class migration (a lab script swapping pulse
                # classes mid-session) grows the inventory; carrying then
                # guarantees "harvest drift" errors against a manifest KNOWN
                # to be stale — the false positive a user reported as 10×
                # error on a perfectly healthy chip. Not covered → abstain
                # (diagnostics show "Probe now") and kick the warm re-probe,
                # which heals in seconds.
                try:
                    with store._lock:
                        inv = set(state_env_schema.harvest_classes(store.state))
                except Exception:  # noqa: BLE001
                    inv = None
                if inv is not None and inv <= set(carried.get("classes") or {}):
                    manifest = carried
                else:
                    try:
                        _warm_state_schema_async(store, inst, live_folder=live)
                    except Exception:  # noqa: BLE001
                        pass
        store.type_policy = type_policy.load_policy(inst, live, manifest)
        store._type_manifest_env = python_path if manifest is not None else None
        if manifest is not None and manifest.get("pulse_roster"):
            # env pulse roster overlays the static catalog (env-verified
            # homes, false-unmodeled suppression) — global, like env selection
            from quam_state_manager.core import pulse_catalog
            pulse_catalog.apply_env_overlay(manifest["pulse_roster"])
    except Exception:  # noqa: BLE001
        logger.warning("type-policy attach failed", exc_info=True)


def _warm_state_schema_async(store, inst, live_folder=None) -> None:
    """Background-warm the state↔env schema manifest for *store*.

    Request-path safe: the only inline work is a stat-keyed cache check
    (``cached_only=True`` never spawns); the actual probe subprocess runs in a
    daemon thread with a single-flight guard per (env, class-set) — repeated
    loads/selects while a probe is in flight coalesce (the replot pattern).
    On probe success the store's type policy is re-attached so the freshly
    warmed env layer takes effect without another activation.
    """
    if store is None or inst is None:
        return
    from quam_state_manager.core import state_env_schema
    try:
        python_path = config_generator.get_selected_env(inst)
    except Exception:  # noqa: BLE001
        return
    if not python_path:
        return
    try:
        if state_env_schema.manifest_for_store(store, python_path, inst,
                                               cached_only=True) is not None:
            return                                # already warm — zero cost
        with store._lock:
            classes = state_env_schema.harvest_classes(store.state)
    except Exception:  # noqa: BLE001 — warm-up must never break activation
        return
    key = python_path + "|" + ",".join(sorted(classes))
    with _schema_warm_lock:
        if key in _schema_warm_inflight:
            return
        _schema_warm_inflight.add(key)

    def _run():
        try:
            res = state_env_schema.probe_state_schema(python_path, classes, inst)
            if res.get("ok") and live_folder:
                from quam_state_manager.core import pulse_catalog, type_policy
                manifest = {k: res[k] for k in
                            ("classes", "pulse_roster", "by_leaf",
                             "missing_classes", "versions")}
                store.type_policy = type_policy.load_policy(
                    inst, live_folder, manifest)
                store._type_manifest_env = python_path
                if manifest.get("pulse_roster"):
                    pulse_catalog.apply_env_overlay(manifest["pulse_roster"])
        except Exception:  # noqa: BLE001
            logger.warning("state-schema warm probe failed", exc_info=True)
        finally:
            with _schema_warm_lock:
                _schema_warm_inflight.discard(key)

    threading.Thread(target=_run, daemon=True).start()


def _wiring_json() -> str:
    """Return cached wiring JSON string from active context, or '{}'."""
    ctx = _active_ctx()
    if ctx and "wiring_json" in ctx:
        return ctx["wiring_json"]
    store = _store()
    return json.dumps(store.wiring) if store else "{}"


def _invalidate_engine_cache(ctx: dict | None = None) -> None:
    """Clear derived caches after a store mutation: the QueryEngine qubit/pair
    cache, the pulse_index, and the cached wiring JSON.

    ``ctx["wiring_json"]`` was assumed immutable after load, but a wiring-field
    edit (set_value writes into store.wiring) mutates it — so without refreshing
    it here the wiring / topology views render stale wiring after an edit.

    Pass the request-captured ``ctx`` so a concurrent /load that flips the
    active context mid-request can't misdirect the invalidation to the wrong
    chip — the mutated chip's derived caches would otherwise stay stale (and a
    freshly-loaded chip's caches be cleared spuriously)."""
    if ctx is None:
        ctx = _active_ctx()
    if ctx is None:
        return
    engine = ctx.get("engine")
    if engine:
        engine.invalidate_cache()
    pulse_index = ctx.get("pulse_index")
    if pulse_index is not None:
        pulse_index.invalidate()
    store = ctx.get("store")
    if store is not None:
        ctx["wiring_json"] = json.dumps(store.wiring)


def _rebuild_after_working_copy_replaced(ctx: dict) -> None:
    """Rebuild every derived object after the working folder's files were
    replaced wholesale (a sync pull, or a State History restore).

    ``store.reload()`` re-reads the working files (and nulls the cached
    generated config — a replaced state needs a fresh regenerate), then the
    search index, wiring-json, engine + pulse_index caches are rebuilt and
    the working-dirty / live-diverged flags cleared. Single shared
    entrypoint so a restore can never hand-roll a partial cache clear and
    reopen the stale-chip bug. Caller must hold the per-folder build lock.
    """
    store = ctx["store"]
    store.reload()
    index = SearchIndex.build(store.merged, wiring_keys=set(store.wiring.keys()))
    store.search_index = index
    ctx["index"] = index
    ctx["wiring_json"] = json.dumps(store.wiring)
    _invalidate_engine_cache()
    ctx["working_dirty"] = False
    ctx["live_diverged"] = False
    ctx.pop("live_drift_count", None)   # docs/116: the count dies with the verdict
    # audit-r10: a wholesale replace resolves any prior staged base (a pull
    # consumed it; a fresh stage re-sets the flag right after this call).
    ctx["staged_base"] = False
    # docs/107: the reload cleared the change log, so any staged journal steps
    # died with it — re-read the sidecar and put the cursor back at the tip
    # (the redo stack self-invalidates via the mutation_seq handshake).
    _journal_reset(ctx)
    _reseed_drift_baseline_if_chip_changed(ctx)
    # docs/78: content the user did not type just landed — let the next render
    # raise the type-anomaly popup once (pull / stage / restore / run load).
    _arm_type_alarm(ctx, ctx.pop("_alarm_reason", None) or "live-pull")
    # docs/94: the same content-entry moment must also re-attach the type
    # policy and re-probe the env schema — a migration script can have CHANGED
    # the chip's class inventory out-of-band, and the store's old policy would
    # otherwise keep validating the new content against the old manifest
    # ("harvest drift" errors on a healthy chip) until the next activation.
    try:
        inst = current_app.instance_path
        _attach_type_policy(ctx, inst)
        _warm_state_schema_async(store, inst, live_folder=ctx.get("path"))
    except Exception:  # noqa: BLE001 — healing must never break the rebuild
        logger.warning("post-rebuild schema re-attach failed", exc_info=True)


def _reseed_drift_baseline_if_chip_changed(ctx: dict) -> None:
    """C31: after the working folder was replaced (sync pull / State History
    restore), if the new content is a hardware-DIFFERENT chip than the drift
    baseline, re-seed the baseline to the new content so the global "Live changes
    since baseline" count restarts from the new chip — instead of showing a
    meaningless old-chip-vs-new-chip diff that persists until a manual reset. A
    same-chip value change leaves the baseline alone so accumulation persists.
    """
    if not _drift_tracked(ctx):
        return
    try:
        from quam_state_manager.core import history
        path = ctx.get("path")
        store = ctx["store"]
        base = _history().get_live_baseline(path)
        if base is None:
            return  # nothing established yet — next poll seeds from new content
        old_fp = history.fingerprint_from_dicts(
            base.get("state") or {}, base.get("wiring") or {})
        new_fp = history.fingerprint_from_dicts(store.state, store.wiring)
        if history.align(old_fp, new_fp) == history.ALIGN_DIFFERENT_CHIP:
            with store._lock:
                state = copy.deepcopy(store.state)
                wiring = copy.deepcopy(store.wiring)
            _history().set_live_baseline(path, state, wiring)
            _clear_drift_cache(ctx)
            logger.info("drift baseline re-seeded after a different-chip "
                        "working-copy replacement for %s", path)
    except Exception:   # noqa: BLE001 — never break a rebuild on the baseline check
        logger.warning("drift baseline re-seed check failed", exc_info=True)


def _pulse_index():
    """Lazy per-context PulseIndex (rows + reverse-pointer cache)."""
    ctx = _active_ctx()
    store = _store()
    if ctx is None or store is None:
        return None
    pulse_index = ctx.get("pulse_index")
    if pulse_index is None or pulse_index.store is not store:
        pulse_index = PulseIndex(store)
        ctx["pulse_index"] = pulse_index
    return pulse_index


def _working_dirty() -> bool:
    """True when the working copy holds changes not yet applied to the live chip."""
    ctx = _active_ctx()
    return bool(ctx.get("working_dirty")) if ctx else False


def _set_working_dirty(value: bool, ctx: dict | None = None) -> None:
    """Record whether the working copy differs from the live chip.

    Pass the request-captured ``ctx`` so a mid-request /load flip can't mark the
    wrong (freshly-loaded) chip dirty while the actually-mutated chip's flag
    stays stale — the divergence banner + blocked auto-pull then attach to the
    wrong chip."""
    if ctx is None:
        ctx = _active_ctx()
    if ctx is not None:
        ctx["working_dirty"] = value


def _active_origin() -> str:
    """Provenance of the active chip context: ``"live"`` (a real chip folder
    the user loaded) or ``"dataset_archive"`` (a frozen per-run quam_state
    snapshot activated from the Datasets view). Mutating routes refuse to
    write a non-live origin so an experiment's archived state can't be
    silently overwritten."""
    ctx = _active_ctx()
    if not ctx or ctx.get("type") != "quam":
        return "live"
    return ctx.get("origin") or "live"


def _is_run_archive(folder: str | Path) -> bool:
    """True if *folder* is a dataset run's frozen ``quam_state`` snapshot.

    A qualibrate run folder carries ``node.json`` / ``data.json`` and holds the
    snapshot in a ``quam_state`` subfolder, so a sibling ``node.json`` /
    ``data.json`` in the parent identifies the folder as an experiment's
    recorded state. Such a folder is read-only **regardless of which route
    opened it** — without this, the scanner discovers it as an ordinary
    workspace entry and ``/workspace/select`` (or ``/load``) would open it as a
    live-editable copy, letting an edit+Apply overwrite the run's record. A
    normal live chip (``.../quam_states/<chip>``) has no such sibling, so this
    never misfires on real chips."""
    try:
        parent = Path(folder).parent
        return (parent / "node.json").is_file() or (parent / "data.json").is_file()
    except OSError:
        return False


_GENERIC_FOLDER_NAMES = {"quam_state", "quam_states", "state", "states"}


def _chip_display_name(path: str | Path) -> str:
    """The most human-meaningful chip name for *path*.

    A standalone chip folder holds ``state.json`` directly and is named after
    the chip (``.../quam_states/LabA`` → ``LabA``); only when the folder
    itself is a generic container (``.../LabA/quam_state``) do we fall back
    to :func:`chip_name_for`, which understands the qualibration layout. This
    avoids the confusing ``quam_states`` label the raw parent-name gave."""
    p = Path(path)
    own = p.name
    if own and own.lower() not in _GENERIC_FOLDER_NAMES:
        return own
    try:
        derived = chip_name_for(p)
    except Exception:
        derived = None
    return derived or p.parent.name or own or "chip"


def _active_chip_identity() -> dict | None:
    """Single source of truth for "which chip am I editing, and how dirty".

    Returns ``{name, path, origin, change_count, working_dirty, live_diverged}``
    or ``None`` when no quam chip is active. The *name* is the chip-level name
    (shared across per-experiment loads of the same chip), the *path* the live
    folder (so two runs of one chip are still distinguishable). Reused by the
    pending tray, the chip header, and the mutation guards — never re-derive
    ``Path(path).parent.name`` ad hoc."""
    ctx = _active_ctx()
    if not ctx or ctx.get("type") != "quam":
        return None
    path = ctx.get("path")
    name = _chip_display_name(path) if path else None
    store = ctx.get("store")
    return {
        "name": name or (Path(path).name if path else "chip"),
        "path": path,
        "origin": ctx.get("origin") or "live",
        "change_count": len(store.change_log) if store else 0,
        "working_dirty": bool(ctx.get("working_dirty")),
        "live_diverged": bool(ctx.get("live_diverged")),
        # docs/87 — how many values differ, computed for free by the reconcile
        # that raised the flag. None when it could not be determined.
        "live_drift_count": ctx.get("live_drift_count"),
    }


def _archive_write_blocked(ctx: dict | None = None):
    """Return an error response when *ctx* is a dataset archive (a frozen
    per-run snapshot), else None. Mutating routes call this to refuse
    save/apply/restore into an archive — writing it would corrupt an
    experiment's recorded state (a real data-safety hole: the Datasets
    "load state" button activates the run's quam_state as a live-editable
    working copy).

    Callers that captured a context earlier in the request MUST pass it: the
    origin must be read off the SAME context that will be written, not the
    live-active one. A concurrent ``/load`` can flip ``active_context`` between
    capture and this guard, so re-reading the active origin here would clear
    the guard for a now-live context while the route writes the captured
    archive's live folder — silently corrupting the run's state.json (the same
    TOCTOU class the captured-ctx build lock already defends against)."""
    if ctx is None:
        ctx = _active_ctx()
    origin = (ctx.get("origin") if ctx and ctx.get("type") == "quam" else None) or "live"
    if origin == "live":
        return None
    return render_template(
        "_status.html",
        message=("This chip was opened from a dataset run archive (read-only). "
                 "Load it from its live folder to edit or apply."),
        level="error",
    ), 409


def _capture_change_log_as_updates(store) -> dict:
    """Snapshot the change log as an op-tagged ``{dot_path: (op, value)}``
    replay map, where op is ``"set" | "create" | "delete"``.

    Replay used to flatten to plain ``{path: new_value}`` and re-apply via
    ``set_value`` only — which silently dropped created subtrees after a
    pull (the path doesn't exist on the fresh state → KeyError → "failed")
    and could not represent deletions at all. Tagging the op lets
    ``_replay_updates`` dispatch to create_subtree/delete_subtree.

    Composition rules (insertion-ordered, last op per path wins):
      - create then edit(s)  → stays ``create`` (the logged subtree object is
        aliased to the live tree, so it already carries the edits; a same-path
        scalar ``set`` upgrades the stored value),
      - create then delete   → drops out entirely (net nothing),
      - delete then create   → ``replace`` (replayed with ``coerce=False``:
        the pulled live state may hold a value of a DIFFERENT type at that
        path — e.g. a string-alias op the user deleted and re-created as a
        real dict pulse — and a coercing ``set`` would stringify the dict),
      - delete subsumes any earlier entries inside the deleted subtree.
    """
    updates: dict = {}
    for entry in store.change_log:
        path = entry.dot_path
        # Deep-copy the value so the reapply stash is a true SNAPSHOT, decoupled
        # from the live merged tree (a created subtree's new_value is aliased
        # into the store, so a later edit/undo of it before the conflict resolves
        # would otherwise mutate the stashed object by reference) — audit D7.
        nv = copy.deepcopy(entry.new_value)
        if entry.created:
            prev = updates.get(path)
            if prev is not None and prev[0] == "delete":
                updates.pop(path)
                updates[path] = ("replace", nv)
            else:
                updates[path] = ("create", nv)
        elif entry.deleted:
            prev = updates.get(path)
            # Drop anything recorded inside the deleted subtree — the delete
            # subsumes it (and replaying a set under a deleted path would fail).
            prefix = path + "."
            for stale in [p for p in updates if p == path or p.startswith(prefix)]:
                updates.pop(stale)
            if prev is not None and prev[0] == "create":
                continue  # created then deleted in this session → net nothing
            updates[path] = ("delete", None)
        else:
            prev = updates.get(path)
            if prev is not None and prev[0] in ("create", "replace"):
                updates[path] = (prev[0], nv)
            else:
                updates[path] = ("set", nv)
    return updates


def _pending_reapply() -> dict:
    """The accumulated user-edit map kept for a possible re-apply after a pull."""
    return _ctx_obj("pending_reapply") or {}


def _merge_reapply(base: dict, incoming: dict) -> dict:
    """Compose an incoming op-tagged replay map onto *base* using the SAME rules as
    _capture_change_log_as_updates (which only composes WITHIN one capture).

    A working-copy Save clears the change log and stashes the capture, so a
    ``delete pulse → Save → recreate the same op`` sequence stashed ``{op: delete}``
    then a fresh capture ``{op: create}``. Plain dict.update produced a bare
    ``create``, which on a later pull-with-reapply KeyErrors (the pulled live still
    holds the original) and DROPS the user's recreated pulse. Composition-aware
    merge turns it into ``replace`` so the recreate survives.
    """
    out = dict(base)
    for path, opval in incoming.items():
        op, value = opval
        prev = out.get(path)
        if op == "create":
            out[path] = ("replace", value) if (prev and prev[0] == "delete") else ("create", value)
        elif op == "delete":
            prefix = path + "."
            for stale in [p for p in out if p == path or p.startswith(prefix)]:
                out.pop(stale)
            if prev and prev[0] == "create":
                continue   # created then deleted across captures → net nothing
            out[path] = ("delete", None)
        elif op == "replace":
            prefix = path + "."
            for stale in [p for p in out if p != path and p.startswith(prefix)]:
                out.pop(stale)
            out[path] = ("replace", value)
        else:  # set
            out[path] = (prev[0], value) if (prev and prev[0] in ("create", "replace")) else ("set", value)
    return out


def _stash_reapply(updates: dict, ctx: dict | None = None) -> None:
    """Accumulate user edits into the reapply stash.

    A working-copy ``save`` (and the implicit save inside apply-to-live) clears
    the change log, so by the time a staleness conflict surfaces the edits are
    no longer in ``store.change_log``. Stashing them here — before they are
    cleared — lets a subsequent pull re-apply or stage exactly the user's edits
    (not the experiment's changes the diff would also include).

    Pass the CAPTURED ctx on paths that captured one earlier in the request, so a
    concurrent ``/load`` flipping the active context can't make this stash land on
    the wrong chip (mirroring :func:`_clear_reapply`'s ctx pinning) — otherwise a
    later conflict retry would replay an empty stash and silently drop the edits.
    """
    if not updates:
        return
    if ctx is None:
        ctx = _active_ctx()
    if ctx is None:
        return
    # Composition-aware merge (not dict.update) so a delete→Save→recreate across
    # captures composes to 'replace' instead of a bare 'create' that drops on replay.
    ctx["pending_reapply"] = _merge_reapply(ctx.get("pending_reapply") or {}, updates)


def _clear_reapply(ctx: dict | None = None) -> None:
    """Drop the reapply stash (after a pull consumes it, or an apply succeeds).

    Pass the CAPTURED ctx on paths that captured one earlier in the request, so
    a concurrent ``/load`` flipping the active context can't make this clear the
    wrong chip's stash."""
    if ctx is None:
        ctx = _active_ctx()
    if ctx is not None:
        ctx["pending_reapply"] = None


# ======================================================================
# Cross-save undo journal + redo stack (docs/107)
# ======================================================================
# Ctrl+Z used to die at /save (saver.save clears the change log). The journal
# records each save's OUTGOING log as units so /undo can keep walking — by
# STAGING the inverse back into the tray (gid ``jrn:<unit-id>``), never by
# touching live (the SM covenant: any direct live write requires >= 1 explicit
# Apply-to-live press). The redo stack (Ctrl+Shift+Z) holds what undo/discard
# popped; it lives in RAM only and self-invalidates on any FOREIGN mutation
# via a mutation_seq handshake (edits, reloads and pulls all bump the seq
# without calling _redo_mark, so no modifier hooks are needed).

_REDO_MAX_FRAMES = 100


def _journal_reset(ctx: dict) -> None:
    """(Re)load the sidecar journal + cursor := tip.

    Called where journal RAM state must be (re)derived: fresh context builds
    (restart, LRU rehydrate) and wholesale working-copy replacement (pull /
    stage / restore — ``store.reload`` cleared the log, so any staged journal
    steps died with it and the cursor must return to the tip; the redo stack
    dies via the seq handshake). The cached fast path deliberately does NOT
    reset: a live ctx's units+cursor+staged ``jrn:`` groups are coherent RAM
    state, and resetting mid-walk would let the next Ctrl+Z re-stage a unit
    that is already sitting in the tray.
    """
    try:
        if ctx.get("type") != "quam" or (ctx.get("origin") or "live") != "live":
            ctx["undo_units"], ctx["undo_cursor"] = [], 0
            return
        path = undo_journal.sidecar_path(current_app.instance_path, ctx["path"])
        units = undo_journal.load(path)
        ctx["undo_units"], ctx["undo_cursor"] = units, len(units)
    except Exception:
        logger.warning("undo journal load failed", exc_info=True)
        ctx["undo_units"], ctx["undo_cursor"] = [], 0


def _journal_prepare(store, ctx, meta: dict | None = None) -> list[dict]:
    """Capture phase 1, inside the caller's ``store._lock`` hold: serialize
    the OUTGOING change log as journal units. Committed only after the save
    SUCCEEDS (phase 2) — a failed save keeps the log, and appending at prepare
    time would duplicate the units on the retry. Advisory: never raises."""
    try:
        if not ctx or (ctx.get("origin") or "live") != "live":
            return []
        return undo_journal.units_from_log(list(store.change_log), meta=meta)
    except Exception:
        logger.warning("undo journal capture failed", exc_info=True)
        return []


def _journal_commit(ctx, units: list[dict]) -> None:
    """Capture phase 2, after a SUCCESSFUL save: append to the sidecar + RAM
    mirror; cursor := tip (the one rule that collapses every restart /
    eviction / replace edge). Advisory: never raises."""
    if not units or not ctx:
        return
    try:
        path = undo_journal.sidecar_path(current_app.instance_path, ctx["path"])
        ctx["undo_units"] = undo_journal.append_units(path, units)
        ctx["undo_cursor"] = len(ctx["undo_units"])
    except Exception:
        logger.warning("undo journal commit failed", exc_info=True)


def _redo_stack(ctx) -> list:
    return ctx.setdefault("redo_stack", []) if ctx is not None else []


def _redo_begin(ctx, store) -> None:
    """Fork detection, called BEFORE our own pop/push: if the store mutated
    since our machinery's last op (seq moved without :func:`_redo_mark`), the
    redo history belongs to a dead timeline — clear it, exactly like typing
    after undo in an editor."""
    if ctx is None or store is None:
        return
    frames = _redo_stack(ctx)
    if frames and ctx.get("redo_seq") != store.mutation_seq:
        frames.clear()


def _redo_mark(ctx, store) -> None:
    """Stamp the seq after one of OUR ops (undo/redo/discard/journal step) so
    the next :func:`_redo_begin` recognises the interval as ours."""
    if ctx is not None and store is not None:
        ctx["redo_seq"] = store.mutation_seq


def _redo_push_group(ctx, store, entries, *, mark: bool = True,
                     keep_jrn: bool = True) -> None:
    """Push one popped user action (entries newest-first, exactly as
    ``undo_group``/``undo_all`` return them) onto the redo stack. Only a
    ``jrn:`` gid is preserved (it routes /redo's cursor bookkeeping); ordinary
    gids are never reused on re-apply — /redo mints a fresh one.

    ``keep_jrn=False`` (the single-✕ /discard): a ✕ on one entry of a staged
    journal step never moved the cursor, so its redo frame must not either —
    the entry re-applies as an ordinary edit."""
    if ctx is None or not entries:
        return
    gid0 = entries[0].group_id
    frames = _redo_stack(ctx)
    frames.append({
        "gid": gid0 if (keep_jrn and isinstance(gid0, str)
                        and gid0.startswith(undo_journal.GID_PREFIX)) else None,
        "entries": [{
            "path": e.dot_path,
            "new": copy.deepcopy(e.new_value),
            "created": bool(e.created),
            "deleted": bool(e.deleted),
            "source_file": e.source_file,
        } for e in entries],
    })
    if len(frames) > _REDO_MAX_FRAMES:
        del frames[: len(frames) - _REDO_MAX_FRAMES]
    if mark:
        _redo_mark(ctx, store)


# Sentinel: a create-collision target whose current value couldn't be read. An
# unreadable target is treated as a conflict (kept live, reported) — never clobbered.
_REPLAY_UNREADABLE = object()


def _replay_updates(modifier, updates: dict) -> dict:
    """Best-effort re-apply of *updates* onto the (freshly synced) store.

    *updates* is the op-tagged map from :func:`_capture_change_log_as_updates`
    (``{path: ("set"|"create"|"delete", value)}``; a bare value is treated as
    ``("set", value)`` defensively). Dispatch:

    - ``set``    → resolve pointer aliases, ``set_value`` (unchanged semantics);
    - ``create`` → ``create_subtree``; if the path already exists on the pulled
      state (created out-of-band, e.g. qualibrate calibrated the same new pulse),
      a byte-equal value is a no-op success, but a DIFFERING value is recorded in
      ``failed`` and the live version is KEPT — never clobbered wholesale (that
      would silently discard a freshly-calibrated subtree the user never saw);
    - ``replace`` (a delete→re-create pair) → ``set_value(coerce=False)``
      at the literal path — never coercing, because the pulled live value's
      type may legitimately differ (string alias deleted, dict re-created);
      falls back to ``create_subtree`` when the live side deleted it too;
    - ``delete`` → ``delete_subtree``; an already-absent path counts as
      applied (the live side deleted it too — nothing to do).

    Unlike :meth:`Modifier.batch_set` (all-or-nothing), this is per-edit
    tolerant: a path that no longer exists or whose type changed after the
    pull is recorded in ``failed`` and skipped, while every still-valid edit
    is applied. Returns ``{"applied": int, "failed": [{dot_path, error}]}``.
    """
    applied = 0
    failed: list[dict] = []
    store = modifier.store
    with store._lock:
        for dot_path, tagged in updates.items():
            if isinstance(tagged, tuple) and len(tagged) == 2:
                op, value = tagged
            else:  # legacy plain-value form
                op, value = "set", tagged
            try:
                if op == "create":
                    try:
                        # enforce=False: previously-accepted values replayed
                        # verbatim — blocking mid-replay would be data loss.
                        modifier.create_subtree(dot_path, value, enforce=False)
                    except KeyError:
                        # The pulled live state ALREADY has this key — it was
                        # created out-of-band (e.g. qualibrate calibrated the same
                        # new pulse). Do NOT clobber the live subtree with the
                        # user's stale create: compare, and on a real difference
                        # keep the live version and report the conflict. Only a
                        # byte-equal value is a true no-op "applied".
                        target_path = _resolve_edit_path(store, dot_path)
                        try:
                            existing = store.get_value(target_path)
                        except (KeyError, TypeError, ValueError, IndexError):
                            existing = _REPLAY_UNREADABLE
                        if existing != value:
                            failed.append({
                                "dot_path": dot_path,
                                "error": "live already has this key (created "
                                         "out-of-band) — kept the live version",
                            })
                            continue  # skip applied += 1
                elif op == "replace":
                    try:
                        modifier.set_value(dot_path, value,
                                           _defer_hooks=True, coerce=False,
                                           enforce=False)
                    except KeyError:
                        # live deleted it too — re-create the session's value
                        modifier.create_subtree(dot_path, value, enforce=False)
                elif op == "delete":
                    try:
                        modifier.delete_subtree(dot_path)
                    except KeyError:
                        pass  # already gone on the pulled state — noop success
                else:
                    target_path = _resolve_edit_path(store, dot_path)
                    # coerce=False: `value` is entry.new_value — already type-coerced
                    # against the working-copy field the user actually edited. Re-coercing
                    # it against the PULLED live value's type loses the user's edit when
                    # the live side changed the field's TYPE: scalar→list/bool raises
                    # (the edit is dropped to `failed`, live keeps its value) and
                    # scalar→str silently stringifies it (e.g. 8e9 → "8000000000.0",
                    # reported as success). Replay the value the user accepted verbatim —
                    # consistent with the 'create'/'replace' branches above.
                    modifier.set_value(target_path, value, _defer_hooks=True,
                                       coerce=False, enforce=False)
                applied += 1
            except (KeyError, TypeError, ValueError, IndexError) as exc:
                failed.append({"dot_path": dot_path, "error": str(exc)})
        store._clear_pointer_cache()
        if store.search_index is not None:
            for entry in store.change_log:
                # create_subtree/delete_subtree maintain the index themselves
                # (and their paths may be whole subtrees, not leaves).
                if entry.created or entry.deleted:
                    continue
                store.search_index.update_entry(entry.dot_path, entry.new_value)
    return {"applied": applied, "failed": failed}


def _is_htmx() -> bool:
    """True for HTMX partial requests — but NOT for history restores.

    A history-restore GET (Back onto a page whose localStorage snapshot
    was evicted) carries BOTH ``HX-Request: true`` and
    ``HX-History-Restore-Request: true``, and htmx swaps the response into
    <body>. It needs the FULL page (htmx extracts the body itself) — the
    bare partial would destroy the sidebar/topbar chrome (docs/49 A7).
    """
    return (request.headers.get("HX-Request") == "true"
            and request.headers.get("HX-History-Restore-Request") != "true")


def _change_count() -> int:
    store = _store()
    return len(store.change_log) if store else 0


def _chip_channel_flags(store: QuamStore | None) -> tuple[bool, bool, bool]:
    """Chip-wide (has_resonator, has_flux, has_coupler) — raw structural scan
    (not engine.get_qubit()/get_pair(), which builds a full flattened dict per
    row just to check one key). Drives the sidebar's Resonators/Flux/Couplers
    nav items: shown only when at least one qubit/pair actually has that
    channel (a fixed-frequency chip has no Flux; a chip without couplers —
    CR-only or single-qubit — has no Couplers)."""
    if store is None:
        return False, False, False
    qubits = store.merged.get("qubits", {})
    pairs = store.merged.get("qubit_pairs", {})
    has_resonator = any(isinstance(q, dict) and isinstance(q.get("resonator"), dict)
                        for q in qubits.values())
    has_flux = any(isinstance(q, dict) and isinstance(q.get("z"), dict)
                  for q in qubits.values())
    has_coupler = any(isinstance(p, dict) and isinstance(p.get("coupler"), dict)
                      for p in pairs.values())
    return has_resonator, has_flux, has_coupler


# ── extras.data_folder chip↔data pairing (docs/20 v2 Step 5) ───────────────

def _adopt_extras_data_folders(ctx: dict | None) -> None:
    """Register the chip's declared data folder(s) as workspace roots.

    ``extras.data_folder`` travels with the state (like ``chip_name``), so a
    chip opened anywhere auto-pairs with its experiment data — the pairing
    ladder is extras > qualibrate project storage (/qualibrate/open) >
    recorded project roots. Values are RAW lab-side paths: each runs through
    the OS-dialect bridge (a Linux-written path on this Windows host and
    vice versa) and an existence gate; dangling values surface as a muted
    banner note, never an error. LIVE contexts only — archive opens must not
    grow the workspace. Idempotent (set-diffed against current roots).
    """
    if not ctx or ctx.get("type") != "quam":
        return
    ctx["extras_data_roots"] = []
    ctx["extras_data_dangling"] = []
    try:
        if (ctx.get("origin") or "live") != "live":
            return
        store = ctx.get("store")
        if store is None:
            return
        from quam_state_manager.core.history import extras_data_folder
        with store._lock:
            declared = extras_data_folder(store.state)
        if not declared:
            return
        resolved: list[str] = []
        for raw in declared:
            # PUBLIC native_path (r10): unlike the bare 2-arg _to_native it
            # anchors non-/mnt POSIX values onto the config's distro share
            # (wsl_root) — a /home/... value was previously never bridged.
            native = raw
            try:
                native = str(qualibrate_config.native_path(raw) or raw)
            except Exception:  # noqa: BLE001
                pass
            if native and Path(native).is_dir():
                resolved.append(str(Path(native)))
            else:
                ctx["extras_data_dangling"].append(raw)
        if not resolved:
            return
        ctx["extras_data_roots"] = resolved
        ws = _ws()
        existing = {str(p) for p in ws.root_folders}
        added = 0
        for root in resolved:
            if root in existing:
                continue
            try:
                ws.add_root(root)
                added += 1
            except (OSError, ValueError):
                continue
        if added:
            _save_workspace_roots()
            current_app.config.pop("dataset_store", None)
        # Project lens interplay: when this chip ALSO carries a qualibrate
        # scope, remember the declared roots for the Datasets/Trends
        # folder-selection seeding (same file /qualibrate/open records to).
        scope = ctx.get("qualibrate_project")
        if scope:
            try:
                _record_project_roots(scope, resolved)
            except Exception:  # noqa: BLE001
                pass
    except Exception:  # noqa: BLE001 — pairing must never break activation
        logger.debug("extras data-folder adoption failed", exc_info=True)


_DRIVE_RE = re.compile(r"^[A-Za-z]:[\\/]")


def _validate_data_folder(raw: str) -> dict:
    """Classify a candidate ``extras.data_folder`` value (docs/20 r10).

    Returns ``{"ok", "kind", "native"}`` with kind one of:
    - ``ok``           — bridges to a directory reachable from here;
    - ``cross_machine``— path-SHAPED (rooted) but not reachable — storeable
      after an explicit confirm (labs share state across OSes; the reader
      bridges at read time);
    - ``not_a_path``   — a bare name / relative fragment ("deviceC_lab3",
      "data/sub") — never storeable (this is the mistake class that produced
      the un-fixable dangling banner).
    """
    raw = (raw or "").strip()
    if not raw or len(raw) > 1024:
        return {"ok": False, "kind": "not_a_path", "native": None}
    rooted = (raw.startswith("/") or raw.startswith("\\")
              or bool(_DRIVE_RE.match(raw)))
    if not rooted:
        return {"ok": False, "kind": "not_a_path", "native": None}
    try:
        native = qualibrate_config.native_path(raw)
        if native and native.is_dir():
            return {"ok": True, "kind": "ok", "native": str(native)}
    except (OSError, ValueError):
        pass
    return {"ok": False, "kind": "cross_machine", "native": None}


def _data_folder_candidates(ctx: dict | None) -> list[dict]:
    """Known data-folder sources for THIS chip, best first, cap 3.

    ① the qualibrate project scope's resolved storage location (exists only)
    ② roots previously recorded for that project (project_dataset_roots.json)
    ③ the workspace's registered dataset roots.
    fs_key-deduped; anything already adopted from extras is excluded."""
    out: list[dict] = []
    seen: set[str] = set()

    def _key(p: str) -> str:
        try:
            return path_match.fs_key(p) or str(Path(p).resolve()).lower()
        except (OSError, ValueError):
            return str(p).lower()

    for r in (ctx or {}).get("extras_data_roots") or []:
        seen.add(_key(r))

    def _add(value: str, label: str, source: str) -> None:
        if len(out) >= 3 or not value:
            return
        k = _key(value)
        if k in seen:
            return
        seen.add(k)
        out.append({"value": value, "label": label, "source": source})

    scope = (ctx or {}).get("qualibrate_project")
    if scope:
        try:
            st = qualibrate_config.project_storage(scope)
            if st.get("exists") and st.get("native"):
                _add(st.get("raw") or st["native"],
                     f"{scope} project storage ({st['native']})", "project")
        except Exception:  # noqa: BLE001
            pass
        try:
            for r in _load_project_roots().get(scope, []):
                if Path(r).is_dir():
                    _add(r, r, "project_roots")
        except Exception:  # noqa: BLE001
            pass
    try:
        for entry in _active_dataset_stores(fast=True):
            _add(str(entry["path"]), str(entry.get("label") or entry["path"]),
                 "workspace")
    except Exception:  # noqa: BLE001
        pass
    return out


def _maybe_data_folder_suggest(ctx: dict | None) -> None:
    """Compute the banner memos: candidates (always, for datalists/actions)
    and the record-suggestion (only for a NAMED live chip with NO declared
    data folder + at least one candidate + no ::datafolder decline memo).
    Never breaks activation."""
    if not ctx or ctx.get("type") != "quam":
        return
    ctx["data_folder_candidates"] = []
    ctx["data_folder_suggest"] = None
    try:
        if (ctx.get("origin") or "live") != "live":
            return
        store = ctx.get("store")
        path = ctx.get("path")
        if store is None or not path:
            return
        try:
            if Path(path).resolve().is_relative_to(
                    Path(current_app.instance_path).resolve()):
                return   # SM-internal folders (working copies etc.)
        except (OSError, ValueError):
            pass
        from quam_state_manager.core.history import (
            extras_chip_name, extras_data_folder, fingerprint_from_dicts,
        )
        with store._lock:
            named = bool(extras_chip_name(store.state))
            declared = extras_data_folder(store.state)
            fp = fingerprint_from_dicts(store.state, store.wiring)
        # audit-r10 perf gate: the common fully-configured chip (named +
        # declared + reachable) needs NO candidates — skip the dataset-store
        # sweep + TOML parse on its every activation. Candidates are needed
        # only when a banner/datalist will actually consume them: unnamed
        # (name prompt), dangling (fix form), or named-without-declared
        # (the record suggestion).
        needs_candidates = (not named or bool(ctx.get("extras_data_dangling"))
                            or not declared)
        if not needs_candidates:
            return
        ctx["data_folder_candidates"] = _data_folder_candidates(ctx)
        if not named or declared or not ctx["data_folder_candidates"]:
            return
        token = _chip_prompt_token(str(path), fp)
        if f"{token}::datafolder" in _load_chip_prompt_memo():
            return
        ctx["data_folder_suggest"] = {
            "candidate": ctx["data_folder_candidates"][0],
            "token": token,
        }
    except Exception:  # noqa: BLE001
        logger.debug("data-folder suggest failed", exc_info=True)


# ── First-open chip-name prompt (docs/20 v2 — identity ladder tier 1) ──────

_chip_prompt_memo_lock = threading.Lock()   # audit-r10: R-M-W writers race


def _chip_prompt_memo_file() -> Path:
    return Path(current_app.instance_path) / "chip_name_prompts.json"


def _load_chip_prompt_memo() -> dict:
    # ONE attempt, and only when the file is there. This is read from _ctx()
    # — i.e. on EVERY page render — and until a user has declined a prompt the
    # file does not exist. safe_io.read_json's 4-attempt/0.9 s retry ladder is
    # right for a live state.json being replaced under us and pure latency
    # here: measured 906 ms of time.sleep on every single render of a fresh
    # instance.
    path = _chip_prompt_memo_file()
    try:
        if not path.exists():
            return {}
        data = safe_io.read_json(path, attempts=1)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _chip_prompt_token(path: str, fp: Any) -> str:
    """Fingerprint-keyed (folder moves never re-nag); fs-key fallback."""
    from quam_state_manager.core.history import fingerprint_token
    tok = fingerprint_token(fp)
    if tok:
        return tok
    try:
        return "fs:" + path_match.fs_key(path)
    except Exception:  # noqa: BLE001
        return "path:" + str(path)


def _maybe_identity_confirm(ctx: dict | None) -> None:
    """Gate for the CONSERVATIVE identity-confirm banner (docs/20 r12).

    An UNNAMED live chip whose history remembers a declared identity (the
    deviceC incident: a lab-side state regeneration wiped extras while the
    snapshots kept it) gets "This chip appears to be 'X' — is this
    correct?" INSTEAD of the blank name prompt. Same-architecture chips can
    be many and even share a data folder, so this only ever ASKS — Yes
    stages through the normal validated /chip-name/set path; No memoizes
    (``token::identity``) and falls back to the fill-in prompt. The
    remembered data folder is offered only when it passes the validator
    (a remembered not-a-path mistake is never re-suggested)."""
    if not ctx or ctx.get("type") != "quam":
        return
    ctx["identity_confirm"] = None
    try:
        if (ctx.get("origin") or "live") != "live":
            return
        path = str(ctx.get("path") or "")
        store = ctx.get("store")
        if not path or store is None:
            return
        try:
            Path(path).resolve().relative_to(
                Path(current_app.instance_path).resolve())
            return
        except (ValueError, OSError):
            pass
        from quam_state_manager.core.history import (
            extras_chip_name, fingerprint_from_dicts)
        with store._lock:
            if extras_chip_name(store.state):
                return                      # named — nothing to confirm
            fp = fingerprint_from_dicts(store.state, store.wiring)
        token = _chip_prompt_token(path, fp)
        if f"{token}::identity" in _load_chip_prompt_memo():
            return
        hm = current_app.config.get("history_manager")
        if hm is None:
            return
        remembered = hm.remembered_identity(path)
        if not remembered or not remembered.get("name"):
            return
        df = remembered.get("data_folder")
        offer_df = None
        if df and _validate_data_folder(str(df))["kind"] != "not_a_path":
            offer_df = str(df)
        ts = str(remembered.get("snapshot_ts") or "")
        when = (f"{ts[0:4]}-{ts[4:6]}-{ts[6:8]} {ts[9:11]}:{ts[11:13]}"
                if len(ts) >= 13 else ts)
        ctx["identity_confirm"] = {
            "name": remembered["name"],
            "data_folder": offer_df,
            "when": when,
            "token": token,
        }
    except Exception:  # noqa: BLE001 — a banner must never break activation
        logger.debug("identity-confirm gate failed", exc_info=True)


def _maybe_chip_name_prompt(ctx: dict | None) -> None:
    """Gate + memo for the first-open "Name this chip?" banner.

    Re-evaluated on EVERY activation (the origin flag refreshes per
    activation): live quam contexts only — never dataset archives, never
    chips under the instance dir (autofit sim), gone as soon as the WORKING
    copy carries a name (staging dismisses it before Apply), and never
    after an explicit decline (fingerprint-keyed memo).
    """
    if not ctx or ctx.get("type") != "quam":
        return
    ctx["chip_name_prompt"] = None
    try:
        if (ctx.get("origin") or "live") != "live":
            return
        path = str(ctx.get("path") or "")
        store = ctx.get("store")
        if not path or store is None:
            return
        try:
            Path(path).resolve().relative_to(
                Path(current_app.instance_path).resolve())
            return                      # instance-internal chip (sim etc.)
        except (ValueError, OSError):
            pass
        from quam_state_manager.core.history import (
            extras_chip_name, fingerprint_from_dicts)
        with store._lock:
            name = extras_chip_name(store.state)
            fp = fingerprint_from_dicts(store.state, store.wiring)
        if name:
            return
        token = _chip_prompt_token(path, fp)
        if token in _load_chip_prompt_memo():
            return
        suggest = (ctx.get("qualibrate_project")
                   or _chip_display_name(path) or "").strip()
        ctx["chip_name_prompt"] = {"suggest": suggest, "token": token}
    except Exception:  # noqa: BLE001 — a banner must never break activation
        logger.debug("chip-name prompt gate failed", exc_info=True)


@bp.route("/chip-name/set", methods=["POST"])
def chip_name_set():
    """Stage ``extras.chip_name`` (+ optional ``extras.data_folder``) through
    the audited edit machinery — working copy only, never a direct live
    write. Apply-to-live then propagates it into every future run's
    quam_state copy, which is what makes attribution layout-proof."""
    ctx = _active_ctx()
    modifier = ctx.get("modifier") if ctx else None
    if not ctx or ctx.get("type") != "quam" or modifier is None:
        return render_template("_status.html", message="No state loaded",
                               level="warning"), 400
    if (ctx.get("origin") or "live") != "live":
        return render_template(
            "_status.html", level="warning",
            message="Read-only archive — open the live chip to name it"), 409
    name = (request.form.get("name") or "").strip()[:64].strip()
    if not name:
        return render_template("_status.html", message="Chip name required",
                               level="error"), 400
    data_folder = (request.form.get("data_folder") or "").strip()
    cross_note = ""
    if data_folder:
        # r10: "deviceC_lab3"-class values (a NAME typed into the path field)
        # used to be stored verbatim and only failed later as an un-fixable
        # dangling banner. Reject them at the door; path-shaped values that
        # aren't reachable HERE stay storeable (labs share state across OSes).
        v = _validate_data_folder(data_folder)
        if v["kind"] == "not_a_path":
            return render_template(
                "_status.html", level="error",
                message=(f"'{data_folder}' looks like a name, not a folder — "
                         "the data folder must be an absolute path (e.g. "
                         "D:\\data\\myproject). Pick a suggestion or leave it "
                         "empty for now.")), 400
        if v["kind"] == "cross_machine":
            cross_note = (" — note: that folder is not reachable from this "
                          "machine; it will pair where it is visible")
    store = modifier.store
    try:
        with store._lock:
            extras = store.state.get("extras")
        sets: dict[str, Any] = {"chip_name": name}
        if data_folder:
            sets["data_folder"] = data_folder
        if not isinstance(extras, dict):
            modifier.create_subtree("extras", sets)
        else:
            for leaf, value in sets.items():
                if leaf in extras:
                    modifier.set_value(f"extras.{leaf}", value)
                else:
                    modifier.create_subtree(f"extras.{leaf}", value)
        _invalidate_engine_cache(ctx)
    except (KeyError, TypeError, ValueError) as e:
        return render_template("_status.html", message=str(e), level="error"), 400
    ctx["chip_name_prompt"] = None
    ctx["identity_confirm"] = None   # the Yes button routes through here (r12)
    _adopt_extras_data_folders(ctx)
    _maybe_data_folder_suggest(ctx)
    body = render_template(
        "_status.html", level="success",
        message=f"Chip name '{name}' staged — Save + Apply to live makes it"
                f" permanent (future runs then carry it automatically){cross_note}")
    resp = make_response(body + _tray_oob())
    resp.headers["HX-Trigger"] = "pulses-changed"
    return resp


@bp.route("/chip-name/decline", methods=["POST"])
def chip_name_decline():
    """Remember "don't ask again" for this chip (fingerprint-keyed)."""
    ctx = _active_ctx()
    token = (request.form.get("token") or "").strip()
    if token:
        with _chip_prompt_memo_lock:
            data = _load_chip_prompt_memo()
            data[token] = {
                "declined_at": datetime.now().isoformat(timespec="seconds"),
                "path_hint": str((ctx or {}).get("path") or ""),
            }
            try:
                safe_io.atomic_write_json(_chip_prompt_memo_file(), data)
            except OSError:
                logger.warning("Could not persist chip-name decline",
                               exc_info=True)
    if ctx:
        ctx["chip_name_prompt"] = None
    return ""


def _type_alarm_memo(ctx: dict) -> dict:
    """The per-store anomaly scan, memoized on ``mutation_seq``.

    ONE whole-state walk per content change, shared by the banner, the
    self-appearing alert and the diagnostics types card. Holds both classes:
    ``paths`` (stored-as-text — SM can repair these) and ``env`` (the chip
    disagrees with the selected environment's schema — the user decides).
    """
    store = ctx["store"]
    seq = getattr(store, "mutation_seq", None)
    memo = ctx.get("_type_alarm_memo")
    if isinstance(memo, dict) and memo.get("seq") == seq:
        return memo
    from quam_state_manager.core import diagnostics as _diag
    from quam_state_manager.core import type_fix as _tf
    with store._lock:
        state = store.state
    paths = _diag.numeric_string_leaves(state)
    env_findings: list = []
    try:
        from quam_state_manager.core import state_env_validate as _sev
        # The WARM manifest already attached to the store (cached_only at
        # activation) — the request path never spawns a probe. Cold env → [].
        policy = getattr(store, "type_policy", None)
        manifest = policy.manifest if policy is not None else None
        if manifest:
            env_findings = list(
                _sev.analysis_for_store(store, manifest).get("findings") or [])
    except Exception:  # noqa: BLE001 — a cold/broken env must not break the scan
        logger.debug("env-schema scan for the type alarm failed", exc_info=True)
    memo = {"seq": seq, "paths": paths, "sig": _tf.strnum_signature(paths),
            "env_findings": env_findings,
            "env_sig": _tf.env_signature(env_findings)}
    ctx["_type_alarm_memo"] = memo
    return memo


def _alarm_plan(ctx: dict, memo: dict) -> dict | None:
    """The repair plan for the memoized anomaly set, cached alongside it.

    ``_type_alarm_payload`` runs in every ``_ctx()``, i.e. on every render, so
    the plan must be built once per content change — not once per page.
    """
    if not memo["paths"]:
        return None
    if "plan" not in memo:
        from quam_state_manager.core import type_fix as _tf
        memo["plan"] = _tf.build_plan(ctx["store"], paths=memo["paths"])
    return memo["plan"]


def _type_alarm_payload(ctx: dict | None) -> dict | None:
    """The ACTIVE type-anomaly payload (both classes), else None.

    Numeric-looking string leaves ("0.13") usually mean an external state
    regeneration string-ified values wholesale; env-schema mismatches usually
    mean the environment's quam moved under the chip. SM used to surface the
    first as a passive Explorer mark (r14) and the second only as a row in the
    diagnostics list. Both are delta-gated against the per-chip dismissed
    signatures — dismissing silences THOSE sets, and any new anomaly re-raises.

    ``editable`` is False on an archive: a read-only context is told what is
    wrong but never offered the repair.
    """
    if not ctx:
        return None
    store = ctx.get("store")
    if store is None:
        return None
    try:
        from quam_state_manager.core import type_fix as _tf
        memo = _type_alarm_memo(ctx)
        if not memo["paths"] and not memo["env_findings"]:
            return None
        token = _active_chip_token() or ("path:" + str(ctx.get("path") or ""))
        dismissed = _load_chip_prompt_memo().get(f"{token}::typealarm") or {}
        # Each class is gated on its OWN signature: dismissing the text
        # anomalies must never also silence an env mismatch (and a legacy
        # record, which carries no env_sig, reads as "env not yet dismissed").
        strnum_live = bool(memo["paths"]) and dismissed.get("sig") != memo["sig"]
        env_live = (bool(memo["env_findings"])
                    and (dismissed.get("env_sig") or "") != memo["env_sig"])
        if not strnum_live and not env_live:
            return None
        editable = (ctx.get("origin") or "live") == "live"
        summary = _tf.alert_summary(_alarm_plan(ctx, memo) if editable else None,
                                    memo["env_findings"], memo["paths"])
        summary.update({
            "token": token,
            "editable": editable,
            "sig": memo["sig"],
            "env_sig": memo["env_sig"],
            "strnum_live": strnum_live,
            "env_live": env_live,
            # legacy top-level keys — the r14 banner reads these
            "count": len(memo["paths"]),
            "first": memo["paths"][0] if memo["paths"] else "",
            "paths": memo["paths"][:6],
        })
        return summary
    except Exception:  # noqa: BLE001 — an alarm bug must never break rendering
        logger.debug("type-alarm computation failed", exc_info=True)
        return None


_ALARM_REASONS = {
    "chip-open": "this chip was opened",
    "live-pull": "the live state was pulled in",
    "snapshot-stage": "a snapshot was staged into the working copy",
    "restore-live": "a snapshot was restored to the live chip",
    "run-state-load": "a run's state was loaded",
    "node-finished": "SM picked up an external write to the live chip",
}


def _publish_instance_chip(ctx: dict | None) -> None:
    """Tell the instance registry which chip this window now holds (docs/80).

    Called from the same two activation branches the type alarm arms from —
    every path that opens or switches a chip converges there, so the registry
    cannot drift out of date without the chip itself having changed.

    Registry writes are best-effort inside ``core.instances``; the extra guard
    here is for the identity lookup, which touches the store.
    """
    if not ctx or ctx.get("type") != "quam":
        return
    try:
        identity = _active_chip_identity() or {}
    except Exception:       # noqa: BLE001 — a name lookup must not fail a load
        identity = {}
    try:
        from quam_state_manager.core import instances
        instances.update(
            current_app.instance_path,
            chip_path=str(ctx.get("path") or ""),
            chip_name=str(identity.get("name") or ""),
            project=str(ctx.get("qualibrate_project") or ""),
        )
    except Exception:       # noqa: BLE001
        logger.debug("instance registry: chip publish failed", exc_info=True)


def _arm_type_alarm(ctx: dict | None, reason: str) -> None:
    """Mark that NEW CONTENT entered this chip's working copy.

    The popup is never fired from here — this only makes the next render
    ELIGIBLE to raise it once (``GET /type-alert`` consumes the flag). That
    separation is what keeps ordinary editing, rendering and polling silent:
    they never arm, so they can never prompt.
    """
    if not ctx or ctx.get("type") != "quam":
        return
    if (ctx.get("origin") or "live") != "live":
        return          # archive: reported on Diagnostics, never prompted
    ctx["type_alarm_armed"] = {
        "reason": reason if reason in _ALARM_REASONS else "chip-open",
        "at": datetime.now().isoformat(timespec="seconds"),
    }


@bp.route("/type-alert")
def type_alert():
    """The self-appearing type-anomaly popup — ONE-SHOT per content-entry event.

    204 unless new content just entered the working copy AND the anomaly set is
    one the user has not already answered. Reads in-memory state only (never a
    live file), so it costs a dict lookup once the mutation_seq memo is warm.
    """
    ctx = _active_ctx()
    if not ctx or ctx.get("type") != "quam" or ctx.get("store") is None:
        return "", 204
    if (ctx.get("origin") or "live") != "live":
        return "", 204
    armed = ctx.get("type_alarm_armed")
    if not armed:
        return "", 204                     # an ordinary edit/render never arms
    payload = _type_alarm_payload(ctx)
    if not payload:
        ctx.pop("type_alarm_armed", None)
        return "", 204
    seen = ctx.get("_type_alarm_shown")
    shown_key = (payload["sig"], payload["env_sig"])
    if seen == shown_key:
        ctx.pop("type_alarm_armed", None)  # same set, same session — no re-nag
        return "", 204
    ctx.pop("type_alarm_armed", None)
    ctx["_type_alarm_shown"] = shown_key
    from quam_state_manager.core import type_fix as _tf
    memo = _type_alarm_memo(ctx)
    plan = _alarm_plan(ctx, memo) or _tf.build_plan(ctx["store"], paths=[])
    payload = dict(payload)
    payload["reason"] = armed.get("reason") or "chip-open"
    payload["reason_label"] = _ALARM_REASONS.get(payload["reason"], "")
    return render_template("_type_fix_plan.html", plan=plan, alert=payload)


def _instance_peers_for_active() -> list:
    """Live sibling windows holding the SAME chip as this one (docs/80).

    Returns the peers themselves so the template can name each by port. Empty
    whenever nothing is loaded, nothing else is running, or the other windows
    hold different chips — which is the normal multi-project case and gets no
    banner at all.
    """
    ctx = _active_ctx()
    if not ctx or ctx.get("type") != "quam":
        return []
    try:
        from quam_state_manager.core import instances
        return instances.conflicts(current_app.instance_path,
                                   chip_path=ctx.get("path"))["same_chip"]
    except Exception:       # noqa: BLE001 — a warning must not break a render
        logger.debug("instance conflict probe failed", exc_info=True)
        return []


@bp.route("/instances/banner")
def instances_banner():
    """Re-render the sibling-window slot.

    Refreshed from events the app already fires — no new poller — so the strip
    disappears on its own once the other window closes and something on this
    one moves.
    """
    return render_template("_multi_instance_banner.html",
                           instance_peers=_instance_peers_for_active(),
                           active_name=(_active_chip_identity() or {}).get("name"))


@bp.route("/type-alarm/banner")
def type_alarm_banner():
    """Re-render the alarm slot so its count can never outlive the anomaly set
    it describes (a repair used to leave the old number on screen until the
    next full page load)."""
    return render_template("_type_alarm_banner.html",
                           type_alarm=_type_alarm_payload(_active_ctx()))


@bp.route("/type-alarm/dismiss", methods=["POST"])
def type_alarm_dismiss():
    """Memo the CURRENT anomaly signatures as answered (per chip token) — the
    banner and the popup stay gone for these exact sets and re-raise on any new
    anomaly. The two classes are memoed independently."""
    sig = (request.form.get("sig") or "").strip()
    env_sig = (request.form.get("env_sig") or "").strip()
    token = (request.form.get("token") or "").strip()
    if token and (sig or env_sig):
        with _chip_prompt_memo_lock:
            data = _load_chip_prompt_memo()
            prev = data.get(f"{token}::typealarm") or {}
            data[f"{token}::typealarm"] = {
                "sig": sig or prev.get("sig") or "",
                "env_sig": env_sig or prev.get("env_sig") or "",
                "dismissed_at": datetime.now().isoformat(timespec="seconds"),
            }
            try:
                safe_io.atomic_write_json(_chip_prompt_memo_file(), data)
            except OSError:
                logger.warning("Could not persist type-alarm dismissal",
                               exc_info=True)
    return ""


@bp.route("/type-fix/plan")
def type_fix_plan():
    """Preview the one-click repair for numbers stored as TEXT (docs/77).

    Answers "what would SM change?" before anything is written: one row per
    convertible leaf with what is stored now, what it becomes and the
    resulting type, plus the leaves SM refuses to guess at, each with a
    reason. Read-only — the user still has to press the button.
    """
    ctx = _active_ctx()
    store = (ctx or {}).get("store")
    if store is None:
        return render_template("_status.html", message="No state loaded",
                               level="warning")
    blocked = _archive_write_blocked(ctx)
    if blocked is not None:
        return blocked      # a read-only archive is told, never offered a repair
    from quam_state_manager.core import type_fix as _tf
    plan = _tf.build_plan(store)
    return render_template("_type_fix_plan.html", plan=plan)


@bp.route("/type-fix/apply", methods=["POST"])
def type_fix_apply():
    """Convert the selected stored-as-text values, in ONE change group.

    Safety: the plan is re-derived here and its signature compared with the
    one the preview was rendered from — a fix computed against a different
    chip state is refused rather than applied to the wrong leaves (the same
    doctrine as the diagnostics one-click fix). Writes go to the WORKING COPY
    only; the user reviews them in the tray and Saves/Applies as usual.
    """
    ctx = _active_ctx()
    store = (ctx or {}).get("store")
    modifier = (ctx or {}).get("modifier")
    if store is None or modifier is None:
        return jsonify(ok=False, error="No state loaded"), 400
    if (ctx.get("origin") or "live") != "live":
        # A dataset run archive is a frozen record: staging edits into its
        # working copy would only fail later at apply, after the user thought
        # the repair landed.
        return jsonify(
            ok=False, error_kind="archive_read_only",
            error=("This chip was opened from a dataset run archive "
                   "(read-only). Load it from its live folder to repair.")), 409

    payload = request.get_json(silent=True) or {}
    want = [str(p) for p in (payload.get("paths") or [])]
    sig = (payload.get("sig") or "").strip()
    if not want:
        return jsonify(ok=False, error="Nothing selected."), 400

    from quam_state_manager.core import type_fix as _tf
    from quam_state_manager.core import type_policy as _tp

    plan = _tf.build_plan(store)
    if sig and sig != plan["sig"]:
        return jsonify(
            ok=False, error_kind="stale_plan",
            error=("The chip changed since this list was built — reopen the "
                   "fix dialog so you confirm what is actually there now."),
        ), 409

    by_path = {r["path"]: r for r in plan["rows"]}
    rows = [by_path[p] for p in want if p in by_path]
    unknown = [p for p in want if p not in by_path]
    if not rows:
        return jsonify(ok=False, error="None of the selected fields are "
                                       "convertible any more.", unknown=unknown), 409

    # 1. Persist the user type assignment FIRST for every row that needs one:
    #    without an enforced expectation the modifier's legacy coercion would
    #    faithfully cast the number back to the old value's string type.
    inst = current_app.instance_path
    assigned: list[str] = []
    failures: list[dict] = []
    for r in rows:
        if not r["needs_assignment"]:
            continue
        try:
            _tp.save_assignment(inst, ctx["path"], r["path"], {
                "type": r["proposed_type"],
                "override_env": False,
                "note": "auto-corrected from text (docs/77)",
            })
            assigned.append(r["path"])
        except (ValueError, OSError) as exc:
            failures.append({"path": r["path"], "error": str(exc)})
    if assigned:
        _attach_type_policy(ctx, inst)

    # 2. One change GROUP so the whole repair is a single Ctrl+Z.
    gid = modifier.new_group_id() if len(rows) > 1 else None
    applied: list = []
    converted: list[dict] = []
    for r in rows:
        if any(f["path"] == r["path"] for f in failures):
            continue
        try:
            entry = modifier.set_value(r["path"], r["proposed_value"],
                                       group_id=gid, _defer_hooks=True)
            applied.append(entry)
            converted.append({"path": r["path"],
                              "value": r["proposed_value"],
                              "type": r["proposed_type"]})
        except Exception as exc:  # noqa: BLE001 — report, never half-report
            modifier._rollback(applied)
            return jsonify(
                ok=False, error=f"{r['path']}: {exc}",
                error_kind="write_failed",
                note="No fields were changed — the whole repair was rolled back.",
            ), 400
    if applied:
        # Hooks once for the whole group (mirrors modifier.batch_set and the
        # /field/edit-batch epilogue, so per-entry hooks aren't duplicated).
        modifier.store._clear_pointer_cache()
        if modifier.store.search_index is not None:
            for entry in applied:
                modifier.store.search_index.update_entry(entry.dot_path,
                                                         entry.new_value)
        _set_working_dirty(True, ctx)
        _invalidate_engine_cache(ctx)

    return jsonify(ok=True, converted=converted, count=len(converted),
                   failures=failures, unknown=unknown,
                   tray_html=_tray_html())


def _write_datafolder_memo(token: str, ctx: dict | None) -> None:
    with _chip_prompt_memo_lock:
        data = _load_chip_prompt_memo()
        data[f"{token}::datafolder"] = {
            "declined_at": datetime.now().isoformat(timespec="seconds"),
            "path_hint": str((ctx or {}).get("path") or ""),
        }
        try:
            safe_io.atomic_write_json(_chip_prompt_memo_file(), data)
        except OSError:
            logger.warning("Could not persist data-folder decline",
                           exc_info=True)


@bp.route("/chip-data-folder/set", methods=["POST"])
def chip_data_folder_set():
    """Set / clear ``extras.data_folder`` on the open chip (docs/20 r10).

    Always a STAGE through the audited edit machinery (working copy only —
    Apply-to-live publishes). Form: ``use`` (a suggestion button's value,
    wins over the text input), ``value``, ``clear=1``, ``force_cross=1``
    (accept a path-shaped value that isn't reachable from this machine —
    never overrides the not-a-path rejection: one ack never collapses two
    gates)."""
    ctx = _active_ctx()
    modifier = ctx.get("modifier") if ctx else None
    if not ctx or ctx.get("type") != "quam" or modifier is None:
        return render_template("_status.html", message="No state loaded",
                               level="warning"), 400
    if (ctx.get("origin") or "live") != "live":
        return render_template(
            "_status.html", level="warning",
            message="Read-only archive — open the live chip to edit it"), 409
    store = modifier.store
    value = (request.form.get("use") or request.form.get("value") or "").strip()
    clear = request.form.get("clear") == "1"

    if clear:
        try:
            with store._lock:
                extras = store.state.get("extras")
            had = isinstance(extras, dict) and "data_folder" in extras
            if had:
                modifier.delete_subtree("extras.data_folder")
                _invalidate_engine_cache(ctx)
        except (KeyError, TypeError, ValueError) as e:
            return render_template("_status.html", message=str(e),
                                   level="error"), 400
        # An explicit clear is an answer — don't immediately re-suggest.
        try:
            from quam_state_manager.core.history import fingerprint_from_dicts
            with store._lock:
                fp = fingerprint_from_dicts(store.state, store.wiring)
            _write_datafolder_memo(_chip_prompt_token(str(ctx.get("path") or ""), fp), ctx)
        except Exception:  # noqa: BLE001
            pass
        ctx["data_folder_suggest"] = None
        _adopt_extras_data_folders(ctx)
        _maybe_data_folder_suggest(ctx)
        body = render_template(
            "_status.html", level="success",
            message=("Data folder cleared from the working state — Save + "
                     "Apply to live makes it permanent"
                     if had else "No data folder was declared — nothing to clear"))
        resp = make_response(body + _tray_oob())
        resp.headers["HX-Trigger"] = "pulses-changed"
        return resp

    if not value:
        # Guards the folder picker's unconditional auto-submit: an empty
        # submit must never turn into a silent clear.
        return render_template("_status.html", level="error",
                               message="Pick a folder (or use Clear)"), 400

    v = _validate_data_folder(value)
    if v["kind"] == "not_a_path":
        return render_template(
            "_status.html", level="error",
            message=(f"'{value}' looks like a name, not a folder — enter an "
                     "absolute path (e.g. D:\\data\\myproject), pick a "
                     "suggestion, or Browse.")), 400
    if v["kind"] == "cross_machine" and request.form.get("force_cross") != "1":
        return render_template("_data_folder_confirm.html", value=value), 409

    try:
        with store._lock:
            extras = store.state.get("extras")
        if not isinstance(extras, dict):
            modifier.create_subtree("extras", {"data_folder": value})
        elif "data_folder" in extras:
            # coerce=False: a list-valued data_folder would TypeError under
            # list→str coercion; replacement is wholesale and typed-correct.
            modifier.set_value("extras.data_folder", value, coerce=False)
        else:
            modifier.create_subtree("extras.data_folder", value)
        _invalidate_engine_cache(ctx)
    except (KeyError, TypeError, ValueError) as e:
        return render_template("_status.html", message=str(e), level="error"), 400

    ctx["data_folder_suggest"] = None
    _adopt_extras_data_folders(ctx)   # reachable value pairs Datasets NOW
    _maybe_data_folder_suggest(ctx)
    adopted = value not in (ctx.get("extras_data_dangling") or [])
    body = render_template(
        "_status.html", level="success",
        message=("Data folder staged — Save + Apply to live makes it permanent"
                 + (" (and it is now a registered dataset root)"
                    if adopted and v["ok"] else "")))
    resp = make_response(body + _tray_oob())
    resp.headers["HX-Trigger"] = "pulses-changed"
    return resp


@bp.route("/chip-identity/decline", methods=["POST"])
def chip_identity_decline():
    """"No — different chip" on the identity-confirm banner (docs/20 r12):
    memoize ``token::identity`` so the conservative question never re-nags,
    then re-render the strip — which now falls through to the plain fill-in
    name prompt (the user types/browses the right values themselves)."""
    ctx = _active_ctx()
    token = (request.form.get("token") or "").strip()
    if token:
        with _chip_prompt_memo_lock:
            data = _load_chip_prompt_memo()
            data[f"{token}::identity"] = {
                "declined_at": datetime.now().isoformat(timespec="seconds"),
                "path_hint": str((ctx or {}).get("path") or ""),
            }
            try:
                safe_io.atomic_write_json(_chip_prompt_memo_file(), data)
            except OSError:
                logger.warning("Could not persist identity decline",
                               exc_info=True)
    if ctx:
        ctx["identity_confirm"] = None
        _maybe_chip_name_prompt(ctx)   # the fill-in prompt takes over NOW
    return render_template("_chip_name_banner.html", **_ctx())


@bp.route("/chip-data-folder/decline", methods=["POST"])
def chip_data_folder_decline():
    """"Not now" for the record-suggestion banner (::datafolder memo)."""
    ctx = _active_ctx()
    token = (request.form.get("token") or "").strip()
    if token:
        _write_datafolder_memo(token, ctx)
    if ctx:
        ctx["data_folder_suggest"] = None
    return ""


@bp.route("/chip-name/banner", methods=["GET"])
def chip_name_banner():
    """Re-render the chip banner strip (the confirm fragment's Cancel and a
    generic freshness refresh)."""
    ctx = _active_ctx()
    if ctx:
        _maybe_data_folder_suggest(ctx)
    return render_template("_chip_name_banner.html", **_ctx())


def _ctx(**extra: Any) -> dict[str, Any]:
    """Base template context shared by all pages."""
    # Self-heal a missed live change (throttled ground-truth hash) so the
    # "view in SM?" banner reappears on the next render even when the cheap mtime
    # check was fooled by an external editor / coarse-mtime rewrite.
    _refresh_live_diverged(_active_ctx())
    store = _store()
    path = _active_path()
    ident = _active_chip_identity()
    has_resonator, has_flux, has_coupler = _chip_channel_flags(store)
    return {
        "active_path": path,
        # The chip-level name (shared across per-experiment loads), not the
        # raw parent-folder name. None when no quam chip is active.
        "active_name": ident["name"] if ident else None,
        "chip_identity": ident,          # full identity for _chip_header.html
        "chip_origin": ident["origin"] if ident else "live",
        # Render-time chip fingerprint token (topology-only: network + qubit/pair
        # labels, NOT values — so value edits never change it). Baked into the page
        # as window.__chipToken and sent back as expect_chip on every edit POST, so
        # an edit committed from a stale tab after another tab switched the active
        # chip is caught server-side by _chip_mismatch_response (409) instead of
        # silently landing on the wrong chip. "" when no chip is loaded → no gate.
        "chip_token": _active_chip_token() or "",
        "context_type": _context_type(),
        "change_count": _change_count(),
        "working_dirty": _working_dirty(),
        # docs/110 #10-A: the FULL-page tray must carry the same freshness
        # beacon the OOB tray does — otherwise a page render stamps "" while
        # the next mutation's OOB swap stamps a real seq, and PaneState reads
        # that as "the chip moved" and refetches every parked pane. (Caught by
        # the real-browser pass: keep-alive never fired, only the SOFT tier.)
        "mutation_seq": (getattr(_store(), "mutation_seq", "") if _store() else ""),
        # docs/114 (#16): the read-only lock, like the beacon above, must be
        # stamped by the FULL-page render too — _render_tray alone meant the
        # lock only ever appeared after an OOB tray swap.
        "live_readonly": bool((_active_ctx() or {}).get("live_readonly_hint")),
        "qualibrate_tray": _qualibrate_tray_badge(),
        # docs/63 project lens: the qualibrate project this context operates
        # under (explicitly opened, or reverse-matched from its live folder).
        # None ⇒ every consumer renders exactly the pre-lens behavior.
        "project_scope": (_active_ctx() or {}).get("qualibrate_project"),
        "live_diverged": bool(_ctx_obj("live_diverged")),
        "live_drift_count": (_active_ctx() or {}).get("live_drift_count"),
        # docs/117: base.html includes the tray directly, so a FULL page render
        # gets its context from here, not from _render_tray. Both must stamp
        # these or the pill vanishes on every navigation (the same trap
        # mutation_seq hit in docs/110 — real-browser caught both).
        "auto_apply": _auto_apply_state(),
        "auto_apply_armable": _auto_apply_armable(),
        # The FULL session for the pill. `auto_apply` stays push-only because
        # the flusher's data-auto-apply beacon reads it; a pull-only session
        # must not make the client start writing to live.
        "auto_sync": _auto_sync_state(),
        # docs/120 item 8: pull is gated on its OWN question, so the pill can
        # still be offered on a chip where push is refused (a diverged chip is
        # exactly where pull is wanted).
        "auto_pull_armable": _auto_pull_armable(),
        "applied_log": _applied_log_rows(),
        # docs/20 v2: first-open "name this chip?" banner payload (None when
        # named / declined / archive / no chip) + declared-but-unreachable
        # extras.data_folder values (muted note, never an error).
        "chip_name_prompt": (_active_ctx() or {}).get("chip_name_prompt"),
        "identity_confirm": (_active_ctx() or {}).get("identity_confirm"),
        "extras_data_dangling": (_active_ctx() or {}).get("extras_data_dangling") or [],
        "data_folder_suggest": (_active_ctx() or {}).get("data_folder_suggest"),
        "data_folder_candidates": (_active_ctx() or {}).get("data_folder_candidates") or [],
        "dataset_roots_ask": _dataset_roots_ask(_active_ctx()),
        "last_apply": (_active_ctx() or {}).get("last_apply"),
        # r14 ⑨: ACTIVE stored-as-text alarm — numeric-looking string leaves
        # (external regen wholesale-string-ifies values; SM used to detect this
        # only as a passive Explorer mark). None when clean or dismissed-as-is.
        "type_alarm": _type_alarm_payload(_active_ctx()),
        "wc_gc_count": _working_copy_count(),
        "wc_gc_threshold": _WC_GC_THRESHOLD,
        "qubit_names": store.qubit_names if store else [],
        "pair_names": store.qubit_pair_names if store else [],
        "has_resonator": has_resonator,
        "has_flux": has_flux,
        "has_coupler": has_coupler,
        "workspace": _ws(),
        **extra,
    }


_QUBIT_KNOWN_OPS = {
    "xy": {"x180_DragCosine", "x90_DragCosine", "saturation"},
    "resonator": {"readout"},
    "z": set(),
}


def _build_qubit_sections(name: str, qubit_data: dict[str, Any], store: QuamStore) -> list[dict]:
    """Build pointer-aware, sectioned property list for qubit detail template."""
    sections: list[dict] = []
    current_section_name: str | None = None

    for section_name, key, dot_tmpl in _QUBIT_PROPERTY_MAP:
        if section_name != current_section_name:
            sections.append({"name": section_name, "props": []})
            current_section_name = section_name

        resolved_value = qubit_data.get(key)
        dot_path = dot_tmpl.format(name=name) if dot_tmpl else None

        raw_value = resolved_value
        ptr = False
        self_ref = False
        present = dot_path is None       # dot-path-less rows are always "present"
        if dot_path:
            try:
                raw_value = store.get_value(dot_path)
                present = True
                ptr = is_pointer(raw_value)
                self_ref = is_self_ref(raw_value) if ptr else False
            except (KeyError, TypeError):
                pass

        # docs/114 (#15): a pointer row must say what it RESOLVES to, and
        # "dangling" must mean a resolution FAILURE — not the accident that
        # this builder passed the raw pointer through as the value (which is
        # why the old tooltip read "Resolves to: <the pointer itself>", and
        # why deciding on equality painted EVERY pointer on a real chip red).
        dangling = False
        if ptr and not self_ref and dot_path:
            try:
                _res = store.resolve_pointer(raw_value, tuple(dot_path.split(".")))
                if _res == raw_value:
                    dangling = True      # the resolver handed it straight back
                else:
                    resolved_value = _res
            except Exception:            # noqa: BLE001 — display must not break
                dangling = True

        editable = dot_path is not None and key != "id"
        if isinstance(resolved_value, (list, dict)):
            editable = False

        sections[-1]["props"].append({
            "key": key,
            "value": resolved_value,
            "raw": raw_value,
            "dot_path": dot_path,
            "is_pointer": ptr and not self_ref,
            "is_self_ref": self_ref,
            "dangling": dangling,
            "editable": editable,
            "_present": present,
        })

    # Drop static sections that are STRUCTURALLY absent — every prop None AND
    # its path missing from the state (the fixed-frequency CR chip's all-empty
    # "Flux" section). A present-but-null section (a fresh flux chip's
    # uncalibrated Flux fields) survives and stays fillable. Parity with the
    # pair inspector's all-None drop, refined by path presence.
    sections = [
        s for s in sections
        if any(p["value"] is not None or p["_present"] for p in s["props"])
    ]
    for s in sections:
        for p in s["props"]:
            p.pop("_present", None)

    # Surface any operations not covered by the static map — newly-added pulses
    # render automatically, one section per (channel, op_name).
    qubit_obj = store.merged.get("qubits", {}).get(name, {}) or {}
    for channel in _PULSE_CHANNELS:
        ch_obj = qubit_obj.get(channel) or {}
        ops = ch_obj.get("operations")
        if not isinstance(ops, dict):
            continue
        known = _QUBIT_KNOWN_OPS.get(channel, set())
        for op_name, op_body in ops.items():
            if op_name in known or not isinstance(op_body, dict):
                continue
            section = {"name": f"{channel.upper()} · {op_name}", "props": []}
            for field_name, field_value in op_body.items():
                if field_name == "__class__":
                    continue
                op_dot = f"qubits.{name}.{channel}.operations.{op_name}.{field_name}"
                raw = field_value
                ptr = False
                self_ref = False
                try:
                    raw = store.get_value(op_dot)
                    ptr = is_pointer(raw)
                    self_ref = is_self_ref(raw) if ptr else False
                except (KeyError, TypeError):
                    pass
                editable = not isinstance(field_value, (list, dict))
                section["props"].append({
                    "key": field_name,
                    "value": field_value,
                    "raw": raw,
                    "dot_path": op_dot,
                    "is_pointer": ptr and not self_ref,
                    "is_self_ref": self_ref,
                    "editable": editable,
                })
            if section["props"]:
                sections.append(section)

    return sections


def _humanize_gate_name(gate_name: str) -> str:
    """Turn ``cz_flattop`` / ``cz_unipolar`` / ``cz_v3`` into a human label.

    Falls back to title-casing the underscore-separated tokens for unknown names.
    """
    known = {"cz_flattop": "CZ Flattop", "cz_unipolar": "CZ Unipolar"}
    if gate_name in known:
        return known[gate_name]
    return " ".join(tok.upper() if tok == "cz" else tok.capitalize() for tok in gate_name.split("_"))


def _pair_prop(store: QuamStore, key: str, dot_path: str | None,
               resolved_value: Any = None, *, editable: bool | None = None) -> dict:
    """One inspector property row (the shared shape all section builders emit)."""
    raw_value = resolved_value
    ptr = False
    self_ref = False
    if dot_path:
        try:
            raw_value = store.get_value(dot_path)
            ptr = is_pointer(raw_value)
            self_ref = is_self_ref(raw_value) if ptr else False
            if resolved_value is None and not ptr:
                resolved_value = raw_value
            elif resolved_value is None and ptr and not self_ref:
                resolved_value = store.resolve_pointer(
                    raw_value, tuple(dot_path.split(".")))
        except (KeyError, TypeError):
            pass
    if editable is None:
        editable = dot_path is not None and not isinstance(resolved_value, (list, dict))
    return {
        "key": key,
        "value": resolved_value,
        "raw": raw_value,
        "dot_path": dot_path,
        "is_pointer": ptr and not self_ref,
        "is_self_ref": self_ref,
        # docs/114 (#15): dangling == the resolver handed the pointer back
        "dangling": bool(ptr and not self_ref and resolved_value == raw_value),
        "editable": editable,
    }


def _build_cr_zz_sections(name: str, store: QuamStore) -> list[dict]:
    """Dynamic Cross Resonance / ZZ Drive / CR Gate inspector sections.

    Built from the pair's ACTUAL keys via ``cr_semantics`` because the
    calibration levers live in flavor-dependent homes (channel vs macro,
    ``zz_drive`` vs ``zz``) — a static property map cannot hold their dot
    paths, and emitting absent keys would recreate the phantom-section bug
    (editable rows whose Apply 400s). Frequency-chain rows: raw pointers are
    display-only (value-mode editing ``target_qubit_RF_frequency`` would write
    into the TARGET qubit's calibration from a pair page — the cross-entity
    surprise the 3-mode Explorer editor exists to make explicit); the computed
    effective LO/RF/IF rows have no dot path at all.
    """
    pair_obj = store.merged.get("qubit_pairs", {}).get(name)
    if not isinstance(pair_obj, dict):
        return []
    sections: list[dict] = []
    levers = cr_semantics.lever_map(pair_obj)

    def _channel_section(title: str, chan_key: str, kind: str) -> None:
        props: list[dict] = []
        base = f"qubit_pairs.{name}.{chan_key}"
        eff = cr_semantics.effective_frequencies(store, name, channel=kind)
        # calibration levers (editable, flavor-correct paths)
        for lever, suffix in sorted(levers.items()):
            is_zz = lever.startswith("zz_")
            if (kind == "zz") != is_zz or lever.startswith("macro_"):
                continue
            label = lever[3:] if is_zz else lever
            props.append(_pair_prop(store, label, f"qubit_pairs.{name}.{suffix}"))
        # frequency chain: raw fields display-only, then the emulated values
        chan = store.merged["qubit_pairs"][name].get(chan_key)
        if isinstance(chan, dict):
            for fkey in ("LO_frequency", "target_qubit_RF_frequency",
                         "target_qubit_LO_frequency", "target_qubit_IF_frequency",
                         "intermediate_frequency"):
                if fkey in chan:
                    props.append(_pair_prop(store, fkey, f"{base}.{fkey}",
                                            editable=False))
            ops = chan.get("operations")
            if isinstance(ops, dict) and ops:
                props.append(_pair_prop(store, "operations", None,
                                        ", ".join(ops.keys()), editable=False))
        if eff is not None:
            props.append(_pair_prop(store, "effective LO", None, eff.lo_hz,
                                    editable=False))
            props.append(_pair_prop(store, "effective target RF", None,
                                    eff.target_rf_hz, editable=False))
            props.append(_pair_prop(store, "effective IF", None, eff.if_hz,
                                    editable=False))
            for problem in eff.problems:
                props.append(_pair_prop(store, "⚠", None, problem,
                                        editable=False))
        if props:
            sections.append({"name": title, "props": props})

    cr = cr_semantics.cr_channel(pair_obj)
    if cr is not None:
        cr_key = next((k for k in cr_semantics.CR_CHANNEL_KEYS
                       if pair_obj.get(k) is cr), "cross_resonance")
        _channel_section("Cross Resonance", cr_key, "cr")
    zz = cr_semantics.zz_channel(pair_obj)
    if zz is not None:
        _channel_section("ZZ Drive", zz[0], "zz")
    xy_det = cr_semantics.xy_detuned_channel(pair_obj)
    if xy_det is not None:
        base = f"qubit_pairs.{name}.{cr_semantics.XY_DETUNED_KEY}"
        props = [_pair_prop(store, k, f"{base}.{k}", editable=False)
                 for k in ("detuning", "intermediate_frequency", "RF_frequency")
                 if k in xy_det]
        if props:
            sections.append({"name": "XY Detuned", "props": props})

    # CR gate macro: correction phases editable, runtime/id display-only,
    # fidelity through the canonical macro-then-channel ladder.
    hit = cr_semantics.cr_gate_macro(pair_obj)
    if hit is not None:
        gate_name, gate = hit
        gbase = f"qubit_pairs.{name}.macros.{gate_name}"
        props = []
        for k in ("qc_correction_phase", "qt_correction_phase",
                  "drive_amplitude_scaling", "drive_phase",
                  "cancel_amplitude_scaling", "cancel_phase"):
            if k in gate:
                props.append(_pair_prop(store, k, f"{gbase}.{k}"))
        for k in ("duration", "id"):
            if k in gate:
                props.append(_pair_prop(store, k, f"{gbase}.{k}", editable=False))
        fid = cr_semantics.fidelity(pair_obj)
        if fid is not None:
            props.append(_pair_prop(
                store, f"fidelity ({fid['source']})", None, fid["value"],
                editable=False))
        if props:
            sections.append({"name": f"{_humanize_gate_name(gate_name)} Gate",
                             "props": props})
    return sections


def _build_pair_sections(name: str, pair_data: dict[str, Any], store: QuamStore) -> list[dict]:
    """Build pointer-aware, sectioned property list for pair detail template."""
    sections: list[dict] = []
    current_section_name: str | None = None

    # Static properties from _PAIR_PROPERTY_MAP
    for section_name, key, dot_tmpl in _PAIR_PROPERTY_MAP:
        if section_name != current_section_name:
            sections.append({"name": section_name, "props": []})
            current_section_name = section_name

        resolved_value = pair_data.get(key)
        dot_path = dot_tmpl.format(name=name) if dot_tmpl else None

        raw_value = resolved_value
        ptr = False
        self_ref = False
        if dot_path:
            try:
                raw_value = store.get_value(dot_path)
                ptr = is_pointer(raw_value)
                self_ref = is_self_ref(raw_value) if ptr else False
            except (KeyError, TypeError):
                pass

        # docs/114 (#15): a pointer row must say what it RESOLVES to, and
        # "dangling" must mean a resolution FAILURE — not the accident that
        # this builder passed the raw pointer through as the value (which is
        # why the old tooltip read "Resolves to: <the pointer itself>", and
        # why deciding on equality painted EVERY pointer on a real chip red).
        dangling = False
        if ptr and not self_ref and dot_path:
            try:
                _res = store.resolve_pointer(raw_value, tuple(dot_path.split(".")))
                if _res == raw_value:
                    dangling = True      # the resolver handed it straight back
                else:
                    resolved_value = _res
            except Exception:            # noqa: BLE001 — display must not break
                dangling = True

        editable = dot_path is not None and key != "id"
        if isinstance(resolved_value, (list, dict)):
            editable = False

        sections[-1]["props"].append({
            "key": key,
            "value": resolved_value,
            "raw": raw_value,
            "dot_path": dot_path,
            "is_pointer": ptr and not self_ref,
            "is_self_ref": self_ref,
            "dangling": dangling,
            "editable": editable,
        })

    # Drop static sections whose every property is absent (None) — so a CR pair
    # doesn't show an empty "Coupler" section and a CZ pair doesn't show an empty
    # "Cross Resonance" section. Identity always has an id, so it survives.
    sections = [s for s in sections if any(p["value"] is not None for p in s["props"])]

    # Dynamic CR / ZZ / gate-macro sections (flavor-aware; empty on CZ chips).
    sections.extend(_build_cr_zz_sections(name, store))

    # Dynamic CZ gate sections
    _CZ_GATE_FIELDS = [
        ("amplitude", "macros.{gate}.flux_pulse_qubit.amplitude"),
        ("length", "macros.{gate}.flux_pulse_qubit.length"),
        ("flat_length", "macros.{gate}.flux_pulse_qubit.flat_length"),
        ("smoothing_length", "macros.{gate}.flux_pulse_qubit.smoothing_length"),
        ("coupler_amplitude", "macros.{gate}.coupler_flux_pulse.amplitude"),
        ("phase_shift_control", "macros.{gate}.phase_shift_control"),
        ("phase_shift_target", "macros.{gate}.phase_shift_target"),
        ("bell_fidelity", "macros.{gate}.fidelity.Bell_State.Fidelity"),
        ("standard_rb", "macros.{gate}.fidelity.StandardRB"),
        ("interleaved_rb", "macros.{gate}.fidelity.InterleavedRB"),
    ]

    pair_obj = store.merged.get("qubit_pairs", {}).get(name, {})
    macros = pair_obj.get("macros") or {}
    if not isinstance(macros, dict):
        macros = {}
    for gate_name in macros.keys():
        prefix = gate_name
        # Count only ACTUAL CZ fields present — not any key starting with the
        # gate name. A CR gate macro named e.g. "cr" must not match the
        # cross_resonance channel's cr_lo_frequency/cr_upconverter keys (handled
        # by the Cross Resonance static section) and produce a phantom section.
        gate_keys = [f"{prefix}_{sfx}" for sfx, _ in _CZ_GATE_FIELDS
                     if f"{prefix}_{sfx}" in pair_data]
        if not gate_keys:
            continue

        section_label = _humanize_gate_name(gate_name)
        section = {"name": section_label, "props": []}
        for field_suffix, dot_suffix in _CZ_GATE_FIELDS:
            key = f"{prefix}_{field_suffix}"
            if key not in pair_data:
                continue
            resolved_value = pair_data[key]
            dot_path = f"qubit_pairs.{name}.{dot_suffix.format(gate=gate_name)}"

            raw_value = resolved_value
            ptr = False
            self_ref = False
            try:
                raw_value = store.get_value(dot_path)
                ptr = is_pointer(raw_value)
                self_ref = is_self_ref(raw_value) if ptr else False
            except (KeyError, TypeError):
                pass

            # docs/114 (#15): a pointer row must say what it RESOLVES to, and
            # "dangling" must mean a resolution FAILURE — not the accident that
            # this builder passed the raw pointer through as the value (which
            # is why the old tooltip read "Resolves to: <the pointer itself>",
            # and why deciding on equality painted EVERY pointer red).
            dangling = False
            if ptr and not self_ref and dot_path:
                try:
                    _res = store.resolve_pointer(raw_value,
                                                 tuple(dot_path.split(".")))
                    if _res == raw_value:
                        dangling = True   # the resolver handed it straight back
                    else:
                        resolved_value = _res
                except Exception:         # noqa: BLE001 — display must not break
                    dangling = True

            editable = True
            if isinstance(resolved_value, (list, dict)):
                editable = False

            section["props"].append({
                "key": key,
                "value": resolved_value,
                "raw": raw_value,
                "dot_path": dot_path,
                "is_pointer": ptr and not self_ref,
                "is_self_ref": self_ref,
                "dangling": dangling,
                "editable": editable,
            })

        if section["props"]:
            sections.append(section)

    return sections


# ======================================================================
# Home / Load
# ======================================================================


@bp.route("/help")
def help_page():
    """docs/115 (#14): the manual has a PERMANENT address.

    The working-copy mental model (live chip / working state / snapshot)
    and the feature tour lived only on the landing — one swap away and
    gone the moment a user navigated, exactly when they were forming that
    model. ``/help`` renders the SAME shared fragment
    (``_landing_getting_started.html`` — one source, no drift) plus the
    shortcut reference, reachable from the sidebar on every page.
    """
    template = "_help.html" if _is_htmx() else "help.html"
    return render_template(template, **_ctx(page="help"))


@bp.route("/")
def home():
    """Project-first landing (docs/63): with a qualibrate config the home
    page is the Projects landing (shell renders instantly; the cards are a
    lazy fragment so GET / never pays the TOML/doctor I/O — it is the
    workbench iframe's entry and the most-hit route). Without a config the
    pre-lens Welcome renders verbatim."""
    config_exists = bool(qualibrate_config.tray_status().get("config_exists"))
    session = _load_session()
    # Session values are hand-editable JSON — a type-corrupt entry (int,
    # nested list, …) must degrade to "no history", never TypeError the
    # most-hit route out of Path().
    resume_path = session.get("last_quam_state_path")
    if not isinstance(resume_path, str):
        resume_path = None
    if resume_path and not (Path(resume_path) / "state.json").exists():
        resume_path = None      # startup hygiene may not have run yet
    recents = []
    raw_recents = session.get("recent_quam_state_paths")
    for p in (raw_recents if isinstance(raw_recents, list) else [])[:5]:
        if (isinstance(p, str) and p != resume_path
                and (Path(p) / "state.json").exists()):
            recents.append({"path": p, "name": _chip_display_name(Path(p))})
    return render_template("base.html", **_ctx(
        page="home",
        landing_config_exists=config_exists,
        # for the no-config welcome's locate block (docs/63 §B): WHERE we
        # looked and WHY (env/override/default) — cheap, no I/O.
        qualibrate_source=qualibrate_config.config_source(),
        last_project=session.get("last_project"),
        resume_path=resume_path,
        resume_name=_chip_display_name(Path(resume_path)) if resume_path else "",
        recent_paths=recents[:3],
        # True first run (no session history at all) → the landing's
        # "Getting started" manual starts expanded; any history collapses it.
        first_run=not (session.get("last_project") or resume_path or recents),
    ))


@bp.route("/landing/projects")
def landing_projects():
    """The landing's lazy project-cards fragment (docs/63) — the only place
    the landing pays the listing + doctor cost."""
    return render_template(
        "_landing_projects.html",
        listing=_qualibrate_listing(),
        last_project=_load_session().get("last_project"),
    )


@bp.route("/workbench")
def workbench():
    """Co-display shell: Qualibrate (left iframe) beside the State Manager (right iframe).

    The decoupled cross-app bridge. Embeds Qualibrate's served page AS-IS (zero
    Qualibrate code touched) next to SM's own UI so both are visible at once —
    killing the window-hunt between the two apps. The Qualibrate URL is
    user-configurable (Qualibrate moves ports, e.g. 8001/8002) and persisted
    client-side. A standalone full-bleed page (not the sidebar shell); the right
    pane iframes SM's own ``/`` so the full app sits beside Qualibrate.
    """
    default_qb = (
        request.args.get("qb")
        or os.environ.get("QUALIBRATE_URL")
        or "http://127.0.0.1:8001"
    )
    return render_template("workbench.html", qb_url_default=default_qb)


@bp.route("/workbench/watch")
def workbench_watch():
    """Poll target for the workbench: newest mtime of Qualibrate's live state.

    Returns the resolved live-state dir + the max ``*.json`` mtime so the
    /workbench page can detect that Qualibrate applied a fit (which writes the
    live ``state.json`` / ``wiring.json``) and nudge the SM pane. Path is
    resolved per active project — see ``core/qualibrate_config``. Stat-only;
    never reads or blocks the live files.
    """
    from quam_state_manager.core import qualibrate_config
    return jsonify(qualibrate_config.live_state_status())


# Single-slot cache for the workbench-match verdict. The /workbench poll fires
# every 3 s and, in the persistent different-folder states, path_match.verdict
# falls through to fingerprint_of(qb)+fingerprint_of(sm) = 4 full state/wiring
# JSON parses of the LIVE folders (~180 ms each on 9p) — a sustained content
# stat-storm that also breaks the "background detection is os.stat-only" invariant.
# Cache keyed on (paths, reason, file mtimes): recompute only when an mtime moves.
# Lock-free: dict get/set is GIL-atomic and a rare double-compute is harmless.
_workbench_match_cache: dict[str, tuple] = {}


def _workbench_match_key(qb, sm, qb_reason) -> tuple:
    def _mt(folder, name):
        if not folder:
            return None
        try:
            # (mtime_ns, size), not float st_mtime — a same-tick content swap on a
            # coarse/9p/FAT clock would otherwise serve a stale verdict (the exact
            # weakness safe_io._pair_fingerprint hardens against). Advisory UI, but
            # keep the two invalidation keys consistent.
            st = (Path(folder) / name).stat()
            return (st.st_mtime_ns, st.st_size)
        except OSError:
            return None
    return (str(qb), str(sm), qb_reason,
            _mt(qb, "state.json"), _mt(qb, "wiring.json"),
            _mt(sm, "state.json"), _mt(sm, "wiring.json"))


@bp.route("/workbench/match")
def workbench_match():
    """Does the quam_state Qualibrate is writing == the one SM has open?

    Drives the /workbench path indicator and GATES the nudge: if SM has a
    DIFFERENT chip loaded than Qualibrate is writing, a "fit" would silently
    no-op (the watch fires on Qualibrate's path; sync pulls SM's unchanged
    path). Single-OS same-namespace compare + chip fingerprint for the
    "same chip, different folder" case. See core/path_match.

    Returns ``{state, reason, qb_path, sm_path, qb_name, sm_name, loadable,
    load_path}``. ``load_path`` is what the frontend POSTs to /load to switch
    SM onto the chip Qualibrate is using.
    """
    from quam_state_manager.core import path_match, qualibrate_config

    qb = qualibrate_config.resolve_live_state_path()
    qb_reason = None if qb else qualibrate_config.live_state_status().get("reason")
    sm = _active_path()

    # Serve the cached verdict unless a folder path or a state/wiring mtime moved
    # (cheap os.stat) — avoids re-reading the live JSON every 3 s poll.
    _key = _workbench_match_key(qb, sm, qb_reason)
    _hit = _workbench_match_cache.get("v")
    if _hit is not None and _hit[0] == _key:
        v = _hit[1]
    else:
        v = path_match.verdict(qb, sm, qb_reason=qb_reason)
        _workbench_match_cache["v"] = (_key, v)
    qb_dir = Path(qb) if qb else None
    loadable = bool(qb_dir and qb_dir.is_dir() and (qb_dir / "state.json").exists())
    return jsonify({
        "state": v["state"],
        "reason": v.get("reason"),
        "qb_path": str(qb) if qb else None,
        "sm_path": sm,
        "qb_name": path_match.chip_label(qb_dir) if qb_dir else None,
        "sm_name": path_match.chip_label(sm) if sm else None,
        "loadable": loadable,
        "load_path": str(qb) if qb else None,
        # which qualibrate project the resolved path belongs to (docs/55) —
        # lets the workbench say "Qualibrate is on project X" instead of a
        # bare path when the verdict is a mismatch.
        "qb_project": qualibrate_config.active_project(),
    })


# ======================================================================
# QUAlibrate Projects (docs/55) — READ-ONLY over ~/.qualibrate.
# The No-Conflict doctrine: SM never writes qualibrate's configs in this
# tier; every read tolerates a torn (mid-write) file.
# ======================================================================


def _qualibrate_listing() -> dict:
    """list_projects + doctor + the [SM]-loaded marker, one READ-ONLY pass."""
    from quam_state_manager.core import qualibrate_config

    listing = qualibrate_config.list_projects()
    listing["doctor"] = qualibrate_config.lint(listing)
    # Compare against the LIVE folder (ctx["live_path"]) — _ctx_path() is the
    # private working copy under instance/, which never matches a config path.
    ctx = _active_ctx()
    loaded = (ctx or {}).get("live_path")
    for p in listing["projects"]:
        native = p["state_path"]["native"]
        # samefile-grounded (path_match.same_folder) — resolve()-equality
        # false-negatives on case-variant spellings of ONE folder on
        # case-insensitive hosts (POSIX resolve doesn't case-canonicalize).
        # Either side may be None.
        p["loaded_in_sm"] = bool(
            loaded and native and path_match.same_folder(native, loaded))
    return listing


def _project_for_path(folder) -> str | None:
    """The qualibrate project whose EFFECTIVE state_path IS *folder* (docs/63).

    Reverse-matches against the stat-cached
    :func:`qualibrate_config.project_state_paths` index — steady-state cost
    is a handful of ``os.stat`` calls, so it is safe on the chip-activation
    path. Ambiguity rule (several projects can share one state_path via
    inheritance): the qualibrate-ACTIVE matching project wins; else a UNIQUE
    match wins; else None (never guess — an explicit Open pins the choice).
    Never raises; never breaks a load."""
    try:
        idx = qualibrate_config.project_state_paths()
        matches = [name for name, native in idx.get("projects", [])
                   if native and path_match.same_folder(native, folder)]
        if not matches:
            return None
        active = idx.get("active")
        if active in matches:
            return active
        if len(matches) == 1:
            return matches[0]
        return None
    except Exception:  # noqa: BLE001 — the lens must never break a load
        logger.debug("project reverse-match failed", exc_info=True)
        return None


def _scope_for(path, ctx: dict | None = None) -> str | None:
    """Project scope for an arbitrary state folder (snapshot stamping etc.).

    The owning context's memoized scope is the session truth the UI headers
    show — an explicit /qualibrate/open pin included, which the pure reverse
    match can never recover when several projects share one state_path (it
    refuses to guess → None, and the stamp would contradict the header).
    So prefer the memo when the snapshot source IS that context's folder;
    fall back to the reverse match (no ctx in hand — background paths — or
    a ctx that predates scope acquisition)."""
    if ctx:
        memo = ctx.get("qualibrate_project")
        live = ctx.get("live_path") or ctx.get("path") or ""
        try:
            if memo and live and path and path_match.same_folder(live, path):
                return memo
        except Exception:  # noqa: BLE001 — the lens must never break a save
            logger.debug("scope memo match failed", exc_info=True)
    return _project_for_path(path)


def _acquire_project_scope(ctx: dict | None, *, explicit: str | None = None) -> None:
    """Attach the project scope to *ctx* (docs/63).

    ``explicit`` (from POST /qualibrate/open) always overwrites — the user's
    choice is pinned and survives cache-hit re-activations. Otherwise the
    scope is DERIVED from the context's live folder, but only when the ctx
    doesn't already carry a name: a previously pinned/derived name sticks for
    the context's lifetime (re-derivation happens naturally on rebuild after
    LRU eviction or restart — the reverse index is the source of truth, the
    ctx field is a memo)."""
    if not ctx:
        return
    if explicit is not None:
        ctx["qualibrate_project"] = explicit
        # Unconditional (it self-dedups): a RE-open of an already-scoped
        # project must still refresh the landing highlight — gating on the
        # ctx memo left last_project pointing at whatever was derived since.
        _remember_last_project(explicit)
        return
    if ctx.get("qualibrate_project"):
        return
    name = _project_for_path(ctx.get("live_path") or ctx.get("path") or "")
    # Re-check before writing: the derive above does stat/TOML work, and an
    # explicit pin may have landed on this ctx meanwhile — a user's pin
    # always outranks a concurrent derive.
    if name and not ctx.get("qualibrate_project"):
        ctx["qualibrate_project"] = name
        _remember_last_project(name)


def _remember_last_project(name: str) -> None:
    """Persist the landing-page highlight (instance/last_session.json,
    ``last_project``) — NEVER auto-restored (docs/63 decision 3); a
    standalone folder load deliberately does not clear it."""
    try:
        data = _load_session()
        if data.get("last_project") == name:
            return
        data["last_project"] = name
        _save_session(data)
    except Exception:  # noqa: BLE001 — best-effort persistence
        logger.debug("last_project persist failed", exc_info=True)


def _qualibrate_tray_badge() -> dict | None:
    """State for the topbar '⚗ <project>' badge; ``None`` hides it.

    Cheap enough for every render: qualibrate_config.tray_status is
    stat-cached (two os.stat steady-state). ``match`` is True/False vs the
    chip SM has open, or None when either side is unknown. ``sm_scope`` is
    SM's OWN project scope (docs/63) — shown as the badge name when present;
    a mere scope≠active difference is NEVER a warn/danger color (``match``
    keeps meaning "SM's chip == qualibrate's active write target").

    Hidden entirely when there is no config. Otherwise it renders for an
    active project, for SM's own scope, or — docs/120 item 1 — for a chip that
    belongs to no project at all (``standalone``), which SM used to say
    nothing whatsoever about."""
    from quam_state_manager.core import qualibrate_config

    try:
        st = qualibrate_config.tray_status()
    except Exception:  # the badge must never break a page render
        return None
    ctx = _active_ctx()
    sm_scope = (ctx or {}).get("qualibrate_project")
    if not st.get("config_exists"):
        return None
    match = None
    live = (ctx or {}).get("live_path")
    if live and st.get("state_native") and st.get("state_exists"):
        # samefile-grounded — resolve()-equality false-ambered on case-variant
        # spellings of one folder on case-insensitive (macOS/Windows) hosts.
        match = path_match.same_folder(live, st["state_native"])
    # docs\120 item 1: an open chip that belongs to NO project is standalone,
    # and SM used to say nothing whatsoever about that — this function returned
    # None, so the slot rendered empty and "you are editing a bare folder" was
    # indistinguishable from "the badge doesn't apply yet". Editing was never
    # blocked; only the fact was missing. Gated on a config existing, because
    # without one "project" is not a concept this user has, so the contrast
    # the word draws would be meaningless. An archive is excluded: it is not a
    # folder you are working in, and the status badge already names it.
    #
    # ``match is not True`` keeps the tray from contradicting itself. sm_scope
    # is a per-ctx memo derived at ACTIVATION, so pointing qualibrate at the
    # already-open chip afterwards leaves it None — and the tray would then say
    # "same chip as loaded in SM" on the alembic badge while the chip beside it
    # said "not part of any QUAlibrate project". Whatever the memo says, a chip
    # qualibrate is demonstrably writing to is not standalone.
    standalone = bool(
        ctx
        and ctx.get("type") == "quam"
        and (ctx.get("origin") or "live") == "live"
        and not sm_scope
        and match is not True
    )
    if not (st.get("active") or sm_scope or standalone):
        return None
    return {"project": st["active"],
            # dangling only ever describes the ACTIVE project's state_path —
            # a scope-only badge (no active project) must not read as broken.
            "dangling": bool(st.get("active")) and not st["state_exists"],
            "match": match, "sm_scope": sm_scope,
            "standalone": standalone,
            # The folder itself, so the chip can name what is being edited
            # rather than just asserting a category.
            "standalone_path": (ctx or {}).get("live_path") if standalone else None}


@bp.route("/api/qualibrate/projects")
def api_qualibrate_projects():
    """The Projects sidebar/page payload (projects + doctor findings)."""
    return jsonify(_qualibrate_listing())


@bp.route("/qualibrate/subnav")
def qualibrate_subnav():
    """The sidebar's lazy-loaded project submenu (hx-trigger=load) — keeps
    the base-page render free of the 16 TOML reads. Passes the ctx's project
    scope explicitly (this fragment renders outside _ctx()) so a scope whose
    project vanished from qualibrate gets an honest hint row."""
    listing = _qualibrate_listing()
    scope = (_active_ctx() or {}).get("qualibrate_project")
    # r8 feedback: the template caps the visible rows, so order the list
    # relevance-first — the qualibrate-active project and the loaded SM scope
    # must sit in the always-visible head, the rest stays alphabetical.
    listing["projects"] = sorted(
        listing["projects"],
        key=lambda p: (not p["active"], p["name"] != scope,
                       p["name"].lower()))
    return render_template("_qualibrate_subnav.html",
                           listing=listing, project_scope=scope)


@bp.route("/qualibrate")
def qualibrate_page():
    """The Project Config Manager: projects table + effective/raw TOML views
    + doctor panel. 100% read-only (docs/55 No-Conflict doctrine)."""
    from quam_state_manager.core import qualibrate_config

    listing = _qualibrate_listing()
    # raw TOML texts for the detail panes (viewer only — deliberately NO
    # free-text editor: qualibrate round-trips these files through
    # tomllib/tomli_w, so hand-edits/comments are destroyed on its next
    # write, and a typo hard-errors every qualibrate process)
    cfg_dir = Path(listing["config_dir"])
    raws: dict[str, str] = {}
    try:
        raws["__root__"] = (cfg_dir / "config.toml").read_text(
            encoding="utf-8", errors="replace")
    except OSError:
        raws["__root__"] = "(unreadable)"
    for p in listing["projects"]:
        try:
            raws[p["name"]] = (cfg_dir / "projects" / p["name"]
                               / "config.toml").read_text(
                encoding="utf-8", errors="replace") or "(empty overlay — inherits everything)"
        except OSError:
            raws[p["name"]] = "(unreadable)"

    template = "_qualibrate.html" if _is_htmx() else "qualibrate.html"
    return render_template(template, **_ctx(
        page="qualibrate",
        listing=listing,
        raw_tomls=raws,
        qualibrate_source=qualibrate_config.config_source(),
        supported_versions={
            "qualibrate": qualibrate_config.SUPPORTED_QUALIBRATE_VERSION,
            "quam": qualibrate_config.SUPPORTED_QUAM_VERSION,
        },
    ))


@bp.route("/qualibrate/project-config/<name>")
def qualibrate_project_config(name: str):
    """Per-project config popup (r8 feedback): paths + port settings straight
    from the landing card. Effective merge + both raw TOMLs. READ-ONLY."""
    from quam_state_manager.core import qualibrate_config

    listing = _qualibrate_listing()
    proj = next((p for p in listing["projects"] if p["name"] == name), None)
    if proj is None:
        return render_template("_status.html",
                               message=f"Unknown qualibrate project: {name!r}",
                               level="error"), 404
    cfg_dir = Path(listing["config_dir"])
    eff = qualibrate_config.effective_config(name, cfg_dir=cfg_dir)
    raws: dict[str, str] = {}
    try:
        raws["overlay"] = (cfg_dir / "projects" / name / "config.toml").read_text(
            encoding="utf-8", errors="replace") \
            or "(empty overlay — inherits everything)"
    except OSError:
        raws["overlay"] = "(unreadable)"
    try:
        raws["root"] = (cfg_dir / "config.toml").read_text(
            encoding="utf-8", errors="replace")
    except OSError:
        raws["root"] = "(unreadable)"
    return render_template(
        "_qualibrate_project_config.html", p=proj,
        eff_json=json.dumps(eff, indent=2, ensure_ascii=False),
        raws=raws, cfg_dir=str(cfg_dir))


# ---- config-location picker (docs/63 §B) -----------------------------------
# Windows and Linux keep ~/.qualibrate in different homes, and the common
# split deployment (qualibrate inside a WSL distro, SM native Windows) puts it
# somewhere SM's default never looks. These routes let the user point SM at
# the folder — the CHOICE persists in instance/qualibrate_location.json; the
# chosen tree itself is never written (docs/55 doctrine unchanged).


def _qualibrate_location_file() -> Path:
    return Path(current_app.instance_path) / "qualibrate_location.json"


def _normalize_config_input(text: str) -> Path:
    """User-typed config location → a Path in THIS host's dialect.

    Accepts a directory or a direct ``config.toml`` path (the env var's
    dir-or-file semantics), ``~``, both slash kinds, Explorer's quoted
    copy-as-path form, and cross-dialect spellings (``/mnt/d/…`` on Windows,
    ``D:\\…`` under WSL). Pure — existence is the caller's question."""
    raw = (text or "").strip().strip('"').strip("'")
    p = Path(raw).expanduser()
    if p.suffix == ".toml":
        p = p.parent
    # no wsl_root here: a bare /home/… typed on Windows stays as-is and the
    # locate route offers distro-anchored suggestions instead of guessing.
    return qualibrate_config._to_native(str(p), os.name)


def _classify_config_location(p: Path) -> dict:
    """READ-ONLY probe of a candidate config dir. Never raises."""
    out = {"path": str(p), "exists": False, "has_config": False,
           "n_projects": 0, "active": None, "versions_supported": None}
    try:
        if not p.is_dir():
            return out
        out["exists"] = True
        if not (p / "config.toml").is_file():
            return out
        out["has_config"] = True
        listing = qualibrate_config.list_projects(p)
        out["n_projects"] = len(listing.get("projects") or [])
        out["active"] = listing.get("active")
        out["versions_supported"] = bool(
            (listing.get("versions") or {}).get("supported", False))
    except Exception:  # noqa: BLE001 — adversarial user input must not 500
        logger.debug("config-location probe failed for %s", p, exc_info=True)
    return out


def _wsl_distros() -> list[str]:
    """Registered WSL distros visible via the ``\\\\wsl.localhost`` share.
    Windows-only; [] on any problem. Stats a network share — call only from
    user-initiated actions (Check / Scan), never on a render path."""
    if os.name != "nt":
        return []
    try:
        return sorted(d.name for d in Path(r"\\wsl.localhost").iterdir()
                      if d.is_dir())
    except OSError:
        return []


@bp.route("/qualibrate/locate", methods=["POST"])
def qualibrate_locate():
    """Validate a user-supplied config location (READ-ONLY) and render the
    preview fragment. Persistence happens only on 'Use this folder'."""
    text = request.form.get("path") or ""
    if not text.strip():
        return render_template("_qualibrate_locate_result.html", result=None,
                               suggestions=[],
                               message="Type a folder path first.")
    p = _normalize_config_input(text)
    res = _classify_config_location(p)
    suggestions = []
    if not res["has_config"] and os.name == "nt" and str(p).startswith("/"):
        # A WSL-internal path typed on Windows (e.g. /home/u/.qualibrate) —
        # anchor it on each distro share and offer the ones that resolve.
        for d in _wsl_distros():
            cand = Path("\\\\wsl.localhost\\" + d
                        + str(p).replace("/", "\\"))
            c = _classify_config_location(cand)
            if c["has_config"]:
                suggestions.append(c)
    return render_template("_qualibrate_locate_result.html", result=res,
                           suggestions=suggestions, message=None)


@bp.route("/qualibrate/locate-candidates")
def qualibrate_locate_candidates():
    """Scan the well-known locations for config trees (user-clicked — may
    stat WSL/drvfs shares). Candidates: this user's home, every WSL distro's
    /home/<user> (from Windows), every C:\\Users profile (from WSL)."""
    from quam_state_manager.core import path_match

    cands: list[dict] = []
    seen: set[str] = set()

    def add(p: Path) -> None:
        # docs/101 P11: dedup by the repo's own folder-identity rule —
        # unconditional lower() is the documented Linux data-loss class
        # (two case-different homes are two different folders there).
        k = path_match.fs_key(p)
        if k in seen:
            return
        seen.add(k)
        c = _classify_config_location(p)
        if c["has_config"]:
            cands.append(c)

    add(Path.home() / ".qualibrate")
    if os.name == "nt":
        for d in _wsl_distros():
            try:
                for u in (Path("\\\\wsl.localhost") / d / "home").iterdir():
                    add(u / ".qualibrate")
            except OSError:
                continue
        try:
            for u in (Path(Path.home().drive + "\\") / "Users").iterdir():
                add(u / ".qualibrate")
        except OSError:
            pass
    else:
        # docs/101 P12: /mnt/c/Users is a WSL-ism — native Linux homes live
        # under /home and macOS under /Users; scan whichever exists so the
        # docstring's "every user profile" promise holds off-WSL too.
        for root in (Path("/mnt/c/Users"), Path("/home"), Path("/Users")):
            try:
                for u in root.iterdir():
                    add(u / ".qualibrate")
            except OSError:
                continue
    return render_template(
        "_qualibrate_locate_result.html", result=None, suggestions=cands,
        message=None if cands else ("No config.toml found in the common "
                                    "locations — type the folder path "
                                    "manually."))


@bp.route("/qualibrate/use-location", methods=["POST"])
def qualibrate_use_location():
    """Adopt a config directory: persist the choice (instance memo only —
    the chosen tree is never written) + install the process-wide override."""
    src = qualibrate_config.config_source()
    if src["source"] == "env":
        return render_template(
            "_qualibrate_locate_result.html", result=None, suggestions=[],
            message=("An environment variable (QUALIBRATE_CONFIG_FILE / "
                     "QUALIBRATE_CONFIG_DIR) pins the config location for "
                     "this process and outranks a chosen folder — unset it "
                     "and restart SM, or point it at the right place."))
    p = _normalize_config_input(request.form.get("path") or "")
    res = _classify_config_location(p)
    if not res["has_config"]:
        return render_template("_qualibrate_locate_result.html", result=res,
                               suggestions=[], message=None)
    safe_io.atomic_write_json(_qualibrate_location_file(),
                              {"config_dir": str(p)})
    qualibrate_config.set_dir_override(str(p))
    logger.info("qualibrate config location chosen: %s", p)
    if _is_htmx():
        resp = make_response()
        resp.headers["HX-Refresh"] = "true"   # every cache keys on cfg_dir
        return resp
    return redirect(url_for("main.home"))


@bp.route("/qualibrate/use-default-location", methods=["POST"])
def qualibrate_use_default_location():
    """Drop the chosen location — back to env/default resolution."""
    try:
        _qualibrate_location_file().unlink(missing_ok=True)
    except OSError:
        logger.warning("could not remove qualibrate_location.json",
                       exc_info=True)
    qualibrate_config.set_dir_override(None)
    if _is_htmx():
        resp = make_response()
        resp.headers["HX-Refresh"] = "true"
        return resp
    return redirect(url_for("main.home"))


@bp.route("/qualibrate/open", methods=["POST"])
def qualibrate_open_project():
    """'Open in SM': load the project's effective state_path as the chip and
    add its dataset roots to the workspace. ZERO external writes — this is
    the safe daily action, distinct from 'Set active in QUAlibrate' (a later
    tier, guarded). Mirrors /load's HX-Redirect contract so the chip-identity
    tray re-renders fresh."""
    name = (request.form.get("project") or "").strip()
    listing = _qualibrate_listing()
    proj = next((p for p in listing["projects"] if p["name"] == name), None)
    if proj is None:
        return render_template("_status.html",
                               message=f"Unknown qualibrate project: {name!r}",
                               level="error"), 404
    state = proj["state_path"]
    if not state["native"] or not state["exists"]:
        return render_template(
            "_status.html",
            message=(f"Project {name!r}: its state_path "
                     f"({state['raw'] or '(empty)'}) does not exist — fix it "
                     "in qualibrate first (see the Doctor panel)."),
            level="error"), 409

    try:
        opened = _activate_quam(state["native"])
    except (FileNotFoundError, ValueError, OSError) as e:
        return render_template("_status.html", message=str(e), level="error"), 400
    _remember_load_path(state["native"])

    # a qualibrate project is a SCOPE on the context, not a new context type.
    # Explicit open PINS the user's choice — it survives cache-hit
    # re-activations of the same folder even when several projects share this
    # state_path (the reverse-matcher would refuse to guess; docs/63). The
    # pin binds to the context _activate_quam RETURNED, immediately: the
    # storage scan below can take seconds on a network mount, and re-reading
    # _active_ctx() after it would cross-wire the pin onto whatever a
    # concurrent /load activated meanwhile (the _active_wc_lock bug class).
    _acquire_project_scope(opened, explicit=name)

    # dataset roots from the project's storage location (read-only scan)
    added = 0
    storage_native = proj["storage"]["native"]
    if storage_native and proj["storage"]["exists"]:
        ws = _ws()
        existing = {str(p) for p in ws.root_folders}
        found = scheduler.find_dataset_roots(storage_native)
        for root in found:
            if root not in existing:
                try:
                    ws.add_root(root)
                    added += 1
                except (OSError, ValueError):
                    continue
        if added:
            _save_workspace_roots()
            current_app.config.pop("dataset_store", None)
        # Project lens (docs/63): remember ALL of this project's roots —
        # including already-registered ones — so Datasets/Trends can seed
        # their folder selection from the scope in later sessions too.
        _record_project_roots(name, found)

    logger.info("qualibrate open-in-sm: %s -> %s (+%d dataset roots)",
                name, state["native"], added)
    # Land on /qubits (docs/63): the sibling "open a chip" flow
    # (/workspace/select) already does, and the qubit overview is the right
    # first screen for "I opened my project" — /explorer stays the plain
    # /load target for folder-first users.
    if _is_htmx():
        resp = make_response()
        resp.headers["HX-Redirect"] = url_for("main.qubits")
        return resp
    return redirect(url_for("main.qubits"))


@bp.route("/load", methods=["POST"])
def load():
    folder = request.form.get("folder", "").strip()
    if not folder:
        return render_template("_status.html", message="No folder specified", level="error"), 400

    try:
        _activate_quam(folder)
    except (FileNotFoundError, ValueError, OSError) as e:
        # docs/114 (#15): a wrong folder used to answer with a 6-second corner
        # toast — gone before it was read. Render a PERSISTENT inline panel
        # into the load target instead, saying what was looked for and
        # offering any subfolder that ACTUALLY holds a state.json (the old
        # hint only fired on literal "quam_state" names).
        candidates = []
        try:
            base = Path(folder)
            if base.is_dir():
                for child in sorted(base.iterdir()):
                    try:
                        if child.is_dir() and (child / "state.json").exists():
                            candidates.append(str(child))
                    except OSError:
                        continue
                    if len(candidates) >= 6:
                        break
        except OSError:
            pass
        return render_template(
            "_load_failed.html",
            folder=folder, error=str(e), candidates=candidates), 400

    _remember_load_path(folder)
    _maybe_auto_add_workspace_root(folder)

    # HX-Redirect forces a full client navigation so base.html re-renders with a
    # FRESH chip-identity tray + origin badge. A plain redirect would be
    # followed as an AJAX swap into #table-pane only, leaving the topbar tray
    # showing the PREVIOUS chip's name / dirty count / read-only badge — a
    # live-vs-archive safety trap. Mirrors /dataset/<uid>/load-state.
    if _is_htmx():
        resp = make_response()
        resp.headers["HX-Redirect"] = url_for("main.explorer")
        return resp
    return redirect(url_for("main.explorer"))


# ======================================================================
# Explorer (full JSON tree)
# ======================================================================


@bp.route("/explorer")
def explorer():
    store = _store()
    if not store:
        return render_template("_empty_state.html", page="the state explorer")
    state_json = json.dumps(store.state)
    wiring_json = _wiring_json()
    template = "_explorer.html" if _is_htmx() else "explorer.html"
    return render_template(
        template,
        **_ctx(page="explorer"),
        state_json=state_json,
        wiring_json=wiring_json,
    )


# ======================================================================
# Qubits
# ======================================================================


@bp.route("/qubits")
def qubits():
    engine = _engine()
    if not engine:
        return render_template("_empty_state.html", page="qubits")

    chain_filter = request.args.get("chain")
    page = _int_arg("page", 1, minimum=1)
    per_page = _int_arg("per_page", _DEFAULT_PER_PAGE, minimum=1)

    all_qubits = engine.list_qubits()
    # chains from the UNFILTERED list — computing it after the filter left only
    # the selected chain's button rendered (docs/93 §1.1; /table at ~7022 has
    # always used this order).
    chains = sorted({q["id"][1] for q in all_qubits if len(q.get("id", "")) >= 2 and q["id"][0] == "q" and q["id"][1].isalpha()})
    if chain_filter:
        all_qubits = [q for q in all_qubits if q.get("id", "").startswith(f"q{chain_filter}")]

    page_qubits, total, page, total_pages = _paginate(all_qubits, page, per_page)

    store = _store()
    wiring_json = _wiring_json()

    template = "_qubits.html" if _is_htmx() else "qubits.html"
    return render_template(
        template,
        **_ctx(
            page="qubits",
            qubits=page_qubits,
            chains=chains,
            active_chain=chain_filter,
            current_page=page,
            total_pages=total_pages,
            total=total,
            per_page=per_page,
            wiring_json=wiring_json,
        ),
    )


# ======================================================================
# Resonators / Flux — channel-scoped qubit views (same table/filter/JSON-
# drill-down pattern as Qubits, narrowed to one channel's fields; a qubit
# without that channel is simply not a row here — see has_resonator/has_z
# in QueryEngine.get_qubit). The sidebar hides Flux chip-wide when no qubit
# has one (fixed-frequency chips); Resonators stays parallel for symmetry.
# ======================================================================


def _channel_scoped_qubits_page(*, has_key: str, page_name: str,
                                template_stub: str):
    engine = _engine()
    if not engine:
        return render_template("_empty_state.html", page=page_name)

    chain_filter = request.args.get("chain")
    page = _int_arg("page", 1, minimum=1)
    per_page = _int_arg("per_page", _DEFAULT_PER_PAGE, minimum=1)

    all_qubits = engine.list_qubits()
    # chains BEFORE the filter — same docs/93 §1.1 fix as qubits() (this helper
    # serves /resonators; /flux no longer renders the tabs but keeps the param).
    chains = sorted({q["id"][1] for q in all_qubits if len(q.get("id", "")) >= 2 and q["id"][0] == "q" and q["id"][1].isalpha()})
    if chain_filter:
        all_qubits = [q for q in all_qubits if q.get("id", "").startswith(f"q{chain_filter}")]

    scoped_qubits = [q for q in all_qubits if q.get(has_key)]
    page_qubits, total, page, total_pages = _paginate(scoped_qubits, page, per_page)

    wiring_json = _wiring_json()

    template = f"_{template_stub}.html" if _is_htmx() else f"{template_stub}.html"
    return render_template(
        template,
        **_ctx(
            page=page_name,
            qubits=page_qubits,
            chains=chains,
            active_chain=chain_filter,
            current_page=page,
            total_pages=total_pages,
            total=total,
            per_page=per_page,
            wiring_json=wiring_json,
        ),
    )


@bp.route("/resonators")
def resonators():
    return _channel_scoped_qubits_page(
        has_key="has_resonator", page_name="resonators", template_stub="resonators")


@bp.route("/flux")
def flux():
    return _channel_scoped_qubits_page(
        has_key="has_z", page_name="flux", template_stub="flux")


@bp.route("/bulk")
def bulk_edit():
    """Bulk-tune panel: rows = qubits, columns = the high-churn fields, every cell
    an editable input. Commits route through the SAME atomic /field/edit-batch +
    working-copy path the inspector uses — this is purely a denser entry surface,
    no new mutation code. Read-only render."""
    engine = _engine()
    store = _store()
    if not engine or not store:
        return render_template("_empty_state.html", page="live state editing")

    from quam_state_manager.core import mw_fem
    from quam_state_manager.core.qubit_columns import derive_qubit_columns

    # Dynamic (derived) columns — full coverage of every qubit leaf (r6 item 4).
    # The FULL model ships to the client (Properties menu + search hint).
    # r7: default to ALL VISIBLE — an opt-IN model buried fields the search
    # couldn't find (the exact complaint item 4 shipped a hint chip for), so
    # ?dynhide= is instead the client's persisted HIDDEN set (quam_bulk_
    # dynhidden): absent/empty hides nothing, i.e. every derived column
    # renders by default; the user opts OUT per-column instead of in.
    # Stale/unknown keys are silently ignored (the chip may have changed
    # under a saved set).
    dyn_model, _curated = derive_qubit_columns(store)
    _dyn_hidden = {k for k in (request.args.get("dynhide") or "").split(",") if k}
    specs: list[dict[str, Any]] = list(_BULK_COLUMNS_SPEC) + [
        {"section": c["section"], "key": c["key"], "label": c["label"],
         "tmpl": c["tmpl"], "unit": c["unit"], "default_on": True,
         "kind": c["kind"], "dyn": True,
         # per-ROW addressing + per-row mode (the pair grid's path_map). The
         # template stays for column identity, grouping and the dead-channel
         # prune below; it is no longer what a dyn cell resolves through.
         "paths": c.get("paths") or {}, "modes": c.get("modes") or {},
         "multi": c.get("multi", 0), "search": c.get("search", "")}
        for c in dyn_model
        if c.get("kind") != "note" and c["key"] not in _dyn_hidden
    ]

    columns = [
        {"key": c["key"], "label": c["label"], "section": c["section"],
         "unit": c.get("unit", ""), "default_on": c.get("default_on", True),
         "dyn": bool(c.get("dyn")), "multi": c.get("multi", 0),
         # the operation ids the fold hid from the header — search only
         "search": c.get("search", "")}
        for c in specs
    ]
    modified = _modified_map()

    # (kind, con, fem, port) -> {qubit (owner), band, freq} — built as cells
    # resolve, then used to compute each port's LO-coupled peer (Out2↔Out3, …).
    port_info: dict[tuple, dict[str, Any]] = {}
    with store._lock:
        merged = store.merged
        qids = list(store.qubit_names)
        # First pass: resolve every cell once through QUAM pointers (qubit fields
        # and the state→wiring→ports.* port chain by ONE path). The shared
        # _build_bulk_cell flags shared ports (a port dict backing >1 qubit).
        # Dynamic runtime/list columns get their read-only cell variants; a
        # curated column that (defensively) resolves to a list gets one too.
        grid: dict[str, list[dict[str, Any]]] = {}
        for qid in qids:
            cells: list[dict[str, Any]] = []
            for spec in specs:
                if spec.get("dyn"):
                    # Derived columns address per ROW: a folded per-neighbour
                    # operation is named `cz_flattop_pulse_q1` but LIVES at
                    # `cz_flattop_pulse_q1_q2`, so formatting the template hits
                    # a key no qubit owns. A qid with no entry does not carry
                    # the leaf at all — blank, not a fillable box (which is
                    # what "declared but null" keeps meaning).
                    path = (spec.get("paths") or {}).get(qid)
                    if path is None:
                        cells.append(_empty_pair_cell())
                        continue
                    kind = (spec.get("modes") or {}).get(
                        qid, spec.get("kind", "edit"))
                else:
                    path = spec["tmpl"].format(name=qid)
                    kind = spec.get("kind", "edit")
                if kind == "runtime":
                    cells.append(_runtime_pair_cell(merged, path))
                    continue
                cell = _build_bulk_cell(merged, path, modified, port_info, qid)
                # The row mode is a FLOOR on the column kind, never a
                # replacement: a null row inside a list-valued column (a qubit
                # whose exponential_filter is not set yet) must still get the ✎
                # JSON editor. Letting the row mode win rendered it as a plain
                # scalar box that happily stored a bare float where every
                # sibling holds [[amp, tau], ...] — 192 such cells on 13 real
                # chips, on exactly the filter/discrimination fields this
                # change exists to make reachable.
                if kind == "listedit" or spec.get("kind") == "listedit" \
                        or cell.get("is_list"):
                    cells.append(_list_json_cell(merged, path, modified))
                else:
                    cells.append(cell)
            grid[qid] = cells

    # Dead-CHANNEL column pruning: drop a column whose channel component (the
    # first path segment under the qubit, e.g. ``z``) is structurally absent
    # on EVERY qubit — the fixed-frequency CR chip's dead default-on "Flux
    # offset" column and all-dash Z Port section. Deliberately NOT plain
    # per-column unresolvability: dead-end OPTIONAL leaves of a present
    # channel (``xy.opx_output.delay`` on MW ports) stay visible-not-linkable
    # per the pinned bulk-grid contract, and a declared-but-null field on a
    # fresh flux chip stays fillable.
    if qids:
        def _channel_head(spec) -> str | None:
            segs = spec["tmpl"].split(".")
            # "qubits.{name}.<head>.<...>" — a component only when nested
            return segs[2] if len(segs) > 3 else None

        _chan_present = {}
        for spec in specs:
            head = _channel_head(spec)
            if head is None or head in _chan_present:
                continue
            _chan_present[head] = any(
                isinstance((merged.get("qubits", {}).get(qid) or {}).get(head), dict)
                for qid in qids)
        keep = [i for i, spec in enumerate(specs)
                if _chan_present.get(_channel_head(spec), True)]
        if len(keep) < len(columns):
            columns = [columns[i] for i in keep]
            for qid in qids:
                grid[qid] = [grid[qid][i] for i in keep]

    # Second pass: attach MW-FEM LO/band metadata to each band / up-or-downconverter-
    # frequency port cell — the LO-coupled peer, its owning qubit + band — so the
    # client can show "shares LO with qX (band N)" and warn when a freq leaves its band.
    rows = []
    for qid in qids:
        for cell in grid[qid]:
            _attach_lo_meta(cell, port_info)
        rows.append({"id": qid, "cells": grid[qid]})

    _bulk_col_maxlen(columns, grid, qids)
    column_groups = _bulk_column_groups(columns)

    # Slim per-qubit metadata for the ⚏ Qubits row picker (chip-map + grouped
    # list): id + grid_location only — the map's geometry comes from the chip
    # itself (row 0 = bottom; the client flips Y). Non-string/absent grids
    # degrade the picker to its list-only form.
    qubit_meta = []
    for qid in qids:
        g = (merged.get("qubits", {}).get(qid) or {}).get("grid_location")
        qubit_meta.append({"id": qid, "grid": g if isinstance(g, str) else None})

    # Pair grid (stacked below the qubit table): columns are DERIVED from the chip's
    # real pair leaves — lab-flexible, no hardcoded gate/leaf names. Same cell
    # pipeline + commit path. Empty for chips with no pairs / no editable pair leaves.
    pair_columns, pair_groups, pair_rows = _pair_bulk_grid(store, modified)

    band_meta = {"bands": {str(b): list(r) for b, r in mw_fem.BANDS.items()}}
    # Client model for the Properties menu + search hint: key/label/section/
    # unit/kind only — never the per-qubit tmpl values (the server re-derives
    # cells from ?dynhide; the client only needs identity + display metadata).
    dyn_cols = [
        {"key": c["key"], "label": c["label"], "section": c["section"],
         "unit": c.get("unit", ""), "kind": c.get("kind", "edit")}
        for c in dyn_model
    ]
    # The cap's truncation note used to be filtered out with the other
    # kind="note" entries — a SILENT cap (docs/94: a real 452-column chip lost
    # its z-port filter columns with no trace). Surface it as an honest line.
    dyn_truncated = next(
        (c["label"] for c in dyn_model if c.get("kind") == "note"), None)
    # docs/120 item 4 — validated against BOTH grids, because they share the
    # one #bulk-search box, so a chip must not go dead just because its columns
    # live in the pair table.
    filter_chips = _bulk_filter_chips(columns, pair_columns)
    template = "_bulkedit.html" if _is_htmx() else "bulkedit.html"
    html = render_template(template, **_ctx(page="bulk", columns=columns, rows=rows,
                                            column_groups=column_groups, band_meta=band_meta,
                                            dyn_cols=dyn_cols, qubit_meta=qubit_meta,
                                            pair_columns=pair_columns, pair_groups=pair_groups,
                                            pair_rows=pair_rows, filter_chips=filter_chips,
                                            dyn_truncated=dyn_truncated))
    # docs/103: this is the app's largest response by an order of magnitude
    # (measured 10.0 MB / 6.5 MB HTML on real 21Q/10Q chips — docs/85 ships
    # every cell deliberately). Repetitive table markup gzips ~25x, so
    # compress when the client advertises it — same stdlib pattern as
    # /bulk/all-values, Content-Length pinned to the actual bytes.
    body = html.encode("utf-8")
    accepts_gzip = "gzip" in request.headers.get("Accept-Encoding", "")
    if accepts_gzip:
        body = gzip.compress(body, compresslevel=5)
    resp = current_app.response_class(body)
    resp.headers["Content-Type"] = "text/html; charset=utf-8"
    resp.headers["Content-Length"] = str(len(body))
    if accepts_gzip:
        resp.headers["Content-Encoding"] = "gzip"
    resp.headers["Vary"] = "Accept-Encoding"
    return resp


@bp.route("/bulk/all-values")
def bulk_all_values():
    """Flat 'All values' completeness payload for the Live State Edit → All values tab.

    Enumerates EVERY leaf of merged state+wiring (core/all_values), so a user can
    edit LITERALLY ALL scalar values; pointers/lists/membership are read-only. This
    is a SIBLING of /bulk, fetched lazily by all-values.js only on first activation
    of the All-values segmented-control tab — it is never embedded in the /bulk page
    render, so the daily Qubits-grid load carries zero extra bytes.

    Payload is gzipped (stdlib, no new dep) only when the client advertises it; the
    ETag folds both ``mutation_seq`` AND ``len(change_log)`` because the per-row
    ``modified`` flag derives from the change log, which a sync/apply/discard can
    reset WITHOUT advancing mutation_seq — so (chip, mutation_seq) alone would let a
    304 surface stale 'modified' markers. The v2 salt additionally folds the
    payload version + type-policy inputs (assignment count, manifest versions)
    — the ``ty`` chips depend on them, not on the chip content. Content-Length
    is pinned to the actual (maybe-compressed) byte count so a manual-gzip
    desync can't blank the tab.
    """
    from quam_state_manager.core.all_values import build_all_values_rows

    store = _store()
    if not store:
        return jsonify(rows=[], summary={"total": 0, "editable": 0,
                                         "readonly": 0, "by_kind": {},
                                         "arrays": 0, "empties": 0}), 200
    with store._lock:
        rows, summary = build_all_values_rows(store, _modified_map())
        mseq = store.mutation_seq
        mver = len(store.change_log)
        merged = store.merged
    policy = getattr(store, "type_policy", None)
    ctx = _active_ctx() or {}
    chip_tag = hashlib.sha1(str(ctx.get("path", "")).encode("utf-8")).hexdigest()[:12]
    # v2 salt: payload-shape version + the policy inputs the ty chips derive from
    # (assignment count + manifest versions), so a type-assign or env manifest
    # warm can't 304 a client into stale chips.
    if policy is not None:
        man_tag = (hashlib.sha1(repr(policy.manifest.get("versions") or {})
                                .encode("utf-8")).hexdigest()[:8]
                   if policy.manifest is not None else "0")
        etag = f'"{chip_tag}-{mseq}-{mver}-v2-{len(policy.assignments)}-{man_tag}"'
    else:
        etag = f'"{chip_tag}-{mseq}-{mver}-v2"'
    if request.headers.get("If-None-Match") == etag:
        r = make_response("", 304)
        r.headers["ETag"] = etag
        return r
    # v2 expected-type chips (200 path only — a 304 must not pay for them):
    # annotate SCALAR rows only (xref/list enforcement fires at the resolved
    # target — /field/peek covers those on demand). One pass, policy.annotate is
    # O(depth) per path; outside the lock like field_peek's phases 2–3
    # (read-only walk of the captured merged ref). Cap: a >20k-row chip skips
    # the chips entirely rather than stall the tab.
    if policy is not None and len(rows) <= 20000:
        for row in rows:
            if row[2] != "scalar":
                continue
            try:
                cur: Any = merged
                for seg in row[0].split("."):
                    cur = cur[int(seg)] if isinstance(cur, list) else cur[seg]
                ann = policy.annotate(merged, row[0], cur)
            except Exception:  # noqa: BLE001 — annotation must never break the tab
                continue
            if ann:
                row.append({"ty": {"t": ann["type"], "s": ann["source"]}})
    body = json.dumps({"rows": rows, "summary": summary},
                      separators=(",", ":")).encode("utf-8")
    accepts_gzip = "gzip" in request.headers.get("Accept-Encoding", "")
    if accepts_gzip:
        # OUTSIDE store._lock (released above). L6 = default sweet spot (~6.5x on the
        # shared-prefix paths; L9's ~3.5% gain isn't worth the CPU).
        body = gzip.compress(body, compresslevel=6)
    resp = make_response(body)
    resp.headers["Content-Type"] = "application/json"
    resp.headers["Content-Length"] = str(len(body))   # pin to ACTUAL bytes — no desync
    if accepts_gzip:
        resp.headers["Content-Encoding"] = "gzip"
    resp.headers["Vary"] = "Accept-Encoding"
    resp.headers["ETag"] = etag
    resp.headers["Cache-Control"] = "no-cache"         # revalidate via ETag; never serve stale across an edit
    return resp


def _empty_pair_cell() -> dict[str, Any]:
    """A blank cell for a column a given pair doesn't carry."""
    return {"dot_path": "", "resolved_path": "", "display": "", "is_pointer": False,
            "missing": True, "linkable": False, "modified": False, "old_display": "",
            "editable": False, "kind": "missing"}


def _runtime_pair_cell(merged: dict, path: str) -> dict[str, Any]:
    """A read-only cell for a runtime self-ref leaf (e.g. ``#./inferred_duration``):
    show the RESOLVED value but never let it be edited (editing the pointer with a
    literal would silently break length inference)."""
    from quam_state_manager.core.pointer_path import resolve_field_target
    try:
        ft = resolve_field_target(merged, path)
    except Exception:
        ft = {}
    val = ft.get("resolved_value")
    # inferred_* self-refs point at a property QUAM computes at config time (not in
    # the JSON), so they don't resolve to a stored number — show a clean ⟳ marker
    # rather than the raw pointer. A self-ref that DOES resolve to a scalar shows
    # its value; an alias to a whole structure (operations.x180 → the pulse dict;
    # resolved_value scalar-nulls containers) shows ⟳ too, not a blank cell.
    if not ft.get("resolvable") or is_pointer(val) or val is None:
        display = "⟳"
    else:
        display = _bulk_display(val)
    return {"dot_path": path, "resolved_path": ft.get("resolved_path") or path,
            "display": display, "is_pointer": True, "missing": val is None,
            "linkable": False, "modified": False, "old_display": "",
            "editable": False, "kind": "runtime"}


def _list_pair_cell(merged: dict, pair_id: str, path: str) -> dict[str, Any]:
    """A badge cell for a list leaf (confusion matrix, etc.) that deep-links to
    the pair inspector; the ✎ opens the whole-value JSON editor (r6 item 4)."""
    from quam_state_manager.core.pointer_path import _walk as _walk_abs, resolve_field_target
    try:
        ft = resolve_field_target(merged, path)
        # resolved_value is scalar-nulled for containers — fetch the real list
        # (the ▦ dims badge rendered empty off resolved_value).
        found, val = _walk_abs(merged, (ft.get("resolved_path") or path).split("."))
        if not found or not ft.get("resolvable"):
            val = None
    except Exception:
        val = None
    if isinstance(val, list) and val and all(isinstance(r, list) for r in val):
        badge = "▦ %d×%d" % (len(val), len(val[0]) if val[0] else 0)
    elif isinstance(val, list):
        badge = "[ %d ]" % len(val)
    else:
        badge = ""
    return {"dot_path": path, "resolved_path": path, "display": badge,
            "is_pointer": False, "missing": val is None, "linkable": False,
            "modified": False, "old_display": "", "editable": False,
            "kind": "list", "pair_id": pair_id}


def _list_json_cell(merged: dict, path: str, modified: dict) -> dict[str, Any]:
    """A qubit-grid cell for a real LIST value (confusion matrix, filter taps):
    compact JSON preview + the ✎ button that opens the client's JSON editor.
    Commits ride the SAME atomic /field/edit-batch path — the popup posts the
    whole parsed list in the JSON body, so no string round-trip."""
    from quam_state_manager.core.pointer_path import _walk as _walk_abs, resolve_field_target
    try:
        ft = resolve_field_target(merged, path)
    except Exception:
        ft = {}
    resolved = ft.get("resolved_path") or path
    # resolved_value is scalar-nulled for containers — fetch the real list.
    val = None
    if ft.get("resolvable"):
        found, node = _walk_abs(merged, resolved.split("."))
        val = node if found else None
    try:
        preview = json.dumps(val, separators=(",", ":")) if val is not None else ""
    except (TypeError, ValueError):
        preview = str(val)
    if len(preview) > 24:
        preview = preview[:24] + "…"
    return {"dot_path": path, "resolved_path": resolved, "display": preview,
            "is_pointer": bool(ft.get("is_pointer")), "missing": val is None,
            "linkable": False, "modified": resolved in modified,
            "old_display": "", "editable": False, "kind": "listedit"}


def _pair_bulk_grid(store: QuamStore, modified: dict
                    ) -> tuple[list[dict], list[dict], list[dict]]:
    """Build the pair grid (columns, column_groups, rows). Columns are derived from
    the chip's real pair leaves via ``pair_columns.derive_pair_columns``; each cell
    resolves through the SAME ``_build_bulk_cell`` pipeline as the qubit grid, so
    edits ride the existing ``/field/edit-batch`` path with no new mutation code."""
    from quam_state_manager.core.pair_columns import derive_pair_columns
    columns, path_map = derive_pair_columns(store)
    if not columns:
        return [], [], []

    port_info: dict[tuple, dict[str, Any]] = {}
    with store._lock:
        merged = store.merged
        pair_ids = list(store.qubit_pair_names)
        grid: dict[str, list[dict[str, Any]]] = {}
        for pid in pair_ids:
            pm = path_map.get(pid, {})
            cells = []
            for col in columns:
                spec = pm.get(col["key"])
                if not spec or spec[0] is None:
                    cells.append(_empty_pair_cell())
                    continue
                path, mode = spec
                if mode == "runtime":
                    cells.append(_runtime_pair_cell(merged, path))
                elif mode == "list":
                    cells.append(_list_pair_cell(merged, pid, path))
                else:
                    cell = _build_bulk_cell(merged, path, modified, port_info, pid)
                    cell["editable"] = True
                    cell["kind"] = "scalar"
                    cells.append(cell)
            grid[pid] = cells

    rows = []
    for pid in pair_ids:
        for cell in grid[pid]:
            if "_port" in cell:
                _attach_lo_meta(cell, port_info)
        rows.append({"id": pid, "cells": grid[pid]})

    _bulk_col_maxlen(columns, grid, pair_ids)
    groups = _bulk_column_groups(columns)
    return columns, groups, rows


# ── docs/117: the auto-apply session ──────────────────────────────────────
# The covenant (docs/107) reads, as amended by the user on 2026-08-12: a direct
# live write happens only on an explicit Apply press OR inside a user-enabled
# auto-apply session. The session is what that press authorizes, so it lives on
# the ctx — it ends for free on chip switch, LRU eviction and restart, and it is
# never persisted to disk (an armed session must not outlive the app that shows
# it). There is deliberately no idle timeout: the user asked for "armed until I
# turn it off", and the pill makes the state impossible to miss.
_AUTO_SNAPSHOT_MIN_S = 120.0     # post-apply history snapshots, per session


# \u2500\u2500 docs/120 item 8 \u2014 Auto-Sync: the covenant amended a second time \u2500\u2500\u2500\u2500\u2500\u2500\u2500
#
# The user, on 2026-08-16: "many mature users run VS Code with auto-save on.
# When qualibrate updates, VS Code always gets the SYNCED file. But SM's
# original design concept was: never pull/push the source of truth without the
# user's permission. It's time to drop that. What users want is auto pull/push
# WHEN THEY ALLOW IT. Push is done; now pull."
#
# docs/107 stated the covenant as "any direct live write requires >=1 explicit
# Apply-to-live press"; docs/117 amended its SCOPE so that one press could
# authorize a session. This amends the OTHER direction for the first time: a
# session may also replace the working copy from live without asking again.
#
# The floor that did not move, and the reason this is safe to offer: ticking
# "auto replace" IS the consent. The user said so explicitly -- "if 1-1 is
# checked, live ALWAYS wins" -- and that is the only configuration in which SM
# discards work without a question. With it UNCHECKED and local edits present,
# SM still refuses to choose and raises the banner, which is docs/87's rule
# ("SM never swaps what you are looking at") intact.
#
#   pull  replace  local edits   behaviour
#   ----  -------  -----------   -------------------------------------------
#   on    on       either        live wins, silently -- pull and replace
#   on    off      no            pull silently (a provably clean copy; this is
#                                today's RECONCILE_SYNCED, unchanged)
#   on    off      YES           do NOT pull; raise the drift banner and let
#                                the user choose
#   off   -        -             byte-identical to today
#
# ONE session dict holds all three flags. `_auto_apply_state` reads it only
# when `push` is on, so every existing push code path -- the tray beacon, the
# flusher, the applied log, the revert anchor, `_auto_disarm_response` -- is
# untouched by construction rather than by test.


def _auto_sync_state(ctx: dict | None = None) -> dict | None:
    """The armed Auto-Sync session for the active (or given) ctx, else None."""
    ctx = ctx if ctx is not None else _active_ctx()
    if not ctx or ctx.get("type") != "quam":
        return None
    return ctx.get("auto_apply") or None


def _auto_apply_state(ctx: dict | None = None) -> dict | None:
    """The armed session, but ONLY when it authorizes pushing.

    docs/120 item 8: the session grew pull flags, and every caller of this
    helper is a push-side concern (the flusher's beacon, the applied log, the
    revert anchor). A pull-only session must read as "not armed" to all of
    them, or arming pull alone would start writing to the live chip.
    """
    sess = _auto_sync_state(ctx)
    return sess if (sess and sess.get("push")) else None


def _auto_apply_armable(ctx: dict | None = None) -> tuple[bool, str]:
    """(can arm PUSH, why not). Refusals are the ones where arming would be a
    trap, not a nuisance: nothing to write to, nothing writable, or a chip that
    has ALREADY diverged (arming into a guaranteed immediate conflict)."""
    ctx = ctx if ctx is not None else _active_ctx()
    if not ctx or ctx.get("type") != "quam":
        return False, "No chip is open."
    if (ctx.get("origin") or "live") != "live":
        return False, "This is a read-only archive."
    if ctx.get("live_readonly_hint"):
        return False, "The live folder is read-only."
    if ctx.get("live_diverged"):
        return False, ("The live chip changed outside SM \u2014 resolve that first.")
    return True, ""


def _auto_pull_armable(ctx: dict | None = None) -> tuple[bool, str]:
    """(can arm PULL, why not) \u2014 a DIFFERENT question from push.

    Two of push's refusals are backwards here and one is irrelevant:

    * ``live_diverged`` is push's "you would immediately conflict". For pull it
      is the very condition that makes pulling useful, so refusing on it would
      disarm the feature exactly when it is wanted.
    * ``live_readonly_hint`` describes writing TO live. A pull only reads live
      and writes the working copy, so a read-only folder is no obstacle.

    What remains is the same as push: something to sync, and not an archive.
    """
    ctx = ctx if ctx is not None else _active_ctx()
    if not ctx or ctx.get("type") != "quam":
        return False, "No chip is open."
    if (ctx.get("origin") or "live") != "live":
        return False, "This is a read-only archive."
    return True, ""


def _applied_log_rows(ctx: dict | None = None, limit: int = 50) -> list[dict]:
    """The applied log: journal units that actually reached the live chip in an
    auto-apply session, newest first.

    Source is `ctx["undo_units"]` (docs/107) rather than a second store: it is
    already per chip, already on disk, already segmented one-row-per-user-action
    and already holds the old/new the revert needs. The cap matters because the
    tray renders on EVERY page and on every mutation.
    """
    ctx = ctx if ctx is not None else _active_ctx()
    if not ctx or ctx.get("type") != "quam":
        return []
    rows: list[dict] = []
    for u in reversed(ctx.get("undo_units") or []):
        meta = u.get("meta") or {}
        if meta.get("src") != "auto":
            continue
        ents = u.get("entries") or []
        if not ents:
            continue
        rows.append({
            "id": u.get("id"),
            "ts": u.get("ts"),
            "n": len(ents),
            "entries": ents,
            "reverted_by": meta.get("reverted_by"),
        })
        if len(rows) >= limit:
            break
    return rows


def _render_tray(*, oob: bool) -> str:
    """Render ``#pending-tray`` — the single tray renderer for both direct
    target swaps and OOB swaps.

    ALWAYS passes the active chip identity. The template gates the whole
    tray DOM on ``active_name | change_count | working_dirty``; the old
    ``_tray_html`` omitted ``active_name``, so a save/discard that zeroed
    the change count on a clean chip made the entire tray (and the chip
    name with it) vanish. One renderer, one contract.
    """
    modifier = _modifier()
    changes = modifier.get_change_log() if modifier else []
    ident = _active_chip_identity()
    return render_template(
        "_pending_tray.html",
        changes=changes,
        change_count=len(changes),
        working_dirty=_working_dirty(),
        active_name=ident["name"] if ident else None,
        chip_origin=ident["origin"] if ident else "live",
        qualibrate_tray=_qualibrate_tray_badge(),
        # docs/20 v2: the last apply's pre-apply snapshot ts (this session) —
        # powers the explicit "Revert last apply…" affordance.
        last_apply=(_active_ctx() or {}).get("last_apply"),
        # docs/110 #10-A: PaneState's freshness beacon (see _pending_tray.html)
        mutation_seq=(getattr(_store(), "mutation_seq", "") if _store() else ""),
        # docs/114 (#16): a read-only live folder is announced BEFORE 20
        # minutes of edits, not at apply time (os.access W_OK — optimistic on
        # NTFS ACLs but reliable for the read-only-share case this is for).
        live_readonly=bool((_active_ctx() or {}).get("live_readonly_hint")),
        # docs/117: the pill and the behaviour read the SAME attribute on the
        # SAME element, so they cannot disagree; an F5 keeps the truth because
        # the server owns it.
        auto_apply=_auto_apply_state(),
        auto_apply_armable=_auto_apply_armable(),
        auto_sync=_auto_sync_state(),
        auto_pull_armable=_auto_pull_armable(),
        applied_log=_applied_log_rows(),
        oob=oob,
    )


def _tray_html() -> str:
    """Render #pending-tray (non-OOB, for direct target swaps)."""
    return _render_tray(oob=False)


def _tray_oob() -> str:
    """Render #pending-tray as an HTMX OOB swap fragment."""
    return _render_tray(oob=True)


def _diverged_oob() -> str:
    """Render the ``#live-diverged-slot`` as an OOB swap fragment.

    Used by in-place chip switches (where a full page render does NOT happen),
    so the live-files-replaced banner reflects the newly-activated chip instead
    of staying stuck on the previous one. Mirrors the OOB copy ``_explorer.html``
    emits on the chip-load redirect path.
    """
    ident = _active_chip_identity()
    return (
        '<div id="live-diverged-slot" hx-swap-oob="outerHTML">'
        + render_template(
            "_live_diverged_banner.html",
            live_diverged=bool(ident and ident["live_diverged"]),
            live_drift_count=(ident or {}).get("live_drift_count"),
            active_name=ident["name"] if ident else None,
        )
        + "</div>"
    )


def _fmt_val(v) -> str:
    """Format a value the same way _qubit_detail.html does for input value=."""
    if v is None:
        return ""
    if isinstance(v, float):
        abs_v = abs(v)
        if abs_v >= 1e6 or (0 < abs_v < 1e-3):
            return "%.6e" % v
    return str(v)


def _modified_map() -> dict[str, Any]:
    """Build dot_path -> original old_value map from the change log.

    Uses setdefault so that when a field is edited multiple times,
    only the *first* (original) value is recorded.
    """
    store = _store()
    if not store:
        return {}
    m: dict[str, Any] = {}
    for entry in store.change_log:
        if entry.deleted:
            continue  # no live cell corresponds to a deleted subtree
        m.setdefault(entry.dot_path, entry.old_value)
    return m


def _modified_delta() -> list[dict[str, Any]]:
    """The change-log as a list of ``{resolved_path, old_value, old_display}`` —
    sent back on every edit so open surfaces (Bulk Edit, Explorer) can re-mark the
    persistent 'modified since load' cells without a full re-render. Keyed by the
    RESOLVED write path (matches what each cell stores)."""
    return [
        {"resolved_path": p, "old_value": ov, "old_display": _bulk_display(ov)}
        for p, ov in _modified_map().items()
    ]


def _render_qubit_detail(name: str, *, focus_path: str | None = None):
    """Shared renderer for qubit detail (used by both view and edit routes)."""
    engine = _engine()
    store = _store()
    if not engine or not store:
        return render_template("_status.html", message="No state loaded", level="warning")

    try:
        data = engine.get_qubit(name)
    except KeyError as e:
        return render_template("_status.html", message=str(e), level="error"), 404

    sections = _build_qubit_sections(name, data, store)

    port_info = {}
    for ch in ("xy", "rr", "z"):
        port_info[ch] = engine.get_port_for(name, ch)

    template = "_qubit_detail.html" if _is_htmx() else "qubit_detail.html"
    return render_template(
        template,
        **_ctx(
            page="qubit_detail",
            qubit=data,
            qubit_name=name,
            sections=sections,
            port_info=port_info,
            modified_map=_modified_map(),
            focus_path=focus_path,
        ),
    )


# The qubit-detail quick add-pulse form was removed — the Pulses page
# create flow (/pulse/new + /api/pulse/create, driven by pulse_catalog)
# is the single add-pulse surface now (feedback #10).

# xy_detuned: FixedFrequencyZZDriveTransmon qubits carry the Stark-CZ target
# lobe's channel — its zz_* twin ops render in the inspector like any other.
_PULSE_CHANNELS = ("xy", "z", "resonator", "xy_detuned")

_PULSE_NAME_RE = re.compile(r"^[a-zA-Z][a-zA-Z0-9_]{0,63}$")

# A dotted Python class path (the create form's editable __class__ field).
_QCLASS_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*(\.[A-Za-z_][A-Za-z0-9_]*)*$")


@bp.route("/qubit/<name>")
def qubit_detail(name: str):
    # ?focus=<dot-path> deep-links straight to a field (scroll + focus + flash)
    # — exposes the existing focus_path plumbing to the data-viewer value chip.
    return _render_qubit_detail(name, focus_path=request.args.get("focus") or None)


# f_01 ↔ RF_frequency twin paths (suffix rewrite). RF_frequency is the carrier the
# hardware actually plays (the config's intermediate_frequency is inferred from it);
# f_01 is bookkeeping the calibration keeps equal to it. Order matters: the
# ``.resonator.*`` rules must precede the bare ``.f_01`` / ``.xy.*`` rules so a
# resonator path maps within the resonator, never to the xy drive.
_FREQ_TWIN_RULES = [
    (".resonator.f_01", ".resonator.RF_frequency"),
    (".resonator.RF_frequency", ".resonator.f_01"),
    (".xy.RF_frequency", ".f_01"),
    (".f_01", ".xy.RF_frequency"),
]


def _freq_twin_path(dot_path: str) -> str | None:
    """The f_01↔RF_frequency twin dot-path for a qubit freq field, or None.

    Scoped to ``qubits.*`` so an unrelated ``.f_01`` suffix elsewhere (e.g.
    ``twpas.<t>.spectroscopy.f_01``) is never given a phantom twin."""
    if not dot_path.startswith("qubits."):
        return None
    for suf, twin_suf in _FREQ_TWIN_RULES:
        if dot_path.endswith(suf):
            return dot_path[: -len(suf)] + twin_suf
    return None


def _maybe_mirror_freq(modifier, dot_path: str, entry, group_id: str | None = None) -> str | None:
    """Soft-link an inspector edit: when the just-edited f_01/RF field had a twin that
    still held the SAME pre-edit value — i.e. they were coupled, as the calibration
    nodes write them — mirror the committed value into the twin too, so the user
    needn't type it twice. A deliberate detuning (the twin already differed) is left
    untouched, and only plain numbers are mirrored (a pointer-encoded twin is
    skipped). Returns the twin dot_path if it mirrored, else None.

    Equality-keyed soft link, matching the bulk table's client-side mirror. Lives in
    the inspector's per-field edit route only, so it never double-fires with the bulk
    table (which posts BOTH cells in one /field/edit-batch when coupled)."""
    twin = _freq_twin_path(dot_path)
    if not twin:
        return None
    old = entry.old_value
    if not isinstance(old, (int, float)) or isinstance(old, bool):
        return None  # primary was a pointer / non-number → leave the twin alone
    store = modifier.store
    try:
        twin_old = store.get_value(twin)
    except (KeyError, TypeError, ValueError, IndexError):
        return None
    if not isinstance(twin_old, (int, float)) or isinstance(twin_old, bool):
        return None
    if twin_old != old:
        return None  # already detuned → respect it
    try:
        # Share the primary edit's group_id so a single Ctrl+Z (undo_group)
        # reverts BOTH the primary and this mirror atomically.
        modifier.set_value(twin, entry.new_value, group_id=group_id)
        return twin
    except (KeyError, TypeError, ValueError, IndexError):
        return None


def _freq_mirror_oob(mirrored: str) -> str:
    """An out-of-band status-bar toast telling the user the twin was kept in sync."""
    leaf = mirrored.rsplit(".", 1)[-1]
    parent = "readout" if ".resonator." in mirrored else "xy"
    toast = render_template(
        "_status.html",
        message=f"\U0001F517 Also set {parent}.{leaf} to match — f₀₁↔RF linked.",
        level="success")
    return f'\n<div id="status-bar" hx-swap-oob="innerHTML">{toast}</div>'


@bp.route("/qubit/<name>/edit", methods=["POST"])
def qubit_edit(name: str):
    ctx = _active_ctx()
    modifier = ctx.get("modifier") if ctx else None
    if not modifier:
        return render_template("_status.html", message="No state loaded", level="warning")

    # Chip-identity gate (audit #1): reject a stale tab's edit whose render-time
    # chip token no longer matches the loaded chip — same protection the bulk /
    # all-values grids already carry; the inspector forms were the opt-in gap.
    guard = _chip_mismatch_html(
        request.form.get("expect_chip", ""),
        request.form.get("force_chip") in ("1", "true", "True"))
    if guard is not None:
        return guard

    dot_path = request.form.get("dot_path", "")
    raw_value = request.form.get("value", "")
    # Default ON: mirror f_01↔RF unless the client's 🔗 toggle is explicitly off.
    freq_sync = request.form.get("freq_sync", "1") != "0"

    try:
        # Same server-side hardening as /field/edit (audit-P0), so these legacy
        # inspector routes aren't an open side door around it: resolve pointer
        # leaves to their literal target (value-mode, never stringify the
        # pointer), enforce the read-only policy, and parse against the
        # TARGET's expected type (falls back to _parse_value byte-identically;
        # a non-finite 'inf'/'1e999' still becomes a 400, not a 500).
        target_path = _resolve_edit_path(modifier.store, dot_path)
        _ro = _editability_reason(modifier.store, target_path)
        if _ro is not None:
            raise ValueError(_ro)
        parsed = _parse_for_target(modifier.store, target_path, raw_value)
        # Mint one group id when the freq-mirror can fire, so the primary edit
        # and its mirrored twin share it and a single Ctrl+Z reverts both
        # atomically (otherwise one undo reverts only the twin → f_01≠RF
        # detuned, the exact invariant the mirror exists to hold). A lone edit
        # with no twin stays ungrouped, so one undo still reverts it.
        gid = (modifier.new_group_id()
               if freq_sync and _freq_twin_path(target_path) else None)
        entry = modifier.set_value(target_path, parsed, group_id=gid)
        mirrored = _maybe_mirror_freq(modifier, target_path, entry, group_id=gid) if freq_sync else None
        _invalidate_engine_cache(ctx)
    except (KeyError, TypeError, ValueError, IndexError) as e:
        return render_template("_status.html", message=str(e), level="error"), 400

    detail = _render_qubit_detail(name, focus_path=dot_path)
    if isinstance(detail, tuple):  # (html, status_code) — error path
        return detail
    out = detail + "\n" + _tray_oob()
    if mirrored:
        out += _freq_mirror_oob(mirrored)
    # Refresh the diagnostics badge/banner (e.g. the f_01↔RF consistency finding)
    # and pulse-derived surfaces — the inspector edit otherwise leaves them stale
    # until a full reload. Mirrors the gate-macro / undo paths.
    resp = make_response(out)
    resp.headers["HX-Trigger"] = "pulses-changed, diagnostics-changed"
    return resp


def _op_of_path(dot_path: str) -> str | None:
    """The operation alias in a dot-path (segment after ``operations``), if any."""
    segs = dot_path.split(".")
    if "operations" in segs:
        i = segs.index("operations")
        if i + 1 < len(segs):
            return segs[i + 1]
    return None


def _resolve_edit_path(store, dot_path: str) -> str:
    """Follow a pointer-valued leaf to its literal target (value-mode). The canonical
    implementation is core.edit_policy.resolve_edit_path — SHARED with the CLI so
    the two edit paths can't diverge (they did: the CLI stringified pointer leaves)."""
    from quam_state_manager.core.edit_policy import resolve_edit_path
    return resolve_edit_path(store, dot_path)


_BRACKET_SEG_RE = re.compile(r"\[(\d+)\]")


def _normalize_dot_path(dot_path: str) -> str:
    """Rewrite legacy bracket segments (``parent[3]``) to canonical dot form
    (``parent.3``). One-release compatibility shim for stale copied/bookmarked
    paths from before the dot-form list-element grammar; only ``[digits]`` is
    rewritten (free-form ``extras.*`` keys never carry that shape — and a wrong
    rewrite fails safe with a 400 path error, never a mis-write)."""
    if "[" not in dot_path:
        return dot_path
    return _BRACKET_SEG_RE.sub(r".\1", dot_path)


def _active_chip_token() -> str | None:
    """Fingerprint token of the LOADED chip (in-memory state+wiring), or None."""
    store = _store()
    if not store:
        return None
    from quam_state_manager.core import history
    return history.fingerprint_token(
        history.fingerprint_from_dicts(store.state, store.wiring))


def _chip_mismatch_response(expect_chip: str, force_chip: bool):
    """409 JSON if *expect_chip* (a run's fingerprint token) doesn't match the
    loaded chip and the caller didn't force it; else None.

    The dataset "Apply fitted value" path stamps the run's token here so a fit
    can't be silently written onto a different loaded chip that happens to reuse
    the same qubit names (audit #1). Other edit callers send no token → no gate.
    """
    expect_chip = (expect_chip or "").strip()
    if not expect_chip or force_chip:
        return None
    active = _active_chip_token()
    if active is not None and active != expect_chip:
        return jsonify(
            ok=False, chip_mismatch=True,
            error="This value came from a different chip than the one loaded — "
                  "applying it would write onto the wrong chip.",
        ), 409
    return None


def _chip_mismatch_html(expect_chip: str, force_chip: bool):
    """HTML-flavoured chip-identity guard for the inspector edit routes, which
    return an ``_status.html`` fragment (not JSON like /field/edit). Returns a
    409 status template when the client's render-time chip token no longer
    matches the loaded chip (another tab switched chips), else None. Keeps the
    inspector forms from the "opt-in gate" gap the bulk/all-values grids close.
    """
    if force_chip:
        return None
    expect_chip = (expect_chip or "").strip()
    if not expect_chip:
        return None
    active = _active_chip_token()
    if active is not None and active != expect_chip:
        return render_template(
            "_status.html",
            message="This edit was staged against a different chip than the one "
                    "now loaded — not applied (another tab may have switched chips).",
            level="error"), 409
    return None


@bp.route("/chip/active-token", methods=["GET"])
def chip_active_token():
    """The loaded chip's fingerprint token + display name (for the apply-fit
    cross-chip pre-check)."""
    from pathlib import Path as _Path
    from quam_state_manager.core import history
    name = ""
    ctx = _active_ctx()
    if ctx and ctx.get("path"):
        try:
            name = history.chip_name_for(_Path(ctx["path"]))
        except (OSError, ValueError):
            name = ""
    # ``loaded`` distinguishes "no chip loaded" from "loaded but token
    # uncomputable" (corrupt wiring) — clients that treat the ACTIVE context as
    # authoritative need the truth, not an empty-token proxy for it.
    return jsonify(token=_active_chip_token() or "", name=name,
                   loaded=bool(ctx and ctx.get("path")),
                   path=(ctx.get("path") if ctx else None) or "")


@bp.route("/field/edit", methods=["POST"])
def field_edit():
    """Generic field editor — works for any dot-path in state or wiring."""
    # Capture the context up front so the post-mutation cache invalidation binds
    # to THIS chip even if a concurrent /load flips the active context.
    ctx = _active_ctx()
    modifier = ctx.get("modifier") if ctx else None
    if not modifier:
        return jsonify(ok=False, error="No active context"), 400

    dot_path = _normalize_dot_path(request.form.get("dot_path", "").strip())
    raw_value = request.form.get("value", "")

    if not dot_path:
        return jsonify(ok=False, error="dot_path required"), 400

    guard = _chip_mismatch_response(
        request.form.get("expect_chip", ""),
        request.form.get("force_chip") in ("1", "true", "True"))
    if guard is not None:
        return guard

    from quam_state_manager.core import type_policy as _tp
    try:
        # Resolve FIRST, parse against the TARGET's expectation: enforcement
        # fires at the resolved write path, and cross-class pointer chains
        # (z-op pulse field → pair-macro child) can change the expected type.
        target_path = _resolve_edit_path(modifier.store, dot_path)
        # Same server-side read-only policy as /field/edit-batch (audit P0):
        # membership arrays / identity keys must be rejected with the policy
        # reason, not incidentally via a coercion TypeError.
        _ro = _editability_reason(modifier.store, target_path)
        if _ro is not None:
            raise ValueError(_ro)
        # docs/20 r12-B: an FSP edit NEVER silently changes amplitudes — and
        # never commits before the user saw the compensation offer. Without
        # an ack the plan comes back 409; the popup then applies FSP+amps in
        # ONE /field/edit-batch (fsp_ack=comp) or FSP alone (fsp_ack=solo).
        if request.form.get("fsp_ack") not in ("comp", "solo"):
            plan = _fsp_plan_for(modifier.store, target_path, raw_value)
            if plan is not None:
                return jsonify(
                    ok=False, fsp_compensation=plan,
                    error=("This edit changes a port's full-scale power — "
                           "confirm the amplitude compensation first")), 409
        # r14 ⑩: a field stored as TEXT that reads like a number ("0.13") is
        # un-fixable through the legacy coercer (old-type-preserving — typing
        # 0.14 quietly stayed text, forever). Never silent, never blocked:
        # without an ack the offer comes back 409; type_fix=convert assigns
        # the number type at the USER layer (persisted — future edits stay
        # typed) and stores the number; type_fix=keep proceeds as text.
        _type_fix = request.form.get("type_fix", "")
        if _type_fix not in ("convert", "keep"):
            offer = _type_fix_offer(modifier.store, target_path, raw_value)
            if offer is not None:
                return jsonify(
                    ok=False, type_fix=offer,
                    error=(f"{target_path} is stored as TEXT "
                           f"({offer['current_display']}), not a "
                           f"{offer['proposed']} — convert the type to store "
                           f"the number, or keep text (wrap the value in "
                           'quotes "…" to keep text without asking)')), 409
        if _type_fix == "convert":
            offer = _type_fix_offer(modifier.store, target_path, raw_value)
            if offer is not None:
                _tp.save_assignment(
                    current_app.instance_path, ctx["path"], target_path,
                    {"type": offer["proposed"], "override_env": False,
                     "note": "converted from text via edit (r14)"})
                _attach_type_policy(ctx)
        parsed = _parse_for_target(modifier.store, target_path, raw_value)
        modifier.set_value(target_path, parsed)
        _invalidate_engine_cache(ctx)
    except _tp.TypeMismatchError as e:
        return jsonify(ok=False, error=str(e), **e.as_json()), 400
    except (KeyError, TypeError, ValueError, IndexError) as e:
        return jsonify(ok=False, error=str(e)), 400

    # Echo what was ACTUALLY committed (the coercer may have kept the old
    # type) so the client can re-render the value with honest type styling.
    try:
        committed = modifier.store.get_value(target_path)
        if isinstance(committed, float) and (
                committed != committed or committed in (float("inf"), float("-inf"))):
            raise ValueError("non-finite echo would break JSON.parse")
        return jsonify(ok=True, tray_html=_tray_html(),
                       stored=committed, stored_kind=_kind_of(committed))
    except Exception:  # noqa: BLE001 — echo is a bonus, never a failure
        return jsonify(ok=True, tray_html=_tray_html())


def _fsp_plan_for(store, target_path: str, raw_value) -> dict | None:
    """The r12-B compensation plan for an FSP target, else None (non-FSP
    paths, non-numeric values, unchanged FSP — the edit proceeds normally).
    Read-only; never raises (a plan bug must never brick port edits)."""
    if not isinstance(target_path, str) \
            or not target_path.endswith(".full_scale_power_dbm"):
        return None
    try:
        from quam_state_manager.core import mw_fem
        with store._lock:
            merged = store.merged
        return mw_fem.fsp_compensation_plan(merged, target_path, raw_value)
    except Exception:  # noqa: BLE001
        logger.debug("fsp plan computation failed", exc_info=True)
        return None


def _kind_of(v: Any) -> str:
    """Display kind of a committed value for the edit echo (r14)."""
    if v is None:
        return "null"
    if isinstance(v, bool):
        return "bool"
    if isinstance(v, int):
        return "int"
    if isinstance(v, float):
        return "real"
    if isinstance(v, str):
        return "str"
    return "list" if isinstance(v, list) else "dict"


def _type_fix_offer(store, target_path: str, raw_value: str) -> dict | None:
    """The r14 stored-as-text repair offer, else None (edit proceeds normally).

    Fires only when EVERY leg holds: the target is NOT inside an ``extras``
    block; the current value is a string that reads like a number; the typed
    input is an UNQUOTED number (explicit quotes mean "I want text" — no
    gate); and no enforced expectation covers the path (an env schema / user
    assignment already repairs the type on plain edits).

    The ``extras`` leg is what keeps this offer from blocking ordinary
    editing. ``extras`` is user-declared free form with no schema, so a
    numeric-looking string there is a LABEL, not a number that got
    string-ified — and a lab that stores ``extras.cz_branch = "02"`` could not
    change it to ``"03"`` at all without knowing the undocumented quoting
    escape hatch, because every attempt came back as a 409 offering to convert
    the label into a number. Shares one definition with the detector so the
    warning and the repair cannot disagree about what counts as text.

    Read-only; never raises."""
    try:
        from quam_state_manager.core import type_policy as _tp
        from quam_state_manager.core.edit_policy import is_free_form_path
        if is_free_form_path(target_path):
            return None
        raw = (raw_value or "").strip()
        if raw.startswith('"') or raw.startswith("'"):
            return None
        old = store.get_value(target_path)
        if not _is_numeric_string(old):
            return None
        parsed = _tp.parse_value(raw)
        if isinstance(parsed, bool) or not isinstance(parsed, (int, float)):
            return None
        policy = getattr(store, "type_policy", None)
        if policy is not None:
            expected = policy.expected_for(store.merged, target_path,
                                           infer=False)
            if expected is not None and expected.enforced:
                return None
        return {
            "path": target_path,
            "current_display": f'"{old}"',
            "proposed": "int" if isinstance(parsed, int) else "real",
        }
    except Exception:  # noqa: BLE001 — the offer must never brick edits
        logger.debug("type-fix offer computation failed", exc_info=True)
        return None


def _parse_for_target(store, target_path: str, raw_value: str):
    """Parse typed text against the resolved target's ENFORCED expectation;
    without one this is ``type_policy.parse_value`` byte-identical.

    One carve-out: a TEXT leaf inside ``extras`` keeps what the user typed,
    verbatim. Everywhere else the legacy pipeline parses ``"03"`` to the
    number 3 and the coercer casts it back to the field's original type,
    which for a string field silently stores ``"3"`` — the leading zero gone.
    For a schema-backed field that round trip is the point; for a lab's own
    label (``extras.cz_branch``) it is data loss on an ordinary edit, and
    leading zeros are exactly how labels are written. Free-form means the text
    is the value.
    """
    from quam_state_manager.core import type_policy as _tp
    from quam_state_manager.core.edit_policy import is_free_form_path
    if is_free_form_path(target_path):
        try:
            current = store.get_value(target_path)
        except Exception:  # noqa: BLE001 — fall through to the normal path
            current = None
        if isinstance(current, str) and not is_pointer(current):
            return raw_value
    policy = getattr(store, "type_policy", None)
    expected = None
    if policy is not None:
        try:
            expected = policy.expected_for(store.merged, target_path, infer=False)
        except Exception:  # noqa: BLE001 — a policy bug must never brick edits
            expected = None
    return _tp.parse_with_expected(raw_value, expected)


@bp.route("/field/peek", methods=["GET"])
def field_peek():
    """Read current values for one or more dot-paths without mutating.

    Used by the Plotly click-confirmation popup to show "previous value"
    alongside the user-editable new value. Informational endpoint: missing
    paths surface as ``null`` in ``values`` plus a per-path ``errors``
    entry; the overall response is still ``ok=true`` so partial fetches
    don't blow up the whole popup.
    """
    store = _store()
    if not store:
        return jsonify(ok=False, error="No active context"), 400

    from quam_state_manager.core.pointer_path import find_shared_by, resolve_field_target

    clean_paths = [s for s in (_normalize_dot_path((p or "").strip())
                               for p in request.args.getlist("dot_path")) if s]
    values: dict[str, Any] = {}
    errors: dict[str, str] = {}
    # `resolved` follows QUAM pointers (incl. #./ siblings the global resolver
    # leaves raw) so the popup can read/write the value actually in use and show
    # the resolution chain. `values` stays RAW for back-compat.
    resolved: dict[str, Any] = {}

    # Phase 1 — read raw values under the lock (fast dict lookups). Capture the
    # merged-dict reference here too.
    with store._lock:
        merged = store.merged
        for p in clean_paths:
            try:
                values[p] = store.get_value(p)
            except (KeyError, TypeError, ValueError, IndexError) as e:
                values[p] = None
                errors[p] = str(e)

    # Phase 2 — follow QUAM pointers OUTSIDE the lock. This is a read-only walk
    # of the merged dict; doing it here (not under the lock) means a multi-path
    # popup on a 50-qubit chip doesn't hold the store lock across every
    # resolution and stall concurrent workers. A rare concurrent edit at worst
    # yields a slightly stale resolution, which the per-path guard absorbs.
    for p in clean_paths:
        try:
            ft = resolve_field_target(merged, p)
            ft["shared_by"] = (
                find_shared_by(merged, ft["resolved_path"], input_op=_op_of_path(p))
                if ft["is_pointer"] else []
            )
            resolved[p] = ft
        except Exception:  # noqa: BLE001 — a bad path must not 500 the popup
            resolved[p] = {
                "input_path": p, "resolved_path": p, "resolved_value": values.get(p),
                "candidates": [], "chain": [], "is_pointer": False,
                "resolvable": False, "shared_by": [],
            }

    # Phase 3 — expected-type annotation per path (the type layer's ONE UI
    # read contract; /field/typeinfo deliberately does not exist). Computed at
    # the RESOLVED path — that's where enforcement fires. Absent policy /
    # unknown key → the path is simply missing from `expected`.
    expected: dict[str, Any] = {}
    policy = getattr(store, "type_policy", None)
    if policy is not None:
        for p in clean_paths:
            try:
                target = (resolved.get(p) or {}).get("resolved_path") or p
                ann = policy.annotate(merged, target, values.get(p))
                if ann:
                    expected[p] = ann
            except Exception:  # noqa: BLE001 — annotation must not 500 the popup
                continue
    return jsonify(ok=True, values=values, errors=errors, resolved=resolved,
                   expected=expected)


def _fh_fill_string(value: Any) -> str:
    """What the Use button types into the edit input — full precision, exactly
    what a user would have typed to produce this value."""
    if value is None:
        return ""
    if isinstance(value, bool):
        return "True" if value else "False"
    if isinstance(value, float):
        return repr(value)
    if isinstance(value, (list, dict)):
        return json.dumps(value, separators=(",", ":"))
    return str(value)


def _fh_display_string(value: Any) -> str:
    """Compact human form for the history row (grouped digits for numbers)."""
    if value is None:
        return "—"
    if isinstance(value, bool):
        return "True" if value else "False"
    if isinstance(value, (int, float)):
        try:
            from quam_state_manager.core.units import group_digits
            return group_digits(value)
        except Exception:  # noqa: BLE001
            return repr(value)
    s = _fh_fill_string(value)
    return s if len(s) <= 42 else s[:39] + "…"


def _uid_roots() -> list[tuple[Path, str]]:
    """Registered dataset roots as (resolved path, folder key) pairs — the
    containment table for run-uid resolution (shared by /field/history and
    /bulk/column-history).

    audit-r10: DEEPEST root first. In the nested layout
    ``<ws-root>/<chip>/<date>/#N_run`` both the workspace root and the chip
    dir are candidates; the shallow root would win first-match containment
    and mint a uid whose DatasetStore holds zero runs (dead Data links —
    runs are keyed by their date-dir's parent)."""
    roots: list[tuple[Path, str]] = []
    for cand in _dataset_candidate_folders(fast=True):
        try:
            roots.append((Path(cand).resolve(), _folder_key(cand)))
        except OSError:
            continue
    roots.sort(key=lambda t: len(t[0].parts), reverse=True)
    return roots


def _uid_for_run_ref(fp: Any, rid: Any, roots: list[tuple[Path, str]]) -> str | None:
    """Resolve a history row's (experiment_folder_path, run_id) to a dataset
    uid via root containment — None when unresolvable (no Data link)."""
    if not fp or rid is None:
        return None
    try:
        rp = Path(fp).resolve()
    except OSError:
        return None
    for root, key in roots:
        try:
            if rp.is_relative_to(root):
                return f"{key}:{int(rid)}"
        except (OSError, ValueError):
            continue
    return None


# Immutable-run caches for the field-history runs tier (docs/20 v2 Step 6).
# Runs are write-once after completion, so per-folder identity and per-
# (folder, dot_path) values never go stale; both are size-bounded.
_RUN_IDENT_CACHE: "OrderedDict[str, tuple]" = OrderedDict()
# audit-r10: run-fact caches hold ONLY chip-INDEPENDENT immutable facts —
# the run's own declared name + entity sets, and per-path extraction results.
# The include/exclude VERDICT is chip-relative and is re-derived on every
# call from these facts + the CURRENTLY loaded chip; caching the verdict
# (the pre-audit shape) leaked chip A's values into chip B's popover after
# a chip switch (Use buttons and all) and suppressed B's own runs.
_RUN_CHIP_CACHE: "OrderedDict[str, tuple]" = OrderedDict()
_RUN_VALUE_CACHE: "OrderedDict[tuple[str, str], tuple]" = OrderedDict()
_RUN_CACHE_MAX = 4096


def _trim_run_caches() -> None:
    for cache in (_RUN_IDENT_CACHE, _RUN_CHIP_CACHE, _RUN_VALUE_CACHE):
        while len(cache) > _RUN_CACHE_MAX:
            cache.popitem(last=False)


def _run_ts_stamp(run: Any) -> str:
    """RunInfo → the ingested-snapshot timestamp format
    (``YYYYMMDD_HHMMSS_NNN``, ``_entry_timestamp`` parity — the dedup key
    against already-ingested snapshot rows)."""
    folder = Path(getattr(run, "folder_path", "") or "")
    m = re.search(r"_(\d{6})$", folder.name)
    hhmmss = m.group(1) if m else "000000"
    date = folder.parent.name.replace("-", "")
    if not re.match(r"^\d{8}$", date):
        date = "19700101"
    rid = getattr(run, "run_id", 0) or 0
    return f"{date}_{hhmmss}_{rid % 1000:03d}"


def _runs_field_series(ctx: dict, dot_path: str, *,
                       max_runs: int = 60) -> tuple[list[tuple], int]:
    """Direct-scan tier over the workspace runs' own quam_state copies.

    Every run folder IS a timestamped state snapshot with perfect run
    linkage — so the field timeline stays fresh (today's runs included)
    independent of Param History ingestion. Chip attribution per run:
    extras chip names when BOTH sides declare one (definitive), else
    hardware-fingerprint alignment; the wiring.json network is a cheap
    pre-gate so foreign chips usually cost one small read. Newest
    ``max_runs`` runs are examined (honest cap); roots declared in
    ``extras.data_folder`` scan first. Returns ``(series, examined)`` with
    series rows ``(ts, value, "experiment", run_id, experiment, folder)``.
    """
    from quam_state_manager.core.history import (
        ALIGN_ALIGNED, ChipFingerprint, _normalised_network, _walk_any_path,
        align, extras_chip_name, fingerprint_from_dicts)
    from quam_state_manager.core.pointer_resolver import (
        is_pointer, is_self_ref, resolve_pointer)

    store = ctx.get("store")
    if store is None:
        return [], 0
    with store._lock:
        loaded_name = extras_chip_name(store.state)
        loaded_fp = fingerprint_from_dicts(store.state, store.wiring)

    roots: list[Path] = []
    seen_roots: set[str] = set()
    for raw in (ctx.get("extras_data_roots") or []):
        p = Path(raw)
        k = str(p)
        if k not in seen_roots:
            seen_roots.add(k)
            roots.append(p)
    for cand in _dataset_candidate_folders(fast=True):
        k = str(cand)
        if k not in seen_roots:
            seen_roots.add(k)
            roots.append(Path(cand))

    candidates: list[tuple[str, Any]] = []      # (ts, run)
    for root in roots:
        st = _get_or_create_store(root, rescan=True)
        if st is None:
            continue
        for run in st.runs.values():
            candidates.append((_run_ts_stamp(run), run))
    candidates.sort(key=lambda t: t[0], reverse=True)

    series: list[tuple] = []
    examined = 0
    segs = dot_path.split(".")
    for ts, run in candidates:
        if examined >= max_runs:
            break
        folder = Path(getattr(run, "folder_path", "") or "")
        qs = folder / "quam_state"
        fkey = str(folder)
        chip = _RUN_CHIP_CACHE.get(fkey)
        # A cached chip-fact entry proves the run HAD quam_state (runs are
        # write-once) — skip the stat. examined counts uniformly (cache hits
        # included), so the newest-N window has one meaning warm or cold.
        if chip is None and not (qs / "state.json").exists():
            continue
        examined += 1

        run_net = _RUN_IDENT_CACHE.get(fkey)
        wiring: Any = None
        if run_net is None:
            try:
                wiring = safe_io.read_json(qs / "wiring.json")
            except (OSError, ValueError):
                wiring = {}
            run_net = _normalised_network(
                (wiring if isinstance(wiring, dict) else {}).get("network"))
            _RUN_IDENT_CACHE[fkey] = run_net
        # Cheap network pre-gate: a foreign chip usually stops here — but
        # ONLY when the loaded chip is unnamed. A declared chip name is
        # DEFINITIVE across a host move (the whole point of tier 1), so a
        # named chip must read the run's state to compare names first.
        if (not loaded_name and loaded_fp.network and run_net
                and run_net != loaded_fp.network):
            continue
        state: Any = None
        if chip is None:
            try:
                state = safe_io.read_json(qs / "state.json")
            except (OSError, ValueError):
                continue
            if not isinstance(state, dict):
                continue
            chip = (extras_chip_name(state),
                    frozenset((state.get("qubits") or {}).keys()),
                    frozenset((state.get("qubit_pairs") or {}).keys()))
            _RUN_CHIP_CACHE[fkey] = chip
        run_name, run_qubits, run_pairs = chip
        # Identity gate — re-derived EVERY call against the CURRENT chip
        # (never cached: the verdict is chip-relative).
        if loaded_name and run_name:
            included = (run_name == loaded_name)
        else:
            run_fp = ChipFingerprint(
                network=run_net, qubits=run_qubits, pairs=run_pairs)
            included = align(loaded_fp, run_fp) == ALIGN_ALIGNED
        if not included:
            continue

        vkey = (fkey, dot_path)
        cached_val = _RUN_VALUE_CACHE.get(vkey)
        if cached_val is not None:
            value = cached_val[1]
        else:
            if state is None:
                try:
                    state = safe_io.read_json(qs / "state.json")
                except (OSError, ValueError):
                    continue
                if not isinstance(state, dict):
                    continue
            merged = dict(state)
            if segs and segs[0] not in merged:
                if wiring is None:
                    try:
                        wiring = safe_io.read_json(qs / "wiring.json")
                    except (OSError, ValueError):
                        wiring = {}
                if isinstance(wiring, dict):
                    merged.update(wiring)
            found, value = _walk_any_path(merged, segs)
            if not found:
                value = None
            elif is_pointer(value) and not is_self_ref(value):
                value = resolve_pointer(merged, value, tuple(segs))
            _RUN_VALUE_CACHE[vkey] = (found, value)
            _trim_run_caches()
        series.append((ts, value, "experiment",
                       getattr(run, "run_id", None),
                       getattr(run, "experiment_name", None), fkey))
    return series, examined


def _runs_column_series(ctx: dict, path_map: dict[str, str], *,
                        max_runs: int = 6, max_examine: int = 40,
                        ) -> tuple[list[dict], int]:
    """Vectorized runs tier for a grid COLUMN (docs/20 v2 Column History).

    Same chip gates as :func:`_runs_field_series` (shared extras names are
    definitive even across a host move — the network pre-gate only
    short-circuits for unnamed loaded chips; else fingerprint alignment),
    but each matching run's state.json is parsed ONCE and every row's value
    extracted from it. Returns ``(runs newest-first, examined)`` where each
    run is ``{run_id, experiment, ts, when, uid, values: {row_id: value}}``
    — ``uid`` is direct (folder under its registered root), so the panel's
    run links are always valid. Caps at ``max_runs`` MATCHING runs.
    """
    from quam_state_manager.core.history import (
        ALIGN_ALIGNED, ChipFingerprint, _normalised_network, _walk_any_path,
        align, extras_chip_name, fingerprint_from_dicts)
    from quam_state_manager.core.pointer_resolver import (
        is_pointer, is_self_ref, resolve_pointer)

    store = ctx.get("store")
    if store is None or not path_map:
        return [], 0
    with store._lock:
        loaded_name = extras_chip_name(store.state)
        loaded_fp = fingerprint_from_dicts(store.state, store.wiring)

    roots: list[tuple[Path, str]] = []
    seen_roots: set[str] = set()
    for raw in (ctx.get("extras_data_roots") or []):
        k = str(Path(raw))
        if k not in seen_roots:
            seen_roots.add(k)
            roots.append((Path(raw), _folder_key(raw)))
    for cand in _dataset_candidate_folders(fast=True):
        k = str(cand)
        if k not in seen_roots:
            seen_roots.add(k)
            roots.append((Path(cand), _folder_key(cand)))

    candidates: list[tuple[str, Any, str]] = []      # (ts, run, root_key)
    for root, rkey in roots:
        st = _get_or_create_store(root, rescan=True)
        if st is None:
            continue
        for run in st.runs.values():
            candidates.append((_run_ts_stamp(run), run, rkey))
    candidates.sort(key=lambda t: t[0], reverse=True)

    segs_by_row = {row: dp.split(".") for row, dp in path_map.items()}
    out: list[dict] = []
    examined = 0
    for ts, run, rkey in candidates:
        if len(out) >= max_runs or examined >= max_examine:
            break
        folder = Path(getattr(run, "folder_path", "") or "")
        qs = folder / "quam_state"
        fkey = str(folder)
        chip = _RUN_CHIP_CACHE.get(fkey)
        if chip is None and not (qs / "state.json").exists():
            continue
        examined += 1
        run_net = _RUN_IDENT_CACHE.get(fkey)
        wiring: Any = None
        if run_net is None:
            try:
                wiring = safe_io.read_json(qs / "wiring.json")
            except (OSError, ValueError):
                wiring = {}
            run_net = _normalised_network(
                (wiring if isinstance(wiring, dict) else {}).get("network"))
            _RUN_IDENT_CACHE[fkey] = run_net
            _trim_run_caches()
        if (not loaded_name and loaded_fp.network and run_net
                and run_net != loaded_fp.network):
            continue
        # Gate from cached chip facts when possible — a known-foreign run
        # skips the state parse entirely (verdict itself is never cached).
        state: Any = None
        if chip is None:
            try:
                state = safe_io.read_json(qs / "state.json")
            except (OSError, ValueError):
                continue
            if not isinstance(state, dict):
                continue
            chip = (extras_chip_name(state),
                    frozenset((state.get("qubits") or {}).keys()),
                    frozenset((state.get("qubit_pairs") or {}).keys()))
            _RUN_CHIP_CACHE[fkey] = chip
            _trim_run_caches()
        run_name, run_qubits, run_pairs = chip
        if loaded_name and run_name:
            if run_name != loaded_name:
                continue
        else:
            run_fp = ChipFingerprint(
                network=run_net, qubits=run_qubits, pairs=run_pairs)
            if align(loaded_fp, run_fp) != ALIGN_ALIGNED:
                continue
        if state is None:
            try:
                state = safe_io.read_json(qs / "state.json")
            except (OSError, ValueError):
                continue
            if not isinstance(state, dict):
                continue

        merged = dict(state)
        if any(segs and segs[0] not in merged
               for segs in segs_by_row.values()):
            if wiring is None:
                try:
                    wiring = safe_io.read_json(qs / "wiring.json")
                except (OSError, ValueError):
                    wiring = {}
            if isinstance(wiring, dict):
                merged.update(wiring)
        values: dict[str, Any] = {}
        for row, segs in segs_by_row.items():
            found, value = _walk_any_path(merged, segs)
            if not found:
                value = None
            elif is_pointer(value) and not is_self_ref(value):
                value = resolve_pointer(merged, value, tuple(segs))
            values[row] = value
        rid = getattr(run, "run_id", None)
        out.append({
            "run_id": rid,
            "experiment": getattr(run, "experiment_name", None),
            "ts": ts,
            "when": (f"{ts[4:6]}-{ts[6:8]} {ts[9:11]}:{ts[11:13]}"
                     if len(ts) >= 13 else ts),
            "uid": f"{rkey}:{int(rid)}" if rid is not None else None,
            "folder": fkey,
            "values": values,
        })
    return out, examined


CH_MAX_CHIPS = 6       # per-row change-point chips shown in the Changes tab
CH_BYRUN_COLS = 6      # run columns displayed in the By-run tab
# The Changes series merges MORE runs than the By-run tab displays: a value
# introduced by a run just outside the 6-column window would otherwise lose
# its attribution to a later auto snapshot. Window = the cell popover's
# newest-60 scan EXACTLY (live-verified: one busy day of newer runs pushed
# the introducers out of a 24-run window while the cell popover still
# attributed them). One parse per run serves every row, so the cost equals
# one cell-popover open.
CH_SERIES_RUNS = 60
CH_SERIES_EXAMINE = 60


@bp.route("/bulk/column-history", methods=["POST"])
def bulk_column_history():
    """Column History panel (docs/20 v2 + the r9 Changes amendment).

    Body (form): ``grid`` (qubit|pair), ``label``, ``unit``,
    ``paths`` = JSON ``{row_id: dot_path}`` — collected client-side from the
    rendered cells (both grids symmetric; paths feed READ-ONLY extraction
    only). One response renders BOTH tabs: **Changes** (default — per-row
    change-point chips over the merged snapshot+runs series, so manual
    applied edits (trigger "save") appear alongside run-set values, each chip
    clickable to fill the grid cell and hover-revealing its run's Data link)
    and **By run** (the original rows × last-N-matching-runs table with the
    per-run 'Use all'). Staging stays user-explicit in both.
    """
    ctx = _active_ctx()
    store = ctx.get("store") if ctx else None
    if not ctx or ctx.get("type") != "quam" or store is None:
        return render_template("_status.html", message="No state loaded",
                               level="warning"), 400
    label = (request.form.get("label") or "").strip() or "column"
    unit = (request.form.get("unit") or "").strip()
    col_key = (request.form.get("col_key") or "").strip()
    grid = (request.form.get("grid") or "qubit").strip()
    try:
        path_map_raw = json.loads(request.form.get("paths") or "{}")
    except ValueError:
        return render_template("_status.html", message="bad paths payload",
                               level="error"), 400
    path_map: dict[str, str] = {
        str(k): _normalize_dot_path(str(v).strip())
        for k, v in path_map_raw.items()
        if isinstance(k, str) and isinstance(v, str) and str(v).strip()
    }
    if not path_map or len(path_map) > 200:
        return render_template("_status.html", message="no usable paths",
                               level="error"), 400

    hm = _history()
    snap_series = hm.column_history(ctx["path"], path_map)
    try:
        runs_all, examined = _runs_column_series(
            ctx, path_map, max_runs=CH_SERIES_RUNS,
            max_examine=CH_SERIES_EXAMINE)
    except Exception:  # noqa: BLE001 — the panel must survive a bad root
        logger.debug("column-history runs tier failed", exc_info=True)
        runs_all, examined = [], 0
    runs = runs_all[:CH_BYRUN_COLS]     # By-run tab shows the newest few

    from quam_state_manager.core.pointer_path import resolve_field_target

    def _num_or_none(v):
        if isinstance(v, bool) or not isinstance(v, (int, float)):
            return None
        f = float(v)
        return f if f == f and f not in (float("inf"), float("-inf")) else None

    rows_out: list[dict[str, Any]] = []
    uid_roots = _uid_roots()
    with store._lock:
        merged_now = store.merged
    for row_id in sorted(path_map):
        dp = path_map[row_id]
        current = None
        editable = True
        try:
            ft = resolve_field_target(merged_now, dp)
            if ft.get("resolvable"):
                current = ft.get("resolved_value")
        except Exception:  # noqa: BLE001
            editable = False
        # Merged series: snapshot tiers + run values, time-merged, then
        # change-point collapsed (field_history's NaN-safe key rule). The
        # snapshot-rows-first build + STABLE sort on ts alone reproduces
        # field_history's (ts, rank) ordering — an ingested run's snapshot
        # row wins over the direct run row at equal ts, so equal values
        # dedup in the collapse. Do not change the build order or sort key
        # (a full-tuple sort would also crash on None<str at slot 4/5).
        # 7th slot = the run tier's ready uid (snapshot rows resolve via
        # folder containment below). The series merges runs_all (wider than
        # the By-run columns) so attribution matches the cell popover.
        series = [t + (None,) for t in (snap_series.get(row_id) or [])]
        for r in reversed(runs_all):                 # oldest-first append
            series.append((r["ts"], r["values"].get(row_id), "experiment",
                           r["run_id"], r["experiment"], r["folder"],
                           r["uid"]))
        series.sort(key=lambda t: t[0])

        def _key(v):
            if isinstance(v, float) and v != v:
                return "\x00nan"
            return v

        collapsed: list[dict] = []
        prev: Any = object()
        for ts, value, trigger, rid, exp, folder, run_uid in series:
            if _key(value) != prev:
                collapsed.append({"value": value, "trigger": trigger or "auto",
                                  "ts": ts, "run_id": rid, "experiment": exp,
                                  "folder": folder, "uid": run_uid})
            prev = _key(value)
        spark_vals = [{"value": p["value"], "trigger": p["trigger"]}
                      for p in collapsed
                      if _num_or_none(p["value"]) is not None]
        svg = ""
        if len(spark_vals) >= 2:
            try:
                svg = hm.render_sparkline_svg_inner(
                    spark_vals, current=_num_or_none(current))
            except Exception:  # noqa: BLE001
                svg = ""
        # 'changed' marks a run whose value differs from the NEXT-OLDER run
        # column (the moment this run introduced a new value).
        cells = []
        for i, r in enumerate(runs):                  # newest-first columns
            v = r["values"].get(row_id)
            older = (runs[i + 1]["values"].get(row_id)
                     if i + 1 < len(runs) else None)
            cells.append({
                "display": _fh_display_string(v),
                "fill": _fh_fill_string(v),
                "has": v is not None,
                "changed": i + 1 < len(runs) and v != older,
            })
        # Changes tab: this row's OWN change points, newest first. The oldest
        # known value stays (a never-changed row still shows "this value
        # since {when}"); deeper history falls off the cap naturally.
        chips = []
        for p in list(reversed(collapsed))[:CH_MAX_CHIPS]:
            ts = p["ts"]
            chips.append({
                "display": _fh_display_string(p["value"]),
                "fill": _fh_fill_string(p["value"]),
                # RAW value (not the display string) so the Δ chip reports the
                # stored type honestly — docs/76.
                "raw": p["value"],
                "has": p["value"] is not None,
                "when": (f"{ts[4:6]}-{ts[6:8]} {ts[9:11]}:{ts[11:13]}"
                         if len(ts) >= 13 else ts),
                "trigger": p["trigger"],
                "run_id": p["run_id"],
                "experiment": p["experiment"],
                "uid": p["uid"] or _uid_for_run_ref(p["folder"], p["run_id"],
                                                    uid_roots),
            })
        if chips:
            v0 = collapsed[-1]["value"]
            chips[0]["is_current"] = (current is not None and v0 == current
                                      and isinstance(v0, type(current)))
        rows_out.append({
            "id": row_id,
            "dot_path": dp,
            "svg": svg,
            "current": _fh_display_string(current),
            "current_fill": _fh_fill_string(current),
            "editable": editable,
            "cells": cells,
            "chips": chips,
        })

    return render_template(
        "_column_history.html", label=label, unit=unit, grid=grid,
        col_key=col_key, rows=rows_out, runs=runs, examined=examined,
        matched=len(runs_all), chip_cap=CH_MAX_CHIPS)


@bp.route("/field/history", methods=["GET"])
def field_history():
    """Per-field value timeline popover (the 🕘 button on Live-Edit cells and
    inspector rows). ``?path=<dot_path>`` → the ``_field_history.html`` panel:
    value CHANGE points from Param History (SQLite index tier for tracked
    props, capped snapshot scan for any other leaf), each row naming the
    experiment/trigger that introduced the value, with a Use button (fills the
    edit input — commit stays user-explicit) and, when the run folder sits
    under a registered dataset root, a Data button that loads the run's detail
    into #inspector-pane so value and data sit side by side."""
    ctx = _active_ctx()
    store = ctx.get("store") if ctx else None
    if not ctx or ctx.get("type") != "quam" or store is None:
        return render_template("_status.html", message="No state loaded",
                               level="warning"), 400
    dot_path = _normalize_dot_path((request.args.get("path") or "").strip())
    if not dot_path:
        return render_template("_status.html", message="path required",
                               level="error"), 400
    # Runs tier (docs/20 v2): the workspace runs' own quam_state copies keep
    # the timeline fresh independent of Param History ingestion — today's
    # runs appear with a guaranteed Data link.
    try:
        runs_series, _examined = _runs_field_series(ctx, dot_path)
    except Exception:  # noqa: BLE001 — the popover must survive a bad root
        logger.debug("field-history runs tier failed", exc_info=True)
        runs_series = []
    hist = _history().field_history(ctx["path"], dot_path,
                                    extra_series=runs_series)

    from quam_state_manager.core.pointer_path import resolve_field_target
    current = None
    try:
        ft = resolve_field_target(store.merged, dot_path)
        if ft.get("resolvable"):
            current = ft.get("resolved_value")
    except Exception:  # noqa: BLE001
        pass

    roots = _uid_roots()
    for pt in hist["points"]:
        value = pt["value"]
        pt["fill"] = _fh_fill_string(value)
        pt["display"] = _fh_display_string(value)
        pt["is_current"] = (current is not None and value == current
                            and isinstance(value, type(current)))
        ts = pt.get("timestamp") or ""
        pt["when"] = (f"{ts[0:4]}-{ts[4:6]}-{ts[6:8]} {ts[9:11]}:{ts[11:13]}"
                      if len(ts) >= 13 else ts)
        pt["uid"] = _uid_for_run_ref(pt.get("experiment_folder_path"),
                                     pt.get("run_id"), roots)
    # Mini trend chart payload (docs/20 v2 Step 7): the change points as a
    # step series, finite numerics only (a text/list field simply gets no
    # chart). Oldest-first for plotting.
    chart: list[dict[str, Any]] = []
    for pt in reversed(hist["points"]):
        v = pt["value"]
        if isinstance(v, bool) or not isinstance(v, (int, float)):
            continue
        f = float(v)
        if f != f or f in (float("inf"), float("-inf")):
            continue
        ts = pt.get("timestamp") or ""
        iso = (f"{ts[0:4]}-{ts[4:6]}-{ts[6:8]} {ts[9:11]}:{ts[11:13]}:{ts[13:15]}"
               if len(ts) >= 15 else pt.get("when") or "")
        chart.append({"t": iso, "v": f, "trigger": pt.get("trigger") or "auto"})
    return render_template("_field_history.html", hist=hist,
                           current_display=_fh_display_string(current),
                           chart=chart)


def _editability_reason(store: QuamStore, target_path: str) -> str | None:
    """Durable read-only safety policy. Canonical impl is
    core.edit_policy.editability_reason — SHARED with the CLI (which used to
    overwrite identity keys / membership arrays straight to live)."""
    from quam_state_manager.core.edit_policy import editability_reason
    return editability_reason(store, target_path)


@bp.route("/field/type-assignments", methods=["GET"])
def field_type_assignments():
    """The chip's user type assignments + whether the env manifest is warm."""
    store = _store()
    if not store:
        return jsonify(ok=False, error="No active context"), 400
    policy = getattr(store, "type_policy", None)
    assignments = dict(policy.assignments) if policy else {}
    return jsonify(ok=True, assignments=assignments, count=len(assignments),
                   manifest_loaded=bool(policy and policy.manifest))


@bp.route("/field/type-assign", methods=["POST"])
def field_type_assign():
    """Assign a key's expected type (the user layer).

    Env-conflict gate: when the env schema already types this key and
    ``override_env`` is not set, respond 409 with the env's expectation — the
    UI confirms and re-POSTs with ``override_env=1`` (independent-gates rule:
    one click never silently outranks the schema). A current value already
    violating the new type is a WARNING, not a block — the assignment IS the
    repair path."""
    from quam_state_manager.core import state_env_validate, type_policy as _tp
    ctx = _active_ctx()
    store = ctx.get("store") if ctx else None
    if not store or not ctx.get("path"):
        return jsonify(ok=False, error="No active context"), 400
    guard = _chip_mismatch_response(
        request.form.get("expect_chip", ""),
        request.form.get("force_chip") in ("1", "true", "True"))
    if guard is not None:
        return guard

    dot_path = _normalize_dot_path(request.form.get("dot_path", "").strip())
    type_expr = request.form.get("type", "").strip()
    override_env = request.form.get("override_env") in ("1", "true", "True")
    if not dot_path or not type_expr:
        return jsonify(ok=False, error="dot_path and type required"), 400

    policy = getattr(store, "type_policy", None)
    if policy is None:
        _attach_type_policy(ctx)
        policy = getattr(store, "type_policy", None)
    if policy is not None and not override_env:
        env_exp = policy._env_expected(store.merged, dot_path)
        if env_exp is not None:
            return jsonify(ok=False, error_kind="env_conflict",
                           error=("the env schema already types this key as "
                                  f"{_tp.format_type(env_exp.spec)} — confirm to "
                                  "override it"),
                           env_type=env_exp.as_json()), 409

    try:
        record = _tp.save_assignment(
            current_app.instance_path, ctx["path"], dot_path,
            {"type": type_expr, "override_env": override_env,
             "note": request.form.get("note", "")})
    except ValueError as e:
        return jsonify(ok=False, error=str(e)), 400

    _attach_type_policy(ctx)                    # rebuild with the new assignment
    policy = getattr(store, "type_policy", None)

    warning = None
    try:
        current = store.get_value(dot_path)
        if (current is not None
                and not state_env_validate.is_pointer_str(current)):
            ok_now, _, msg = state_env_validate.judge(
                current, _tp.parse_type(type_expr))
            if not ok_now:
                warning = (f"the CURRENT value already violates this type "
                           f"({msg}) — edit it to a conforming value")
    except (KeyError, TypeError, ValueError, IndexError):
        pass

    effective = policy.annotate(store.merged, dot_path) if policy else None
    return jsonify(ok=True, assigned=record, expected=effective, warning=warning)


def _env_manifest_and_versions(store) -> tuple[dict | None, dict]:
    """The PRISTINE env manifest (never the verdict overlay) + its versions."""
    policy = getattr(store, "type_policy", None)
    manifest = getattr(policy, "env_manifest", None) or getattr(policy, "manifest", None)
    return manifest, ((manifest or {}).get("versions") or {})


@bp.route("/env-schema/changes")
def env_schema_changes():
    """What the ENVIRONMENT's schema changed since the last one SM recorded.

    This is the half of the type story SM could not tell before: a type
    mismatch may be the chip's fault OR the library's, and only a retained
    baseline can distinguish them. With no prior baseline the answer is honest
    ("this run recorded the first one"), never an invented diff.
    """
    from quam_state_manager.core import state_env_baseline as _seb
    from quam_state_manager.core import type_verdicts as _tv
    ctx = _active_ctx()
    store = (ctx or {}).get("store")
    if store is None:
        return render_template("_status.html", message="No state loaded",
                               level="warning")
    manifest, _versions = _env_manifest_and_versions(store)
    transition = _seb.env_transition(current_app.instance_path, manifest)
    resolved = {}
    if manifest:
        resolved = _tv.resolve_for_manifest(current_app.instance_path, manifest)
    return render_template("_env_schema_changes.html", transition=transition,
                           verdicts=resolved,
                           rows=_env_change_rows(transition, resolved))


def _env_change_rows(transition: dict | None, resolved: dict) -> list[dict]:
    """Diff rows joined with any verdict already recorded for that field, so a
    question the user has answered renders as answered."""
    rows = []
    for r in ((transition or {}).get("diff") or {}).get("rows") or []:
        cls, field = r.get("class") or "", r.get("field") or ""
        v = resolved.get(f"{cls}.{field}") if field else None
        rows.append({
            **r,
            "leaf": cls.rsplit(".", 1)[-1],
            "verdict": v,
            "answered": bool(v and v.get("status") in ("exact", "carried")),
            "needs_reaffirm": bool(v and v.get("status") == "needs_reaffirm"),
        })
    return rows


@bp.route("/env-schema/verdicts")
def env_schema_verdicts():
    """List / manage what the user has taught SM about this environment."""
    from quam_state_manager.core import type_verdicts as _tv
    ctx = _active_ctx()
    store = (ctx or {}).get("store")
    manifest, versions = _env_manifest_and_versions(store) if store else (None, {})
    resolved = (_tv.resolve_for_manifest(current_app.instance_path, manifest)
                if manifest else {})
    items = []
    for key, v in sorted(resolved.items()):
        conflicts = []
        if store is not None and v.get("spec"):
            with store._lock:
                conflicts = _tv.conflicting_leaves(
                    store.state, manifest, v["class_path"], v["field"], v["spec"])
        items.append({**v, "key": key, "conflicts": len(conflicts),
                      "leaf": (v.get("class_path") or "").rsplit(".", 1)[-1]})
    if request.args.get("format") == "json":
        return jsonify(ok=True, count=len(items),
                       env=_seb_label(versions), verdicts=items)
    return render_template("_env_schema_verdicts.html", items=items,
                           env_label=_seb_label(versions))


def _seb_label(versions: dict) -> str:
    from quam_state_manager.core import state_env_baseline as _seb
    return _seb.env_label(versions)


@bp.route("/env-schema/verdict", methods=["POST"])
def env_schema_verdict():
    """Record what the user knows that SM does not: in THIS environment, that
    class·field's type is X.

    Scope is env + class·field on purpose — "the library changed in this
    version" is a fact about the library, not about one chip, so it must not
    have to be re-taught per chip. The blast radius is disclosed (how many
    values on the CURRENT chip the type would reject) but never blocks: the
    verdict may itself be the repair path, exactly like a per-key assignment.
    """
    from quam_state_manager.core import type_policy as _tp
    from quam_state_manager.core import type_verdicts as _tv
    ctx = _active_ctx()
    store = (ctx or {}).get("store")
    if store is None:
        return jsonify(ok=False, error="No state loaded"), 400
    manifest, versions = _env_manifest_and_versions(store)
    if not manifest:
        return jsonify(ok=False, error_kind="cold_env",
                       error=("the environment's schema has not been probed yet "
                              "— probe it on Diagnostics first")), 409

    class_path = (request.form.get("class_path") or "").strip()
    field = (request.form.get("field") or "").strip()
    decision = (request.form.get("decision") or "").strip()
    use = (request.form.get("use") or "env").strip()
    if not class_path or not field or decision not in ("accept", "override"):
        return jsonify(ok=False, error="class_path, field and decision required"), 400

    env_spec, reason = _tv.env_field_spec(manifest, class_path, field)
    if reason == "unknown_field":
        return jsonify(
            ok=False, error_kind="unknown_field",
            error=(f"this environment's {class_path.rsplit('.', 1)[-1]} has no "
                   f"field '{field}' at all. That breaks Quam.load() outright — "
                   "it is not a disagreement about types, so teaching SM cannot "
                   "fix it. Select the environment this chip was written by, or "
                   "migrate the state.")), 409
    if reason:
        return jsonify(ok=False, error_kind=reason,
                       error=("SM cannot introspect that class in this "
                              "environment, so there is nothing to teach")), 409

    spec = None
    type_expr = None
    spec_source = use
    if decision == "override":
        if use == "grammar":
            type_expr = (request.form.get("type") or "").strip()
            try:
                spec = _tp.parse_type(type_expr)
            except ValueError as exc:
                return jsonify(ok=False, error=str(exc)), 400
        elif use == "baseline":
            from quam_state_manager.core import state_env_baseline as _seb
            key = (request.form.get("baseline_key") or "").strip()
            body = _seb.load_baseline(current_app.instance_path, key)
            entry = ((body or {}).get("classes") or {}).get(class_path) or {}
            f = ((entry.get("fields") or {}) or {}).get(field) or {}
            spec = f.get("t")
            if not spec:
                return jsonify(ok=False, error_kind="no_baseline_spec",
                               error=("that recorded environment has no type for "
                                      "this field")), 409
        else:
            spec = env_spec
        if not spec:
            return jsonify(ok=False, error="no type to record"), 400

    record = _tv.save_verdict(
        current_app.instance_path, versions, class_path, field,
        decision=decision, spec=spec, type_expr=type_expr,
        spec_source=spec_source, env_spec=env_spec,
        note=request.form.get("note", ""))
    _attach_type_policy(ctx)                 # the overlay takes effect now
    ctx.pop("_type_alarm_memo", None)        # findings change with the overlay

    conflicts = []
    if spec:
        with store._lock:
            conflicts = _tv.conflicting_leaves(store.state, manifest,
                                               class_path, field, spec)
    return jsonify(ok=True, verdict=record, conflicts=conflicts,
                   affected=len(conflicts),
                   warning=((f"{len(conflicts)} value(s) on THIS chip do not "
                             "match that type — they are now flagged until you "
                             "edit them") if conflicts else None))


@bp.route("/env-schema/verdict/revoke", methods=["POST"])
def env_schema_verdict_revoke():
    """Take back something the user taught SM (never destructive to data)."""
    from quam_state_manager.core import type_verdicts as _tv
    ctx = _active_ctx()
    key = (request.form.get("env_key") or "").strip()
    class_path = (request.form.get("class_path") or "").strip()
    field = (request.form.get("field") or "").strip()
    removed = _tv.revoke_verdict(current_app.instance_path, key, class_path, field)
    if ctx is not None:
        _attach_type_policy(ctx)
        ctx.pop("_type_alarm_memo", None)
    return jsonify(ok=True, removed=removed)


@bp.route("/env-schema/dismiss", methods=["POST"])
def env_schema_dismiss():
    """Memo THIS env transition as seen. Env-scope fact, so it lives with the
    baselines rather than in the chip-keyed prompt memo — and it is delta-gated:
    a further schema change raises it again."""
    from quam_state_manager.core import state_env_baseline as _seb
    _seb.dismiss_transition(current_app.instance_path,
                            (request.form.get("from_key") or "").strip(),
                            (request.form.get("to_key") or "").strip(),
                            (request.form.get("sig") or "").strip())
    return ""


@bp.route("/field/type-unassign", methods=["POST"])
def field_type_unassign():
    """Remove a user type assignment; returns the now-effective expectation
    (env schema or inference)."""
    from quam_state_manager.core import type_policy as _tp
    ctx = _active_ctx()
    store = ctx.get("store") if ctx else None
    if not store or not ctx.get("path"):
        return jsonify(ok=False, error="No active context"), 400
    dot_path = _normalize_dot_path(request.form.get("dot_path", "").strip())
    if not dot_path:
        return jsonify(ok=False, error="dot_path required"), 400
    removed = _tp.delete_assignment(current_app.instance_path, ctx["path"], dot_path)
    _attach_type_policy(ctx)
    policy = getattr(store, "type_policy", None)
    effective = None
    if policy is not None:
        try:
            current = store.get_value(dot_path)
        except (KeyError, TypeError, ValueError, IndexError):
            current = None
        effective = policy.annotate(store.merged, dot_path, current)
    return jsonify(ok=True, removed=removed, expected=effective)


def _crud_policy_reason(store, dot_path: str, *, deleting: bool = False) -> str | None:
    """Create/delete guard sharing the durable read-only vocabulary: membership
    tops, identity keys, and list parents (create/delete-on-list needs element
    insert semantics — edit the whole array instead)."""
    from quam_state_manager.core.edit_policy import _container_at
    from quam_state_manager.core.leaf_classify import MEMBERSHIP_TOPS, SKIP_LEAVES
    segs = dot_path.split(".")
    if not segs or not all(segs):
        return "invalid path"
    if segs[0] in MEMBERSHIP_TOPS:
        return "chip-membership array — edit via the chip add/remove controls, not here"
    if segs[-1] in SKIP_LEAVES:
        return "identity / type key — read-only"
    if len(segs) >= 2 and isinstance(_container_at(store.merged, segs[:-1]), list):
        return ("list element — edit the whole array instead "
                "(element insert/remove is not supported)")
    return None


def _count_refs_into(store, dot_path: str) -> tuple[int, list[dict]]:
    """Pointer leaves elsewhere that resolve AT or UNDER *dot_path*.

    One walk over the merged tree's string leaves; absolute + relative
    pointers translate via ``pointer_to_abs``. Unresolvable pointers are
    skipped (they can't be pointing here provably). Capped list of examples.
    """
    from quam_state_manager.core.pointer_path import pointer_to_abs
    from quam_state_manager.core.pointer_resolver import is_pointer
    target = dot_path.split(".")
    refs: list[dict] = []
    count = 0

    def walk(node, segs):
        nonlocal count
        if isinstance(node, dict):
            for k, v in node.items():
                walk(v, segs + [str(k)])
        elif isinstance(node, list):
            for i, v in enumerate(node):
                walk(v, segs + [str(i)])
        elif isinstance(node, str) and is_pointer(node):
            if segs[:len(target)] == target:
                return                       # pointers INSIDE the subtree don't count
            abs_segs = pointer_to_abs(node, segs)
            if abs_segs and abs_segs[:len(target)] == target:
                count += 1
                if len(refs) < 50:
                    refs.append({"from_path": ".".join(segs), "pointer": node})

    with store._lock:
        walk(store.merged, [])
    return count, refs


@bp.route("/field/refs")
def field_refs():
    """Pointer references into a path (the delete-confirm blast radius)."""
    store = _store()
    if not store:
        return jsonify(ok=False, error="No active context"), 400
    dot_path = _normalize_dot_path(request.args.get("dot_path", "").strip())
    if not dot_path:
        return jsonify(ok=False, error="dot_path required"), 400
    total, refs = _count_refs_into(store, dot_path)
    return jsonify(ok=True, total=total, refs=refs)


@bp.route("/field/create", methods=["POST"])
def field_create():
    """Create a brand-new key (scalar or subtree) anywhere a dict parent
    exists — the Explorer's ＋. ``expect_type`` is a PARSE HINT only (the
    modifier's type gate is the single enforcement authority — no separate
    route-level gate, per the one-judge rule)."""
    from quam_state_manager.core import type_policy as _tp
    ctx = _active_ctx()
    modifier = ctx.get("modifier") if ctx else None
    if not modifier:
        return jsonify(ok=False, error="No active context"), 400
    guard = _chip_mismatch_response(
        request.form.get("expect_chip", ""),
        request.form.get("force_chip") in ("1", "true", "True"))
    if guard is not None:
        return guard

    dot_path = _normalize_dot_path(request.form.get("dot_path", "").strip())
    raw_value = request.form.get("value", "")
    expect_type = request.form.get("expect_type", "").strip()
    if not dot_path:
        return jsonify(ok=False, error="dot_path required"), 400
    reason = _crud_policy_reason(modifier.store, dot_path)
    if reason is not None:
        return jsonify(ok=False, error=reason, error_kind="policy"), 400

    try:
        if expect_type and expect_type != "infer":
            hint = _tp.Expected(spec=_tp.parse_type(expect_type), source="user")
            parsed = _tp.parse_with_expected(raw_value, hint)
        else:
            parsed = _tp.parse_value(raw_value)
        modifier.create_subtree(dot_path, parsed)
        _invalidate_engine_cache(ctx)
    except _tp.TypeMismatchError as e:
        return jsonify(ok=False, error=str(e), **e.as_json()), 400
    except (KeyError, TypeError, ValueError, IndexError) as e:
        return jsonify(ok=False, error=str(e)), 400

    if request.form.get("assign_type") in ("1", "true", "True") and expect_type \
            and expect_type != "infer" and ctx.get("path"):
        # optional convenience: record the chosen type as a user assignment
        try:
            _tp.save_assignment(current_app.instance_path, ctx["path"], dot_path,
                                {"type": expect_type})
            _attach_type_policy(ctx)
        except ValueError:
            pass
    return jsonify(ok=True, tray_html=_tray_html(), created_path=dot_path)


@bp.route("/field/delete", methods=["POST"])
def field_delete():
    """Delete a key (or whole subtree) — the Explorer's ✕. Reports the
    removed-leaf count and how many pointers elsewhere now dangle (the
    broken-pointer linter flags them immediately via diagnostics-changed)."""
    ctx = _active_ctx()
    modifier = ctx.get("modifier") if ctx else None
    if not modifier:
        return jsonify(ok=False, error="No active context"), 400
    guard = _chip_mismatch_response(
        request.form.get("expect_chip", ""),
        request.form.get("force_chip") in ("1", "true", "True"))
    if guard is not None:
        return guard

    dot_path = _normalize_dot_path(request.form.get("dot_path", "").strip())
    if not dot_path:
        return jsonify(ok=False, error="dot_path required"), 400
    if len(dot_path.split(".")) == 1:
        return jsonify(ok=False, error="top-level containers can't be deleted here",
                       error_kind="policy"), 400
    reason = _crud_policy_reason(modifier.store, dot_path, deleting=True)
    if reason is not None:
        return jsonify(ok=False, error=reason, error_kind="policy"), 400

    dangling, _ = _count_refs_into(modifier.store, dot_path)
    try:
        entry = modifier.delete_subtree(dot_path)
        _invalidate_engine_cache(ctx)
    except (KeyError, TypeError, ValueError, IndexError) as e:
        return jsonify(ok=False, error=str(e)), 400

    from quam_state_manager.core.modifier import _enumerate_leaves
    removed = sum(1 for _ in _enumerate_leaves(entry.old_value, dot_path))
    return jsonify(ok=True, tray_html=_tray_html(), removed_leaves=removed,
                   dangling_refs=dangling)


@bp.route("/schema/missing-keys")
def schema_missing_keys():
    """Keys the env schema expects under *scope* that the state lacks — the
    Explorer add-key datalist ('your class has these unset fields')."""
    store = _store()
    if not store:
        return jsonify(ok=False, error="No active context"), 400
    scope = _normalize_dot_path(request.args.get("scope", "").strip())
    policy = getattr(store, "type_policy", None)
    manifest = policy.manifest if policy is not None else None
    if not manifest:
        return jsonify(ok=True, warm=False, scope=scope, missing=[])
    from quam_state_manager.core import type_policy as _tp
    from quam_state_manager.core.state_env_validate import _anchor, _NO_ANCHOR
    try:
        node = store.get_value(scope) if scope else store.merged
    except (KeyError, TypeError, ValueError, IndexError):
        return jsonify(ok=True, warm=True, scope=scope, missing=[])
    if not isinstance(node, dict):
        return jsonify(ok=True, warm=True, scope=scope, missing=[])
    anchored = _anchor(manifest, node)
    if anchored is _NO_ANCHOR or anchored is None:
        return jsonify(ok=True, warm=True, scope=scope, missing=[])
    # ("fields", (class, canonical class, fields)) — the payload gained the
    # class identity in docs/79 so a caller can name the class·field a verdict
    # is scoped to; the fields map moved to [2] and this datalist 500'd on the
    # tuple it kept reading as a dict.
    fields = anchored[1][2]
    missing = []
    for fname, f in sorted(fields.items()):
        if fname in node:
            continue
        ts = f.get("type") or {}
        missing.append({
            "key": fname,
            "path": f"{scope}.{fname}" if scope else fname,
            "expected_type": _tp.format_type(ts),
            "default": f.get("default"),
            "source_class": (node.get("__class__") or "").rsplit(".", 1)[-1],
        })
    return jsonify(ok=True, warm=True, scope=scope, missing=missing[:40])


@bp.route("/field/edit-batch", methods=["POST"])
def field_edit_batch():
    """Apply many edits atomically; report per-path success/failure.

    Powers the Plotly popup's "Apply All" button. Accepts either:
      * form ``dot_path=<p1>&value=<v1>&dot_path=<p2>&value=<v2>...``
        (paired positionally), or
      * JSON ``{"updates": [{"dot_path": <p>, "value": <v>}, ...]}``.

    Atomic: if any single edit fails (type coercion, missing path, etc.),
    every previously-applied edit in this batch is rolled back. The
    response includes a per-path ``results`` array so the popup can mark
    individual rows applied or annotate the failing one with its error
    message.

    JSON callers may set ``"independent": true`` to apply each update on its
    own instead (no cross-row rollback): failures are reported per row and
    the successful rows stay applied. Used by the Explorer live-diff
    "Accept all" — one drifted/rejected value must not roll back hundreds
    of accepted ones.
    """
    # Capture the context up front so the post-mutation cache invalidation binds
    # to THIS chip even if a concurrent /load flips the active context.
    ctx = _active_ctx()
    modifier = ctx.get("modifier") if ctx else None
    if not modifier:
        return jsonify(ok=False, error="No active context"), 400

    payload = request.get_json(silent=True)
    # Cross-chip guard (audit #1): the apply-fit popup stamps the run's chip token.
    _pj = payload if isinstance(payload, dict) else {}
    guard = _chip_mismatch_response(
        str(_pj.get("expect_chip") or request.form.get("expect_chip", "")),
        bool(_pj.get("force_chip")) or request.form.get("force_chip") in ("1", "true", "True"))
    if guard is not None:
        return guard

    if isinstance(payload, dict) and isinstance(payload.get("updates"), list):
        # Per-update ``create`` opt-in: the review overlay sets it for "added"
        # rows so accepting one creates the missing key instead of KeyError-ing
        # (a generic bulk/plot edit never sets it, so its semantics are unchanged).
        pairs = [
            (_normalize_dot_path(str(u.get("dot_path", "")).strip()),
             u.get("value"), bool(u.get("create")))
            for u in payload["updates"]
            if isinstance(u, dict)
        ]
    else:
        dot_paths = request.form.getlist("dot_path")
        raw_values = request.form.getlist("value")
        if len(dot_paths) != len(raw_values):
            return jsonify(
                ok=False,
                error="dot_path / value count mismatch",
            ), 400
        pairs = [(_normalize_dot_path(p.strip()), v, False)
                 for (p, v) in zip(dot_paths, raw_values)]

    pairs = [(p, v, c) for (p, v, c) in pairs if p]
    if not pairs:
        return jsonify(ok=False, error="No updates supplied"), 400

    independent = bool(_pj.get("independent"))

    # docs/20 r12-B: an FSP edit never silently changes amplitudes — and
    # never commits before the compensation offer was seen. Batches without
    # the ack get the FIRST FSP entry's plan back as a 409; the popup then
    # resends the whole batch with the compensated amps included and
    # fsp_ack=comp (one gid = one Review bundle = one Ctrl+Z) or
    # fsp_ack=solo (FSP alone).
    _fsp_ack = str(_pj.get("fsp_ack") or request.form.get("fsp_ack") or "")
    if _fsp_ack not in ("comp", "solo"):
        for _dp, _rv, _c in pairs:
            try:
                _tgt = _resolve_edit_path(modifier.store, _dp)
            except Exception:  # noqa: BLE001
                continue
            plan = _fsp_plan_for(modifier.store, _tgt, _rv)
            if plan is not None:
                fsp_paths = sum(
                    1 for (p2, _v2, _c2) in pairs
                    if str(p2).endswith("full_scale_power_dbm"))
                plan["more_fsp_in_batch"] = fsp_paths > 1
                return jsonify(
                    ok=False, fsp_compensation=plan, fsp_dot_path=_dp,
                    error=("This batch changes a port's full-scale power — "
                           "confirm the amplitude compensation first")), 409

    # r14 ⑩ (same never-silent shape as the FSP gate): rows whose target is a
    # numeric-looking STRING get the conversion offer — 409 with the first
    # offender unless acked; type_fix=convert assigns the number type for
    # EVERY offending row (persisted), type_fix=keep proceeds as text.
    _type_fix = str(_pj.get("type_fix") or request.form.get("type_fix") or "")
    _tf_offers: list[tuple[str, dict]] = []
    for _dp, _rv, _c in pairs:
        if not isinstance(_rv, str):
            continue
        try:
            _tgt = _resolve_edit_path(modifier.store, _dp)
        except Exception:  # noqa: BLE001
            continue
        offer = _type_fix_offer(modifier.store, _tgt, _rv)
        if offer is not None:
            _tf_offers.append((_dp, offer))
    if _tf_offers and _type_fix not in ("convert", "keep"):
        _dp0, _off0 = _tf_offers[0]
        _off0["more_in_batch"] = len(_tf_offers) - 1
        return jsonify(
            ok=False, type_fix=_off0, type_fix_dot_path=_dp0,
            error=(f"{len(_tf_offers)} field(s) in this batch are stored as "
                   f"TEXT (e.g. {_off0['path']} = {_off0['current_display']}) "
                   "— convert their type to store numbers, or keep text")), 409
    if _tf_offers and _type_fix == "convert":
        from quam_state_manager.core import type_policy as _tp2
        for _dp, _off in _tf_offers:
            try:
                _tp2.save_assignment(
                    current_app.instance_path, ctx["path"], _off["path"],
                    {"type": _off["proposed"], "override_env": False,
                     "note": "converted from text via edit (r14)"})
            except ValueError:
                continue
        _attach_type_policy(ctx)

    results: list[dict[str, Any]] = []
    applied_entries: list[Any] = []

    # One group id when this batch commits MORE THAN ONE field (a grid row with
    # several edited cells, a plot Apply-All) so a single Ctrl+Z undoes the whole
    # batch atomically. A single-field batch stays ungrouped (undoes on its own).
    _batch_gid = modifier.new_group_id() if len(pairs) > 1 else None

    with modifier.store._lock:
        ok_overall = True
        for dot_path, raw_value, allow_create in pairs:
            try:
                # Follow pointers to the real literal when the path isn't navigable
                # as-is (keeps the posted dot_path in `results` for row matching).
                target_path = _resolve_edit_path(modifier.store, dot_path)
                _ro = _editability_reason(modifier.store, target_path)
                if _ro is not None:
                    raise ValueError(_ro)   # read-only policy → existing atomic rollback (audit P0)
                # String values parse against the TARGET's expectation (JSON
                # values pass through — the modifier gate is the backstop).
                parsed = (_parse_for_target(modifier.store, target_path, raw_value)
                          if isinstance(raw_value, str) else raw_value)
                try:
                    entry = modifier.set_value(target_path, parsed, _defer_hooks=True,
                                               group_id=_batch_gid)
                except KeyError:
                    if not allow_create:
                        raise
                    # "Added" review row pulled on the fly: the working copy lacks
                    # this key (qualibrate added new structure on the live chip).
                    # Create it (mirrors _replay_updates' create branch) so the ✓
                    # "pull just this field" works in exactly its most-useful case.
                    #
                    # docs/88: create at the RESOLVED target, not the posted path.
                    # A grid cell for a field this entity has not got yet posts an
                    # ALIAS whose parent is a pointer (qubits.q.resonator.opx_input
                    # → ports.mw_inputs.con1.2.1), so creating at dot_path could
                    # only ever fail — which is why "lo_mode" was uneditable.
                    try:
                        entry = modifier.create_subtree(target_path, parsed,
                                                        group_id=_batch_gid)
                    except KeyError as ce:
                        # The PARENT is also absent — a wholly-new subtree, not a
                        # new leaf. Guide to a full Pull rather than half-build it.
                        raise KeyError(
                            f"{dot_path} is new structure on the live chip — use "
                            f"Pull to bring it in (can't add a field whose parent "
                            f"doesn't exist yet)"
                        ) from ce
                applied_entries.append(entry)
                # Echo the COMMITTED value (type-coerced by set_value) + its display
                # + the resolved write path, so the client re-renders the cell from
                # the server's truth (never the typed string) and can match the
                # persistent modified-marker on the resolved path.
                results.append({
                    "dot_path": dot_path,
                    "resolved_path": entry.dot_path,
                    "applied": True,
                    "new_value": entry.new_value,
                    "display": _bulk_display(entry.new_value),
                })
            except (KeyError, TypeError, ValueError, IndexError) as e:
                row = {"dot_path": dot_path, "applied": False, "error": str(e)}
                from quam_state_manager.core.type_policy import TypeMismatchError
                if isinstance(e, TypeMismatchError):
                    row.update(e.as_json())     # error_kind/expected/got for the UI
                results.append(row)
                ok_overall = False
                if not independent:
                    break
                # independent mode: this row simply failed (set_value is atomic
                # per-row — nothing to roll back); keep applying the rest.

        if not ok_overall and not independent:
            # Roll back every entry applied so far; mark rolled-back rows in results.
            modifier._rollback(applied_entries)
            for r in results:
                if r["applied"]:
                    r["applied"] = False
                    r["error"] = "rolled back due to other failure(s) in this batch"
            return jsonify(
                ok=False,
                tray_html=_tray_html(),
                results=results,
            ), 400

        # Success path (and independent partial-success): clear pointer cache and
        # refresh search index ONCE for whatever applied (mirrors modifier.batch_set
        # so the per-entry hooks aren't duplicated).
        if applied_entries:
            modifier.store._clear_pointer_cache()
            if modifier.store.search_index is not None:
                for entry in applied_entries:
                    # create_subtree already registered the new leaves itself, and a
                    # created entry's dot_path may be a subtree root, not a leaf.
                    if getattr(entry, "created", False):
                        continue
                    modifier.store.search_index.update_entry(entry.dot_path, entry.new_value)

    if applied_entries:
        _invalidate_engine_cache(ctx)
    return jsonify(ok=ok_overall, tray_html=_tray_html(), results=results,
                   modified=_modified_delta())


# ======================================================================
# Pairs
# ======================================================================


@bp.route("/pairs")
def pairs():
    engine = _engine()
    store = _store()
    if not engine or not store:
        return render_template("_empty_state.html", page="qubit pairs")

    pair_data = []
    for pair_name in store.qubit_pair_names:
        try:
            pair_data.append(engine.get_pair(pair_name))
        except KeyError:
            continue
        except Exception as exc:  # noqa: BLE001 — r16 0-1: users hand-edit
            # pairs freely (nulls, strings, odd shapes). One poisoned pair
            # used to 500 the WHOLE list page ("the pairs menu won't open");
            # degrade it to a visible error row and keep the rest.
            logger.warning("get_pair(%r) failed: %s", pair_name, exc)
            pair_data.append({"id": pair_name, "is_active": True,
                              "_error": f"{type(exc).__name__}: {exc}"})

    # CR chips get CR-native columns (drive levers, 2Q fidelity, effective IF,
    # active badge) and adjacency ordering so the two DIRECTIONS of a physical
    # edge sit together (they are independent calibration targets — never
    # collapsed, docs/54). CZ/mixed chips keep the table byte-identical.
    pair_vocab = "cz"
    if cr_semantics.is_cr_chip(store.merged):
        kinds = {("cr" if "cr_upconverter" in p or "cr_operations" in p else "cz")
                 for p in pair_data}
        pair_vocab = "cr" if kinds == {"cr"} else "mixed"
    if pair_vocab == "cr":
        pair_data.sort(key=lambda p: (
            tuple(sorted((str(p.get("qubit_control") or ""),
                          str(p.get("qubit_target") or "")))),
            str(p.get("qubit_control") or "")))

    page = _int_arg("page", 1, minimum=1)
    per_page = _int_arg("per_page", _DEFAULT_PER_PAGE, minimum=1)
    page_pairs, total, page, total_pages = _paginate(pair_data, page, per_page)

    wiring_json = _wiring_json()

    template = "_pairs.html" if _is_htmx() else "pairs.html"
    return render_template(
        template,
        **_ctx(
            page="pairs",
            pairs=page_pairs,
            pair_vocab=pair_vocab,
            current_page=page,
            total_pages=total_pages,
            total=total,
            per_page=per_page,
            wiring_json=wiring_json,
        ),
    )


@bp.route("/pair/<name>")
def pair_detail(name: str):
    return _render_pair_detail(name, focus_path=request.args.get("focus") or None)


# ======================================================================
# Couplers — coupler-channel-scoped pair view (same table/filter/JSON-
# drill-down pattern as Pairs, narrowed to coupler fields; a pair without a
# coupler — e.g. a CR chip — simply isn't a row here, see has_coupler in
# QueryEngine.get_pair). The sidebar hides this nav item chip-wide when no
# pair has one.
# ======================================================================


@bp.route("/couplers")
def couplers():
    engine = _engine()
    store = _store()
    if not engine or not store:
        return render_template("_empty_state.html", page="couplers")

    pair_data = []
    for pair_name in store.qubit_pair_names:
        try:
            p = engine.get_pair(pair_name)
        except KeyError:
            continue
        if p.get("has_coupler"):
            pair_data.append(p)

    page = _int_arg("page", 1, minimum=1)
    per_page = _int_arg("per_page", _DEFAULT_PER_PAGE, minimum=1)
    page_pairs, total, page, total_pages = _paginate(pair_data, page, per_page)

    wiring_json = _wiring_json()

    template = "_couplers.html" if _is_htmx() else "couplers.html"
    return render_template(
        template,
        **_ctx(
            page="couplers",
            pairs=page_pairs,
            current_page=page,
            total_pages=total_pages,
            total=total,
            per_page=per_page,
            wiring_json=wiring_json,
        ),
    )


def _render_pair_detail(name: str, *, focus_path: str | None = None):
    """Shared renderer for pair detail (used by both view and edit routes)."""
    engine = _engine()
    store = _store()
    if not engine or not store:
        return render_template("_status.html", message="No state loaded", level="warning")

    try:
        data = engine.get_pair(name)
    except KeyError as e:
        return render_template("_status.html", message=str(e), level="error"), 404
    except Exception as e:  # noqa: BLE001 — r16 0-1: honest inline error
        # instead of a raw 500 htmx drops (the pane just froze before).
        logger.warning("get_pair(%r) failed: %s", name, e)
        return render_template(
            "_status.html", level="error",
            message=f"Pair {name!r} could not be read: "
                    f"{type(e).__name__}: {e}"), 422

    sections = _build_pair_sections(name, data, store)

    # Honest role labels (coupler / cr / zz) — a CR drive port used to render
    # captioned "coupler" through the single-return fallback.
    port_info = engine.get_pair_port_roles(name)

    template = "_pair_detail.html" if _is_htmx() else "pair_detail.html"
    return render_template(
        template,
        **_ctx(
            page="pair_detail",
            pair=data,
            pair_name=name,
            sections=sections,
            port_info=port_info,
            modified_map=_modified_map(),
            focus_path=focus_path,
        ),
    )


# ``arch`` gates each type to the pair's architecture (docs/54): flux types
# need flux evidence (z lines / coupler / CZ-shaped macros) and must never be
# written onto a FixedFrequencyTransmonPair; CR/Stark types need the matching
# pair drive channel. Enforced in pair_gate_form (UI) AND pair_add_gate (409).
_GATE_TYPES: dict[str, dict[str, Any]] = {
    "cz_unipolar": {
        "label": "CZ Unipolar (square pulse)",
        "arch": "flux",
        "fields": [
            ("amplitude", "Flux amplitude", 0.05, "float"),
            ("length", "Flux length (ns)", 100, "int"),
            ("coupler_amplitude", "Coupler amplitude", 0.0, "float"),
            ("coupler_length", "Coupler length (ns)", 100, "int"),
            ("phase_shift_control", "Phase shift (control)", 0.0, "float"),
            ("phase_shift_target", "Phase shift (target)", 0.0, "float"),
        ],
    },
    "cz_flattop": {
        "label": "CZ Flat-top (Gaussian envelope)",
        "arch": "flux",
        "fields": [
            ("amplitude", "Flux amplitude", 0.05, "float"),
            ("flat_length", "Flat length (ns)", 200, "int"),
            ("smoothing_length", "Smoothing length (ns)", 20, "int"),
            ("coupler_amplitude", "Coupler amplitude", 0.0, "float"),
            ("phase_shift_control", "Phase shift (control)", 0.0, "float"),
            ("phase_shift_target", "Phase shift (target)", 0.0, "float"),
        ],
    },
    "cz_parametric": {
        "label": "CZ Parametric (AC-flux modulated)",
        "arch": "flux",
        "fields": [
            ("amplitude", "Flux amplitude", 0.05, "float"),
            ("length", "Flux length (ns)", 100, "int"),
            ("modulation_frequency", "Modulation freq (Hz)", 250_000_000.0, "float"),
            ("coupler_amplitude", "Coupler amplitude", 0.0, "float"),
            ("phase_shift_control", "Phase shift (control)", 0.0, "float"),
            ("phase_shift_target", "Phase shift (target)", 0.0, "float"),
        ],
    },
    "cr_gate": {
        "label": "CR gate (cross-resonance macro)",
        "arch": "cr",
        "fields": [
            ("qc_correction_phase", "Correction phase (control)", 0.0, "float"),
            ("qt_correction_phase", "Correction phase (target)", 0.0, "float"),
        ],
    },
    "stark_cz": {
        "label": "Stark-induced CZ (ZZ drive macro)",
        "arch": "zz",
        "fields": [
            ("qc_correction_phase", "Correction phase (control)", 0.0, "float"),
            ("qt_correction_phase", "Correction phase (target)", 0.0, "float"),
        ],
    },
}


# r15 (docs/71 §3): the three remaining generator CZ variants become add-gate
# types — offered ONLY when the selected env's roster verifies the pulse
# classes they need (mirror of capabilities._CZ_VARIANT_CAPS; no roster ⇒
# exactly the legacy list, a regression fence). Shapes mirror
# run_build._cz_variant_pulses: SNZ / flattop_erf carry the whole gate on the
# qubit z line (coupler slot None); bipolar's coupler mirrors the qubit
# shape's fields by self-reference (single source of truth, the builder's
# link_attrs semantics).
_ENV_GATE_TYPES: dict[str, dict[str, Any]] = {
    "cz_bipolar": {
        "label": "CZ Bipolar (cosine, net-zero)",
        "arch": "flux",
        "fields": [
            ("amplitude", "Flux amplitude", 0.05, "float"),
            ("flat_length", "Flat length (ns)", 200, "int"),
            ("smoothing_length", "Smoothing length (ns)", 20, "int"),
            ("post_zero_padding_length", "Post-zero padding (ns)", 20, "int"),
            ("coupler_amplitude", "Coupler amplitude", 0.0, "float"),
            ("phase_shift_control", "Phase shift (control)", 0.0, "float"),
            ("phase_shift_target", "Phase shift (target)", 0.0, "float"),
        ],
    },
    "cz_snz": {
        "label": "CZ SNZ (sudden net-zero, qubit line only)",
        "arch": "flux",
        "fields": [
            ("amplitude", "Flux amplitude", 0.05, "float"),
            ("flat_length", "Flat length (ns)", 200, "int"),
            ("t_phi_eff", "t_phi_eff (ns)", 0.0, "float"),
            ("padding", "Padding (ns)", 20, "int"),
            ("phase_shift_control", "Phase shift (control)", 0.0, "float"),
            ("phase_shift_target", "Phase shift (target)", 0.0, "float"),
        ],
    },
    "cz_flattop_erf": {
        "label": "CZ Flat-top Erf (qubit line only)",
        "arch": "flux",
        "fields": [
            ("amplitude", "Flux amplitude", 0.05, "float"),
            ("flat_length", "Flat length (ns)", 200, "int"),
            ("risetime_samples", "Risetime (samples)", 16, "int"),
            ("post_zero_padding_length", "Post-zero padding (ns)", 20, "int"),
            ("phase_shift_control", "Phase shift (control)", 0.0, "float"),
            ("phase_shift_target", "Phase shift (target)", 0.0, "float"),
        ],
    },
}

# Roster leaves each new variant needs — per slot, ANY listed alternative
# satisfies it (modern quam_builder ships CosineBipolarPulse, legacy quam
# ships _CosineBipolarPulse; both are valid bipolar carriers).
_ENV_GATE_LEAVES: dict[str, tuple[tuple[str, ...], ...]] = {
    "cz_bipolar": (("CosineBipolarPulse", "_CosineBipolarPulse"),
                   ("SmoothedFlatTopGaussianPulse", "_FlatTopGaussianPulse")),
    "cz_snz": (("SNZPulse",),),
    "cz_flattop_erf": (("ErfSquarePulse",),),
}

# Flux-slot pulse-class candidates per gate type (evidence/roster resolved by
# _slot_qclass; None entry ⇒ that gate has no coupler slot).
_SLOT_LEAVES: dict[str, dict[str, tuple[str, ...] | None]] = {
    "cz_unipolar": {"qubit": ("SquarePulse",), "coupler": ("SquarePulse",)},
    "cz_flattop": {"qubit": ("SmoothedFlatTopGaussianPulse",
                             "_FlatTopGaussianPulse"),
                   "coupler": ("SmoothedFlatTopGaussianPulse",
                               "_FlatTopGaussianPulse")},
    "cz_bipolar": {"qubit": ("CosineBipolarPulse", "_CosineBipolarPulse"),
                   "coupler": ("SmoothedFlatTopGaussianPulse",
                               "_FlatTopGaussianPulse")},
    "cz_snz": {"qubit": ("SNZPulse",), "coupler": None},
    "cz_flattop_erf": {"qubit": ("ErfSquarePulse",), "coupler": None},
}


def _env_gate_types() -> dict[str, dict[str, Any]]:
    """The roster-verified subset of ``_ENV_GATE_TYPES`` (docs/71 §3).

    No roster installed ⇒ {} — the legacy gate list stays byte-identical.
    """
    from quam_state_manager.core.pulse_catalog import env_overlay_active
    roster = env_overlay_active()
    if not roster:
        return {}
    out: dict[str, dict[str, Any]] = {}
    for gid, gdef in _ENV_GATE_TYPES.items():
        needs = _ENV_GATE_LEAVES[gid]
        if all(any(leaf in roster for leaf in alts) for alts in needs):
            out[gid] = gdef
    return out


def _slot_qclass(store, leaf_candidates: tuple[str, ...] | None) -> str | None:
    """``__class__`` for a gate flux slot (docs/71 §3): chip evidence (the
    first candidate with a same-leaf pulse on the chip, verbatim majority) >
    roster canonical > None (today's classless shape — SM renders it as an
    implicit SquarePulse). Never guesses a module path."""
    if not leaf_candidates:
        return None
    from quam_state_manager.core.pulse_catalog import (env_overlay_active,
                                                      evidence_qclass)
    for leaf in leaf_candidates:
        ev = evidence_qclass(store.merged, leaf)
        if ev:
            return ev
    roster = env_overlay_active()
    if roster:
        for leaf in leaf_candidates:
            rec = roster.get(leaf)
            canonical = rec.get("canonical") if isinstance(rec, dict) else None
            if isinstance(canonical, str) and canonical:
                return canonical
    return None


def _slot_qclasses_for(store, gate_type: str) -> dict[str, str]:
    """Resolved slot classes for *gate_type* — only verified entries appear."""
    leaves = _SLOT_LEAVES.get(gate_type) or {}
    out: dict[str, str] = {}
    for slot_key in ("qubit", "coupler"):
        qc = _slot_qclass(store, leaves.get(slot_key))
        if qc:
            out[slot_key] = qc
    return out


def _pair_arch(store, pair_obj: dict) -> dict[str, bool]:
    """Structural evidence for which gate families this pair supports.

    A pair with no evidence either way (bare macros, no channels, no flux)
    defaults to flux — the legacy add-gate behavior for hand-built chips.
    """
    merged = store.merged
    cr = cr_semantics.cr_channel(pair_obj) is not None
    zz = cr_semantics.zz_channel(pair_obj) is not None
    macros = pair_obj.get("macros") if isinstance(pair_obj.get("macros"), dict) else {}
    has_flux_macro = any(cr_semantics.is_cz_shaped_macro(m)
                         for m in macros.values() if isinstance(m, dict))
    qc, qt = cr_semantics.pair_endpoints(pair_obj)
    has_z = any(
        isinstance(((merged.get("qubits") or {}).get(q) or {}).get("z"), dict)
        for q in (qc, qt) if q)
    flux = bool(isinstance(pair_obj.get("coupler"), dict) or has_flux_macro or has_z)
    if not cr and not zz and not flux:
        flux = True
    return {"cr": cr, "zz": zz, "flux": flux}


# Fallback CR/Stark macro class paths (the quam-builder CR branch layout).
# Only used when the chip carries no same-leaf macro to copy the exact path
# from — evidence beats guessing (the _parametric_cz_evidence idiom, via
# cr_semantics.gate_class_evidence).
_CR_GATE_QCLASS = ("quam_builder.architecture.superconducting.custom_gates"
                   ".fixed_transmon_pair.two_qubit_gates.CRGate")
_STARK_CZ_QCLASS = ("quam_builder.architecture.superconducting.custom_gates"
                    ".fixed_transmon_pair.two_qubit_gates.StarkInducedCZGate")


# Fallback ParametricCZGate class path — matches the quam_builder layout the
# app was developed against. Only used when the chip itself carries no
# ParametricCZGate macro to copy the path from (see _parametric_cz_qclass).
_PARAMETRIC_CZ_QCLASS = (
    "quam_builder.architecture.superconducting.custom_gates"
    ".flux_tunable_transmon_pair.two_qubit_gates.ParametricCZGate"
)


def _parametric_cz_evidence(store) -> str | None:
    """An existing ParametricCZGate macro ``__class__`` on this chip, or None.

    Reuses the exact string verbatim (majority, tie → lexicographic) —
    evidence beats guessing. Real chips rarely carry macro ``__class__``
    markers at all, and gate classes live in a different package family
    than pulses, so no prefix heuristic is safe here.
    """
    merged = getattr(store, "merged", None)
    pairs = merged.get("qubit_pairs") if isinstance(merged, dict) else None
    found: list[str] = []
    if isinstance(pairs, dict):
        for pair in pairs.values():
            macros = pair.get("macros") if isinstance(pair, dict) else None
            if not isinstance(macros, dict):
                continue
            for macro in macros.values():
                qc = macro.get("__class__") if isinstance(macro, dict) else None
                if isinstance(qc, str) and qc.rsplit(".", 1)[-1] == "ParametricCZGate":
                    found.append(qc)
    if found:
        ranked = sorted(Counter(found).items(), key=lambda kv: (-kv[1], kv[0]))
        return ranked[0][0]
    return None


def _parametric_cz_qclass(store) -> str:
    """The ParametricCZGate ``__class__`` to write on THIS chip."""
    return _parametric_cz_evidence(store) or _PARAMETRIC_CZ_QCLASS


def _build_gate_template(gate_type: str, fields: dict[str, Any], *,
                         parametric_qclass: str | None = None,
                         cr_qclass: str | None = None,
                         stark_qclass: str | None = None,
                         slot_qclasses: dict[str, str] | None = None) -> dict:
    """Construct the macro dict for ``gate_type`` from validated *fields*.

    *slot_qclasses* (r15, docs/71 §3) optionally carries evidence/roster-
    verified pulse ``__class__`` strings for the ``qubit``/``coupler`` flux
    slots — absent, the slots stay classless (the long-standing shape SM
    renders as an implicit SquarePulse). Never guessed.
    """
    _sq = slot_qclasses or {}

    def _classed(d: dict, slot_key: str) -> dict:
        qc = _sq.get(slot_key)
        return {"__class__": qc, **d} if qc else d

    if gate_type == "cr_gate":
        # the modern CRGate shape (verified on every flavor artifact, docs/54)
        return {
            "__class__": cr_qclass or _CR_GATE_QCLASS,
            "id": "#./inferred_id",
            "fidelity": None,
            "duration": "#./inferred_duration",
            "qc_correction_phase": fields["qc_correction_phase"],
            "qt_correction_phase": fields["qt_correction_phase"],
        }
    if gate_type == "stark_cz":
        return {
            "__class__": stark_qclass or _STARK_CZ_QCLASS,
            "id": "#./inferred_id",
            "fidelity": None,
            "duration": "#./inferred_duration",
            "qc_correction_phase": fields["qc_correction_phase"],
            "qt_correction_phase": fields["qt_correction_phase"],
        }
    if gate_type == "cz_unipolar":
        return {
            "fidelity": {},
            "flux_pulse_qubit": _classed({
                "amplitude": fields["amplitude"],
                "length": fields["length"],
            }, "qubit"),
            "coupler_flux_pulse": _classed({
                "amplitude": fields["coupler_amplitude"],
                "length": fields["coupler_length"],
            }, "coupler"),
            "phase_shift_control": fields["phase_shift_control"],
            "phase_shift_target": fields["phase_shift_target"],
        }
    if gate_type == "cz_flattop":
        return {
            "fidelity": {},
            "flux_pulse_qubit": _classed({
                "amplitude": fields["amplitude"],
                "flat_length": fields["flat_length"],
                "smoothing_length": fields["smoothing_length"],
                "length": "#./inferred_total_length",
            }, "qubit"),
            "coupler_flux_pulse": _classed(
                {"amplitude": fields["coupler_amplitude"]}, "coupler"),
            "phase_shift_control": fields["phase_shift_control"],
            "phase_shift_target": fields["phase_shift_target"],
        }
    if gate_type == "cz_bipolar":
        # bipolar carrier on the qubit line; the coupler mirrors the shape
        # fields by self-reference (run_build's link_attrs semantics — one
        # source of truth, edits can't drift the two slots apart).
        return {
            "fidelity": {},
            "flux_pulse_qubit": _classed({
                "amplitude": fields["amplitude"],
                "flat_length": fields["flat_length"],
                "smoothing_length": fields["smoothing_length"],
                "post_zero_padding_length": fields["post_zero_padding_length"],
                "length": "#./inferred_total_length",
            }, "qubit"),
            "coupler_flux_pulse": _classed({
                "amplitude": fields["coupler_amplitude"],
                "flat_length": "#../flux_pulse_qubit/flat_length",
                "smoothing_length": "#../flux_pulse_qubit/smoothing_length",
                "post_zero_padding_length":
                    "#../flux_pulse_qubit/post_zero_padding_length",
                "length": "#./inferred_total_length",
            }, "coupler"),
            "phase_shift_control": fields["phase_shift_control"],
            "phase_shift_target": fields["phase_shift_target"],
        }
    if gate_type == "cz_snz":
        # the whole gate rides the qubit z line (safe on fixed OR tunable
        # couplers) — no coupler pulse, matching run_build's SNZ shape.
        return {
            "fidelity": {},
            "flux_pulse_qubit": _classed({
                "amplitude": fields["amplitude"],
                "flat_length": fields["flat_length"],
                "t_phi_eff": fields["t_phi_eff"],
                "padding": fields["padding"],
                "length": "#./inferred_length",
            }, "qubit"),
            "coupler_flux_pulse": None,
            "phase_shift_control": fields["phase_shift_control"],
            "phase_shift_target": fields["phase_shift_target"],
        }
    if gate_type == "cz_flattop_erf":
        return {
            "fidelity": {},
            "flux_pulse_qubit": _classed({
                "amplitude": fields["amplitude"],
                "flat_length": fields["flat_length"],
                "risetime_samples": fields["risetime_samples"],
                "post_zero_padding_length": fields["post_zero_padding_length"],
                "length": "#./inferred_length",
            }, "qubit"),
            "coupler_flux_pulse": None,
            "phase_shift_control": fields["phase_shift_control"],
            "phase_shift_target": fields["phase_shift_target"],
        }
    if gate_type == "cz_parametric":
        return {
            "__class__": parametric_qclass or _PARAMETRIC_CZ_QCLASS,
            "fidelity": {},
            "flux_pulse_qubit": {
                "amplitude": fields["amplitude"],
                "length": fields["length"],
            },
            "modulation_frequency": fields["modulation_frequency"],
            "coupler_flux_pulse": {"amplitude": fields["coupler_amplitude"]},
            "phase_shift_control": fields["phase_shift_control"],
            "phase_shift_target": fields["phase_shift_target"],
        }
    raise ValueError(f"Unknown gate_type: {gate_type!r}")


_GATE_NAME_RE = re.compile(r"^[a-zA-Z][a-zA-Z0-9_]{0,63}$")


@bp.route("/pair/<name>/gate/new/cancel", methods=["GET"])
def pair_gate_form_cancel(name: str):
    """Restore the empty 'Add gate' button after the user cancels the form."""
    return render_template("_pair_add_gate_area.html", pair_name=name)


@bp.route("/pair/<name>/gate/new", methods=["GET"])
def pair_gate_form(name: str):
    """Return the partial form HTML for adding a new gate on this pair."""
    store = _store()
    if not store:
        return render_template("_status.html", message="No state loaded", level="warning")
    pair_obj = store.merged.get("qubit_pairs", {}).get(name)
    if pair_obj is None:
        return render_template("_status.html", message=f"Unknown pair: {name}", level="error"), 404
    existing_gates = sorted((pair_obj.get("macros") or {}).keys())
    # cz_parametric writes a ParametricCZGate __class__. quam_builder 0.4.0
    # removed that class entirely — a chip carrying it becomes unloadable on
    # that stack. Offer the option only when the chip itself already proves
    # the class exists (an existing ParametricCZGate macro to copy the exact
    # path from); otherwise drop it rather than corrupt the file.
    # Architecture gate: flux types on flux pairs, CR/Stark on their channels —
    # never offer a flux-CZ macro on a FixedFrequencyTransmonPair (writing one
    # corrupts the pair for quam's loader) nor a CR macro on a coupler pair.
    arch = _pair_arch(store, pair_obj)
    # r15 (docs/71 §3): + the roster-verified generator variants (SNZ /
    # bipolar / flattop-erf) — no roster ⇒ exactly the legacy list.
    gate_types = {k: v for k, v in {**_GATE_TYPES, **_env_gate_types()}.items()
                  if arch.get(v.get("arch", "flux"))}
    if _parametric_cz_evidence(store) is None:
        gate_types = {k: v for k, v in gate_types.items() if k != "cz_parametric"}
    parametric_qclass = _parametric_cz_qclass(store)
    return render_template(
        "_pair_add_gate.html",
        pair_name=name,
        gate_types=gate_types,
        existing_gates=existing_gates,
        # The JSON preview in the form must show the class the SERVER will
        # write (chip-derived when possible), not a duplicated literal.
        parametric_qclass=parametric_qclass,
        cr_gate_qclass=(cr_semantics.gate_class_evidence(store.merged, "CRGate")
                        or _CR_GATE_QCLASS),
        stark_cz_qclass=(cr_semantics.gate_class_evidence(
            store.merged, "StarkInducedCZGate") or _STARK_CZ_QCLASS),
        xy_detuned_missing=(arch["zz"] and cr_semantics.xy_detuned_channel(
            pair_obj) is None),
    )


@bp.route("/pair/<name>/gate", methods=["POST"])
def pair_add_gate(name: str):
    """Create a new gate macro on this pair.  Form fields: ``gate_name``,
    ``gate_type``, plus type-specific numeric fields.
    """
    modifier = _modifier()
    store = _store()
    if not modifier or not store:
        return render_template("_status.html", message="No state loaded", level="warning")

    gate_name = (request.form.get("gate_name") or "").strip()
    gate_type = (request.form.get("gate_type") or "").strip()

    all_gate_types = {**_GATE_TYPES, **_ENV_GATE_TYPES}
    if gate_type not in all_gate_types:
        return render_template("_status.html", message=f"Unknown gate type: {gate_type!r}", level="error"), 400
    # Server twin of the roster gating (never trust the form): an env-variant
    # whose pulse classes the selected env can't verify must not be written.
    if gate_type in _ENV_GATE_TYPES and gate_type not in _env_gate_types():
        return render_template(
            "_status.html",
            message=(f"{gate_type} needs pulse classes the selected "
                     "environment does not verify — probe/select the env "
                     "that ships them first."),
            level="error"), 409
    if not _GATE_NAME_RE.match(gate_name):
        return render_template(
            "_status.html",
            message="Gate name must start with a letter and contain only letters, digits, or underscores (max 64).",
            level="error",
        ), 400

    pair_obj = store.merged.get("qubit_pairs", {}).get(name)
    if pair_obj is None:
        return render_template("_status.html", message=f"Unknown pair: {name}", level="error"), 404
    if gate_name in (pair_obj.get("macros") or {}):
        return render_template(
            "_status.html",
            message=f"Gate {gate_name!r} already exists on this pair. Pick a different name.",
            level="error",
        ), 409
    # Server-side twin of the form's architecture gating (never trust the
    # form): a flux-CZ macro on a CR pair — or a CR macro on a coupler pair —
    # corrupts the pair for quam's loader.
    arch = _pair_arch(store, pair_obj)
    wanted_arch = all_gate_types[gate_type].get("arch", "flux")
    if not arch.get(wanted_arch):
        labels = {"flux": "a flux-tunable/coupler pair",
                  "cr": "a pair with a cross-resonance channel",
                  "zz": "a pair with a ZZ drive channel"}
        return render_template(
            "_status.html",
            message=(f"{gate_type} needs {labels[wanted_arch]} — this pair's "
                     "architecture doesn't support it."),
            level="error",
        ), 409
    # Without on-chip evidence that ParametricCZGate exists in this chip's
    # stack, refuse to write a class path that may make the whole file
    # unloadable (quam_builder 0.4.0 removed the class).
    if gate_type == "cz_parametric" and _parametric_cz_evidence(store) is None:
        return render_template(
            "_status.html",
            message=("cz_parametric is unavailable: this chip carries no "
                     "ParametricCZGate macro to copy the class path from, and "
                     "recent quam-builder releases removed the class — a "
                     "guessed path would make state.json unloadable."),
            level="error",
        ), 409

    fields: dict[str, Any] = {}
    for field_name, _label, default, kind in all_gate_types[gate_type]["fields"]:
        raw = request.form.get(field_name, "")
        try:
            fields[field_name] = float(raw) if kind == "float" else int(float(raw))
        except (TypeError, ValueError):
            fields[field_name] = default

    template = _build_gate_template(
        gate_type, fields,
        parametric_qclass=_parametric_cz_qclass(store),
        cr_qclass=cr_semantics.gate_class_evidence(store.merged, "CRGate"),
        stark_qclass=cr_semantics.gate_class_evidence(
            store.merged, "StarkInducedCZGate"),
        slot_qclasses=_slot_qclasses_for(store, gate_type))
    dot_path = f"qubit_pairs.{name}.macros.{gate_name}"
    try:
        modifier.create_subtree(dot_path, template)
        _invalidate_engine_cache()
    except (KeyError, ValueError, TypeError) as e:
        return render_template("_status.html", message=str(e), level="error"), 400

    focus_field = ("qc_correction_phase" if wanted_arch in ("cr", "zz")
                   else "flux_pulse_qubit.amplitude")
    detail = _render_pair_detail(name, focus_path=f"{dot_path}.{focus_field}")
    if isinstance(detail, tuple):
        return detail
    # A new gate macro adds pulse-shaped nodes (flux_pulse_qubit, …) — tell an
    # open Pulses table to refresh, like the other pulse-mutating routes do.
    resp = make_response(detail + "\n" + _tray_oob())
    resp.headers["HX-Trigger"] = "pulses-changed, diagnostics-changed"
    return resp


@bp.route("/pair/<name>/edit", methods=["POST"])
def pair_edit(name: str):
    ctx = _active_ctx()
    modifier = ctx.get("modifier") if ctx else None
    if not modifier:
        return render_template("_status.html", message="No state loaded", level="warning")

    # Chip-identity gate (audit #1) — see qubit_edit.
    guard = _chip_mismatch_html(
        request.form.get("expect_chip", ""),
        request.form.get("force_chip") in ("1", "true", "True"))
    if guard is not None:
        return guard

    dot_path = request.form.get("dot_path", "")
    raw_value = request.form.get("value", "")
    freq_sync = request.form.get("freq_sync", "1") != "0"

    try:
        # Same server-side hardening as /field/edit (audit-P0) — see qubit_edit:
        # resolve pointer leaves to their literal target, enforce the read-only
        # policy, parse against the target's expected type. Keeps these legacy
        # routes from being a side door around the durable policy layer.
        target_path = _resolve_edit_path(modifier.store, dot_path)
        _ro = _editability_reason(modifier.store, target_path)
        if _ro is not None:
            raise ValueError(_ro)
        parsed = _parse_for_target(modifier.store, target_path, raw_value)
        # Group primary + freq-mirror twin under one id (see qubit_edit) so a
        # single Ctrl+Z reverts both atomically instead of leaving f_01≠RF.
        gid = (modifier.new_group_id()
               if freq_sync and _freq_twin_path(target_path) else None)
        entry = modifier.set_value(target_path, parsed, group_id=gid)
        # No-op for pair-level fields (their paths carry no f_01/RF suffix); covers
        # the case where a pair inspector exposes a member qubit's frequency.
        mirrored = _maybe_mirror_freq(modifier, target_path, entry, group_id=gid) if freq_sync else None
        _invalidate_engine_cache(ctx)
    except (KeyError, TypeError, ValueError, IndexError) as e:
        return render_template("_status.html", message=str(e), level="error"), 400

    detail = _render_pair_detail(name, focus_path=dot_path)
    if isinstance(detail, tuple):
        return detail
    out = detail + "\n" + _tray_oob()
    if mirrored:
        out += _freq_mirror_oob(mirrored)
    # Keep the diagnostics badge/banner + pulse surfaces in sync after an
    # inspector edit (see qubit_edit) — they listen on diagnostics-changed.
    resp = make_response(out)
    resp.headers["HX-Trigger"] = "pulses-changed, diagnostics-changed"
    return resp


# ======================================================================
# Comparison Table
# ======================================================================


@bp.route("/table")
def comparison_table():
    engine = _engine()
    if not engine:
        return render_template("_empty_state.html", page="the parameter table")

    selected = request.args.getlist("props") or _ALL_TABLE_PROPS

    chain_filter = request.args.get("chain")
    sort_by = request.args.get("sort")
    sort_dir = request.args.get("dir", "asc")

    all_rows = engine.summary_table(selected)

    chains = sorted({
        r["id"][1] for r in all_rows
        if len(r.get("id", "")) >= 2 and r["id"][0] == "q" and r["id"][1].isalpha()
    })

    rows = all_rows
    if chain_filter:
        rows = [r for r in all_rows if r["id"].startswith(f"q{chain_filter}")]

    if sort_by and sort_by in selected:
        def _sort_key(r):
            v = r.get(sort_by)
            # Numerics sort numerically; a dangling-pointer string (real data has
            # them — pointer_resolver returns raw strings for unresolvable pointers)
            # or any non-number sorts in a SEPARATE bucket after, so a column mixing
            # str and float can't raise TypeError → 500 the HTMX-swapped table.
            num = isinstance(v, (int, float)) and not isinstance(v, bool)
            return (v is None, not num, v if num else str(v))
        rows.sort(key=_sort_key, reverse=(sort_dir == "desc"))

    # col_stats computed on ALL filtered rows (before pagination) for accurate min/max
    col_stats: dict[str, dict] = {}
    for prop in selected:
        numeric_vals = [r[prop] for r in rows if isinstance(r.get(prop), (int, float))]
        if numeric_vals:
            col_stats[prop] = {"min": min(numeric_vals), "max": max(numeric_vals)}

    page = _int_arg("page", 1, minimum=1)
    per_page = _int_arg("per_page", _DEFAULT_PER_PAGE, minimum=1)
    page_rows, total, page, total_pages = _paginate(rows, page, per_page)

    template = "_table.html" if _is_htmx() else "table.html"
    return render_template(
        template,
        **_ctx(
            page="table",
            rows=page_rows,
            selected_props=selected,
            all_props=_ALL_TABLE_PROPS,
            prop_groups=_TABLE_PROP_GROUPS,
            sort_by=sort_by,
            sort_dir=sort_dir,
            active_chain=chain_filter,
            chains=chains,
            col_stats=col_stats,
            current_page=page,
            total_pages=total_pages,
            total=total,
            per_page=per_page,
        ),
    )


# ======================================================================
# Chip Topology
# ======================================================================


@bp.route("/wiring")
@bp.route("/topology")
def wiring_view():
    engine = _engine()
    if not engine:
        return render_template("_empty_state.html", page="the chip topology")

    store = _store()
    topology = engine.get_topology()
    wiring_json = _wiring_json()

    history_count = len(_history().list_snapshots(_active_path())) if store else 0

    # Health layer (Chip Status overhaul): the structural linter (port collisions,
    # dangling pointers, value-spec violations) — already used by the drag-drop
    # preview, compare, and /diagnostics — now also drives the Chip Status health
    # badge + per-node markers, so "is it healthy" and "is it broken" stop being
    # two disconnected pages. Thresholds seed the client's live verdict/colour
    # (the client persists UI edits to localStorage).
    diag_findings = diagnostics.lint_state(store) if store else []
    diag_summary = diagnostics.summarize(diag_findings)

    # Optional ?view= picks the Chip Status sub-view (Topology / Full View /
    # Overview / Fidelity / …) so the left-nav sub-items and shareable links land
    # directly on a section. Validated against the known set; anything else (incl.
    # bare /topology from the main "Chip Status" item) → client default, which is
    # the topology-diagram-only view.
    # Phase C scroll-spy sections (+ "full" kept for old bookmarks → topology).
    _CHIP_VIEWS = {"topology", "overview", "distributions", "gate", "fidelity",
                   "coherence", "frequencies", "calibration", "trends", "full"}
    chip_view = request.args.get("view", "").strip().lower()
    if chip_view not in _CHIP_VIEWS:
        chip_view = ""

    template = "_wiring.html" if _is_htmx() else "wiring.html"
    return render_template(
        template,
        **_ctx(
            page="topology",
            topology_json=json.dumps(topology),
            wiring_json=wiring_json,
            history_count=history_count,
            chip_view=chip_view,
            diag_summary=diag_summary,
            diag_findings=[f.as_dict() for f in diag_findings],
            default_thresholds=chip_health.DEFAULT_THRESHOLDS,
            # Per-metric glossary (label / abbr / good-direction / blurb) — the
            # single source the client's tooltips, arrows and threshold-editor
            # labels all read, so they can't drift from the verdict direction.
            metric_meta=chip_health.METRIC_META,
        ),
    )


# ======================================================================
# State History & Live Monitoring
# ======================================================================


# History-panel default page size. At 500 snapshots the old show-all default
# rendered every row on each panel open; 0 stays the EXPLICIT "All" choice
# (user doctrine: every pager offers a user-selectable size including All).
_HISTORY_PANEL_PER_PAGE = 50


@bp.route("/api/history")
def history_list():
    """Return the history panel content with paginated snapshot list."""
    store = _store()
    if not store:
        return render_template("_status.html", message="No state loaded", level="warning")

    hm = _history()
    snapshots = hm.list_snapshots(_active_path())

    page = _int_arg("page", 1, minimum=1)
    per_page = _int_arg("per_page", _HISTORY_PANEL_PER_PAGE, minimum=0)  # 0 = All (explicit)
    page_items, total, current_page, total_pages = _paginate(snapshots, page, per_page)

    # hist_chip_key + active_path power the additive "⇄ Compare…" deep link
    # (docs/49 U1a — the in-panel Compare Selected stays verbatim)
    try:
        hist_chip_key = hm._key_for(Path(_active_path()))
    except Exception:
        hist_chip_key = ""
    return render_template(
        "_history_panel.html",
        snapshots=page_items,
        total=total,
        page=current_page,
        total_pages=total_pages,
        per_page=per_page,
        hist_chip_key=hist_chip_key,
        active_path=_active_path(),
    )


@bp.route("/api/history/snapshot", methods=["POST"])
def history_snapshot():
    """Create a manual snapshot and return updated history list."""
    store = _store()
    if not store:
        return render_template("_status.html", message="No state loaded", level="warning")

    hm = _history()
    hm.check_and_snapshot(_active_path(), "manual", force=True,
                          project=_scope_for(_active_path(), _active_ctx()))

    return history_list()


@bp.route("/api/history/<timestamp>/diff")
def history_diff_detail(timestamp: str):
    """Show diff between a snapshot and the current state."""
    store = _store()
    if not store:
        return render_template("_status.html", message="No state loaded", level="warning")

    hm = _history()
    try:
        # Diff against the in-memory store (the working copy) — never the
        # live files, which an experiment program may be writing.
        entries = hm.diff_current(_active_path(), timestamp, current_store=store)
    except Exception as e:
        return render_template("_status.html", message=f"Diff failed: {e}", level="error")

    summary = Differ.summary(entries)
    # User preference: show all entries by default (no silent truncation).
    # The template renders a simple table which the browser handles fine
    # well past 10k rows.
    return render_template(
        "_history_detail.html",
        entries=entries,
        summary=summary,
        timestamp=timestamp,
        total=len(entries),
    )


@bp.route("/api/history/compare")
def history_compare():
    """Compare two historical snapshots side by side."""
    store = _store()
    if not store:
        return render_template("_status.html", message="No state loaded", level="warning")

    ts_a = request.args.get("ts_a", "")
    ts_b = request.args.get("ts_b", "")
    if not ts_a or not ts_b:
        return render_template("_status.html", message="Select two snapshots", level="warning")

    hm = _history()
    try:
        entries = hm.diff_snapshots(_active_path(), ts_a, ts_b)
    except Exception as e:
        return render_template("_status.html", message=f"Compare failed: {e}", level="error")

    summary = Differ.summary(entries)
    return render_template(
        "_history_compare.html",
        entries=entries,
        summary=summary,
        ts_a=ts_a,
        ts_b=ts_b,
        total=len(entries),
    )


# ======================================================================
# State History — full-state snapshots: review, diff, restore, replace
# (a view + restore layer over the SAME HistoryManager snapshot store that
# backs Param History; no new snapshot capture). Sidebar peer of Bulk Edit.
# ======================================================================

_STATE_HISTORY_PER_PAGE = 40


@bp.route("/state-history")
def state_history():
    """The State History page: full-chip snapshots over time, newest first,
    framed by the experiment that produced each (experiment-attribution)."""
    store = _store()
    if not store:
        return render_template("_empty_state.html", page="state history")
    hm = _history()
    snapshots = hm.list_snapshots(_active_path())
    page = _int_arg("page", 1, minimum=1)
    per_page = _int_arg("per_page", _STATE_HISTORY_PER_PAGE, minimum=1)
    page_items, total, page, total_pages = _paginate(snapshots, page, per_page)
    try:
        hist_chip_key = _history()._key_for(Path(_active_path()))
    except Exception:
        hist_chip_key = ""
    # Honest footprint line for the header ("N snapshots · X on disk") —
    # cached per (count, newest ts), so steady-state renders pay no walk.
    try:
        disk_stats = hm.history_disk_stats(_active_path())
    except Exception:   # noqa: BLE001 — a stats failure must never kill the page
        disk_stats = None
    ctx = _ctx(
        page="state_history",
        snapshots=page_items,
        total=total,
        current_page=page,
        total_pages=total_pages,
        per_page=per_page,
        chip_origin=_active_origin(),
        hist_chip_key=hist_chip_key,
        disk_stats=disk_stats,
    )
    # body=1 → just the timeline inner (toolbar + entries + pagination), for the
    # stateRestored auto-refresh that re-fetches it into #state-history-body
    # without disturbing the detail/result pane beside it.
    if request.args.get("body") == "1":
        return render_template("_state_history_body.html", **ctx)
    template = "_state_history.html" if _is_htmx() else "state_history.html"
    return render_template(template, **ctx)


def _snapshot_state_wiring(hm, path, timestamp) -> tuple[dict, dict]:
    """Parsed (state, wiring) of a snapshot — deep-copied so callers can write
    them to the working folder without aliasing the cached store."""
    snap = hm.load_snapshot(path, timestamp)
    return copy.deepcopy(snap.state), copy.deepcopy(snap.wiring)


@bp.route("/state-history/<timestamp>/stage", methods=["POST"])
def state_history_stage(timestamp: str):
    """Mode 1 (safe): load a snapshot into the WORKING COPY so the user can
    review the diff and then Apply to live through the normal flow. Never
    writes the live chip directly. Confirms over a dirty working copy."""
    ctx = _active_ctx()
    if not ctx or ctx.get("type") != "quam":
        return render_template("_status.html", message="No state loaded", level="warning")
    store = ctx["store"]
    wc = ctx["working_copy"]
    hm = _history()
    path = ctx["path"]   # snapshot source = the captured folder, not the live-active one

    # Don't silently drop pending edits the user hasn't reviewed. Includes
    # working_dirty (saved-but-unapplied edits), which after an LRU eviction or
    # restart is the ONLY surviving signal — change_log is empty on a rehydrated
    # store and pending_reapply is dropped on eviction.
    with store._lock:
        has_pending = (bool(store.change_log) or bool(ctx.get("pending_reapply"))
                       or bool(ctx.get("working_dirty")))
    if has_pending and request.values.get("force") != "1":
        # audit-r10: the fragment's force button must target an element that
        # EXISTS where the fragment lands. From the tray's "Revert last
        # apply" (from=tray) that is #status-bar — the default
        # #state-history-detail exists only on the State History page, so
        # the button was a guaranteed htmx targetError (dead click) there.
        _from_tray = request.values.get("from") == "tray"
        return render_template(
            "_sh_confirm.html",
            message=("You have unsaved edits in the working state. Loading this "
                     "snapshot will replace them."),
            action_url=(f"/state-history/{timestamp}/stage?force=1"
                        + ("&from=tray" if _from_tray else "")),
            action_label="Replace working state anyway",
            confirm="Discard your unsaved edits in the working state and load this snapshot?",
            **({"target": "#status-bar"} if _from_tray else {}),
        ), 409

    try:
        state, wiring = _snapshot_state_wiring(hm, path, timestamp)
    except Exception as exc:
        return render_template("_status.html",
                               message=f"Could not load snapshot {timestamp}: {exc}",
                               level="error"), 404
    try:
        with _active_wc_lock(ctx):
            safe_io.write_state_wiring(wc.working_folder, state, wiring)
            ctx["_alarm_reason"] = "snapshot-stage"
            _rebuild_after_working_copy_replaced(ctx)
            ctx["working_dirty"] = True   # working now differs from live
            # audit-r10: mark the STAGED BASE — content not representable as
            # change-log entries. The sync guard/conflict tray key on this,
            # not on stash emptiness (a later /save or conflict fills the
            # stash with only the EDITS and would flip a stash-based check).
            ctx["staged_base"] = True
            _clear_reapply(ctx)   # in-lock: no window for a concurrent
                                  # sync to read dirty+stale-stash (audit-r10)
    except (OSError, ValueError) as exc:
        return render_template("_status.html",
                               message=f"Staging failed: {exc}", level="error"), 500
    logger.info("State History: staged snapshot %s into working copy", timestamp)
    msg = render_template(
        "_status.html",
        message=(f"Snapshot {timestamp} loaded as the working state. Review the "
                 "diff below, then Apply to live from the top bar."),
        level="success")
    # detail-area message + OOB tray refresh (now shows working_dirty).
    # stateRestored so an inspector/pulse pane open on another menu re-reads
    # the staged values too (the working copy changed wholesale).
    resp = make_response(msg + "\n" + _tray_oob())
    resp.headers["HX-Trigger"] = "pulses-changed, stateRestored, diagnostics-changed"
    return resp


@bp.route("/state-history/<timestamp>/restore-live", methods=["POST"])
def state_history_restore_live(timestamp: str):
    """Mode 2 (gated): replace the LIVE chip with a snapshot in one step.

    Safety gates (all S1):
      - refuse on a dataset-archive context (origin != live);
      - snapshot the CURRENT live first, so the restore is itself reversible;
      - block when the snapshot's wiring topology doesn't align with live
        (a chip-swapped snapshot would overwrite mismatched wiring) unless
        forced — the UI funnels non-aligned to stage+diff;
      - write through working_copy.apply_to_live(force) under the build lock
        (the single live writer), then rebuild every derived cache.
    """
    ctx = _active_ctx()
    if not ctx or ctx.get("type") != "quam":
        return render_template("_status.html", message="No state loaded", level="warning")
    blocked = _archive_write_blocked(ctx)   # guard the CAPTURED ctx (TOCTOU)
    if blocked is not None:
        return blocked
    store = ctx["store"]
    wc = ctx["working_copy"]
    hm = _history()
    path = ctx["path"]   # snapshot source = the captured folder, not the live-active one
    # Two INDEPENDENT confirmations — one token must never collapse both gates,
    # or consenting to "discard my edits" would silently also overwrite live
    # wiring with a mismatched topology. A bare ?force=1 is a master override
    # (tests / "I confirmed everything").
    force_all = request.values.get("force") == "1"
    force_pending = force_all or request.values.get("force_pending") == "1"
    force_align = force_all or request.values.get("force_align") == "1"

    # Don't silently destroy unsaved working-copy edits. restore-live replaces
    # the live chip (and the working copy) with the snapshot; any in-memory
    # change-log / reapply-stash edits the user hasn't applied would be lost.
    # working_dirty (saved-but-unapplied) is the only surviving signal after an
    # eviction/restart. Warn first (mirrors the stage gate), with a proceed button.
    with store._lock:
        has_pending = (bool(store.change_log) or bool(ctx.get("pending_reapply"))
                       or bool(ctx.get("working_dirty")))
    if has_pending and not force_pending:
        return render_template(
            "_sh_confirm.html",
            message=("You have unsaved edits in the working state. Restoring this "
                     "snapshot to live will discard them."),
            action_url=f"/state-history/{timestamp}/restore-live?force_pending=1",
            action_label="Discard edits and continue",
            confirm="Discard your unsaved edits and continue restoring this snapshot?",
        ), 409

    # Fingerprint-align gate: a single chip dir can hold snapshots routed by
    # fingerprint from a different wiring topology. Never overwrite live wiring
    # with a non-aligned snapshot unless explicitly forced — a SEPARATE
    # confirmation so the topology warning is always shown, even after the user
    # forced past the unsaved-edits gate. The confirm carries force_pending too
    # so it doesn't bounce back to the first gate.
    from quam_state_manager.core.history import (
        ALIGN_ALIGNED, align, fingerprint_of)
    try:
        snap_dir = hm.load_snapshot(path, timestamp).folder_path
        alignment = align(fingerprint_of(snap_dir), fingerprint_of(path))
    except Exception:
        alignment = "unknown"
    if alignment != ALIGN_ALIGNED and not force_align:
        return render_template(
            "_sh_confirm.html",
            message=(f"This snapshot's wiring does not match the loaded chip "
                     f"({alignment}). Loading it as the working state to review the "
                     "diff first is safer than a direct restore."),
            action_url=(f"/state-history/{timestamp}/restore-live"
                        "?force_pending=1&force_align=1"),
            action_label="Restore to live anyway",
            confirm="The wiring topology differs — overwrite the live chip regardless?",
        ), 409

    # Snapshot the current live BEFORE overwriting — the restore is reversible.
    # The reversibility guarantee is the whole safety story of Mode 2, so if the
    # snapshot can't be taken we refuse the restore rather than proceed blind.
    # The pre-restore backup, the snapshot load, and the live overwrite all run
    # UNDER the per-folder build lock, so no in-app mutator (another tab's apply,
    # the scheduler's post-node reconcile) can write new live between the backup
    # and the overwrite — that content would otherwise be clobbered and exist in
    # NO snapshot while the user is told the restore is reversible. Taking the
    # backup immediately before the write also minimises the external-writer
    # (experiment) window.
    #
    # check_and_snapshot reports failure BOTH ways: it raises, OR it returns None
    # (source mtime unreadable / OSError writing the snapshot dir). With force=True
    # the dedup/no-change early-returns are bypassed, so None here unambiguously
    # means "no backup was taken" — treat it identically to the exception branch,
    # never fall through to overwrite the live chip while telling the user it's
    # reversible (matches the sibling /state/archive route's `if meta is None`).
    try:
        with _active_wc_lock(ctx):
            try:
                backup_meta = hm.check_and_snapshot(path, "manual", force=True,
                                                    project=_scope_for(path, ctx))
            except Exception as exc:
                logger.warning("Pre-restore snapshot failed", exc_info=True)
                return render_template(
                    "_status.html",
                    message=(f"Could not snapshot the current state before restoring "
                             f"({exc}). Aborted so the restore stays reversible — retry, "
                             "or use 'Load as working state' to review first."),
                    level="error"), 500
            if backup_meta is None:
                logger.warning(
                    "Pre-restore snapshot returned None (no backup taken) — aborting "
                    "restore to keep it reversible")
                return render_template(
                    "_status.html",
                    message=("Could not snapshot the current state before restoring "
                             "(the live files may be locked by a running experiment, or "
                             "the snapshot could not be written). Aborted so the restore "
                             "stays reversible — retry, or use 'Load as working state' to "
                             "review first."),
                    level="error"), 500

            try:
                state, wiring = _snapshot_state_wiring(hm, path, timestamp)
            except Exception as exc:
                return render_template("_status.html",
                                       message=f"Could not load snapshot {timestamp}: {exc}",
                                       level="error"), 404

            safe_io.write_state_wiring(wc.working_folder, state, wiring)
            working_copy.apply_to_live(wc, force=True)
            ctx["_alarm_reason"] = "restore-live"
            _rebuild_after_working_copy_replaced(ctx)
    except (OSError, ValueError, safe_io.LiveFileError) as exc:
        return render_template("_status.html",
                               message=f"Restore to live failed: {exc}", level="error"), 500
    # The restored snapshot is now both the working copy and live; any stash of
    # pre-restore edits is stale (it targeted the old state) — drop it so a
    # later sync 'reapply' can't replay it onto the restored chip.
    _clear_reapply(ctx)
    # A restore is the user deliberately writing live — rebase drift tracking on
    # the restored state so it isn't reported as accumulated live drift.
    _reset_baseline_after_apply(ctx)
    # Record that the live chip is now this snapshot's content. NOT force=True:
    # the restored bytes are identical to an existing snapshot, so content-hash
    # dedup should recognise it and skip a redundant ~1MB write.
    try:
        hm.check_and_snapshot(path, "restore", project=_scope_for(path, ctx))
    except Exception:
        logger.warning("Post-restore snapshot failed", exc_info=True)
    logger.info("State History: restored snapshot %s to live", timestamp)
    msg = render_template(
        "_status.html",
        message=(f"Live chip restored to snapshot {timestamp}. The prior state "
                 "was snapshotted first, so this is reversible."),
        level="success")
    resp = make_response(msg + "\n" + _tray_oob())
    resp.headers["HX-Trigger"] = "pulses-changed, stateRestored, diagnostics-changed"
    return resp


@bp.route("/state-history/<timestamp>/label", methods=["POST"])
def state_history_label(timestamp: str):
    """Set or clear a snapshot's label and/or pinned flag. Pinned snapshots
    are exempt from pruning (protect a known-good baseline)."""
    store = _store()
    if not store:
        return render_template("_status.html", message="No state loaded", level="warning")
    hm = _history()
    label = (request.values.get("label") or "").strip() or None
    pinned = request.values.get("pinned")
    pinned_val = None if pinned is None else (pinned == "1")
    try:
        hm.annotate_snapshot(_active_path(), timestamp, label=label, pinned=pinned_val)
    except Exception as exc:
        return render_template("_status.html",
                               message=f"Could not update snapshot: {exc}", level="error"), 400
    return state_history()


@bp.route("/state-history/snapshot", methods=["POST"])
def state_history_snapshot():
    """Capture the current state now and re-render the State History page.

    Distinct from ``/api/history/snapshot`` (which returns the Param-History
    panel) — pointing the State History page's "Take snapshot" button at that
    one swapped the Param-History list in and wiped the timeline + restore/pin
    controls. This re-renders the State History fragment instead.
    """
    ctx = _active_ctx()
    if not ctx or ctx.get("type") != "quam":
        return render_template("_status.html", message="No state loaded", level="warning")
    try:
        _history().check_and_snapshot(ctx["path"], "manual", force=True,
                                      project=_scope_for(ctx["path"], ctx))
    except Exception as exc:
        return render_template("_status.html",
                               message=f"Snapshot failed: {exc}", level="error"), 500
    return state_history()


# ── docs/120 items 5+9 — chip-wide Trends ─────────────────────────────────
#
# Customer (twice, as items 5 and 9): "to see T1/RB/T2 trends today you go to
# Param History, but that shows PER-QUBIT trends, not an INTEGRATED one. Add a
# Trends tab under Chip Status where ALL qubits' T1 appear in a SINGLE plot.
# A multiple-line plot, legend = all qubits."
#
# The user approved "any numeric parameter" conditionally on the overhead
# investigation, which came back green (docs/120): a whole-chip leaf index is
# 1.21 MB over 433 real snapshots and a 20-qubit overlay of one metric costs
# 0.32-0.92 ms. Coverage is why it matters -- on the real chip T1/T2/fidelity
# are null and the curated eleven would render an almost empty page, while 588
# parameters have a real history.
#
# So: the curated properties are the DEFAULT selection (useful on open, and the
# fast SQL tier), and any other numeric leaf is reachable by dot-path through
# the docs/83 leaf index. Both tiers are normalised to ONE series shape so the
# template renders them identically.

_TRENDS_MAX_SERIES = 400        # a 20-qubit chip x 4 metrics is 80; armor only.


def _trend_series_curated(hm, path: Path, props: list[str]) -> list[dict]:
    """The SQLite property index — one call returns EVERY qubit for a metric."""
    try:
        rows = hm.extract_property_history(path, props, downsample=400)
    except Exception:  # noqa: BLE001
        return []
    out = []
    for r in rows:
        pts = [(p.get("timestamp"), p.get("value")) for p in (r.get("values") or [])
               if isinstance(p.get("value"), (int, float))]
        if pts:
            out.append({"metric": r["property"], "entity": r["qubit"], "points": pts})
    return out


def _trend_series_leaf(hm, path: Path, dot_path: str, qubits: list[str]) -> list[dict]:
    """Any numeric leaf, via the docs/83 change-point index.

    A path names ONE qubit (``qubits.q3.xy.operations.x180.amplitude``); the
    point of this page is every qubit at once, so the qubit segment is swapped
    across the chip and each concrete path queried. A qubit that never carried
    the leaf simply contributes no line — absence is not an error.
    """
    parts = dot_path.split(".")
    out: list[dict] = []
    if len(parts) >= 3 and parts[0] == "qubits":
        tmpl, label = parts[:], ".".join(parts[2:])
        for q in qubits:
            tmpl[1] = q
            rows = hm.leaf_field_series(path, ".".join(tmpl))
            pts = [(r[0], r[1]) for r in (rows or []) if isinstance(r[1], (int, float))]
            if pts:
                out.append({"metric": label, "entity": q, "points": pts})
        return out
    # Not qubit-scoped (a port, a pair leaf, a top-level key) — one line, and
    # the path itself is the legend, because there is nothing to fan out over.
    rows = hm.leaf_field_series(path, dot_path)
    pts = [(r[0], r[1]) for r in (rows or []) if isinstance(r[1], (int, float))]
    if pts:
        out.append({"metric": dot_path, "entity": dot_path.split(".")[-1], "points": pts})
    return out


@bp.route("/topology/trends")
def topology_trends():
    """The Trends section: one chart per metric, one line per qubit.

    Lazily fetched by the section (never on the Chip Status render) because it
    touches the history index, and a chip with hundreds of snapshots should not
    pay for a page the user may not scroll to.
    """
    ctx = _active_ctx()
    if not ctx or ctx.get("type") != "quam" or not ctx.get("path"):
        return render_template("_topo_trends.html", charts=[], curated=[],
                               selected=[], extra="", no_chip=True)
    hm = _history()
    path = Path(ctx["path"])
    store = _store()
    qubits = list(store.qubit_names) if store else []

    curated = list(DEFAULT_TRACKED_PROPERTIES)
    extra = (request.args.get("path") or "").strip()
    sel = [m for m in (request.args.get("metrics") or "").split(",") if m.strip()]
    # Default only on a BARE request. Turning every chip off is a choice, and
    # re-injecting the defaults over it would make the last chip un-turn-off-able
    # whenever a parameter path was also being charted.
    if not sel and "metrics" not in request.args and not extra:
        # A useful default rather than an empty page: the coherence + fidelity
        # trio a lab looks at first. Metrics with no data still render, saying
        # so — see the template; silently dropping them would make a chip look
        # like it has no history at all.
        sel = ["T1", "T2echo", "gate_fidelity_avg"]
    sel = [m for m in sel if m in curated][:8]

    series = _trend_series_curated(hm, path, sel) if sel else []
    if extra:
        series += _trend_series_leaf(hm, path, extra, qubits)

    if len(series) > _TRENDS_MAX_SERIES:
        series = series[:_TRENDS_MAX_SERIES]

    # Group into one chart per metric, preserving the requested order so the
    # page does not reshuffle as data arrives.
    order: list[str] = []
    by_metric: dict[str, list[dict]] = {}
    for s in series:
        by_metric.setdefault(s["metric"], [])
        if s["metric"] not in order:
            order.append(s["metric"])
        by_metric[s["metric"]].append(s)
    charts = [{"metric": m, "series": by_metric[m],
               "n_entities": len(by_metric[m])} for m in order]
    # A selected metric with NO series still gets a chart slot so the page can
    # say "nothing recorded yet" for it, instead of quietly showing fewer
    # charts than the user asked for.
    for m in sel:
        if m not in by_metric:
            charts.append({"metric": m, "series": [], "n_entities": 0})

    return render_template("_topo_trends.html", charts=charts, curated=curated,
                           selected=sel, extra=extra, no_chip=False,
                           snapshots=len(hm.list_snapshots(path)))


@bp.route("/topology/trends/paths")
def topology_trends_paths():
    """Typeahead over every numeric leaf the chip has ever recorded."""
    q = (request.args.get("q") or "").strip()
    ctx = _active_ctx()
    if not q or not ctx or not ctx.get("path"):
        return jsonify([])
    hits = _history().leaf_search(Path(ctx["path"]), q, limit=25)
    return jsonify(hits)


# ── docs/120 item 10 — the working-state version, from the top bar ────────
#
# Customer: "move the bookmark button below Calculator, and in its place show
# the current state working version id. Since we're adding Auto-Sync, revert
# back and forth has to be really free. Clicking it lists the version history
# with WHEN each was updated, checkboxes to pick several -> show just the
# combined diff -> and let a chosen state be applied to the live chip."
#
# Deliberately a thin, reachable surface over machinery that already exists:
# the snapshots are State History's, the 2-way diff is the docs/84 workbench's
# front door, an N-way pick is the Compare hub's basket, and applying is
# /state-history/<ts>/restore-live WITH BOTH its independent force gates. The
# value added is that it is one click from every page rather than a navigation.
#
# The version ID is the snapshot TIMESTAMP. Not store.mutation_seq: that resets
# on reload, eviction and restart, so it is a liveness pulse, never an identity.
# Which snapshot is "now" is CONTENT-matched (snapshot_ts_for_current_content),
# never "the newest" -- after an A->B->A cycle the newest snapshot holds the
# wrong content, which is the audit-r10 finding that put that helper there.


def _state_version_now(ctx: dict | None) -> dict:
    """What the top-bar chip shows. Never called from a page render.

    Resolving this hashes the live state+wiring pair, so it rides its own lazy
    endpoint (docs/28: no live reads on a surface that renders on every page).
    """
    out: dict[str, Any] = {"ts": None, "count": 0, "unmatched": False}
    if not ctx or ctx.get("type") != "quam" or not ctx.get("path"):
        return out
    hm = _history()
    try:
        out["count"] = len(hm.list_snapshots(Path(ctx["path"])))
    except Exception:  # noqa: BLE001
        return out
    try:
        out["ts"] = hm.snapshot_ts_for_current_content(Path(ctx["path"]))
    except Exception:  # noqa: BLE001
        out["ts"] = None
    # "No snapshot holds exactly this content" is the ORDINARY mid-edit state,
    # not a fault — say so plainly rather than inventing a nearest match.
    out["unmatched"] = out["ts"] is None and out["count"] > 0
    return out


@bp.route("/state/version")
def state_version_chip():
    """The top-bar version chip (lazy; see _state_version_now)."""
    ctx = _active_ctx()
    # Gate on a chip actually being OPEN, not on a display name — the raw ctx
    # carries no "name" (that is assembled by _ctx for full renders), and using
    # it here made the chip silently render empty on every page.
    return render_template("_state_version_chip.html",
                           ver=_state_version_now(ctx),
                           has_chip=bool(ctx and ctx.get("type") == "quam"
                                         and ctx.get("path")))


@bp.route("/state/versions")
def state_versions_panel():
    """The version list the chip opens: when each was recorded, what produced
    it, and the two things a user wants from it — compare, and go back."""
    ctx = _active_ctx()
    if not ctx or ctx.get("type") != "quam" or not ctx.get("path"):
        return render_template("_state_versions.html", rows=[], ver=_state_version_now(None),
                               chip_key="", archive=True)
    hm = _history()
    path = Path(ctx["path"])
    try:
        snaps = hm.list_snapshots(path)
    except Exception:  # noqa: BLE001
        snaps = []
    try:
        chip_key = Path(hm.resolve_chip_dir(path)[0]).name
    except Exception:  # noqa: BLE001
        chip_key = ""
    ver = _state_version_now(ctx)
    limit = min(_int_arg("limit", 40, minimum=1), 500)
    rows = [{
        "ts": m.timestamp,
        "trigger": m.trigger,
        "label": m.label,
        "note": m.note,
        "pinned": bool(m.pinned),
        "experiment": m.experiment_name,
        "run_id": m.run_id,
        "current": m.timestamp == ver["ts"],
    } for m in snaps[:limit]]
    return render_template("_state_versions.html", rows=rows, ver=ver,
                           chip_key=chip_key, total=len(snaps),
                           archive=(ctx.get("origin") or "live") != "live")


@bp.route("/state/archive", methods=["POST"])
def state_archive():
    """Bookmark/archive the current chip state with a tag + note (feedback #3).

    A manual, force-captured snapshot — PINNED so it's prune-exempt (a durable
    bookmark) — annotated with the user's tag (label) + note. It lands in the same
    snapshot store as State History, so the user views + restores it from there.
    Returns a tiny inline status for the topbar popover form; the response carries
    an ``archive-ok``/``archive-err`` marker the form's hx-on uses to close on success.
    """
    ctx = _active_ctx()
    if not ctx or ctx.get("type") != "quam":
        return '<span class="archive-status archive-err">No state loaded</span>'
    tag = (request.values.get("tag") or "").strip() or None
    note = (request.values.get("note") or "").strip() or None
    # The bookmark captures the LIVE chip (ctx["path"]). If the working copy holds
    # unapplied edits, those are NOT in the bookmark — say so instead of silently
    # capturing live while the user is viewing their edits (audit P2). Record it in the
    # note too so the State-History entry is self-explanatory.
    dirty = _quam_ctx_dirty(ctx)
    if dirty:
        note = ((note + " — ") if note else "") + "captured the LIVE chip; unapplied working-copy edits not included"
    try:
        meta = _history().check_and_snapshot(ctx["path"], "manual", force=True,
                                             project=_scope_for(ctx["path"], ctx))
        if meta is None:
            return '<span class="archive-status archive-err">Could not capture state</span>'
        _history().annotate_snapshot(ctx["path"], meta.timestamp,
                                     label=tag, pinned=True, note=note)
    except Exception as exc:  # noqa: BLE001 — surface any failure inline, never 500 the topbar
        logger.warning("archive bookmark failed", exc_info=True)
        return f'<span class="archive-status archive-err">Archive failed: {exc}</span>'
    label = tag or "untagged"
    # Include the snapshot's own (local-converted) time so a 2nd save with the same tag
    # produces DIFFERENT content — HTMX won't visibly swap byte-identical innerHTML, so
    # repeated saves were indistinguishable (feedback C1). ts_local renders in the user's
    # local time (feedback C2).
    when = current_app.jinja_env.filters["ts_local"](meta.timestamp)
    warn = (' <span class="archive-warn">⚠ live chip — your unapplied edits aren’t '
            'included; apply to live first to bookmark them</span>') if dirty else ''
    return (f'<span class="archive-status archive-ok">✓ Bookmarked &ldquo;{escape(label)}&rdquo; '
            f'at {when} — see State History</span>{warn}')


# ======================================================================
# Instrument Wiring
# ======================================================================


@bp.route("/instrument")
def instrument_view():
    """Render the OPX instrument wiring diagram showing FEM slots and port assignments."""
    engine = _engine()
    if not engine:
        return render_template("_empty_state.html", page="instrument wiring")

    store = _store()
    instrument_error = None
    try:
        instrument_data = engine.get_instrument_wiring()
        instrument_json = json.dumps(instrument_data)
    except Exception as exc:  # noqa: BLE001
        # Surface the failure as a visible banner instead of the empty-rack
        # sentinel — an empty {"controllers": {}} renders as "no wiring data",
        # which wrongly tells the user their chip is unwired and sends them
        # debugging state files instead of reporting a tool bug.
        logger.exception("Failed to build instrument wiring data")
        instrument_json = '{"controllers": {}}'
        instrument_error = str(exc) or exc.__class__.__name__
    wiring_json = _wiring_json()

    template = "_instrument_wiring.html" if _is_htmx() else "instrument_wiring.html"
    return render_template(
        template,
        **_ctx(page="instrument", instrument_json=instrument_json,
               wiring_json=wiring_json, instrument_error=instrument_error),
    )


# ----------------------------------------------------------------------
# Drag-drop preview (read-only) + wiring compare
#
# Drag-drop never yields a real filesystem path (pywebview only injects
# paths through its own DOM bridge, and dropped folders aren't in
# dataTransfer.files), so the browser reads state.json + wiring.json
# *contents* and POSTs them here. We build an in-memory store
# (QuamStore.from_dicts — never registered as a context, never saved) and
# render the same diagram + run the diagnostics linter.
# ----------------------------------------------------------------------

_PREVIEW_MAX_BYTES = 64 * 1024 * 1024


def _preview_problem_ports(findings: list) -> list[dict]:
    """Port keys (for on-diagram highlight) from wiring findings."""
    return [f.port_key for f in findings if f.port_key]


@bp.route("/instrument/preview", methods=["POST"])
def instrument_preview():
    """Read-only wiring preview of a dropped quam_state folder."""
    if request.content_length and request.content_length > _PREVIEW_MAX_BYTES:
        return render_template(
            "_status.html", message="Dropped files are too large to preview", level="error",
        ), 413
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):  # a non-object JSON body (list/scalar) → 400, not 500
        return render_template(
            "_status.html", message="Invalid request body", level="error"), 400
    state = payload.get("state")
    wiring = payload.get("wiring")
    label = str(payload.get("label") or "dropped chip")
    if not isinstance(state, dict) or not isinstance(wiring, dict):
        return render_template(
            "_status.html",
            message="Dropped folder must contain a valid state.json and wiring.json",
            level="error",
        ), 400
    try:
        store = QuamStore.from_dicts(state, wiring)
        instrument_json = json.dumps(QueryEngine(store).get_instrument_wiring())
        wiring_json = json.dumps(store.wiring)
        findings = diagnostics.lint_state(store)
    except Exception:
        logger.exception("Failed to build instrument preview")
        return render_template(
            "_status.html",
            message="Could not read the dropped chip — is it a valid quam_state folder?",
            level="error",
        ), 400
    return render_template(
        "_instrument_preview.html",
        label=label,
        instrument_json=instrument_json,
        wiring_json=wiring_json,
        problems_json=json.dumps(_preview_problem_ports(findings)),
        findings=findings,
        diag_summary=diagnostics.summarize(findings),
        allow_jump=False,
    )


def _compare_port_set(iw: dict) -> dict[str, list[str]]:
    """Map ``"ctrl/fem/port/io"`` → sorted assignment labels, for diffing."""
    out: dict[str, list[str]] = {}
    for ctrl, cdata in (iw.get("controllers") or {}).items():
        for fem, fdata in (cdata.get("fems") or {}).items():
            for kind, io in (("output_ports", "out"), ("input_ports", "in")):
                for port, assigns in (fdata.get(kind) or {}).items():
                    key = f"{ctrl}/{fem}/{port}/{io}"
                    out[key] = sorted(
                        (a.get("label") or f"{a.get('element')}.{a.get('role')}")
                        for a in assigns
                    )
    return out


def _compare_diff_ports(sets: list[dict]) -> list[dict]:
    """Port keys whose assignment signature differs across the compared chips."""
    all_keys: set[str] = set()
    for s in sets:
        all_keys.update(s.keys())
    diff: list[dict] = []
    for k in sorted(all_keys):
        sigs = {tuple(s.get(k, [])) for s in sets}
        if len(sigs) > 1:
            ctrl, fem, port, io = k.split("/")
            diff.append({"ctrl": ctrl, "fem": fem, "port": port, "io": io})
    return diff


@bp.route("/instrument/compare", methods=["POST"])
def instrument_compare():
    """Side-by-side wiring diagrams for 2–3 chips with per-port diff highlighting."""
    if request.content_length and request.content_length > _PREVIEW_MAX_BYTES:
        return render_template(
            "_status.html", message="Dropped files are too large to compare", level="error",
        ), 413
    payload = request.get_json(silent=True)
    chips_in = payload.get("chips") if isinstance(payload, dict) else None
    if not isinstance(chips_in, list) or len(chips_in) < 2:
        return render_template(
            "_status.html", message="Add at least 2 chips to compare", level="warning",
        ), 400
    chips: list[dict] = []
    for i, c in enumerate(chips_in[:3]):
        if not (isinstance(c, dict) and isinstance(c.get("state"), dict)
                and isinstance(c.get("wiring"), dict)):
            continue
        try:
            store = QuamStore.from_dicts(c["state"], c["wiring"])
            iw = QueryEngine(store).get_instrument_wiring()
        except Exception:
            logger.exception("compare: failed to build chip %d", i)
            continue
        chips.append({
            "label": str(c.get("label") or f"chip {i + 1}"),
            "instrument_json": json.dumps(iw),
            "wiring_json": json.dumps(store.wiring),
            "diag": diagnostics.summarize(diagnostics.lint_state(store)),
            "_ports": _compare_port_set(iw),
        })
    if len(chips) < 2:
        return render_template(
            "_status.html", message="Need at least 2 valid chips to compare", level="warning",
        ), 400
    diff_json = json.dumps(_compare_diff_ports([c["_ports"] for c in chips]))
    for c in chips:
        c["diff_json"] = diff_json
        del c["_ports"]
    return render_template("_instrument_compare.html", chips=chips, n=len(chips))


# ======================================================================
# Pulses (first-class pulse management page)
# ======================================================================

# Mutating pulse endpoints only ever touch paths of these two shapes —
# the guard keeps them from becoming arbitrary-dot-path gadgets.
_PULSE_PATH_RES = (
    re.compile(r"^qubits\.[^.]+\.(xy|z|resonator|xy_detuned)\.operations\.[^.]+$"),
    re.compile(
        r"^qubit_pairs\.[^.]+\.macros\.[^.]+\.(flux_pulse_qubit|coupler_flux_pulse)$"),
    # Pair drive-channel ops (CR/ZZ chips): the real CR drive pulses.
    # `zz_drive` vs `zz` is the quam-builder generation rename (docs/54).
    re.compile(
        r"^qubit_pairs\.[^.]+\.(cross_resonance|zz_drive|zz|xy_detuned)"
        r"\.operations\.[^.]+$"),
)

_PULSE_PLOT_MAX_POINTS = 2000


def _is_pulse_path(path: str) -> bool:
    return isinstance(path, str) and any(rx.match(path) for rx in _PULSE_PATH_RES)


def _pulse_plot_traces(payload: dict) -> dict:
    """Plot-ready trace dict from a synth payload (decimated for display)."""
    if not payload.get("ok"):
        return {"ok": False, "error": payload.get("error"),
                "param_errors": payload.get("param_errors") or {}}
    from quam_state_manager.core.waveform_synth import decimate_minmax

    traces = []
    decimated = False
    for name, values in (("I", payload.get("i")), ("Q", payload.get("q"))):
        if values is None:
            continue
        xs, ys, dec = decimate_minmax(values, _PULSE_PLOT_MAX_POINTS)
        decimated = decimated or dec
        traces.append({"name": name, "x": xs, "y": ys})
    return {
        "ok": True,
        "traces": traces,
        "iq": bool(payload.get("iq")),
        "kind": payload.get("kind"),
        "length": payload.get("length"),
        "decimated": decimated,
        "warnings": payload.get("warnings") or [],
    }


@bp.route("/pulses")
def pulses_page():
    """The Pulses library: every pulse on the chip in one flat table."""
    store = _store()
    pulse_index = _pulse_index()
    if not store or not pulse_index:
        return render_template("_empty_state.html", page="pulses")

    channel = request.args.get("channel", "")
    query = request.args.get("q", "").strip()
    page = _int_arg("page", 1, minimum=1)
    per_page = _int_arg("per_page", _DEFAULT_PER_PAGE, minimum=0)  # 0 = "All" (see _paginate)
    rows_only = request.args.get("rows") == "1"

    from quam_state_manager.core.pulse_index import GATE_SLOTS, PAIR_PULSE_CHANNELS

    all_rows = pulse_index.rows()
    # Tab visibility: pair tabs render only when matching rows exist, so CZ
    # chips keep exactly their old tab set and CR chips gain "Pair CR/ZZ"
    # (and drop a dead "Pair flux") without any chip-type sniffing.
    # STRUCTURAL evidence, not row existence: a mid-bringup flux chip whose
    # gate slots are declared-but-null yields zero flux rows, yet its "Pair
    # flux" tab is exactly where the create flow's replace_none_slot path
    # fills them — dropping the tab there would hide the surface.
    has_pair_flux = any(
        r["owner_kind"] == "pair" and r["channel"] in GATE_SLOTS
        for r in all_rows
    ) or any(
        slot in macro
        for pair in (store.merged.get("qubit_pairs") or {}).values()
        if isinstance(pair, dict)
        for macro in (pair.get("macros") or {}).values()
        if isinstance(macro, dict)
        for slot in GATE_SLOTS
    )
    has_pair_drive = any(r["owner_kind"] == "pair"
                         and r["channel"] in PAIR_PULSE_CHANNELS
                         for r in all_rows)
    if channel == "flux":
        # pair-gate flux slots only — pair drive channels have their own tab
        # (this filter used to be `owner_kind == "pair"`, which would silently
        # swallow the CR rows into "Pair flux")
        all_rows = [r for r in all_rows if r["owner_kind"] == "pair"
                    and r["channel"] in GATE_SLOTS]
    elif channel == "pair_drive":
        all_rows = [r for r in all_rows if r["owner_kind"] == "pair"
                    and r["channel"] in PAIR_PULSE_CHANNELS]
    elif channel in ("xy", "z", "resonator", "xy_detuned"):
        all_rows = [r for r in all_rows
                    if r["owner_kind"] == "qubit" and r["channel"] == channel]

    # SERVER-side search across the WHOLE library (not just the current page —
    # the old client filter only saw the 50 rendered rows, so qubits on later
    # pages were unfindable). AND-tokens over owner / op name / class / channel
    # / alias target / summary — purely metadata, no waveform synthesis.
    if query:
        # Shared grammar (docs/96): space = AND, standalone | = OR. A plain
        # query parses to singleton groups == the old every-term all(). The
        # haystack is built once per row now — it used to be re-joined once
        # per TERM (harmless at 0.4-1 ms over real chips, just wasteful).
        from quam_state_manager.core.search_query import groups as _sq_groups
        from quam_state_manager.core.search_query import matches_hay as _sq_match
        grps = _sq_groups(query)
        def _hay(r):
            return " ".join(str(x) for x in (
                r.get("owner"), r.get("op_name"), r.get("class_short"),
                r.get("channel"), r.get("alias_target"), r.get("summary"),
            ) if x).lower()
        all_rows = [r for r in all_rows if _sq_match(_hay(r), grps)]

    page_rows, total, page, total_pages = _paginate(all_rows, page, per_page)

    # Sparklines for the visible page only, memoized per (op, mutation_seq) so
    # repeated search keystrokes / pagination over an unchanged chip never
    # re-synthesize. Aliases / unknown classes render "→ target" instead.
    from quam_state_manager.core.waveform_synth import sparkline_svg, synth_for_operation
    for row in page_rows:
        if row["is_alias"] or not row["known"]:
            row["spark_svg"] = None
            continue
        path = row["path"]
        row["spark_svg"] = pulse_index.sparkline(
            path, lambda p=path: sparkline_svg(synth_for_operation(store, p)))

    if rows_only:
        template = "_pulse_rows.html"
    elif _is_htmx():
        template = "_pulses.html"
    else:
        template = "pulses.html"
    return render_template(
        template,
        **_ctx(
            page="pulses",
            # The sidebar "Add pulse" sub-item lands here with create=1 so the
            # full create form opens straight away (the Pulses page's own
            # "+ New pulse" button is the other entry point).
            open_create=(request.args.get("create") == "1"),
            rows=page_rows,
            active_channel=channel,
            active_query=query,
            current_page=page,
            total_pages=total_pages,
            total=total,
            per_page=per_page,
            has_pair_flux=has_pair_flux,
            has_pair_drive=has_pair_drive,
        ),
    )


@bp.route("/pulse/detail")
def pulse_detail():
    """Inspector detail for one pulse: waveform plot + parameter table."""
    path = request.args.get("path", "").strip()
    return _render_pulse_detail(path)


def _render_pulse_detail(path: str, *, status_msg: str | None = None,
                         status_level: str = "success"):
    """Shared renderer for the pulse detail partial (GET + mutation responses)."""
    store = _store()
    pulse_index = _pulse_index()
    if not store or not pulse_index:
        return render_template("_status.html", message="No state loaded",
                               level="warning")

    if not _is_pulse_path(path):
        return render_template("_status.html",
                               message=f"Not a pulse path: {path!r}",
                               level="error"), 404

    row = next((r for r in pulse_index.rows() if r["path"] == path), None)
    if row is None:
        return render_template("_status.html",
                               message=f"Pulse not found: {path}",
                               level="error"), 404

    from quam_state_manager.core.pulse_catalog import (
        infer_spec_ex, unmodeled_fields)
    from quam_state_manager.core.waveform_synth import synth_for_operation

    payload = synth_for_operation(store, path)
    actual_path = payload.get("alias_of") or path
    alias_chain = [path] if payload.get("alias_of") else []

    # The dict whose fields we list — for aliases, the resolved target's.
    try:
        body = store.get_value(actual_path)
    except (KeyError, TypeError, ValueError, IndexError):
        body = None
    if not isinstance(body, dict):
        body = {}

    # Derived from the ACTUAL body (not the row) so an alias opened here
    # reports its resolved target's class provenance, consistent with the
    # synth payload built from the same dict.
    spec, class_match = infer_spec_ex(
        body, context_slot=actual_path.rsplit(".", 1)[-1])
    unmodeled = (unmodeled_fields(spec, body)
                 if class_match in ("exact", "env", "alias", "leaf") else [])
    pointer_fields = payload.get("pointer_fields") or {}
    resolved_params = payload.get("resolved_params") or {}

    # For each field, the most recent pointer value it was unlinked FROM (the
    # change-log old_value, latest-wins). Lets a just-broken link show a gray
    # "was → #/…" chip + a one-click "re-link to previous" — otherwise the
    # whole pointer-action block vanishes the instant you unlink (feedback #6).
    # Only valid while the change log holds it; /save clears the log, so the
    # affordance is intentionally session-scoped (never claimed to persist).
    prev_links: dict[str, str] = {}
    if store.change_log:
        for entry in store.change_log:           # forward → latest wins
            ov = entry.old_value
            if (isinstance(ov, str) and ov.startswith(("#/", "#./", "#../"))
                    and entry.dot_path.startswith(actual_path + ".")):
                prev_links[entry.dot_path] = ov

    param_rows = []
    for fname, fval in body.items():
        if fname == "__class__":
            continue
        spec_param = spec.param(fname) if spec else None
        ptr_info = pointer_fields.get(fname)
        is_ptr = is_pointer(fval)
        is_runtime = isinstance(fval, str) and fval.startswith(
            ("#./inferred", "#./default_"))
        resolved_value = resolved_params.get(fname, fval)
        is_container = isinstance(fval, (list, dict))
        display = fval
        if is_ptr and ptr_info and ptr_info.get("resolved"):
            display = resolved_value
            if isinstance(resolved_value, (list, dict)):
                # pointer → container: render read-only (unlink would have
                # written the pointer string back onto itself)
                is_container = True
                display = resolved_value
            elif resolved_value == fval and ptr_info.get("target_path"):
                # containers come back nulled from the synth snapshot
                # (_scalar) — re-fetch the real value for display
                try:
                    refetched = store.get_value(ptr_info["target_path"])
                except (KeyError, TypeError, ValueError, IndexError):
                    refetched = None
                if isinstance(refetched, (list, dict)):
                    is_container = True
                    display = refetched
        shared_by: list[str] = []
        if is_ptr and ptr_info and ptr_info.get("resolved") and ptr_info.get("target_path"):
            shared_by = [p for p in pulse_index.used_by(ptr_info["target_path"])
                         if p != f"{actual_path}.{fname}"
                         and not p.startswith(f"{actual_path}.")]
        param_rows.append({
            "key": fname,
            "dot_path": f"{actual_path}.{fname}",
            "raw": fval,
            "value": display,
            "is_pointer": is_ptr,
            "is_runtime": is_runtime,
            "resolved": bool(ptr_info and ptr_info.get("resolved")),
            "target_path": ptr_info.get("target_path") if ptr_info else None,
            "shared_by": shared_by,
            "unit": spec_param.unit if spec_param else "",
            "kind": spec_param.kind if spec_param else "",
            "synth": spec_param.synth if spec_param else True,
            "is_list": is_container,
            # set only when this field is a literal now but was a pointer
            # earlier this session — drives the "re-link to previous" chip
            "prev_link": (None if is_ptr
                          else prev_links.get(f"{actual_path}.{fname}")),
        })

    detail_json = json.dumps({
        "path": path,
        "actual_path": actual_path,
        "qclass": payload.get("qclass") or row.get("qclass"),
        "spec_key": payload.get("spec_key"),
        "plot": _pulse_plot_traces(payload),
    })

    is_qubit_op = bool(_PULSE_PATH_RES[0].match(path))
    used_by_target = pulse_index.used_by(actual_path)
    # The delete button removes `path` (the alias itself when opened via
    # one) — its confirm step must list THAT node's referrers, not the
    # target's, or the real would-dangle set is hidden.
    delete_used_by = (pulse_index.used_by(path) if alias_chain
                      else used_by_target)
    return render_template(
        "_pulse_detail.html",
        path=path,
        actual_path=actual_path,
        row=row,
        spec=spec,
        alias_chain=alias_chain,
        op_name=row["op_name"],
        owner=row["owner"],
        channel=row["channel"],
        class_short=row["class_short"],
        # payload["qclass"] carries the RESOLVED target's __class__ — the
        # alias row's own qclass is None, and the leaf-caution banner must
        # show the chip's real stored path, not "None" (same source as
        # detail_json above).
        qclass=payload.get("qclass") or row.get("qclass"),
        known=row["known"],
        class_match=class_match,
        unmodeled=unmodeled,
        catalog_qclass=spec.qclass if spec else None,
        params=param_rows,
        used_by=used_by_target,
        delete_used_by=delete_used_by,
        synth_error=None if payload.get("ok") else payload.get("error"),
        detail_json=detail_json,
        can_rename=is_qubit_op and not alias_chain,
        status_msg=status_msg,
        status_level=status_level,
    )


def _pulse_mutation_response(detail, *, trigger: bool = True):
    """detail HTML + tray OOB + the pulses-changed table-refresh trigger."""
    if isinstance(detail, tuple):  # error (html, code) passthrough
        return detail
    resp = make_response(detail + "\n" + _tray_oob())
    if trigger:
        resp.headers["HX-Trigger"] = "pulses-changed, diagnostics-changed"
    return resp


@bp.route("/pulse/edit", methods=["POST"])
def pulse_edit():
    """Commit one pulse-parameter edit (instant per-field commit).

    Three explicit modes — never guess what a pointer-valued field means:

    - ``value`` (default): write the parsed value at the field, following
      pointer aliases to the REAL target (shared mutation, disclosed in the
      UI beforehand).
    - ``literal`` (break-link): replace the pointer string itself with a
      typed literal — parsed against the RESOLVED value's type and written
      with ``coerce=False`` so it can't be silently stringified (the
      _type_coerce old-is-str branch would turn 40 into "40").
    - ``pointer`` (re-link): write a syntax-checked pointer string.
    """
    store = _store()
    modifier = _modifier()
    if not store or not modifier:
        return render_template("_status.html", message="No state loaded",
                               level="warning")

    path = request.form.get("path", "").strip()
    dot_path = request.form.get("dot_path", "").strip()
    mode = request.form.get("mode", "value")
    raw_value = request.form.get("value", "")

    # dot_path is "<op_path>.<field>": validate the OP part (which for an
    # alias detail is the resolved target — possibly in another container,
    # e.g. a cross-qubit absolute alias), and require `path` itself to be a
    # pulse path so the re-render target stays constrained.
    if (not _is_pulse_path(path)
            or "." not in dot_path
            or not _is_pulse_path(dot_path.rsplit(".", 1)[0])):
        return render_template("_status.html", message="Invalid pulse path",
                               level="error"), 400

    from quam_state_manager.core.type_policy import parse_value as _parse_value
    from quam_state_manager.core.pointer_path import resolve_field_target

    # Armor: __class__ is never an editable field on this surface (the detail
    # render skips it), so a handcrafted POST targeting it must not write.
    # (`id`/`digital_marker` stay editable here — shipped Pulses behavior.)
    if dot_path.rsplit(".", 1)[-1] == "__class__":
        return render_template("_status.html",
                               message="identity / type key — read-only",
                               level="error"), 400

    try:
        if mode == "pointer":
            value = raw_value.strip()
            if not value.startswith(("#/", "#./", "#../")):
                return render_template(
                    "_status.html",
                    message="A pointer must start with #/, #./ or #../",
                    level="error"), 400
            modifier.set_value(dot_path, value)
        elif mode == "literal":
            # Break-link: type the literal after the RESOLVED value, write
            # uncoerced (the field's CURRENT value is a pointer string).
            target = resolve_field_target(store.merged, dot_path)
            resolved = target.get("resolved_value")
            parsed = _parse_value(raw_value)
            if isinstance(resolved, bool) and not isinstance(parsed, bool):
                parsed = str(parsed).strip().lower() in ("1", "true", "yes", "on")
            elif isinstance(resolved, int) and not isinstance(resolved, bool):
                # Mirror modifier._type_coerce: a non-integral edit to an int field
                # must NOT silently truncate (typing 0.3 to unlink an int-resolved
                # pointer wrote 0 — data-loss). Keep the fractional value as a float.
                as_f = float(parsed)
                parsed = int(as_f) if as_f.is_integer() else as_f
            elif isinstance(resolved, float):
                parsed = float(parsed)
            elif isinstance(resolved, str):
                # Type the literal against a str-resolved pointer too, else an int is
                # written over a str field uncoerced and generate_config gets the
                # wrong type.
                parsed = str(parsed)
            # enforce=False: literal mode IS the explicit, audited type-CHANGE
            # surface — forcing a type-assign ceremony here was rejected.
            modifier.set_value(dot_path, parsed, coerce=False, enforce=False)
        else:  # value — follow pointer aliases to the real write target
            parsed = _parse_value(raw_value)
            if isinstance(parsed, str) and parsed.startswith("#"):
                # Pointer-shaped input in value mode would re-link the
                # RESOLVED TARGET node (a shared node!) — reject and point
                # at the explicit modes instead of guessing.
                return render_template(
                    "_status.html",
                    message=("That looks like a pointer — use the re-link "
                             "button to repoint this field, or unlink first "
                             "to write a literal."),
                    level="error"), 400
            target_path = _resolve_edit_path(store, dot_path)
            raw_current = None
            try:
                raw_current = store.get_value(dot_path)
            except (KeyError, TypeError, ValueError, IndexError):
                pass
            if is_pointer(raw_current):
                target = resolve_field_target(store.merged, dot_path)
                if target.get("resolvable"):
                    # the leaf IS a pointer — write at its resolved target
                    modifier.set_value(target["resolved_path"], parsed)
                else:
                    # dangling pointer: the old value's str type is
                    # meaningless — write the typed literal uncoerced
                    # (coercing would stringify 40 into "40")
                    modifier.set_value(dot_path, parsed, coerce=False)
            else:
                modifier.set_value(target_path, parsed)
    except (KeyError, TypeError, ValueError, IndexError) as exc:
        return render_template("_status.html", message=str(exc),
                               level="error"), 400

    _invalidate_engine_cache()
    return _pulse_mutation_response(_render_pulse_detail(path))


@bp.route("/api/pulse/synth", methods=["POST"])
def api_pulse_synth():
    """Stateless live-preview synthesis. NEVER mutates; always 200 with
    ``ok``/``error`` inside (the client keeps the last good plot)."""
    store = _store()
    payload_in = request.get_json(silent=True) or {}
    path = (payload_in.get("path") or "").strip()
    qclass = (payload_in.get("qclass") or "").strip()
    params = payload_in.get("params") or {}
    if not isinstance(params, dict):
        return jsonify({"ok": False, "error": "params must be an object"})

    from quam_state_manager.core.waveform_synth import (
        synth_for_operation, synthesize)

    if path:
        if not store:
            return jsonify({"ok": False, "error": "No state loaded"})
        if not _is_pulse_path(path):
            return jsonify({"ok": False, "error": f"not a pulse path: {path}"})
        payload = synth_for_operation(store, path, overrides=params)
    elif qclass:
        payload = synthesize(qclass, params)
    else:
        return jsonify({"ok": False, "error": "need path or qclass"})

    return jsonify({
        "ok": payload.get("ok", False),
        "error": payload.get("error"),
        "param_errors": payload.get("param_errors") or {},
        "plot": _pulse_plot_traces(payload),
    })


@bp.route("/api/pulse/compare", methods=["POST"])
def api_pulse_compare():
    """Overlay waveforms for 2–5 selected pulses. Returns per-pulse traces."""
    store = _store()
    if not store:
        return jsonify({"ok": False, "error": "No state loaded"})
    data = request.get_json(silent=True) or {}
    paths = data.get("paths") or []
    if not isinstance(paths, list) or len(paths) < 2:
        return jsonify({"ok": False, "error": "Select at least 2 pulses."})
    if len(paths) > 5:
        return jsonify({"ok": False, "error": "Compare up to 5 pulses at a time."})

    from quam_state_manager.core.waveform_synth import synth_for_operation

    pulses = []
    for path in paths:
        if not _is_pulse_path(path):
            pulses.append({"path": path, "ok": False, "error": f"not a pulse: {path}"})
            continue
        payload = synth_for_operation(store, path)
        plot = _pulse_plot_traces(payload)
        # Derive a short label: owner.op_name from the path
        parts = path.rsplit(".", 1)
        label = parts[-1] if parts else path
        owner_parts = path.split(".operations.")
        if len(owner_parts) == 2:
            label = owner_parts[0].rsplit(".", 1)[-1] + "." + owner_parts[1]
        pulses.append({
            "path": path,
            "label": label,
            "ok": payload.get("ok", False),
            "error": payload.get("error"),
            "plot": plot,
        })
    return jsonify({"ok": True, "pulses": pulses})


def _coerce_catalog_fields(spec, form) -> tuple[dict, dict]:
    """Parse create-form fields per catalog kinds. Pointer strings pass
    verbatim (syntax-checked). Returns (fields, errors)."""
    fields: dict[str, Any] = {}
    errors: dict[str, str] = {}
    for p in spec.params:
        if p.name == "length" and spec.length_mode != "explicit":
            continue
        raw = form.get(p.name)
        if raw is None or raw == "":
            if p.required and p.default is None:
                errors[p.name] = "required"
            continue
        raw = raw.strip() if isinstance(raw, str) else raw
        if isinstance(raw, str) and raw.startswith("#"):
            if not raw.startswith(("#/", "#./", "#../")):
                errors[p.name] = "malformed pointer"
            else:
                fields[p.name] = raw
            continue
        try:
            if p.kind == "int":
                fields[p.name] = int(float(raw))
            elif p.kind == "float":
                fields[p.name] = float(raw)
            elif p.kind == "bool":
                fields[p.name] = str(raw).strip().lower() in (
                    "1", "true", "yes", "on")
            elif p.kind == "list_float":
                fields[p.name] = [float(v) for v in str(raw).replace(
                    "[", "").replace("]", "").split(",") if v.strip()]
            else:
                fields[p.name] = raw
        except (TypeError, ValueError):
            errors[p.name] = f"cannot parse {raw!r} as {p.kind}"
    return fields, errors


@bp.route("/pulse/new")
def pulse_create_form():
    """The create-pulse form (inspector pane)."""
    store = _store()
    engine = _engine()
    if not store or not engine:
        return render_template("_status.html", message="No state loaded",
                               level="warning")

    from quam_state_manager.core.pulse_catalog import PULSE_CATALOG, chip_qclass

    # r15 (docs/71 §2): the selected env's pulse roster drives the form —
    # roster-only classes become creatable (synthesized specs), catalog
    # classes get an importability verdict, and the strip on top names the
    # env + class count (or says "static catalog" honestly).
    from quam_state_manager.core.pulse_catalog import (env_creatable_specs,
                                                      env_overlay_active)
    roster = env_overlay_active()
    env_specs = env_creatable_specs(roster)

    creatable = [s for s in PULSE_CATALOG.values() if s.creatable]
    groups: dict[str, list] = {}
    for s in creatable:
        groups.setdefault(s.group, []).append(s)
    for s in env_specs.values():
        groups.setdefault(s.group, []).append(s)

    # The __class__ each type would write on THIS chip (+ provenance) — the
    # form shows it (read-only since r15) with a caution when it is a guess
    # ("prefix"/"catalog") rather than evidence ("reused"/"env").
    chip_classes = {s.key: chip_qclass(store.merged, s)
                    for s in [*creatable, *env_specs.values()]}

    from quam_state_manager.core.pulse_index import PAIR_PULSE_CHANNELS, PULSE_CHANNELS

    qubit_names = [q.get("id") for q in engine.list_qubits() if q.get("id")]
    pairs_map: dict[str, list[str]] = {}
    pair_channels_map: dict[str, list[str]] = {}
    # r15 (docs/71 §3) — the CZ-first pair island: per pair, control/target
    # with their frequencies (control = higher f₀₁ is the CZ convention —
    # the form shows a ⚠ when the stored orientation contradicts it; the
    # wizard's czAutoOrient + run_build._cz_order_warning are the write-side
    # twins), per-gate SLOT occupancy (held slots are disabled with an
    # edit-instead deep link — the server's 409 stays the backstop), and the
    # arch+roster-gated "+ new gate" variants.
    pairs_info: dict[str, dict[str, Any]] = {}
    env_gates = _env_gate_types()
    for pair_name, pair in (store.merged.get("qubit_pairs") or {}).items():
        if not isinstance(pair, dict):
            continue
        macros = pair.get("macros")
        if isinstance(macros, dict):
            # Flux-slot targets: never offer CR/Stark macros — their drive
            # lives on the pair's cross_resonance/zz channel, and inserting a
            # flux_pulse_qubit would corrupt the macro schema.
            gates = [
                g for g, m in macros.items()
                if isinstance(m, dict)
                and cr_semantics.classify_class(m.get("__class__"))[0]
                not in ("cr_gate", "stark_cz_gate")
            ]
            if gates:
                pairs_map[pair_name] = gates
        channels = [
            ch for ch in PAIR_PULSE_CHANNELS
            if isinstance(pair.get(ch), dict)
            and isinstance(pair[ch].get("operations"), dict)
        ]
        if channels:
            pair_channels_map[pair_name] = channels

        try:
            p = engine.get_pair(pair_name)
        except KeyError:
            p = {}
        info: dict[str, Any] = {
            "control": p.get("qubit_control"),
            "target": p.get("qubit_target"),
            "gates": {},
        }
        for role, qn in (("f_control", p.get("qubit_control")),
                         ("f_target", p.get("qubit_target"))):
            f = None
            if qn:
                try:
                    q = engine.get_qubit(qn)
                    f = q.get("f_01")
                    if not isinstance(f, (int, float)):
                        f = q.get("xy_RF_frequency")
                except KeyError:
                    pass
            info[role] = f if isinstance(f, (int, float)) else None
        fc, ft = info["f_control"], info["f_target"]
        # strict fc > ft mirrors czAutoOrient; either freq missing ⇒ no ⚠
        info["orient_ok"] = (fc > ft) if (fc is not None and ft is not None) \
            else None
        if isinstance(macros, dict):
            for g, m in macros.items():
                if not isinstance(m, dict):
                    continue
                if cr_semantics.classify_class(m.get("__class__"))[0] in (
                        "cr_gate", "stark_cz_gate"):
                    continue
                slots: dict[str, dict[str, Any]] = {}
                for slot in ("flux_pulse_qubit", "coupler_flux_pulse"):
                    v = m.get(slot)
                    if isinstance(v, dict):
                        leaf = (v.get("__class__") or "").rsplit(".", 1)[-1]
                        slots[slot] = {
                            "state": "held",
                            "class": leaf or "SquarePulse (implicit)",
                            "path": (f"qubit_pairs.{pair_name}.macros"
                                     f".{g}.{slot}"),
                        }
                    else:
                        # absent key or present-but-None — both creatable
                        # (the POST's replace_none_slot path handles None)
                        slots[slot] = {"state": "empty", "class": None,
                                       "path": None}
                info["gates"][g] = {"slots": slots}
        arch = _pair_arch(store, pair)
        if arch.get("flux"):
            new_gates = [gid for gid in ("cz_unipolar", "cz_flattop")
                         ] + sorted(env_gates)
        else:
            new_gates = []
        info["new_gates"] = new_gates
        pairs_info[pair_name] = info

    gate_defs_json = json.dumps({
        gid: {"label": gdef["label"],
              "has_coupler_slot": (_SLOT_LEAVES.get(gid) or {}).get("coupler")
              is not None}
        for gid, gdef in {**_GATE_TYPES, **_ENV_GATE_TYPES}.items()
        if gdef.get("arch") == "flux" and gid != "cz_parametric"
    })

    # existing op names per target, for client-side duplicate validation
    existing: dict[str, list[str]] = {}
    has_xy_detuned = False
    for qubit_name, qubit in (store.merged.get("qubits") or {}).items():
        if not isinstance(qubit, dict):
            continue
        for channel in PULSE_CHANNELS:
            chan = qubit.get(channel)
            ops = chan.get("operations") if isinstance(chan, dict) else None
            if isinstance(ops, dict):
                existing[f"{qubit_name}/{channel}"] = sorted(ops.keys())
                if channel == "xy_detuned":
                    has_xy_detuned = True
    for pair_name, channels in pair_channels_map.items():
        pair = (store.merged.get("qubit_pairs") or {}).get(pair_name) or {}
        for channel in channels:
            ops = (pair.get(channel) or {}).get("operations")
            if isinstance(ops, dict):
                existing[f"pair:{pair_name}/{channel}"] = sorted(ops.keys())

    def _cat_entry(s, *, env_only=False):
        # verify: None = no roster installed (no verdict possible);
        # "env" = the selected env imports this class; "missing" = it does
        # NOT (creating it writes a state that env can't Quam.load — the
        # form confirms before submitting, the POST 409s as backstop).
        verify = None
        if roster is not None:
            verify = "env" if s.key in roster else "missing"
        d = {
            "label": s.label, "group": s.group, "doc": s.doc,
            "iq": s.iq, "length_mode": s.length_mode,
            "channels": list(s.channels),
            "verify": verify,
            "qclass": chip_classes[s.key][0],
            "qclass_how": chip_classes[s.key][1],
            "params": [
                {"name": p.name, "label": p.label, "kind": p.kind,
                 "default": p.default, "unit": p.unit, "synth": p.synth,
                 "required": p.required}
                for p in s.params
            ],
        }
        if env_only:
            d["env_only"] = True
        return d

    catalog_json = json.dumps({
        **{s.key: _cat_entry(s) for s in creatable},
        **{s.key: _cat_entry(s, env_only=True) for s in env_specs.values()},
    })

    # Optional preselection (the qubit-detail "Add pulse" button passes these
    # so the form opens already targeted at that qubit + channel).
    sel_qubit = request.args.get("qubit", "").strip()
    sel_channel = request.args.get("channel", "").strip()
    return render_template(
        "_pulse_create.html",
        groups=groups,
        qubit_names=qubit_names,
        pairs_map=pairs_map,
        pair_channels_map=pair_channels_map,
        has_xy_detuned=has_xy_detuned,
        existing_json=json.dumps(existing),
        catalog_json=catalog_json,
        sel_qubit=sel_qubit if sel_qubit in qubit_names else "",
        sel_channel=(sel_channel
                     if sel_channel in ("xy", "z", "resonator", "xy_detuned")
                     else ""),
        env_card=_env_card_state(store),
        env_class_count=len(roster or {}),
        pairs_info_json=json.dumps(pairs_info),
        gate_defs_json=gate_defs_json,
        pairs_all=list(pairs_info),
    )


@bp.route("/pulse/new/env-strip")
def pulse_env_strip():
    """The add-pulse form's env-discovery strip (r15, docs/71 §2) — states:
    no env / interpreter gone / not probed [Probe now] / probing (self-poll)
    / ✓ N classes from <env>. Reuses the diagnostics env-card state + the
    existing POST /diagnostics/env-probe; zero new probe machinery."""
    store = _store()
    if not store:
        return ""
    from quam_state_manager.core.pulse_catalog import env_overlay_active
    return render_template(
        "_pulse_env_strip.html",
        env_card=_env_card_state(store),
        env_class_count=len(env_overlay_active() or {}),
    )


@bp.route("/api/pulse/create", methods=["POST"])
def api_pulse_create():
    """Create a new pulse on a qubit channel or a pair-gate flux slot."""
    store = _store()
    modifier = _modifier()
    if not store or not modifier:
        return render_template("_status.html", message="No state loaded",
                               level="warning")

    from quam_state_manager.core.pulse_catalog import (PULSE_CATALOG,
                                                       env_creatable_specs,
                                                       env_overlay_active)

    pulse_type = request.form.get("pulse_type", "").strip()
    roster = env_overlay_active()
    spec = PULSE_CATALOG.get(pulse_type)
    if spec is None or not spec.creatable:
        # r15 (docs/71 §2): roster-only classes are creatable via their
        # synthesized specs (e.g. quam_builder 0.4.0's CosineBipolarPulse).
        spec = env_creatable_specs(roster).get(pulse_type)
    if spec is None or not spec.creatable:
        hint = (" (the selected environment may have changed since this "
                "form was opened — reload it)" if roster is not None else "")
        return render_template("_status.html",
                               message=f"Unknown pulse type {pulse_type!r}{hint}",
                               level="error"), 400

    # Never-silent env-compat gate (r15, docs/71 §2): with a roster installed,
    # a class the selected env can NOT import writes a state that env will
    # fail to Quam.load. The form pre-confirms and re-submits with force=1;
    # this 409 is the backstop for un-wired callers.
    if (roster is not None and spec.key not in roster
            and request.form.get("force") != "1"):
        return render_template(
            "_status.html",
            message=(f"{spec.key} is not importable in the selected "
                     "environment — a state carrying it will not Quam.load "
                     "there. Re-submit with force=1 to create it anyway "
                     "(the form asks for this confirmation automatically)."),
            level="error"), 409

    # Optional explicit class path (the create form surfaces the derived one
    # as an editable field). The LEAF must equal the chosen spec — otherwise
    # the form's field schema and the written class cross-wire.
    qclass = (request.form.get("qclass") or "").strip()
    if qclass:
        if (not _QCLASS_RE.match(qclass)
                or qclass.rsplit(".", 1)[-1] != spec.key):
            return render_template(
                "_status.html",
                message=(f"Class path {qclass!r} does not name a "
                         f"{spec.key} (the last segment must match the "
                         "selected pulse type)"),
                level="error"), 400

    fields, errors = _coerce_catalog_fields(spec, request.form)
    if errors:
        first = next(iter(errors.items()))
        return render_template("_status.html",
                               message=f"Invalid {first[0]}: {first[1]}",
                               level="error"), 400

    target_kind = request.form.get("target_kind", "qubit")
    # Check-and-create under one lock hold (same pattern as delete/rename) —
    # a concurrent mutator must not occupy the slot/name between the
    # existence check and the write. Modifier methods re-enter the RLock;
    # the (synth-heavy) detail render happens after release.
    env_dropped: list[str] = []
    with store._lock:
        outcome = _pulse_create_locked(store, modifier, spec, fields,
                                       target_kind, qclass=qclass or None,
                                       env_dropped_out=env_dropped)
    if not isinstance(outcome, str):
        return outcome  # an error response (html, code)
    dot_path = outcome
    _invalidate_engine_cache()
    logger.info("pulse create %s (%s)%s", dot_path, pulse_type,
                f" env-dropped={env_dropped}" if env_dropped else "")
    msg = f"Created {dot_path.rsplit('.', 1)[-1]}"
    if env_dropped:
        # docs/98: fields the selected env's class model doesn't know were
        # removed before the write (they would make Quam.load fail there).
        msg += (" — omitted " + ", ".join(env_dropped)
                + " (not in the selected environment's "
                + f"{spec.key} model)")
    return _pulse_mutation_response(_render_pulse_detail(
        dot_path, status_msg=msg))


def _pulse_create_locked(store, modifier, spec, fields, target_kind,
                         qclass: str | None = None,
                         env_dropped_out: list | None = None):
    """Validate the target and insert the pulse. Caller holds store._lock.

    Returns the created dot_path on success, or an (html, status) error
    response tuple. *qclass* (already leaf-validated) overrides the written
    ``__class__``; absent, it is derived from the chip's own pulses
    (``chip_qclass``) so a path-churned stack gets its real module path.
    """
    from quam_state_manager.core.pulse_catalog import build_template, chip_qclass

    from quam_state_manager.core.pulse_index import PAIR_PULSE_CHANNELS

    replace_none_slot = False
    new_gate_template: dict | None = None
    new_gate_name = ""
    if target_kind == "pair":
        pair = request.form.get("pair", "").strip()
        gate = request.form.get("gate", "").strip()
        slot = request.form.get("slot", "").strip()
        if slot not in ("flux_pulse_qubit", "coupler_flux_pulse"):
            return render_template("_status.html", message="Invalid slot",
                                   level="error"), 400
        macros = ((store.merged.get("qubit_pairs") or {}).get(pair) or {}).get("macros")
        if gate.startswith("__new__:"):
            # r15 (docs/71 §3): CZ-first — create the gate macro AND its
            # selected slot's pulse in ONE create_subtree (one lock hold,
            # one Review entry, one Ctrl+Z). The macro's other fields take
            # the gate type's defaults; slot classes are evidence/roster
            # verified via _slot_qclasses_for (never guessed).
            gate_type = gate.split(":", 1)[1]
            new_gate_name = (request.form.get("new_gate_name") or "").strip()
            all_types = {**_GATE_TYPES, **_ENV_GATE_TYPES}
            gdef = all_types.get(gate_type)
            if gdef is None or gdef.get("arch") != "flux":
                return render_template("_status.html",
                                       message=f"Unknown gate type {gate_type!r}",
                                       level="error"), 400
            if (gate_type in _ENV_GATE_TYPES
                    and gate_type not in _env_gate_types()):
                return render_template(
                    "_status.html",
                    message=(f"{gate_type} needs pulse classes the selected "
                             "environment does not verify."),
                    level="error"), 409
            if not _GATE_NAME_RE.match(new_gate_name):
                return render_template(
                    "_status.html",
                    message=("Gate name must start with a letter "
                             "(letters/digits/_, max 64)."),
                    level="error"), 400
            pair_obj = (store.merged.get("qubit_pairs") or {}).get(pair)
            if not isinstance(pair_obj, dict):
                return render_template("_status.html",
                                       message=f"Unknown pair: {pair!r}",
                                       level="error"), 404
            if new_gate_name in (macros or {}):
                return render_template(
                    "_status.html",
                    message=f"Gate {new_gate_name!r} already exists on {pair}.",
                    level="error"), 409
            if not _pair_arch(store, pair_obj).get("flux"):
                return render_template(
                    "_status.html",
                    message=("This pair's architecture has no flux line — "
                             "a flux-CZ macro would corrupt it."),
                    level="error"), 409
            if (slot == "coupler_flux_pulse"
                    and (_SLOT_LEAVES.get(gate_type) or {}).get("coupler")
                    is None):
                return render_template(
                    "_status.html",
                    message=(f"{gate_type} carries the whole gate on the "
                             "qubit line — it has no coupler slot."),
                    level="error"), 400
            defaults = {f[0]: f[2] for f in gdef["fields"]}
            new_gate_template = _build_gate_template(
                gate_type, defaults,
                slot_qclasses=_slot_qclasses_for(store, gate_type))
            dot_path = f"qubit_pairs.{pair}.macros.{new_gate_name}"
        else:
            macro = macros.get(gate) if isinstance(macros, dict) else None
            if not isinstance(macro, dict):
                return render_template("_status.html",
                                       message=f"Gate {gate!r} not found on {pair!r}",
                                       level="error"), 404
            # Never write a flux slot into a CR/Stark macro — the gate's
            # drive lives on the pair's cross_resonance/zz channel; a
            # flux_pulse_qubit here corrupts the macro's schema.
            if cr_semantics.classify_class(macro.get("__class__"))[0] in (
                    "cr_gate", "stark_cz_gate"):
                return render_template(
                    "_status.html",
                    message=(f"{gate!r} is a CR/Stark gate — it takes no flux "
                             "pulse. Create the pulse on the pair's "
                             "cross-resonance / ZZ channel instead."),
                    level="error"), 409
            if slot in macro and macro[slot] is not None:
                return render_template(
                    "_status.html",
                    message=f"{pair}.{gate}.{slot} already holds a pulse",
                    level="error"), 409
            replace_none_slot = slot in macro  # present-but-None → set
            dot_path = f"qubit_pairs.{pair}.macros.{gate}.{slot}"
    elif target_kind == "pair_channel":
        pair = request.form.get("pc_pair", "").strip()
        channel = request.form.get("pc_channel", "").strip()
        op_name = request.form.get("op_name", "").strip()
        if channel not in PAIR_PULSE_CHANNELS:
            return render_template("_status.html", message="Invalid channel",
                                   level="error"), 400
        if not _PULSE_NAME_RE.match(op_name):
            return render_template(
                "_status.html",
                message="Name must start with a letter (letters/digits/_, max 64)",
                level="error"), 400
        pair_obj = (store.merged.get("qubit_pairs") or {}).get(pair)
        chan = pair_obj.get(channel) if isinstance(pair_obj, dict) else None
        ops = chan.get("operations") if isinstance(chan, dict) else None
        # The channel itself is never created here — a null zz_drive means the
        # chip wasn't built/wired for it; fabricating the channel dict would
        # produce a state quam can't map onto its component classes.
        if not isinstance(ops, dict):
            return render_template(
                "_status.html",
                message=(f"{pair!r} has no {channel}.operations dict — "
                         "this pair channel cannot hold pulses yet"),
                level="error"), 400
        if op_name in ops:
            return render_template("_status.html",
                                   message=f"Operation {op_name!r} already exists",
                                   level="error"), 409
        dot_path = f"qubit_pairs.{pair}.{channel}.operations.{op_name}"
    else:
        qubit = request.form.get("qubit", "").strip()
        channel = request.form.get("channel", "").strip()
        op_name = request.form.get("op_name", "").strip()
        if channel not in ("xy", "z", "resonator", "xy_detuned"):
            return render_template("_status.html", message="Invalid channel",
                                   level="error"), 400
        if not _PULSE_NAME_RE.match(op_name):
            return render_template(
                "_status.html",
                message="Name must start with a letter (letters/digits/_, max 64)",
                level="error"), 400
        qubit_obj = (store.merged.get("qubits") or {}).get(qubit)
        chan = qubit_obj.get(channel) if isinstance(qubit_obj, dict) else None
        ops = chan.get("operations") if isinstance(chan, dict) else None
        if not isinstance(ops, dict):
            return render_template(
                "_status.html",
                message=(f"{qubit!r} has no {channel}.operations dict — "
                         "this channel cannot hold pulses yet"),
                level="error"), 400
        if op_name in ops:
            return render_template("_status.html",
                                   message=f"Operation {op_name!r} already exists",
                                   level="error"), 409
        dot_path = f"qubits.{qubit}.{channel}.operations.{op_name}"

    if not qclass:
        qclass, _how = chip_qclass(store.merged, spec)
    template = build_template(spec, fields, qclass=qclass)
    # docs/98: never write a field the SELECTED env's class model doesn't
    # know — quam treats an unknown attribute as a hard Quam.load failure
    # (field renames between stack generations, e.g. post_zero_padding_length
    # → padding_length). No roster / unprobed fields ⇒ no-op.
    from quam_state_manager.core.pulse_catalog import env_field_filter
    _dropped = env_field_filter(template, spec.key)
    if env_dropped_out is not None:
        env_dropped_out.extend(_dropped)
    try:
        if new_gate_template is not None:
            # r15 (docs/71 §3): the new gate macro + its configured slot
            # pulse land as ONE subtree — one lock hold, one Review entry.
            new_gate_template[request.form.get("slot", "").strip()
                              or "flux_pulse_qubit"] = template
            modifier.create_subtree(dot_path, new_gate_template)
            dot_path = (f"{dot_path}."
                        f"{request.form.get('slot', '').strip() or 'flux_pulse_qubit'}")
        elif replace_none_slot:
            modifier.set_value(dot_path, template, coerce=False)
            # docs/98: set_value indexes only the slot path itself — a dict
            # replacing an explicit-null slot leaves its LEAVES unsearchable
            # (and later edits warn "not found in index"). Mirror
            # create_subtree's per-leaf indexing for the new subtree.
            _si = getattr(store, "search_index", None)
            if _si is not None:
                for _k, _v in template.items():
                    if not isinstance(_v, (dict, list)):
                        _si.add_entry(f"{dot_path}.{_k}", _v)
        else:
            modifier.create_subtree(dot_path, template)
    except (KeyError, ValueError, TypeError, IndexError) as exc:
        return render_template("_status.html", message=str(exc),
                               level="error"), 400
    return dot_path


@bp.route("/api/pulse/delete", methods=["POST"])
def api_pulse_delete():
    """Delete a pulse. Inbound references block unless force=1 (the UI shows
    the used_by list in its confirm step and posts force)."""
    store = _store()
    modifier = _modifier()
    pulse_index = _pulse_index()
    if not store or not modifier or not pulse_index:
        return render_template("_status.html", message="No state loaded",
                               level="warning")

    path = request.form.get("path", "").strip()
    force = request.form.get("force") == "1"
    if not _is_pulse_path(path):
        return render_template("_status.html", message="Invalid pulse path",
                               level="error"), 400

    # Check-and-delete under one lock hold so a concurrent edit can't add an
    # inbound pointer between the used_by check and the pop.
    with store._lock:
        referrers = pulse_index.used_by(path)
        if referrers and not force:
            return render_template(
                "_status.html",
                message=("Refusing to delete: referenced by "
                         + ", ".join(referrers)
                         + ". Use the confirm step to delete anyway."),
                level="error"), 409
        try:
            modifier.delete_subtree(path)
        except (KeyError, ValueError) as exc:
            return render_template("_status.html", message=str(exc),
                                   level="error"), 404

    _invalidate_engine_cache()
    logger.info("pulse delete %s (forced=%s, referrers=%d)",
                path, force, len(referrers))
    note = f"Deleted {path.rsplit('.', 1)[-1]}"
    if referrers:
        note += f" — {len(referrers)} reference(s) now dangle"
    detail = render_template("_status.html", message=note, level="success")
    return _pulse_mutation_response(detail)


@bp.route("/api/pulse/duplicate", methods=["POST"])
def api_pulse_duplicate():
    """Duplicate a qubit operation under a new name (pointer-correct copy)."""
    store = _store()
    modifier = _modifier()
    if not store or not modifier:
        return render_template("_status.html", message="No state loaded",
                               level="warning")

    path = request.form.get("path", "").strip()
    new_name = (request.form.get("new_name") or "").strip()
    # Channel operations only (qubit channels + pair CR/ZZ drive channels) —
    # a gate FLUX SLOT (flux_pulse_qubit) is schema, not a named op: renaming
    # or duplicating it would corrupt the macro shape.
    if not (_PULSE_PATH_RES[0].match(path) or _PULSE_PATH_RES[2].match(path)):
        return render_template(
            "_status.html",
            message="Duplicate applies to channel operations only",
            level="error"), 400
    if not _PULSE_NAME_RE.match(new_name):
        return render_template(
            "_status.html",
            message="Name must start with a letter (letters/digits/_, max 64)",
            level="error"), 400

    from quam_state_manager.core.pulse_index import rewrite_subtree_pointers

    parent, _ = path.rsplit(".", 1)
    new_path = f"{parent}.{new_name}"
    # read + rewrite + create under one lock hold (consistent snapshot)
    with store._lock:
        try:
            body = store.get_value(path)
        except (KeyError, TypeError, ValueError, IndexError):
            return render_template("_status.html", message=f"Not found: {path}",
                                   level="error"), 404

        rewritten = rewrite_subtree_pointers(body, path, new_path)
        try:
            modifier.create_subtree(new_path, rewritten)
        except KeyError:
            return render_template("_status.html",
                                   message=f"{new_name!r} already exists",
                                   level="error"), 409

    _invalidate_engine_cache()
    logger.info("pulse duplicate %s -> %s", path, new_path)
    return _pulse_mutation_response(_render_pulse_detail(
        new_path, status_msg=f"Duplicated as {new_name}"))


@bp.route("/api/pulse/rename", methods=["POST"])
def api_pulse_rename():
    """Rename a qubit operation; inbound pointers are re-targeted by default
    (each rewrite is a normal change-log entry — undoable, replayable)."""
    store = _store()
    modifier = _modifier()
    pulse_index = _pulse_index()
    if not store or not modifier or not pulse_index:
        return render_template("_status.html", message="No state loaded",
                               level="warning")

    path = request.form.get("path", "").strip()
    new_name = (request.form.get("new_name") or "").strip()
    retarget = request.form.get("retarget", "1") == "1"
    # Channel operations only — see api_pulse_duplicate. Retarget-on-rename is
    # what keeps the target-xy cancellation stubs' pointers into a renamed CR
    # drive op from silently dangling.
    if not (_PULSE_PATH_RES[0].match(path) or _PULSE_PATH_RES[2].match(path)):
        return render_template(
            "_status.html",
            message="Rename applies to channel operations only",
            level="error"), 400
    if not _PULSE_NAME_RE.match(new_name):
        return render_template(
            "_status.html",
            message="Name must start with a letter (letters/digits/_, max 64)",
            level="error"), 400

    from quam_state_manager.core.pulse_index import (
        rewrite_referrer_pointer, rewrite_subtree_pointers)

    parent, old_name = path.rsplit(".", 1)
    new_path = f"{parent}.{new_name}"

    retargeted = 0
    with store._lock:
        referrers = pulse_index.used_by(path)
        try:
            body = store.get_value(path)
        except (KeyError, TypeError, ValueError, IndexError):
            return render_template("_status.html", message=f"Not found: {path}",
                                   level="error"), 404
        rewritten = rewrite_subtree_pointers(body, path, new_path)
        # One group id for the rename AND its pointer retargets → a single Ctrl+Z
        # undoes the whole rename as one action.
        gid = modifier.new_group_id()
        try:
            modifier.rename_subtree(path, new_path, new_value=rewritten, group_id=gid)
        except KeyError as exc:
            return render_template("_status.html", message=str(exc),
                                   level="error"), 409
        if retarget:
            for referrer in referrers:
                try:
                    raw = store.get_value(referrer)
                except (KeyError, TypeError, ValueError, IndexError):
                    continue
                new_ptr = rewrite_referrer_pointer(raw, referrer, path, new_path)
                if new_ptr and new_ptr != raw:
                    modifier.set_value(referrer, new_ptr, group_id=gid)
                    retargeted += 1

    _invalidate_engine_cache()
    logger.info("pulse rename %s -> %s (retargeted=%d)", path, new_path, retargeted)
    note = f"Renamed {old_name} → {new_name}"
    if retargeted:
        note += f" · {retargeted} reference(s) re-pointed"
    elif referrers and not retarget:
        note += f" · {len(referrers)} reference(s) now dangle"
    return _pulse_mutation_response(_render_pulse_detail(
        new_path, status_msg=note))


def _config_op_for_pulse_path(config: dict, path: str,
                              state: dict | None = None) -> tuple[str | None, str | None]:
    """Map a state-file pulse path to a (element_key_or_prefix, op_name) in the
    generated config.

    Qubit ops are exact: ``qubits.<q>.<ch>.operations.<op>`` → element
    ``<q>.<ch>``, operation ``<op>``. Pair drive-channel ops map through the
    channel's own ``id`` in *state* (quam-builder names the element after it:
    ``cr_q1_q2`` / ``zz_q1_q2`` — dot-less keys). Pair-gate flux pulses are
    registered by quam-builder on the CONTROL qubit's z element under generated
    names like ``cz_unipolar_pulse_qA1`` / ``cz_SNZ_flux_pulse_qA2-qA1`` — we
    scan all elements for op names starting with the gate name and disambiguate
    by pair/qubit hints; ambiguous or missing → (None, None).
    """
    m = _PULSE_PATH_RES[0].match(path)
    if m:
        parts = path.split(".")
        return f"{parts[1]}.{parts[2]}", parts[4]

    m = _PULSE_PATH_RES[2].match(path)
    if m:
        parts = path.split(".")
        pair_name, channel, op = parts[1], parts[2], parts[4]
        chan = (((state or {}).get("qubit_pairs") or {}).get(pair_name) or {}
                ).get(channel)
        elem_id = chan.get("id") if isinstance(chan, dict) else None
        if isinstance(elem_id, str) and elem_id:
            return elem_id, op
        return None, None

    m = _PULSE_PATH_RES[1].match(path)
    if not m:
        return None, None
    parts = path.split(".")
    pair_name, gate, slot = parts[1], parts[3], parts[4]
    # r16 0-1: derive members from the pair's REFS (cr_semantics doctrine —
    # never split the id: "q1-2" yields the bare token "2", which as a
    # substring hint matched nearly every op name → multi-candidate → the
    # Config-Viewer link silently vanished). Fallback re-prefixes short
    # tokens and drops bare digits from the hint set.
    _pair_obj = ((state or {}).get("qubit_pairs") or {}).get(pair_name)
    _eps = cr_semantics.pair_endpoints(_pair_obj)
    pair_qubits = [q for q in _eps if q]
    if not pair_qubits:
        pair_qubits = [(t if t[:1].isalpha() else f"q{t}")
                       for t in pair_name.split("-") if t]
    wants_coupler = slot == "coupler_flux_pulse"

    candidates: list[tuple[str, str]] = []
    for elem_key, elem in (config.get("elements") or {}).items():
        ops = elem.get("operations") if isinstance(elem, dict) else None
        if not isinstance(ops, dict):
            continue
        for op_name in ops:
            if not op_name.startswith(gate):
                continue
            is_coupler = "coupler" in op_name
            if is_coupler != wants_coupler:
                continue
            # prefer ops that mention the pair (or one of its qubits)
            if pair_name in op_name or any(q in op_name for q in pair_qubits):
                candidates.append((elem_key, op_name))
    if len(candidates) == 1:
        return candidates[0]
    if len(candidates) > 1:
        # several matches (multi-neighbor gates) — prefer one naming the pair
        exact = [c for c in candidates if pair_name in c[1]]
        if len(exact) == 1:
            return exact[0]
    return None, None


@bp.route("/api/pulse/ground-truth")
def api_pulse_ground_truth():
    """Ground-truth waveform for a pulse from the cached generated config,
    plus staleness info and a server-side synth-vs-truth comparison."""
    store = _store()
    if not store:
        return jsonify({"ok": False, "status": "no-state",
                        "error": "No state loaded"}), 400

    path = request.args.get("path", "").strip()
    if not _is_pulse_path(path):
        return jsonify({"ok": False, "status": "bad-path",
                        "error": f"not a pulse path: {path}"}), 404

    cfg = store.generated_config
    meta = store.generated_config_meta or {}
    if cfg is None:
        return jsonify({
            "ok": False, "status": "absent",
            "error": ("No config has been generated for this chip yet — "
                      "generate one to compare against."),
        }), 409

    from quam_state_manager.core import config_view
    from quam_state_manager.core.waveform_synth import synth_for_operation

    elem_or_prefix, op_name = _config_op_for_pulse_path(cfg, path, store.merged)
    if op_name is None:
        return jsonify({
            "ok": False, "status": "not-found",
            "error": ("This pulse isn't in the cached config — it was likely "
                      "created/renamed/duplicated after the config was generated. "
                      "Regenerate to include it."),
        }), 404

    # The qubit-op matcher returns op_name straight from the path without
    # consulting the config, so an op the config has never heard of would
    # otherwise masquerade as "no-trace". Verify the element actually carries
    # the op before deciding: absent ⇒ not-found, present-but-empty ⇒ no-trace.
    cfg_elem = (cfg.get("elements") or {}).get(elem_or_prefix)
    cfg_ops = cfg_elem.get("operations") if isinstance(cfg_elem, dict) else None
    if not isinstance(cfg_ops, dict) or op_name not in cfg_ops:
        return jsonify({
            "ok": False, "status": "not-found",
            "error": ("This pulse isn't in the cached config — it was likely "
                      "created/renamed/duplicated after the config was generated. "
                      "Regenerate to include it."),
        }), 404

    # Qubit/flux branches return a dotted "<target>.<channel>" key; pair-drive
    # elements (cr_q1_q2 / zz_q1_q2) are dot-less — rpartition then yields an
    # empty prefix, which waveform_for_operation treats as "channel IS the key".
    prefix, _, chan = elem_or_prefix.rpartition(".")
    truth = config_view.waveform_for_operation(cfg, prefix, op_name, channel=chan)
    if truth is None or not truth.get("traces"):
        return jsonify({
            "ok": False, "status": "no-trace",
            "error": (f"{op_name!r} is in the config but carries no waveform "
                      "(e.g. a measurement op with only integration weights). "
                      "Nothing to overlay."),
        }), 404

    # I = the single/I trace, Q = the Q trace (config_view returns one trace
    # per waveform entry, ordered single → I → Q → rest).
    truth_i = next((t for t in truth["traces"]
                    if t.get("label") in ("single", "I")), truth["traces"][0])
    truth_q = next((t for t in truth["traces"] if t.get("label") == "Q"), None)

    # Staleness: the single shared primitive (basis = working-copy file hash
    # at regenerate; an undo back to the generated content reads fresh again).
    stale = _config_stale(store)

    # Server-side comparison against the CURRENT synth (full arrays, no
    # display decimation) — only meaningful when the config is fresh.
    comparison = None
    synth = synth_for_operation(store, path)
    if synth.get("ok") and truth_i.get("y"):
        import numpy as np
        i_arr = np.asarray(synth["i"], dtype=float)
        t_arr = np.asarray(truth_i["y"], dtype=float)
        if len(i_arr) == len(t_arr):
            max_delta = float(np.max(np.abs(i_arr - t_arr))) if len(i_arr) else 0.0
            if synth.get("q") is not None and truth_q and truth_q.get("y"):
                q_arr = np.asarray(synth["q"], dtype=float)
                tq_arr = np.asarray(truth_q["y"], dtype=float)
                if len(q_arr) == len(tq_arr):
                    max_delta = max(max_delta, float(
                        np.max(np.abs(q_arr - tq_arr))) if len(q_arr) else 0.0)
            comparison = {"max_delta": max_delta,
                          "match": max_delta < 1e-9, "lengths_match": True}
        else:
            comparison = {"max_delta": None, "match": False,
                          "lengths_match": False,
                          "synth_len": len(i_arr), "truth_len": len(t_arr)}

    traces = [{"name": "I", "x": truth_i.get("x") or [],
               "y": truth_i.get("y") or []}]
    if truth_q and truth_q.get("y"):
        traces.append({"name": "Q", "x": truth_q.get("x") or [],
                       "y": truth_q["y"]})
    # decimate for display parity with the synth plot
    from quam_state_manager.core.waveform_synth import decimate_minmax
    for trace in traces:
        xs, ys, _ = decimate_minmax(trace["y"], _PULSE_PLOT_MAX_POINTS)
        trace["x"], trace["y"] = xs, ys

    return jsonify({
        "ok": True,
        "status": "stale" if stale else "fresh",
        "plot": {"ok": True, "traces": traces},
        "element": truth.get("element"),
        "operation": op_name,
        "meta": {"at": meta.get("at"), "stale": stale,
                 "unsaved_at_generate": bool(meta.get("unsaved_at_generate"))},
        "comparison": comparison,
    })


# ======================================================================
# Search
# ======================================================================


@bp.route("/search")
def search():
    index = _index()
    query = request.args.get("q", "").strip()

    if not query:
        return render_template("_search_results.html", results=[], query=query, active_category=None)
    if not index:
        # Distinguish "no chip loaded" from a genuine miss so the user isn't told
        # "No results" (which reads as: the value isn't in this chip) when in fact
        # nothing is loaded to search.
        return render_template("_search_results.html", results=[], query=query,
                               active_category=None, no_state=True)

    category = request.args.get("category")
    limit = _int_arg("limit", 50, minimum=1)
    results = index.search(query, limit=limit, category=category)

    return render_template("_search_results.html", results=results, query=query, active_category=category)


# ======================================================================
# Save / Undo
# ======================================================================


@bp.route("/save", methods=["POST"])
def save():
    # Capture the context up front so the build lock below pins to THIS folder
    # even if a concurrent /load flips the active context mid-request.
    ctx = _active_ctx()
    saver = ctx.get("saver") if ctx else None
    store = ctx.get("store") if ctx else None
    if not saver or not store:
        return render_template("_status.html", message="No state loaded", level="warning")

    if not store.change_log:
        return render_template("_status.html", message="No unsaved changes", level="info")

    count = len(store.change_log)
    # Stash the edits before save() clears the change log, so a later pull can
    # re-apply or stage them even though they're no longer "unsaved".
    with store._lock:
        _stash_reapply(_capture_change_log_as_updates(store), ctx)
        _jrn_units = _journal_prepare(store, ctx)   # docs/107: outgoing log
    try:
        # Build lock: the save writes the working folder's state/wiring pair —
        # the same files a concurrent _activate_quam reconcile auto-sync
        # rewrites. Serialise so the two can't interleave on the .tmp files.
        with _active_wc_lock(ctx):
            saver.save()
    except Exception as e:
        return render_template("_status.html", message=f"Save failed: {e}", level="error"), 500
    _journal_commit(ctx, _jrn_units)   # docs/107: the log is gone — journal it

    # Save writes the working copy only — the live chip is untouched until an
    # explicit "Apply to live".  History is snapshotted on apply, not here.
    # Flag THIS captured chip dirty, not whatever a concurrent /load may have
    # made active mid-request.
    _set_working_dirty(True, ctx)

    toast = render_template(
        "_status.html",
        message=f"Saved {count} change(s) to the working state",
        level="success",
    )
    tray = _tray_html()
    return tray + "\n" + f'<div id="status-bar" hx-swap-oob="innerHTML">{toast}</div>'


@bp.route("/state/tray")
def state_tray():
    """Current Review tray fragment (r16 6): applyEditsToLive re-checks the
    SERVER before declaring 'nothing to apply' — the tray's data-* attributes
    can go stale when an OOB swap was missed."""
    return _tray_html()


@bp.route("/undo", methods=["POST"])
def undo():
    modifier = _modifier()
    if not modifier:
        return render_template("_status.html", message="No state loaded", level="warning")

    ctx = _active_ctx()
    store = ctx.get("store") if ctx else None

    # docs/107 routing: an ordinary group on top undoes exactly as before; an
    # EMPTY log — or a ``jrn:`` staged step already on top — walks DEEPER into
    # the cross-save journal (staging the next unit's inverse into the tray)
    # instead of stopping at the save boundary. No peek route (docs/73): the
    # decision is made here, on the server, from the log itself.
    if store is not None:
        top_gid = store.change_log[-1].group_id if store.change_log else None
        if (not store.change_log
                or (isinstance(top_gid, str)
                    and top_gid.startswith(undo_journal.GID_PREFIX))):
            return _undo_journal_step(ctx)

    _redo_begin(ctx, store)   # docs/107: a foreign edit since forks history
    try:
        # Undo the last USER ACTION atomically (a batch edit / rename undoes as
        # one unit, not one Ctrl+Z per underlying entry).
        entries = modifier.undo_group()
    except KeyError as exc:
        # e.g. restoring a deleted subtree whose key was re-created since
        _invalidate_engine_cache()
        return render_template("_status.html", message=str(exc), level="error"), 409
    _invalidate_engine_cache()
    if not entries:
        # Nothing to undo — return the (unchanged) tray so the keyboard-triggered
        # outerHTML swap is a harmless no-op instead of replacing the tray with a
        # status line.
        return _tray_html()
    _redo_push_group(ctx, store, entries)   # docs/107: Ctrl+Shift+Z target

    # Reverts are most-recent-first; report the oldest (the action's anchor) and
    # revert every affected cell/tree-node visually (reuses the /discard path).
    anchor = entries[-1]
    n = len(entries)
    if n > 1:
        message = f"Undone: {n} changes ({anchor.dot_path} …)"
    elif anchor.deleted:
        message = f"Undone: {anchor.dot_path} restored"
    elif anchor.created:
        message = f"Undone: {anchor.dot_path} removed"
    else:
        # docs/76: say how far the undo moved the value, not just where it
        # landed — "→ 5,100,000,000" alone doesn't tell you what was lost.
        from quam_state_manager.core import value_delta as _vd
        _d = _vd.compute(anchor.new_value, anchor.old_value)
        message = f"Undone: {anchor.dot_path} → {_fmt_val(anchor.old_value)}"
        if _d and _d["dir"] != "same":
            message += f" ({_d['text']}"
            message += f", {_d['pct_text']})" if _d["pct_text"] else ")"

    resp = make_response(_tray_html())
    resp.headers["HX-Trigger"] = json.dumps({
        # Revert each affected inspector cell + Explorer tree node in place, and
        # toast the summary (handled client-side in the cellsReverted listener).
        "cellsReverted": {
            "message": message,
            # source_file + deleted feed UndoNav (r16 0-2, docs/73): the
            # RESPONSE is the authoritative what-was-undone signal (a peek-
            # then-undo design would race a concurrent commit), so the
            # navigate-to-owner decision rides these fields.
            "entries": [
                {"dot_path": e.dot_path, "old_value_str": _fmt_val(e.old_value),
                 "created": e.created, "deleted": e.deleted,
                 "source_file": e.source_file}
                for e in entries
            ],
        },
        # open Pulses/grids re-fetch their rows (no-op elsewhere)
        "pulses-changed": True,
        "diagnostics-changed": True,
    })
    return resp


def _undo_journal_step(ctx):
    """Stage the inverse of the next journal unit into the tray (docs/107).

    The SM covenant holds: this NEVER touches the live files — the inverse
    edits land in the change log (review tray) under gid ``jrn:<unit-id>``,
    and reaching the chip still requires the user's explicit Apply-to-live.

    All-or-nothing: a partial staging (e.g. the create clobber guard — the
    key re-appeared since) is rolled back LIFO and answered 409 with the
    cursor unchanged. Drift is REPORTED, not blocking: values that moved
    since the unit was recorded are counted off the modifier's own returned
    ``old_value`` (zero extra reads) and named in the toast.

    Exhausted journal / archive / cursor==0 ⇒ the same silent unchanged-tray
    no-op the empty-log Ctrl+Z always produced.
    """
    store = ctx.get("store") if ctx else None
    modifier = ctx.get("modifier") if ctx else None
    units = (ctx.get("undo_units") or []) if ctx else []
    cursor = int(ctx.get("undo_cursor") or 0) if ctx else 0
    if (store is None or modifier is None
            or (ctx.get("origin") or "live") != "live"
            or cursor <= 0 or cursor > len(units)):
        return _tray_html()

    unit = units[cursor - 1]
    uents = list(reversed(unit.get("entries") or []))   # staging order (LIFO)
    if not uents:
        ctx["undo_cursor"] = cursor - 1                 # skip an empty unit
        return _tray_html()
    gid = undo_journal.GID_PREFIX + str(unit.get("id") or cursor)
    ops = undo_journal.inverse_ops(unit)

    staged: list = []
    drift = 0
    with store._lock:
        _redo_begin(ctx, store)   # a foreign edit since our last op forks redo
        try:
            for (op, path, value, _src), uent in zip(ops, uents):
                if op == "delete":
                    e = modifier.delete_subtree(path, group_id=gid)
                    if e.old_value != uent.get("new"):
                        drift += 1
                elif op == "create":
                    e = modifier.create_subtree(path, value, group_id=gid)
                else:
                    # coerce=False: restore the historical value VERBATIM — the
                    # field's CURRENT type may differ (e.g. a later type-fix),
                    # and coercion would cast the restoration back to it.
                    e = modifier.set_value(path, value, coerce=False,
                                           group_id=gid)
                    if e.old_value != uent.get("new"):
                        drift += 1
                staged.append(e)
        except Exception as exc:   # noqa: BLE001 — any failure un-stages all
            for _ in staged:
                try:
                    modifier.undo()   # our entries are the log tail — pops LIFO
                except Exception:
                    logger.warning("journal-step rollback lagged", exc_info=True)
                    break
            _invalidate_engine_cache()
            return render_template(
                "_status.html",
                message=f"Undo (staged) failed, nothing changed: {exc}",
                level="error"), 409

    ctx["undo_cursor"] = cursor - 1
    _redo_mark(ctx, store)
    _invalidate_engine_cache()

    # Response mirrors /undo's exactly (docs/73: UndoNav + cell repaints ride
    # the response). Flags are the ORIGINAL entry's — original created=True
    # means the key is removed now (client: "removed"), original deleted=True
    # means it is restored now — and old_value_str is the value now in place.
    n = len(uents)
    anchor = uents[-1]
    if n > 1:
        message = f"Undone (staged): {n} changes ({anchor['path']} …)"
    elif anchor.get("deleted"):
        message = f"Undone (staged): {anchor['path']} restored"
    elif anchor.get("created"):
        message = f"Undone (staged): {anchor['path']} removed"
    else:
        from quam_state_manager.core import value_delta as _vd
        _d = _vd.compute(anchor.get("new"), anchor.get("old"))
        message = f"Undone (staged): {anchor['path']} → {_fmt_val(anchor.get('old'))}"
        if _d and _d["dir"] != "same":
            message += f" ({_d['text']}"
            message += f", {_d['pct_text']})" if _d["pct_text"] else ")"
    if drift:
        message += f" — {drift} value(s) had moved since; the tray now holds the journal value"

    resp = make_response(_tray_html())
    resp.headers["HX-Trigger"] = json.dumps({
        "cellsReverted": {
            "message": message,
            "entries": [
                {"dot_path": u["path"], "old_value_str": _fmt_val(u.get("old")),
                 "created": bool(u.get("created")), "deleted": bool(u.get("deleted")),
                 "source_file": u.get("source_file", "state")}
                for u in uents
            ],
        },
        "pulses-changed": True,
        "diagnostics-changed": True,
    })
    return resp


@bp.route("/redo", methods=["POST"])
def redo():
    """Ctrl+Shift+Z (docs/107). Two cases, no peek route:

    - a ``jrn:`` staged step on top of the log ⇒ un-stage it (this IS
      ``undo_group``) and move the journal cursor back up;
    - otherwise pop the redo stack and re-apply that group forward. The stack
      is only valid if nothing foreign mutated since our machinery's last op
      (mutation_seq handshake) — a mismatch clears it silently, the same
      forked-history rule every editor applies.

    Like every undo/redo/discard, this stages into the tray only — the live
    chip is untouched until an explicit Apply-to-live.
    """
    modifier = _modifier()
    if not modifier:
        return render_template("_status.html", message="No state loaded", level="warning")
    ctx = _active_ctx()
    store = ctx.get("store") if ctx else None
    if store is None:
        return _tray_html()

    top_gid = store.change_log[-1].group_id if store.change_log else None
    if isinstance(top_gid, str) and top_gid.startswith(undo_journal.GID_PREFIX):
        # Un-stage the staged journal step; cursor moves back toward the tip.
        try:
            entries = modifier.undo_group()
        except KeyError as exc:
            _invalidate_engine_cache()
            return render_template("_status.html", message=str(exc), level="error"), 409
        _invalidate_engine_cache()
        if not entries:
            return _tray_html()
        ctx["undo_cursor"] = min(int(ctx.get("undo_cursor") or 0) + 1,
                                 len(ctx.get("undo_units") or []))
        _redo_mark(ctx, store)
        anchor = entries[-1]
        n = len(entries)
        message = (f"Redone: un-staged {n} change(s) ({anchor.dot_path} …)"
                   if n > 1 else f"Redone: un-staged {anchor.dot_path}")
        return _redo_response(message, [
            {"dot_path": e.dot_path, "old_value_str": _fmt_val(e.old_value),
             "created": e.created, "deleted": e.deleted,
             "source_file": e.source_file}
            for e in entries
        ])

    frames = _redo_stack(ctx)
    if not frames:
        return _tray_html()
    if ctx.get("redo_seq") != store.mutation_seq:
        frames.clear()   # foreign mutation since — dead timeline, silent no-op
        return _tray_html()
    frame = frames.pop()

    fents = list(reversed(frame["entries"]))   # chronological re-apply order
    is_jrn = bool(frame.get("gid"))
    gid = frame.get("gid") if is_jrn else (
        modifier.new_group_id() if len(fents) > 1 else None)
    staged: list = []
    with store._lock:
        try:
            for fe in fents:
                if fe["created"]:
                    e = modifier.create_subtree(fe["path"], fe["new"], group_id=gid)
                elif fe["deleted"]:
                    e = modifier.delete_subtree(fe["path"], group_id=gid)
                else:
                    e = modifier.set_value(fe["path"], fe["new"], coerce=False,
                                           group_id=gid)
                staged.append(e)
        except Exception as exc:   # noqa: BLE001 — all-or-nothing, frame dropped
            for _ in staged:
                try:
                    modifier.undo()
                except Exception:
                    logger.warning("redo rollback lagged", exc_info=True)
                    break
            _invalidate_engine_cache()
            return render_template(
                "_status.html",
                message=f"Redo failed, nothing changed: {exc}",
                level="error"), 409
    if is_jrn:
        # Re-staging a discarded journal step re-consumes its unit.
        ctx["undo_cursor"] = max(int(ctx.get("undo_cursor") or 0) - 1, 0)
    _redo_mark(ctx, store)
    _invalidate_engine_cache()

    n = len(fents)
    anchor = fents[0]
    if n > 1:
        message = f"Redone: {n} changes ({anchor['path']} …)"
    elif anchor["deleted"]:
        message = f"Redone: {anchor['path']} removed"
    elif anchor["created"]:
        message = f"Redone: {anchor['path']} restored"
    else:
        message = f"Redone: {anchor['path']} → {_fmt_val(anchor['new'])}"
    # Client flags describe what happened to the CELL now: a re-applied
    # create restored it (deleted=True in undo-speak), a re-applied delete
    # removed it (created=True) — the exact inversion of the frame's flags.
    return _redo_response(message, [
        {"dot_path": fe["path"], "old_value_str": _fmt_val(fe["new"]),
         "created": bool(fe["deleted"]), "deleted": bool(fe["created"]),
         "source_file": fe.get("source_file", "state")}
        for fe in reversed(fents)   # newest-first, like /undo's payload
    ])


def _redo_response(message: str, entries_payload: list[dict]):
    """The /undo response shape (tray + cellsReverted HX-Trigger), reused by
    both /redo branches so the client repaint/UndoNav path stays ONE."""
    resp = make_response(_tray_html())
    resp.headers["HX-Trigger"] = json.dumps({
        "cellsReverted": {"message": message, "entries": entries_payload},
        "pulses-changed": True,
        "diagnostics-changed": True,
    })
    return resp


@bp.route("/changes")
def changes():
    modifier = _modifier()
    if not modifier:
        return render_template("_changes.html", changes=[])

    return render_template("_changes.html", changes=modifier.get_change_log())


@bp.route("/discard", methods=["POST"])
def discard():
    modifier = _modifier()
    if not modifier:
        return _tray_html()

    try:
        index = int(request.form.get("index", -1))
    except (ValueError, TypeError):
        return render_template("_status.html", message="Invalid index", level="error"), 400

    # Identity guard: the tray posts the change's dot_path alongside its
    # render-time index. If another tab's discard/undo shifted the log since,
    # the index now names a DIFFERENT entry — discard() rejects the mismatch so
    # the stale click can't revert the wrong change.
    expect_path = request.form.get("expect_path") or None

    ctx = _active_ctx()
    store = ctx.get("store") if ctx else None
    _redo_begin(ctx, store)   # docs/107: a foreign edit since forks history
    try:
        entry = modifier.discard(index, expect_path=expect_path)
    except KeyError as exc:
        # e.g. discarding a delete whose key was re-created since, or an edit
        # inside a subtree that a later entry deleted — surface, don't 500.
        _invalidate_engine_cache()
        return render_template("_status.html", message=str(exc), level="error"), 409
    _invalidate_engine_cache()
    if entry is None:
        return render_template("_status.html", message="Change not found", level="warning")
    # docs/107: recoverability is what licenses the ✕'s no-confirm — the
    # discarded change goes onto the redo stack, Ctrl+Shift+Z brings it back.
    _redo_push_group(ctx, store, [entry], keep_jrn=False)

    resp = make_response(_tray_html())
    resp.headers["HX-Trigger"] = json.dumps({
        "cellDiscarded": {
            "dot_path": entry.dot_path,
            "old_value_str": _fmt_val(entry.old_value),
        },
        # open Pulses surfaces re-fetch their rows (no-op elsewhere)
        "pulses-changed": True,
        # refresh the diagnostics tray badge + error banner
        "diagnostics-changed": True,
    })
    return resp


@bp.route("/discard_all", methods=["POST"])
def discard_all():
    """Revert EVERY pending change in one press (docs/107). No confirm by
    design: each popped user-action group lands on the redo stack in the
    order repeated Ctrl+Z would have produced it, so Ctrl+Shift+Z restores
    them one by one, in order — recoverability is the license. Staged
    journal steps (``jrn:`` groups) un-stage too, and the journal cursor
    moves back toward the tip by their count."""
    modifier = _modifier()
    if not modifier:
        return _tray_html()
    ctx = _active_ctx()
    store = ctx.get("store") if ctx else None
    if store is None or not store.change_log:
        return _tray_html()

    _redo_begin(ctx, store)   # foreign edit since our last op forks history
    try:
        groups = modifier.undo_all()
    except KeyError as exc:
        # A mid-way raise leaves fully popped groups gone and the failing one
        # partially in the log (modifier keeps log==state); surface honestly.
        _invalidate_engine_cache()
        return render_template("_status.html", message=str(exc), level="error"), 409
    _invalidate_engine_cache()
    if not groups:
        return _tray_html()

    jrn = 0
    for g in groups:
        _redo_push_group(ctx, store, g, mark=False)
        gid0 = g[0].group_id
        if isinstance(gid0, str) and gid0.startswith(undo_journal.GID_PREFIX):
            jrn += 1
    if ctx is not None and jrn:
        ctx["undo_cursor"] = min(int(ctx.get("undo_cursor") or 0) + jrn,
                                 len(ctx.get("undo_units") or []))
    _redo_mark(ctx, store)

    total = sum(len(g) for g in groups)
    resp = make_response(_tray_html())
    resp.headers["HX-Trigger"] = json.dumps({
        "cellsReverted": {
            "message": (f"Discarded all: {total} change(s) — "
                        "Ctrl+Shift+Z restores them one by one"),
            "entries": [
                {"dot_path": e.dot_path, "old_value_str": _fmt_val(e.old_value),
                 "created": e.created, "deleted": e.deleted,
                 "source_file": e.source_file}
                for g in groups for e in g
            ],
        },
        "pulses-changed": True,
        "diagnostics-changed": True,
    })
    return resp


# ======================================================================
# Auto-apply (docs/117) — the session the covenant amendment authorizes
# ======================================================================


def _auto_disarm_response(ctx, body: str, reason: str, status: int = 200):
    """Clear the session and hand the body back with the disarm signal.

    The client stops scheduling the moment it sees ``autoApplyDisarm``; the
    server has already forgotten the session, so the two cannot disagree even
    if the trigger is lost. Used by every failure branch of the apply route —
    a background writer must never keep pushing at a chip that refused it.
    """
    if ctx is not None:
        ctx.pop("auto_apply", None)
    resp = make_response(body, status)
    resp.headers["HX-Trigger"] = json.dumps({
        "autoApplyDisarm": {"reason": reason},
    })
    return resp


def _new_auto_session(*, pull: bool, pull_replace: bool, push: bool) -> dict:
    """A fresh Auto-Sync session. NEVER persisted (docs/117): an armed session
    must not outlive the window that armed it."""
    return {
        "armed_at": time.time(),
        "pre_ts": None,        # the session's ONE revert anchor (docs/117)
        "last_snap": 0.0,      # post-apply snapshot throttle
        "flushes": 0,
        "pull": bool(pull),
        "pull_replace": bool(pull_replace),
        "push": bool(push),
    }


@bp.route("/auto-sync/set", methods=["POST"])
def auto_sync_set():
    """Set the three Auto-Sync switches (docs/120 item 8).

    The user's design: the pill opens a popup with checkboxes — auto pull from
    the live chip, auto replace SM's modified values on that pull, and auto
    push to the live chip — all on by default, uncheck what you do not want.
    THIS submission is the consent the covenant asks for; the pill then states
    the mode on every page until it is turned off.

    Turning everything off clears the session entirely, so "off" is exactly
    today's behaviour rather than an armed session that happens to do nothing.
    """
    ctx = _active_ctx()
    if not ctx or ctx.get("type") != "quam":
        return render_template("_status.html", message="No chip is open.",
                               level="warning"), 409
    want_pull = request.values.get("pull") in ("1", "on", "true")
    want_replace = request.values.get("pull_replace") in ("1", "on", "true")
    want_push = request.values.get("push") in ("1", "on", "true")

    if not (want_pull or want_push):
        ctx.pop("auto_apply", None)
        return _tray_html()

    # Each direction is gated on ITS OWN question; a refusal on one must not
    # silently swallow the other.
    if want_push:
        ok, why = _auto_apply_armable(ctx)
        if not ok:
            return render_template("_status.html", message=why, level="warning"), 409
    if want_pull:
        ok, why = _auto_pull_armable(ctx)
        if not ok:
            return render_template("_status.html", message=why, level="warning"), 409
    # "Replace" without "pull" is not a state — it qualifies a pull.
    ctx["auto_apply"] = _new_auto_session(
        pull=want_pull, pull_replace=want_pull and want_replace, push=want_push)
    resp = make_response(_tray_html())
    resp.headers["HX-Trigger"] = json.dumps({"autoApplyArmed": True})
    return resp


@bp.route("/auto-sync/pull", methods=["POST"])
def auto_sync_pull():
    """Perform (or decline) one automatic pull. The POLICY lives here.

    The client's only job is to notice that the server said a pull is due and
    to press this; every branch of the user's table is decided server-side, so
    the rule cannot be half-implemented in two places.

    204 means "nothing done" for every reason -- not armed, not diverged, or
    DECLINED because the user has unapplied edits and did not tick replace. In
    that last case the drift banner is already up (``live_diverged`` is what
    made this due), so refusing is not silent: the user is being asked.
    """
    ctx = _active_ctx()
    sess = _auto_sync_state(ctx)
    if not sess or not sess.get("pull"):
        return "", 204
    ok, _why = _auto_pull_armable(ctx)
    if not ok:
        return "", 204
    if not ctx.get("live_diverged"):
        return "", 204            # nothing to pull; the common case

    dirty = bool(ctx.get("working_dirty") or (ctx.get("store")
                                              and ctx["store"].change_log))
    if dirty and not sess.get("pull_replace"):
        # THE line docs/87 draws, kept: SM does not choose between the user's
        # work and the live chip. The banner is already showing.
        return "", 204

    wc = ctx.get("working_copy")
    if wc is None:
        return "", 204
    build_lock = _active_wc_lock(ctx)
    try:
        with build_lock:
            working_copy.sync_from_live(wc)
            ctx["_alarm_reason"] = "live-pull"
            _rebuild_after_working_copy_replaced(ctx)
    except (FileNotFoundError, OSError, ValueError):
        # A pull that cannot read live is not a reason to keep retrying every
        # poll: disarm, exactly like the push side's failure branches.
        logger.warning("Auto-sync pull failed", exc_info=True)
        return _auto_disarm_response(ctx, "", "error", status=204)

    resp = make_response(_tray_html())
    resp.headers["HX-Trigger"] = json.dumps({
        "liveDriftChanged": True, "stateRestored": True,
        "autoSyncPulled": {"replaced": bool(dirty)},
    })
    return resp


@bp.route("/auto-apply/arm", methods=["POST"])
def auto_apply_arm():
    """Arm the session. THIS press is the consent the covenant asks for —
    one labelled act, no confirm dialog (docs/104 #1), and the pill it turns
    on is visible on every page until the user turns it off.

    Kept as the PUSH-only entry point (docs/120 item 8 added /auto-sync/set for
    the three-way choice) so the docs/117 contract and its pins are unchanged.
    """
    ctx = _active_ctx()
    ok, why = _auto_apply_armable(ctx)
    if not ok:
        return render_template("_status.html", message=why, level="warning"), 409
    ctx["auto_apply"] = _new_auto_session(pull=False, pull_replace=False, push=True)
    resp = make_response(_tray_html())
    # Pending edits made BEFORE arming are flushed by the client's first
    # mutation-driven pass; the label said so before the press.
    resp.headers["HX-Trigger"] = json.dumps({"autoApplyArmed": True})
    return resp


@bp.route("/auto-apply/disarm", methods=["POST"])
def auto_apply_disarm():
    """Turn it off and close the session with one unconditional snapshot, so a
    session always ends on a captured state even though the flushes in between
    were throttled."""
    ctx = _active_ctx()
    sess = _auto_apply_state(ctx)
    if ctx is not None:
        ctx.pop("auto_apply", None)
    if sess and sess.get("flushes"):
        try:
            _history().check_and_snapshot(
                ctx["path"], "save",
                defer_index=not current_app.config.get("TESTING"),
                project=_scope_for(ctx["path"], ctx))
        except Exception:
            logger.warning("Auto-apply closing snapshot failed", exc_info=True)
    return _tray_html()


@bp.route("/auto-apply/gate")
def auto_apply_gate():
    """What the pill needs to describe itself. Informational only: a run in
    progress is REPORTED, never a block (the user's explicit choice, and the
    docs/86 precedent — a run going wrong is a reason to be editing)."""
    ctx = _active_ctx()
    ok, why = _auto_apply_armable(ctx)
    run_active, run_label = False, ""
    try:
        inst = _sched_inst()
        st = inst.status() if inst else None
        if isinstance(st, dict) and st.get("is_running"):
            run_active = True
            run_label = str(st.get("current_label") or st.get("current") or "")
    except Exception:          # noqa: BLE001 — the pill must never break a page
        pass
    return jsonify({
        "ok": True,
        "armed": bool(_auto_apply_state(ctx)),
        "armable": ok,
        "reason": why,
        "run_active": run_active,
        "run_label": run_label,
    })


@bp.route("/auto-apply/revert", methods=["POST"])
def auto_apply_revert():
    """Revert ONE applied change from the log (its ✕).

    Compare-and-swap, mirroring ``autofit/writer.revert_patches`` (docs/56 §8):
    the unit's ``new`` must still be what the chip holds, or a later change to
    the same path would be destroyed by a blind restore. A mismatch refuses with
    the reason and writes NOTHING.

    Like every other undo in SM this only STAGES the inverse (gid ``alr:`` —
    never ``jrn:``, which routes /undo deeper and moves the journal cursor).
    While the session is armed the client's flusher pushes it immediately, so
    the ✕ reaches the chip through the same one door as everything else; with
    the session off it waits in the review tray, which is the ordinary model.
    """
    from quam_state_manager.core import edit_policy
    from quam_state_manager.core.pointer_path import resolve_field_target

    ctx = _active_ctx()
    store = ctx.get("store") if ctx else None
    modifier = ctx.get("modifier") if ctx else None
    if store is None or modifier is None or (ctx.get("origin") or "live") != "live":
        return render_template("_status.html", message="No live chip is open.",
                               level="warning"), 409
    unit_id = (request.values.get("unit_id") or "").strip()
    unit = None
    for u in (ctx.get("undo_units") or []):
        if u.get("id") == unit_id:
            unit = u
            break
    if unit is None:
        return render_template("_status.html",
                               message="That change is no longer in the log.",
                               level="warning"), 409
    if (unit.get("meta") or {}).get("reverted_by"):
        return render_template("_status.html",
                               message="That change was already reverted.",
                               level="warning"), 409

    ents = list(reversed(unit.get("entries") or []))
    ops = undo_journal.inverse_ops(unit)
    gid = "alr:" + str(unit_id)
    staged: list = []
    with store._lock:
        # ── CAS pre-check under the same lock hold as the staging, so nothing
        # can move between the check and the write (the advisory-then-recheck
        # split in the autofit writer exists because it releases the lock).
        merged = store.merged
        for e in ents:
            if e.get("created") or e.get("deleted"):
                continue           # structural ops: inverse_ops handles them
            try:
                cur = resolve_field_target(merged, e["path"]).get("resolved_value")
            except Exception:      # noqa: BLE001 — unreadable == moved
                cur = None
            if not edit_policy.cas_equal(cur, e.get("new")):
                return render_template(
                    "_status.html", level="warning",
                    message=(f"Not reverted — {e['path']} has changed since "
                             f"(now {_fmt_val(cur)}, this change wrote "
                             f"{_fmt_val(e.get('new'))}). Nothing was written.")
                ), 409

        _redo_begin(ctx, store)
        try:
            for (op, path, value, _src) in ops:
                if op == "delete":
                    staged.append(modifier.delete_subtree(path, group_id=gid))
                elif op == "create":
                    staged.append(modifier.create_subtree(path, value, group_id=gid))
                else:
                    # coerce=False: restore the historical value VERBATIM
                    staged.append(modifier.set_value(path, value, coerce=False,
                                                     group_id=gid))
        except Exception as exc:   # noqa: BLE001 — all-or-nothing
            for _ in staged:
                try:
                    modifier.undo()
                except Exception:
                    logger.warning("applied-log revert rollback lagged",
                                   exc_info=True)
                    break
            _invalidate_engine_cache()
            return render_template(
                "_status.html", level="error",
                message=f"Revert failed, nothing changed: {exc}"), 409

    # Mark the row so it renders reverted instead of inviting a second revert.
    try:
        meta_patch = {"reverted_by": gid}
        path = undo_journal.sidecar_path(current_app.instance_path, ctx["path"])
        ctx["undo_units"] = undo_journal.mark_unit(path, unit_id, meta_patch)
        ctx["undo_cursor"] = min(int(ctx.get("undo_cursor") or 0),
                                 len(ctx["undo_units"]))
    except Exception:
        logger.warning("applied-log mark failed", exc_info=True)

    _redo_mark(ctx, store)
    _invalidate_engine_cache()
    anchor = ents[-1] if ents else {"path": "?"}
    resp = make_response(_tray_html())
    resp.headers["HX-Trigger"] = json.dumps({
        "cellsReverted": {
            "message": (f"Reverted: {anchor['path']}"
                        + (f" (+{len(ents) - 1} more)" if len(ents) > 1 else "")),
            "entries": [
                {"dot_path": e.dot_path, "old_value_str": _fmt_val(e.old_value),
                 "created": e.created, "deleted": e.deleted,
                 "source_file": e.source_file}
                for e in staged
            ],
        },
        "pulses-changed": True,
        "diagnostics-changed": True,
    })
    return resp


# ======================================================================
# Live-state sync / apply (working-copy <-> live chip)
# ======================================================================


@bp.route("/state/review")
def state_review():
    """Diff the live state files against the working copy, on demand.

    Triggered by "Review changes" — the only live-file *content* read that
    happens outside an explicit sync or apply.

    Diffs in-memory: the live (state, wiring) tuple is fed directly to
    ``Differ.diff`` without the tmp-dir-and-disk round trip the previous
    implementation used (red-team Phase 2 finding §5.2).
    """
    ctx = _active_ctx()
    if not ctx or ctx.get("type") != "quam":
        return render_template("_status.html", message="No state loaded", level="warning")
    wc = ctx["working_copy"]
    store = ctx["store"]
    try:
        live_state, live_wiring = working_copy.read_live(wc)
    except FileNotFoundError:
        return render_template(
            "_status.html",
            message="Live state folder not found — it may have been moved or deleted.",
            level="error")
    except OSError as exc:
        return render_template(
            "_status.html",
            message=f"Could not read the live state: {exc}", level="error")

    entries = Differ().diff(store, (live_state, live_wiring))

    # Paths the user has actually edited in this session (incl. on-the-fly
    # accepts, which land in the change log). A row on one of these holds the
    # USER's value in "Your copy", so its ✓ would REVERT that to the live value
    # — mark it so the client confirms before overwriting (audit A12).
    edited_paths = {e.dot_path for e in store.change_log}

    return render_template(
        "_state_review.html",
        entries=entries[:300],
        edited_paths=edited_paths,
        summary=Differ.summary(entries),
        total=len(entries),
        unsaved=len(store.change_log),
        working_dirty=bool(ctx.get("working_dirty")),
        chip_origin=_active_origin(),
    )


@bp.route("/state/live-diff")
def state_live_diff():
    """Before/after diff as JSON: working copy (before) vs Qualibrate's live (after).

    The workbench's JSON sibling of /state/review. Drives the **content-aware
    nudge** (``total`` — only nudge when the live state TRULY differs, so a
    touch-without-change doesn't fire) and the inline Explorer live-diff
    (``?with_live=1`` adds the raw live state/wiring for renderJsonTree's
    ``refData``). Same on-demand live-content read as /state/review; the dot-paths
    are over the merged dict so they align with the Explorer trees (qubits/ports →
    state tree, wiring/network → wiring tree).
    """
    ctx = _active_ctx()
    if not ctx or ctx.get("type") != "quam":
        return jsonify(ok=False, transient=False, error="No state loaded"), 400
    wc = ctx["working_copy"]
    store = ctx["store"]
    # This is an EXPLICIT user-click read (like a sync), so read HARDER than the
    # background poll (attempts=8) — a QUAlibrate save-burst is momentary, so the
    # extra patience usually lets os.replace settle. A torn-pair / lock is reported
    # as TRANSIENT (503) so the client retries; it is never a dead error.
    try:
        live_state, live_wiring = working_copy.read_live(wc, attempts=8)
    except FileNotFoundError:
        return jsonify(ok=False, transient=False, error="Live state folder not found"), 404
    except (safe_io.LiveFileError, OSError):
        logger.info("live-diff read deferred (live being written) — client will retry")
        return jsonify(ok=False, transient=True,
                       error="Live chip is being written — retrying shortly"), 503
    # Build + serialize INSIDE a guard so an odd-data Differ/serialization error can
    # never escape as a Werkzeug HTML 500 (which the client's r.json() would mis-parse
    # as a "network error"). Any failure here is reported as structured JSON.
    try:
        entries = Differ().diff(store, (live_state, live_wiring))
        payload = {
            "ok": True,
            "total": len(entries),
            "summary": Differ.summary(entries),
            "entries": [
                {"dot_path": e.dot_path, "old": e.old_value, "new": e.new_value,
                 "change_type": e.change_type}
                for e in entries[:500]
            ],
        }
        if request.args.get("with_live") == "1":
            payload["live_state"] = live_state
            payload["live_wiring"] = live_wiring
        return jsonify(payload)
    except Exception as exc:  # noqa: BLE001 — never emit a non-JSON 500 to the live-diff client
        logger.warning("live-diff build failed", exc_info=True)
        return jsonify(ok=False, transient=False,
                       error=f"Could not build the live diff: {exc}"), 500


# ======================================================================
# Live-drift tracking — accumulating "what the live chip changed since a
# baseline". DECOUPLED from the working-copy sync point so it survives the
# clean-working-copy auto-sync that otherwise silently absorbs the diff (the
# reported "SM loses track of repeated qualibrate fits" bug). The baseline is
# a self-contained per-chip sidecar (core.history); the main view keeps
# auto-adopting the latest live, while this comparison keeps accumulating.
# ======================================================================


def _drift_tracked(ctx) -> bool:
    """Drift is tracked only for a writable LIVE chip — a read-only dataset
    archive has no evolving live files to drift from."""
    return bool(ctx) and ctx.get("type") == "quam" \
        and (ctx.get("origin") or "live") == "live" \
        and ctx.get("working_copy") is not None


def _clear_drift_cache(ctx) -> None:
    """Drop the ctx-cached drift count + baseline content (under ``_drift_lock``).

    Called whenever the baseline moves (reset / apply), so the next poll
    re-establishes from disk. The lock keeps a concurrent poll from re-caching
    a stale baseline between these two pops.
    """
    with _drift_lock:
        ctx.pop("_drift", None)
        ctx.pop("_drift_baseline", None)
        ctx.pop("_drift_seen", None)  # reset the settle-gate tracker too
        ctx.pop("_drift_defer_count", None)  # and its max-defer streak


def _drift_baseline(ctx) -> dict | None:
    """The active ctx's baseline content, cached on the ctx (double-checked).

    Loads the persisted baseline once (a ~MB file read), then keeps it on
    ``ctx["_drift_baseline"]`` so the steady-state poll never re-reads it.
    Lazily ESTABLISHES one from the current live the first time a chip is
    watched (so accumulation starts at first sight, with no manual step).
    Returns ``None`` if the live files can't be read to seed a baseline.

    The cache get/set is guarded by ``_drift_lock`` (the disk I/O is NOT — it
    runs between the two locked sections) so concurrent pollers don't corrupt
    the shared ctx dict; a double-establish race just redoes idempotent work.
    """
    with _drift_lock:
        base = ctx.get("_drift_baseline")
        if base is not None:
            return base
    hm = _history()
    path = ctx["path"]
    base = hm.get_live_baseline(path)
    if base is None:
        try:
            live_state, live_wiring = working_copy.read_live(ctx["working_copy"])
        except (OSError, ValueError):
            return None
        try:
            ptr = hm.set_live_baseline(path, live_state, live_wiring)
        except OSError:
            logger.warning("could not persist live-drift baseline for %s", path,
                           exc_info=True)
            return None
        base = {"captured_utc": ptr["captured_utc"], "state_hash": ptr["state_hash"],
                "state": live_state, "wiring": live_wiring}
    with _drift_lock:
        # Double-check: another poll may have established meanwhile — adopt its
        # baseline so both threads agree on one captured_utc.
        existing = ctx.get("_drift_baseline")
        if existing is not None:
            return existing
        ctx["_drift_baseline"] = base
        return base


def _compute_drift(ctx, *, full: bool = False) -> dict | None:
    """Drift of the live chip vs the baseline. ``None`` when not tracked.

    mtime-GATED: the steady-state poll is two ``os.stat`` calls + a cached
    count (no content read). Only when the live mtimes actually move, the
    baseline changes, or ``full`` is requested does it read live + diff against
    the ctx-cached baseline. Returns ``{count, baseline_utc}`` (+ ``entries`` /
    ``summary`` when ``full``).
    """
    if not _drift_tracked(ctx):
        return None
    wc = ctx["working_copy"]
    try:
        sm, wm = safe_io.state_wiring_mtimes(wc.live_folder)
    except OSError:
        # Live unreadable right now — serve the last known count if we have one.
        with _drift_lock:
            c = ctx.get("_drift")
            if c is not None and not full:
                return {"count": c["count"], "baseline_utc": c["baseline_utc"]}
        return None

    base = _drift_baseline(ctx)
    if base is None:
        return None
    # Cache is valid only if BOTH the live mtimes AND the baseline identity
    # still match — so a baseline reset that didn't move the live mtimes can't
    # serve a stale count (red-team finding §3).
    with _drift_lock:
        cache = ctx.get("_drift")
        if (not full and cache is not None
                and cache.get("state_mtime") == sm
                and cache.get("wiring_mtime") == wm
                and cache.get("baseline_utc") == base["captured_utc"]):
            return {"count": cache["count"], "baseline_utc": cache["baseline_utc"]}

        # SETTLE-GATE (audit #2): the background poll must not read live CONTENT
        # while a writer may be mid-save — that breaches the working-copy
        # invariant (live content is touched only on explicit load/sync, never a
        # background poll) and, on Windows, an open read handle can collide with
        # the writer's os.replace. So a background poll only reads once the live
        # mtimes have SETTLED (seen unchanged across two consecutive polls); while
        # they're still moving we serve the last known count and defer the read to
        # a later poll. A user-opened drift panel (``full``) reads immediately —
        # that's an explicit, user-initiated read, like a sync.
        if not full:
            seen = ctx.get("_drift_seen")
            ctx["_drift_seen"] = (sm, wm)
            if seen != (sm, wm):  # live still changing (mid-burst) — don't read yet
                # MAX-DEFER CAP: a chip writing faster than the poll would defer EVERY
                # poll forever, pinning count at a stale 0 while the chip really drifted
                # (a silent sync-stop). After K defers, force ONE read anyway (fall
                # through). Safe: safe_io still refuses a torn pair (raises) and we serve
                # last-known on raise, so a forced read can't tear the baseline or 500.
                defers = ctx.get("_drift_defer_count", 0) + 1
                if defers < _DRIFT_MAX_DEFERS:
                    ctx["_drift_defer_count"] = defers
                    if cache is not None:
                        return {"count": cache["count"],
                                "baseline_utc": cache["baseline_utc"]}
                    return {"count": 0, "baseline_utc": base["captured_utc"]}
                ctx["_drift_defer_count"] = 0  # cap reached → force the read below

    try:
        live_state, live_wiring = working_copy.read_live(wc)
    except (OSError, ValueError):
        with _drift_lock:
            c = ctx.get("_drift")
            if c is not None and not full:
                return {"count": c["count"], "baseline_utc": c["baseline_utc"]}
        return None
    entries = Differ().diff((base["state"], base["wiring"]),
                            (live_state, live_wiring))
    summary = Differ.summary(entries)
    with _drift_lock:
        ctx["_drift"] = {"state_mtime": sm, "wiring_mtime": wm,
                         "count": summary["total"],
                         "baseline_utc": base["captured_utc"]}
        ctx["_drift_defer_count"] = 0   # a successful read clears the defer streak
    out = {"count": summary["total"], "baseline_utc": base["captured_utc"]}
    if full:
        out["entries"] = entries
        out["summary"] = summary
    return out


def _reset_baseline_after_apply(ctx) -> None:
    """The user just pushed their OWN state to the live chip — make THAT the
    new drift baseline so their own change isn't reported as live drift.

    Captures the content under the per-folder build lock so a concurrent
    reconcile auto-sync (which also takes that lock before reloading the store)
    can't swap a fresh qualibrate write into ``store`` mid-capture and absorb it
    into the baseline (red-team finding §5). Uses the in-memory store content
    (which equals live right after an apply), not a fresh live read. Best-effort:
    a failure here must never break the apply that already succeeded.
    """
    if not _drift_tracked(ctx):
        return
    try:
        store = ctx["store"]
        with _active_wc_lock(ctx):          # serialise vs reconcile store.reload()
            with store._lock:
                state = copy.deepcopy(store.state)
                wiring = copy.deepcopy(store.wiring)
            _history().set_live_baseline(ctx["path"], state, wiring)
        _clear_drift_cache(ctx)
    except Exception:   # noqa: BLE001
        logger.warning("baseline reset after apply-to-live failed", exc_info=True)


def _auto_pull_due(ctx: dict | None) -> bool:
    """Should the client press /auto-sync/pull right now?

    docs/120 item 8 — this rides the EXISTING drift poll rather than adding a
    poller of its own (docs/110 doctrine). It is deliberately a cheap read of
    flags already on the ctx: `live_diverged` is maintained by the throttled
    ground-truth check that runs on render and on /api/topology-mtime, so
    asking here costs nothing and cannot itself read the live files.

    It answers only "is a pull worth attempting". The POLICY -- whether local
    edits block it -- is decided in /auto-sync/pull, so it lives in one place.
    """
    sess = _auto_sync_state(ctx)
    if not sess or not sess.get("pull"):
        return False
    if (ctx or {}).get("origin", "live") != "live":
        return False
    return bool((ctx or {}).get("live_diverged"))


@bp.route("/state/drift")
def state_drift():
    """Cheap poll: how many params the live chip changed since the baseline.

    Drives the global accumulating "Live changes" banner. mtime-gated so the
    every-few-seconds poll on every page costs ~two ``os.stat`` calls at rest.
    Always 200 (even with no chip) so the banner poll never logs errors.
    """
    ctx = _active_ctx()
    # docs/120 item 8: the Auto-Sync pull signal rides this poll. It is carried
    # on EVERY branch, including the untracked ones — a chip with no drift
    # baseline can still have diverged, and dropping the flag there would make
    # auto-pull work only for chips that happen to be tracked.
    auto_pull = _auto_pull_due(ctx)
    if not _drift_tracked(ctx):
        return jsonify(ok=True, tracked=False, count=0, auto_pull=auto_pull)
    try:
        info = _compute_drift(ctx)
    except Exception:   # noqa: BLE001 — a poll must never 500
        logger.debug("drift compute failed", exc_info=True)
        return jsonify(ok=True, tracked=True, count=0, auto_pull=auto_pull)
    if info is None:
        return jsonify(ok=True, tracked=False, count=0, auto_pull=auto_pull)
    # (docs/87) ``auto_pulled`` used to ride along here as a one-shot so the
    # silent clean auto-pull became a visible toast. The user-facing path no
    # longer pulls without asking, so there is nothing to announce after the
    # fact — the banner asks BEFORE, and names the same count.
    return jsonify({"ok": True, "tracked": True, "count": info["count"],
                    "baseline_utc": info["baseline_utc"],
                    "auto_pull": auto_pull})


@bp.route("/state/drift/view")
def state_drift_view():
    """Full before/after table of everything the live chip changed since the
    baseline. Rendered into the State History panel (``?embed=1``) or the
    floating overlay (default)."""
    embed = request.args.get("embed") == "1"
    ctx = _active_ctx()
    if not ctx or ctx.get("type") != "quam":
        return render_template("_live_drift.html", tracked=False, embed=embed)
    if (ctx.get("origin") or "live") != "live":
        return render_template("_live_drift.html", tracked=False, embed=embed,
                               archive=True)
    try:
        full = _compute_drift(ctx, full=True)
    except Exception as exc:   # noqa: BLE001
        logger.warning("drift view failed", exc_info=True)
        return render_template("_live_drift.html", tracked=True, embed=embed,
                               error=str(exc))
    if full is None:
        return render_template("_live_drift.html", tracked=False, embed=embed)
    entries = full.get("entries", [])
    return render_template(
        "_live_drift.html",
        tracked=True, embed=embed,
        entries=entries[:1000],
        summary=full["summary"],
        total=full["summary"]["total"],
        baseline_utc=full["baseline_utc"],
    )


@bp.route("/state/baseline/reset", methods=["POST"])
def state_baseline_reset():
    """Reset the drift baseline to the current live chip — acknowledge all
    accumulated changes and start counting fresh from now."""
    ctx = _active_ctx()
    if not ctx or ctx.get("type") != "quam":
        return jsonify(ok=False, error="No state loaded"), 400
    if (ctx.get("origin") or "live") != "live":
        return jsonify(ok=False,
                       error="This chip was opened from a read-only archive."), 409
    wc = ctx["working_copy"]
    build_lock = _active_wc_lock(ctx)
    try:
        # Serialise vs an in-flight apply-to-live (which holds this SAME per-folder
        # lock while writing the live files): without it, baseline reset could read
        # the PRE-apply live and record THAT as the baseline, then report the user's
        # just-applied edits as fresh live drift.
        with build_lock:
            live_state, live_wiring = working_copy.read_live(wc)
            ptr = _history().set_live_baseline(ctx["path"], live_state, live_wiring)
    except (OSError, ValueError) as exc:
        return jsonify(ok=False, error=f"Could not read the live chip: {exc}"), 500
    _clear_drift_cache(ctx)
    return jsonify(ok=True, baseline_utc=ptr["captured_utc"], count=0)


@bp.route("/state/sync", methods=["POST"])
def state_sync():
    """Pull the live state files into the working copy (manual sync).

    The ``mode`` form/query param decides what happens to the user's pending
    edits, which a plain pull would otherwise discard:

    - ``discard`` (default) — drop the pending edits (the historical behavior).
    - ``reapply`` — re-apply the pending edits on top of the freshly synced
      state (best-effort), leaving them as *pending* edits for the user to push
      with a later "Apply to live".
    - ``apply`` — re-apply the pending edits on top of the freshly synced state,
      then write the merged result straight to the live chip in one step. On a
      fresh staleness conflict during that write, returns ``status=="conflict"``
      with the conflict tray (the reapply stash is preserved for a retry).

    Returns JSON ``{"status": "ok"|"conflict"|"error", "mode", "tray_html",
    "replay"}``. The client soft-refreshes from this — no page reload.
    """
    ctx = _active_ctx()
    if not ctx or ctx.get("type") != "quam":
        return jsonify({"status": "error", "message": "No state loaded"}), 400
    mode = request.values.get("mode", "discard")
    # "apply" writes the live chip — refuse on a read-only dataset archive
    # (pull/reapply only touch the working copy, so they stay allowed). Read
    # origin off the CAPTURED ctx, not the live-active one: a concurrent /load
    # could otherwise flip active_context and clear the guard mid-request.
    if mode == "apply" and (ctx.get("origin") or "live") != "live":
        return jsonify({"status": "error",
                        "message": "This chip was opened from a dataset run "
                                   "archive (read-only) — cannot apply to live."}), 409

    # docs/65 state-roundtrip: a SAVED/STAGED working copy (working_dirty — a
    # snapshot, a run's state, a revert, or /save'd edits; content that is NOT
    # representable as change-log entries) must never be destroyed by this
    # route's pull-first model:
    #  - apply: the working copy IS the intended live content. Delegate straight
    #    to the save+push path (the same machinery as /state/apply-to-live).
    #    Pulling first would overwrite the staged files with the live content
    #    and then push the live chip back onto itself — the reported "I pressed
    #    apply and it FETCHED the live state instead" bug.
    #  - discard/reapply: the pull would wipe the staged content — require an
    #    explicit force token (the client turns needs_confirm into a confirm()).
    # The stash carve-out: after an apply CONFLICT the user's edits live in
    # ctx["pending_reapply"] and the working files are expendable — pull+replay
    # is the designed resolution there, and short-circuiting it would retry the
    # same stale push forever. So the protection applies only when there is no
    # stash to replay — UNLESS a STAGED BASE is present (audit-r10): a stage
    # followed by edits fills the stash with only the EDITS while the staged
    # snapshot lives solely in the working files, so a stash-based carve-out
    # would pull-destroy it. staged_base is set by the stage routes and
    # cleared on apply-success / wholesale pull.
    if ctx.get("working_dirty") and (
            ctx.get("staged_base") or not ctx.get("pending_reapply")):
        if mode == "apply":
            return _sync_pull_apply_to_live(ctx, None, pulled_other_changes=False)
        if request.values.get("force") != "1":
            return jsonify({
                "status": "needs_confirm",
                "mode": mode,
                "message": ("The working state holds loaded/saved content that "
                            "is not on the live chip yet — pulling the live "
                            "chip will DISCARD it."),
            })

    wc = ctx["working_copy"]
    store = ctx["store"]

    # The user's edits to re-apply = whatever is stashed (saved edits the change
    # log no longer holds) plus any still-unsaved change-log edits, with the
    # latter winning per path. Snapshot BEFORE the sync's reload clears them.
    with store._lock:
        pending = _merge_reapply(_pending_reapply(), _capture_change_log_as_updates(store))

    # Build lock: sync_from_live rewrites the working folder and advances the
    # (mtime, mtime, hash) sync point on the SAME WorkingCopy a concurrent
    # _activate_quam reconcile may be auto-syncing — unserialised, the two
    # collide on identical .tmp paths and interleave the triplet assignment.
    build_lock = _active_wc_lock(ctx)
    # Did the pull absorb changes the on-screen surface hasn't seen? Compare the
    # sync point before vs after: a moved synced_live_hash ⇒ the live files
    # changed since the last sync (an experiment wrote between edits) and the
    # grid/tree behind the tray is now stale. The client uses this to decide
    # whether a clean one-click apply still needs the one surface refresh (a
    # blanket refresh on every apply was the "blink/freeze" — but suppressing it
    # when third-party changes WERE pulled left the grid silently stale).
    _pre_sync_hash = wc.synced_live_hash
    pulled_other_changes = False
    replay = None
    try:
        # Hold the build lock across sync + rebuild + replay (not just sync): the
        # scheduler worker's post-node _reconcile_cached_quam_ctx takes this SAME
        # per-folder lock and fires exactly when a node finishes — i.e. exactly when
        # users click Sync. Rebuilding outside the lock let its reload/index/
        # wiring_json/flag rebuild interleave with ours (mixing two reload
        # generations), and store.reload() could read the working folder mid-rewrite
        # (safe_io's torn-pair refusal → ValueError → 500). Matches the
        # State-History callers, which already hold the lock across the rebuild.
        with build_lock:
            working_copy.sync_from_live(wc)
            pulled_other_changes = (_pre_sync_hash is not None
                                    and wc.synced_live_hash != _pre_sync_hash)
            # Rebuild the store + derived objects from the freshly-synced copy.
            ctx["_alarm_reason"] = "live-pull"
            _rebuild_after_working_copy_replaced(ctx)   # the pull consumed the change
            if mode in ("reapply", "apply") and pending:
                replay = _replay_updates(ctx["modifier"], pending)
                _invalidate_engine_cache(ctx)  # replay used _defer_hooks — refresh caches (ctx-pinned, r16 6)
                ctx["working_dirty"] = False  # edits unsaved in the change log, not saved
    except FileNotFoundError:
        return jsonify({"status": "error", "message": "Live state folder not found"}), 404
    except (OSError, ValueError) as exc:
        return jsonify({"status": "error", "message": str(exc)}), 500

    # "apply" goes one step further: save the re-applied edits and push them to
    # the live chip now, instead of leaving them pending for a second click.
    if mode == "apply":
        return _sync_pull_apply_to_live(ctx, replay,
                                        pulled_other_changes=pulled_other_changes)

    # reapply / discard: the pull consumed the stash; edits (if any) are now
    # pending in the change log (reapply) or intentionally dropped (discard).
    _clear_reapply(ctx)

    # Record a Param History snapshot of the now-current live state.
    try:
        _history().check_and_snapshot(ctx["path"], "auto",
                                      project=_scope_for(ctx["path"], ctx))
    except Exception:
        logger.warning("History snapshot after sync failed", exc_info=True)
    return jsonify({
        "status": "ok",
        "mode": mode,
        "tray_html": _tray_html(),
        "replay": replay,
    })


def _sync_pull_apply_to_live(ctx, replay, *, pulled_other_changes=False):
    """Finish a ``mode=apply`` sync: save the re-applied edits to the working
    copy and push them to the live chip. Mirrors ``/state/apply-to-live`` but
    returns JSON so ``doStateSync`` can drive it. On a fresh staleness conflict
    the reapply stash is kept so the user can retry / force / discard.
    ``pulled_other_changes`` (echoed to the client) — the pull absorbed live
    changes beyond the user's own edits, so the surface needs one refresh."""
    store = ctx["store"]
    wc = ctx["working_copy"]
    saver = ctx["saver"]

    # Re-stash exactly the edits now in the change log, so save()'s clear can't
    # lose them if the apply below hits a fresh conflict. Pin to the CAPTURED ctx
    # (passed into this helper): a concurrent /load flipping the active context
    # would otherwise stash onto the wrong chip, so a conflict retry from the tray
    # replays an empty stash and the user's edits vanish.
    with store._lock:
        _clear_reapply(ctx)
        _stash_reapply(_capture_change_log_as_updates(store), ctx)
        _jrn_units = _journal_prepare(store, ctx)   # docs/107: outgoing log

    if store.change_log:
        try:
            with _active_wc_lock(ctx):
                saver.save()
        except OSError as exc:
            logger.warning(
                "pull-apply save failed with %d unsaved entries: %s",
                len(store.change_log), exc,
            )
            return jsonify({
                "status": "error",
                "message": (
                    f"Save failed: {exc}. Your edits are still in memory — "
                    "close any program that has state.json open and retry."
                ),
            }), 500
        _journal_commit(ctx, _jrn_units)   # docs/107: the log is gone — journal it
        _set_working_dirty(True, ctx)

    # docs/20 v2 "Revert last apply": capture the PRE-apply live content.
    # audit-r10: pinned to the CAPTURED ctx (a concurrent /load must never
    # divert the snapshot to another chip), and a None return is resolved by
    # CONTENT MATCH — check_and_snapshot dedups against every hash ever seen,
    # so "newest snapshot" is the wrong revert target after an A-B-A cycle.
    pre_apply_ts = None
    try:
        _hm = _history()
        _pre = _hm.check_and_snapshot(
            ctx["path"], "auto",
            defer_index=not current_app.config.get("TESTING"),
            project=_scope_for(ctx["path"], ctx))
        if _pre is not None:
            pre_apply_ts = _pre.timestamp
        else:
            pre_apply_ts = _hm.snapshot_ts_for_current_content(ctx["path"])
    except Exception:
        logger.warning("Pre-apply snapshot failed", exc_info=True)

    try:
        with _active_wc_lock(ctx):
            working_copy.apply_to_live(wc, force=False)
    except working_copy.StaleLiveError:
        # The live chip changed again while we merged. Keep the stash and hand
        # back the conflict tray so the user can retry / force / discard.
        # staged_conflict (docs/65): no stash to replay — the working copy IS
        # the payload (a staged snapshot / saved edits), so "pull & re-apply"
        # would replay nothing and lose it; the tray offers only the honest
        # choices (force-overwrite or pull-and-discard).
        return jsonify({
            "status": "conflict",
            "mode": "apply",
            "tray_html": render_template(
                "_state_apply_conflict.html",
                staged_conflict=bool(ctx.get("working_dirty"))
                and (bool(ctx.get("staged_base"))
                     or not ctx.get("pending_reapply"))),
            "replay": replay,
        })
    except (OSError, ValueError) as exc:
        _pd = ("the live folder is READ-ONLY (network share?) — "
               if getattr(exc, "errno", None) in (13, 1) else "")
        return jsonify({"status": "error",
                        "message": f"Apply to live failed: {_pd}{exc}"}), 500
    except Exception as exc:  # noqa: BLE001 — r16 6: honest message, never a dropped 500
        logger.exception("apply-to-live (sync) unexpected failure")
        return jsonify({"status": "error",
                        "message": f"Apply to live failed unexpectedly: "
                                   f"{type(exc).__name__}: {exc}"}), 500

    _set_working_dirty(False, ctx)
    ctx["staged_base"] = False   # the staged content reached live (audit-r10)
    _clear_reapply(ctx)  # edits are on the live chip now — nothing left to re-apply
    ctx["live_diverged"] = False  # live now holds the merged working content
    ctx.pop("live_drift_count", None)   # docs/116
    _reset_baseline_after_apply(ctx)  # the user's own change isn't "live drift"
    if pre_apply_ts:
        ctx["last_apply"] = {
            "pre_ts": pre_apply_ts,
            "at": datetime.now().isoformat(timespec="seconds"),
        }
    else:
        # audit-r10: no trustworthy pre-apply target — an honestly missing
        # button beats offering a stale/wrong revert (the previous apply's
        # memo must not survive this one).
        ctx.pop("last_apply", None)
    # Snapshot files + meta SYNCHRONOUSLY (so the State-History timeline refreshed
    # by this same response's stateHistoryChanged sees the new snapshot, and the
    # content is captured before any concurrent writer can change the live files);
    # only the SQLite indexing is deferred (defer_index) — it's the dominant
    # snapshot cost on the 9p filesystem (~270ms on a 21Q chip) and is idempotent
    # + self-healing. Synchronous under TESTING for deterministic assertions.
    try:
        _history().check_and_snapshot(
            ctx["path"], "save",
            defer_index=not current_app.config.get("TESTING"),
            project=_scope_for(ctx["path"], ctx))
    except Exception:
        logger.warning("History snapshot after pull-apply failed", exc_info=True)
    return jsonify({
        "status": "ok",
        "mode": "apply",
        "tray_html": _tray_html(),
        "replay": replay,
        "pulled_other_changes": pulled_other_changes,
    })


@bp.route("/state/apply-to-live", methods=["POST"])
def state_apply_to_live():
    """Push the working copy's state + wiring to the live chip.

    In-memory edits are saved to the working copy first.  On a staleness
    conflict (the live files changed since the last sync) the user is shown a
    warning and an explicit force-overwrite option.
    """
    ctx = _active_ctx()
    if not ctx or ctx.get("type") != "quam":
        return render_template("_status.html", message="No state loaded", level="warning")
    blocked = _archive_write_blocked(ctx)   # guard the CAPTURED ctx (TOCTOU)
    if blocked is not None:
        return blocked
    wc = ctx["working_copy"]
    store = ctx["store"]
    saver = ctx["saver"]
    force = request.values.get("force") == "1"
    # docs/117: an armed session turns THIS route into the auto-apply writer.
    # It is still the only thing that writes live; the session just presses it.
    _auto = _auto_apply_state(ctx)

    # Stash the edits before save() clears the change log, so if this hits a
    # staleness conflict the subsequent pull can re-apply or stage them. Pin to
    # the captured ctx so a concurrent /load can't divert the stash to another chip.
    with store._lock:
        _stash_reapply(_capture_change_log_as_updates(store), ctx)
        _jrn_units = _journal_prepare(          # docs/107: outgoing log
            store, ctx,
            meta={"src": "auto", "at": time.time()} if _auto else None)

    if store.change_log:
        try:
            with _active_wc_lock(ctx):
                saver.save()
        except OSError as exc:
            # Narrow except (Phase 2 finding §5.1). The change_log is still
            # populated, so a retry after the user clears the file lock will
            # re-attempt the save. Tell the user that their edits are not
            # lost; log the change_log length for admin correlation.
            logger.warning(
                "apply-to-live save failed with %d unsaved entries: %s",
                len(store.change_log), exc,
            )
            # docs/114 (#16): name the permission case explicitly — a
            # read-only share is the common cause and the OS says so.
            _pd = ("the WORKING folder is not writable — "
                   if getattr(exc, "errno", None) in (13, 1) else "")
            return render_template(
                "_status.html",
                message=(
                    f"Save failed: {_pd}{exc}. Your edits are still in memory — "
                    "close any program that has state.json open and retry."
                ),
                level="error",
            ), 500
        _journal_commit(ctx, _jrn_units)   # docs/107: the log is gone — journal it
        _set_working_dirty(True, ctx)

    # docs/20 v2 "Revert last apply": capture the PRE-apply live content.
    # audit-r10: ctx-pinned + content-matched fallback (see the sync twin —
    # "newest snapshot" is wrong after an A-B-A revert cycle).
    # docs/117: under auto-apply the anchor is the SESSION, not the flush —
    # "revert last apply" means "put back what the chip held when I armed it"
    # (the user's own choice; per-change revert is the applied log's X). Taking
    # it once is also what stops a 10-minute session writing 200 full snapshots.
    pre_apply_ts = (_auto or {}).get("pre_ts")
    if not pre_apply_ts:
        try:
            _hm = _history()
            _pre = _hm.check_and_snapshot(
                ctx["path"], "auto",
                defer_index=not current_app.config.get("TESTING"),
                project=_scope_for(ctx["path"], ctx))
            if _pre is not None:
                pre_apply_ts = _pre.timestamp
            else:
                pre_apply_ts = _hm.snapshot_ts_for_current_content(ctx["path"])
        except Exception:
            logger.warning("Pre-apply snapshot failed", exc_info=True)
        if _auto is not None and pre_apply_ts:
            _auto["pre_ts"] = pre_apply_ts

    try:
        with _active_wc_lock(ctx):
            working_copy.apply_to_live(wc, force=force)
    except working_copy.StaleLiveError:
        # stash kept for the pull choice; staged_conflict — see the sync twin
        body = render_template(
            "_state_apply_conflict.html",
            staged_conflict=bool(ctx.get("working_dirty"))
            and (bool(ctx.get("staged_base"))
                 or not ctx.get("pending_reapply")))
        # docs/117: nothing was written (apply_to_live raises BEFORE its write)
        # and the edit is safe in the working copy, but a background writer the
        # user may have forgotten about must never keep pushing at a chip that
        # moved. Disarm, and say so where they are looking.
        return _auto_disarm_response(ctx, body, "conflict") if _auto else body
    except (OSError, ValueError) as exc:
        # docs/114 (#16): the read-only case fails HERE (the LIVE write), not
        # in the working-copy save — name it where it actually happens.
        _pd = ("the live folder is READ-ONLY (network share?) — "
               if getattr(exc, "errno", None) in (13, 1) else "")
        _body = render_template("_status.html",
                                message=f"Apply to live failed: {_pd}{exc}",
                                level="error")
        if _auto:
            return _auto_disarm_response(ctx, _body, "error", status=500)
        return _body, 500
    except Exception as exc:  # noqa: BLE001 — r16 6: honest message, never a dropped 500
        logger.exception("apply-to-live unexpected failure")
        _body = render_template(
            "_status.html", level="error",
            message=f"Apply to live failed unexpectedly: "
                    f"{type(exc).__name__}: {exc}")
        if _auto:
            return _auto_disarm_response(ctx, _body, "error", status=500)
        return _body, 500

    _set_working_dirty(False, ctx)
    ctx["staged_base"] = False   # the staged content reached live (audit-r10)
    _clear_reapply(ctx)  # the edits are now on the live chip — nothing left to re-apply
    ctx["live_diverged"] = False  # live now holds the working content (incl. force)
    ctx.pop("live_drift_count", None)   # docs/116
    _reset_baseline_after_apply(ctx)  # the user's own change isn't "live drift"
    if pre_apply_ts:
        ctx["last_apply"] = {
            "pre_ts": pre_apply_ts,
            "at": datetime.now().isoformat(timespec="seconds"),
        }
    else:
        # audit-r10: no trustworthy pre-apply target — an honestly missing
        # button beats offering a stale/wrong revert (the previous apply's
        # memo must not survive this one).
        ctx.pop("last_apply", None)
    # Snapshot files + meta synchronously; only the SQLite indexing is deferred —
    # see the pull-apply path above for the full rationale.
    # docs/117: under auto-apply this would run once per edit — a full copy of
    # state+wiring every few seconds. Throttled per session; the disarm route
    # takes the unconditional closing one, so a session always ends captured.
    _snap_now = True
    if _auto is not None:
        _last = float(_auto.get("last_snap") or 0.0)
        _snap_now = (time.time() - _last) >= _AUTO_SNAPSHOT_MIN_S
    if _snap_now:
        try:
            _history().check_and_snapshot(
                ctx["path"], "save",
                defer_index=not current_app.config.get("TESTING"),
                project=_scope_for(ctx["path"], ctx))
            if _auto is not None:
                _auto["last_snap"] = time.time()
        except Exception:
            logger.warning("History snapshot after apply failed", exc_info=True)
    if _auto is not None:
        # The applied log IS the feedback; one success toast per edit would be
        # noise the user cannot dismiss fast enough.
        _auto["flushes"] = int(_auto.get("flushes") or 0) + 1
        resp = make_response(_tray_html())
        resp.headers["HX-Trigger"] = ("liveDriftChanged, stateHistoryChanged, "
                                      "autoApplyApplied")
        return resp
    toast = render_template(
        "_status.html", message="Applied to the live chip.", level="success")
    resp = make_response(_tray_html() + "\n"
                         + f'<div id="status-bar" hx-swap-oob="innerHTML">{toast}</div>')
    # The live chip + baseline just moved — refresh the open State-History timeline (a
    # new snapshot was captured) + the embedded drift panel + global banner, instead of
    # pre-apply state until a manual reload (audit P0-5/6). Use stateHistoryChanged (a
    # dedicated timeline-refresh signal), NOT stateRestored — the latter also closes any
    # open qubit/pair inspector (app.js:1423) and a routine edit→apply must not blank it
    # (audit P1). stateRestored stays reserved for real stage/restore.
    resp.headers["HX-Trigger"] = "liveDriftChanged, stateHistoryChanged"
    return resp


@bp.route("/state/overwrite-live/preflight")
def state_overwrite_live_preflight():
    """Everything the "keep mine, overwrite live" confirm needs, in one call.

    The third option (docs/86). Pull and push are NOT symmetric: a pull discards
    the working copy while the live files stay on disk, but a push discards live
    content that may be a real calibration another program just wrote. So the
    confirm has to name what disappears — and there is no honest way to know that
    without reading the live files, which is why this is a separate endpoint
    fired ON CLICK rather than a count baked into a banner that renders on every
    page (docs/28: live content is read only for an explicit user action).

    Returns ``{ok, live_changes, unsaved, reversible, run_active, run_label}``.
    A failed live read is NOT an error here — the user can still choose to
    overwrite; the confirm just says the count is unknown.
    """
    ctx = _active_ctx()
    if not ctx or ctx.get("type") != "quam":
        return jsonify({"ok": False, "message": "No state loaded"}), 400
    if (ctx.get("origin") or "live") != "live":
        return jsonify({"ok": False,
                        "message": "This chip was opened from a dataset run "
                                   "archive (read-only)."}), 409

    store = ctx["store"]
    live_changes = None
    try:
        live_state, live_wiring = working_copy.read_live(ctx["working_copy"])
        live_changes = len(Differ().diff(store, (live_state, live_wiring)))
    except (FileNotFoundError, OSError, ValueError):
        logger.info("overwrite-live preflight could not read the live files",
                    exc_info=True)

    # A run writing this chip right now would simply re-write whatever we push
    # the moment its node finishes — worth saying, never worth blocking (the
    # user may be overwriting precisely because a run went wrong).
    run_active, run_label = False, None
    try:
        inst = _sched_inst()
        run_active = scheduler.is_active(inst)
        if run_active:
            owner = scheduler.foreign_owner(
                {"run": (scheduler.load_queue(inst).get("run") or {})})
            if owner is not None:
                run_label = scheduler.owner_label(*owner)
    except Exception:       # noqa: BLE001 — an advisory must never break the gate
        logger.debug("overwrite-live preflight run probe failed", exc_info=True)

    with store._lock:
        unsaved = len(store.change_log)
    return jsonify({
        "ok": True,
        "live_changes": live_changes,
        "unsaved": unsaved,
        # The push snapshots the pre-apply live first, which is what powers the
        # tray's "Revert last apply" — so this is a reversible action and the
        # confirm should say so.
        "reversible": True,
        "run_active": bool(run_active),
        "run_label": run_label,
    })


# ======================================================================
# Export
# ======================================================================


@bp.route("/export")
def export_csv():
    engine = _engine()
    if not engine:
        return render_template("_status.html", message="No state loaded", level="warning")

    props = request.args.getlist("props") or [
        "f_01", "readout_frequency", "T1", "T2ramsey",
        "readout_amplitude", "readout_threshold", "gate_fidelity_avg",
    ]
    rows = engine.summary_table(props)
    fieldnames = ["id"] + props

    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=fieldnames, extrasaction="ignore")
    writer.writeheader()
    writer.writerows(rows)

    mem = io.BytesIO(output.getvalue().encode("utf-8"))
    mem.seek(0)
    return send_file(mem, mimetype="text/csv", as_attachment=True, download_name="quam_summary.csv")


@bp.route("/api/topology/sparklines/<qubit>")
def topology_sparklines(qubit: str):
    """Lazy per-qubit Param-History sparklines for the Chip Status '…more' popup.

    Reuses the Param History index (extract_property_history) + the same
    server-rendered SVG (render_sparkline_svg_inner). Emits a row ONLY for
    metrics with a real ≥2-point series — assignment_fidelity / cz are never
    tracked here, so they honestly get no sparkline rather than a fake flat line.
    """
    engine = _engine()
    store = _store()
    path = _active_path()
    if not engine or not store or not path:
        return ""
    try:
        qd = engine.get_qubit(qubit)
    except Exception:
        qd = {}
    hm = _history()
    rows = []
    for r in hm.extract_property_history(path, list(DEFAULT_TRACKED_PROPERTIES),
                                         qubit_filter=[qubit], downsample=40):
        prop = r["property"]
        cur = qd.get(prop)
        cur_num = float(cur) if isinstance(cur, (int, float)) and not isinstance(cur, bool) else None
        if cur_num is not None and not chip_health.physicality(prop, cur_num):
            cur_num = None
        # Physicality-gate the series — the rest of Chip Status quarantines an
        # unphysical fit (e.g. a −473µs T2), so the trend must not plot it either.
        # physicality() only bounds the keys that have one (T1/T2 >0); frequencies
        # / amplitudes are unconstrained and pass through unchanged.
        phys_vals = [p for p in r["values"] if chip_health.physicality(prop, p.get("value"))]
        svg = HistoryManager.render_sparkline_svg_inner(phys_vals, current=cur_num)
        if not svg:
            continue  # <2 finite points → no real trend (honest gap)
        nums = [p["value"] for p in phys_vals
                if isinstance(p.get("value"), (int, float)) and not isinstance(p.get("value"), bool)]
        delta = nums[-1] - nums[-2] if len(nums) >= 2 else None
        delta_pct = (delta / abs(nums[-2]) * 100) if (delta is not None and nums[-2]) else None
        meta = chip_health.metric_meta(prop)
        good = None
        if delta not in (None, 0) and meta["direction"] in ("higher", "lower"):
            improving = (delta > 0) if meta["direction"] == "higher" else (delta < 0)
            good = bool(improving)
        rows.append({
            "property": prop, "label": meta["label"], "abbr": meta["abbr"],
            "svg_inner": svg, "n": len(nums), "delta_pct": delta_pct,
            "trend": None if delta in (None, 0) else ("up" if delta > 0 else "down"),
            "good": good,
        })
    return render_template("_topo_sparklines.html", rows=rows,
                           snapshots=len(hm.list_snapshots(path)))


@bp.route("/topology/report")
def export_report():
    """Download a dated Chip Report Card (md / csv / html) — the on-screen Chip
    Status health, computed server-side from the SAME trust-gated records, in a
    shareable file. Plain download (the link has no hx-* so HTMX won't swap it)."""
    engine = _engine()
    if not engine:
        return render_template("_status.html", message="No state loaded", level="warning")
    store = _store()

    from pathlib import Path
    from quam_state_manager.core import report_card
    from quam_state_manager.core.history import chip_name_for

    path = _active_path()
    chip = chip_name_for(Path(path)) if path else "chip"
    diag_findings = [f.as_dict() for f in diagnostics.lint_state(store)] if store else []
    # Honour the user's UI-edited thresholds (sent as a JSON query param by the
    # export link) so the card's below-spec counts MATCH the on-screen header.
    # Falls back to the seed defaults when absent/malformed.
    thresholds = None
    raw_th = request.args.get("thresholds")
    if raw_th:
        try:
            parsed = json.loads(raw_th)
            if isinstance(parsed, dict):
                thresholds = parsed
        except (ValueError, TypeError):
            thresholds = None
    report = report_card.build_report(engine, chip_name=chip, diag_findings=diag_findings,
                                      thresholds=thresholds)

    fmt = (request.args.get("format") or "md").lower()
    if fmt == "csv":
        body, mime, ext = report_card.render_csv(report), "text/csv", "csv"
    elif fmt == "html":
        body, mime, ext = report_card.render_html(report), "text/html", "html"
    else:
        body, mime, ext = report_card.render_markdown(report), "text/markdown", "md"

    safe_chip = re.sub(r"[^A-Za-z0-9_.-]+", "_", chip) or "chip"
    stamp = report["generated_at"][:10]
    mem = io.BytesIO(body.encode("utf-8"))
    mem.seek(0)
    return send_file(mem, mimetype=mime, as_attachment=True,
                     download_name=f"chip_report_{safe_chip}_{stamp}.{ext}")


# ======================================================================
# Diff
# ======================================================================


def _hub_redirect(url: str):
    """Legacy-surface → Compare-hub translation response (docs/49 P4).

    HTMX requests need ``HX-Redirect`` — a plain 302 would be *followed*
    by htmx and swapped into the pane instead of navigating (A7); plain
    browser requests get a real redirect.
    """
    if _is_htmx():
        resp = make_response()
        resp.headers["HX-Redirect"] = url
        return resp
    return redirect(url)


def _legacy_src_token(path: str) -> str:
    """ws:/run: token for a legacy folder path — archive-run layouts get the
    honest ``run:`` origin (RUN badge), everything else ``ws:``."""
    try:
        p = Path(path)
        if p.name == "quam_state" and _RUN_DIR_RE.match(p.parent.name):
            return f"run:{path}"
    except (OSError, ValueError):
        pass
    return f"ws:{path}"


_DIFF_TABS = ("state", "wiring", "node", "data")
_DIFF_LIST_PAGE = 300      # ranked rows per list page
# One diff is one flatten of two documents (20-45 ms measured on real chips).
# The tab strip and the tree/list toggle re-ask for the same pair, so a small
# content-hash-keyed memo makes those switches free.
_DIFF_MEMO: "OrderedDict[tuple, dict]" = OrderedDict()
_DIFF_MEMO_MAX = 6
_diff_memo_lock = threading.Lock()


def _diff_side_doc(src, tab: str) -> tuple[Any, str]:
    """``(document, unavailable_reason)`` for one side of one tab."""
    if tab in ("state", "wiring"):
        entry = compare_sources.DEFAULT_POOL.get(src.content_hash)
        if entry is None:
            entry = compare_sources.resolve_source(
                src.ref, history_root=_hub_history_root(),
                working_lookup=_hub_working_lookup)
            entry = compare_sources.DEFAULT_POOL.get(src.content_hash)
        if entry is None:
            return None, "content unavailable"
        return (entry.state if tab == "state" else entry.wiring), ""
    if tab == "node":
        # A run's node.json sits BESIDE its quam_state folder. Snapshots and
        # the working copy have no run behind them — say so rather than
        # pretending the tab is empty.
        node = Path(src.path).parent / "node.json"
        if src.origin in ("history", "working") or not node.exists():
            return None, "no node.json — this side is not an experiment run"
        try:
            return safe_io.read_json(node), ""
        except (OSError, ValueError) as exc:
            return None, f"node.json unreadable ({type(exc).__name__})"
    if tab == "data":
        folder = Path(src.path).parent
        if src.origin in ("history", "working"):
            return None, "no data files — this side is not an experiment run"
        try:
            from quam_state_manager.core import ndview
            files = ndview.list_h5_files(folder)
        except Exception:      # noqa: BLE001
            files = []
        if not files:
            return None, "no .h5 data files in this run"
        inv: dict[str, Any] = {}
        for name in files:
            probe = ndview.probe_file(folder / name)
            if not probe.get("ok"):
                inv[name] = {"error": probe.get("error") or "unreadable"}
                continue
            inv[name] = {v["name"]: {"shape": v.get("shape"),
                                     "dims": v.get("dims")}
                         for v in probe.get("vars", [])}
        return inv, ""
    return None, "unknown tab"


def _diff_payload(src_a, src_b, tab: str, *, with_rows: bool) -> dict:
    key = (src_a.content_hash, src_b.content_hash, src_a.ref, src_b.ref, tab)
    with _diff_memo_lock:
        hit = _DIFF_MEMO.get(key)
        if hit is not None:
            _DIFF_MEMO.move_to_end(key)
            return hit
    doc_a, why_a = _diff_side_doc(src_a, tab)
    doc_b, why_b = _diff_side_doc(src_b, tab)
    if doc_a is None or doc_b is None:
        return {"ok": False, "unavailable": why_a or why_b,
                "counts": {}, "rows": [], "tree_a": {}, "tree_b": {}}
    res = json_diff.build(doc_a, doc_b)
    res["ok"] = True
    res["unavailable"] = ""
    with _diff_memo_lock:
        _DIFF_MEMO[key] = res
        _DIFF_MEMO.move_to_end(key)
        while len(_DIFF_MEMO) > _DIFF_MEMO_MAX:
            _DIFF_MEMO.popitem(last=False)
    return res if with_rows else {**res, "rows": []}


def _diff_default_refs() -> tuple[str, str]:
    """The pair a bare /diff opens on: the newest snapshot vs what is loaded.

    That is the question a user has when they arrive without having picked
    anything — *what have I changed?* — and it needs no picking at all.
    """
    ctx = _active_ctx()
    if not ctx or ctx.get("type") != "quam":
        return "", ""
    path = ctx.get("path") or ""
    b = f"working:{path}"
    try:
        hm = _history()
        chip_dir = hm.resolve_chip_dir(Path(path))[0]
        snaps = hm.list_snapshots(path)
        if snaps:
            return f"hist:{Path(chip_dir).name}/{snaps[0].timestamp}", b
    except Exception:      # noqa: BLE001 — a bare /diff must still open
        logger.debug("diff default refs failed", exc_info=True)
    return "", b


def _diff_source_options() -> list[dict]:
    """What the two pickers offer: the loaded chip, then its snapshots."""
    out: list[dict] = []
    ctx = _active_ctx()
    if ctx and ctx.get("type") == "quam" and ctx.get("path"):
        out.append({"ref": f"working:{ctx['path']}",
                    "label": "Loaded chip (with unsaved edits)"})
    try:
        hm = _history()
        path = ctx.get("path") if ctx else None
        if path:
            chip = Path(hm.resolve_chip_dir(Path(path))[0]).name
            for m in hm.list_snapshots(path)[:60]:
                ts = m.timestamp
                when = (f"{ts[0:4]}-{ts[4:6]}-{ts[6:8]} {ts[9:11]}:{ts[11:13]}"
                        if len(ts) >= 13 else ts)
                what = m.experiment_name or (m.trigger or "snapshot")
                out.append({"ref": f"hist:{chip}/{ts}",
                            "label": f"{when} · {what}"})
    except Exception:      # noqa: BLE001
        logger.debug("diff source options failed", exc_info=True)
    return out


@bp.route("/diff", methods=["GET", "POST"])
def diff_view():
    """The diff workbench (docs/84) — two sources, four tabs, differences only.

    Replaces the legacy 2-folder form as the front door. The Compare hub still
    owns the hard cases (mapping entities across different devices); this owns
    the common one, which is the one users actually have.

    Legacy callers are still honoured: a POST, or a GET carrying the hub's own
    query grammar, redirects into the hub exactly as before.
    """
    legacy = request.method == "POST" or any(
        request.args.get(k) for k in ("src", "bucket", "preset", "map"))
    if legacy:
        params: list[tuple[str, str]] = []
        if request.method == "POST":
            for key in ("path_a", "path_b"):
                path = (request.form.get(key) or "").strip()
                if path:
                    params.append(("src", _legacy_src_token(path)))
        else:
            params = list(request.args.items(multi=True))
        if not params:
            params.append(("from", "diff"))
        return _hub_redirect(f"/compare-hub?{urlencode(params)}")

    a_ref = (request.args.get("a") or "").strip()
    b_ref = (request.args.get("b") or "").strip()
    tab = (request.args.get("tab") or "state").strip()
    if tab not in _DIFF_TABS:
        tab = "state"
    view = "list" if (request.args.get("view") or "").strip() == "list" else "tree"
    # The list is server-rendered, so it pages: 2,257 ranked rows of a real
    # cross-generation diff serialise to 1.2 MB, and the rows a user reads are
    # the first screenful.
    limit = _int_arg("rows", _DIFF_LIST_PAGE, minimum=1)
    limit = min(limit, json_diff.ROW_CAP)
    if not a_ref and not b_ref:
        a_ref, b_ref = _diff_default_refs()

    error = ""
    src_a = src_b = None
    for ref, slot in ((a_ref, "a"), (b_ref, "b")):
        if not ref:
            continue
        try:
            resolved = _hub_resolve_one(ref)
        except compare_sources.SourceError as exc:
            error = str(exc)
            continue
        if slot == "a":
            src_a = resolved
        else:
            src_b = resolved

    payload = None
    rows = []
    more = 0
    if src_a is not None and src_b is not None and not error:
        try:
            payload = _diff_payload(src_a, src_b, tab, with_rows=(view == "list"))
            if view == "list":
                all_rows = payload.get("rows") or []
                rows = all_rows[:limit]
                more = max(0, len(all_rows) - len(rows))
        except Exception as exc:      # noqa: BLE001 — never 500 the menu
            logger.warning("diff build failed: %s", exc, exc_info=True)
            error = "Could not build this diff."

    template = "_diff_workbench.html" if _is_htmx() else "diff_workbench.html"
    return render_template(
        template,
        **_ctx(page="diff", a_ref=a_ref, b_ref=b_ref, tab=tab, view=view,
               src_a=src_a, src_b=src_b, payload=payload, error=error,
               rows=rows, more=more, next_rows=limit + _DIFF_LIST_PAGE,
               tabs=_DIFF_TABS, options=_diff_source_options()))


@bp.route("/diff/snapshots")
def diff_snapshots():
    """Two snapshot timestamps → the diff workbench.

    The entry point for BOTH "Compare selected" buttons in the history pages.
    They used to land on two different surfaces; the chip dir is resolved here
    so neither page has to carry it in the DOM.
    """
    ts_a = (request.args.get("ts_a") or "").strip()
    ts_b = (request.args.get("ts_b") or "").strip()
    if not ts_a or not ts_b:
        return _hub_redirect("/diff")
    chip = (request.args.get("chip_key") or "").strip()
    if not chip:
        try:
            chip = Path(_history().resolve_chip_dir(Path(_active_path()))[0]).name
        except Exception:      # noqa: BLE001
            return _hub_redirect("/diff")
    # Oldest on the left, so the diff reads forward in time like every other
    # before→after surface.
    a, b = sorted((ts_a, ts_b))
    return _hub_redirect(
        f"/diff?a=hist:{quote(chip)}/{quote(a)}&b=hist:{quote(chip)}/{quote(b)}")


@bp.route("/diff/runs")
def diff_runs():
    """Two run uids → the diff workbench, opened on the node.json tab.

    Two runs differ in what was ASKED (node parameters) more often than in the
    chip state, so that is the tab this lands on.
    """
    uids = [u for u in (request.args.get("uids") or "").split(",") if u.strip()]
    refs: list[str] = []
    for uid in uids[:2]:
        resolved = _resolve_run(uid.strip())
        if not resolved:
            continue
        store, run_id, _label = resolved
        run = store.get_run(run_id) or {}
        folder = run.get("folder_path")
        if folder:
            refs.append(f"run:{Path(folder) / 'quam_state'}")
    if len(refs) != 2:
        return _hub_redirect("/diff")
    return _hub_redirect(
        f"/diff?a={quote(refs[0])}&b={quote(refs[1])}&tab=node")


@bp.route("/diff/data")
def diff_data():
    """The pruned trees for the tree view — JSON, rendered by the JSON tree."""
    a_ref = (request.args.get("a") or "").strip()
    b_ref = (request.args.get("b") or "").strip()
    tab = (request.args.get("tab") or "state").strip()
    if tab not in _DIFF_TABS:
        tab = "state"
    try:
        src_a = _hub_resolve_one(a_ref)
        src_b = _hub_resolve_one(b_ref)
    except compare_sources.SourceError as exc:
        return jsonify(ok=False, error=str(exc)), 200
    try:
        res = _diff_payload(src_a, src_b, tab, with_rows=False)
    except Exception as exc:      # noqa: BLE001
        logger.warning("diff data failed: %s", exc, exc_info=True)
        return jsonify(ok=False, error="Could not build this diff."), 200
    return jsonify(ok=res.get("ok", False), unavailable=res.get("unavailable", ""),
                   counts=res.get("counts", {}), tree_a=res.get("tree_a", {}),
                   tree_b=res.get("tree_b", {}),
                   truncated=res.get("truncated", False),
                   label_a=src_a.label, label_b=src_b.label)


# ======================================================================
# Workspace
# ======================================================================


# Scoped-search support for the sidebar workspace filter — mirrors the
# Datasets-page search (web/static/dataset-virtual.js: tokenize/parseQuery).
# Scopes map to the fields a tree entry actually has.
_SIDEBAR_SCOPE_ALIASES = {"e": "name", "exp": "name", "d": "date", "st": "status",
                          "run": "id", "q": "qubit", "qp": "pair"}
_SIDEBAR_KNOWN_SCOPES = {"name", "date", "status", "id", "qubit", "pair"}


def _tokenize_query(text: str) -> list[str]:
    """Whitespace-or-comma split that keeps "double-quoted" runs as one token.

    Quotes are stripped (the matched value is the inner text), mirroring
    dataset-virtual.js ``tokenize`` so ``name:"power rabi"`` stays one token.
    The comma is the drift the search-grammar audit caught: the JS side has
    always split on commas (``q2, q5`` = two AND keywords) while this twin
    split on whitespace only, so the same query filtered differently on the
    two surfaces that claim to mirror each other. Additive: a comma-carrying
    token matched nothing here before (no tree field contains a comma).
    """
    out: list[str] = []
    cur: list[str] = []
    in_q = False
    for ch in text:
        if ch == '"':
            in_q = not in_q
            continue
        if not in_q and (ch.isspace() or ch == ","):
            if cur:
                out.append("".join(cur))
                cur = []
            continue
        cur.append(ch)
    if cur:
        out.append("".join(cur))
    return out


def _parse_tree_query(text: str) -> list[dict]:
    """Parse a sidebar filter query into AND-ed conditions.

    Returns a list of ``{"field": str|None, "value": str, "negate": bool}``.
    ``field=None`` is a free-text token (matches any of name/date/status).
    Negation (``-scope:value``) applies only to scoped tokens, matching the
    Datasets search semantics.
    """
    conds: list[dict] = []
    for tok in _tokenize_query(text):
        negate = False
        body = tok
        # Guard shape kept identical to dataset-virtual.js:154 (the other
        # drift the audit caught — the JS side also accepts `=` because the
        # Datasets table has `key=value` param facets). The sidebar has no
        # params, so a negated `-x=y` falls through to the SAME place an
        # unknown scope does on both surfaces: the original token, literal.
        # The divergence that remains is capability, not grammar.
        if len(body) > 1 and body[0] == "-" and (":" in body[1:] or "=" in body[1:]):
            negate = True
            body = body[1:]
        if ":" in body:
            key, _, value = body.partition(":")
            key = _SIDEBAR_SCOPE_ALIASES.get(key.strip().lower(), key.strip().lower())
            value = value.strip().lower()
            if key in _SIDEBAR_KNOWN_SCOPES and value:
                conds.append({"field": key, "value": value, "negate": negate})
                continue
        # Bare / unknown token → free-text with the ORIGINAL token (negation
        # needs a consumable scope, so a bare leading '-' stays literal —
        # exactly the JS side's unknown-scope fallthrough).
        conds.append({"field": None, "value": tok.lower(), "negate": False})
    return conds


def _group_tree_conds(conds: list[dict]) -> list[list[dict]]:
    """AND-of-OR groups over the parsed conditions (the shared ``|`` grammar).

    ``core.search_query.group_by`` is the same structure every client-side
    search box uses (its JS twin) — a standalone free-text ``|`` with a
    non-negated term on both sides ORs its neighbours; every other pipe stays
    a literal token. No pipe → singleton groups → the historic flat AND.
    """
    from quam_state_manager.core.search_query import group_by

    return group_by(
        conds,
        get_raw=lambda c: c["value"] if c["field"] is None else "",
        joinable=lambda c: not c["negate"],
    )


def _entry_matches(entry, conds: list[dict]) -> bool:
    """True iff *entry* satisfies the parsed query — AND across groups, OR
    within one (``q1 | q2``), negation as before (always a singleton group)."""
    name = (entry.experiment_name or "").lower()
    date = (entry.date_str or "").lower()
    status = (entry.status or "").lower()
    rid = "" if entry.run_id is None else str(entry.run_id)
    qubits = [str(q).lower() for q in (getattr(entry, "qubits", None) or [])]
    pairs = [str(p).lower() for p in (getattr(entry, "qubit_pairs", None) or [])]

    def _hit(c: dict) -> bool:
        field, value = c["field"], c["value"]
        if field is None:
            # Free-text: name/date/status substring, EXACT qubit (q1 ≠ q10), or
            # substring pair — so `q0` / a pair name filter the tree.
            hit = (value in name or value in date or value in status
                   or value in qubits or any(value in p for p in pairs))
            # r16 ⑨: run-id fast search — an all-digits query matches the run
            # id exactly or as a prefix ("780" finds #780 / #7801 without the
            # id: scope). Run ids were deliberately excluded from free text
            # (any digit matched everything); digits-only is unambiguous.
            if not hit and value.isdigit() and rid:
                hit = rid == value or rid.startswith(value)
            return hit
        if field == "name":
            return value in name
        if field == "date":
            return value in date
        if field == "status":
            return value in status
        if field == "id":
            return value in rid
        if field == "qubit":
            return value in qubits                       # exact
        if field == "pair":
            return any(value in p for p in pairs)        # substring
        return False

    for group in _group_tree_conds(conds):
        if len(group) == 1 and group[0]["negate"]:
            if _hit(group[0]):                # negated hit → reject
                return False
            continue
        if not any(_hit(c) for c in group):   # no member matched → reject
            return False
    return True


def _filter_tree(tree: dict, text: str) -> dict:
    """Filter workspace tree entries by a scoped query.

    Supports free-text tokens plus ``key:value`` scopes (name/exp/e, date/d,
    status/st, id/run) with ``-`` negation and "quoted values", mirroring the
    Datasets-page search. Boolean structure is the shared grammar
    (``core.search_query``): space = AND, a standalone ``|`` = OR between its
    neighbours (``rabi | ramsey``).
    """
    from quam_state_manager.core.scanner import DateGroup

    conds = _parse_tree_query(text)
    if not conds:
        return tree
    result: dict = {}
    for root_path, date_groups in tree.items():
        filtered_groups = []
        for dg in date_groups:
            matched = [e for e in dg.entries if _entry_matches(e, conds)]
            if matched:
                filtered_groups.append(DateGroup(date_str=dg.date_str, entries=matched))
        if filtered_groups:
            result[root_path] = filtered_groups
    return result


def _tree_render_ctx(tree: dict) -> dict:
    """Template context for _sidebar_tree.html: the flat tree (root iteration,
    empty checks) + the per-root NESTED render model (r13 hierarchy — real
    folder levels between root and runs, newest-first) + the per-root
    discovery-cap flag (docs/105 #9 — a truncated walk must say so)."""
    from quam_state_manager.core.scanner import (build_nested_tree,
                                                 root_scan_truncated)
    nested = {}
    truncated = {}
    for root_path, groups in (tree or {}).items():
        entries = [e for g in groups for e in g.entries]
        nested[root_path] = build_nested_tree(Path(root_path), entries)
        truncated[root_path] = root_scan_truncated(root_path)
    return {"tree": tree or {}, "nested": nested, "tree_truncated": truncated}


@bp.route("/workspace/add", methods=["POST"])
def workspace_add():
    folder = request.form.get("folder", "").strip()
    if not folder:
        return render_template("_status.html", message="No folder specified", level="error"), 400

    ws = _ws()
    try:
        entries = ws.add_root(folder)
    except Exception as e:
        return render_template("_status.html", message=str(e), level="error"), 400

    # Invalidate cached DatasetStore so it rebuilds with new roots
    current_app.config.pop("dataset_store", None)
    # …and the candidate-folder cache, so the per-run fast path (which skips the
    # workspace-token validation) can't serve a list missing the new root.
    _dataset_candidates_cache.pop(id(current_app._get_current_object()), None)
    _save_workspace_roots()

    return render_template("_sidebar_tree.html", **_tree_render_ctx(ws.tree),
                           message=f"Added {len(entries)} experiment(s)")


@bp.route("/workspace/remove", methods=["POST"])
def workspace_remove():
    folder = request.form.get("folder", "").strip()
    ws = _ws()
    ws.remove_root(folder)
    # Invalidate cached DatasetStore so it rebuilds without removed root
    current_app.config.pop("dataset_store", None)
    # …and the candidate-folder cache (per-run fast path — see workspace_add).
    _dataset_candidates_cache.pop(id(current_app._get_current_object()), None)
    _save_workspace_roots()
    # Project lens (docs/63): an explicitly removed folder must not keep
    # seeding project scopes on the Datasets/Trends pages.
    _strip_project_root(folder)
    # Remember the explicit remove so future /load calls don't auto-re-add it.
    # Propagate OSError so the user is warned if the exclusion didn't persist
    # — without this, the next auto-rehydrate quietly re-adds the folder
    # (red-team Phase 2 finding §5.3).
    if folder:
        try:
            data = _load_session()
            excluded = data.get("workspace_excluded", [])
            abs_path = str(Path(folder).resolve())
            # Membership via fs_key on BOTH sides — a case-variant / NFD
            # spelling of an already-excluded folder must not append a second
            # entry (stored spelling stays the resolved path, for display).
            if path_match.fs_key(abs_path) not in {
                    path_match.fs_key(e) for e in excluded}:
                excluded.append(abs_path)
                data["workspace_excluded"] = excluded
                _save_session_raising(data)
        except OSError as exc:
            logger.warning("Could not record workspace exclusion: %s", exc)
            return render_template(
                "_status.html",
                message=(
                    "Removed from workspace but the exclusion couldn't be "
                    f"saved ({exc}); the folder may re-appear on next launch."
                ),
                level="warning",
            )
    return render_template("_sidebar_tree.html", **_tree_render_ctx(ws.tree))


@bp.route("/workspace/tree")
def workspace_tree():
    ws = _ws()
    if ws.rescan_if_stale():  # cheap mtime check; full rescan only when disk has changed
        current_app.config.pop("dataset_store", None)  # keep Datasets tab in sync
    name_filter = request.args.get("name", "").strip()

    if name_filter:
        return render_template("_sidebar_tree.html",
                               **_tree_render_ctx(_filter_tree(ws.tree, name_filter)),
                               name_filter=name_filter)

    return render_template("_sidebar_tree.html",
                           **_tree_render_ctx(ws.tree if ws else {}))


@bp.route("/workspace/tree/group")
def workspace_tree_group():
    """Render the full (uncapped) entry list for one container group.

    Backs the sidebar's "Show all N" button: the main tree renders only the
    newest N entries per group to bound the DOM at scale, and this fragment
    swaps in the complete list for one group on demand. Honours the active
    name filter so expanding a group never reveals filtered-out entries.
    r13: groups are addressed by ``tpath`` (the container's /-joined relative
    path from the nested tree); ``date`` stays as a legacy alias — for the
    flat layout the pseudo-container's tpath IS the date string.
    """
    ws = _ws()
    root = request.args.get("root", "")
    tpath = request.args.get("tpath", "") or request.args.get("date", "")
    name_filter = request.args.get("name", "").strip()
    tree = _filter_tree(ws.tree, name_filter) if (name_filter and ws) else (ws.tree if ws else {})
    from quam_state_manager.core.scanner import build_nested_tree
    all_entries = [e for g in tree.get(root, []) for e in g.entries]
    nodes = build_nested_tree(Path(root), all_entries)

    def _find(nodes_: list) -> dict | None:
        for n in nodes_:
            if n["tpath"] == tpath:
                return n
            hit = _find(n["children"])
            if hit is not None:
                return hit
        return None

    node = _find(nodes)
    entries = node["entries"] if node else []
    return render_template("_sidebar_tree_entries.html", entries=entries)


@bp.route("/workspace/tree/poll")
def workspace_tree_poll():
    """Cheap workspace-change probe for the sidebar's version-gated refresh.

    Runs the same mtime staleness check the full-tree route does (rescanning
    only if the disk actually changed) and returns a monotonic version. The
    client polls this every N seconds and re-fetches the tree only when the
    version changes — instead of rebuilding the sidebar DOM every 60 s
    regardless (the periodic full swap that this replaces).
    """
    ws = _ws()
    if not ws:
        return jsonify(v=0, rescanned=False)
    rescanned = ws.rescan_if_stale()
    if rescanned:
        current_app.config.pop("dataset_store", None)  # keep Datasets tab in sync
    # r16 ⑦ D-C: report whether THIS poll's staleness check did the rescan —
    # the first poll used to adopt its own version bump as the baseline and
    # never render what it just discovered.
    return jsonify(v=ws.version, rescanned=rescanned)


@bp.route("/workspace/refresh", methods=["POST"])
def workspace_refresh():
    """Force-rescan all workspace roots and return the updated sidebar tree."""
    ws = _ws()
    ws.rescan_all()
    current_app.config.pop("dataset_store", None)  # keep Datasets tab in sync
    # r16 ⑦ D-D: honor the sidebar filter — the rotate button used to render
    # UNFILTERED while the poller re-applied the (possibly browser-restored,
    # chip-less, invisible) filter ≤60 s later: entries "came back" on Refresh
    # then vanished again.
    name_filter = request.form.get("name", "").strip() \
        or request.args.get("name", "").strip()
    tree = ws.tree if ws else {}
    if name_filter:
        tree = _filter_tree(tree, name_filter)
    return render_template("_sidebar_tree.html", **_tree_render_ctx(tree))


@bp.route("/workspace/select", methods=["POST"])
def workspace_select():
    path = request.form.get("path", "").strip()
    if not path:
        return render_template("_status.html", message="No path specified", level="error"), 400

    try:
        _activate_quam(path)
    except (FileNotFoundError, ValueError, OSError) as e:
        # Corrupt/unreadable state.json raises ValueError (bad JSON) or OSError,
        # not FileNotFoundError — catch all three (matching /load) so a bad chip
        # yields a friendly status toast, not a generic 500 "Internal Server Error".
        return render_template("_status.html", message=str(e), level="error"), 400

    # A dataset-RUN sidebar entry fires this POST *and*, concurrently, a JS swap
    # of the run's dataset detail into #inspector-pane (see app.js). A full
    # HX-Redirect does a client-side navigation that destroys that inspector
    # swap — the dataset panel "flashes then vanishes" (the run entry sends
    # inplace=1 so we can tell it apart). For that path do an IN-PLACE
    # #table-pane swap of the new chip's qubits view and OOB-refresh the topbar
    # tray / origin badge + live-diverged banner — leaving #inspector-pane
    # untouched so the dataset detail survives. Plain chip-folder entries (no
    # concurrent inspector swap) keep the full-render HX-Redirect, which the
    # pre-customer audit added so the header reflects the switched chip.
    if _is_htmx():
        if request.form.get("inplace") == "1":
            body = qubits()
            if isinstance(body, str):
                resp = make_response(body + "\n" + _tray_oob() + _diverged_oob())
                resp.headers["HX-Trigger"] = "diagnostics-changed"
                return resp
            # qubits() returned a Response/tuple (unexpected) — fall through to
            # the redirect so the click is never a silent no-op.
        resp = make_response()
        resp.headers["HX-Redirect"] = url_for("main.qubits")
        return resp
    return redirect(url_for("main.qubits"))


def _has_experiment_descendant(root: Path, max_depth: int = 4) -> bool:
    """Check if *root* contains quam_state folders within *max_depth* levels."""
    try:
        for dirpath, dirnames, _filenames in os.walk(root):
            depth = len(Path(dirpath).relative_to(root).parts)
            if depth > 0:
                dp = Path(dirpath)
                if dp.name == "quam_state" and (dp / "state.json").is_file():
                    return True
                if (dp / "state.json").is_file() and (dp / "wiring.json").is_file():
                    return True
            if depth >= max_depth:
                dirnames.clear()
    except PermissionError:
        pass
    return False


def _is_system_path(p: Path, for_mkdir: bool = False) -> bool:
    """Check if path points to a protected system directory.

    Prefix matching is by whole path COMPONENTS (``Path.parts``) — the old
    ``str.startswith`` check also blocked innocent siblings like
    ``C:\\Windows_backup``. POSIX gets its own block list; ``/`` itself is
    additionally protected from mkdir (browsing the root stays allowed).
    """
    import platform
    try:
        resolved = p.resolve()
    except OSError:
        return True
    if platform.system() == "Windows":
        parts = tuple(part.casefold() for part in resolved.parts)
        blocked = [
            ("c:\\", "windows"),
            ("c:\\", "program files"),
            ("c:\\", "program files (x86)"),
            ("c:\\", "programdata"),
            ("c:\\", "$recycle.bin"),
        ]
    else:
        parts = resolved.parts
        if for_mkdir and parts == ("/",):
            return True
        blocked = [("/", "proc"), ("/", "sys"), ("/", "dev"), ("/", "etc")]
        if sys.platform == "darwin":
            # docs/101 P10: macOS is case-insensitive AND has its own
            # system roots — compare casefolded and cover them.
            parts = tuple(p.casefold() for p in parts)
            blocked += [("/", "system"), ("/", "library"), ("/", "private")]
    return any(parts[:len(b)] == b for b in blocked)


@bp.route("/browse")
def browse_directory():
    """Return JSON listing of directory children for folder browsing / autocomplete.

    Paths in the response are NATIVE to the server OS (``str(Path)``) — the
    client treats both ``/`` and ``\\`` as separators (breadcrumbs). Unreadable
    directories return an ``error`` field at HTTP 200 (the dialog renders it
    with a Retry) — never a silent empty listing, never a 500.
    """
    import platform
    import string as string_mod

    raw = request.args.get("path", "").strip()

    if not raw:
        if platform.system() == "Windows":
            dirs = [
                f"{d}:\\"
                for d in string_mod.ascii_uppercase
                if Path(f"{d}:\\").exists()
            ]
            return jsonify({"path": "", "dirs": dirs, "has_quam_state": False, "parent": ""})
        # POSIX: an empty path lands in the user's home — a "/" root listing
        # is rarely useful and permission-noisy. Falls through to the normal
        # directory branch so parent/".."-navigation still walks to /.
        # Path.home() raises RuntimeError when HOME is unset (stripped-env
        # service launches) — degrade to the filesystem root, never a 500.
        try:
            p = Path.home()
        except RuntimeError:
            p = Path("/")
    else:
        # "~"-forms come from hand-typed paths and replayed localStorage —
        # expand them server-side (audit: "~/x" was treated as a RELATIVE
        # path whose first segment is literally "~").
        try:
            p = Path(raw).expanduser()
        except RuntimeError:            # "~" with no resolvable home
            p = Path(raw)

    def _isdir(d: Path) -> bool:
        # Path.is_dir() PROPAGATES PermissionError (only ENOENT-class errnos
        # are swallowed) — a path under a non-traversable dir must classify
        # as missing (→ the ancestor-walk / empty branches), never a 500.
        try:
            return d.is_dir()
        except OSError:
            return False

    if _is_system_path(p):
        logger.warning("Browse attempt on protected system path: %s", raw)
        return jsonify({"path": raw, "dirs": [], "has_quam_state": False, "parent": ""})

    if not _isdir(p):
        # Two consumers with different needs (the root-jump bug lived here —
        # the response `path` MUST always be the folder actually listed, or
        # the dialog's breadcrumbs desync and a mid-crumb click cascades to
        # the drive root):
        #
        # 1. `?complete=1` — the path-input autocomplete: keep the classic
        #    prefix-completion over the parent's entries.
        # 2. The folder-browser DIALOG (no flag): walk up to the nearest
        #    EXISTING ancestor, list it, and say what was missing — a stale
        #    Recent entry or a deleted folder lands at its deepest surviving
        #    parent with truthful breadcrumbs, never at the root.
        if request.args.get("complete"):
            # Absolute-only, like the dialog branch below — a relative input
            # used to list the APP's CWD and return bare relative suggestions.
            if not p.is_absolute():
                return jsonify({"path": raw, "dirs": [], "has_quam_state": False, "parent": ""})
            parent = p.parent
            if not _isdir(parent):
                return jsonify({"path": raw, "dirs": [], "has_quam_state": False, "parent": ""})
            prefix = p.name.lower()
            err = None
            try:
                dirs = sorted(
                    (str(c) for c in parent.iterdir()
                     if c.is_dir() and c.name.lower().startswith(prefix)
                     # Dot-dirs surface only when the TYPED segment starts with
                     # "." (…/.qual → ~/.qualibrate) — otherwise stay hidden.
                     and (prefix.startswith(".") or not c.name.startswith("."))),
                    key=str.casefold,   # display order only — paths untouched
                )
            except PermissionError:
                dirs, err = [], "Permission denied"
            except OSError as exc:
                dirs, err = [], f"Could not read folder ({exc.__class__.__name__})"
            payload = {
                "path": str(parent),
                "dirs": dirs[:20],
                "has_quam_state": False,
                "parent": str(parent.parent),
            }
            if err:
                payload["error"] = err
            return jsonify(payload)

        missing = raw
        # Only ABSOLUTE paths walk: a relative bogus path (e.g. "Z:/x" on
        # POSIX) would otherwise walk down to "." and list the process CWD.
        if not p.is_absolute():
            return jsonify({"path": raw, "dirs": [], "has_quam_state": False,
                            "parent": "", "missing": missing})
        anc = p.parent
        while anc != anc.parent and not _isdir(anc):
            anc = anc.parent
        if not _isdir(anc):
            return jsonify({"path": raw, "dirs": [], "has_quam_state": False,
                            "parent": "", "missing": missing})
        p = anc
        # fall through to the normal listing of the ancestor, carrying the
        # `missing` marker so the dialog can explain what happened.
    else:
        missing = None

    err = None
    try:
        children = sorted(
            (str(c) for c in p.iterdir()
             if c.is_dir() and not c.name.startswith(".")),
            key=str.casefold,   # display order only — paths untouched
        )
    except PermissionError:
        children, err = [], "Permission denied"
    except OSError as exc:
        children, err = [], f"Could not read folder ({exc.__class__.__name__})"

    # The badge probes also touch the unreadable directory — guard them the
    # same way (a permission-denied folder must answer, not 500).
    try:
        has_quam = (p / "state.json").is_file() and (p / "wiring.json").is_file()
    except OSError:
        has_quam = False
    has_children = False if err else _has_experiment_descendant(p)

    parent_str = "" if p.parent == p else str(p.parent)

    cap = 400   # dialog cap — the old silent 50 hid most of big lab archives
    payload = {
        "path": str(p),
        "dirs": children[:cap],
        "has_quam_state": has_quam,
        "has_experiment_children": has_children,
        "parent": parent_str,
        **({"missing": missing} if missing else {}),
    }
    if len(children) > cap:
        # Honest capping — the client renders a "showing first N of M" note.
        payload["truncated"] = True
        payload["total"] = len(children)

    # kind=dataset (the Dataset-load / workspace-add pickers): mark which
    # children are dataset RUN folders (node.json / data.json — the same
    # markers core/dataset.py discovers on) so the dialog highlights the
    # folders the user is actually hunting, not quam_state ones. One cheap
    # stat pair per shown child, computed only when asked for.
    if request.args.get("kind") == "dataset":
        ds_dirs = []
        for c in payload["dirs"]:
            try:
                cp = Path(c)
                if (cp / "node.json").is_file() or (cp / "data.json").is_file():
                    ds_dirs.append(c)
            except OSError:
                continue
        payload["dataset_dirs"] = ds_dirs
        try:
            payload["has_dataset"] = (
                (p / "node.json").is_file() or (p / "data.json").is_file()
            )
        except OSError:
            payload["has_dataset"] = False

    if err:
        payload["error"] = err
    return jsonify(payload)


_WIN_RESERVED_NAMES = frozenset(
    ["CON", "PRN", "AUX", "NUL"]
    + [f"COM{i}" for i in range(1, 10)]
    + [f"LPT{i}" for i in range(1, 10)]
)


@bp.route("/mkdir", methods=["POST"])
def make_directory():
    """Create a new subfolder inside an existing directory — the folder browser's
    'New folder' action. Same anti-traversal / system-path guards as ``/browse``;
    the name is sanitized (non-empty, no path separators, not ``.``/``..``, and
    PORTABLE: creatable on every OS the workspace may move to). Mutation is
    origin-CSRF-gated by ``_csrf_origin_check`` (no token needed)."""
    parent_raw = (request.form.get("path") or "").strip()
    name = (request.form.get("name") or "").strip()
    if not parent_raw:
        return jsonify({"ok": False, "error": "No parent folder given"}), 400
    if (not name or name in (".", "..") or "\0" in name
            or "/" in name or "\\" in name):
        return jsonify({"ok": False, "error": "Invalid folder name"}), 400
    # Portable-folder policy (enforced on ALL OSes): a name Windows can't
    # represent would strand the folder the day the workspace moves to a
    # Windows machine — reject it up front, saying why.
    if any(ch in name for ch in '<>:"|?*'):
        return jsonify({"ok": False, "error":
                        'Folder name contains <>:"|?* — not allowed on '
                        "Windows, so the folder would not be portable"}), 400
    if name.endswith(".") or name.endswith(" "):
        return jsonify({"ok": False, "error":
                        "Folder name ends with a dot or space — Windows "
                        "silently strips these, so the folder would not be "
                        "portable"}), 400
    if name.split(".", 1)[0].upper() in _WIN_RESERVED_NAMES:
        return jsonify({"ok": False, "error":
                        f"'{name}' is a reserved device name on Windows "
                        "(CON, PRN, AUX, NUL, COM1-9, LPT1-9), so the folder "
                        "would not be portable"}), 400

    try:
        parent = Path(parent_raw).expanduser()
    except RuntimeError:                # "~" with no resolvable home
        parent = Path(parent_raw)
    if not parent.is_absolute():
        # A relative parent resolved against the APP's CWD (audit: created
        # stray folders under the server's working directory).
        return jsonify({"ok": False,
                        "error": "Parent folder must be an absolute path"}), 400
    try:
        parent_ok = parent.is_dir()
    except OSError:                     # stat under a non-traversable dir
        parent_ok = False
    if not parent_ok:
        return jsonify({"ok": False, "error": "Parent folder does not exist"}), 400
    if _is_system_path(parent, for_mkdir=True):
        logger.warning("mkdir rejected on protected system path: %s", parent_raw)
        return jsonify({"ok": False, "error": "Protected system path"}), 403

    new = parent / name
    if _is_system_path(new, for_mkdir=True):
        return jsonify({"ok": False, "error": "Protected system path"}), 403
    try:
        new.mkdir(parents=False, exist_ok=True)
    except OSError as exc:
        return jsonify({"ok": False, "error": f"Could not create folder: {exc}"}), 400
    return jsonify({"ok": True, "path": str(new)})


@bp.route("/open-folder", methods=["POST"])
def open_folder():
    """Open a folder in the OS file explorer.

    The path MUST resolve to (or inside) a registered workspace root — this is
    an anti-traversal gate, since the route is reachable by any JS on the page
    or any local process. Fire-and-forget (``Popen``, never ``check``) so it
    can't block a Flask worker; ``explorer.exe`` returns exit code 1 even on a
    *successful* open, so checking the return code would falsely report failure.
    Degrades to a graceful JSON error on headless Linux / CI.
    """
    import platform
    import subprocess

    raw = request.form.get("folder", "").strip()
    if not raw:
        return jsonify({"ok": False, "error": "No folder specified"}), 400

    try:
        path = Path(raw).resolve()  # resolve() collapses '..' so traversal can't escape
    except OSError:
        return jsonify({"ok": False, "error": "Invalid path"}), 400

    # Containment: path must equal or sit under a registered workspace root.
    roots = [Path(r).resolve() for r in _ws().root_folders]
    if not any(path == r or path.is_relative_to(r) for r in roots):
        logger.warning("open-folder rejected (outside workspace roots): %s", path)
        return jsonify({"ok": False, "error": "Path is not inside a workspace folder"}), 403

    if not path.is_dir():
        return jsonify({"ok": False, "error": "Path does not exist"}), 400

    try:
        system = platform.system()
        if system == "Windows":
            subprocess.Popen(["explorer", str(path)])  # rc==1 on success → never check
        elif system == "Darwin":
            subprocess.Popen(["open", str(path)])
        else:
            # Linux: under WSL, translate to a Windows path and hand to explorer.exe;
            # on bare Linux fall back to xdg-open; otherwise report no launcher.
            is_wsl = "microsoft" in platform.uname().release.lower()
            wslpath = shutil.which("wslpath")
            explorer = shutil.which("explorer.exe")
            if is_wsl and wslpath and explorer:
                # wslpath -w handles every case (D:\..., \\wsl.localhost\..., uppercase
                # drives) where a hand-rolled /mnt/<x> regex silently breaks.
                win = subprocess.run(
                    [wslpath, "-w", str(path)],
                    capture_output=True, text=True, timeout=5, check=True,
                ).stdout.strip()
                subprocess.Popen([explorer, win])  # rc==1 on success → never check
            elif shutil.which("xdg-open"):
                subprocess.Popen(["xdg-open", str(path)])
            else:
                return jsonify({"ok": False, "error": "No file manager available"}), 501
        return jsonify({"ok": True})
    except Exception as e:  # never let a launcher fault 500-crash the worker
        logger.exception("open-folder failed for %s", path)
        return jsonify({"ok": False, "error": str(e)}), 500


# ======================================================================
# Compare / Trend
# ======================================================================


# _COMPARE_PROPS / _ALL_QUBIT_PROPS / _TABLE_PROP_GROUPS / _ALL_TABLE_PROPS
# live in core/param_specs.py (re-imported above — see the shim note).

# Archive-run folder name (``#N_<experiment>_HHMMSS``) + its date dir
# (``YYYY-MM-DD``) — same layout convention as core.history / core.scanner.
_RUN_DIR_RE = re.compile(r"^#?(\d+)_(.+?)_(\d{6})$")
_DATE_DIR_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _compare_source_label(p: str | Path) -> str:
    """Honest label for one compare/trend source folder.

    The old label was ``Path(p).parent.name`` — for flat chip folders
    (``state.json`` directly in ``<root>/<chip>/``) two *different* chips
    under the same root both rendered the root's name (docs/49 P0).

    - Folder itself is a conventional ``quam_state`` dir → ``chip_name_for``
      (chip name for both live ``<chip>/quam_state`` and archive-run
      layouts); archive runs additionally get ``#run-id · date HH:MM:SS``
      parsed from the ``#N_<exp>_<HHMMSS>`` run dir + ``YYYY-MM-DD`` date
      dir so two snapshots of the same chip stay distinguishable.
    - Any other folder → its own name (the chip name in flat layouts).
    """
    path = Path(p).resolve()
    if path.name != "quam_state":
        return path.name
    base = chip_name_for(path)
    run_dir = path.parent
    m = _RUN_DIR_RE.match(run_dir.name)
    if m and run_dir.parent and _DATE_DIR_RE.match(run_dir.parent.name):
        hms = m.group(3)
        return (f"{base} #{m.group(1)} · {run_dir.parent.name} "
                f"{hms[:2]}:{hms[2:4]}:{hms[4:6]}")
    return base


def _dedupe_compare_labels(labels: list[str], paths: list[str]) -> list[str]:
    """Disambiguate colliding labels with the shortest distinguishing path suffix.

    Two sources that still produce the same label (e.g. ``rootA/LabA`` and
    ``rootB/LabA``) get ``LabA (rootA)`` / ``LabA (rootB)``: the longest
    common trailing path suffix is stripped, then the shortest suffix of the
    remaining parent chain that makes every member of the collision group
    unique is appended. Identical resolved paths (the same folder added
    twice) are left as-is — they genuinely are the same source.
    """
    out = list(labels)
    groups: dict[str, list[int]] = {}
    for i, lbl in enumerate(labels):
        groups.setdefault(lbl, []).append(i)
    for lbl, idxs in groups.items():
        if len(idxs) < 2:
            continue
        chains = [Path(paths[i]).resolve().parts for i in idxs]
        # Strip the common trailing suffix (it carries no information).
        min_len = min(len(c) for c in chains)
        strip = 0
        while strip < min_len and len({c[len(c) - 1 - strip] for c in chains}) == 1:
            strip += 1
        stripped = [c[:len(c) - strip] for c in chains]
        max_k = max((len(c) for c in stripped), default=0)
        for k in range(1, max_k + 1):
            sufs = ["/".join(c[-k:]) for c in stripped]
            if len(set(sufs)) == len(sufs):
                for i, suf in zip(idxs, sufs):
                    if suf:
                        out[i] = f"{lbl} ({suf})"
                break
    return out


def _load_compare_stores(paths_raw: list[str]):
    """Load QuamStore objects and ExperimentContexts for the given paths.

    Returns (stores, contexts, labels, all_qubit_names). Labels are honest
    chip-level names (``_compare_source_label``) with a shortest-suffix
    dedup when two sources would otherwise collide.
    """
    ws = _ws()
    stores: list[QuamStore] = []
    contexts: list[ExperimentContext] = []
    labels: list[str] = []
    loaded_paths: list[str] = []
    for p in paths_raw:
        try:
            store = ws.load_store(p)
            stores.append(store)
            contexts.append(load_experiment_context(p))
            labels.append(_compare_source_label(p))
            loaded_paths.append(p)
        except Exception as e:
            logger.warning("Failed to load %s: %s", p, e)
            continue

    labels = _dedupe_compare_labels(labels, loaded_paths)

    all_qubit_names: list[str] = []
    for s in stores:
        for qn in s.qubit_names:
            if qn not in all_qubit_names:
                all_qubit_names.append(qn)
    all_qubit_names.sort()

    return stores, contexts, labels, all_qubit_names


def _compute_diff_cells(all_rows: list[dict], ref_idx: int) -> set[tuple[str, str, int]]:
    """Compute which (qubit, property, store_idx) cells differ from the reference."""
    diff_cells: set[tuple[str, str, int]] = set()
    for row in all_rows:
        ref_val = row["values"][ref_idx]["value"] if ref_idx < len(row["values"]) else None
        for i, v in enumerate(row["values"]):
            if i == ref_idx:
                continue
            if v["value"] != ref_val:
                diff_cells.add((row["qubit"], row["property"], i))
    return diff_cells


@bp.route("/compare", methods=["POST"])
def compare():
    """Sidebar experiment-checkbox compare — now a deep-link adapter into
    the Compare hub (docs/49 P4: the checkbox flow is kept — good UX —
    its POST translates into hub src tokens). The Trend Tracker button
    still POSTs /trend directly; the /compare/* tab fragments stay until
    the redirect soaks."""
    all_paths = [p for p in request.form.getlist("paths") if p]
    paths = all_paths[:_HUB_MAX_SOURCES]
    params: list[tuple[str, str]] = [("src", _legacy_src_token(p)) for p in paths]
    if len(all_paths) > _HUB_MAX_SOURCES:   # never truncate silently
        params.append(("trunc", str(len(all_paths))))
    if not params:
        params.append(("from", "compare"))
    return _hub_redirect(f"/compare-hub?{urlencode(params)}")


@bp.route("/compare/diff")
def compare_diff():
    """Re-render the Differences tab (called via HTMX when switching back)."""
    paths_raw = request.args.getlist("paths")
    qubit_filter = request.args.getlist("qubits") or None
    ref_idx = _int_arg("ref", 0, minimum=0)

    stores, contexts, labels, _ = _load_compare_stores(paths_raw)
    if len(stores) < 2:
        return render_template("_status.html", message="Need at least 2 valid stores", level="warning")

    ref_idx = min(ref_idx, len(stores) - 1)

    differ = Differ()
    diff_rows = differ.multi_diff(
        stores, labels, _COMPARE_PROPS, qubit_filter=qubit_filter,
    )
    param_diff_rows = Differ.compare_parameters(contexts, labels)
    fit_diff_rows = Differ.compare_fit_results(contexts, labels, qubit_filter=qubit_filter)
    metadata_list = [ctx.metadata for ctx in contexts]

    return render_template(
        "_compare_diff.html",
        diff_rows=diff_rows,
        param_diff_rows=param_diff_rows,
        fit_diff_rows=fit_diff_rows,
        metadata_list=metadata_list,
        labels=labels,
        ref_idx=ref_idx,
        paths_raw=paths_raw,
    )


@bp.route("/compare/state")
def compare_state():
    """Lazy-loaded per-state tab content for the compare view."""
    idx = _int_arg("idx", 0, minimum=0)
    paths_raw = request.args.getlist("paths")
    qubit_filter = request.args.getlist("qubits") or None
    ref_idx = _int_arg("ref", 0, minimum=0)

    stores, contexts, labels, all_qubit_names = _load_compare_stores(paths_raw)
    if idx >= len(stores):
        return render_template("_status.html", message="Invalid state index", level="error"), 400

    ref_idx = min(ref_idx, len(stores) - 1)

    differ = Differ()
    all_rows = differ.multi_compare(stores, labels, _COMPARE_PROPS, qubit_filter=qubit_filter)
    diff_cells_3 = _compute_diff_cells(all_rows, ref_idx)

    state_diff_cells: set[tuple[str, str]] = set()
    for (qb, prop, si) in diff_cells_3:
        if si == idx:
            state_diff_cells.add((qb, prop))

    store = stores[idx]
    eng = QueryEngine(store)
    qubits = eng.summary_table(_ALL_QUBIT_PROPS)
    if qubit_filter:
        qubits = [q for q in qubits if q["id"] in qubit_filter]

    ref_store = stores[ref_idx]
    ref_eng = QueryEngine(ref_store)
    ref_data: dict[str, dict] = {}
    for name in ref_store.qubit_names:
        try:
            ref_data[name] = ref_eng.get_qubit(name)
        except Exception:
            continue

    exp_ctx = contexts[idx]
    ref_ctx = contexts[ref_idx]

    state_json = json.dumps(store.state)
    wiring_json = _wiring_json()
    ref_state_json = json.dumps(ref_store.state) if ref_store else "null"
    ref_wiring_json = json.dumps(ref_store.wiring) if ref_store else "null"

    return render_template(
        "_compare_state.html",
        label=labels[idx],
        qubits=qubits,
        diff_cells=state_diff_cells,
        props=_ALL_QUBIT_PROPS,
        ref_data=ref_data,
        exp_ctx=exp_ctx,
        ref_ctx=ref_ctx,
        state_json=state_json,
        wiring_json=wiring_json,
        ref_state_json=ref_state_json,
        ref_wiring_json=ref_wiring_json,
    )


@bp.route("/compare/full")
def compare_full():
    """Full unified comparison tree merging all selected states."""
    paths_raw = request.args.getlist("paths")
    if len(paths_raw) < 2:
        return render_template("_status.html", message="Select at least 2 experiments", level="warning")

    stores, _contexts, labels, _all_qubit_names = _load_compare_stores(paths_raw)
    if len(stores) < 2:
        return render_template("_status.html", message="Need at least 2 valid stores", level="warning")

    datasets = []
    for store, label in zip(stores, labels):
        datasets.append({
            "label": label,
            "state": store.state,
            "wiring": store.wiring,
        })

    return render_template(
        "_compare_full.html",
        datasets_json=json.dumps([{"label": d["label"], "state": d["state"], "wiring": d["wiring"]} for d in datasets]),
        labels=labels,
        paths_raw=paths_raw,
    )


# ======================================================================
# Chip Compare — multi-chip Chip Status comparison.
#
# Distinct from /compare (which is dataset/experiment-checkbox driven):
# the user picks 2+ ``quam_state`` folders (different chips) and sees
# them side-by-side as topology cards plus a unified diff table.
# Leans entirely on _load_compare_stores + Differ + QueryEngine.
# ======================================================================


def _chip_compare_topology_data(stores, labels, qubit_filter):
    """Per-chip topology data + cross-chip diff highlights for the topology tab."""
    differ = Differ()
    all_rows = differ.multi_compare(
        stores, labels, _COMPARE_PROPS, qubit_filter=qubit_filter,
    )
    chips = []
    for store, label in zip(stores, labels):
        eng = QueryEngine(store)
        topo = eng.get_topology()
        if qubit_filter:
            topo["nodes"] = [n for n in topo["nodes"] if n["id"] in qubit_filter]
        chips.append({"label": label, "topology": topo})
    return chips, all_rows


@bp.route("/chip-compare", methods=["GET", "POST"])
def chip_compare():
    """Legacy N-way chip compare → Compare hub (docs/49 P4). POSTed picker
    paths translate into hub src tokens; the topology/diff tab fragments
    stay until the redirect soaks."""
    all_paths = [p for p in request.values.getlist("paths") if p]
    paths = all_paths[:_HUB_MAX_SOURCES]
    params = [("src", _legacy_src_token(p)) for p in paths]
    if len(all_paths) > _HUB_MAX_SOURCES:   # never truncate silently
        params.append(("trunc", str(len(all_paths))))
    if not params:
        params.append(("from", "chip-compare"))
    return _hub_redirect(f"/compare-hub?{urlencode(params)}")


@bp.route("/chip-compare/topology")
def chip_compare_topology():
    """HTMX tab — side-by-side topology cards."""
    paths_raw = request.args.getlist("paths")
    qubit_filter = request.args.getlist("qubits") or None
    ref_idx = _int_arg("ref", 0, minimum=0)

    stores, _contexts, labels, _all = _load_compare_stores(paths_raw)
    if len(stores) < 2:
        return render_template(
            "_status.html",
            message="Need at least 2 valid quam_state folders",
            level="warning",
        )

    ref_idx = min(max(ref_idx, 0), len(stores) - 1)
    chips, all_rows = _chip_compare_topology_data(stores, labels, qubit_filter)
    diff_cells_3 = _compute_diff_cells(all_rows, ref_idx)

    # Per-chip diff highlight set: {qubit_id: {property, ...}}.
    per_chip_diff: list[dict[str, set[str]]] = [
        {} for _ in stores
    ]
    for (qb, prop, si) in diff_cells_3:
        per_chip_diff[si].setdefault(qb, set()).add(prop)

    return render_template(
        "_chip_compare_topology.html",
        chips=chips,
        per_chip_diff=per_chip_diff,
        ref_idx=ref_idx,
        labels=labels,
        paths_raw=paths_raw,
    )


@bp.route("/chip-compare/diff")
def chip_compare_diff():
    """HTMX tab — unified diff table (reuses _compare_diff.html).

    ``tolerance`` (relative, default 1e-9) filters the state-value rows:
    two different chips legitimately store the same physical value as
    ``40`` vs ``40.0`` or with sub-ppb float noise — exact ``!=`` would
    flag every such cell as a difference. /compare keeps exact semantics.
    """
    paths_raw = request.args.getlist("paths")
    qubit_filter = request.args.getlist("qubits") or None
    ref_idx = _int_arg("ref", 0, minimum=0)
    tolerance = max(_float_arg("tolerance", 1e-9), 0.0)

    stores, contexts, labels, _all = _load_compare_stores(paths_raw)
    if len(stores) < 2:
        return render_template(
            "_status.html",
            message="Need at least 2 valid quam_state folders",
            level="warning",
        )

    ref_idx = min(max(ref_idx, 0), len(stores) - 1)

    differ = Differ()
    diff_rows = differ.multi_diff(
        stores, labels, _COMPARE_PROPS, qubit_filter=qubit_filter,
        tolerance=tolerance,
    )
    param_diff_rows = Differ.compare_parameters(contexts, labels)
    fit_diff_rows = Differ.compare_fit_results(contexts, labels, qubit_filter=qubit_filter)
    metadata_list = [ctx.metadata for ctx in contexts]

    return render_template(
        "_compare_diff.html",
        diff_rows=diff_rows,
        param_diff_rows=param_diff_rows,
        fit_diff_rows=fit_diff_rows,
        metadata_list=metadata_list,
        labels=labels,
        ref_idx=ref_idx,
        paths_raw=paths_raw,
    )


@bp.route("/trend", methods=["POST"])
def trend():
    """Show the trend property picker after selecting experiments."""
    paths_raw = request.form.getlist("paths")
    if len(paths_raw) < 2:
        return render_template("_status.html", message="Select at least 2 experiments", level="warning")

    stores, _contexts, labels, all_qubit_names = _load_compare_stores(paths_raw)
    if len(stores) < 2:
        return render_template("_status.html", message="Need at least 2 valid stores", level="warning")

    template = "_trend_picker.html" if _is_htmx() else "compare.html"
    return render_template(
        template,
        **_ctx(
            page="trend",
            paths_raw=paths_raw,
            labels=labels,
            all_qubit_names=all_qubit_names,
            prop_groups=_TABLE_PROP_GROUPS,
        ),
    )


def _extract_run_id(label: str) -> int | None:
    """Extract the run-id number from a label like '#10691_08_qubit_...'.

    Prefers an explicit ``#N`` marker — honest compare labels start with the
    chip name (``examplechip9q #10691 · …``), so a bare first-digit-run search
    would pick the ``9`` out of the chip name. Falls back to the first digit
    run for legacy labels without a ``#``.
    """
    m = re.search(r"#(\d+)", label) or re.search(r"(\d+)", label)
    return int(m.group(1)) if m else None


@bp.route("/trend/chart", methods=["POST"])
def trend_chart():
    """Render stacked trend charts for selected properties/qubits."""
    paths_raw = request.form.getlist("paths")
    props = request.form.getlist("props") or ["f_01"]
    qubit_filter = request.form.getlist("qubits") or None

    stores, _contexts, labels, _ = _load_compare_stores(paths_raw)
    if len(stores) < 2:
        return render_template("_status.html", message="Need at least 2 valid stores", level="warning")

    indexed = list(zip(stores, labels))
    indexed.sort(key=lambda pair: _extract_run_id(pair[1]) or 0)
    stores = [s for s, _l in indexed]
    labels = [l for _s, l in indexed]

    legend = []
    symbol_map: dict[str, str] = {}
    for i, lab in enumerate(labels):
        sym = f"E{i + 1}"
        rid = _extract_run_id(lab)
        legend.append({"symbol": sym, "label": lab, "run_id": rid})
        symbol_map[lab] = sym

    differ = Differ()
    results = differ.multi_compare(stores, labels, props, qubit_filter=qubit_filter)

    return render_template(
        "_trend_chart.html",
        trend_data=results,
        trend_json=json.dumps(results),
        labels=labels,
        paths_raw=paths_raw,
        props=props,
        qubit_filter=qubit_filter or [],
        legend=legend,
        symbol_map=symbol_map,
    )


# ======================================================================
# Param History — per-state field-trend dashboard
# ======================================================================

# Background backfill state, keyed by store path
_backfill_state: dict[str, dict[str, Any]] = {}
_backfill_lock = threading.Lock()


def _parse_since(value: str | None) -> str | None:
    """Convert a relative or absolute since= filter into a snapshot timestamp.

    Accepts ``"now-7d"``, ``"now-24h"``, ``"now-30d"``, an explicit
    ``YYYY-MM-DD``, or a raw snapshot timestamp. Returns the timestamp string
    used by ``HistoryManager.extract_property_history`` (lexicographic).
    """
    if not value or value.lower() == "all":
        return None
    v = value.strip().lower()
    if v.startswith("now-"):
        spec = v[4:]
        unit = spec[-1]
        try:
            n = int(spec[:-1])
        except ValueError:
            return None
        from datetime import timedelta
        delta = {"d": timedelta(days=n), "h": timedelta(hours=n), "m": timedelta(minutes=n)}.get(unit)
        if delta is None:
            return None
        cutoff = datetime.now(timezone.utc) - delta
        return cutoff.strftime("%Y%m%d_%H%M%S_000")
    if "-" in value and len(value) >= 10:
        return value.replace("-", "").ljust(15, "0")
    return value


def _path_for_chip_key(chip_key: str) -> Path:
    """Synthesize a path whose ``_key_for`` resolves to the given chip_key.

    Used when the dashboard targets a chip that's not the currently
    loaded one. The path itself is never read; only its parent.name
    matters for the keying.
    """
    return Path("/__chip_key__") / chip_key / "quam_state"


def _detect_workspace_chips(ws) -> list[dict[str, Any]]:
    """Discover all chips reachable from workspace roots.

    Two passes:
      1. Direct top-level layout: ``<root>/<chip>/quam_state/`` (cheap iterdir).
      2. Per-experiment layout: ``<root>/<chip>/<date>/#N_<exp>_HHMMSS/quam_state/``
         — uses the scanner's already-discovered ``ws.all_entries`` and groups
         them by ``chip_name_for(entry.quam_state_path)``.

    Returns one row per unique chip:
    ``{"key": sanitized_name, "name": raw_name, "path": str, "snapshot_ts": str}``.
    ``snapshot_ts`` is non-empty only when the chip has no live
    ``<chip>/quam_state`` folder and the path resolves to an archived run's
    snapshot — it then names the run's date/time so the picker shows what the
    user actually gets. That run is the NEWEST one (the old code silently
    took the first-scanned, i.e. oldest — docs/49 P0).
    """
    if not ws:
        return []
    from quam_state_manager.core.history import _sanitize_name, chip_name_for
    seen: set[str] = set()
    out: list[dict[str, Any]] = []

    # Pass 1: direct top-level chip folders.
    for root in ws.root_folders:
        try:
            for child in Path(root).iterdir():
                if not child.is_dir():
                    continue
                qs = child / "quam_state"
                if qs.is_dir() and (qs / "state.json").exists():
                    key = _sanitize_name(child.name)
                    if key in seen:
                        continue
                    seen.add(key)
                    out.append({"key": key, "name": child.name,
                                "path": str(qs.resolve()), "snapshot_ts": ""})
        except OSError:
            continue

    # Pass 2: chips reachable via per-experiment entries. Group ALL entries
    # per chip first, then resolve — never "first scanned wins".
    try:
        by_key: dict[str, list] = {}
        for entry in ws.all_entries:
            key = _sanitize_name(chip_name_for(entry.quam_state_path))
            if key in seen:
                continue
            by_key.setdefault(key, []).append(entry)
        for key, entries in by_key.items():
            qs_path = entries[0].quam_state_path
            chip_name = chip_name_for(qs_path)
            # Prefer the chip's live <chip>/quam_state if it exists; else
            # fall back to the NEWEST run's snapshot as the load target.
            chip_folder = qs_path.parent.parent.parent if (
                qs_path.parent.parent and qs_path.parent.parent.parent
            ) else qs_path.parent
            live = chip_folder / "quam_state"
            if live.is_dir():
                load_path, snapshot_ts = str(live.resolve()), ""
            else:
                newest = max(entries, key=_entry_recency_key)
                load_path = str(newest.quam_state_path.resolve())
                snapshot_ts = " ".join(p for p in _entry_snapshot_parts(newest) if p)
            out.append({"key": key, "name": chip_name,
                        "path": load_path, "snapshot_ts": snapshot_ts})
    except Exception:
        pass

    return out


def _entry_snapshot_parts(entry) -> tuple[str, str]:
    """Best-effort ``(date, time)`` strings for a workspace entry's snapshot.

    Prefers the archive layout itself (``<date>/#N_<exp>_HHMMSS/``) — the
    run-folder name for the time, its date-dir for the date. Falls back to
    the entry's ``date_str``/ISO timestamp (which, for standalone entries,
    derive from file mtime — NOT the run date).
    """
    date_part = entry.date_str or ""
    time_part = ""
    m = _RUN_DIR_RE.match(entry.folder_path.name)
    if m:
        hms = m.group(3)
        time_part = f"{hms[:2]}:{hms[2:4]}:{hms[4:6]}"
        parent = entry.folder_path.parent
        if parent and _DATE_DIR_RE.match(parent.name):
            date_part = parent.name
    elif entry.timestamp and "T" in entry.timestamp:
        time_part = entry.timestamp.split("T")[1][:8]
    return (date_part, time_part)


def _entry_recency_key(entry) -> tuple[str, str, int]:
    """Sort key ordering workspace entries oldest → newest."""
    date_part, time_part = _entry_snapshot_parts(entry)
    return (date_part, time_part,
            entry.run_id if entry.run_id is not None else -1)


# ---------------------------------------------------------------------------
# Compare hub (docs/49) — the one comparison surface that will replace
# /diff + /compare + /chip-compare (P4). Stateless + URL-canonical: the
# basket IS the query string (``src=`` ref tokens + ``bucket`` + ``preset``
# + ``ref`` + ``map``), so reload / share / Back need no server session.
# Sources resolve through the isolated compare_sources pool — NEVER through
# the scanner LRU or ``_quam_cache`` (pinned by tests).
# ---------------------------------------------------------------------------

_HUB_MAX_SOURCES = compare_sources.POOL_MAX_ENTRIES  # 8 — matches the pool

# Gutter glyph per row class — the review-modal M/A/D language extended to
# the hub's closed class enum (docs/49 row classes; visual grouping is U3).
_HUB_GLYPH = {
    compare_engine.CLS_MODIFIED: "M",
    compare_engine.CLS_WITHIN: "≈",         # ≈
    compare_engine.CLS_ADDED: "A",
    compare_engine.CLS_REMOVED: "D",
    compare_engine.CLS_ONLY_IN: "±",        # ±
    compare_engine.CLS_NOT_IN_SOURCE: "±",
    compare_engine.CLS_LINK_CHANGED: "L",
    compare_engine.CLS_TYPE_CHANGED: "T",
    compare_engine.CLS_SCHEMA_CHANGED: "S",
    compare_engine.CLS_PROVENANCE: "P",
    compare_engine.CLS_UNRESOLVED: "!",
    compare_engine.CLS_DERIVED: "~",
    compare_engine.CLS_EQUAL: "·",          # ·
}

# U3 — users learn FOUR visual families, not twelve classes: changed /
# one-sided ("Only in <alias>", neutral, never red) / attention (amber) /
# dim (equal · within tolerance). link/type/schema/provenance render as one
# muted "meta" affix behind a toolbar toggle (off by default).
_HUB_FAMILY = {
    compare_engine.CLS_MODIFIED: "changed",
    compare_engine.CLS_ADDED: "added",
    compare_engine.CLS_REMOVED: "removed",
    compare_engine.CLS_ONLY_IN: "onesided",
    compare_engine.CLS_NOT_IN_SOURCE: "onesided",
    compare_engine.CLS_UNRESOLVED: "attention",
    compare_engine.CLS_DERIVED: "attention",
    compare_engine.CLS_LINK_CHANGED: "meta",
    compare_engine.CLS_TYPE_CHANGED: "meta",
    compare_engine.CLS_SCHEMA_CHANGED: "meta",
    compare_engine.CLS_PROVENANCE: "meta",
    compare_engine.CLS_WITHIN: "dim",
    compare_engine.CLS_EQUAL: "dim",
}

_HUB_ORIGIN_BADGE = {
    compare_sources.ORIGIN_RUN: "RUN",
    compare_sources.ORIGIN_HISTORY: "HISTORY",
    compare_sources.ORIGIN_WORKING: "WORKING",
    compare_sources.ORIGIN_DROP: "FILE",   # U8: "FILE", never "DROPPED"
}

_HUB_BUCKETS = [
    (1, "① Same chip, over time",
     "last night's calibration, before vs after · backup-folder check"),
    (2, "② Same design, different device",
     "wafer twins · same design, another lab · wizard variants A/B"),
    (3, "③ Different devices",
     "customer chip vs demo chip"),
]

# Rendering budget (docs/49 A5): real pairs coalesce to 1,451–2,044 rows —
# too much for one partial. Small groups render inline; big ones (and
# everything past the running total) load lazily per group.
_HUB_INLINE_GROUP_ROWS = 80
_HUB_INLINE_TOTAL_ROWS = 600
# The Summary tab obeys the same budget: real fleet pairs produce ~1,200
# summary <tr> — the main partial ships the first N entities, the rest load
# through /compare-hub/summary on demand.
_HUB_SUMMARY_INLINE = 25


def _hub_history_root() -> Path:
    return Path(current_app.instance_path) / "history"


def _hub_working_lookup(ctx_path: str):
    """``working:<ctx_path>`` → the loaded in-memory QuamStore (or None).

    Checks ``_quam_cache`` first (covers every loaded chip, active or not),
    then the contexts registry (covers contexts registered without passing
    through the cache, e.g. in tests)."""
    with _quam_cache_lock:
        # The cache is keyed by fs_key (see _activate_quam); the ref carries
        # the ctx["path"] spelling, so derive the same key here.
        cached = _quam_cache.get(path_match.fs_key(ctx_path) if ctx_path else "")
    if cached is not None:
        return cached.get("store")
    for ctx in current_app.config.get("contexts", {}).values():
        if ctx.get("type") == "quam" and ctx.get("path") == ctx_path:
            return ctx.get("store")
    return None


# ref → (state/wiring mtimes, CompareSource). A lazy-group expand re-resolves
# the whole basket; without this memo every expand re-reads + re-parses every
# state/wiring pair from disk (~137 ms on an 8-source fleet basket). Keyed by
# mtimes — any on-disk change misses and re-reads (same-tick replacement is
# the known house-wide stat-granularity gap; matches the scanner LRU).
_HUB_SRC_MEMO: "OrderedDict[str, tuple[Any, Any]]" = OrderedDict()
_HUB_SRC_MEMO_MAX = 16
_hub_src_memo_lock = threading.Lock()


def _hub_resolve_one(ref: str):
    """``resolve_source`` with an mtime-keyed memo (disk origins only).

    ``working:`` refs bypass the memo — they read in-memory stores whose
    unsaved edits no mtime can see. A memo hit is honoured only while the
    pool still holds the content; a pool-evicted hit falls through to a
    full resolve (which re-seeds the pool), so the LookupError retry in
    ``_hub_compare`` can never loop on a stale memo entry.
    """
    try:
        scheme, rest = compare_sources.parse_ref(ref)
    except compare_sources.SourceError:
        scheme, rest = "", ""
    folder = None
    if scheme in ("ws", "run", "drop"):
        folder = Path(rest)
    elif scheme == "hist":
        chip, _, ts = rest.rpartition("/")
        if chip and ts:
            folder = _hub_history_root() / chip / ts
    fp = None
    if folder is not None:
        try:
            fp = safe_io.state_wiring_mtimes(folder)
        except OSError:
            fp = None
    if fp is not None:
        with _hub_src_memo_lock:
            hit = _HUB_SRC_MEMO.get(ref)
            if (hit is not None and hit[0] == fp
                    and compare_sources.DEFAULT_POOL.get(hit[1].content_hash) is not None):
                _HUB_SRC_MEMO.move_to_end(ref)
                return hit[1]
    label_hint = None
    if scheme == "drop" and folder is not None:
        # the stash dir name is a content hash — the human label lives in
        # the meta.json sidecar written by /compare-hub/stash
        try:
            meta = safe_io.read_json(folder / "meta.json")
            if isinstance(meta, dict) and meta.get("label"):
                label_hint = f"{meta['label']} · file"
        except Exception:
            label_hint = None
    src = compare_sources.resolve_source(
        ref,
        history_root=_hub_history_root(),
        working_lookup=_hub_working_lookup,
        label_hint=label_hint,
    )
    if fp is not None:
        with _hub_src_memo_lock:
            _HUB_SRC_MEMO[ref] = (fp, src)
            _HUB_SRC_MEMO.move_to_end(ref)
            while len(_HUB_SRC_MEMO) > _HUB_SRC_MEMO_MAX:
                _HUB_SRC_MEMO.popitem(last=False)
    return src


def _hub_basket(refs: list[str], live_paths: set[str]):
    """Resolve every ref token into a basket row.

    Returns ``(sources, rows)``: ``sources`` is the valid CompareSource list
    (engine order); ``rows`` mirrors the raw URL order — unreadable refs
    become honest error rows, excluded from the count (docs/49 zone A) but
    still removable. ``valid_idx`` maps a row to its engine/ref index."""
    sources: list = []
    rows: list[dict[str, Any]] = []
    for idx, ref in enumerate(refs):
        try:
            src = _hub_resolve_one(ref)
        except compare_sources.SourceError as exc:
            rows.append({
                "src_idx": idx, "valid_idx": None, "ref": ref, "label": ref,
                "badge": "", "path": "", "snapshot_ts": "",
                "wiring_missing": False,
                "error": str(exc), "transient": exc.transient,
            })
            continue
        badge = _HUB_ORIGIN_BADGE.get(src.origin)
        if badge is None:   # a ws: folder — LIVE when it's a workspace chip
            badge = "LIVE" if src.path in live_paths else "FOLDER"
        rows.append({
            "src_idx": idx, "valid_idx": len(sources), "ref": src.ref,
            "label": src.label, "badge": badge, "path": src.path,
            "snapshot_ts": src.snapshot_ts,
            "wiring_missing": src.wiring_missing,
            "error": None, "transient": False,
        })
        sources.append(src)
    # Same-named flat chips (two "LabA" folders under different roots)
    # would render identical labels — disambiguate with the shortest
    # distinguishing path suffix (the P0 honest-label rule, carried over).
    valid_rows = [r for r in rows if r["error"] is None]
    if valid_rows:
        deduped = _dedupe_compare_labels([r["label"] for r in valid_rows],
                                         [r["path"] for r in valid_rows])
        for row, lab in zip(valid_rows, deduped):
            row["label"] = lab
    return sources, rows


def _hub_parse_map(raw: str) -> dict[str, str] | None:
    """``qA1:q1,qA2:q2`` → ``{a: b}``. Malformed segments are dropped."""
    if not raw:
        return None
    pairs: dict[str, str] = {}
    for seg in raw.split(","):
        a, sep, b = seg.partition(":")
        if sep and a.strip() and b.strip():
            pairs[a.strip()] = b.strip()
    return pairs or None


def _hub_validated_map(sources, ref_idx: int, map_raw: str):
    """Validate a URL ``map=`` against the actual devices (review finding).

    The engine wraps any dict as method=manual/status=confirmed without
    checking a single name, so a stale/typo'd/mis-oriented map would render
    an EMPTY compare as a confident result. Returns ``(qmap, warning)``:
    - keys must be ★ref-side qubit names, values other-side; a fully
      inverted map (ref moved after confirming) is flipped back, with a note;
    - a map matching neither device is dropped → needs_confirm suggestion;
    - duplicate target names keep the first pair only (the engine's reverse
      dict would silently keep the LAST otherwise);
    - partial garbage keeps the valid subset, with a count note.
    """
    qmap = _hub_parse_map(map_raw)
    if not qmap or len(sources) != 2:
        return qmap, None
    try:
        ref_names = set(compare_engine.snapshot_for(sources[ref_idx]).qubits)
        other_names = set(compare_engine.snapshot_for(sources[1 - ref_idx]).qubits)
    except LookupError:
        # Pool churn mid-request: dropping the map is the SAFE degrade —
        # passing it through unvalidated would reach the engine as
        # status=confirmed (the empty-result-as-answer failure class).
        return None, "Sources changed underneath — re-showing the suggestion."
    total = len(qmap)
    valid = {a: b for a, b in qmap.items()
             if a in ref_names and b in other_names}
    warning = None
    if not valid:
        flipped = {b: a for a, b in qmap.items()
                   if b in ref_names and a in other_names}
        if flipped:
            valid = flipped
            warning = ("The mapping in the URL was oriented the other way "
                       "around — flipped to match the ★ reference.")
        else:
            return None, ("The mapping in the URL matches neither device — "
                          "showing the suggestion instead.")
    elif len(valid) < total:
        warning = (f"{len(valid)}/{total} mapping entries match these "
                   "devices; the rest were ignored.")
    deduped: dict[str, str] = {}
    seen_targets: set[str] = set()
    for a, b in valid.items():
        if b in seen_targets:
            continue
        seen_targets.add(b)
        deduped[a] = b
    if len(deduped) < len(valid):
        warning = ((warning + " ") if warning else "") + \
            "Duplicate mapping targets were dropped (first pair kept)."
    return deduped, warning


def _hub_compare(sources, **kwargs):
    """``compare_engine.compare`` with the pool-eviction retry.

    ``snapshot_for`` raises LookupError when the pool evicted a source's
    content between resolve and snapshot — re-resolve every ref once and
    retry; a second failure propagates to the caller's error branch."""
    try:
        return compare_engine.compare(sources, **kwargs)
    except LookupError as exc:
        if isinstance(exc, (KeyError, IndexError)):
            raise   # an engine bug, not a pool eviction — never mask it
        fresh = [_hub_resolve_one(s.ref) for s in sources]
        return compare_engine.compare(fresh, **kwargs)


def _hub_display_value(v: Any) -> str:
    """Honest scalar rendering — ``—`` for absent, never blank (docs/49)."""
    if v is None:
        return "—"
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return group_digits(v)
    if isinstance(v, dict) and "value" in v and "gate" in v:
        # two_qubit_fidelity cell — label a bare-float StandardRB honestly
        # as the Clifford fidelity, never as the gate fidelity (U8).
        val = _hub_display_value(v.get("value"))
        gate = v.get("gate") or "?"
        suffix = " · Clifford fid." if v.get("clifford") else ""
        return f"{val} ({gate}{suffix})"
    try:
        s = json.dumps(v, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        s = str(v)
    return s


def _hub_delta_disp(d: Any) -> str:
    if not isinstance(d, (int, float)) or isinstance(d, bool) or not d:
        return ""
    return ("+" if d > 0 else "−") + group_digits(abs(d))


def _hub_view_rows(rows: list[dict], ref_idx: int) -> list[dict]:
    """Decorate engine rows with display strings for the hairline template."""
    out = []
    for row in rows:
        vals = row.get("resolved") or []
        raws = row.get("raw") or []
        cell_cls = row.get("cells") or []
        ref_v = vals[ref_idx] if ref_idx < len(vals) else None
        numeric_ref = isinstance(ref_v, (int, float)) and not isinstance(ref_v, bool)
        view_cells = []
        for i, v in enumerate(vals):
            full = _hub_display_value(v)
            text = full if len(full) <= 60 else full[:57] + "…"
            delta = ""
            if (i != ref_idx and numeric_ref
                    and isinstance(v, (int, float)) and not isinstance(v, bool)):
                delta = _hub_delta_disp(v - ref_v)
            title = full
            raw = raws[i] if i < len(raws) else None
            if raw is not None and raw != v:
                title = f"{full}\nraw: {raw}"
            view_cells.append({
                "text": text, "delta": delta, "title": title,
                "cls": cell_cls[i] if i < len(cell_cls) else "",
            })
        parts = row.get("key", "").rsplit(".", 1)
        out.append({
            **row,
            "view_cells": view_cells,
            "glyph": _HUB_GLYPH.get(row.get("cls"), "?"),
            "family": _HUB_FAMILY.get(row.get("cls"), "changed"),
            "dp_dirs": parts[0] + "." if len(parts) == 2 else "",
            "dp_leaf": parts[-1] if parts else "",
        })
    return out


def _hub_summary_view(result: dict, ref_idx: int) -> list[dict]:
    """Group summary rows by entity; decorate values/Δ for display (U2)."""
    ents: list[dict] = []
    by_key: dict = {}
    for row in result.get("summary") or []:
        k = (row.get("scope"), row.get("entity"))
        ent = by_key.get(k)
        if ent is None:
            ent = {"scope": row.get("scope"), "entity": row.get("entity"),
                   "names": row.get("names") or [], "flipped": False,
                   "rows": []}
            by_key[k] = ent
            ents.append(ent)
        if row.get("flipped"):
            ent["flipped"] = True
        deltas = row.get("delta") or []
        ent["rows"].append({
            **row,
            "display": [_hub_display_value(v) for v in row.get("values") or []],
            "delta_disp": [("" if i == ref_idx else _hub_delta_disp(d))
                           for i, d in enumerate(deltas)],
        })
    for ent in ents:
        canonical = ent["entity"] or ""
        mapped = [n for n in ent["names"] if n and n != canonical]
        header = canonical
        if mapped:
            header = f"{canonical} ↔ {mapped[0]}"     # U2 mapping header
        if ent["scope"] == "pair" and ent["flipped"]:
            header += " · orientation flipped"
        ent["header"] = header
    return ents


def _hub_result_view(result: dict, ref_idx: int) -> dict:
    """Decorate the engine result for rendering: sections, inline/lazy split
    (A5 budget), summary entities, per-group U2 mapping headers."""
    view = dict(result)
    mapping_pairs = (result.get("mapping") or {}).get("pairs") or {}
    sections: list[dict] = []
    by_section: dict = {}
    inline_budget = _HUB_INLINE_TOTAL_ROWS
    for group in result.get("groups") or []:
        sec = by_section.get(group["section"])
        if sec is None:
            sec = {"name": group["section"], "groups": []}
            by_section[group["section"]] = sec
            sections.append(sec)
        g = dict(group)
        n_rows = len(group.get("rows") or [])
        g["n_rows"] = n_rows
        g["mapped_to"] = mapping_pairs.get(group.get("entity"))
        if n_rows and (n_rows <= _HUB_INLINE_GROUP_ROWS
                       and inline_budget - n_rows >= 0):
            g["view_rows"] = _hub_view_rows(group["rows"], ref_idx)
            g["lazy"] = False
            inline_budget -= n_rows
        else:
            g["view_rows"] = []
            g["lazy"] = n_rows > 0
        g.pop("rows", None)   # never ship raw rows to the template
        sec["groups"].append(g)
    view["sections"] = sections
    view["summary_entities"] = _hub_summary_view(result, ref_idx)
    return view


def _hub_strips(result: dict, sources) -> dict:
    """Per-source structure-strip cards for TopoGraph.renderStatic (P2).

    Mode by bucket (docs/49): ① ``hidden`` unless the wiring/topology
    actually changed (then ``wiring-changed`` — amber banner + strip);
    ② ``tint`` — stones whose mapped counterpart differs get
    ``cmp-stone-diff``; ③ ``plain`` — shape-at-a-glance only, NEVER
    tinted (no correspondence exists).
    """
    bucket = result.get("bucket") or 0
    ref_idx = result.get("ref") or 0
    try:
        snaps = [compare_engine.snapshot_for(s) for s in sources]
    except LookupError:
        return {"mode": "off", "cards": []}

    changed_entities: set[str] = set()
    if bucket == 2:
        for g in result.get("groups") or []:
            if g.get("section") != "Qubits":
                continue
            c = g.get("counts") or {}
            # Only REAL inter-source differences tint a stone — not derived
            # (#./ self-refs), provenance, unresolved, link-changed or
            # type-changed rows, which the over-broad "not equal/within" filter
            # counted (false stone tints on identical chips with self-refs).
            if sum(v for k, v in c.items() if k in compare_engine.CHANGED_CLASSES):
                changed_entities.add(g.get("entity"))
    mapping_pairs = (result.get("mapping") or {}).get("pairs") or {}
    mapped_changed = {mapping_pairs.get(e) for e in changed_entities} - {None}

    cards = []
    for i, snap in enumerate(snaps):
        st = snap.structure or {}
        gates = st.get("gates") or []
        if "cr" in gates:
            gate = "cr"
        elif "cz" in gates:
            gate = ("cz_fixed" if st.get("chip_type") == "fixed_frequency"
                    else "cz_tunable")
        else:
            gate = "plain"
        qubits = []
        for q in snap.qubits:
            loc = snap.grid.get(q)
            entry: dict[str, Any] = {
                "id": q,
                "grid_location": f"{loc[0]},{loc[1]}" if loc else None,
            }
            if bucket == 2 and changed_entities:
                if i == ref_idx and q in changed_entities:
                    entry["cls"] = "cmp-stone-diff"
                elif i != ref_idx and q in mapped_changed:
                    entry["cls"] = "cmp-stone-diff"
            qubits.append(entry)
        pairs = [[c, t] for (c, t) in snap.pair_endpoints.values() if c and t]
        cards.append({
            "qubits": qubits, "pairs": pairs, "gate": gate,
            "ref": i == ref_idx,
        })

    if bucket == 1:
        # Topology signal only: the ``wiring`` section carries the string port
        # ASSIGNMENTS (a genuine re-route), so a non-equal count there is a real
        # wiring change.  The ``ports``/``octaves``/``mixers`` sections carry
        # calibrated NUMERIC values (LO/downconverter freq, port delay, DC
        # offset) that drift on routine recalibration WITHOUT any topology
        # change — counting those here fired a false "Wiring / topology changed"
        # banner on structurally identical snapshots.  A real structural change
        # (qubit/pair/instrument set, grid, gate) is caught by the structure
        # fallback below.
        changed = False
        for g in result.get("groups") or []:
            if (g.get("section") != "Infrastructure"
                    or g.get("entity") != "wiring"):
                continue
            c = g.get("counts") or {}
            # A real re-route is a MODIFIED/one-sided assignment — not a
            # link-changed (same physical port via a rewired pointer) or a
            # derived/provenance row (same over-broad filter as the ② tints).
            if sum(v for k, v in c.items() if k in compare_engine.CHANGED_CLASSES):
                changed = True
                break
        if not changed:
            structs = {json.dumps(s.structure, sort_keys=True, default=str)
                       for s in snaps}
            changed = len(structs) > 1
        mode = "wiring-changed" if changed else "hidden"
    elif bucket == 2:
        mode = "tint"
    else:
        mode = "plain"
    return {"mode": mode, "cards": cards}


def _hub_suggestion(sources, bucket: int, hinted: bool):
    """Fingerprint-based ①-suggestion (docs/49 zone B + U1b).

    Ghost [Use ①] line when every source carries the SAME full fingerprint
    token (align() would say "aligned") and no context is chosen yet.
    ``primary`` (focused CTA, one Enter to results) ONLY for deep links that
    arrive hinted AND fingerprint-proven — never for manual baskets, and the
    hint alone is never trusted. Never pre-selects (axiom 2).
    """
    if bucket or len(sources) < 2:
        return None
    if any(s.wiring_missing for s in sources):
        # a wiring-less fingerprint is a name-set match, not device identity
        return None
    tokens = {s.fingerprint_token for s in sources}
    if None in tokens or len(tokens) != 1:
        return None
    return {"bucket": 1, "primary": bool(hinted)}


def _hub_map_anchor(src) -> str:
    """A device-stable anchor for one source (A1: the anchor identifies
    the DEVICE, never one frozen snapshot of it).

    - ``hist:`` sources anchor on the chip key — every snapshot of a chip
      shares one record (per-snapshot path anchors fragmented the
      confirm-once promise: each new snapshot re-asked for confirmation).
    - ``run:`` archive layouts anchor on the run's DATA-derived chip
      identity (fingerprint token), never its folder depth: the flat
      qualibrate layout is ``<data_root>/<date>/#run/quam_state``, so
      three-up from quam_state is the shared data root — every device in a
      single-cluster / flat-storage workspace collapsed onto ONE anchor,
      cross-applying ② maps between different chips. The fingerprint token
      (network + qubit/pair topology) is device-stable AND distinguishes
      chips of different design; fall back to the folder chip-name, then
      the path.
    - ``ws:``/``working:``/``drop:`` anchor on the folder path (stable on
      this machine; drops never persist anyway).
    Known v1 drift: the SAME chip reached via ws: live vs hist: snapshot
    still keys two records — unifying those needs a cross-origin device
    identity, deferred with the shareable-URL work. Two same-design chips
    on one cluster are indistinguishable from run data alone; that residual
    ambiguity is out of scope (their auto-map is identity anyway).
    """
    if src.origin == compare_sources.ORIGIN_HISTORY:
        rest = src.ref.split(":", 1)[1]
        chip = rest.rsplit("/", 1)[0]
        if chip:
            return f"hist:{chip}"
    if src.origin == compare_sources.ORIGIN_RUN:
        if src.fingerprint_token:
            return f"run:{src.fingerprint_token}"
        if src.chip_name:
            return f"run:{src.chip_name}"
    return src.path


def _hub_map_anchors(a, b) -> tuple[str, str, str]:
    """(network_token, anchor_ref, anchor_other) for MappingStore (A1).

    ② compares chips whose networks usually DIFFER — the record token is
    the lexicographically smaller of the two network tokens, so the key
    is deterministic and ref-independent (the store sorts anchors the
    same way)."""
    return (min(a.network_token, b.network_token),
            _hub_map_anchor(a), _hub_map_anchor(b))


def _hub_load_saved_map(sources, ref_idx: int):
    """Auto-reload a previously CONFIRMED mapping for this source pair (A1).

    Returns the MappingStore.load dict (pairs oriented ref→other, stale
    names split out) or None. Drop-origin sources are session-only."""
    if len(sources) != 2:
        return None
    a, b = sources[ref_idx], sources[1 - ref_idx]
    if compare_sources.ORIGIN_DROP in (a.origin, b.origin):
        return None
    try:
        snap_a = compare_engine.snapshot_for(a)
        snap_b = compare_engine.snapshot_for(b)
    except LookupError:
        return None
    token, anchor_a, anchor_b = _hub_map_anchors(a, b)
    try:
        return compare_engine.MappingStore(current_app.instance_path).load(
            token, anchor_a, anchor_b,
            set(snap_a.qubits), set(snap_b.qubits))
    except Exception:
        logger.exception("saved-mapping load failed")
        return None


def _hub_map_view(view: dict) -> None:
    """Attach the mapping-editor lists + the U7 wrong-② guard to the view.

    U7 has TWO triggers (both binding): <70% of qubits matched, OR >50% of
    the summary rows beyond even the Wide preset — either says "these
    probably aren't the same design"."""
    m = view.get("mapping") or {}
    pairs = m.get("pairs") or {}
    ref_names = sorted(set(pairs) | set(m.get("unmatched_a") or []))
    other_names = sorted(set(pairs.values()) | set(m.get("unmatched_b") or []))
    view["map_editor"] = {"ref_names": ref_names,
                          "other_names": other_names, "pairs": pairs}
    total = max(len(ref_names), len(other_names))
    if total and len(pairs) / total < 0.7:
        view["map_guard"] = {"matched": len(pairs), "total": total,
                             "reason": "matched"}
        return
    ref_i = view.get("ref") or 0
    beyond_wide = 0
    n_rows = 0
    for ent in view.get("summary_entities") or []:
        for row in ent.get("rows") or []:
            vals = row.get("values") or []
            ref_v = vals[ref_i] if ref_i < len(vals) else None
            if not isinstance(ref_v, (int, float)) or isinstance(ref_v, bool):
                continue
            numeric = [v for i, v in enumerate(vals)
                       if i != ref_i and isinstance(v, (int, float))
                       and not isinstance(v, bool)]
            if not numeric:
                continue
            n_rows += 1
            dim = compare_engine.dimension_of(row.get("key") or "")
            if any(not compare_engine.values_within(v, ref_v, dim, "wide")
                   for v in numeric):
                beyond_wide += 1
    if n_rows >= 4 and beyond_wide / n_rows > 0.5:
        view["map_guard"] = {"matched": len(pairs), "total": total,
                             "reason": "wide", "beyond": beyond_wide,
                             "rows": n_rows}


def _hub_apply_deduped_labels(result: dict, sources) -> None:
    """The P0 honest-label dedup, applied to the RESULT columns too —
    summary headers, ③ card heads and strip labels all render
    result['sources'], which the engine builds un-deduped."""
    res_sources = result.get("sources") or []
    if len(res_sources) != len(sources):
        return
    labels = _dedupe_compare_labels([s.label for s in sources],
                                    [s.path for s in sources])
    for entry, lab in zip(res_sources, labels):
        entry["label"] = lab


def _hub_qs(refs: list[str], bucket: int, preset: str, ref_idx: int,
            map_raw: str) -> str:
    """Canonical hub query string (used to build lazy per-group URLs)."""
    params: list[tuple[str, str]] = [("src", r) for r in refs]
    params += [("bucket", str(bucket)), ("preset", preset),
               ("ref", str(ref_idx))]
    if map_raw:
        params.append(("map", map_raw))
    return urlencode(params)


def _hub_chip_runs(chip_path: str, limit: int = 15) -> list[dict[str, Any]]:
    """Archived runs belonging to the chip owning ``chip_path`` — newest
    first, capped (the options popover is a picker, not a browser)."""
    ws = _ws()
    if not ws:
        return []
    from quam_state_manager.core.history import _sanitize_name
    try:
        target = _sanitize_name(chip_name_for(Path(chip_path)))
    except Exception:
        return []
    entries = []
    try:
        for entry in ws.all_entries:
            if _sanitize_name(chip_name_for(entry.quam_state_path)) == target:
                entries.append(entry)
    except Exception:
        return []
    entries.sort(key=_entry_recency_key, reverse=True)
    out = []
    for e in entries[:limit]:
        date_part, time_part = _entry_snapshot_parts(e)
        out.append({
            "path": str(e.quam_state_path), "run_id": e.run_id,
            "name": e.experiment_name,
            "ts": " ".join(p for p in (date_part, time_part) if p),
        })
    return out


@bp.route("/compare-hub")
def compare_hub():
    """Compare hub — the unified comparison surface (docs/49).

    Stateless: everything lives in the query string. ``src`` repeats (ref
    tokens, order = column order), ``bucket`` 1|2|3 (absent = not chosen —
    the comparison context is ALWAYS user-declared, docs/49 axiom 2),
    ``preset`` exact|lab|wide, ``ref`` index into the *valid* sources,
    ``map`` a confirmed bucket-② qubit mapping (``a:b,c:d``)."""
    refs = [r for r in request.args.getlist("src") if r][:_HUB_MAX_SOURCES]
    bucket = _int_arg("bucket", 0)
    if bucket not in (1, 2, 3):
        bucket = 0
    preset = request.args.get("preset", "lab")
    if preset not in compare_engine.TOLERANCE_PRESETS:
        preset = "lab"
    ref_idx = _int_arg("ref", 0, minimum=0)
    map_raw = request.args.get("map", "")

    workspace_chips = _detect_workspace_chips(_ws())
    live_paths = {c["path"] for c in workspace_chips if not c["snapshot_ts"]}
    sources, basket = _hub_basket(refs, live_paths)
    ref_idx = min(ref_idx, max(len(sources) - 1, 0))

    result = None
    compare_error = None
    map_warning = None
    map_source = None
    map_stale = None
    if bucket and len(sources) >= 2:
        if bucket == 2 and len(sources) != 2:
            compare_error = ("② Same design compares exactly two "
                             "sources — remove extras or switch to "
                             "① / ③.")
        else:
            qmap = None
            map_source = None
            map_stale = None
            if bucket == 2:
                qmap, map_warning = _hub_validated_map(sources, ref_idx, map_raw)
                if qmap is None:
                    map_raw = ""   # dropped — hub_qs/lazy URLs must not carry it
                if qmap is None:
                    # A1 — a confirmed mapping for this pair reloads on its own
                    saved = _hub_load_saved_map(sources, ref_idx)
                    if saved and saved.get("pairs"):
                        qmap = saved["pairs"]
                        map_source = "saved"
                        map_stale = saved.get("stale") or None
                        if map_warning:
                            # don't claim "showing the suggestion" while a
                            # saved mapping quietly applies instead
                            map_warning = ("The mapping in the URL matches "
                                           "neither device — using your "
                                           "saved mapping instead.")
                    elif saved and saved.get("stale"):
                        map_warning = ((map_warning + " ") if map_warning
                                       else "") + \
                            ("A previously saved mapping exists but ALL its "
                             "qubit names are stale on these sources — "
                             "confirm a new one.")
            try:
                result = _hub_compare(sources, bucket=bucket, preset=preset,
                                      ref=ref_idx, qubit_map=qmap)
            except (compare_sources.SourceError, LookupError) as exc:
                compare_error = f"Comparison failed: {exc}"
            except ValueError as exc:
                compare_error = str(exc)

    view = _hub_result_view(result, ref_idx) if result else None
    if view is not None:
        _hub_apply_deduped_labels(view, sources)
    if view is not None and not view.get("identical"):
        view["strips"] = _hub_strips(view, sources)
        if view.get("bucket") == 2 and view.get("mapping"):
            _hub_map_view(view)

    try:
        history_chips = _history().list_chip_histories()
    except Exception:
        history_chips = []

    trunc_total = _int_arg("trunc", 0)
    legacy_from = request.args.get("from", "")
    if legacy_from not in ("diff", "compare", "chip-compare"):
        legacy_from = ""

    template = "_compare_hub.html" if _is_htmx() else "compare_hub.html"
    return render_template(
        template,
        **_ctx(
            page="compare_hub",
            trunc_total=trunc_total if trunc_total > _HUB_MAX_SOURCES else 0,
            legacy_from=legacy_from,
            basket=basket,
            sources_count=len(sources),
            bucket=bucket,
            hub_buckets=_HUB_BUCKETS,
            preset=preset,
            preset_labels=compare_engine.PRESET_LABELS,
            preset_tips={k: compare_engine.describe_preset(k)
                         for k in compare_engine.PRESET_LABELS},
            ref_idx=ref_idx,
            map_raw=map_raw,
            result=view,
            compare_error=compare_error,
            map_warning=map_warning,
            map_source=map_source,
            map_stale=map_stale,
            summary_inline=_HUB_SUMMARY_INLINE,
            hub_qs=_hub_qs(refs, bucket, preset, ref_idx, map_raw),
            suggestion=_hub_suggestion(sources, bucket,
                                       request.args.get("hint") == "1"),
            workspace_chips=workspace_chips,
            history_chips=history_chips,
            hub_active=_active_chip_identity(),
        ),
    )


@bp.route("/compare-hub/group")
def compare_hub_group():
    """One group's rows — lazy per-group expansion (A5 rendering budget).

    Same query contract as /compare-hub plus ``section`` + ``entity`` (the
    group key) and ``eq=1`` (include equal rows). Re-runs the compare with
    ``include_summary=False``; sources come through the mtime memo and
    snapshots through the content-hash cache, so a warm expand is assembly
    cost only (~90 ms for 2 sources; ~300 ms on an 8-source basket)."""
    refs = [r for r in request.args.getlist("src") if r][:_HUB_MAX_SOURCES]
    bucket = _int_arg("bucket", 0)
    preset = request.args.get("preset", "lab")
    if preset not in compare_engine.TOLERANCE_PRESETS:
        preset = "lab"
    ref_idx = _int_arg("ref", 0, minimum=0)
    map_raw = request.args.get("map", "")
    section = request.args.get("section", "")
    entity = request.args.get("entity", "")
    include_eq = _int_arg("eq", 0) == 1

    sources, _rows = _hub_basket(refs, set())
    ref_idx = min(ref_idx, max(len(sources) - 1, 0))
    if bucket not in (1, 2) or len(sources) < 2 or (
            bucket == 2 and len(sources) != 2):
        return render_template(
            "_status.html", level="warning",
            message="Group unavailable — the sources changed. Re-run the comparison.")
    qmap = None
    if bucket == 2:
        qmap, _warn = _hub_validated_map(sources, ref_idx, map_raw)
        if qmap is None:
            saved = _hub_load_saved_map(sources, ref_idx)
            if saved and saved.get("pairs"):
                qmap = saved["pairs"]
    try:
        result = _hub_compare(sources, bucket=bucket, preset=preset,
                              ref=ref_idx, qubit_map=qmap,
                              include_equal_rows=include_eq,
                              include_summary=False)
    except (compare_sources.SourceError, LookupError, ValueError) as exc:
        return render_template("_status.html", level="warning",
                               message=f"Comparison failed: {exc}")
    for group in result.get("groups") or []:
        if group["section"] == section and group["entity"] == entity:
            g = dict(group)
            g["view_rows"] = _hub_view_rows(group.get("rows") or [], ref_idx)
            g["n_rows"] = len(group.get("rows") or [])
            g.pop("rows", None)
            return render_template(
                "_compare_hub_group.html",
                group=g, sources=result.get("sources") or [],
                ref_idx=ref_idx, show_eq=include_eq,
                hub_qs=_hub_qs(refs, bucket, preset, ref_idx, map_raw))
    return render_template(
        "_status.html", level="warning",
        message="Group not found — the comparison may have changed.")


@bp.route("/compare-hub/map/save", methods=["POST"])
def compare_hub_map_save():
    """Persist a user-confirmed bucket-② qubit mapping (A1).

    Body (JSON): ``{"srcs": [refA, refB], "ref": 0|1, "map": "a:b,c:d"}``.
    The map is validated against the actual devices before saving (same
    rules as the URL param). Drop-origin sources are session-only — the
    response says so honestly instead of pretending to persist."""
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return jsonify({"ok": False, "error": "Malformed body"}), 400
    refs = payload.get("srcs")
    map_raw = str(payload.get("map") or "")
    ref_idx = payload.get("ref") if payload.get("ref") in (0, 1) else 0
    if not isinstance(refs, list) or len(refs) != 2:
        return jsonify({"ok": False,
                        "error": "A mapping needs exactly two sources"}), 400
    try:
        sources = [_hub_resolve_one(str(r)) for r in refs]
    except compare_sources.SourceError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    qmap, warn = _hub_validated_map(sources, ref_idx, map_raw)
    if not qmap:
        return jsonify({"ok": False,
                        "error": warn or "The mapping matches neither device"}), 400
    a, b = sources[ref_idx], sources[1 - ref_idx]
    save_warning = warn   # surfaced to the user (dedup / partial matches)
    try:
        snap_a = compare_engine.snapshot_for(a)
        snap_b = compare_engine.snapshot_for(b)
        token, anchor_a, anchor_b = _hub_map_anchors(a, b)
        compare_engine.MappingStore(current_app.instance_path).save(
            token, anchor_a, anchor_b, qmap,
            set(snap_a.qubits), set(snap_b.qubits),
            origins=(a.origin, b.origin))
    except ValueError as exc:
        # drop origins etc. — the map still applies via the URL this session
        return jsonify({"ok": True, "persisted": False, "reason": str(exc)})
    except (LookupError, OSError) as exc:
        logger.exception("mapping save failed")
        return jsonify({"ok": True, "persisted": False, "reason": str(exc)})
    return jsonify({"ok": True, "persisted": True, "warning": save_warning})


_HUB_DROP_GC_MAX = 20
_hub_stash_lock = threading.Lock()


def _hub_drops_root() -> Path:
    return Path(current_app.instance_path) / "compare_drops"


@bp.route("/compare-hub/stash", methods=["POST"])
def compare_hub_stash():
    """Persist a dropped state+wiring pair → a ``drop:`` basket token.

    Drag-drop never yields a filesystem path (pywebview bridge) — the
    browser reads the files and POSTs ``{state, wiring, label}``. The pair
    is stashed content-hash-keyed under ``instance/compare_drops/<sha12>/``
    (re-drop = same dir = dedup for free) via safe_io, with a meta.json
    label sidecar, and pruned to the newest ``_HUB_DROP_GC_MAX`` dirs — a
    pruned ``drop:`` ref degrades to an honest error row, never a 500.
    """
    body_len = request.content_length
    if body_len is None:   # chunked transfer — measure the actual body
        body_len = len(request.get_data(cache=True) or b"")
    if body_len > _PREVIEW_MAX_BYTES:
        return jsonify({"ok": False,
                        "error": "Dropped files exceed the 64 MB cap"}), 413
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return jsonify({"ok": False, "error": "Malformed drop payload"}), 400
    state = payload.get("state")
    wiring = payload.get("wiring")
    if not isinstance(state, dict) or not isinstance(wiring, dict):
        return jsonify({"ok": False,
                        "error": "state and wiring must be JSON objects"}), 400
    label = str(payload.get("label") or "dropped chip")[:120]

    sha12 = working_copy.content_hash(state, wiring)[:12]
    root = _hub_drops_root()
    stash_dir = root / sha12
    try:
        # serialized: safe_io's .tmp sibling names are fixed per target, so
        # two concurrent same-content stashes would race in one directory
        with _hub_stash_lock:
            safe_io.write_state_wiring(stash_dir, state, wiring)
            safe_io.atomic_write_json(stash_dir / "meta.json", {
                "label": label,
                "created_utc": datetime.now(timezone.utc).isoformat(),
            })
    except (safe_io.LiveFileError, OSError) as exc:
        logger.exception("compare-hub stash write failed")
        return jsonify({"ok": False,
                        "error": f"Could not stash the drop: {exc}"}), 500

    # Count-cap GC, oldest-mtime first (hash names aren't chronological).
    # Stash content is always re-droppable, so plain pruning is safe.
    try:
        dirs = sorted((d for d in root.iterdir() if d.is_dir()),
                      key=lambda d: d.stat().st_mtime)
        for old_dir in dirs[:max(len(dirs) - _HUB_DROP_GC_MAX, 0)]:
            if old_dir.name != sha12:
                shutil.rmtree(old_dir, ignore_errors=True)
    except OSError:
        pass
    with _hub_src_memo_lock:
        # a re-drop with new content on an old label must not serve a memo
        _HUB_SRC_MEMO.pop(f"drop:{stash_dir}", None)
    return jsonify({"ok": True, "ref": f"drop:{stash_dir}", "label": label})


@bp.route("/compare-hub/summary")
def compare_hub_summary():
    """Full Summary-tab table — loaded on demand past _HUB_SUMMARY_INLINE
    entities (the A5 rendering budget applies to the summary too)."""
    refs = [r for r in request.args.getlist("src") if r][:_HUB_MAX_SOURCES]
    bucket = _int_arg("bucket", 0)
    preset = request.args.get("preset", "lab")
    if preset not in compare_engine.TOLERANCE_PRESETS:
        preset = "lab"
    ref_idx = _int_arg("ref", 0, minimum=0)
    map_raw = request.args.get("map", "")

    sources, _rows = _hub_basket(refs, set())
    ref_idx = min(ref_idx, max(len(sources) - 1, 0))
    if bucket not in (1, 2) or len(sources) < 2 or (
            bucket == 2 and len(sources) != 2):
        return render_template(
            "_status.html", level="warning",
            message="Summary unavailable — the sources changed. Re-run the comparison.")
    qmap = None
    if bucket == 2:
        qmap, _warn = _hub_validated_map(sources, ref_idx, map_raw)
        if qmap is None:
            saved = _hub_load_saved_map(sources, ref_idx)
            if saved and saved.get("pairs"):
                qmap = saved["pairs"]
    try:
        result = _hub_compare(sources, bucket=bucket, preset=preset,
                              ref=ref_idx, qubit_map=qmap)
    except (compare_sources.SourceError, LookupError, ValueError) as exc:
        return render_template("_status.html", level="warning",
                               message=f"Comparison failed: {exc}")
    entities = _hub_summary_view(result, ref_idx)
    _hub_apply_deduped_labels(result, sources)
    return render_template("_compare_hub_summary.html",
                           entities=entities,
                           srcs=result.get("sources") or [],
                           ref_idx=ref_idx, show_more=False, hub_qs="")


@bp.route("/compare-hub/options")
def compare_hub_options():
    """The "which state?" popover for one chip (docs/49 zone-A picker).

    ``path`` (a quam_state folder — workspace chip / browsed) or ``chip``
    (a history chip key). Lists Live / Working / history snapshots /
    archived runs; each option is one click to add (default = Live,
    listed first — fixes the silent-oldest-run trap)."""
    path = request.args.get("path", "")
    chip_key = request.args.get("chip", "")
    name = request.args.get("name", "") or chip_key or (
        Path(path).name if path else "")
    hm = _history()
    options: list[dict[str, Any]] = []

    if path:
        p = Path(path)
        try:
            has_state = (p / "state.json").exists()
        except OSError:
            has_state = False
        if has_state:
            options.append({"ref": f"ws:{path}", "label": "Live files",
                            "badge": "LIVE", "hint": str(p)})
        # working: refs resolve ONLY through an in-memory store — offering
        # one for a merely-persisted copy would mint a permanently dead
        # basket row (review finding). A saved-but-not-loaded working copy
        # is offered as a plain folder ref onto its on-disk working files.
        if _hub_working_lookup(path) is not None:
            options.append({"ref": f"working:{path}", "label": "Working state",
                            "badge": "WORKING",
                            "hint": "unsaved edits included"})
        else:
            try:
                wc = working_copy.load(Path(current_app.instance_path), p)
            except Exception:
                wc = None
            if wc is not None:
                options.append({
                    "ref": f"ws:{wc.working_folder}",
                    "label": "Working state (saved on disk — chip not loaded)",
                    "badge": "WORKING",
                    "hint": str(wc.working_folder)})
        if not chip_key:
            try:
                chip_key = hm._key_for(p)
            except Exception:
                chip_key = ""

    if chip_key:
        snap_path = Path(path) if path else _path_for_chip_key(chip_key)
        try:
            snaps = hm.list_snapshots(snap_path)[:20]
        except Exception:
            snaps = []
        for meta in snaps:
            label = compare_sources._hist_ts_human(meta.timestamp)
            extra = meta.label or meta.experiment_name or meta.trigger or ""
            options.append({
                "ref": f"hist:{chip_key}/{meta.timestamp}",
                "label": f"{label}{' · ' + extra if extra else ''}",
                "badge": "HISTORY",
                "hint": "pinned" if meta.pinned else "",
            })

    if path:
        for r in _hub_chip_runs(path):
            rid = f"#{r['run_id']}" if r.get("run_id") is not None else ""
            bits = " · ".join(b for b in (rid, r.get("name") or "", r.get("ts") or "") if b)
            options.append({"ref": f"run:{r['path']}",
                            "label": bits or r["path"],
                            "badge": "RUN", "hint": r["path"]})

    return render_template("_compare_hub_options.html",
                           name=name, options=options)


@bp.route("/param-history")
def param_history():
    """Param History dashboard — sparkline grid of trended state.json fields.

    Multi-chip aware: ``?chip_key=`` switches the dashboard to a chip
    other than the currently loaded one.  The alignment banner and
    chip selector are computed from on-disk histories + workspace.
    """
    store = _store()
    if not store:
        return render_template("_empty_state.html", page="parameter history")

    hm = _history()
    loaded_path = Path(_active_path())
    loaded_key = hm._key_for(loaded_path)
    # Pre-ladder path-derived key: the client's localStorage/sessionStorage
    # backfill guards were written under it — emitted as data-legacy-chip-key
    # so an adopted/named chip doesn't re-fire a redundant auto-backfill.
    from quam_state_manager.core.history import (
        _sanitize_name as _hist_sanitize, chip_name_for as _chip_name_for)
    legacy_chip_key = _hist_sanitize(_chip_name_for(loaded_path))

    # Raw user selections (preserve empty list as "user explicitly cleared")
    raw_props = request.args.getlist("props")
    raw_qubits = request.args.getlist("qubits")
    raw_triggers = request.args.getlist("triggers")
    chip_key_param = request.args.get("chip_key", "").strip()

    active_chip_key = chip_key_param or loaded_key
    is_loaded_chip = (active_chip_key == loaded_key)
    target_path = loaded_path if is_loaded_chip else _path_for_chip_key(active_chip_key)

    props = raw_props or list(DEFAULT_TRACKED_PROPERTIES)
    qubits_selected = raw_qubits
    qubit_filter = qubits_selected or None
    triggers = raw_triggers or None
    # Loaded chip's default view = recent 7d (active monitoring).
    # Other chip view = all (user is browsing historical data, often
    # months old from past backfills).
    default_since = "now-7d" if is_loaded_chip else "all"
    since_raw = request.args.get("since", default_since)
    since = _parse_since(since_raw)
    until = _parse_since(request.args.get("until"))
    only_changed = request.args.get("only_changed", "0") == "1"

    # The trend index reads open a fresh sqlite connection; if an experiment
    # writeback or a concurrent backfill holds the WAL past the busy timeout, these
    # raise sqlite3.OperationalError ("database is locked"). Degrade gracefully
    # (empty grid + a non-fatal banner) instead of 500'ing — a 500 on an HX-Request
    # swaps a Werkzeug error page into #param-history-root, turning the whole menu
    # into a dead/broken page (the diff/compare routes already catch this way).
    index_error = None
    try:
        rows = hm.extract_property_history(
            target_path, props,
            qubit_filter=qubit_filter,
            since=since, until=until,
            triggers=triggers,
        )
    except Exception as exc:   # noqa: BLE001 — never 500 the dashboard on a busy index
        logger.warning("param-history trend query failed: %s", exc)
        rows = []
        index_error = "The trend index is busy (a save or import may be running). Reload in a moment."

    if only_changed:
        def _changed(values: list[dict[str, Any]]) -> bool:
            seen: set[float] = set()
            for v in values:
                x = v.get("value")
                if isinstance(x, (int, float)):
                    seen.add(round(float(x), 12))
                if len(seen) > 1:
                    return True
            return False
        rows = [r for r in rows if _changed(r["values"])]

    try:
        summary = hm.index_summary(target_path)
        summary["window_count"] = hm.count_window(target_path, since=since, until=until, triggers=triggers)
    except Exception as exc:   # noqa: BLE001 — same busy-index degrade as the trend query
        logger.warning("param-history summary failed: %s", exc)
        # Must match index_summary's shape (+ window_count) or the template throws.
        summary = {"total": 0, "by_trigger": {}, "latest": None, "window_count": 0}
        if index_error is None:
            index_error = "The trend index is busy (a save or import may be running). Reload in a moment."

    # Honest footprint line for the header ("N snapshots · X on disk") —
    # cached per (count, newest ts), so steady-state renders pay no walk.
    disk_stats = None
    if target_path:
        try:
            disk_stats = hm.history_disk_stats(target_path)
        except Exception:   # noqa: BLE001 — a stats failure must never kill the page
            disk_stats = None

    # Qubit list: when viewing a different chip, derive qubits from the
    # SQLite index (since store.qubit_names is the loaded chip's). When
    # viewing the loaded chip, use the store directly so the list is
    # complete even if some qubits have no indexed data yet.
    if is_loaded_chip:
        all_qubits = list(store.qubit_names)
    else:
        try:
            conn = hm._open_index(target_path)
            all_qubits = [r[0] for r in conn.execute(
                "SELECT DISTINCT qubit FROM param_history ORDER BY qubit"
            ).fetchall()]
            conn.close()
        except Exception:
            all_qubits = sorted({r["qubit"] for r in rows})

    qubits = sorted(qubits_selected) if qubits_selected else sorted(all_qubits)
    by_cell = {(r["qubit"], r["property"]): r for r in rows}

    # Current-value overlay: only meaningful when viewing the loaded chip.
    current_values: dict[tuple[str, str], Any] = {}
    engine = _engine()
    if is_loaded_chip and engine:
        for q in qubits:
            try:
                qd = engine.get_qubit(q)
                for p in props:
                    current_values[(q, p)] = qd.get(p)
            except Exception:
                pass

    # Pre-render sparkline SVGs server-side (Family D1+D2 in
    # docs/23_param_history_performance.md): the JS used to JSON.parse
    # + compute coords + innerHTML for every cell, blocking the main
    # thread for ~1.5 s at 1000+ cells. Doing it once in Python here
    # keeps the work off the user's browser.
    for r in rows:
        cur = current_values.get((r["qubit"], r["property"]))
        cur_num = float(cur) if isinstance(cur, (int, float)) and not isinstance(cur, bool) else None
        r["svg_inner"] = HistoryManager.render_sparkline_svg_inner(
            r["values"], current=cur_num,
        )

    # Multi-chip metadata for the selector + alignment banner.
    #
    # The chip selector lists ONLY:
    #   - active_chips: the currently-loaded chip (path-derived from quam_state).
    #     Workspace top-level folders (e.g. data/LabB_1Q/) DO NOT define chips
    #     here — they're data sources whose alignment with the loaded chip is
    #     determined by network fingerprint, surfaced via the alignment banner.
    #   - archived_chips: chips with on-disk history that aren't currently
    #     loaded. Shown in a collapsible "Other chip histories on disk" section.
    ws = _ws()
    all_disk_chips = hm.list_chip_histories()
    disk_by_key = {c["key"]: c for c in all_disk_chips}

    # Project lens (docs/63): the loaded chip's selector text becomes
    # "<project> · <key>" when the context carries a scope. DISPLAY ONLY —
    # ``key`` (and every URL / data attribute built from it) stays the raw
    # history key so ?chip_key=, hist: refs and the JS contract are untouched.
    scope_project = (_active_ctx() or {}).get("qualibrate_project")

    active_chips: list[dict[str, Any]] = []
    active_keys: set[str] = set()
    # Always include the currently-loaded chip — the only thing in the main
    # selector is what the user has loaded right now.
    if loaded_key not in active_keys:
        info = disk_by_key.get(loaded_key, {})
        active_chips.insert(0, {
            "key": loaded_key,
            "name": f"{scope_project} · {loaded_key}" if scope_project else loaded_key,
            "snapshot_count": info.get("snapshot_count", 0),
            "latest_timestamp": info.get("latest_timestamp", ""),
            "qubits": info.get("qubits", []),
        })
        active_keys.add(loaded_key)

    # Archived chips: disk history exists but chip isn't in workspace
    # and isn't loaded.
    archived_chips = [c for c in all_disk_chips if c["key"] not in active_keys]
    archived_chips.sort(key=lambda c: c.get("latest_timestamp", ""), reverse=True)

    # Alignment scan only meaningful for the currently-loaded chip; it
    # answers "which workspace experiments belong to my loaded chip?".
    alignment = None
    if is_loaded_chip:
        try:
            alignment = hm.scan_workspace_alignment(loaded_path, ws) if ws else None
        except Exception:
            logger.warning("Alignment scan failed", exc_info=True)
            alignment = None

    # Importable workspace count for the empty-state CTA + auto-incremental
    # check (see docs/23_param_history_performance.md, "What Phase 1 actually
    # shipped" → empty-state CTA discussion). ``aligned`` matches both
    # network and qubit labels, so it's the conservative count of "things
    # we'd ingest right now without prompting". ``renamed`` would also be
    # ingested only with force_renamed=True, so it's not added here.
    importable_count = 0
    pending_import_count = 0
    if alignment is not None:
        importable_count = int(alignment.get("counts", {}).get("aligned", 0) or 0)
        # RESIDUAL (auto-backfill gate): aligned workspace experiments whose run_id
        # isn't in this chip's index yet. Replaces the old aligned-vs-indexed count
        # diff + threshold-of-5 that silently skipped a small batch (1-4 new experiments).
        # A false-positive is harmless (the backfill content-hash-dedups), so run_id-None
        # entries are NOT counted, to avoid re-firing the scan every session.
        try:
            aligned_entries = alignment.get("aligned") or []
            indexed = hm.indexed_run_ids(loaded_path)
            pending_import_count = sum(
                1 for e in aligned_entries
                if getattr(e, "run_id", None) is not None and e.run_id not in indexed)
        except Exception:  # noqa: BLE001
            logger.warning("pending-import residual failed", exc_info=True)
            pending_import_count = 0

    last_failures, last_attempted = _last_backfill_failures(loaded_path)

    template = "_param_history.html" if _is_htmx() else "param_history.html"
    return render_template(
        template,
        **_ctx(
            page="param_history",
            qubits=qubits,
            qubits_selected=qubits_selected,
            properties=props,
            all_properties=list(DEFAULT_TRACKED_PROPERTIES),
            all_qubits=all_qubits,
            cells=by_cell,
            current_values=current_values,
            summary=summary,
            disk_stats=disk_stats,
            index_error=index_error,
            since=since_raw,
            triggers_filter=triggers or [],
            only_changed=only_changed,
            # Multi-chip:
            active_chip_key=active_chip_key,
            loaded_chip_key=loaded_key,
            legacy_chip_key=legacy_chip_key,
            is_loaded_chip=is_loaded_chip,
            # One-time docs/20-v2 notice (popped: shown on the first render
            # after the v3 index-attribution migration actually moved rows).
            reattributed_v3=current_app.config.pop("history_reattributed_v3", None),
            active_chips=active_chips,
            archived_chips=archived_chips,
            # Back-compat alias for any code/tests still referencing the
            # old combined name. New UI uses active_chips + archived_chips.
            chip_histories=active_chips + archived_chips,
            alignment=alignment,
            # How many workspace experiments are ready to import (empty-state
            # CTA + auto-incremental on revisit). Only meaningful on the
            # loaded chip; 0 otherwise.
            importable_count=importable_count,
            # Residual (aligned-but-unindexed) — the auto-backfill gate (feedback P1).
            pending_import_count=pending_import_count,
            # Latest chip-swap event (if any) for the banner.
            last_chip_swap=current_app.config.get("last_chip_swap"),
            # Pending ambiguity decisions surfaced after the last backfill,
            # if any. Pulled from the most-recent backfill state for this
            # chip key.
            pending_decisions=_pending_decisions_for(loaded_path),
            # Failed-import banner. Both default to empty/0 when no
            # backfill has completed for this chip yet — banner hides via
            # `{% if last_backfill_failures %}`.
            last_backfill_failures=last_failures,
            last_backfill_attempted=last_attempted,
        ),
    )


_CHANGES_SNAPS = 20        # snapshots per page — the feed's unit is the EVENT
_CHANGES_ROWS = 25         # rows shown per snapshot before "and N more"
_CHANGES_ROWS_AT = 2000    # one snapshot opened in full


@bp.route("/param-history/changes")
def param_history_changes():
    """"What changed" — every numeric parameter's change points, newest first.

    The curated dashboard answers "how has T1 drifted"; this answers the
    question a user actually arrives with — "what did that run change?" — over
    ALL ~8,000 numeric parameters, which only became affordable once they were
    stored as change points (docs/83).

    Paged by SNAPSHOT, not by row: a regenerate rewrites thousands of
    parameters at once (2,716 measured on a real chip) and a row-paged feed
    would spend the whole page on it and hide every other event.
    """
    store = _store()
    if not store:
        return render_template("_empty_state.html", page="parameter history")
    hm = _history()
    path = Path(_active_path())
    prefix = (request.args.get("prefix") or "").strip()
    before = (request.args.get("before") or "").strip() or None
    at = (request.args.get("at") or "").strip() or None
    try:
        groups = hm.leaf_change_groups(
            path, limit_snaps=1 if at else _CHANGES_SNAPS + 1,
            rows_per_snap=_CHANGES_ROWS_AT if at else _CHANGES_ROWS,
            prefix=prefix or None, before_ts=before, at_ts=at)
    except Exception as exc:      # noqa: BLE001 — never 500 the menu
        logger.warning("param-history changes failed: %s", exc, exc_info=True)
        groups = []
    has_more = (not at) and len(groups) > _CHANGES_SNAPS
    groups = groups[:1 if at else _CHANGES_SNAPS]

    roots = _uid_roots()
    for g in groups:
        ts = g.get("timestamp") or ""
        g["when"] = (f"{ts[0:4]}-{ts[4:6]}-{ts[6:8]} {ts[9:11]}:{ts[11:13]}"
                     if len(ts) >= 13 else ts)
        g["uid"] = _uid_for_run_ref(g.get("experiment_folder_path"),
                                    g.get("run_id"), roots)

    stats = hm.leaf_stats(path)
    oldest = groups[-1]["timestamp"] if groups else None
    template = ("_param_history_changes.html" if _is_htmx()
                else "param_history_changes.html")
    return render_template(
        template, **_ctx(page="param_history", groups=groups, stats=stats,
                         prefix=prefix, has_more=has_more, oldest_ts=oldest,
                         at_ts=at))


@bp.route("/param-history/param-search")
def param_history_param_search():
    """Typeahead over every indexed parameter path (docs/83). 8,000 paths is
    not a dropdown — this is how a user finds the one they mean."""
    if not _store():
        return jsonify(ok=False, results=[]), 400
    q = (request.args.get("q") or "").strip()
    try:
        hits = _history().leaf_search(Path(_active_path()), q, limit=30)
    except Exception:      # noqa: BLE001
        logger.debug("param search failed", exc_info=True)
        hits = []
    return jsonify(ok=True, results=hits)


def _pending_decisions_for(loaded_path: Path) -> list[dict[str, Any]]:
    """Return the list of pending chip-decision prompts from the last backfill."""
    key = str(Path(loaded_path).resolve())
    with _backfill_lock:
        state = _backfill_state.get(key)
    if not state:
        return []
    return list(state.get("pending_decisions", []) or [])


def _last_backfill_failures(loaded_path: Path) -> tuple[list[dict[str, Any]], int]:
    """Return ``(failures, attempted_count)`` from the most recent completed
    backfill for the loaded chip.

    Only surfaces results when the backfill finished (``status == "done"``);
    while a backfill is still running, the banner stays hidden. Used to
    drive the Param History page's amber failure-banner so the user can
    see *why* the auto-import didn't close the workspace gap — which
    otherwise produces the infinite "Importing…" loop the bug report
    describes.
    """
    key = str(Path(loaded_path).resolve())
    with _backfill_lock:
        state = _backfill_state.get(key)
    if not state or state.get("status") != "done":
        return ([], 0)
    failures = list(state.get("failed_entries") or [])
    attempted = int(state.get("attempted_count") or state.get("total") or 0)
    return (failures, attempted)


@bp.route("/param-history/dismiss-chip-swap", methods=["POST"])
def param_history_dismiss_chip_swap():
    """Clear the last_chip_swap banner state once the user has seen it."""
    current_app.config.pop("last_chip_swap", None)
    return jsonify({"ok": True})


@bp.route("/param-history/decide", methods=["POST"])
def param_history_decide_chip():
    """Persist a user's ambiguity decision for (chip_key, data_folder).

    Body params:
      - chip_key: str
      - data_folder: str
      - decision: "same" or "different"
    """
    from quam_state_manager.core.history import save_chip_decision

    chip_key = (request.values.get("chip_key", "") or "").strip()
    data_folder = (request.values.get("data_folder", "") or "").strip()
    decision = (request.values.get("decision", "") or "").strip()
    if not chip_key or not data_folder or decision not in ("same", "different"):
        return jsonify({"error": "invalid params"}), 400
    try:
        save_chip_decision(current_app.instance_path, chip_key, data_folder, decision)
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500
    return jsonify({"ok": True, "chip_key": chip_key, "data_folder": data_folder, "decision": decision})


@bp.route("/param-history/expand")
def param_history_expand():
    """Drawer detail — full-resolution Plotly chart for one (qubit, property)."""
    store = _store()
    if not store:
        return jsonify({"error": "No state loaded"}), 400

    qubit = request.args.get("qubit", "").strip()
    prop = request.args.get("prop", "").strip()
    if not qubit or not prop:
        return jsonify({"error": "qubit and prop required"}), 400

    hm = _history()
    # Optional ``?chip_key=`` targets another chip's dir — the grid supports
    # chip_key but the drawer used to silently chart the LOADED chip when
    # opened from an archived chip's grid.
    chip_key = (request.args.get("chip_key") or "").strip()
    target_path = _active_path()
    is_loaded = True
    if chip_key and chip_key != hm._key_for(Path(target_path)):
        target_path = _path_for_chip_key(chip_key)
        is_loaded = False
    rows = hm.extract_property_history(
        target_path, [prop],
        qubit_filter=[qubit], downsample=None,
    )
    row = rows[0] if rows else {"qubit": qubit, "property": prop, "raw_pointer": None, "values": []}

    current_value = None
    if is_loaded:
        engine = _engine()
        if engine:
            try:
                current_value = engine.get_qubit(qubit).get(prop)
            except Exception:
                pass

    return render_template(
        "_param_history_drawer.html",
        row=row,
        row_json=json.dumps(row),
        qubit=qubit,
        prop=prop,
        current_value=current_value,
    )


@bp.route("/param-history/backfill", methods=["POST"])
def param_history_backfill():
    """Kick off (or check) the alignment-aware workspace backfill.

    Pass ``force_renamed=1`` (form or query) to also ingest experiments
    whose hardware fingerprint matches but whose qubit names differ.
    """
    store = _store()
    if not store:
        return jsonify({"error": "No state loaded"}), 400

    folder = _active_path()
    key = str(Path(folder).resolve())
    # Resolve dependencies on the request thread — the background thread runs
    # after the app context has closed, so it cannot use current_app.
    hm = _history()
    ws = _ws()
    instance_path = current_app.instance_path
    force_renamed = (request.values.get("force_renamed", "0") == "1")

    with _backfill_lock:
        state = _backfill_state.get(key)
        if state and state.get("status") == "running":
            return jsonify(state)

        _backfill_state[key] = {
            "status": "running", "done": 0, "total": 0,
            "ingested": 0, "skipped_renamed": 0,
            "skipped_different": 0, "skipped_unknown": 0,
        }

    def _run() -> None:
        def _progress(done: int, total: int) -> None:
            with _backfill_lock:
                _backfill_state[key]["done"] = done
                _backfill_state[key]["total"] = total

        try:
            report = hm.backfill_from_workspace(
                folder, ws, progress_cb=_progress,
                force_renamed=force_renamed,
                instance_path=instance_path,
            )
            with _backfill_lock:
                _backfill_state[key].update({"status": "done", **report})
        except Exception as exc:
            logger.exception("Backfill failed")
            with _backfill_lock:
                _backfill_state[key].update({"status": "error", "error": str(exc)})

    threading.Thread(target=_run, daemon=True).start()
    return jsonify(_backfill_state[key])


@bp.route("/param-history/backfill/status")
def param_history_backfill_status():
    """Poll-friendly progress endpoint for the backfill job."""
    store = _store()
    if not store:
        return jsonify({"status": "idle"})
    key = str(Path(_active_path()).resolve())
    with _backfill_lock:
        return jsonify(_backfill_state.get(key, {"status": "idle"}))


# ======================================================================
# API: JSON endpoints for programmatic access
# ======================================================================


@bp.route("/api/qubit/<name>")
def api_qubit(name: str):
    engine = _engine()
    if not engine:
        return jsonify({"error": "No state loaded"}), 400
    try:
        return jsonify(engine.get_qubit(name))
    except KeyError as e:
        return jsonify({"error": str(e)}), 404


@bp.route("/api/pair/<name>")
def api_pair(name: str):
    engine = _engine()
    if not engine:
        return jsonify({"error": "No state loaded"}), 400
    try:
        return jsonify(engine.get_pair(name))
    except KeyError as e:
        return jsonify({"error": str(e)}), 404


@bp.route("/api/search")
def api_search():
    index = _index()
    query = request.args.get("q", "").strip()
    if not index or not query:
        return jsonify([])

    limit = _int_arg("limit", 50, minimum=1)
    category = request.args.get("category")
    results = index.search(query, limit=limit, category=category)

    return jsonify([
        {
            "dot_path": r.dot_path,
            "value": r.raw_value if not isinstance(r.raw_value, float) or r.raw_value == r.raw_value else None,
            "category": r.category,
            "parent_id": r.parent_id,
            "leaf_key": r.leaf_key,
            "score": r.score,
        }
        for r in results
    ])


@bp.route("/api/topology")
def api_topology():
    if request.args.get("refresh") == "1":
        path = _active_path()
        if path:
            _activate_quam(path)
    engine = _engine()
    if not engine:
        return jsonify({"error": "No state loaded"}), 400
    return jsonify(engine.get_topology())


@bp.route("/api/topology-mtime")
def api_topology_mtime():
    """Lightweight live-state change check — os.stat only, never a content read.

    ``changed`` is true when the live state/wiring files differ from the
    working copy's last sync point.
    """
    ctx = _active_ctx()
    wc = ctx.get("working_copy") if ctx else None
    if wc is None:
        return jsonify({"error": "No state loaded"}), 400
    try:
        sm, wm = safe_io.state_wiring_mtimes(wc.live_folder)
        changed = working_copy.live_changed(wc)
    except OSError:
        return jsonify({"error": "State folder not found — files may have been moved or deleted"}), 404
    if not changed:
        # The cheap mtime check can miss a content rewrite (coarse / same-second
        # mtime, an editor save). Escalate to the throttled ground-truth hash so
        # the poll still recovers — and surface its verdict to the banner.
        _refresh_live_diverged(ctx)
        changed = bool(ctx.get("live_diverged"))
    return jsonify({
        "state_mtime": sm,
        "wiring_mtime": wm,
        "folder": str(wc.live_folder),
        "changed": changed,
    })


def _wc_keys_in_use() -> set[str]:
    """Working-copy keys a running State Manager may still mutate — this
    process's active context plus everything in its in-memory QUAM cache,
    UNION every live peer window's open chip (docs/80). GC must never
    delete these even when they scan as clean (an unsaved in-memory edit
    is invisible on disk)."""
    keys: set[str] = set()
    with _quam_cache_lock:
        ctxs = list(_quam_cache.values())
    ctx = _active_ctx()
    if ctx is not None:
        ctxs.append(ctx)
    for c in ctxs:
        wc = c.get("working_copy") if isinstance(c, dict) else None
        if wc is not None:
            keys.add(wc.key)
    # docs/80: other live windows share this instance dir, so THEIR open
    # chip's working copy can scan provably clean on disk while the peer
    # holds in-memory-only edits it is about to save — memory is invisible
    # to the scan, which is exactly why liveness ALONE protects, never the
    # peer's apparent dirty state. peers() PID-probes each entry itself, so
    # a dead window's chip is not protected. Best-effort union: a peer
    # record with a missing/invalid chip_path contributes nothing, and a
    # registry read failure must never block GC of genuinely-orphaned
    # copies (raising here would make keep_fn skip-to-be-safe EVERY copy).
    try:
        from quam_state_manager.core import instances
        for peer in instances.peers(current_app.instance_path):
            chip_path = peer.chip_path
            if not chip_path or not isinstance(chip_path, str):
                continue
            try:
                # THE key derivation the GC scanner names folders by —
                # never a hand-rolled twin.
                keys.add(working_copy.key_for(chip_path))
            except Exception:   # noqa: BLE001 — malformed path ⇒ protects nothing
                continue
    except Exception:           # noqa: BLE001 — registry trouble ⇒ no peer keys
        logger.debug("GC peer protection: instance registry unreadable",
                     exc_info=True)
    return keys


@bp.route("/api/working-copies/scan")
def api_working_copies_scan():
    """Classify all persisted working copies (content-reads each one)."""
    records = working_copy.scan_working_copies(current_app.instance_path)
    by_status: dict[str, int] = {}
    for rec in records:
        by_status[rec["status"]] = by_status.get(rec["status"], 0) + 1
    return jsonify({
        "total": len(records),
        "by_status": by_status,
        "threshold": _WC_GC_THRESHOLD,
    })


@bp.route("/api/working-copies/gc", methods=["POST"])
def api_working_copies_gc():
    """Delete provably-clean (and broken) working copies.

    Copies with unapplied edits ("dirty") or unprovable legacy copies
    ("unverifiable") are always kept, as are the active/cached contexts'
    copies regardless of status.
    """
    # keep_fn is re-evaluated before EACH deletion: the scan content-reads
    # every copy (seconds with hundreds), during which a copy can become
    # active via a concurrent /load — a load-time snapshot would miss it.
    result = working_copy.gc_working_copies(
        current_app.instance_path, keep_fn=_wc_keys_in_use)
    _wc_count_cache.pop(str(current_app.instance_path), None)  # refresh banner count
    return jsonify(result)


@bp.route("/api/recent-paths")
def api_recent_paths():
    """Last-loaded quam_state path + recents list, for the Load dropdown."""
    data = _load_session()
    return jsonify({
        "last": data.get("last_quam_state_path"),
        "recents": data.get("recent_quam_state_paths", []),
    })


# ======================================================================
# Workspace persistence
# ======================================================================

_workspace_loaded = False
_session_loaded = False
_rehydrated = False
# Phase 5 §2.1 — guards the one-shot startup wiring in
# ``_ensure_workspace_loaded``. Pre-fix, three module-level booleans
# were checked-then-set without any synchronisation; the threaded
# Werkzeug dev server could dispatch two requests at cold-start
# fast enough for both threads to see ``not _flag``, both flip it,
# and both run ``_load_workspace_roots`` / ``_activate_quam`` /
# ``_rehydrate_workspace_from_recents``. The result was a workspace
# tree with duplicate root entries on first refresh. Held for ~ms
# during cold start, ~ns thereafter.
_startup_lock = threading.Lock()

_RECENTS_CAP = 10


def _workspace_roots_file() -> Path:
    return Path(current_app.instance_path) / "workspace_roots.json"


def _save_workspace_roots() -> None:
    """Persist current workspace root folders to disk atomically.

    Writes through :func:`safe_io.atomic_write_json` so a crash mid-write
    cannot leave a half-written file that :func:`_load_workspace_roots`
    would interpret as "no workspace roots at all" (red-team Phase 2
    finding §5.3 — same pattern as the chip-decisions fix). The on-disk
    format is the historical bare JSON array of folder paths; we keep
    it that way so external tooling that reads this file directly is
    unaffected.
    """
    ws = _ws()
    roots = [str(r) for r in ws.root_folders]
    try:
        safe_io.atomic_write_json(_workspace_roots_file(), roots)
    except OSError as exc:
        logging.getLogger(__name__).warning("Could not save workspace roots: %s", exc)


def _load_workspace_roots() -> None:
    """Reload workspace root folders saved from a previous session.

    Tolerates both the historical bare-list format and the newer
    ``{"roots": [...]}`` envelope (introduced when the writer switched to
    :func:`safe_io.atomic_write_json` for crash-safety).
    """
    p = _workspace_roots_file()
    if not p.exists():
        return
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
        if isinstance(raw, dict):
            roots = raw.get("roots") or []
        elif isinstance(raw, list):
            roots = raw  # legacy format
        else:
            roots = []
        ws = _ws()
        existing = {str(r) for r in ws.root_folders}
        for root in roots:
            if Path(root).is_dir() and root not in existing:
                ws.add_root(root)
    except Exception as exc:
        logging.getLogger(__name__).warning("Could not restore workspace roots: %s", exc)


# ----------------------------------------------------------------------
# Per-project dataset roots (docs/63 decision 5).
#
# ``instance/project_dataset_roots.json`` — ``{project: [resolved root paths]}``.
# A SEPARATE file from workspace_roots.json on purpose: that writer serializes
# only the historical bare array (an envelope would be silently wiped on the
# next save) and the bare-list format is an external contract. The map is a
# memo of "which data roots belong to which qualibrate project" used ONLY to
# seed the Datasets folder filter / Trends folder pick — never to hide data.
# ----------------------------------------------------------------------


def _project_roots_file() -> Path:
    return Path(current_app.instance_path) / "project_dataset_roots.json"


def _load_project_roots() -> dict[str, list[str]]:
    p = _project_roots_file()
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except Exception as exc:  # noqa: BLE001 — a corrupt memo must never break a page
        logging.getLogger(__name__).warning(
            "Could not read project dataset roots: %s", exc)
        return {}
    if not isinstance(raw, dict):
        return {}
    return {str(k): [str(r) for r in v]
            for k, v in raw.items() if isinstance(v, list)}


def _pending_roots_file() -> Path:
    return Path(current_app.instance_path) / "project_roots_pending.json"


def _load_pending_roots() -> dict:
    try:
        raw = json.loads(_pending_roots_file().read_text(encoding="utf-8"))
        return raw if isinstance(raw, dict) else {}
    except FileNotFoundError:
        return {}
    except Exception:  # noqa: BLE001 — a corrupt memo must never break a page
        return {}


def _save_pending_roots(data: dict) -> None:
    try:
        safe_io.atomic_write_json(_pending_roots_file(), data)
    except OSError as exc:
        logger.warning("Could not save pending project roots: %s", exc)


def _record_project_roots(project: str, roots: list[str]) -> None:
    """Merge *roots* into the project's recorded list (fs_key-dedup, atomic).

    Called by /qualibrate/open with ALL of the project's found roots —
    including ones already registered in the workspace — so a root added in
    an earlier session still scopes. A recorded root that later vanishes
    from the workspace simply stops intersecting the present folder set.

    r16 ③: when the project ALREADY has recorded roots and a genuinely-new
    fs_key arrives (the user changed/added the state's data path), the new
    root is NOT silently merged — it goes to the pending file and the
    dataset-roots banner asks "only the new path, or both?". First-time
    recording (no prior roots) stays automatic; a per-project decline memo
    stops the asking permanently (keeps today's merge behavior).
    """
    if not project or not roots:
        return
    data = _load_project_roots()
    cur = data.get(project, [])
    have = {path_match.fs_key(r) for r in cur}
    fresh: list[str] = []
    for r in roots:
        resolved = str(Path(r).resolve())
        if path_match.fs_key(resolved) not in have:
            fresh.append(resolved)
    if not fresh:
        return
    if cur:
        pend = _load_pending_roots()
        entry = pend.get(project)
        if isinstance(entry, dict) and entry.get("declined"):
            pass                                   # user said "stop asking" — merge
        else:
            known = set((entry or {}).get("new", [])) if isinstance(entry, dict) else set()
            merged_new = sorted(known | set(fresh))
            if set(merged_new) != known:
                pend[project] = {"new": merged_new}
                _save_pending_roots(pend)
            return                                  # ask before scoping (r16 ③)
    for resolved in fresh:
        cur.append(resolved)
        have.add(path_match.fs_key(resolved))
    data[project] = cur
    try:
        safe_io.atomic_write_json(_project_roots_file(), data)
    except OSError as exc:
        logger.warning("Could not save project dataset roots: %s", exc)


def _dataset_roots_ask(ctx: dict | None) -> dict | None:
    """Banner payload for the pending dataset-roots question (r16 ③): the
    active scope project has recorded roots AND a newly-declared data path
    awaiting the user's "only new / both" choice. Cheap: one stat when no
    pending file exists (the common case)."""
    project = (ctx or {}).get("qualibrate_project")
    if not project:
        return None
    try:
        if not _pending_roots_file().exists():
            return None
    except OSError:
        return None
    entry = _load_pending_roots().get(project)
    if not isinstance(entry, dict) or entry.get("declined"):
        return None
    new = [r for r in entry.get("new", []) if isinstance(r, str)]
    if not new:
        return None
    return {"project": project, "new": new,
            "current": _load_project_roots().get(project, [])}


@bp.route("/project-roots/confirm", methods=["POST"])
def project_roots_confirm():
    """Resolve the pending dataset-roots question (r16 ③).

    ``choice``: ``both`` merges the new roots into the project's record
    (the old always-behavior); ``new_only`` REPLACES the record with the new
    roots (the old folder stays in the workspace / under "All" — only the
    project's lens narrows); ``decline`` memoizes "stop asking" and keeps
    merging silently from then on.
    """
    project = (request.form.get("project") or "").strip()
    choice = (request.form.get("choice") or "").strip()
    pend = _load_pending_roots()
    entry = pend.get(project)
    new = [r for r in (entry or {}).get("new", [])] if isinstance(entry, dict) else []
    if choice == "decline":
        pend[project] = {"declined": True}
        _save_pending_roots(pend)
        if new:                       # declining = keep today's merge behavior
            _record_project_roots(project, new)
    elif choice in ("both", "new_only") and project:
        data = _load_project_roots()
        if choice == "both":
            merged = data.get(project, [])
            have = {path_match.fs_key(r) for r in merged}
            for r in new:
                if path_match.fs_key(r) not in have:
                    merged.append(r)
                    have.add(path_match.fs_key(r))
            data[project] = merged
        else:
            data[project] = list(new)
        try:
            safe_io.atomic_write_json(_project_roots_file(), data)
        except OSError as exc:
            logger.warning("Could not save project dataset roots: %s", exc)
        pend.pop(project, None)
        _save_pending_roots(pend)
        current_app.config.pop("dataset_store", None)   # re-seed the lens
    return render_template("_dataset_roots_banner.html",
                           dataset_roots_ask=_dataset_roots_ask(_active_ctx()))


def _strip_project_root(folder: str) -> None:
    """Remove *folder* from EVERY project's recorded roots (workspace_remove:
    an explicitly removed folder must not keep seeding project scopes)."""
    if not folder:
        return
    data = _load_project_roots()
    if not data:
        return
    k = path_match.fs_key(str(Path(folder).resolve()))
    out: dict[str, list[str]] = {}
    changed = False
    for name, roots in data.items():
        kept = [r for r in roots if path_match.fs_key(r) != k]
        if len(kept) != len(roots):
            changed = True
        if kept:
            out[name] = kept
        else:
            changed = True  # drop the now-empty project entry entirely
    if not changed:
        return
    try:
        safe_io.atomic_write_json(_project_roots_file(), out)
    except OSError as exc:
        logger.warning("Could not update project dataset roots: %s", exc)


def _scope_folder_keys(scope_project: str | None,
                       folders: list[dict[str, Any]]) -> list[str]:
    """The ``folder_key``s among *folders* recorded for *scope_project*.

    ``folders`` dicts carry ``key`` + ``full_path`` (the /datasets + /trends
    shape). Empty when unscoped / nothing recorded / no intersection — every
    caller treats [] as "behave exactly as today".
    """
    if not scope_project:
        return []
    recorded = _load_project_roots().get(scope_project) or []
    if not recorded:
        return []
    rec_keys = {path_match.fs_key(r) for r in recorded}
    return [f["key"] for f in folders
            if path_match.fs_key(str(f.get("full_path") or "")) in rec_keys]


# ----------------------------------------------------------------------
# Last-session persistence (mirrors the workspace_roots pattern).
# Stores the most recently activated quam_state path plus an LRU list of
# the last N paths so the user can switch between active projects with
# one click after a server restart.
# ----------------------------------------------------------------------


def _session_file() -> Path:
    return Path(current_app.instance_path) / "last_session.json"


def _load_session() -> dict[str, Any]:
    p = _session_file()
    if not p.exists():
        return {}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return {}
        # Hand-editable JSON: a type-corrupt path entry (int, nested list, …)
        # must degrade to absent — GET / and the before_request hook Path()
        # these keys, and a TypeError there takes down every route.
        if not isinstance(data.get("last_quam_state_path"), (str, type(None))):
            data.pop("last_quam_state_path", None)
        recents = data.get("recent_quam_state_paths")
        if isinstance(recents, list):
            data["recent_quam_state_paths"] = [r for r in recents
                                               if isinstance(r, str)]
        elif recents is not None:
            data.pop("recent_quam_state_paths", None)
        return data
    except Exception:
        logging.getLogger(__name__).warning("Could not read last_session.json", exc_info=True)
        return {}


def _save_session(data: dict[str, Any]) -> None:
    """Persist ``last_session.json`` atomically via :mod:`safe_io`.

    Failures are logged but not propagated to callers — most call sites
    are best-effort persistence hooks (recents update, exclusion list,
    etc.). Callers that need to know whether the write succeeded should
    catch :class:`OSError` from :func:`_save_session_raising` instead.
    """
    try:
        safe_io.atomic_write_json(_session_file(), data)
    except OSError as exc:
        logging.getLogger(__name__).warning("Could not save last_session.json: %s", exc)


def _save_session_raising(data: dict[str, Any]) -> None:
    """Like :func:`_save_session` but raises :class:`OSError` on failure.

    Used by routes whose user-facing semantics depend on persistence
    actually happening (e.g. ``workspace_remove`` — see red-team Phase 2
    finding §5.3 — needs to know the exclusion list was recorded so the
    next auto-rehydrate doesn't quietly re-add the folder).
    """
    safe_io.atomic_write_json(_session_file(), data)


def _remember_load_path(path: str | Path) -> None:
    """Update last_session.json after a successful /load."""
    abs_path = str(Path(path).resolve())
    data = _load_session()
    recents = [p for p in data.get("recent_quam_state_paths", []) if p != abs_path]
    recents.insert(0, abs_path)
    data["last_quam_state_path"] = abs_path
    data["recent_quam_state_paths"] = recents[:_RECENTS_CAP]
    _save_session(data)


def _drop_bad_path(path: str | Path) -> None:
    """Remove a path from last_session.json (e.g. folder no longer exists)."""
    abs_path = str(Path(path).resolve())
    data = _load_session()
    if data.get("last_quam_state_path") == abs_path:
        data["last_quam_state_path"] = None
    data["recent_quam_state_paths"] = [
        p for p in data.get("recent_quam_state_paths", []) if p != abs_path
    ]
    _save_session(data)


# ----------------------------------------------------------------------
# Auto-populate Workspace from Loaded paths
# ----------------------------------------------------------------------


def _chip_folder_for(quam_state_path: str | Path) -> Path | None:
    """Return the chip-level folder for a *per-experiment* quam_state path.

    Recognises ``<workspace>/<chip>/<date>/#N_<exp>_HHMMSS/quam_state/``
    and returns the ``<chip>`` folder. Returns ``None`` for any other
    layout (e.g. standalone ``<chip>/quam_state/``) — auto-promotion is
    intentionally limited to the per-experiment workflow because the
    chip folder is unambiguous there.  Standalone loads can still be
    added to the workspace manually.
    """
    from quam_state_manager.core.history import _DATE_PATTERN, _EXPERIMENT_PATTERN
    p = Path(quam_state_path).resolve()
    parent = p.parent
    if (
        _EXPERIMENT_PATTERN.match(parent.name)
        and parent.parent and _DATE_PATTERN.match(parent.parent.name)
        and parent.parent.parent
    ):
        return parent.parent.parent
    return None


def _maybe_auto_add_workspace_root(quam_state_path: str | Path) -> None:
    """Add the chip folder to workspace, unless already present or excluded.

    No-op for non per-experiment paths (see ``_chip_folder_for``).
    """
    chip_folder = _chip_folder_for(quam_state_path)
    if chip_folder is None or not chip_folder.is_dir():
        return
    chip_str = str(chip_folder.resolve())
    # Membership via fs_key on BOTH sides — a case-variant spelling of an
    # already-registered root (or of an excluded folder) must not re-add it
    # on case-insensitive-default hosts. Roots are still STORED as the
    # resolved (unfolded) path.
    chip_key = path_match.fs_key(chip_folder)
    ws = _ws()
    if any(path_match.fs_key(r) == chip_key for r in ws.root_folders):
        return
    excluded = {path_match.fs_key(e)
                for e in _load_session().get("workspace_excluded", [])}
    if chip_key in excluded:
        return
    try:
        ws.add_root(chip_str)
        _save_workspace_roots()
        # Invalidate dataset store so it rebuilds with the new root
        current_app.config.pop("dataset_store", None)
    except Exception:
        logger.warning("Auto-add workspace root failed for %s", chip_str, exc_info=True)


def _rehydrate_workspace_from_recents() -> None:
    """One-time: derive chip folders from recent loaded paths and auto-add
    any that aren't already roots and aren't excluded.

    Skips paths whose folders no longer exist (pytest leftovers, moved
    folders, etc.). Runs once per app start via the ``@before_request``
    hook.
    """
    data = _load_session()
    recents: list[str] = data.get("recent_quam_state_paths", [])
    if not recents:
        return
    ws = _ws()
    # fs_key on both sides — see _maybe_auto_add_workspace_root.
    existing = {path_match.fs_key(r) for r in ws.root_folders}
    excluded = {path_match.fs_key(e)
                for e in data.get("workspace_excluded", [])}
    added: list[str] = []
    for rp in recents:
        try:
            qs_path = Path(rp)
            if not qs_path.exists():
                continue
            chip_folder = _chip_folder_for(qs_path)
            if chip_folder is None or not chip_folder.is_dir():
                continue
            chip_str = str(chip_folder.resolve())
            chip_key = path_match.fs_key(chip_folder)
            if chip_key in existing or chip_key in excluded:
                continue
            ws.add_root(chip_str)
            existing.add(chip_key)
            added.append(chip_str)
        except Exception:
            continue
    if added:
        _save_workspace_roots()
        current_app.config.pop("dataset_store", None)
        logger.info(
            "Rehydrated %d workspace root(s) from recent loads: %s",
            len(added), added,
        )


@bp.before_request
def _ensure_workspace_loaded() -> None:
    """One-shot startup wiring: workspace roots, last session, rehydration.

    Phase 5 §2.1: held under ``_startup_lock`` so two concurrent
    first-requests can't both pass the boolean-flag check and both run
    the (non-idempotent) startup body. After the first request, the
    fast path is a single lock acquire + three boolean checks ≈ ns.
    """
    global _workspace_loaded, _session_loaded, _rehydrated

    # Fast path — all three flags set means startup is done. Cheap
    # check before grabbing the lock so steady-state requests pay
    # close to zero.
    if _workspace_loaded and _session_loaded and _rehydrated:
        return

    with _startup_lock:
        if not _workspace_loaded:
            _workspace_loaded = True
            _load_workspace_roots()

        if not _session_loaded:
            _session_loaded = True
            # docs/63 decision 3: startup NEVER auto-activates the last chip —
            # SM lands on the clean Projects page and the last chip/project
            # are one-click "Resume"/"Continue" cards there instead. What
            # remains of the old restore is the hygiene: prune a vanished
            # last path from the session so recents and the landing never
            # offer a dead folder. (A transient read error can't happen any
            # more — nothing is read beyond the two existence stats.)
            data = _load_session()
            last = data.get("last_quam_state_path")
            if last:
                last_path = Path(last)
                if not (last_path.is_dir() and (last_path / "state.json").exists()):
                    _drop_bad_path(last)

        if not _rehydrated:
            _rehydrated = True
            try:
                _rehydrate_workspace_from_recents()
            except Exception:
                logger.warning("Workspace rehydration failed", exc_info=True)


# ======================================================================
# Dataset browsing
# ======================================================================


_DATASET_STORE_LRU_MAX = 32
# Raised from 5 → 32 for multi-folder Datasets: the merged table + poll keep a
# live DatasetStore for EVERY active workspace data folder simultaneously (see
# ``_active_dataset_stores``). The cap must stay >= the number of registered
# data roots or the build loop would evict a store it just created and force a
# cold re-scan when a later uid resolves back to it.
# Phase 5 §2.2 — guards the get-or-create on the per-app
# ``dataset_store_lru`` config slot. Pre-fix, two concurrent
# ``/datasets`` requests on a fresh process could both see
# ``current_app.config.get(...)`` return None, both build an empty
# ``OrderedDict``, and one would overwrite the other. The loser's
# DatasetStore was effectively orphaned. Held only on the first
# request, then it's a fast cache hit forever after.
_dataset_lru_lock = threading.Lock()


def _dataset_store_lru() -> OrderedDict[Path, DatasetStore]:
    """LRU cache (``_DATASET_STORE_LRU_MAX`` = 32) of DatasetStore instances
    keyed by data folder.

    Survives invalidation of the active-pointer slot ``dataset_store``,
    so revisiting the same workspace after a rescan or workspace toggle
    skips the full cold scan. Reusing a cached store also lets
    ``rescan_if_stale`` run incrementally instead of re-parsing every
    folder from disk.

    Thread-safe (Phase 5 §2.2) — the first-call get-or-create runs
    under ``_dataset_lru_lock`` so two concurrent requests on a fresh
    app don't each create their own OrderedDict.
    """
    from collections import OrderedDict
    lru = current_app.config.get("dataset_store_lru")
    if lru is not None:
        return lru
    with _dataset_lru_lock:
        lru = current_app.config.get("dataset_store_lru")
        if lru is None:
            lru = OrderedDict()
            current_app.config["dataset_store_lru"] = lru
    return lru


def _get_or_create_store(folder: Path, rescan: bool = True) -> DatasetStore | None:
    """Return a cached DatasetStore for ``folder``, building it if needed.

    Cached stores have their incremental rescan triggered before return UNLESS
    ``rescan=False``. The per-run detail/figure/h5 routes open an ALREADY-LISTED
    run, which only needs ``store.get_run`` — forcing a full ``rescan_if_stale``
    there stalls every click behind a lock-held ``_scan`` while an experiment is
    actively writing (the run TABLE is kept fresh by the delta poll + the /datasets
    render, not by opening a row). See the datasets-dead-clicks root-cause notes.
    """
    lru = _dataset_store_lru()
    cached = lru.get(folder)
    if cached is not None:
        lru.move_to_end(folder)
        if rescan:
            try:
                cached.rescan_if_stale()
            except Exception:
                logger.exception("rescan_if_stale failed for %s", folder)
        return cached
    try:
        ds = DatasetStore(folder)
    except Exception:
        return None
    lru[folder] = ds
    lru.move_to_end(folder)
    while len(lru) > _DATASET_STORE_LRU_MAX:
        lru.popitem(last=False)
    return ds


def _dataset_store() -> DatasetStore | None:
    """Get or auto-create DatasetStore from workspace roots.

    Tries all candidate data folders and picks the one with the most runs.
    Never caches a store that found 0 runs — returns None instead so the
    caller can show a helpful "add workspace" message.
    """
    ds = current_app.config.get("dataset_store")
    if ds:
        # Active store known: refresh it incrementally and return.
        try:
            ds.rescan_if_stale()
        except Exception:
            logger.exception("rescan_if_stale failed for active dataset store")
        return ds

    ws = current_app.config.get("workspace")
    if not ws:
        return None

    # Build candidates: workspace root_folders + entries' grandparent folder.
    # entry.folder_path is the run folder (e.g. .../2026-03-03/#9973_.../),
    # so folder_path.parent.parent is the data root (e.g. .../1Q_ExampleChip/).
    candidates: set[Path] = set()
    for root in ws.root_folders:
        candidates.add(root)
    for entry in ws.all_entries:
        if entry.is_standalone:
            continue
        candidate = entry.folder_path.parent.parent
        if candidate.is_dir():
            candidates.add(candidate)

    if not candidates:
        return None

    # Try each candidate (LRU-cached so rebuilds after invalidation are cheap);
    # keep the one with the most runs.
    best_ds: DatasetStore | None = None
    best_count = 0
    for candidate in sorted(candidates):
        test_ds = _get_or_create_store(candidate)
        if test_ds is None:
            continue
        if test_ds.run_count > best_count:
            best_ds = test_ds
            best_count = test_ds.run_count

    if not best_ds or best_ds.run_count == 0:
        return None  # Don't cache empty stores; caller shows "add workspace" message

    current_app.config["dataset_store"] = best_ds
    return best_ds


# ──────────────────────────────────────────────────────────────────────
# Multi-folder Datasets — composite run identity (uid) + aggregator
#
# ``run_id`` (parsed from the run-folder name, e.g. ``#250``) is unique only
# WITHIN one data folder; two registered folders can each hold a ``#250``. So
# the merged Datasets table, its detail/figure/h5/tag routes, and the JS state
# all key on an opaque uid ``"<folder_key>:<run_id>"`` where ``folder_key`` is a
# short stable hash of the registered folder's resolved path (survives
# workspace reordering; no URL-encoding hazard from folder names).
# ──────────────────────────────────────────────────────────────────────


def _folder_key(path: str | Path) -> str:
    """Stable short identity for a registered data folder (hash of its
    resolved path). Used as the ``folder_key`` half of a dataset uid."""
    resolved = str(Path(path).resolve())
    return hashlib.sha1(resolved.encode("utf-8")).hexdigest()[:8]


def _dataset_uid(folder_key: str, run_id: int) -> str:
    return f"{folder_key}:{run_id}"


def _split_dataset_uid(uid: str) -> tuple[str, int] | None:
    """``"a1b2c3d4:250"`` → ``("a1b2c3d4", 250)``; ``None`` if malformed.

    ``folder_key`` is hex (no ``:``) so a single ``partition`` is unambiguous.
    """
    if not uid or ":" not in uid:
        return None
    folder_key, _, rid = uid.partition(":")
    if not folder_key:
        return None
    try:
        return folder_key, int(rid)
    except ValueError:
        return None


# Memoization for ``_dataset_candidate_folders`` (finding B22). The candidate
# set is an ~17-folder result recomputed from an O(runs) ``is_dir()`` stat storm,
# yet ``_active_dataset_stores`` calls it twice on every /datasets render AND
# every /datasets/changes-since poll (~every 60s). On a 10k-run workspace that's
# ~10k stats to rebuild an unchanging set, forever. We cache the computed set as
# ``(workspace_token, ws.version, list)``. The non-fast path validates the token
# (a shallow-stat that flips on any layout change); the fast path (the polls)
# skips the stat-walk and validates the cheap monotonic ``ws.version`` instead,
# which the scanner bumps whenever it discovers a new root/chip/date dir — so a
# fast poll STILL surfaces a brand-new chip dir once the sidebar scan picks it up,
# without paying the token walk each poll. The stat-heavy rebuild only runs on a
# miss; we never hold the lock across the rebuild.
# Wall-clock budget for ONE /datasets/changes-since poll (docs/80). A rescan
# runs while other writers land runs, so its cost is not ours to bound; what we
# CAN bound is how long the monitoring request may take before answering. Past
# the budget the remaining folders are left un-scanned with their cursors held,
# so nothing is skipped — it is re-offered on the next poll (the client comes
# back in ~1.2s on `partial`). The budget therefore trades chattiness, never
# correctness, which is why it can afford to be generous.
#
# INVARIANT: this must stay well below the client's abort
# (POLL_TIMEOUT_MS in dataset-virtual.js, currently 20s). The two have
# different jobs — the budget means "answer by now", the abort means
# "something is wrong" — and an aborted request delivers NEITHER a cursor nor
# the partial flag, i.e. exactly the silent-stall this whole mechanism exists
# to prevent. Keeping the abort at >2x the budget means it only ever fires on
# a genuinely hung request, never on a merely slow scan.
_POLL_BUDGET_S = 8.0

# docs/105 #5: the explicit Rescan button gets a larger — but still bounded —
# budget: it is a user-initiated "check now", so it may work harder than a
# poll tick, but it must never hold the scan lock for an entire 10k-run
# re-parse. A truncated re-check continues through the ordinary poll.
_RESCAN_BUDGET_S = 20.0

_dataset_candidates_lock = threading.Lock()
_dataset_candidates_cache: dict[Any, tuple[Any, int, list[Path]]] = {}


def _dataset_candidate_folders(*, fast: bool = False) -> list[Path]:
    """Sorted, deduped, existing data-root folders for the current workspace.

    Mirrors the candidate-building in ``_dataset_store`` (workspace roots +
    each non-standalone entry's grandparent), shared by the multi-folder
    helpers below.

    Memoized on a workspace token (finding B22): the result is rebuilt only
    when the workspace layout changes, never on every poll.

    ``fast=True`` skips recomputing the validator token — a directory stat-walk
    that costs ~1ms/stat × ~900 stats on a 9p (WSL2→Windows) workspace (~1.2s),
    i.e. ~1000× more than the rebuild it guards, and it used to run on EVERY
    per-run click (the measured ~900ms run-detail latency) and every ~60s poll.
    It instead validates the cheap monotonic ``ws.version`` (bumped by the scanner
    when a root/chip/date dir appears), so a fast caller still rebuilds — and
    discovers a new candidate dir — when the workspace tree actually changes."""
    ws = current_app.config.get("workspace")
    if not ws:
        return []
    if fast:
        cached = _dataset_candidates_cache.get(id(current_app._get_current_object()))
        if cached is not None and cached[1] == ws.version:
            return list(cached[2])
        # Miss (empty, or the scanner discovered a new dir → version moved) —
        # fall through to the token-validated rebuild.
    # Cheap-on-ext4, EXPENSIVE-on-9p token — flips when the workspace layout
    # changes (mirrors ``HistoryManager``'s alignment cache). Outside the lock.
    try:
        token = HistoryManager._workspace_token(ws)
    except Exception:
        token = None
    # One cache slot per app (keyed by app identity so multiple apps in-process
    # don't collide); the token guards staleness.
    app_key = id(current_app._get_current_object())
    if token is not None:
        cached = _dataset_candidates_cache.get(app_key)
        if cached is not None and cached[0] == token and cached[1] == ws.version:
            return list(cached[2])
    candidates: set[Path] = set()
    for root in ws.root_folders:
        candidates.add(Path(root))
    for entry in ws.all_entries:
        if entry.is_standalone:
            continue
        cand = entry.folder_path.parent.parent
        if cand.is_dir():
            candidates.add(cand)
    result = sorted(candidates)
    if token is not None:
        with _dataset_candidates_lock:
            _dataset_candidates_cache[app_key] = (token, ws.version, list(result))
    return result


def _active_dataset_stores(*, fast: bool = False) -> list[dict[str, Any]]:
    """Every workspace data folder that yielded >=1 run, each as
    ``{"key", "path", "label", "store"}``.

    Replaces the single "most-runs winner" pick (``_dataset_store``) for the
    merged Datasets table, the delta-poll, and the new-run poll. Stores are
    LRU-cached (``_get_or_create_store``) so repeat calls are cheap; the LRU
    cap is sized to hold them all at once (see ``_DATASET_STORE_LRU_MAX``).

    ``fast=True`` (the periodic polls) reuses the cached candidate-folder list
    instead of recomputing the expensive workspace-token stat-walk (~0.3-1s on
    9p) every 60 s — a poll only needs the already-known folder set, and
    workspace mutations invalidate the candidates cache explicitly. The per-store
    ``rescan_if_stale`` below still runs, so new runs are still detected.
    """
    result: list[dict[str, Any]] = []
    seen_keys: set[str] = set()
    for cand in _dataset_candidate_folders(fast=fast):
        store = _get_or_create_store(cand)
        if store is None or store.run_count == 0:
            continue
        key = _folder_key(cand)
        if key in seen_keys:  # two candidates resolving to the same real path
            continue
        seen_keys.add(key)
        result.append({"key": key, "path": str(cand), "label": cand.name, "store": store})
    # Honor a directly-injected/legacy active store (set by ``_dataset_store`` or
    # tests) even when its folder isn't a workspace root — keeps single-folder
    # flows and existing fixtures working under the multi-folder aggregator.
    injected = current_app.config.get("dataset_store")
    inj_path = getattr(injected, "folder_path", None) if injected is not None else None
    if inj_path is not None and injected.run_count > 0:
        key = _folder_key(inj_path)
        if key not in seen_keys:
            seen_keys.add(key)
            result.append({"key": key, "path": str(inj_path),
                           "label": inj_path.name, "store": injected})
    return result


def _store_for_folder_key(folder_key: str, rescan: bool = True,
                          run_id: int | None = None) -> tuple[DatasetStore | None, str | None]:
    """Resolve one folder_key → (store, leaf-label) WITHOUT instantiating the
    other folders' stores (hashing candidate paths is I/O-free). Used by the
    per-run routes so a figure/h5 request rescans only its own folder.
    ``rescan=False`` skips the (lock-held) incremental rescan on the cached store.
    ``run_id`` (when given) gates the single-folder drift fallback below."""
    # FAST path first: resolve against the cached candidate list without the
    # expensive workspace-token validation (the ~1.2s stat-walk on 9p that made
    # every run click ~900ms). A clicked row's folder is already in the cached
    # list; only a genuine miss (brand-new folder / stale cache) pays for the
    # validated rebuild below.
    cands = list(_dataset_candidate_folders(fast=True))
    for cand in cands:
        if _folder_key(cand) == folder_key:
            return _get_or_create_store(cand, rescan=rescan), cand.name
    validated = list(_dataset_candidate_folders())
    for cand in validated:
        if cand in cands:
            continue   # already checked above
        if _folder_key(cand) == folder_key:
            return _get_or_create_store(cand, rescan=rescan), cand.name
    # Fall back to a directly-injected/legacy active store (see _active_dataset_stores).
    injected = current_app.config.get("dataset_store")
    inj_path = getattr(injected, "folder_path", None) if injected is not None else None
    if inj_path is not None and _folder_key(inj_path) == folder_key:
        return injected, inj_path.name
    # folder_key DRIFT fallback: the folder may have been renamed/moved on disk since the
    # row was rendered, so its sha1(resolved-path) key no longer matches any candidate and
    # an already-listed row would 404 silently. On a SINGLE-folder workspace there is no
    # other folder to confuse it with — use the lone candidate. GUARD: only when that
    # candidate ACTUALLY holds run_id (after a freshness rescan), so a drifted key whose
    # run doesn't exist 404s honestly instead of resolving to some unrelated run. (A
    # different single folder that merely reuses the same numeric run_id is an accepted,
    # logged residual — it needs a mid-session multi→single transition AND a colliding id;
    # see audit 2026-06-26.)
    if len(cands) == 1:
        store = _get_or_create_store(cands[0], rescan=rescan)
        if store is None:
            return None, None
        if run_id is None:
            return store, cands[0].name
        if store.get_run(run_id) is None:
            try:
                store.rescan_if_stale()
            except Exception:
                logger.exception("rescan_if_stale on single-folder drift fallback failed")
        if store.get_run(run_id) is not None:
            logger.warning(
                "dataset folder_key %s matched no candidate; resolved to the lone folder "
                "%s (run %s) via single-folder drift fallback", folder_key, cands[0].name, run_id)
            return store, cands[0].name
    return None, None


def _resolve_run(uid: str, rescan: bool = False) -> tuple[DatasetStore, int, str] | None:
    """uid → ``(store, run_id, folder_label)`` or ``None`` (malformed uid or
    folder no longer in the workspace → caller returns 404).

    ``rescan`` defaults to FALSE: every caller is a per-run route opening an
    already-listed run, so the row is already in ``store.runs`` and a full
    ``rescan_if_stale`` is pure latency on the click path (and serialises behind
    a lock-held ``_scan`` during an active experiment). A genuinely-new run that
    isn't in the store yet is recovered by the rescan-on-miss fallback in the
    caller (see ``dataset_detail``)."""
    parsed = _split_dataset_uid(uid)
    if not parsed:
        return None
    folder_key, run_id = parsed
    store, label = _store_for_folder_key(folder_key, rescan=rescan, run_id=run_id)
    if store is None:
        return None
    return store, run_id, label or ""


@bp.app_template_global("ds_entry_uid")
def _ds_entry_uid(entry: Any) -> str:
    """Jinja global: the dataset uid for a workspace tree entry (a run), or "".

    A run's DatasetStore folder is the run folder's grandparent (mirrors
    ``_active_dataset_stores``), so the uid is ``folder_key(grandparent):run_id``.
    This keeps the sidebar tree's open/highlight link in sync with the merged
    Datasets table (both resolve a run by the same uid)."""
    rid = getattr(entry, "run_id", None)
    if rid is None:
        return ""
    folder = getattr(entry, "folder_path", None)
    if folder is None:
        return ""
    try:
        return _dataset_uid(_folder_key(Path(folder).parent.parent), rid)
    except Exception:
        return ""


def _folder_fingerprint(store: DatasetStore):
    """ChipFingerprint of a representative quam_state run in this folder, or None.

    Reuses ``history.fingerprint_of`` (network host/cluster + qubit/pair labels).
    """
    from quam_state_manager.core.history import fingerprint_of
    for run in store.runs_snapshot():
        if getattr(run, "has_quam_state", False):
            qs = store.get_quam_state_path(run.run_id)
            if qs:
                fp = fingerprint_of(qs)
                if fp is not None:
                    return fp
    return None


def _folders_same_chip(folders: list[dict[str, Any]]) -> str:
    """``"same"`` | ``"different"`` — whether the selected data folders hold the
    same physical chip. Combining trends across DIFFERENT chips is meaningless
    (chip A's T1 next to chip B's), so Trends only merges same-chip folders.

    Uses the chip fingerprint (network + qubit/pair labels via ``history.align``);
    falls back to comparing the qubit-name set when no folder has a quam_state to
    fingerprint."""
    from quam_state_manager.core.history import align
    fps = [_folder_fingerprint(f["store"]) for f in folders]
    if all(fp is not None for fp in fps):
        base = fps[0]
        for other in fps[1:]:
            if align(base, other) in ("different_chip", "unknown"):
                return "different"
        return "same"
    # Fallback: same chip ⇒ identical (non-empty) qubit-name sets.
    qsets = [frozenset(f["store"].summary_stats.get("unique_qubits", [])) for f in folders]
    if not qsets[0]:
        return "different"
    return "same" if all(q == qsets[0] for q in qsets[1:]) else "different"


@bp.route("/datasets")
def datasets():
    """Browse experiment runs — auto-discovered from workspace roots.

    The table body is rendered client-side by web/static/dataset-virtual.js,
    which reads the compact JSON payload embedded in the page.
    """
    return _datasets_view("datasets")


@bp.route("/collections")
def collections():
    """Curated-datasets hub: only runs that carry >=1 tag, with a tag-filter
    chip row (+ the reused experiment-filter chips). Reuses the entire Datasets
    backbone — same template + virtual scroller — with the rows pre-filtered to
    tagged runs. "Favorite" (the ⭐) is just a pinned tag here.
    """
    return _datasets_view("collections")


def _datasets_view(view_mode: str):
    """Shared renderer for the Datasets and Collections pages (parameterized).

    ``view_mode`` is 'datasets' (all runs) or 'collections' (only tagged runs +
    tag-filter chips). Everything else — store lookup, payload, exp chips, date
    tabs, virtual table, compare bar — is identical and reused.
    """
    from quam_state_manager.core.dataset import FAVORITE_TAG
    from quam_state_manager.core.fit_targets import curated_fit_keys as _curated_fit_keys

    is_collections = view_mode == "collections"
    page = "collections" if is_collections else "datasets"
    # Multi-folder: the table merges runs from EVERY active data folder, not the
    # single "most-runs" winner. Each row is tagged with its folder_key ("f") so
    # the client can build a uid ("<f>:<id>") and the folder filter badges.
    active = _active_dataset_stores()
    if _is_htmx():
        template = "_datasets.html"
    else:
        template = "collections.html" if is_collections else "datasets.html"
    if not active:
        return render_template(template, **_ctx(page=page),
                               rows_json="[]", initial_poll_ts=0, total=0,
                               active_folder="", folders=[], folders_json="[]",
                               no_workspace=True, curated_keys_json="[]",
                               view_mode=view_mode, collection_tags=[])
    import time as _t
    poll_ts = _t.time()
    date = request.args.get("date")

    rows: list[dict] = []
    folders: list[dict] = []
    experiments_set: set[str] = set()
    dates_set: set[str] = set()
    tags_set: set[str] = set()
    qubits_set: set[str] = set()
    cat_map: dict[str, set[str]] = {}
    total = 0
    for fol in active:
        store = fol["store"]
        folders.append({"key": fol["key"], "label": fol["label"], "full_path": fol["path"]})
        for row in store.list_runs_compact(date=date):
            row["f"] = fol["key"]   # _compact_row returns a fresh dict — safe to tag
            rows.append(row)
        experiments_set.update(store.experiment_types)
        dates_set.update(store.dates)
        tags_set.update(store.list_all_tags())
        total += store.run_count
        qubits_set.update(store.summary_stats.get("unique_qubits", []))
        for cat in store.categorize_experiments():
            cat_map.setdefault(cat["label"], set()).update(cat["experiments"])

    # Newest-first by run timestamp — run_id isn't comparable across folders.
    rows.sort(key=lambda r: (r.get("date") or "", r.get("time") or "", r.get("id") or 0),
              reverse=True)

    all_tags = sorted(tags_set)
    collection_tags: list[str] = []
    if is_collections:
        # Only runs that carry >=1 tag belong in Collections.
        rows = [r for r in rows if r.get("tags")]
        # Tag-filter chips: every tag, with the reserved favorite pinned first.
        rest = [t for t in all_tags if t != FAVORITE_TAG]
        collection_tags = ([FAVORITE_TAG] if FAVORITE_TAG in all_tags else []) + rest

    experiments = sorted(experiments_set)
    dates = sorted(dates_set, reverse=True)
    # Merge per-folder experiment categories preserving the canonical order.
    _CANON = ["Readout", "2Q", "Coupler", "Qubit Flux", "1Q", "Other"]
    exp_categories = [{"label": lbl, "experiments": sorted(cat_map[lbl])}
                      for lbl in _CANON if cat_map.get(lbl)]
    for lbl in cat_map:  # defensive: any non-canonical label
        if lbl not in _CANON:
            exp_categories.append({"label": lbl, "experiments": sorted(cat_map[lbl])})
    stats = {
        "total_runs": total,
        "date_range": f"{dates[-1]} - {dates[0]}" if dates else "",
        "experiment_types": len(experiments),
        "unique_qubits": sorted(qubits_set),
    }
    # At-a-glance digest of the LATEST day (the PI/skim persona: "what happened
    # last night" without clicking 50 runs). Computed over the already-built
    # rows; chips click-to-filter via the existing scoped search (is:failed /
    # outcome:failed). Skipped on Collections (curated view, different intent).
    digest = None
    if rows and not is_collections:
        latest_day = rows[0].get("date") or ""
        day_rows = [r for r in rows if r.get("date") == latest_day]
        import re as _re
        _bad = _re.compile(r"error|fail|abort|crash")
        failed_rows = [r for r in day_rows
                       if _bad.search(str(r.get("status") or "").lower())]
        qubit_fail: dict[str, int] = {}
        for r in day_rows:
            for q, oc in (r.get("oc") or {}).items():
                if _bad.search(str(oc).lower()):
                    qubit_fail[q] = qubit_fail.get(q, 0) + 1
        digest = {
            "date": latest_day,
            "total": len(day_rows),
            "failed": len(failed_rows),
            "qubit_fail": sorted(qubit_fail.items(), key=lambda kv: (-kv[1], kv[0]))[:8],
        }
    # Folder-set signature for dataset-virtual.js: when the active-folder SET
    # changes (folder added/removed), the client clears its compare-checkbox
    # selection because uids from a dropped folder are meaningless. Order-
    # independent so a mere reordering doesn't wipe the selection. Replaces the
    # old single ``str(ds.folder_path)`` stamp (see _persistedFolder in
    # dataset-virtual.js).
    folder_sig = ",".join(sorted(f["key"] for f in folders))

    # Project lens (docs/63): which of the present folders are recorded for
    # the active context's project scope. Seeds the client's folder filter
    # only — rows, uids and the folder_key contract are untouched, and the
    # All chip always escapes. scope=None → [] → byte-identical behavior.
    scope_project = (_active_ctx() or {}).get("qualibrate_project") or ""
    scope_keys = _scope_folder_keys(scope_project, folders)

    return render_template(
        template,
        **_ctx(page=page),
        scope_project=scope_project,
        scope_keys=scope_keys,
        scope_keys_json=json.dumps(scope_keys, separators=(",", ":")),
        view_mode=view_mode,
        collection_tags=collection_tags,
        digest=digest,
        rows_json=json.dumps(rows, separators=(",", ":")),
        # Curated fit-key order (from FIT_TARGET_MAP) for the Sort banner's
        # Fit-metrics group — the client builds the key union + counts from each
        # row's `sm` map (so it stays correct after delta-poll merges) and floats
        # these curated metrics first, then the rest A–Z.
        curated_keys_json=json.dumps(_curated_fit_keys(), separators=(",", ":")),
        initial_poll_ts=poll_ts,
        active_folder=folder_sig,
        # Active data folders → folder filter badges + per-row folder chip
        # lookups (folder_key → label/full_path) in dataset-virtual.js.
        folders=folders,
        folders_json=json.dumps(folders, separators=(",", ":")),
        total=total,
        experiments=experiments,
        exp_categories=exp_categories,
        dates=dates,
        stats=stats,
        all_tags=all_tags,
        active_date=date,
    )


@bp.route("/datasets/changes-since")
def datasets_changes_since():
    """Delta poll endpoint for the dataset table.

    Returns rows added/updated since ``ts`` (Unix seconds, float) plus a
    list of run_ids that have vanished. Intended for the JS auto-poller in
    web/static/dataset-virtual.js — replaces the previous full-table refetch.
    """
    # Aggregate the per-folder deltas: tag each updated row with its folder_key
    # and namespace the vanished list by uid (so a #250-vanishes-in-A +
    # #250-appears-in-B never reads as a single run resurrecting).
    try:
        ts = float(request.args.get("ts", 0))
    except (TypeError, ValueError):
        ts = 0.0
    try:
        active = _active_dataset_stores(fast=True)  # 60s poll — skip the token stat-walk
    except Exception:
        # Never 500 the monitoring poll: hold the cursor and try again next
        # tick. A 500 here is invisible to the user (the client catches it)
        # but stops the table updating for good (docs/80).
        logger.exception("changes-since: could not resolve active data folders")
        return jsonify({"updated": [], "vanished": [], "now": ts,
                        "partial": True, "skipped": 0})
    if not active:
        return jsonify({"updated": [], "vanished": [], "now": 0,
                        "partial": False, "skipped": 0})
    date = request.args.get("date") or None
    updated: list[dict] = []
    vanished: list[str] = []
    # r13 audit D7: the single client cursor must be the MINIMUM of the
    # per-folder samples, not the max. Folders are polled sequentially and
    # each stamps its own `now` at the END of its changes_since — with max,
    # the next poll queried folder A with a ts LATER than A's own sample, so
    # any run stamped into A during the loop window (a concurrent
    # /datasets/poll or page render also rescans) was skipped forever. min
    # only re-emits a few rows (client merges by id — harmless). A folder
    # whose rescan failed returns now == ts (D8) and correctly holds the
    # cursor back until it heals.
    #
    # docs/80 — this loop must survive a folder another process is writing.
    # Two failure modes it now closes:
    #   * an exception escaping ONE folder used to 500 the whole poll. The
    #     client swallows the error, never advances its cursor, and the table
    #     silently stops updating — the worst possible shape for a monitoring
    #     surface. A failed folder now contributes nothing and reports
    #     ``now == ts``, i.e. it holds its own cursor exactly like the D8
    #     rescan-failure path already does, while healthy folders keep flowing.
    #   * an unbounded walk. With a writer actively landing runs, a rescan can
    #     take arbitrarily long; past the budget we stop scanning further
    #     folders and hold THEIR cursors, so the response stays prompt and the
    #     un-scanned window is re-offered next poll rather than skipped.
    now: float | None = None
    deadline = time.monotonic() + _POLL_BUDGET_S
    skipped = 0
    partial_scans = 0
    scan_ms_total = 0.0
    for fol in active:
        if time.monotonic() >= deadline:
            skipped += 1
            now = ts if now is None else min(now, ts)
            continue
        try:
            # docs/105 #4: the budget now reaches INSIDE the folder — the
            # date-dir walk itself stops at the deadline and reports partial,
            # instead of one huge folder blowing past the client's abort.
            delta = fol["store"].changes_since(ts, date=date,
                                               deadline=deadline)
            for row in delta.get("updated", []):
                row["f"] = fol["key"]
                updated.append(row)
            for rid in delta.get("vanished", []):
                vanished.append(_dataset_uid(fol["key"], rid))
            sample = delta.get("now", 0.0)
            if delta.get("partial"):
                partial_scans += 1
            scan_ms_total += float(delta.get("scan_ms") or 0.0)
        except Exception:
            logger.exception("changes-since failed for %s", fol.get("path"))
            skipped += 1
            sample = ts
        now = sample if now is None else min(now, sample)
    return jsonify({"updated": updated, "vanished": vanished, "now": now or 0.0,
                    "partial": bool(skipped or partial_scans),
                    "skipped": skipped,
                    # docs/105 #11: minimum viable observability — every
                    # symptom on this path used to present identically as
                    # "the table stopped updating".
                    "scan_ms": round(scan_ms_total, 1),
                    "folders": len(active)})


@bp.route("/datasets/rescan", methods=["POST"])
def datasets_rescan():
    """Rescan every active data folder for new runs (incremental)."""
    active = _active_dataset_stores()
    if not active:
        return render_template("_status.html",
                               message="No data folders in workspace", level="warning")
    # docs/105 #5: the user's recovery button used to hold the scan lock for
    # an UNBUDGETED full re-parse of every root — on a 10k-run share that
    # wedged every concurrent poll in both windows for minutes. The budget
    # bounds the lock hold; a truncated re-check CONTINUES via the ordinary
    # poll (force_rescan leaves the un-walked dirs poisoned + the gate open),
    # and content-equal re-parses keep their cursor so the continuation
    # never floods polling clients.
    _deadline = time.monotonic() + _RESCAN_BUDGET_S
    _truncated = 0
    for fol in active:
        try:
            # force_rescan (not rescan_if_stale): the explicit button must bypass
            # the mtime gate + B27 date-dir skip so an in-place node.json/data.json
            # rewrite (fit-result writeback) is actually re-read — otherwise the
            # user's recovery button was a silent no-op on the run they care about.
            if fol["store"].force_rescan(deadline=_deadline):
                _truncated += 1
        except Exception:
            logger.exception("Manual rescan failed for %s", fol["path"])
    if _truncated:
        logger.info("Manual rescan hit the %.0fs budget on %d folder(s) — "
                    "the poll continues the re-check incrementally.",
                    _RESCAN_BUDGET_S, _truncated)
    if _is_htmx():
        resp = make_response()
        resp.headers["HX-Redirect"] = "/datasets"
        return resp
    return redirect(url_for("main.datasets"))


@bp.route("/datasets/poll")
def datasets_poll():
    """New-run poll across ALL active folders.

    Returns the globally-latest run by ``(date, time)`` plus a folder-aware
    ``uid``. The client tracks "seen" by uid, so a mere change in WHICH folder
    is active never fires a popup — only a genuinely newer run does. (This is
    the multi-folder fix for the spurious "New Experiment Run" popup.)
    """
    active = _active_dataset_stores(fast=True)   # 60s poll — skip the token stat-walk
    latest_key: tuple[str, str] | None = None
    latest_uid: str | None = None
    latest_run = None
    for fol in active:
        for run in fol["store"].runs_snapshot():
            key = (run.date or "", run.time or "")
            if latest_key is None or key > latest_key:
                latest_key = key
                latest_uid = _dataset_uid(fol["key"], run.run_id)
                latest_run = run
    if latest_run is None:
        return jsonify({"uid": None, "run_id": None})
    return jsonify({
        "uid": latest_uid,
        "run_id": latest_run.run_id,
        "experiment_name": latest_run.experiment_name,
        "qubits": latest_run.qubits or [],
        "time": latest_run.time or "",
        "date": latest_run.date or "",
    })


# Per-run chip-identity memo. A run's frozen quam_state is WRITE-ONCE (tags/
# notes/bookmarks live app-side, never in the run folder), so the mtime-keyed
# entry is effectively immutable; if a run ever IS rewritten the key self-heals.
# Without this every run click re-read + re-hashed the run's state.json+wiring.json.
_run_chip_identity_cache: dict[str, tuple[tuple[float, float], tuple[str, str]]] = {}
_RUN_CHIP_IDENTITY_CAP = 512


def _run_chip_identity(run_qs: Path) -> tuple[str, str]:
    """``(chip_token, chip_name)`` for a run's frozen quam_state folder, memoized
    on the state/wiring mtimes."""
    from quam_state_manager.core import history
    key = str(run_qs)
    try:
        stamp = ((run_qs / "state.json").stat().st_mtime,
                 (run_qs / "wiring.json").stat().st_mtime)
    except OSError:
        stamp = None
    if stamp is not None:
        hit = _run_chip_identity_cache.get(key)
        if hit is not None and hit[0] == stamp:
            return hit[1]
    token = history.fingerprint_token(history.fingerprint_of(run_qs)) or ""
    name = history.chip_name_for(run_qs)
    if stamp is not None:
        if len(_run_chip_identity_cache) >= _RUN_CHIP_IDENTITY_CAP:
            _run_chip_identity_cache.clear()   # simple full-reset bound
        _run_chip_identity_cache[key] = (stamp, (token, name))
    return token, name


@bp.route("/dataset/by-run/<int:run_id>")
def dataset_by_run(run_id):
    """Resolve a BARE run id to its composite ``<folder_key>:<run_id>`` uid and open it.

    Topology / Chip Status experiment links (e.g. an RB ``load_id`` a calibration
    node stamped into state.json) carry only the bare run id — no folder context —
    but :func:`dataset_detail` needs the composite uid. Scan every loaded data
    folder for a run with this id and redirect to its detail view; if none has it
    (brand-new → one rescan pass; or genuinely not in any loaded folder), say so
    clearly instead of the opaque "Run N not found". Fixes the topology RB link
    that 404'd because ``/dataset/1139`` has no ``:`` for the uid parser."""
    def _find():
        for entry in _active_dataset_stores():
            if entry["store"].get_run(run_id) is not None:
                return entry["key"]
        return None
    key = _find()
    if key is None:
        # Maybe brand-new (written since the last scan) — rescan once, then retry.
        for entry in _active_dataset_stores():
            try:
                entry["store"].rescan_if_stale()
            except Exception:
                logger.exception("rescan_if_stale during by-run resolve failed")
        key = _find()
    if key is not None:
        return redirect(url_for("main.dataset_detail", uid=f"{key}:{run_id}"))
    return render_template(
        "_status.html",
        message=(f"Run #{run_id} isn't in any loaded data folder. Add the data "
                 f"folder that produced it as a Datasets workspace root, then retry."),
        level="warning"), 404


@bp.route("/dataset/<uid>")
def dataset_detail(uid):
    """Single run detail view (uid = ``<folder_key>:<run_id>``)."""
    resolved = _resolve_run(uid)
    if not resolved:
        return render_template("_status.html",
                               message=f"Run {uid} not found", level="error"), 404
    ds, run_id, folder_label = resolved
    run = ds.get_run(run_id)
    if not run:
        # Not in the cached store — it may be brand-new (written since the last
        # poll), so the scan-free resolve above missed it. Rescan ONCE and retry
        # before declaring it missing; the common (already-listed) case stays
        # scan-free, and only a genuine miss pays for the rescan.
        try:
            ds.rescan_if_stale()
        except Exception:
            logger.exception("rescan_if_stale on dataset_detail miss failed")
        run = ds.get_run(run_id)
    if not run:
        return render_template("_status.html",
                               message=f"Run #{run_id} not found", level="error"), 404
    from quam_state_manager.core.fit_targets import resolve_fit_targets
    # Stamp the run's OWN chip identity so an "Apply fitted value" can be checked
    # against the loaded chip — a run's fit must not be silently written onto a
    # different chip that merely reuses the same qubit names (audit #1).
    # NB: get_run() returns a dict (folder_path as str) — attribute access here
    # silently disabled the whole gate (empty token for every run).
    chip_token = chip_name = ""
    if run.get("has_quam_state"):
        run_qs = Path(run["folder_path"]) / "quam_state"
        chip_token, chip_name = _run_chip_identity(run_qs)
    template = "_dataset_detail.html" if _is_htmx() else "dataset_detail.html"
    return render_template(template, **_ctx(page="dataset_detail"), run=run,
                           fit_targets=resolve_fit_targets(run),
                           uid=uid, folder_key=uid.split(":")[0],
                           run_chip_token=chip_token, run_chip_name=chip_name,
                           folder_label=folder_label, folder_path=str(ds.folder_path))


@bp.route("/dataset/<uid>/fig/<name>")
def dataset_figure(uid, name):
    """Serve a figure PNG from the run's folder."""
    resolved = _resolve_run(uid)
    if not resolved:
        return "", 404
    ds, run_id, _ = resolved
    safe_name = secure_filename(name)
    fig_path = ds.get_figure_path(run_id, safe_name or name)
    if not fig_path or not fig_path.exists():
        return "", 404
    # Run figures are effectively write-once: let the browser reuse them across
    # the rapid run-flip compare gesture (max_age) and revalidate with a cheap
    # 304 afterwards (conditional Last-Modified/ETag) instead of re-downloading
    # every PNG on every re-click of the same run.
    return send_file(fig_path, mimetype="image/png", conditional=True, max_age=60)


@bp.route("/dataset/<uid>/ndview")
def dataset_ndview(uid):
    """N-D data viewer shell: list every *.h5 in the run folder + each file's
    plottable entries (data vars AND fit-result coord vars). The heavy cube
    loads lazily per variable via /ndview/data. Replaces the legacy h5 summary
    (which guessed dims by length and hid non-whitelisted files)."""
    from quam_state_manager.core import ndview
    resolved = _resolve_run(uid)
    if not resolved:
        return render_template("_status.html",
                               message="No dataset loaded", level="warning")
    ds, run_id, _ = resolved
    run = ds.runs.get(run_id)
    if run is None:
        return render_template("_status.html",
                               message="Run not found", level="warning")
    files = ndview.list_h5_files(run.folder_path)
    which = request.args.get("which") or ("ds_raw.h5" if "ds_raw.h5" in files
                                          else (files[0] if files else None))
    probe = None
    if which and which in files:   # membership check IS the containment gate
        probe = ndview.probe_file(run.folder_path / which)
    return render_template("_dataset_ndview.html",
                           uid=uid, run_id=run_id, files=files,
                           which=which, probe=probe)


@bp.route("/dataset/<uid>/ndview/data")
def dataset_ndview_data(uid):
    """One variable's JSON cube (decimated data + coords + semantics).

    ALWAYS answers HTTP 200: any internal failure returns
    ``{ok:false, error, fallback}`` so the client renders an honest card,
    never a broken swap (the never-crash contract)."""
    from quam_state_manager.core import ndview
    resolved = _resolve_run(uid)
    if not resolved:
        return jsonify({"ok": False, "error": "No dataset loaded", "fallback": None})
    ds, run_id, _ = resolved
    run = ds.runs.get(run_id)
    if run is None:
        return jsonify({"ok": False, "error": "Run not found", "fallback": None})
    which = request.args.get("which", "")
    var = request.args.get("var", "")
    files = ndview.list_h5_files(run.folder_path)
    if which not in files:
        return jsonify({"ok": False, "error": f"No data file {which!r} in this run.",
                        "fallback": None})
    if not var:
        return jsonify({"ok": False, "error": "No variable requested.", "fallback": None})
    cube_bytes, cube_meta = ndview.build_cube_bytes(run.folder_path / which, var)
    # Click→state candidates + the run's chip identity ride OUTSIDE the cached
    # cube (attached per-request): candidate paths are static, but they must
    # never be baked into an mtime-keyed cache entry alongside anything that
    # could go stale — current values are fetched at CLICK time via /field/peek.
    # The cube is cached as SERIALIZED bytes (re-dumping a 6.5 MB cube cost
    # ~100 ms per warm hit), so the extras are spliced in at the byte level:
    # cached bytes are one JSON object → drop its closing brace, append the
    # extras' members. The client-visible payload shape is unchanged (flat).
    extra = {"uid": uid, "which": which}
    if cube_meta.get("ok") and cube_meta.get("default_view"):
        from quam_state_manager.core import click_targets
        v = cube_meta["default_view"]
        entity_kind = v.get("entity")
        extra["click"] = {
            "candidates": click_targets.candidates_for(
                run.experiment_name, v.get("x"), v.get("y"), entity_kind),
            "experiment": run.experiment_name,
        }
        if getattr(run, "has_quam_state", False):
            token, name = _run_chip_identity(run.folder_path / "quam_state")
            extra["click"]["chip"] = {"token": token, "name": name}
    extra_members = json.dumps(extra, separators=(",", ":"))[1:-1].encode("utf-8")
    body = cube_bytes[:-1] + b"," + extra_members + b"}"
    resp = make_response(body)
    resp.mimetype = "application/json"
    return resp


@bp.route("/dataset/<uid>/h5")
def dataset_h5(uid):
    """Lazy-load HDF5 dataset summary."""
    resolved = _resolve_run(uid)
    if not resolved:
        return render_template("_status.html",
                               message="No dataset loaded", level="warning")
    ds, run_id, _ = resolved
    which = request.args.get("which", "ds_raw")
    summary = ds.get_h5_summary(run_id, which)
    if not summary:
        return render_template("_status.html",
                               message="Dataset not available", level="warning")
    return render_template("_dataset_h5.html",
                           summary=summary, run_id=run_id, uid=uid, which=which)


@bp.route("/dataset/<uid>/h5/plot")
def dataset_h5_plot(uid):
    """Read a data variable from HDF5 and return Plotly-ready JSON."""
    resolved = _resolve_run(uid)
    if not resolved:
        return jsonify({"error": "No dataset loaded"}), 400
    ds, run_id, _ = resolved
    which = request.args.get("which", "ds_raw")
    var_name = request.args.get("var")
    qubit_idx = request.args.get("qubit")
    if qubit_idx is not None:
        try:
            qubit_idx = int(qubit_idx)
        except ValueError:
            qubit_idx = None
    data = ds.get_h5_plot_data(run_id, which, var_name, qubit_idx)
    if not data:
        return jsonify({"error": "Data not available"}), 404
    return jsonify(data)


@bp.route("/dataset/<uid>/interactive")
def dataset_interactive(uid):
    """Interactive-figures tab: the figure *menu* for this run (HTML partial).

    Dispatches to the per-experiment recipe; each figure's heavy arrays load
    lazily via /interactive/plot when its tile scrolls into view.
    """
    resolved = _resolve_run(uid)
    if not resolved:
        return render_template("_status.html",
                               message="No dataset loaded", level="warning")
    ds, run_id, _ = resolved
    run = ds.runs.get(run_id)  # RunInfo (attribute access); get_run() returns a plain dict
    if not run:
        return render_template("_status.html",
                               message=f"Run #{run_id} not found", level="error"), 404
    # Imported lazily so a missing numpy/h5py only affects this feature, not startup.
    from quam_state_manager.core.interactive_plots import list_interactive_figures
    figures = list_interactive_figures(run)
    return render_template("_dataset_interactive.html", run_id=run_id, uid=uid, figures=figures)


@bp.route("/dataset/<uid>/interactive/plot")
def dataset_interactive_plot(uid):
    """Return one interactive figure's Plotly JSON: ``?fig=<key>``."""
    resolved = _resolve_run(uid)
    if not resolved:
        return jsonify({"error": "No dataset loaded"}), 400
    ds, run_id, _ = resolved
    run = ds.runs.get(run_id)  # RunInfo (attribute access)
    if not run:
        return jsonify({"error": "Run not found"}), 404
    fig_key = request.args.get("fig", "")
    if not fig_key:
        return jsonify({"error": "fig required"}), 400
    from quam_state_manager.core.interactive_plots import build_interactive_figure
    fig = build_interactive_figure(run, fig_key)
    if fig is None:
        return jsonify({"error": "Figure not available"}), 404
    return jsonify(fig)


@bp.route("/dataset/<uid>/replot")
def dataset_replot(uid):
    """Strategy-B menu: reproduce this run's figures by re-running its own plotting.

    Runs the experiment's ``plotting.py`` in the selected QM env against the saved
    datasets (cached). ``?force=1`` re-runs after an analysis-code edit. The heavy
    arrays for each tile load lazily via ``/replot/plot``.
    """
    resolved = _resolve_run(uid)
    if not resolved:
        return render_template("_status.html", message="No dataset loaded", level="warning")
    ds, run_id, _ = resolved
    run = ds.runs.get(run_id)
    if not run:
        return render_template("_status.html",
                               message=f"Run #{run_id} not found", level="error"), 404
    from quam_state_manager.core.interactive_plots.replot import (
        replot_capability, replot_run, replot_menu)
    cap = replot_capability(run, current_app.instance_path)
    if not cap["available"]:
        return render_template("_dataset_replot.html", uid=uid, run_id=run_id,
                               available=False, reason=cap["reason"],
                               figures=[], errors=[])
    force = request.args.get("force") == "1"
    result = replot_run(run, current_app.instance_path, force=force)
    return render_template("_dataset_replot.html", uid=uid, run_id=run_id,
                           available=True, reason="", util=result.get("util", ""),
                           figures=replot_menu(result), errors=result.get("errors", []))


@bp.route("/dataset/<uid>/replot/plot")
def dataset_replot_plot(uid):
    """One reproduced figure as Plotly JSON from the cached re-run: ``?fig=<key>``."""
    resolved = _resolve_run(uid)
    if not resolved:
        return jsonify({"error": "No dataset loaded"}), 400
    ds, run_id, _ = resolved
    run = ds.runs.get(run_id)
    if not run:
        return jsonify({"error": "Run not found"}), 404
    fig_key = request.args.get("fig", "")
    if not fig_key:
        return jsonify({"error": "fig required"}), 400
    from quam_state_manager.core.interactive_plots.replot import (
        replot_capability, replot_run, replot_figure)
    # Gate before spawning: a direct tile hit (no prior menu, or no env) must not
    # kick off a subprocess for an unreproducible run.
    if not replot_capability(run, current_app.instance_path)["available"]:
        return jsonify({"error": "Reproduction not available"}), 404
    result = replot_run(run, current_app.instance_path)  # in-flight-coalesced w/ the menu call
    fig = replot_figure(result, fig_key)
    if fig is None:
        return jsonify({"error": "Figure not available"}), 404
    return jsonify(fig)


@bp.route("/dataset/<uid>/json")
def dataset_json_file(uid):
    """Return a raw JSON file from a run's folder."""
    resolved = _resolve_run(uid)
    if not resolved:
        return jsonify({"error": "No dataset loaded"}), 400
    ds, run_id, _ = resolved
    run = ds.runs.get(run_id)
    if not run:
        return jsonify({"error": "Run not found"}), 404

    file = request.args.get("file", "node")  # state | wiring | node | data
    if file in ("state", "wiring"):
        qs = ds.get_quam_state_path(run_id)
        if not qs:
            return jsonify({"error": "No quam_state in this run"}), 404
        json_path = qs / f"{file}.json"
    elif file in ("node", "data"):
        json_path = run.folder_path / f"{file}.json"
    else:
        return jsonify({"error": f"Unknown file: {file}"}), 400

    if not json_path.exists():
        return jsonify({"error": f"{file}.json not found"}), 404
    try:
        # safe_io.read_json (not a bare open): on Windows a default open() lacks
        # FILE_SHARE_DELETE and blocks a still-writing experiment's atomic
        # os.replace of this exact file — viewing the JSON tab of the running run
        # during its final writeback would fail the experiment's save. safe_io also
        # retries transient locks instead of 500-ing on a mid-write read.
        return jsonify(safe_io.read_json(json_path))
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@bp.route("/dataset/<uid>/prev-state-diff")
def dataset_prev_state_diff(uid):
    """Diff this run's quam_state against an earlier run's (item 5).

    Lazy-loaded by the Prev State tab / Full-View summary. ``vs`` overrides which
    earlier run to compare against (the stepper); without it the immediately-
    previous state-carrying run is used. Reuses ``Differ.diff`` over the two
    ``quam_state/`` folders. prev/next stepping stays WITHIN this run's folder.
    """
    resolved = _resolve_run(uid)
    if not resolved:
        return render_template("_dataset_prev_diff.html", error="No dataset loaded")
    ds, run_id, _ = resolved
    cur_path = ds.get_quam_state_path(run_id)
    if not cur_path:
        return render_template("_dataset_prev_diff.html",
                               error="This run has no saved state to compare.")

    vs = request.args.get("vs", type=int)
    if vs is None:
        vs = ds.get_previous_run_id(run_id)
    compact = request.args.get("compact") == "1"
    if vs is None:
        return render_template("_dataset_prev_diff.html", run_id=run_id, uid=uid,
                               prev_run_id=None, compact=compact)

    prev_path = ds.get_quam_state_path(vs)
    if not prev_path:
        return render_template("_dataset_prev_diff.html", run_id=run_id, uid=uid,
                               prev_run_id=None, compact=compact)

    entries = Differ().diff(prev_path, cur_path)
    summary = Differ.summary(entries)

    # Stepper bounds: walk the comparison run older/newer. r16 ④: the old
    # clamp refused any vs ≥ run_id, which made "newer ›" DISABLED on every
    # first open (vs starts at the immediate prev, so its next IS run_id) —
    # the reported "only prev works". Comparing against a LATER run is a
    # legitimate ask (how did the state change AFTER this run?), so only the
    # self-diff is skipped; the column headers name both ids either way.
    older = ds.get_previous_run_id(vs)
    newer = ds.get_next_run_id(vs)
    if newer == run_id:                     # skip the self-diff in BOTH directions
        newer = ds.get_next_run_id(run_id)
    if older == run_id:
        older = ds.get_previous_run_id(run_id)

    return render_template("_dataset_prev_diff.html", run_id=run_id, uid=uid,
                           prev_run_id=vs, entries=entries, summary=summary,
                           older=older, newer=newer, compact=compact,
                           limit=(8 if compact else 300))


@bp.route("/dataset/<uid>/neighbor")
def dataset_neighbor(uid):
    """The adjacent run's uid in run-id order (r16 ④): the inspector-header
    ↑/↓ nav walked only the sidebar tree's VISIBLE entries — a run opened
    from the Datasets table (or with the tree collapsed/filtered) had both
    buttons silently dead. ``dir``: -1 = newer (next id), 1 = older
    (previous id) — the tree's spatial order. Any run counts (not just
    state-carrying): this is navigation, not a diff."""
    resolved = _resolve_run(uid)
    if not resolved:
        return jsonify(ok=False), 404
    ds, run_id, _ = resolved
    d = request.args.get("dir", type=int) or 1
    nid = (ds.get_next_run_id(run_id, require_state=False) if d < 0
           else ds.get_previous_run_id(run_id, require_state=False))
    folder_key = uid.rsplit(":", 1)[0]
    return jsonify(ok=True,
                   uid=(f"{folder_key}:{nid}" if nid is not None else None),
                   run_id=nid)


@bp.route("/dataset/<uid>/load-state", methods=["POST"])
def dataset_load_state(uid):
    """Bring a run's frozen quam_state INTO the open chip (r11 feedback).

    With a project chip open, the user's intent is "pull this experiment's
    state into MY chip" — so the snapshot is STAGED into the active context's
    WORKING COPY (State History Mode-1 semantics: review, then Sync / Apply
    pushes it live; the live files are never written here). The old behavior
    — activating the run's archive as a separate read-only context, which
    hijacked the active context (project scope lost, load-path box rewritten
    to the experiment folder) — stays available as ``mode=archive`` and as
    the fallback when nothing editable is loaded.

    Gates (independent tokens, docs/41 doctrine): chip-identity mismatch →
    ``force_chip=1``; pending working-copy edits → ``force=1``.

    ``apply=1`` (docs/108, user-requested): ONE press stages the snapshot AND
    pushes it to the live chip through the shared apply core
    (:func:`_sync_pull_apply_to_live` — the one write path, so the pre-apply
    snapshot arms "↺ Revert last apply" exactly like every other apply).
    Covenant-compliant: the button is labeled "Apply to chip", so the press
    IS the one explicit apply act — no confirm dialog (the no-confirm
    doctrine), and reversibility is what makes one click safe. The identity /
    pending-edit gates stay (they answer a DIFFERENT question than consent),
    and a staleness conflict renders the honest conflict tray rather than
    force-pushing over a live chip that moved.
    """
    resolved = _resolve_run(uid)
    state_path = None
    run_id = None
    if resolved:
        ds, run_id, _ = resolved
        state_path = ds.get_quam_state_path(run_id)
    if not state_path:
        return render_template("_status.html",
                               message="No quam_state in this run", level="error")

    ctx = _active_ctx()
    can_stage = (ctx is not None and ctx.get("type") == "quam"
                 and (ctx.get("origin") or "live") == "live"
                 and request.values.get("mode") != "archive")
    if not can_stage:
        try:
            # A dataset run's quam_state is a FROZEN archive — open it
            # read-only so save/apply routes refuse to overwrite the record.
            _activate_quam(state_path, origin="dataset_archive")
        except Exception as e:  # noqa: BLE001
            return render_template("_status.html",
                                   message=f"Failed to load state: {e}",
                                   level="error")
        resp = make_response()
        resp.headers["HX-Redirect"] = "/qubits"
        return resp

    # ---- stage into the ACTIVE chip's working copy ----
    base_url = f"/dataset/{uid}/load-state"
    chip_label = _chip_display_name(Path(ctx["path"]))
    apply_req = request.values.get("apply") == "1"   # docs/108 one-click
    apply_qs = "&apply=1" if apply_req else ""
    apply_note = " It will then be APPLIED to the live chip." if apply_req else ""

    # Gate 1 — chip identity: the run's snapshot must fingerprint-align with
    # the loaded chip; UNKNOWN (unreadable fingerprint) also confirms.
    if request.values.get("force_chip") != "1":
        from quam_state_manager.core import history as _hist
        alignment = _hist.align(_hist.fingerprint_of(ctx["path"]),
                                _hist.fingerprint_of(state_path))
        if alignment != _hist.ALIGN_ALIGNED:
            reason = ("could not be verified (unreadable fingerprint)"
                      if alignment == _hist.ALIGN_UNKNOWN
                      else "looks like a DIFFERENT chip")
            url = (base_url + "?force_chip=1"
                   + ("&force=1" if request.values.get("force") == "1" else "")
                   + apply_qs)
            return render_template(
                "_sh_confirm.html",
                message=(f"This run's quam_state {reason} vs the loaded chip "
                         f"({chip_label}). Loading it replaces the working "
                         "state wholesale." + apply_note),
                action_url=url,
                action_label="Load into working state anyway",
                confirm=("Replace the working state with a snapshot from a "
                         "different/unverified chip?"),
                target="#ds-load-state-result",
            ), 409

    # Gate 2 — pending edits (mirrors /state-history/<ts>/stage).
    store = ctx["store"]
    with store._lock:
        has_pending = (bool(store.change_log) or bool(ctx.get("pending_reapply"))
                       or bool(ctx.get("working_dirty")))
    if has_pending and request.values.get("force") != "1":
        url = (base_url + "?force=1"
               + ("&force_chip=1" if request.values.get("force_chip") == "1" else "")
               + apply_qs)
        return render_template(
            "_sh_confirm.html",
            message=("You have unsaved edits in the working state. Loading "
                     "this run's snapshot will replace them." + apply_note),
            action_url=url,
            action_label="Replace working state anyway",
            confirm="Discard your unsaved edits and load this run's state?",
            target="#ds-load-state-result",
        ), 409

    try:
        state, wiring = safe_io.read_state_wiring(Path(state_path))
    except (OSError, ValueError, safe_io.LiveFileError) as exc:
        return render_template("_status.html",
                               message=f"Could not read the run's quam_state: {exc}",
                               level="error"), 500
    wc = ctx["working_copy"]
    try:
        with _active_wc_lock(ctx):
            safe_io.write_state_wiring(wc.working_folder, state, wiring)
            ctx["_alarm_reason"] = "run-state-load"
            _rebuild_after_working_copy_replaced(ctx)
            ctx["working_dirty"] = True   # working now differs from live
            ctx["staged_base"] = True     # audit-r10 (see state_history_stage)
            _clear_reapply(ctx)
    except (OSError, ValueError) as exc:
        return render_template("_status.html",
                               message=f"Loading run state failed: {exc}",
                               level="error"), 500
    logger.info("dataset run %s staged into the working copy of %s",
                uid, ctx["path"])

    if apply_req:
        # docs/108 one-click: push the just-staged snapshot through the SHARED
        # apply core — same pre-apply snapshot (arms ↺ Revert last apply),
        # same staleness handling, same bookkeeping as the ⚡ button.
        result = _sync_pull_apply_to_live(ctx, None)
        body = (result[0] if isinstance(result, tuple) else result).get_json()
        status = (body or {}).get("status")
        if status == "ok":
            msg = render_template(
                "_status.html",
                message=(f"Run #{run_id}'s state is now LIVE on {chip_label}. "
                         "Reversible — ↺ Revert last apply (top bar) "
                         "restores the pre-apply state."),
                level="success")
            resp = make_response(msg + "\n" + _tray_oob())
            resp.headers["HX-Trigger"] = "pulses-changed, stateRestored, diagnostics-changed"
            return resp
        if status == "conflict":
            # The live chip moved since it was loaded — the snapshot is STAGED
            # (safe) and live was NOT written; docs/108 never force-pushes over
            # a chip that changed under us.
            #
            # docs/116: that verdict used to arrive as a one-line warning
            # pointing at the TOP BAR, and the tray it pointed at asks the
            # question of a different flow ("choose which side wins" — whose
            # ↓ option discards the run the user had just chosen). One press
            # therefore became: read a warning, go find the tray, pick from
            # choices that misdescribe the situation, then answer a native
            # confirm. The continuation now renders in #ds-load-state-result
            # — where the button was — and offers the choice the press meant.
            # The tray still swaps OOB so the two surfaces cannot disagree.
            tray = (body.get("tray_html") or "").replace(
                '<div id="pending-tray"',
                '<div id="pending-tray" hx-swap-oob="outerHTML"', 1)
            msg = render_template("_ds_apply_conflict.html",
                                  run_id=run_id, chip_label=chip_label)
            resp = make_response(msg + "\n" + tray)
            resp.headers["HX-Trigger"] = "pulses-changed, stateRestored, diagnostics-changed"
            return resp
        # error — the staging succeeded; say exactly how far things got.
        return render_template(
            "_status.html",
            message=((body or {}).get("message") or "Apply to live failed")
            + " — the run's state IS staged in the working copy; retry from "
              "the top bar.",
            level="error"), 500

    msg = render_template(
        "_status.html",
        message=(f"Run #{run_id}'s state is now the WORKING state of "
                 f"{chip_label} — review it, then Sync / Apply to live from "
                 "the top bar (the live chip is untouched until then)."),
        level="success")
    # detail-area message + OOB tray refresh; stateRestored closes stale
    # inspector panes open on another menu (the working copy changed wholesale).
    resp = make_response(msg + "\n" + _tray_oob())
    resp.headers["HX-Trigger"] = "pulses-changed, stateRestored, diagnostics-changed"
    return resp


@bp.route("/datasets/tags")
def datasets_all_tags():
    """Return all unique tags across every active folder."""
    tags_set: set[str] = set()
    for fol in _active_dataset_stores():
        tags_set.update(fol["store"].list_all_tags())
    return jsonify({"tags": sorted(tags_set)})


@bp.route("/dataset/<uid>/bookmark", methods=["POST"])
def dataset_bookmark(uid):
    """Toggle the run's ⭐ favorite. Thin alias over the favorite tag.

    Bookmarking is now just the reserved favorite tag, so this returns the
    updated tag list too — the client patches both the star (``bookmarked``)
    and the row's tags (so the Collections "favorite" filter stays live).
    """
    resolved = _resolve_run(uid)
    if not resolved:
        return jsonify({"error": "No dataset loaded"}), 400
    ds, run_id, _ = resolved
    try:
        new_state = ds.toggle_bookmark(run_id)
    except OSError as exc:
        # The store already rolled back the in-memory toggle; the disk write
        # failed (read-only archive / permissions) — a 400, not a 500.
        return jsonify({"error": f"dataset folder is read-only ({exc})"}), 400
    run = ds.runs.get(run_id)
    tags = list(run.tags) if run else []
    return jsonify({"bookmarked": new_state, "tags": tags, "run_id": run_id, "uid": uid})


@bp.route("/dataset/<uid>/tag", methods=["POST"])
def dataset_add_tag(uid):
    """Add a tag to a run."""
    resolved = _resolve_run(uid)
    if not resolved:
        return jsonify({"error": "No dataset loaded"}), 400
    ds, run_id, _ = resolved
    tag = request.json.get("tag", "").strip() if request.is_json else request.form.get("tag", "").strip()
    if not tag:
        return jsonify({"error": "No tag specified"}), 400
    try:
        tags = ds.add_tag(run_id, tag)
    except OSError as exc:
        # In-memory change already rolled back by the store (docs: _tags_lock
        # rollback contract) — surface the read-only folder as a 400.
        return jsonify({"error": f"dataset folder is read-only ({exc})"}), 400
    return jsonify({"tags": tags, "run_id": run_id, "uid": uid})


@bp.route("/dataset/<uid>/tag", methods=["DELETE"])
def dataset_remove_tag(uid):
    """Remove a tag from a run."""
    resolved = _resolve_run(uid)
    if not resolved:
        return jsonify({"error": "No dataset loaded"}), 400
    ds, run_id, _ = resolved
    tag = request.json.get("tag", "").strip() if request.is_json else ""
    if not tag:
        return jsonify({"error": "No tag specified"}), 400
    try:
        tags = ds.remove_tag(run_id, tag)
    except OSError as exc:
        return jsonify({"error": f"dataset folder is read-only ({exc})"}), 400
    return jsonify({"tags": tags, "run_id": run_id, "uid": uid})


@bp.route("/dataset/<uid>/compare-prev")
def dataset_compare_prev(uid):
    """One-click 'vs previous run of the SAME experiment' — the calibration
    engineer's core question, previously reconstructed by hand (find the prior
    same-node run in the tree, checkbox both in the table, Compare). Resolves
    the nearest earlier same-experiment run and 302s to the standard compare
    view (the XHR follows the redirect transparently, so the compare partial
    lands in whatever pane requested it)."""
    resolved = _resolve_run(uid)
    if not resolved:
        return render_template("_status.html",
                               message=f"Run {uid} not found", level="error"), 404
    ds, run_id, _label = resolved
    prev_id = ds.get_previous_same_experiment_id(run_id)
    if prev_id is None:
        run = ds.get_run(run_id) or {}
        return render_template(
            "_status.html",
            message=(f"No earlier run of “{run.get('experiment_name', 'this experiment')}”"
                     f" in this folder — this is the first one."),
            level="info")
    folder_key = uid.split(":")[0]
    return redirect(url_for("main.datasets_compare",
                            ids=f"{folder_key}:{prev_id},{uid}"))


@bp.route("/datasets/compare")
def datasets_compare():
    """Compare 2-8 selected dataset runs side-by-side (uids may span folders)."""
    ids_raw = request.args.get("ids", "")
    uids = [x.strip() for x in ids_raw.split(",") if x.strip()]
    # Cap raised 5→8 (persona audit: post-mortem analysts want wider sweeps; the
    # figure-grid renderer scales, the old cap was arbitrary).
    if len(uids) < 2 or len(uids) > 8:
        return render_template("_status.html",
                               message="Select 2-8 runs to compare", level="warning")

    runs = []
    contexts = []
    labels = []
    for uid in uids:
        resolved = _resolve_run(uid)
        if not resolved:
            continue
        store, rid, _label = resolved
        run = store.get_run(rid)
        if not run:
            continue
        run["uid"] = uid   # the template builds figure URLs from run.uid
        runs.append(run)
        ctx = ExperimentContext(
            parameters=run["parameters"] or {},
            fit_results=run["fit_results"] or {},
            outcomes=run["outcomes"] or {},
            metadata={"name": run["experiment_name"],
                      "status": run.get("status", ""),
                      "run_start": run.get("run_start", ""),
                      "run_end": run.get("run_end", "")},
            experiment_name=run["experiment_name"],
            has_data=True,
        )
        contexts.append(ctx)
        labels.append(f"#{rid} {run['experiment_name']}")

    if len(runs) < 2:
        return render_template("_status.html",
                               message="Could not load at least 2 runs",
                               level="warning")

    param_diff_rows = Differ.compare_parameters(contexts, labels, include_equal=True)
    fit_diff_rows = Differ.compare_fit_results(contexts, labels)

    # Compute figure key union (preserve order)
    all_fig_keys: list[str] = []
    for run in runs:
        for fn in run.get("figure_names", []):
            if fn not in all_fig_keys:
                all_fig_keys.append(fn)

    return render_template("_dataset_compare.html", **_ctx(page="dataset_compare"),
                           runs=runs, labels=labels, run_ids=[r["uid"] for r in runs],
                           param_diff_rows=param_diff_rows,
                           fit_diff_rows=fit_diff_rows,
                           all_fig_keys=all_fig_keys,
                           ref_idx=0)


@bp.route("/trends")
def trends():
    """Trend dashboard page.

    Multi-folder: a folder selector (single by default) sits atop the experiment
    + qubit pickers. The experiment/qubit option lists are the UNION across all
    active folders so they stay populated whichever folder is chosen.
    """
    active = _active_dataset_stores()
    template = "_trends.html" if _is_htmx() else "trends.html"
    if not active:
        return render_template(template, **_ctx(page="trends"), no_workspace=True)
    experiments: set[str] = set()
    qubits: set[str] = set()
    for f in active:
        experiments.update(f["store"].experiment_types)
        qubits.update(f["store"].summary_stats.get("unique_qubits", []))
    folders = [{"key": f["key"], "label": f["label"], "full_path": f["path"]} for f in active]
    # Project lens (docs/63): pre-select the scope's recorded roots — all of
    # them only when they're provably the SAME chip (cross-chip trend merges
    # are meaningless), else just the newest one. Unscoped → [] → the
    # template falls back to today's first-folder default.
    scope_project = (_active_ctx() or {}).get("qualibrate_project") or ""
    trend_scope_keys = _scope_folder_keys(scope_project, folders)
    if len(trend_scope_keys) > 1:
        in_scope = set(trend_scope_keys)
        scope_folders = [f for f in active if f["key"] in in_scope]
        if _folders_same_chip(scope_folders) != "same":
            newest = max(scope_folders,
                         key=lambda f: max(f["store"].dates or [""]))
            trend_scope_keys = [newest["key"]]
    return render_template(template, **_ctx(page="trends"),
                           experiments=sorted(experiments),
                           qubits=sorted(qubits),
                           folders=folders,
                           scope_project=scope_project,
                           trend_scope_keys=trend_scope_keys,
                           no_workspace=False)


@bp.route("/trends/data")
def trends_data():
    """HTMX fragment: trend charts + figure strip for an experiment.

    ``folders=<key,key>`` scopes the trend. Single folder → ordered by run_id.
    Multiple folders are only combined when they're the SAME chip (a cross-chip
    trend is physically meaningless); same-chip merges order by run timestamp so
    colliding run_ids across folders interleave correctly. Different chips →
    a warning fragment offering to fall back to one folder.
    """
    from quam_state_manager.core.dataset import build_trend_data
    active = _active_dataset_stores()
    if not active:
        return render_template("_status.html",
                               message="No dataset loaded", level="warning")
    experiment = request.args.get("experiment", "")
    qubit = request.args.get("qubit") or None
    if not experiment:
        return render_template("_status.html",
                               message="Select an experiment type", level="info")

    sel_keys = [k for k in request.args.get("folders", "").split(",") if k.strip()]
    sel = [f for f in active if f["key"] in sel_keys] if sel_keys else []
    if not sel:
        sel = [active[0]]   # default: a single (first active) folder

    # Cross-chip trends are meaningless — only merge folders that share a chip.
    if len(sel) > 1 and _folders_same_chip(sel) != "same":
        return render_template("_trends_chip_warning.html",
                               folders=sel, experiment=experiment, qubit=qubit or "")

    key_of: dict[int, str] = {}
    matching: list = []
    for f in sel:
        for run in f["store"].runs_snapshot():
            if run.experiment_name == experiment and (qubit is None or qubit in run.qubits):
                key_of[id(run)] = f["key"]
                matching.append(run)
    # Single folder → run_id order (chronological within a folder); same-chip
    # merge → run-timestamp order, since run_ids may collide across folders.
    if len(sel) > 1:
        matching.sort(key=lambda r: ((r.date or ""), (r.time or ""), r.run_id))
    else:
        matching.sort(key=lambda r: r.run_id)

    trend = build_trend_data(matching, qubit=qubit, folder_key_of=lambda r: key_of[id(r)])

    # Parameter diffs across matching runs.
    param_diff_rows: list[dict] = []
    trend_labels: list[str] = []
    if len(matching) >= 2:
        contexts = []
        for run in matching:
            ctx = ExperimentContext(
                parameters=run.parameters or {},
                fit_results=run.fit_results or {},
                outcomes=run.outcomes or {},
                experiment_name=run.experiment_name,
                has_data=True,
            )
            contexts.append(ctx)
            trend_labels.append(f"#{run.run_id}")
        param_diff_rows = Differ.compare_parameters(contexts, trend_labels)

    return render_template("_trends_data.html",
                           trend=trend, experiment=experiment,
                           qubit=qubit,
                           param_diff_rows=param_diff_rows,
                           labels=trend_labels)


@bp.route("/dataset/<uid>/note", methods=["POST"])
def dataset_set_note(uid):
    """Set a note on a run."""
    resolved = _resolve_run(uid)
    if not resolved:
        return jsonify({"error": "No dataset loaded"}), 400
    ds, run_id, _ = resolved
    note = request.json.get("note", "") if request.is_json else request.form.get("note", "")
    try:
        ds.set_note(run_id, note)
    except OSError as exc:
        return jsonify({"error": f"dataset folder is read-only ({exc})"}), 400
    return jsonify({"note": note, "run_id": run_id, "uid": uid})


# ======================================================================
# Generate Config — interactive QUAM config builder
# ======================================================================


@bp.route("/generate")
def generate():
    """The Generate Config wizard page."""
    template = "_generate.html" if _is_htmx() else "generate.html"
    return render_template(template, **_ctx(page="generate"))


@bp.route("/regenerate")
def regenerate_page():
    """Re-generate Config — rebuild an existing chip from an editable spec
    (reassign ports, change bands, add/remove qubits) while its calibrated
    values are carried over, into a NEW folder. Skeleton; the wizard prefill +
    build/merge wiring (``core.regenerate``) is attached next.

    ``?step=N`` (r15 CG1, docs/70) deep-links the wizard to a step after the
    reconstruct+hydrate finishes — the /instrument "Modify wiring…" button
    passes step=5 (Wiring). Clamped 1..8; absent/0 keeps the default step 1.
    """
    step = min(_int_arg("step", 0, minimum=0), 8)
    template = "_regenerate.html" if _is_htmx() else "regenerate.html"
    return render_template(template, **_ctx(page="regenerate",
                                            regen_step=step or None))


@bp.route("/generate/envs")
def generate_envs():
    """List conda environments (fast — QM-stack probing is done separately)."""
    return jsonify({
        "envs": config_generator.discover_envs(),
        "selected": config_generator.get_selected_env(current_app.instance_path),
    })


# --- Populate-step default-value presets (core/gen_presets.py) -------------
# Named default sets (pulse values, resonator timings, flux points, pair
# seeds) stored server-side under <instance>/gen_presets/ so they survive
# browser sessions. CSRF is covered by the app-level origin check.

@bp.route("/generate/import-port-csv", methods=["POST"])
def generate_import_port_csv():
    """Parse a port-label-mapping CSV (the QM fixed-transmon reference flow)
    into the wizard prefill payload — instruments, qubits, grid, directed
    pairs, port pins. The client reads the file via FileReader and posts the
    text; validation failures come back as a structured error list (400)."""
    data = request.get_json(silent=True) or {}
    text = data.get("text")
    if not isinstance(text, str) or not text.strip():
        return jsonify({"ok": False, "errors": ["empty CSV"]}), 400
    if len(text) > 2 * 1024 * 1024:
        return jsonify({"ok": False, "errors": ["CSV too large (2 MB max)"]}), 400
    from quam_state_manager.core import port_csv
    payload = port_csv.parse_port_label_csv(text)
    return (jsonify(payload), 200) if payload.get("ok") else (jsonify(payload), 400)


@bp.route("/generate/presets")
def generate_presets_list():
    """Summaries of every stored preset (corrupt files flagged, never a 500)."""
    return jsonify({
        "ok": True,
        "presets": gen_presets.list_presets(current_app.instance_path),
    })


@bp.route("/generate/presets/<slug>")
def generate_presets_get(slug: str):
    """One preset's full payload."""
    data = gen_presets.load_preset(current_app.instance_path, slug)
    if data is None:
        return jsonify({"ok": False, "error": "Preset not found."}), 404
    data["ok"] = True
    data["slug"] = slug
    return jsonify(data)


@bp.route("/generate/presets", methods=["POST"])
def generate_presets_save():
    """Save (or overwrite) a named preset.

    An existing slug without ``overwrite`` returns ``needs_confirm`` — the
    same confirm-round-trip idiom as /generate/build's stray-JSON gate.
    """
    data = request.get_json(silent=True) or {}
    name = data.get("name") or ""
    sections = data.get("sections") or {}
    try:
        summary = gen_presets.save_preset(
            current_app.instance_path, name, sections,
            overwrite=bool(data.get("overwrite")),
        )
    except FileExistsError as exc:
        return jsonify({
            "ok": False, "needs_confirm": True, "slug": str(exc),
            "error": f'A preset named "{name}" already exists.',
        })
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    except OSError as exc:
        return jsonify({"ok": False, "error": f"Could not save preset: {exc}"}), 500
    summary["ok"] = True
    return jsonify(summary)


@bp.route("/generate/presets/<slug>", methods=["DELETE"])
def generate_presets_delete(slug: str):
    """Delete a preset (idempotent). The built-in one is undeletable."""
    if slug == gen_presets.BUILTIN_SLUG:
        return jsonify({"ok": False,
                        "error": "The built-in preset can't be deleted."}), 400
    gen_presets.delete_preset(current_app.instance_path, slug)
    return jsonify({"ok": True})


@bp.route("/generate/probe")
def generate_probe():
    """Probe one interpreter for the QM stack (qualang_tools/quam_builder/quam).

    Routes through ``probe_envs`` so the result is cached under
    ``<instance>/config_generator_probe_cache.json`` keyed on the
    interpreter's mtime. Repeated wizard visits skip the subprocess
    spawn when nothing changed.
    """
    python_path = (request.args.get("python") or "").strip()
    if not python_path:
        return jsonify({"error": "No interpreter path given."}), 400
    # r15 (docs/71): accept a venv/conda FOLDER (or a project folder holding
    # a .venv) and resolve it to the interpreter file server-side.
    resolved = (config_generator.resolve_python_interpreter(python_path)
                or python_path)
    res = config_generator.probe_selected_env(
        resolved, instance_path=current_app.instance_path,
    )
    res["resolved"] = resolved
    return jsonify(res)


@bp.route("/generate/select-env", methods=["POST"])
def generate_select_env():
    """Persist the user's chosen generator interpreter — ANY Python interpreter
    (a conda env OR a plain venv's ``python`` / ``python.exe``), not just the
    discovered conda envs. Validates the path points at a real file before
    persisting (a bad path would otherwise fail every later subprocess with a
    confusing error)."""
    data = request.get_json(silent=True) or {}
    python_path = (data.get("python") or "").strip()
    if not python_path:
        return jsonify({"ok": False, "error": "No interpreter path given."}), 400
    # r15 (docs/71): a venv folder / project folder with a .venv resolves to
    # its interpreter here — uv users pick the FOLDER, never type the file.
    resolved = config_generator.resolve_python_interpreter(python_path)
    if not resolved:
        return jsonify({
            "ok": False,
            "error": (f"No Python interpreter at: {python_path}. Point at the "
                      "interpreter file OR a venv folder containing "
                      r"Scripts\python.exe / bin/python (a project folder "
                      "holding a .venv works too)."),
        }), 400
    python_path = resolved
    # A Windows interpreter selected from a POSIX app can't open the POSIX
    # /tmp work paths every subprocess bridge hands it — the failure mode is
    # a silent "produced no _result.json" on every later build. The ONE
    # documented, supported bridge is WSL driving a /mnt/<drive> Windows
    # conda env, so allow exactly that.
    if os.name != "nt" and python_path.lower().endswith(".exe"):
        if not (python_path.startswith("/mnt/")
                and config_generator.running_under_wsl()):
            return jsonify({
                "ok": False,
                "error": ("That's a Windows interpreter (.exe) — it can't read "
                          "this app's POSIX work files, so every build would "
                          "fail with 'produced no _result.json'. Pick the "
                          "env's bin/python instead."),
            }), 400
    config_generator.set_selected_env(current_app.instance_path, python_path)
    # The pulse-roster overlay belongs to the PREVIOUS env — clear it now; the
    # warm below re-applies the new env's roster when its probe lands.
    from quam_state_manager.core import pulse_catalog
    pulse_catalog.apply_env_overlay(None)
    # Warm the capability manifest in the background so the review step's report
    # is instant (the deep probe imports the stack — a few seconds, then cached).
    inst = current_app.instance_path
    threading.Thread(
        target=lambda: config_generator.probe_capabilities(python_path, inst),
        daemon=True,
    ).start()
    # Re-bind the active chip's type policy to the NEW env (stat-cached read —
    # likely cold for a fresh env → assignments-only until the warm lands),
    # then warm the schema manifest in the background (single-flight).
    _ctx = _active_ctx()
    if _ctx:
        _attach_type_policy(_ctx, inst)
        _warm_state_schema_async(_ctx.get("store"), inst,
                                 live_folder=_ctx.get("path"))
    return jsonify({"ok": True, "selected": python_path})


@bp.route("/generate/capabilities", methods=["POST"])
def generate_capabilities():
    """Assess a spec against the SELECTED env's real capabilities.

    Deep-probes the env (cached, version-keyed) then returns a three-bucket
    report: what the spec needs and the env HAS (ok), needs but is MISSING and
    would fail (blockers), or needs but is missing and would silently degrade
    (warnings) — plus the full env capability inventory. ``force`` re-probes
    (for editable installs whose version string didn't change).
    """
    data = request.get_json(silent=True) or {}
    spec = data.get("spec") or {}
    force = bool(data.get("force"))
    python_path = config_generator.get_selected_env(current_app.instance_path)
    if not python_path:
        return jsonify({"ok": False, "error": "No environment selected."}), 400
    probe = config_generator.probe_capabilities(
        python_path, current_app.instance_path, force=force)
    manifest = {"capabilities": probe.get("capabilities"),
                "versions": probe.get("versions")}
    report = capabilities.assess(spec, manifest)
    # Chip↔env schema-flavor findings (regenerate flows pass the source chip):
    # a CR chip written by one quam-builder generation can't even be Quam.load'ed
    # by another — warn BEFORE any subprocess load fails (docs/54).
    flavor = _flavor_findings_for_folder(data.get("source_folder"), manifest)
    return jsonify({
        "ok": True, "probe_ok": probe.get("ok"), "probe_error": probe.get("error"),
        "cached": probe.get("cached"), "report": report, "flavor": flavor,
    })


def _flavor_findings_for_folder(folder, manifest) -> list[dict]:
    """CR schema-flavor mismatch findings for the chip in *folder* vs an env
    capability *manifest* — ``[]`` when folder/state/manifest is unavailable
    (unknown ≠ bad; never blocks on a read failure)."""
    if not folder:
        return []
    try:
        state = safe_io.read_json(Path(folder) / "state.json")
    except (OSError, ValueError):
        return []
    if not isinstance(state, dict):
        return []
    return capabilities.flavor_findings(state, manifest)


@bp.route("/generate/allocate", methods=["POST"])
def generate_allocate():
    """Dry-run channel allocation for a spec; returns the port assignment."""
    data = request.get_json(silent=True) or {}
    spec = data.get("spec")

    errors = config_generator.validate_spec(spec)
    if errors:
        return jsonify({"ok": False, "errors": errors}), 400

    python_path = config_generator.get_selected_env(current_app.instance_path)
    if not python_path:
        return jsonify({"ok": False, "error": "No environment selected."}), 400

    work_dir = Path(tempfile.mkdtemp(prefix="quamgen_alloc_"))
    try:
        outcome = config_generator.run_generator(
            python_path, "allocate", spec, work_dir, timeout=120
        )
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)
    return jsonify(outcome)


def _ingest_abs_path(raw: str) -> tuple[str, str | None]:
    """Normalize a user-supplied folder path from a build request: expand ``~``
    and require an ABSOLUTE result. Returns ``(path, error)``. Audit-proven
    failure modes this closes: ``~/chips`` built into a literal ``./~`` dir,
    and a Windows path replayed from localStorage onto a POSIX server
    silently built into ``$CWD/D:\\builds``."""
    try:
        p = Path(raw).expanduser()
    except RuntimeError:                # "~" with no resolvable home
        p = Path(raw)
    if not p.is_absolute():
        return raw, ("not an absolute path — use an absolute path, "
                     "e.g. /Users/you/chips/out")
    return str(p), None


def _build_output_guard(output_path: str) -> dict | None:
    """A needs_confirm payload if building into *output_path* would clobber an
    existing chip or ingest stray JSON, else None. Two hazards: (1) an EXISTING chip
    (state.json present) would be silently OVERWRITTEN with no backup; (2) QUAM's
    loader RECURSIVELY ingests every .json under the folder (rglob, dot-dirs skipped),
    so stray .json — including in subfolders (experiment archives, datasets) — would
    corrupt the built state (the old guard only checked the top level + exempted the
    state/wiring pair, so an existing chip slipped through silently)."""
    out = Path(output_path)
    if not out.is_dir():
        return None
    stray = sorted({
        p.relative_to(out).as_posix() for p in out.rglob("*.json")
        if not any(part.startswith(".") for part in p.relative_to(out).parts)
        and p.relative_to(out).as_posix() not in ("state.json", "wiring.json")
    })
    existing_chip = (out / "state.json").exists()
    if not stray and not existing_chip:
        return None
    parts = []
    if existing_chip:
        parts.append("This folder already contains a chip (state.json + wiring.json) "
                     "that would be OVERWRITTEN with no backup.")
    if stray:
        parts.append("QUAM's loader reads every .json under a folder recursively, so "
                     "these would corrupt the generated state: " + ", ".join(stray[:20]))
    return {"ok": False, "needs_confirm": True, "conflict_files": stray,
            "existing_chip": existing_chip, "error": " ".join(parts)}


@bp.route("/generate/build", methods=["POST"])
def generate_build():
    """Build state.json + wiring.json from a spec into the chosen folder.

    ``scripts_dir`` (optional) additionally exports the editable Python
    bundle (core/script_emitter.py) there after a successful build —
    best-effort: an emission failure lands in ``scripts_error``, never
    fails the build itself.
    """
    data = request.get_json(silent=True) or {}
    spec = data.get("spec")
    output_path = (data.get("output_path") or "").strip()
    scripts_dir = (data.get("scripts_dir") or "").strip()

    errors = config_generator.validate_spec(spec)
    if errors:
        return jsonify({"ok": False, "errors": errors}), 400
    if not output_path:
        return jsonify({"ok": False, "error": "No output folder given."}), 400
    output_path, path_err = _ingest_abs_path(output_path)
    if path_err:
        return jsonify({"ok": False, "error": f"Output folder: {path_err}"}), 400
    if scripts_dir:
        scripts_dir, path_err = _ingest_abs_path(scripts_dir)
        if path_err:
            return jsonify({"ok": False,
                            "error": f"Scripts folder: {path_err}"}), 400

    python_path = config_generator.get_selected_env(current_app.instance_path)
    if not python_path:
        return jsonify({"ok": False, "error": "No environment selected."}), 400

    # Capability guard (independent of the stray-JSON force below — one ack must
    # never collapse two gates). Blockers can't be overridden (the build would
    # crash); degrades need an explicit `ack_degrades`. Fail-open if the env
    # can't be probed (a transient probe failure shouldn't block a valid build —
    # the build itself will surface any real error).
    probe = config_generator.probe_capabilities(python_path, current_app.instance_path)
    report = capabilities.assess(
        spec, {"capabilities": probe.get("capabilities"),
               "versions": probe.get("versions")})
    if report["manifest_ok"]:
        if report["blockers"]:
            return jsonify({
                "ok": False, "capability_blockers": report["blockers"],
                "error": ("This environment can't build part of this chip. "
                          "Fix the environment or change the design."),
            }), 400
        if report["warnings"] and not bool(data.get("ack_degrades")):
            return jsonify({
                "ok": False, "needs_confirm": True, "confirm_kind": "capability",
                "capability_warnings": report["warnings"],
                "error": ("Some requested features can't be built in this "
                          "environment and will be skipped or downgraded."),
            })

    # Output-folder guard: QUAM's loader reads *every* .json in a folder, so a
    # stray file there would corrupt the state.json the build is about to
    # write. Block on any non-state/wiring .json unless the user forces it.
    if not bool(data.get("force")):
        guard = _build_output_guard(output_path)
        if guard is not None:
            return jsonify(guard)

    outcome = config_generator.run_generator(
        python_path, "build", spec, Path(output_path), timeout=600
    )

    # Optional editable-scripts export (customer requirement: "generate/
    # populate python scripts in a different user-defined folder"). Runs
    # app-side from the same spec + the build's allocation — pure templating,
    # no QM stack — and never fails a successful build.
    if scripts_dir and outcome.get("ok"):
        try:
            from quam_state_manager.core import script_emitter
            result = outcome.get("result") or {}
            bundle = script_emitter.emit_bundle(
                spec,
                result.get("allocation") or {},
                result.get("versions") or probe.get("versions") or {},
                chip_name=Path(output_path).name or "chip",
            )
            outcome["scripts"] = {
                "dir": scripts_dir,
                "files": script_emitter.write_bundle(Path(scripts_dir), bundle),
            }
        except Exception as exc:  # noqa: BLE001 — best-effort side artefact
            logger.warning("script bundle emission failed: %s", exc)
            outcome["scripts_error"] = str(exc)
    return jsonify(outcome)


@bp.route("/regenerate/reconstruct", methods=["POST"])
def regenerate_reconstruct():
    """Reconstruct a build spec from an existing chip (the loaded chip by
    default, or an explicit ``folder``) to pre-fill the Re-generate wizard."""
    data = request.get_json(silent=True) or {}
    # Prefer the WORKING COPY (like the Config Viewer's _ctx_path), so a
    # reconstruct carries the user's in-app edits instead of the stale live files.
    folder = (data.get("folder") or "").strip() or _ctx_path()
    if not folder:
        return jsonify({"ok": False, "error": "No chip loaded and no folder given."}), 400
    # The exact-spec sidecar lives in the chip's REAL folder, never in the
    # working copy — offer the live folder as a fallback lookup (hash-gated
    # against the working-copy content, so it only applies while equivalent).
    _ctx = _active_ctx()
    _live = (_ctx or {}).get("path")
    sidecar_dirs = (str(_live),) if _live and str(_live) != str(folder) else ()
    try:
        rec = regenerate.reconstruct_from_folder(folder, sidecar_dirs=sidecar_dirs)
    except (OSError, ValueError) as exc:
        return jsonify({"ok": False, "error": f"Could not read {folder}: {exc}"}), 400
    except Exception as exc:  # noqa: BLE001 — users hand-edit state/wiring
        # freely (docs/72); an unexpected shape must degrade to an honest
        # message, never a blank wizard on a raw 500.
        logger.exception("reconstruct failed on %s", folder)
        return jsonify({"ok": False,
                        "error": f"Could not reconstruct a spec from {folder}: "
                                 f"{type(exc).__name__}: {exc}"}), 500
    ident = _active_chip_identity()
    # Chip↔env flavor mismatch warnings ride the notes so the wizard shows them
    # before the user reaches the build step (the probe is version-keyed cached
    # — cheap after the env panel's first check).
    notes = list(rec.notes)
    flavor: list[dict] = []
    python_path = config_generator.get_selected_env(current_app.instance_path)
    if python_path:
        probe = config_generator.probe_capabilities(
            python_path, current_app.instance_path)
        if probe.get("ok"):
            flavor = _flavor_findings_for_folder(
                folder, {"capabilities": probe.get("capabilities"),
                         "versions": probe.get("versions")})
            notes.extend(f"[env {f['level']}] {f['message']}" for f in flavor)
    return jsonify({
        "ok": True,
        "spec": rec.spec,
        "mixed_gates": rec.mixed_gates,
        "notes": notes,
        "flavor": flavor,
        "source_folder": str(folder),
        "source_name": ident["name"] if ident else Path(folder).name,
    })


@bp.route("/regenerate/build", methods=["POST"])
def regenerate_build():
    """Rebuild an existing chip from an (edited) spec into a NEW folder, then
    merge the source chip's calibrated values back on. Returns the build outcome
    with a ``merge`` transparency block (carried / grafted / residual_lost)."""
    data = request.get_json(silent=True) or {}
    spec = data.get("spec")
    output_path = (data.get("output_path") or "").strip()
    # Working copy (see regenerate_reconstruct) so the value-merge carries in-app
    # edits, not the stale live files.
    source_folder = (data.get("source_folder") or "").strip() or _ctx_path()
    # Populate-protect inputs (docs/72): the wizard's hydration-time populate
    # snapshot + explicitly-touched cells. Absent ⇒ legacy merge behavior.
    populate_baseline = data.get("populate_baseline")
    if not isinstance(populate_baseline, dict):
        populate_baseline = None
    populate_touched = data.get("populate_touched")
    if not isinstance(populate_touched, list):
        populate_touched = None
    scripts_dir = (data.get("scripts_dir") or "").strip() or None

    errors = config_generator.validate_spec(spec)
    if errors:
        return jsonify({"ok": False, "errors": errors}), 400
    if not output_path:
        return jsonify({"ok": False, "error": "No output folder given."}), 400
    if not source_folder:
        return jsonify({"ok": False, "error": "No source chip to merge from."}), 400
    output_path, path_err = _ingest_abs_path(output_path)
    if path_err:
        return jsonify({"ok": False, "error": f"Output folder: {path_err}"}), 400
    source_folder, path_err = _ingest_abs_path(source_folder)
    if path_err:
        return jsonify({"ok": False, "error": f"Source folder: {path_err}"}), 400
    if scripts_dir:
        scripts_dir, path_err = _ingest_abs_path(scripts_dir)
        if path_err:
            return jsonify({"ok": False,
                            "error": f"Scripts folder: {path_err}"}), 400
    out_p, src_p = Path(output_path), Path(source_folder)
    # samefile-grounded when the output EXISTS — a case-variant spelling on a
    # case-insensitive FS bypasses resolve()-equality and the rebuild would
    # write INTO the source chip (calibrations silently lost). resolve()
    # equality stays as the cheap check for a not-yet-existing output.
    if (path_match.same_folder(out_p, src_p) if out_p.exists()
            else out_p.resolve() == src_p.resolve()):
        return jsonify({
            "ok": False,
            "error": "Output folder must differ from the source chip folder.",
        }), 400

    python_path = config_generator.get_selected_env(current_app.instance_path)
    if not python_path:
        return jsonify({"ok": False, "error": "No environment selected."}), 400

    # Capability guard (independent of the stray-JSON force). Same contract as
    # /generate/build: blockers refuse; degrades need `ack_degrades`.
    probe = config_generator.probe_capabilities(python_path, current_app.instance_path)
    report = capabilities.assess(
        spec, {"capabilities": probe.get("capabilities"),
               "versions": probe.get("versions")})
    if report["manifest_ok"]:
        if report["blockers"]:
            return jsonify({
                "ok": False, "capability_blockers": report["blockers"],
                "error": ("This environment can't rebuild part of this chip. "
                          "Fix the environment or change the design."),
            }), 400
        if report["warnings"] and not bool(data.get("ack_degrades")):
            return jsonify({
                "ok": False, "needs_confirm": True, "confirm_kind": "capability",
                "capability_warnings": report["warnings"],
                "error": ("Some requested features can't be rebuilt in this "
                          "environment and will be skipped or downgraded."),
            })

    # Same stray-.json guard as /generate/build — QUAM's loader reads every
    # .json in a folder, so a stray file would corrupt the generated state.
    if not bool(data.get("force")):
        guard = _build_output_guard(output_path)
        if guard is not None:
            return jsonify(guard)

    outcome = regenerate.run_regenerate(
        python_path, source_folder, spec, Path(output_path), timeout=600,
        populate_baseline=populate_baseline,
        populate_touched=populate_touched,
        scripts_dir=scripts_dir,
    )
    return jsonify(outcome)


# Post-build preview seeds: /generate/preview-config stashes the generated
# config keyed by folder so /generate/load can transplant it onto the freshly
# activated store without a second subprocess spawn. Configs can be MBs —
# keep the stash tiny and short-lived. Module-level like _wc_count_cache.
_PREVIEW_SEEDS: dict[str, dict] = {}
_PREVIEW_SEEDS_LOCK = threading.Lock()
_PREVIEW_SEED_MAX = 4
_PREVIEW_SEED_TTL_S = 900.0  # 15 min; wizard build→load is usually seconds


def _seed_key(folder) -> str:
    # per-OS canonical folder identity — the same normalization
    # working_copy.key_for hashes (case-folded only on Windows/macOS).
    return path_match.fs_key(folder)


def _stash_preview_seed(folder, files_hash: str, config, meta: dict) -> None:
    if not isinstance(config, dict):
        return
    with _PREVIEW_SEEDS_LOCK:
        now = time.monotonic()
        for k in [k for k, v in _PREVIEW_SEEDS.items()
                  if now - v["at"] > _PREVIEW_SEED_TTL_S]:
            _PREVIEW_SEEDS.pop(k, None)
        while len(_PREVIEW_SEEDS) >= _PREVIEW_SEED_MAX:
            _PREVIEW_SEEDS.pop(next(iter(_PREVIEW_SEEDS)))
        _PREVIEW_SEEDS[_seed_key(folder)] = {
            "hash": files_hash, "config": config, "meta": meta,
            "at": now,
        }


def _pop_preview_seed(folder) -> dict | None:
    with _PREVIEW_SEEDS_LOCK:
        entry = _PREVIEW_SEEDS.pop(_seed_key(folder), None)
    if entry and time.monotonic() - entry["at"] <= _PREVIEW_SEED_TTL_S:
        return entry
    return None


def _peek_preview_seed(folder) -> dict | None:
    """Like ``_pop`` but leaves the seed in place — the wizard export downloads
    the previewed config repeatedly (json + py) without consuming the transplant
    seed that a subsequent "Load into app" relies on."""
    with _PREVIEW_SEEDS_LOCK:
        entry = _PREVIEW_SEEDS.get(_seed_key(folder))
    if entry and time.monotonic() - entry["at"] <= _PREVIEW_SEED_TTL_S:
        return entry
    return None


@bp.route("/generate/preview-config", methods=["POST"])
def generate_preview_config():
    """Preview generate_config() of a just-built folder WITHOUT loading it.

    Closes the build→inspect loop right inside the wizard: no Load-into-app,
    no trip to the Config Viewer, no second Regenerate. The result is also
    stashed so a subsequent /generate/load seeds the new store's cache.
    """
    data = request.get_json(silent=True) or {}
    folder = (data.get("path") or "").strip()
    if not folder:
        return jsonify({"ok": False, "error": "No folder given."}), 400
    folder_path = Path(folder)
    if not (folder_path / "state.json").exists():
        return jsonify({"ok": False, "error": f"state.json not found in {folder}"}), 400
    python_path = config_generator.get_selected_env(current_app.instance_path)
    if not python_path:
        return jsonify({"ok": False, "error": "No environment selected."}), 400

    # Hash the files BEFORE the subprocess reads them: this becomes the
    # transplant guard + staleness basis if the user clicks "Load into app".
    try:
        f_state, f_wiring = safe_io.read_state_wiring(folder_path)
        files_hash = working_copy.content_hash(f_state, f_wiring)
    except (OSError, ValueError) as exc:
        return jsonify({"ok": False, "error": f"could not read state files: {exc}"}), 400

    outcome = config_generator.run_config_preview(python_path, folder_path)
    if not (outcome.get("ok") and outcome.get("result")):
        return jsonify({
            "ok": False,
            "error": outcome.get("error") or "previewer reported an error",
            "traceback": (outcome.get("result") or {}).get("traceback") or "",
        }), 502

    result = outcome["result"]
    meta = {
        "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "versions": result.get("versions") or {},
        "warnings": result.get("warnings") or [],
        "qubits": result.get("qubits") or [],
        "qubit_pairs": result.get("qubit_pairs") or [],
    }
    _stash_preview_seed(folder_path, files_hash, result.get("config"), meta)
    return jsonify({"ok": True, "config": result.get("config"), "meta": meta})


@bp.route("/generate/preview-pulses")
def generate_preview_pulses():
    """List every 2Q-gate operation in the just-previewed config (supercritical
    feedback: show the pulse shapes of the default-seeded CZ gate library right
    in the wizard, like readout pulses). Gated on a warm preview seed — the
    gallery only appears after Preview config has run."""
    folder = (request.args.get("path") or "").strip()
    if not folder:
        return jsonify({"ok": False, "error": "No folder given."}), 400
    seed = _peek_preview_seed(Path(folder))
    if seed is None:
        return jsonify({"ok": False,
                        "error": "No previewed config for this folder — run "
                                 "Preview config first."}), 409
    ops = config_view.all_pair_gate_operations(seed.get("config") or {})
    return jsonify({"ok": True, "ops": ops})


@bp.route("/generate/preview-pulse-waveform")
def generate_preview_pulse_waveform():
    """One 2Q-gate op's waveform traces from the stashed preview config — the
    same payload shape as the Config Viewer's /pair/<n>/waveform/<op>, so the
    client renders both with the same plot code."""
    folder = (request.args.get("path") or "").strip()
    element = (request.args.get("element") or "").strip()
    op_name = (request.args.get("op") or "").strip()
    if not folder or not element or not op_name:
        return jsonify({"ok": False, "error": "path, element and op required"}), 400
    seed = _peek_preview_seed(Path(folder))
    if seed is None:
        return jsonify({"ok": False,
                        "error": "No previewed config for this folder — run "
                                 "Preview config first."}), 409
    payload = config_view.waveform_for_element_op(
        seed.get("config") or {}, element, op_name)
    if payload is None:
        return jsonify({"ok": False,
                        "error": f"no waveform for {op_name!r} on {element!r}"}), 404
    payload["ok"] = True
    return jsonify(payload)


@bp.route("/generate/export-config", methods=["GET"])
def generate_export_config():
    """Download a just-previewed build's config as a drop-in file for bare QUA.

    Serves the config the wizard's "Preview config" stashed (keyed by folder),
    so a bare-QUA user gets ``config.json`` / ``config.py`` straight from the
    build result — no "Load into app" round trip. Gated on a warm preview seed:
    the download links only appear once Preview config has run, so a cold link
    (expired/never previewed) is a clean 409 rather than a silent subprocess.
    """
    from quam_state_manager.core import config_export as cfgexp
    from quam_state_manager.core.history import chip_name_for

    folder = (request.args.get("path") or "").strip()
    fmt = (request.args.get("format") or "json").lower()
    if not folder:
        return jsonify({"ok": False, "error": "No folder given."}), 400
    seed = _peek_preview_seed(folder)
    if seed is None or not isinstance(seed.get("config"), dict):
        return jsonify({
            "ok": False,
            "error": "No previewed config to export — click \"Preview config\" first.",
        }), 409

    # Same chip-name derivation as the Config Viewer export (chip_name_for, not
    # a naive basename) so the SAME config downloads under the SAME filename
    # whichever surface the user exports from.
    chip = chip_name_for(Path(folder))
    stem = cfgexp.safe_stem(chip)
    try:
        if fmt == "py":
            body = cfgexp.python_module_source(
                seed["config"], chip=chip, meta=seed.get("meta"))
            mem = io.BytesIO(body.encode("utf-8"))
            mime, ext = "text/x-python", "py"
        else:
            mem = io.BytesIO(cfgexp.json_bytes(seed["config"]))
            mime, ext = "application/json", "json"
    except (TypeError, ValueError) as exc:
        logger.exception("Wizard config export serialization failed")
        return jsonify({"ok": False, "error": f"serialize failed: {exc}"}), 500
    mem.seek(0)
    return send_file(mem, mimetype=mime, as_attachment=True,
                     download_name=f"config_{stem}.{ext}")


@bp.route("/generate/load", methods=["POST"])
def generate_load():
    """Activate a generated quam_state folder as the live context."""
    data = request.get_json(silent=True) or {}
    folder = (data.get("path") or "").strip()
    if not folder:
        return jsonify({"ok": False, "error": "No folder given."}), 400
    try:
        _activate_quam(folder)
    except (FileNotFoundError, ValueError, OSError) as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    _remember_load_path(folder)

    # Transplant a recent post-build preview onto the freshly loaded store so
    # detail pages show the generated config without a second subprocess
    # spawn. Only when content provably matches: a hash mismatch means the
    # files changed between preview and load (or an old working copy with
    # prior edits was rehydrated) — seeding then would mislabel a non-preview
    # as fresh, so we skip and let the user Regenerate.
    seed = _pop_preview_seed(folder)
    if seed is not None:
        store = _store()
        if store is not None and store.generated_config is None:
            with store._lock:
                cur = working_copy.content_hash(store.state, store.wiring)
                if cur == seed["hash"]:
                    store.generated_config = seed["config"]
                    store.generated_config_meta = {
                        **seed["meta"],
                        "basis_hash": cur,
                        "unsaved_at_generate": False,
                        "seeded": True,
                    }
                else:
                    logger.info(
                        "Preview seed skipped for %s: files changed between "
                        "preview and load", folder,
                    )
    return jsonify({"ok": True, "redirect": url_for("main.explorer")})


# ======================================================================
# Config Viewer — offline preview of machine.generate_config()
# ======================================================================
#
# Reads the working-copy state.json + wiring.json, calls generate_config()
# in a subprocess (the chosen "Generate Config" conda env from the wizard,
# since that's the only env with the QM stack installed), and surfaces
# three views:
#
#   - /config              full-config browser (Surface C)
#   - /qubit/<n>/config    per-qubit slice  (Surface B)
#   - /pair/<n>/config     per-pair slice   (Surface B)
#   - /qubit|pair/<n>/waveform/<op>   JSON for Plotly  (Surface A)
#
# Refresh is button-only: POST /config/regenerate re-runs the subprocess.
# See docs/30_config_viewer.md.


def _qubit_target_prefix(name: str) -> str:
    """QUAM-side element prefix for a state-manager qubit name.

    State-manager uses ``"qA1"``; quam-builder strips the leading ``q``
    when building element keys, producing elements like ``"qA1.xy"``.
    Today both conventions land on the same string, so this is a no-op
    indirection — keep it explicit so we can adjust if quam-builder
    changes the rule.
    """
    return name


def _pair_target_prefix(name: str) -> str:
    """QUAM-side element prefix for a state-manager pair name.

    State-manager uses ``"qA1-A2"`` (or ``"qA1-qA2"``). The element key
    in the generated config follows the same pattern; we forward as-is.
    """
    return name


def _pair_qubit_names(store: QuamStore, name: str):
    """Resolve a pair name to its ``(control, target)`` qubit names.

    The generated config names 2Q gates by the two qubits (``cr_q0_q4``,
    ``cz_..._qA1``), never by the pair name, so config_view needs the qubit
    names. Prefer the pair's ``qubit_control`` / ``qubit_target`` references;
    fall back to splitting the pair name (``"qA2-qA1"`` or ``"q0-4"``, where
    the target may drop the control's non-digit prefix).
    """
    pair = (store.merged.get("qubit_pairs") or {}).get(name) or {}

    def _resolve(ref):
        if isinstance(ref, str):
            return ref.rsplit("/", 1)[-1] if ref.startswith("#") else ref
        if isinstance(ref, dict):
            return ref.get("id") or ref.get("name")
        return None

    control = _resolve(pair.get("qubit_control"))
    target = _resolve(pair.get("qubit_target"))
    if control and target:
        return control, target

    # Fallback: parse the pair name. "q0-4" -> control "q0", target "q4".
    parts = name.split("-", 1)
    if len(parts) == 2:
        left, right = parts
        m = re.match(r"^(\D*)\d", left)
        if m and right[:1].isdigit():
            right = m.group(1) + right
        control = control or left
        target = target or right
    return control, target


def _config_state_hash(store: QuamStore) -> str:
    """Content hash of the store's in-memory state+wiring (canonical JSON)."""
    with store._lock:
        return working_copy.content_hash(store.state, store.wiring)


def _config_stale(store: QuamStore) -> bool:
    """True when the cached generated config no longer matches the in-memory state.

    The basis hash is recorded at regenerate time from the working-copy
    FILES the previewer subprocess actually read (``meta["basis_hash"]``),
    so unsaved edits at regenerate time honestly read as stale, and an
    undo back to the generated content reads as fresh again.
    """
    if not store.generated_config or not store.generated_config_meta:
        return False  # nothing cached -> nothing to be stale
    basis = store.generated_config_meta.get("basis_hash")
    if not basis:
        return True  # unknown basis: cannot prove freshness
    return _config_state_hash(store) != basis


@bp.route("/config", methods=["GET"])
def config_browser():
    """Top-level Config Viewer page (Surface C)."""
    store = _store()
    if not store:
        return render_template("_status.html", message="No state loaded", level="warning")
    cfg = store.generated_config
    top_keys = config_view.top_level_keys(cfg) if cfg else []
    env_selected = bool(config_generator.get_selected_env(current_app.instance_path))
    template = "_config.html" if _is_htmx() else "config.html"
    return render_template(
        template,
        **_ctx(
            page="config_browser",
            config=cfg,
            meta=store.generated_config_meta,
            top_level_keys=top_keys,
            config_stale=_config_stale(store),
            env_selected=env_selected,
        ),
    )


@bp.route("/config/section/<path:key>", methods=["GET"])
def config_section(key):
    """One top-level config section's pretty JSON, loaded lazily when its
    <details> is expanded. The full Config Viewer used to dump EVERY section's
    multi-MB ``tojson`` into the initial swap — on a 21-qubit chip the
    waveforms/integration_weights arrays froze the browser at swap time. Now the
    page ships only summaries + placeholders and fetches each section on demand."""
    store = _store()
    cfg = store.generated_config if store else None
    if not cfg or key not in cfg:
        return render_template("_status.html",
                               message="That config section is not available.",
                               level="warning"), 404
    return render_template("_config_section.html", section=cfg[key])


@bp.route("/config/export", methods=["GET"])
def config_export_file():
    """Download the loaded chip's generated config as a drop-in file for bare QUA.

    ``?format=json`` → the raw config dict (``json.load`` → ``qmm.open_qm``);
    ``?format=py`` → a standalone ``config.py`` (``import config`` → ``open_qm``,
    no QUAM needed). Plain download (no hx-* so HTMX won't swap). Staleness is
    surfaced honestly — a stale export carries a header warning + the UI hint —
    but never blocks: exporting the last-good config is legitimate.
    """
    from quam_state_manager.core import config_export as cfgexp
    from quam_state_manager.core.history import chip_name_for

    store = _store()
    if not store or not store.generated_config:
        return render_template(
            "_status.html",
            message=("No generated config to export yet — click "
                     "\"Generate config from loaded chip\" on the Config Viewer first."),
            level="warning",
        ), 404

    fmt = (request.args.get("format") or "json").lower()
    path = _active_path()
    chip = chip_name_for(Path(path)) if path else None
    stem = cfgexp.safe_stem(chip)
    try:
        if fmt == "py":
            body = cfgexp.python_module_source(
                store.generated_config, chip=chip,
                meta=store.generated_config_meta, stale=_config_stale(store))
            mem = io.BytesIO(body.encode("utf-8"))
            mime, ext = "text/x-python", "py"
        else:
            mem = io.BytesIO(cfgexp.json_bytes(store.generated_config))
            mime, ext = "application/json", "json"
    except (TypeError, ValueError) as exc:
        logger.exception("Config export serialization failed")
        return render_template(
            "_status.html", message=f"Could not serialize config: {exc}", level="error",
        ), 500
    mem.seek(0)
    return send_file(mem, mimetype=mime, as_attachment=True,
                     download_name=f"config_{stem}.{ext}")


@bp.route("/config/preview", methods=["POST"])
def config_preview():
    """Read-only preview of a dropped config.json, with referential diagnostics."""
    if request.content_length and request.content_length > _PREVIEW_MAX_BYTES:
        return render_template(
            "_status.html", message="Dropped config is too large to preview", level="error",
        ), 413
    payload = request.get_json(silent=True) or {}
    config = payload.get("config")
    label = str(payload.get("label") or "config.json")
    if not isinstance(config, dict):
        return render_template(
            "_status.html",
            message="Dropped file must be a JSON object (a QM config dict)",
            level="error",
        ), 400
    try:
        findings = diagnostics.lint_config(config)
        top_keys = config_view.top_level_keys(config)
    except Exception:
        logger.exception("Failed to build config preview")
        return render_template(
            "_status.html", message="Could not read the dropped config.json", level="error",
        ), 400
    return render_template(
        "_config_preview.html",
        label=label,
        config=config,
        top_level_keys=top_keys,
        findings=findings,
        diag_summary=diagnostics.summarize(findings),
        allow_jump=False,
    )


# ======================================================================
# Diagnostics — "what is cracked / misaligned?"
# ======================================================================

_DIAG_RANK = {"error": 0, "warning": 1, "info": 2}


def _env_schema_findings(store: QuamStore) -> list:
    """Env-match findings for the active chip against the SELECTED env.

    Request-path safe: reads only the warm (stat-keyed) schema-manifest cache
    — ``cached_only=True`` NEVER spawns a subprocess — and the per-store
    memoized analysis (one walk per mutation). Cold cache / no env → []
    (the /diagnostics env card renders its "Probe now" affordance instead).
    """
    from quam_state_manager.core import state_env_validate
    try:
        policy = getattr(store, "type_policy", None)
        manifest = policy.manifest if policy is not None else None
        if manifest is None:
            return []
        analysis = state_env_validate.analysis_for_store(store, manifest)
        versions = manifest.get("versions") or {}
        label = " ".join(f"{k} {v}" for k, v in sorted(versions.items())
                         if k in ("quam", "quam_builder") and v)
        # docs/94 fix 3: while a schema probe for the selected env is in
        # flight, an unknown class is a not-yet-known — downgrade to warning
        # ("probing the environment…") for the seconds the probe needs.
        probing = False
        try:
            _pp = config_generator.get_selected_env(current_app.instance_path)
            if _pp:
                with _schema_warm_lock:
                    probing = any(k.startswith(_pp + "|")
                                  for k in _schema_warm_inflight)
        except Exception:  # noqa: BLE001
            probing = False
        return state_env_validate.to_diag_findings(analysis, env_label=label,
                                                   probing=probing)
    except Exception:  # noqa: BLE001 — env findings must never break /diagnostics
        logger.warning("env-schema findings failed", exc_info=True)
        return []


def _active_chip_findings(store: QuamStore) -> list:
    """Lint the active chip's state (+ cached generated config + env match),
    errors first."""
    findings = diagnostics.lint_state(store)
    if store.generated_config:
        findings = findings + diagnostics.lint_config(store.generated_config)
    findings = findings + _env_schema_findings(store)
    findings.sort(key=lambda f: _DIAG_RANK.get(f.severity, 3))
    return findings


def _env_card_state(store: QuamStore) -> dict:
    """Context for the /diagnostics env-match card: selection, warmth,
    versions, in-flight probe status. Cheap (stat + in-memory)."""
    inst = current_app.instance_path
    python_path = None
    try:
        python_path = config_generator.get_selected_env(inst)
    except Exception:  # noqa: BLE001
        pass
    policy = getattr(store, "type_policy", None)
    manifest = policy.manifest if policy is not None else None
    with _schema_warm_lock:
        probing = any(k.startswith((python_path or "\0") + "|")
                      for k in _schema_warm_inflight)
    missing = list((manifest or {}).get("missing_classes") or [])
    return {
        "selected": python_path,
        "selected_exists": bool(python_path and Path(python_path).is_file()),
        "warm": manifest is not None,
        "probing": probing,
        "versions": (manifest or {}).get("versions") or {},
        "missing_classes": missing,
    }


def _types_card_state(ctx: dict | None) -> dict | None:
    """Context for the /diagnostics "Types & values" card (docs/78).

    Reads the SAME mutation_seq-memoized scan the alert and the banner use, so
    the page costs nothing extra. Unlike the alert this is NOT delta-gated: a
    dismissed anomaly is silenced as a prompt, never hidden from the report.
    """
    if not ctx or ctx.get("store") is None:
        return None
    try:
        from quam_state_manager.core import type_fix as _tf
        store = ctx["store"]
        memo = _type_alarm_memo(ctx)
        editable = (ctx.get("origin") or "live") == "live"
        card = _tf.alert_summary(_alarm_plan(ctx, memo),
                                 memo["env_findings"], memo["paths"])
        card["editable"] = editable
        card["strnum"]["first"] = memo["paths"][0] if memo["paths"] else ""
        policy = getattr(store, "type_policy", None)
        card["env"]["warm"] = bool(policy is not None and policy.manifest)
        # docs/79: the library's own movements + what the user has taught SM.
        card["env_change"], card["taught"] = _env_change_summary(store)
        return card
    except Exception:  # noqa: BLE001 — the card must never break /diagnostics
        logger.warning("types-card state failed", exc_info=True)
        return None


def _env_change_summary(store) -> tuple[dict | None, int]:
    """(env transition summary, taught-type count) — both cheap reads."""
    try:
        from quam_state_manager.core import state_env_baseline as _seb
        from quam_state_manager.core import type_verdicts as _tv
        manifest, _versions = _env_manifest_and_versions(store)
        if not manifest:
            return None, 0
        inst = current_app.instance_path
        transition = _seb.env_transition(inst, manifest)
        summary = None
        if transition:
            diff = transition.get("diff") or {}
            summary = {
                "first": transition.get("first"),
                "changed": transition.get("changed"),
                "count": diff.get("total") or 0,
                "from_label": transition.get("from_label") or "",
                "to_label": transition.get("to_label") or "",
                "dismissed": bool(transition.get("dismissed")),
            }
        taught = len(_tv.resolve_for_manifest(inst, manifest))
        return summary, taught
    except Exception:  # noqa: BLE001 — baseline history must never break the card
        logger.warning("env-change summary failed", exc_info=True)
        return None, 0


@bp.route("/diagnostics/types-card")
def diagnostics_types_card():
    """Re-render just the types card (after a repair, the counts must drop
    immediately — same self-refresh contract as the env card)."""
    ctx = _active_ctx()
    if not ctx or ctx.get("store") is None:
        return "", 204
    return render_template("_diagnostics_types.html",
                           types_card=_types_card_state(ctx))


@bp.route("/diagnostics")
def diagnostics_view():
    """Full diagnostics report for the active chip."""
    store = _store()
    if not store:
        return render_template("_empty_state.html", page="diagnostics")
    findings = _active_chip_findings(store)
    template = "_diagnostics.html" if _is_htmx() else "diagnostics.html"
    return render_template(
        template,
        **_ctx(
            page="diagnostics",
            findings=findings,
            diag_summary=diagnostics.summarize(findings),
            diag_catalog=diagnostics.check_catalog(),
            allow_jump=True,
            has_config=bool(store.generated_config),
            env_card=_env_card_state(store),
            types_card=_types_card_state(_active_ctx()),
            # The config-reference findings were linted against the cached
            # generated config, which may predate the latest edits — surface
            # that so a stale config doesn't pass off old findings as current
            # (the one staleness primitive, shared with the Config Viewer).
            config_stale=_config_stale(store),
        ),
    )


@bp.route("/diagnostics/env-probe", methods=["POST"])
def diagnostics_env_probe():
    """Kick off the schema probe for the active chip in a background thread
    (the explicit "Probe environment" button; ``force=1`` re-probes even a
    warm cache — editable installs). Poll /diagnostics/env-card for status."""
    ctx = _active_ctx()
    store = ctx.get("store") if ctx else None
    if not store:
        return jsonify(ok=False, error="No active context"), 400
    inst = current_app.instance_path
    python_path = config_generator.get_selected_env(inst)
    if not python_path:
        return jsonify(ok=False, error="No environment selected — pick one in "
                                       "Generate Config first."), 400
    force = request.form.get("force") in ("1", "true", "True")
    from quam_state_manager.core import state_env_schema
    with store._lock:
        classes = state_env_schema.harvest_classes(store.state)
    key = python_path + "|" + ",".join(sorted(classes))
    with _schema_warm_lock:
        already = key in _schema_warm_inflight
        if not already:
            _schema_warm_inflight.add(key)
    if not already:
        live_folder = ctx.get("path")

        def _run():
            try:
                res = state_env_schema.probe_state_schema(
                    python_path, classes, inst, force=force)
                if res.get("ok") and live_folder:
                    from quam_state_manager.core import pulse_catalog, type_policy
                    manifest = {k: res[k] for k in
                                ("classes", "pulse_roster", "by_leaf",
                                 "missing_classes", "versions")}
                    store.type_policy = type_policy.load_policy(
                        inst, live_folder, manifest)
                    store._type_manifest_env = python_path
                    if manifest.get("pulse_roster"):
                        pulse_catalog.apply_env_overlay(manifest["pulse_roster"])
            except Exception:  # noqa: BLE001
                logger.warning("env probe failed", exc_info=True)
            finally:
                with _schema_warm_lock:
                    _schema_warm_inflight.discard(key)

        threading.Thread(target=_run, daemon=True).start()
    return jsonify(ok=True, started=not already, probing=True)


@bp.route("/diagnostics/env-card")
def diagnostics_env_card():
    """The env-match card fragment (self-polls while a probe is in flight)."""
    store = _store()
    if not store:
        return ("", 204)
    return render_template("_diagnostics_env.html",
                           env_card=_env_card_state(store), oob=False)


@bp.route("/diagnostics/summary")
def diagnostics_summary():
    """Tiny issue-count badge, lazy-loaded into the Wiring / Config headers."""
    store = _store()
    if not store:
        return ("", 204)
    findings = _active_chip_findings(store)
    return render_template("_diagnostics_badge.html", diag_summary=diagnostics.summarize(findings))


@bp.route("/diagnostics/banner")
def diagnostics_banner():
    """Auto-popped, dismissible error banner for the active chip.

    Lazily fetched into a base-level slot on every full page load (so it pops on
    chip load / switch) and on the ``diagnostics-changed`` event. Renders ONLY
    when the chip has >=1 error-severity finding — a value the QUA compiler /
    ``machine.generate_config()`` provably rejects (e.g. a waveform sample
    outside the DAC range, a missing/colliding port, a NaN) — i.e. something that
    would crash the next node run. Warnings/suggestions stay in the quiet tray
    badge; only crash-class errors get the banner. Empty (204) otherwise."""
    store = _store()
    if not store:
        return ("", 204)
    summary = diagnostics.summarize(_active_chip_findings(store))
    if not summary.get("error"):
        return ("", 204)
    ident = _active_chip_identity()
    return render_template("_diagnostics_banner.html", diag_summary=summary,
                           active_name=ident["name"] if ident else None)


@bp.route("/diagnostics/findings.json")
def diagnostics_findings_json():
    """JSON feed driving the Explorer per-row marks, the wiring-port rings, and
    the per-tab sidebar dots.

    Splits the active chip's findings into ``value_spec`` (Explorer-jumpable
    via ``jump_path``) and ``connectivity`` (wiring ports via ``port_key``, and
    also Explorer via ``jump_path``); ``ports`` is the flat port_key list the
    diagram highlighter consumes. ``counts`` drives the two sidebar dots.
    """
    store = _store()
    empty = {"value_spec": [], "connectivity": [], "ports": [],
             "counts": {"value_spec": 0, "connectivity": 0}}
    if not store:
        return jsonify(empty)
    findings = _active_chip_findings(store)
    # r14 ⑨: value_type/value_nan were excluded (the startswith("value_spec")
    # accident), so the stored-as-text warnings never marked an Explorer row —
    # exactly the passive-detection complaint. All value_* findings with a
    # jump path now feed the marks.
    spec = [f.as_dict() for f in findings
            if f.category.startswith(("value_spec", "value_type", "value_nan",
                                      "waveform"))]
    # env_* findings carry jump_paths into the Explorer too — a field the
    # selected env's class doesn't know gets the same ⚠ row mark treatment.
    spec += [f.as_dict() for f in findings
             if f.category.startswith("env_") and f.jump_path]
    conn = [f.as_dict() for f in findings
            if f.category.startswith(("connectivity", "port_"))]
    return jsonify({
        "value_spec": spec,
        "connectivity": conn,
        "ports": [f["port_key"] for f in conn if f.get("port_key")],
        "counts": {"value_spec": len(spec), "connectivity": len(conn)},
    })


@bp.route("/diagnostics/apply-fix", methods=["POST"])
def diagnostics_apply_fix():
    """Apply a diagnostics-offered one-click fix.

    Currently the only action is ``set_pointer``: convert a literal readout
    ``downconverter_frequency`` into a JSON pointer to its paired output's
    ``upconverter_frequency`` (a deliberate float->pointer change, so the edit
    runs with ``coerce=False``). The edit lands in the working copy like any
    other, so the user still Saves / Applies it. Guarded to that one link shape.
    """
    modifier = _modifier()
    if not modifier:
        return jsonify(ok=False, error="No active context"), 400
    action = request.form.get("action", "")
    dot_path = request.form.get("dot_path", "").strip()
    pointer = request.form.get("pointer", "").strip()

    if action == "set_value":
        # r9: the frequency-consistency "Update f_01 → carrier" fix. Same
        # doctrine as set_pointer below: never trust the render-time form —
        # re-run the linter on the CURRENT store and require a live finding
        # offering EXACTLY this fix (chip identity + current values).
        value = request.form.get("value", "").strip()
        from quam_state_manager.core.diagnostics import (
            _frequency_consistency_findings,
        )
        if not any(
            (fnd.fix or {}).get("action") == "set_value"
            and (fnd.fix or {}).get("dot_path") == dot_path
            and (fnd.fix or {}).get("value") == value
            for fnd in _frequency_consistency_findings(modifier.store)
        ):
            return jsonify(ok=False, error=(
                "This fix is no longer valid for the loaded chip — the value "
                "or chip changed since Diagnostics was rendered. Reload the "
                "page.")), 409
        try:
            from quam_state_manager.core import type_policy as _tp
            modifier.set_value(dot_path, _tp.parse_value(value))
            _invalidate_engine_cache()
        except (KeyError, TypeError, ValueError, IndexError) as e:
            return jsonify(ok=False, error=str(e)), 400
        return jsonify(ok=True, tray_html=_tray_html())

    if action != "set_pointer":
        return jsonify(ok=False, error="unsupported fix action"), 400
    if not (dot_path.endswith(".downconverter_frequency")
            and pointer.startswith("#/ports/mw_outputs/")
            and pointer.endswith("/upconverter_frequency")):
        return jsonify(ok=False, error="fix is not an allowed downconverter link"), 400
    # Re-validate against the CURRENT store (not the render-time client form): re-run
    # the linter and confirm a LIVE finding still offers EXACTLY this fix. This one
    # check enforces chip identity (a fix baked for chip A won't match chip B's
    # findings after a background /load flip — contexts are app-global), current
    # wiring pairing, and paired-output/target existence (the finding is only offered
    # when the output resolves) — the gates the raw form-driven apply-fix bypassed.
    from quam_state_manager.core.diagnostics import _downconverter_findings
    if not any(
        (fnd.fix or {}).get("action") == action
        and (fnd.fix or {}).get("dot_path") == dot_path
        and (fnd.fix or {}).get("pointer") == pointer
        for fnd in _downconverter_findings(modifier.store.merged)
    ):
        return jsonify(ok=False, error=(
            "This fix is no longer valid for the loaded chip — the chip, wiring, or "
            "paired output changed since Diagnostics was rendered. Reload the page."
        )), 409
    try:
        modifier.set_value(dot_path, pointer, coerce=False)
        _invalidate_engine_cache()
    except (KeyError, TypeError, ValueError, IndexError) as e:
        return jsonify(ok=False, error=str(e)), 400
    return jsonify(ok=True, tray_html=_tray_html())


# --- Fit Audit (gate-migration triage, docs/50) ---------------------------

_FIT_AUDIT_JOB_KEY = "backlog"


def _fit_audit_targets() -> list[dict]:
    """Every auditable run across the active dataset stores (Phase-1 families)."""
    from quam_state_manager.core import fit_audit
    targets: list[dict] = []
    for fol in _active_dataset_stores():
        store, key = fol["store"], fol["key"]
        for run in store.runs_snapshot():
            fam = fit_audit.family_for(run.experiment_name)
            if fam is None:
                continue
            targets.append({
                "folder": str(run.folder_path),
                "node_name": run.experiment_name,
                "run": f"#{run.run_id} {run.experiment_name}",
                "uid": _dataset_uid(key, run.run_id),
                "family_label": fit_audit.FAMILIES[fam]["label"],
            })
    return targets


def _fit_audit_page_ctx(**extra) -> dict:
    """Shared context for the Fit-Audit page + its config re-render."""
    from quam_state_manager.core import fit_audit
    env = config_generator.get_selected_env(current_app.instance_path)
    source_root = fit_audit.get_audit_source_root(current_app.instance_path)
    targets = _fit_audit_targets()
    by_family: "OrderedDict[str, int]" = OrderedDict()
    for t in targets:
        by_family[t["family_label"]] = by_family.get(t["family_label"], 0) + 1
    job = fit_audit.get_sweep(_FIT_AUDIT_JOB_KEY)
    snap = job.snapshot() if job else None
    # A digest computed against a now-changed source root OR a different QM env is
    # stale — flag it so the confusion table can't pass off old-gate verdicts as
    # current (the gate math depends on the env's calibration code too, not just
    # the source tree).
    digest_stale = bool(snap and (
        (snap.get("source_root") or None) != (source_root or None)
        or (snap.get("env") or None) != (env or None)))
    ctx = {
        "env": env,
        "source_root": source_root,
        "families": fit_audit.FAMILIES,
        "n_targets": len(targets),
        "targets_by_family": by_family,
        "snap": snap,
        "digest_stale": digest_stale,
    }
    ctx.update(extra)
    return ctx


@bp.route("/fit-audit")
def fit_audit_view():
    """Fit-Auditor backlog-digest surface."""
    template = "_fit_audit.html" if _is_htmx() else "fit_audit.html"
    return render_template(template, **_ctx(page="fit-audit", **_fit_audit_page_ctx()))


@bp.route("/fit-audit/source-root", methods=["POST"])
def fit_audit_set_source_root():
    """Persist the analysis source-root the audit replays against + validate it, so a
    typo/blank can't silently anchor verdicts to the wrong gate; re-render page."""
    from quam_state_manager.core import fit_audit
    sr = request.form.get("source_root", "").strip()
    ok, msg = fit_audit.validate_source_root(sr)
    fit_audit.set_audit_source_root(current_app.instance_path, sr)
    extra = {"source_root_ok": msg} if ok else {"source_root_error": msg}
    return render_template("_fit_audit.html", **_ctx(page="fit-audit", **_fit_audit_page_ctx(**extra)))


@bp.route("/fit-audit/cancel", methods=["POST"])
def fit_audit_cancel():
    """Request cancellation of the running sweep (stops after the current run)."""
    from quam_state_manager.core import fit_audit
    job = fit_audit.get_sweep(_FIT_AUDIT_JOB_KEY)
    if not job:
        return ("", 204)
    job.cancel()
    return render_template("_fit_audit_digest.html", snap=job.snapshot())


@bp.route("/fit-audit/run", methods=["POST"])
def fit_audit_run():
    """Kick off a background backlog sweep; return the (self-polling) digest partial."""
    from quam_state_manager.core import fit_audit
    env = config_generator.get_selected_env(current_app.instance_path)
    if not env:
        return render_template("_status.html",
                               message="No QM environment selected", level="warning"), 400
    source_root = fit_audit.get_audit_source_root(current_app.instance_path)
    targets = _fit_audit_targets()
    if not targets:
        return render_template("_status.html",
                               message="No auditable runs in the workspace", level="warning")
    job = fit_audit.start_sweep(_FIT_AUDIT_JOB_KEY, targets, env, source_root)
    return render_template("_fit_audit_digest.html", snap=job.snapshot())


@bp.route("/fit-audit/status")
def fit_audit_status():
    """Poll endpoint: current sweep snapshot as the digest partial."""
    from quam_state_manager.core import fit_audit
    job = fit_audit.get_sweep(_FIT_AUDIT_JOB_KEY)
    if not job:
        return ("", 204)
    return render_template("_fit_audit_digest.html", snap=job.snapshot())


@bp.route("/fit-audit/verdict")
def fit_audit_verdict():
    """Async, non-blocking fit-audit verdict badge for ONE (run, qubit), fetched by
    the plot-apply popup so the user sees whether the current gate would still accept
    this fit before applying it. Read-only; NEVER blocks Apply; 204 (no badge) for a
    non-auditable family / no env / any failure — advisory only, like /field/peek."""
    from quam_state_manager.core import fit_audit
    uid = request.args.get("uid", "").strip()
    qubit = request.args.get("qubit", "").strip()
    if not uid or not qubit:
        return ("", 204)
    resolved = _resolve_run(uid)
    if not resolved:
        return ("", 204)
    ds, run_id, _ = resolved
    run = ds.runs.get(run_id)
    if not run:
        return ("", 204)
    node_name = run.experiment_name
    if fit_audit.family_for(node_name) is None:
        return ("", 204)   # not a Phase-1 auditable family — no badge
    env = config_generator.get_selected_env(current_app.instance_path)
    if not env:
        return ("", 204)   # can't replay without an env — silently no badge
    source_root = fit_audit.get_audit_source_root(current_app.instance_path)
    folder = str(run.folder_path)
    compute = request.args.get("compute") == "1"
    if not compute:
        # Warm-only: show a verdict instantly when cached, else offer an opt-in
        # check (the replay is slow-cold) — never block a popup open on a subprocess.
        res = fit_audit.cached_result(folder, source_root, env)
        if res is None:
            return render_template("_fit_audit_verdict_check.html", uid=uid, qubit=qubit)
    else:
        # An explicit "Check" is a deliberate re-audit: force a fresh replay so an
        # in-place gate/source edit is reflected, not a stale cached verdict.
        try:
            res = fit_audit.audit_run_cached(node_name, folder, env, source_root, force=True)
        except Exception:
            logger.exception("fit-audit verdict compute failed for %s/%s", uid, qubit)
            return ("", 204)
    if not res or not res.get("auditable"):
        return ("", 204)
    row = next((r for r in res.get("rows", []) if r.get("qubit") == qubit), None)
    if row is None:
        return ("", 204)
    return render_template("_fit_audit_verdict.html", row=row,
                           gate_hash=(res.get("gate_hash") or ""))


@bp.route("/config/regenerate", methods=["POST"])
def config_regenerate():
    """Run the previewer subprocess and cache the result on the store."""
    store = _store()
    if not store:
        return render_template("_status.html", message="No state loaded", level="warning"), 400

    folder = _ctx_path()
    if not folder:
        return render_template(
            "_status.html", message="Active context has no folder path", level="error",
        ), 400

    python_path = config_generator.get_selected_env(current_app.instance_path)
    if not python_path:
        return render_template(
            "_config_status.html",
            meta=None,
            error=(
                "No Generate-Config env selected. Pick one in the wizard's "
                "Environment step first — that's the env the previewer runs in."
            ),
        ), 400

    # Hash the working-copy FILES before the subprocess reads them — that is
    # the content the preview will actually reflect. The staleness basis must
    # be the files, not the in-memory store: unsaved edits at regenerate time
    # mean the preview provably lacks them (honestly stale), and an undo back
    # to the file content reads fresh again. A save landing between this hash
    # and the subprocess read errs toward "stale" — the safe direction.
    with store._lock:
        unsaved = bool(store.change_log)
    try:
        f_state, f_wiring = safe_io.read_state_wiring(Path(folder))
        basis_hash = working_copy.content_hash(f_state, f_wiring)
    except (OSError, ValueError):
        # Files unreadable for hashing (the subprocess will likely fail too);
        # fall back to the in-memory hash only when it provably equals the files.
        basis_hash = _config_state_hash(store) if not unsaved else None

    outcome = config_generator.run_config_preview(python_path, folder)

    if outcome.get("ok") and outcome.get("result"):
        result = outcome["result"]
        store.generated_config = result.get("config")
        store.generated_config_meta = {
            "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "versions": result.get("versions") or {},
            "warnings": result.get("warnings") or [],
            "qubits": result.get("qubits") or [],
            "qubit_pairs": result.get("qubit_pairs") or [],
            "basis_hash": basis_hash,
            "unsaved_at_generate": unsaved,
        }
        resp = make_response(render_template(
            "_config_status.html",
            meta=store.generated_config_meta,
            error=None,
            config_stale=_config_stale(store),
        ))
        # Lets the per-qubit/pair Generated Config sections re-GET themselves.
        resp.headers["HX-Trigger"] = "configRegenerated"
        return resp

    err = outcome.get("error") or "previewer reported an error"
    trace = ""
    if outcome.get("result"):
        trace = outcome["result"].get("traceback") or ""
    return render_template(
        "_config_status.html",
        meta=store.generated_config_meta,  # keep showing the last-good info
        error=err,
        traceback=trace,
        # Keep the export row's stale hint honest even when the refresh failed —
        # the last-good config the buttons export may predate current edits.
        config_stale=_config_stale(store),
    ), 502


@bp.route("/qubit/<name>/config", methods=["GET"])
def qubit_config(name: str):
    """Surface B: the slice of the generated config that belongs to one qubit."""
    store = _store()
    if not store:
        return render_template("_status.html", message="No state loaded", level="warning")
    if name not in (store.merged.get("qubits") or {}):
        return render_template("_status.html", message=f"Unknown qubit: {name}", level="error"), 404
    cfg = store.generated_config
    if cfg is None:
        return render_template(
            "_qubit_config.html",
            qubit_name=name,
            meta=store.generated_config_meta,
            slice=None,
            operations=[],
            target_prefix=_qubit_target_prefix(name),
            config_stale=False,
        )
    target = _qubit_target_prefix(name)
    return render_template(
        "_qubit_config.html",
        qubit_name=name,
        meta=store.generated_config_meta,
        slice=config_view.slice_for(cfg, target),
        operations=config_view.operations_for(cfg, target),
        target_prefix=target,
        config_stale=_config_stale(store),
    )


@bp.route("/pair/<name>/config", methods=["GET"])
def pair_config(name: str):
    """Surface B: the slice of the generated config that belongs to one pair."""
    store = _store()
    if not store:
        return render_template("_status.html", message="No state loaded", level="warning")
    if name not in (store.merged.get("qubit_pairs") or {}):
        return render_template("_status.html", message=f"Unknown pair: {name}", level="error"), 404
    cfg = store.generated_config
    if cfg is None:
        return render_template(
            "_pair_config.html",
            pair_name=name,
            meta=store.generated_config_meta,
            slice=None,
            operations=[],
            target_prefix=_pair_target_prefix(name),
            config_stale=False,
        )
    # 2Q gates are named by the two qubits (cr_<c>_<t>, cz_..._<t>), never by
    # the pair name, so resolve the qubit names and match on those.
    control, target = _pair_qubit_names(store, name)
    return render_template(
        "_pair_config.html",
        pair_name=name,
        meta=store.generated_config_meta,
        slice=config_view.pair_slice_for(cfg, control, target, name),
        operations=config_view.pair_operations_for(cfg, control, target, name),
        target_prefix=_pair_target_prefix(name),
        config_stale=_config_stale(store),
    )


@bp.route("/qubit/<name>/waveform/<op_name>", methods=["GET"])
def qubit_waveform(name: str, op_name: str):
    """Surface A: JSON payload for the Plotly trace of one operation's waveform."""
    return _waveform_payload(_qubit_target_prefix(name), op_name)


@bp.route("/pair/<name>/waveform/<op_name>", methods=["GET"])
def pair_waveform(name: str, op_name: str):
    """Surface A: JSON payload for the Plotly trace of one pair-macro waveform.

    The optional ``?element=`` query arg disambiguates op names shared across a
    pair's elements (e.g. ``square`` on both ``cr_<c>_<t>`` and ``cr_<t>_<c>``).
    """
    store = _store()
    if not store:
        return jsonify({"error": "No state loaded"}), 400
    cfg = store.generated_config
    if cfg is None:
        return jsonify({
            "error": (
                "Generated config is not cached yet. Click \"Regenerate config\""
                " on the Config Viewer first."
            ),
        }), 409
    control, target = _pair_qubit_names(store, name)
    payload = config_view.pair_waveform_for_operation(
        cfg, control, target, op_name,
        element=request.args.get("element") or None, pair_name=name)
    if payload is None:
        return jsonify({"error": f"operation {op_name!r} not found for pair {name!r}"}), 404
    return jsonify(payload)


def _waveform_payload(target_prefix: str, op_name: str):
    store = _store()
    if not store:
        return jsonify({"error": "No state loaded"}), 400
    cfg = store.generated_config
    if cfg is None:
        return jsonify({
            "error": (
                "Generated config is not cached yet. Click \"Regenerate config\""
                " on the Config Viewer first."
            ),
        }), 409
    payload = config_view.waveform_for_operation(cfg, target_prefix, op_name)
    if payload is None:
        return jsonify({"error": f"operation {op_name!r} not found on {target_prefix!r}"}), 404
    payload["stale"] = _config_stale(store)
    return jsonify(payload)


def _ctx_path() -> str | None:
    """Resolve the active context's folder path for the Config Viewer.

    Prefers the working copy (so previews reflect in-progress edits) and
    falls back to the live folder if no working copy is attached.
    """
    ctx = _active_ctx()
    if ctx is None:
        return None
    wc = ctx.get("working_copy")
    if wc is not None and getattr(wc, "working_folder", None):
        return str(wc.working_folder)
    return ctx.get("path")


# ======================================================================
# Experiment Scheduler (setup · pre-flight · queue · runner)
# See docs/40_scheduler.md. SM never imports the QM/qualibrate stack — the
# effective-config read shells out to generator/run_experiment.py in the
# chosen env, exactly like the Generate-Config wizard.
# ======================================================================

def _sched_bool(value) -> bool:
    return value in (True, 1, "1", "true", "True", "on", "yes")


# Routes that mutate the chip or spawn a QM subprocess — locked while a queue
# runs (the experiment is driving the chip + OPX). Reads/navigation stay live.
_SCHEDULER_MUTATOR_ENDPOINTS = {
    # generic + legacy field editors (all call modifier.set_value)
    "main.field_edit", "main.field_edit_batch",
    "main.qubit_edit", "main.pair_edit",
    # subtree creation (pulses refactored qubit_add_pulse -> the /api/pulse/* CRUD)
    "main.pair_add_gate",
    "main.pulse_edit", "main.api_pulse_create", "main.api_pulse_delete",
    "main.api_pulse_duplicate", "main.api_pulse_rename",
    # state-history writers (stage -> working copy, restore-live -> live files)
    "main.state_history_stage", "main.state_history_restore_live",
    # working-copy / live writers
    "main.save", "main.state_sync", "main.state_apply_to_live",
    "main.undo", "main.discard", "main.diagnostics_apply_fix",
    # extras identity editors (audit-r10: these stage through the modifier
    # too — un-reviewed extras must not ride an autofit plan's next apply,
    # and a mid-plan chip_name flip would re-route snapshot attribution)
    "main.chip_name_set", "main.chip_data_folder_set",
    # QM-subprocess spawners (a 2nd OPX connection during a run = collision)
    "main.generate_build", "main.generate_allocate",
    "main.generate_preview_config", "main.generate_load",
    "main.config_regenerate", "main.config_preview",
    # Re-generate spawns the SAME run_build subprocess as generate_build (a 2nd
    # OPX-connecting build during a run); fit-audit spawns a heavy env subprocess
    # (no OPX, but CPU/RAM contention with a live experiment). Sibling routes above
    # were locked but these were omitted.
    "main.regenerate_build", "main.regenerate_reconstruct", "main.fit_audit_run",
}


# /scheduler/* control endpoints an AUTOFIT plan must own exclusively while it
# runs (docs/56 §7b-B3 — two-masters closure: between plan steps the queue's
# run.status is idle, so without this gate a user could persist new critical
# settings / mutate the queue / wipe the engine's items mid-plan).
_AUTOFIT_BLOCKED_SCHEDULER_ENDPOINTS = {
    "main.scheduler_start", "main.scheduler_pause", "main.scheduler_cancel",
    "main.scheduler_settings", "main.scheduler_queue_add",
    "main.scheduler_queue_mutate", "main.scheduler_preset_load",
    "main.scheduler_presets", "main.scheduler_preset_delete",
    "main.scheduler_register_storage",
}


@bp.before_request
def _scheduler_lock_guard():
    """409 chip-mutating / QM-subprocess routes while the Scheduler is running.

    Server-side enforcement is authoritative — the UI badge/disable is only a
    hint. A manual edit or a second QM subprocess during a run would collide
    with the experiment on the chip + OPX. The same guard covers a running
    AUTOFIT plan (which drives the scheduler chassis in-process), plus the
    scheduler CONTROL endpoints themselves while autofit owns the queue.
    """
    # Autofit first (audit R4): while a REAL plan runs, mutators + scheduler
    # controls consistently report autofit_running even mid-step (when the
    # chassis makes scheduler.is_active True too). Method-gated so GETs like
    # /scheduler/settings and the presets list stay readable (audit R3). A
    # SIM plan never locks the user's chip (locks_chip — audit R2).
    if request.method != "GET" and request.endpoint in (
            _SCHEDULER_MUTATOR_ENDPOINTS | _AUTOFIT_BLOCKED_SCHEDULER_ENDPOINTS):
        from quam_state_manager.core.autofit import engine as autofit_engine
        if autofit_engine.locks_chip(_sched_inst()):
            resp = make_response(jsonify({
                "error": "autofit_running",
                "message": "An Auto Calibrate plan is running — this action is "
                           "locked until it finishes or is aborted "
                           "(see the Auto Calibrate page).",
            }), 409)
            resp.headers["HX-Reswap"] = "none"
            resp.headers["HX-Trigger"] = "autofitLocked"
            return resp
    if request.endpoint in _SCHEDULER_MUTATOR_ENDPOINTS \
            and scheduler.is_active(_sched_inst()):
        resp = make_response(jsonify({
            "error": "scheduler_running",
            "message": "The Experiment Runner is running — editing is locked "
                       "until it finishes or is cancelled.",
        }), 409)
        resp.headers["HX-Reswap"] = "none"       # don't swap an error into the page
        resp.headers["HX-Trigger"] = "schedulerLocked"
        return resp
    return None


@bp.route("/scheduler", methods=["GET"])
def scheduler_page():
    """Top-level Scheduler page — setup, pre-flight, queue + runner."""
    settings = scheduler.load_settings(_sched_inst())
    open_folder = _active_path() if _context_type() == "quam" else None
    # Prefill the quam_state target from the open chip when unset (Strict policy
    # wants them equal anyway).
    if not settings.get("quam_state_path") and open_folder:
        settings = dict(settings)
        settings["quam_state_path"] = open_folder
    template = "_scheduler.html" if _is_htmx() else "scheduler.html"
    return render_template(
        template,
        **_ctx(
            page="scheduler",
            scheduler_settings=settings,
            open_chip_folder=open_folder,
        ),
    )


# Settings whose change mid-run alters what runs on hardware / against which chip
# — locked while a queue is active (mirrors the mutator lock).
_SCHED_CRITICAL_SETTINGS = (
    "global_simulate", "quam_state_path", "env_python", "calibrations_folder")


def _sched_settings_patch(data: dict) -> dict[str, Any]:
    """Extract the persistable Scheduler settings from a request body."""
    patch: dict[str, Any] = {}
    for key in ("calibrations_folder", "env_python", "quam_state_path", "failure_policy"):
        if key in data:
            patch[key] = (data.get(key) or "").strip()
    for key in ("global_simulate", "continue_without_ui"):
        if key in data:
            patch[key] = _sched_bool(data.get(key))
    if "default_timeout_s" in data:
        try:
            patch["default_timeout_s"] = int(data.get("default_timeout_s"))
        except (TypeError, ValueError):
            pass
    return patch


@bp.route("/scheduler/settings", methods=["GET", "POST"])
def scheduler_settings():
    """Read (GET) or persist (POST) Scheduler settings."""
    inst = _sched_inst()
    if request.method == "GET":
        return jsonify(scheduler.load_settings(inst))

    data = request.get_json(silent=True) or request.form.to_dict() or {}
    patch = _sched_settings_patch(data)
    # Refuse mid-run changes to the hardware/chip-affecting settings: the worker
    # re-reads settings before every item, so un-ticking Dry run (or re-pointing
    # the chip/env) while a queue runs would flip the REST of it to LIVE hardware —
    # with none of the Strict-gate safeguards that gate Start. Non-critical keys
    # (failure_policy, continue_without_ui, timeout) stay editable.
    if scheduler.is_active(inst):
        current = scheduler.load_settings(inst)
        changed = [k for k in _SCHED_CRITICAL_SETTINGS
                   if k in patch and patch[k] != current.get(k)]
        if changed:
            return jsonify({
                "ok": False,
                "error": ("Can't change " + ", ".join(changed) + " while the "
                          "scheduler is running — pause or cancel the queue first."),
            }), 409
    return jsonify({"ok": True, "settings": scheduler.save_settings(inst, patch)})


@bp.route("/scheduler/effective-config", methods=["GET"])
def scheduler_effective_config():
    """Read the chosen env's effective qualibrate config (subprocess)."""
    inst = _sched_inst()
    python_path = (request.args.get("python") or "").strip()
    if not python_path:
        python_path = scheduler.load_settings(inst).get("env_python") or ""
    if not python_path:
        return jsonify({"ok": False, "error": "No interpreter selected."}), 400
    result = scheduler.read_effective_config(python_path)
    if result.get("ok"):
        scheduler.save_settings(inst, {"effective_config": result.get("config")})
    return jsonify(result)


def _gather_preflight(inst: str, data: dict) -> dict:
    """Run the identity/safety checks that gate a run (shared by the preflight
    route and the Strict gate inside /scheduler/start)."""
    settings = scheduler.load_settings(inst)
    cal = (data.get("calibrations_folder") or settings.get("calibrations_folder") or "").strip()
    env_python = (data.get("env_python") or settings.get("env_python") or "").strip()
    target = (data.get("quam_state_path") or settings.get("quam_state_path") or "").strip()

    ctx_type = _context_type()
    open_folder = _active_path() if ctx_type == "quam" else None
    chip_clean = not (_change_count() > 0 or _working_dirty() or bool(_ctx_obj("pending_reapply")))

    eff = scheduler.read_effective_config(env_python) if env_python else {}
    cfg = eff.get("config") or {}
    install = (eff.get("editable_install") or {}).get("path")

    probe = config_generator.probe_selected_env(env_python, instance_path=inst) if env_python else {}

    align_result = scheduler.align_folders(open_folder, target) if (open_folder and target) else None
    dataset_roots = scheduler.find_dataset_roots(cfg.get("storage_location"))
    workspace_roots = [str(p) for p in _ws().root_folders]

    result = scheduler.build_preflight({
        "chip_open": open_folder is not None,
        "chip_type": ctx_type,
        "open_chip_folder": open_folder,
        "target_quam_state": target,
        "calibrations_folder": cal,
        "effective_config": cfg,
        "editable_install_path": install,
        "align_result": align_result,
        "env_usable": probe.get("usable") if probe else None,
        "env_missing": probe.get("missing") or [],
        "chip_clean": chip_clean,
        "dataset_roots": dataset_roots,
        "workspace_roots": workspace_roots,
    })
    result["effective_config"] = cfg
    result["editable_install"] = eff.get("editable_install")
    result["dataset_roots"] = dataset_roots
    result["env_probe"] = probe
    return result


@bp.route("/scheduler/preflight", methods=["POST"])
def scheduler_preflight():
    """Run the identity/safety checks that gate a future run (Strict policy)."""
    inst = _sched_inst()
    data = request.get_json(silent=True) or {}
    return jsonify(_gather_preflight(inst, data))


@bp.route("/scheduler/register-storage", methods=["POST"])
def scheduler_register_storage():
    """Register a qualibrate storage/dataset folder as an SM workspace root."""
    data = request.get_json(silent=True) or {}
    folder = (data.get("folder") or "").strip()
    if not folder:
        return jsonify({"ok": False, "error": "No folder given."}), 400
    ws = _ws()
    try:
        entries = ws.add_root(folder)
    except Exception as exc:  # noqa: BLE001
        return jsonify({"ok": False, "error": str(exc)}), 400
    current_app.config.pop("dataset_store", None)
    _save_workspace_roots()
    return jsonify({"ok": True, "added": len(entries), "folder": folder})


# --- Scheduler queue (Phase 1) ----------------------------------------

def _sched_inst() -> str:
    """Where the Experiment Runner's state lives FOR THE OPEN CHIP (docs/80).

    This one function is what makes two windows able to drive two different
    chips. Runner state used to sit directly in the instance dir — one
    settings file, one queue, machine-wide — so window 2 choosing its chip
    rewrote the ``quam_state_path`` window 1's worker re-reads per item, and
    both windows stared at the same queue. Nothing about PIDs or ``qm``
    prevented two chips; the storage layout did.

    Scoping by the OPEN chip is the only non-circular choice available: the
    runner's own ``quam_state_path`` lives *inside* the scoped file, so it
    cannot also select it. It is also the honest mental model — a window has a
    chip open, and its runner belongs to that chip. Machine-level settings
    (env, calibrations folder) stay shared one level up, so per-chip isolation
    never becomes per-chip re-configuration.

    Callers that want the INSTANCE dir — the node-library scan caches, which
    are chip-independent — must ask for it explicitly.
    """
    ctx = _active_ctx()
    chip = ctx.get("path") if ctx and ctx.get("type") == "quam" else None
    return str(scheduler.scope_dir(current_app.instance_path, chip))


def _sched_state() -> dict:
    """The poll payload: queue + run state (+ orphan reconcile). Carries a
    compact autofit summary so the existing badge poll powers the Autofit
    badge for free (docs/56 §7b-F)."""
    out = scheduler.runner_status(_sched_inst())
    # docs/80 — name the window that owns a run we are only watching, so the
    # Experiment Runner page can say "another window is running this" instead
    # of showing controls that would 409.
    try:
        owner = scheduler.foreign_owner({"run": out.get("run") or {}})
        if owner is not None:
            out["foreign_owner"] = {
                "pid": owner[0], "port": owner[1],
                "label": scheduler.owner_label(*owner)}
    except Exception:       # noqa: BLE001 — a badge detail must not break the poll
        logger.debug("foreign-owner probe failed", exc_info=True)
    try:
        from quam_state_manager.core.autofit import engine as autofit_engine
        eng = autofit_engine.get_engine(_sched_inst())
        if eng is not None:
            st = eng.status()
            out["autofit"] = {
                "status": st["status"],
                "running": eng.is_running(),
                "sim": eng.is_sim,
                "plan": (st.get("plan") or {}).get("name"),
                "current": st.get("current"),
                "review_count": len(st.get("review_queue") or []),
            }
        else:
            # after an SM restart the morning-after review count must still
            # reach the badge — stat-cached read of the persisted final state
            summary = autofit_engine.persisted_summary(_sched_inst())
            if summary is not None:
                out["autofit"] = summary
    except Exception:  # noqa: BLE001 — the badge must never break the poll
        logger.warning("autofit badge summary failed", exc_info=True)
    return out


@bp.route("/scheduler/scan", methods=["GET"])
def scheduler_scan():
    """List nodes/graphs in a folder (hardware-safe ast scan) + the chip roster."""
    folder = (request.args.get("folder") or "").strip()
    if not folder:
        folder = scheduler.load_settings(_sched_inst()).get("calibrations_folder") or ""
    items = [n.to_dict() for n in node_scan.scan_folder(folder, instance_path=current_app.instance_path)] if folder else []
    store = _store() if _context_type() == "quam" else None
    return jsonify({
        "folder": folder,
        "items": items,
        "qubits": store.qubit_names if store else [],
        "pairs": store.qubit_pair_names if store else [],
    })


@bp.route("/scheduler/scan-params", methods=["POST"])
def scheduler_scan_params():
    """Inspection-based scan (subprocess in the env) → full parameter schemas.

    Slower than /scheduler/scan (imports every file); the frontend caches the
    result and renders per-node parameter forms from it.
    """
    inst = _sched_inst()
    settings = scheduler.load_settings(inst)
    data = request.get_json(silent=True) or {}
    folder = (data.get("folder") or settings.get("calibrations_folder") or "").strip()
    env = (data.get("env_python") or settings.get("env_python") or "").strip()
    if not folder:
        return jsonify({"ok": False, "error": "No calibrations folder set."}), 400
    if not env:
        return jsonify({"ok": False, "error": "No env selected."}), 400
    # The parameter-scan cache keys on (env, folder) and knows nothing about
    # chips, so it stays instance-level rather than being rebuilt per scope.
    return jsonify(scheduler.scan_params(
        env, folder, instance_path=current_app.instance_path))


@bp.route("/scheduler/queue/add", methods=["POST"])
def scheduler_queue_add():
    data = request.get_json(silent=True) or {}
    file = (data.get("file") or "").strip()
    name = (data.get("name") or "").strip()
    if not file or not name:
        return jsonify({"ok": False, "error": "file and name are required"}), 400
    info = {
        "file": file, "name": name,
        "kind": data.get("kind") or "node",
        "has_hook": bool(data.get("has_hook")),
        "targets_name": data.get("targets_name") or "qubits",
    }
    # Re-derive kind / has_hook / targets_name authoritatively from the file (ast)
    # — never trust the client label for the safety-critical run-path branch
    # (a graph mislabeled 'node' would run verbatim on the wrong qubits).
    # scan_file is always FRESH (the scan cache is display-only), so the queued
    # classification is the file's own current bytes, never a stale cache hit.
    try:
        if Path(file).is_file():
            scanned = node_scan.scan_file(Path(file))
            if not scanned.error:
                info["kind"] = scanned.kind
                info["has_hook"] = scanned.has_hook
                info["targets_name"] = scanned.targets_name
                info["name"] = scanned.name or name
    except OSError:
        pass
    if data.get("label"):
        info["label"] = str(data["label"])
    scheduler.add_item(_sched_inst(), info, data.get("targets") or [],
                       after_id=(data.get("after_id") or None))
    return jsonify({"ok": True, "state": _sched_state()})


@bp.route("/scheduler/queue/<action>", methods=["POST"])
def scheduler_queue_mutate(action: str):
    """remove / toggle / targets / reorder / duplicate / expand / clear-finished."""
    inst = _sched_inst()
    data = request.get_json(silent=True) or {}
    item_id = (data.get("id") or "").strip()
    if action in ("remove", "toggle", "targets", "params", "duplicate", "expand") and not item_id:
        return jsonify({"ok": False, "error": "id is required"}), 400
    if action == "remove":
        scheduler.remove_item(inst, item_id)
    elif action == "toggle":
        scheduler.toggle_item(inst, item_id, data.get("enabled"))
    elif action == "targets":
        scheduler.set_targets(inst, item_id, data.get("targets") or [])
    elif action == "params":
        scheduler.set_param_overrides(inst, item_id, data.get("param_overrides") or {})
    elif action == "reorder":
        scheduler.reorder(inst, data.get("order") or [])
    elif action == "duplicate":
        scheduler.duplicate_item(inst, item_id)
    elif action == "expand":
        scheduler.expand_per_qubit(inst, item_id, data.get("targets") or [])
    elif action == "clear-finished":
        scheduler.clear_finished(inst)
    elif action == "rules":
        if not item_id:
            return jsonify({"ok": False, "error": "id is required"}), 400
        err = scheduler.set_item_rules(inst, item_id, data.get("on_outcome") or [])
        if err:
            return jsonify({"ok": False, "error": err}), 400
    elif action == "set-label":
        if not item_id:
            return jsonify({"ok": False, "error": "id is required"}), 400
        scheduler.set_item_label(inst, item_id, data.get("label") or "")
    else:
        return jsonify({"ok": False, "error": f"unknown action: {action}"}), 404
    return jsonify({"ok": True, "state": _sched_state()})


@bp.route("/scheduler/presets", methods=["GET", "POST"])
def scheduler_presets():
    """List presets (GET) or snapshot the current queue as a named preset (POST)."""
    inst = _sched_inst()
    if request.method == "GET":
        return jsonify({"ok": True, "presets": [
            {"id": p.get("id"), "name": p.get("name"),
             "created_at": p.get("created_at"), "n_items": len(p.get("items") or [])}
            for p in scheduler.list_presets(inst)
        ]})
    data = request.get_json(silent=True) or {}
    name = (data.get("name") or "").strip()
    if not name:
        return jsonify({"ok": False, "error": "name is required"}), 400
    preset = scheduler.save_preset(inst, name)
    return jsonify({"ok": True, "id": preset["id"], "n_items": len(preset["items"])})


@bp.route("/scheduler/presets/<preset_id>/load", methods=["POST"])
def scheduler_preset_load(preset_id: str):
    data = request.get_json(silent=True) or {}
    mode = "replace" if data.get("mode") == "replace" else "append"
    state, warnings = scheduler.load_preset(_sched_inst(), preset_id, mode=mode)
    if state is None:
        return jsonify({"ok": False, "error": "preset not found"}), 404
    return jsonify({"ok": True, "state": _sched_state(), "warnings": warnings})


@bp.route("/scheduler/presets/<preset_id>", methods=["DELETE"])
def scheduler_preset_delete(preset_id: str):
    scheduler.delete_preset(_sched_inst(), preset_id)
    return jsonify({"ok": True})


def _latest_run_ref() -> dict | None:
    """The globally-latest dataset run as ``{uid, run_id, name}`` (or None)."""
    latest_key: tuple[str, str] | None = None
    ref = None
    for fol in _active_dataset_stores():
        store = fol["store"]
        with store._scan_lock:               # snapshot — avoid racing a worker rescan
            runs = list(store.runs.values())
        for run in runs:
            key = (run.date or "", run.time or "")
            if latest_key is None or key > latest_key:
                latest_key = key
                ref = {"uid": _dataset_uid(fol["key"], run.run_id),
                       "run_id": run.run_id, "name": run.experiment_name}
    return ref


def _fit_results_for_ref(ref: dict) -> dict | None:
    """Resolve the attributed run's per-qubit ``fit_results`` (scalars resolved).

    Returns None when the run cannot be found or its data.json is unreadable —
    outcome rules then no-op with a note rather than guessing.
    """
    try:
        rid = ref.get("run_id")
        for fol in _active_dataset_stores():
            store = fol["store"]
            if _dataset_uid(fol["key"], rid) != ref.get("uid"):
                continue
            with store._scan_lock:
                run = store.runs.get(rid)
            if run is None:
                return None
            fr = store._resolve_fit_refs(run)
            return fr if isinstance(fr, dict) else None
    except Exception:  # noqa: BLE001
        logger.warning("fit_results resolve failed", exc_info=True)
    return None


def _names_match(item_name: str | None, run_name: str | None) -> bool:
    """Attribution guard: the attributed run must BE this node's run.

    Node names and dataset experiment names drift in decoration (numeric
    prefixes, timestamps), so compare loosely but require real overlap —
    ambiguity fails closed (rules no-op with a note, never chain on a
    stranger's run)."""
    a = (item_name or "").strip().lower()
    b = (run_name or "").strip().lower()
    if not a or not b:
        return False
    norm = lambda s: s.strip("_").lstrip("0123456789#_")  # noqa: E731
    a2, b2 = norm(a), norm(b)
    return bool(a2 and b2 and (a2 in b2 or b2 in a2))


def _scheduler_refresh_hook(app, instance_path, folder, item_id, status) -> None:
    """Post-node refresh — runs in the worker thread under an app context.

    Reconciles the RUN's chip folder (not the active context, so nav-away is
    fine), rescans datasets, and — only for a SUCCESSFUL item that produced a
    genuinely new run — attaches that run's dataset ref to the item. Bumps
    run.chip_rev so every open tab re-renders once. Works headless (no tab).
    Never raises into the worker.
    """
    try:
        with app.app_context():
            changed = False
            ctx = _find_quam_ctx_by_path(folder)
            if ctx is not None and ctx.get("type") == "quam":
                store = ctx.get("store")
                # Hash under store._lock for a consistent snapshot (cheap; the
                # reconcile in between must NOT hold it — it takes the build lock
                # and mutates store.state itself).
                def _hash():
                    if not store:
                        return None
                    with store._lock:
                        return working_copy.content_hash(store.state, store.wiring)
                before = _hash()
                try:
                    # docs/87 — MACHINE caller: the scheduler worker just ran a
                    # node that wrote live. It adopts (auto_adopt default) so the
                    # engine's own view stays correct; the ASK path is the user
                    # opening/switching a chip.
                    _reconcile_cached_quam_ctx(ctx["path"], ctx, auto_adopt=True)
                except (OSError, ValueError):
                    logger.warning("post-node reconcile failed", exc_info=True)
                after = _hash()
                changed = before != after
            attributed = False
            try:
                for fol in _active_dataset_stores():
                    fol["store"].rescan_if_stale()
                # Attribute a dataset run ONLY to a successful item, and only when
                # a run newer than any already-attributed one actually appeared
                # (so a failed / dry-run / no-output node never gets a wrong ↗).
                if status == "done":
                    ref = _latest_run_ref()
                    rid = ref.get("run_id") if ref else None
                    if isinstance(rid, int):
                        last = scheduler.load_queue(instance_path)["run"].get("last_assigned_run_id", -1)
                        if rid > (last if isinstance(last, int) else -1):
                            scheduler.set_item_result(instance_path, item_id, ref)
                            attributed = True
            except Exception:  # noqa: BLE001
                logger.warning("post-node dataset rescan failed", exc_info=True)
            # --- outcome-aware chaining (sequence editor) -------------------
            # Runs synchronously BEFORE the worker's next _next_queued read, so
            # rule inserts land seamlessly mid-run. Fails closed on any
            # attribution doubt: no run / name mismatch → no-op + outcome_note.
            try:
                item = scheduler._find(scheduler.load_queue(instance_path), item_id)
                if item is not None and (item.get("on_outcome") or []):
                    fit_results = None
                    note = None
                    if status == "done":
                        ref = item.get("result_ref")
                        if not ref:
                            note = "fit_fail rule skipped: no run attributed to this item"
                        elif not _names_match(item.get("name"), ref.get("name")):
                            note = (f"fit_fail rule skipped: attributed run "
                                    f"{ref.get('name')!r} does not match node name (ambiguous)")
                        else:
                            fit_results = _fit_results_for_ref(ref)
                            if fit_results is None:
                                note = "fit_fail rule skipped: run data.json unreadable"
                    planned, plan_note = scheduler.plan_outcome_inserts(item, status, fit_results)
                    n_ins = scheduler.apply_outcome_inserts(
                        instance_path, item_id, planned, note or plan_note)
                    if n_ins:
                        logger.info("outcome rules inserted %d follow-up item(s) after %s",
                                    n_ins, item.get("name"))
                        attributed = True  # force a UI re-render
            except Exception:  # noqa: BLE001
                logger.warning("outcome-rule evaluation failed", exc_info=True)
            if changed or attributed:
                scheduler.bump_chip_rev(instance_path)
    except Exception:  # noqa: BLE001
        logger.warning("scheduler refresh hook failed", exc_info=True)


@bp.route("/scheduler/start", methods=["POST"])
def scheduler_start():
    inst = _sched_inst()
    data = request.get_json(silent=True) or {}
    # Persist the POSTed settings BEFORE the preflight + start, so the values the
    # Strict gate validates are exactly the ones the worker reads from disk. The
    # text fields are debounce-saved (400ms), so a Start that beats the debounce —
    # or whose settings POST lost the race — would otherwise preflight the NEW chip
    # path while the run executed against the OLD one (the wrong-chip case the gate
    # exists to block).
    #
    # ONLY on a genuine cold start: guard on `not is_active()` so a racing/
    # double-submit POST to /scheduler/start DURING a live run can't write new
    # critical settings (quam_state_path / global_simulate) to disk — the worker
    # re-reads settings per item, so an unguarded persist would flip the rest of
    # the queue to a new chip / LIVE mode, bypassing the mid-run settings lock
    # (/scheduler/settings 409). start() below already no-ops a duplicate worker,
    # but only AFTER this persist would have landed, so the guard must be here.
    if not scheduler.is_active(inst):
        try:
            scheduler.save_settings(inst, _sched_settings_patch(data))
        except Exception:  # noqa: BLE001
            logger.warning("start settings persist failed", exc_info=True)
    # Strict gate: re-run the identity/safety preflight server-side and refuse to
    # start if it fails (wrong chip, env unusable, library mismatch, …) — unless
    # the user explicitly forces past the warnings.
    if not data.get("force"):
        try:
            pre = _gather_preflight(inst, data)
        except Exception:  # noqa: BLE001
            logger.warning("start preflight failed", exc_info=True)
            pre = None
        if pre is not None and not pre.get("ok"):
            return jsonify({"ok": False, "reason": "preflight", "preflight": pre}), 409
    app = current_app._get_current_object()
    scheduler.set_refresh_hook(
        lambda folder, item_id, status: _scheduler_refresh_hook(
            app, inst, folder, item_id, status))
    try:
        scheduler.start(inst)
    except scheduler.ForeignRunnerError as exc:
        # docs/80 — the one refusal, because it is the one case a warning
        # cannot make safe: a second worker over the same queue means two
        # processes driving one OPX.
        return jsonify({"ok": False, "reason": "foreign_runner",
                        "error": "scheduler_foreign_owner",
                        "owner": {"pid": exc.pid, "port": exc.port,
                                  "label": scheduler.owner_label(exc.pid, exc.port)},
                        "message": str(exc)}), 409
    return jsonify({"ok": True, "state": _sched_state()})


@bp.route("/scheduler/pause", methods=["POST"])
def scheduler_pause():
    scheduler.pause(_sched_inst())
    return jsonify({"ok": True, "state": _sched_state()})


@bp.route("/scheduler/cancel", methods=["POST"])
def scheduler_cancel():
    scheduler.cancel(_sched_inst())
    return jsonify({"ok": True, "state": _sched_state()})


@bp.route("/scheduler/status", methods=["GET"])
def scheduler_status():
    return jsonify(_sched_state())


@bp.route("/scheduler/log", methods=["GET"])
def scheduler_log():
    item_id = (request.args.get("id") or "").strip()
    return jsonify({"id": item_id, "log": scheduler.tail_log(_sched_inst(), item_id)})


def _find_quam_ctx_by_path(target: str | None) -> dict | None:
    """Find the loaded QUAM context whose live folder == *target* (normalized)."""
    if not target:
        return None
    tn = scheduler.norm_path(target)
    for ctx in current_app.config.get("contexts", {}).values():
        if ctx.get("type") == "quam" and scheduler.norm_path(ctx.get("path")) == tn:
            return ctx
    return None




# ======================================================================
# Autofit — the one-button automatic fitting scheduler (docs/56).
# Engine + gates + auditor live in core/autofit; these routes are thin:
# readiness, plan resolution, start/abort, status poll, report.
# ======================================================================

def _autofit_engine_mod():
    from quam_state_manager.core.autofit import engine as autofit_engine
    return autofit_engine


# Serializes /autofit/start per instance: the guards + world-prep + engine
# claim must be one atomic unit (audits: sim-world rmtree TOCTOU, the
# scheduler-exclusion window during real-backend prep).
_AUTOFIT_START_LOCKS: dict[str, threading.Lock] = {}
_AUTOFIT_START_LOCKS_GUARD = threading.Lock()


def _autofit_start_lock(inst: str) -> threading.Lock:
    with _AUTOFIT_START_LOCKS_GUARD:
        lock = _AUTOFIT_START_LOCKS.get(inst)
        if lock is None:
            lock = threading.Lock()
            _AUTOFIT_START_LOCKS[inst] = lock
        return lock


def _autofit_readiness() -> dict:
    """The plan bar's readiness strip (docs/56 §7b — preflight on page open,
    never first at ▶): chip, scheduler env, calibrations folder, LLM."""
    from quam_state_manager.core.autofit import auditor as af_auditor

    inst = _sched_inst()
    settings = scheduler.load_settings(inst)
    ctx = _active_ctx()
    chip_ok = bool(ctx and ctx.get("type") == "quam")
    env = settings.get("env_python") or ""
    folder = settings.get("calibrations_folder") or ""
    # Instance-level, NOT the per-chip scope (docs/80 Part 4 audit): the LLM
    # provider, model and API KEY are a user credential, not a fact about a
    # device. Reading them from a scope would both hide the file the user
    # already wrote at instance/autofit_ai.json — silently turning the audit
    # off — and ask for the key again per chip.
    ai = af_auditor.load_settings(current_app.instance_path)
    store = _store() if chip_ok else None
    return {
        "chip": {"ok": chip_ok,
                 "name": (_active_chip_identity() or {}).get("name") if chip_ok else None},
        "env": {"ok": bool(env), "value": env},
        "calibrations_folder": {"ok": bool(folder), "value": folder},
        "llm": {"provider": ai.get("provider", "off"),
                "enabled": ai.get("provider", "off") not in ("off", "")},
        "scheduler_active": scheduler.is_active(inst),
        "autofit_active": _autofit_engine_mod().is_active(inst),
        "qubits": store.qubit_names if store else [],
        "qubit_pairs": store.qubit_pair_names if store else [],
    }


@bp.route("/autofit", methods=["GET"])
def autofit_page():
    """The one-button page: plan bar + live board + review queue + report."""
    from quam_state_manager.core.autofit import plan as af_plan

    template = "_autofit.html" if _is_htmx() else "autofit.html"
    return render_template(template, **_ctx(
        page="autofit",
        presets={k: v["name"] for k, v in af_plan.PRESETS.items()},
        readiness=_autofit_readiness(),
    ))


@bp.route("/autofit/status", methods=["GET"])
def autofit_status():
    """Engine status for the page poll (board, review queue, ledger tail)."""
    eng = _autofit_engine_mod().get_engine(_sched_inst())
    if eng is None:
        # a previous session's persisted final state (morning-after report)
        try:
            persisted = safe_io.read_json(
                Path(_sched_inst()) / "autofit_run.json")
        except (OSError, ValueError):
            persisted = None
        return jsonify({"active": False, "state": persisted,
                        "readiness": _autofit_readiness()})
    return jsonify({"active": eng.is_running(), "state": eng.status(),
                    "readiness": _autofit_readiness()})


@bp.route("/autofit/resolve", methods=["POST"])
def autofit_resolve():
    """Resolve a preset/plan's steps against the scanned calibrations folder —
    the pre-Run step→file table (docs/56 §7b-D)."""
    from quam_state_manager.core.autofit import plan as af_plan

    data = request.get_json(silent=True) or {}
    try:
        p = _autofit_plan_from_request(data)
    except af_plan.PlanError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    folder = scheduler.load_settings(_sched_inst()).get("calibrations_folder") or ""
    if not folder:
        return jsonify({"ok": False, "error": "no calibrations folder set — "
                        "configure it on the Experiment Runner page"}), 400
    items = [n.to_dict() for n in
             node_scan.scan_folder(folder, instance_path=current_app.instance_path)]
    files = [{"name": i.get("name"), "path": i.get("file") or i.get("path")}
             for i in items if i.get("kind") == "node"]
    res = af_plan.resolve_steps(p, files)
    return jsonify({"ok": True, "resolution": res,
                    "plan": p.as_dict(), "folder": folder})


def _autofit_plan_from_request(data: dict):
    from quam_state_manager.core.autofit import plan as af_plan

    preset = (data.get("preset") or "").strip()
    if preset:
        p = af_plan.preset_plan(preset)
        raw = p.as_dict()
    else:
        raw = data.get("plan") or {}
    # request-level overrides
    if data.get("targets") is not None:
        raw["targets"] = list(data["targets"])
    if data.get("autonomy"):
        raw["autonomy"] = data["autonomy"]
    return af_plan.validate_plan(raw)


@bp.route("/autofit/start", methods=["POST"])
def autofit_start():
    """THE button. Guards: mutual exclusion vs the scheduler + a running plan,
    a clean scheduler queue (real), preflight (real, force-able), full step
    resolution (real). backend=sim runs the LiveSimBackend demo world under
    instance/autofit/sim — zero hardware, same engine + write path."""
    from quam_state_manager.core.autofit import auditor as af_auditor
    from quam_state_manager.core.autofit import plan as af_plan
    from quam_state_manager.core.autofit.auditor import Auditor
    from quam_state_manager.core.autofit.engine import PlanEngine

    inst = _sched_inst()
    autofit_engine = _autofit_engine_mod()
    data = request.get_json(silent=True) or {}
    start_lock = _autofit_start_lock(inst)
    if not start_lock.acquire(blocking=False):
        return jsonify({"ok": False,
                        "error": "another autofit start is in progress"}), 409
    try:
        if autofit_engine.is_active(inst):
            return jsonify({"ok": False,
                            "error": "an autofit plan is already running"}), 409
        if scheduler.is_active(inst):
            return jsonify({"ok": False, "error": "the Experiment Runner is "
                            "running — wait for it or cancel it first"}), 409
        try:
            p = _autofit_plan_from_request(data)
        except af_plan.PlanError as exc:
            return jsonify({"ok": False, "error": str(exc)}), 400
        backend_kind = (data.get("backend") or "sim").strip()

        # instance-level: see the note in _autofit_readiness
        auditor = Auditor(af_auditor.load_settings(current_app.instance_path))

        if backend_kind == "sim":
            eng = _autofit_start_sim(inst, p, auditor)
        else:
            eng, err, code = _autofit_start_real(inst, p, data, auditor)
            if err:
                return jsonify({"ok": False, **err}), code
        try:
            run_id = eng.start()
        except RuntimeError as exc:
            return jsonify({"ok": False, "error": str(exc)}), 409
        return jsonify({"ok": True, "plan_run_id": run_id,
                        "backend": backend_kind})
    finally:
        start_lock.release()


def _autofit_start_sim(inst, p, auditor):
    """The demo world: a fresh sim chip + live folder + dataset root under
    instance/autofit/sim, driven through the REAL write path (E2E parity)."""
    import shutil
    import threading as _threading

    from quam_state_manager.core.autofit import synth as af_synth
    from quam_state_manager.core.autofit.engine import PlanEngine
    from quam_state_manager.core.autofit.simbackend import LiveSimBackend
    from quam_state_manager.core.autofit.writer import ChipHandle, RealWriter
    from quam_state_manager.core.loader import QuamStore
    from quam_state_manager.core.modifier import Modifier
    from quam_state_manager.core.saver import Saver

    # Instance-level, NOT per-chip (docs/80 Part 4): the sim world is a
    # throwaway synthetic chip that exists to demo the loop hardware-free. It
    # has nothing to do with whichever real chip is open, so scoping it would
    # scatter identical copies of the same fake device. The plan LEDGER, by
    # contrast, is about a specific chip and does live in the scope.
    sim_root = Path(current_app.instance_path) / "autofit" / "sim"
    shutil.rmtree(sim_root, ignore_errors=True)
    live = sim_root / "live_chip"
    live.mkdir(parents=True)
    pairs = ("qA2-qA1",)
    qubits = ("qA1", "qA2", "qA3")
    chip = af_synth.make_sim_chip(qubits, pairs, seed=42)
    safe_io.write_state_wiring(live, chip.state, chip.wiring)

    wc = working_copy.create(sim_root / "wc_inst", live)
    store = QuamStore(wc.working_folder)
    handle_lock = threading.RLock()

    def reconcile():
        with handle_lock:
            res = working_copy.reconcile_with_live(
                wc, sync_if_clean=not store.change_log)
            if res == working_copy.RECONCILE_SYNCED:
                store.reload()

    handle = ChipHandle(store=store, modifier=Modifier(store),
                        saver=Saver(store), wc=wc, build_lock=handle_lock,
                        live_path=str(live), reconcile=reconcile)

    class _SimIngest(LiveSimBackend):
        def run_step(self, step, targets, params, attempt, abort):
            res = super().run_step(step, targets, params, attempt, abort)
            reconcile()
            return res

    targets = list(p.targets) or (list(pairs) if p.targets_kind == "qubit_pairs"
                                  else list(qubits))
    backend = _SimIngest(chip, sim_root / "data", live, seed=7)
    return PlanEngine(inst, p, targets, backend, RealWriter(handle),
                      auditor, autonomy=p.autonomy, is_sim=True)


def _autofit_start_real(inst, p, data, auditor):
    """Real chassis: preflight + clean-queue + resolution gates, adapter over
    the loaded ctx, ChipHandle bound to the CAPTURED ctx (never re-fetched)."""
    from quam_state_manager.core.autofit import plan as af_plan
    from quam_state_manager.core.autofit.engine import PlanEngine
    from quam_state_manager.core.autofit.realbackend import RealAdapter, RealBackend
    from quam_state_manager.core.autofit.writer import ChipHandle, RealWriter

    ctx = _active_ctx()
    if not ctx or ctx.get("type") != "quam":
        return None, {"error": "no chip loaded"}, 409
    # a foreign queue would run BEFORE our items on the hardware — refuse
    qstate = scheduler.load_queue(inst)
    foreign = [i for i in qstate.get("queue", [])
               if i.get("enabled") and i.get("status") == "queued"]
    if foreign:
        return None, {"error": f"the Experiment Runner queue holds {len(foreign)} "
                      "enabled item(s) — clear or disable them first "
                      "(they would run before the plan's steps)"}, 409
    if not data.get("force"):
        try:
            pre = _gather_preflight(inst, {})
        except Exception:  # noqa: BLE001
            logger.warning("autofit preflight failed", exc_info=True)
            pre = None
        if pre is not None and not pre.get("ok"):
            return None, {"error": "preflight failed", "reason": "preflight",
                          "preflight": pre}, 409
    # step → file resolution must be complete
    folder = scheduler.load_settings(inst).get("calibrations_folder") or ""
    items = [n.to_dict() for n in
             node_scan.scan_folder(folder, instance_path=current_app.instance_path)] if folder else []
    files = [{"name": i.get("name"), "path": i.get("file") or i.get("path")}
             for i in items if i.get("kind") == "node"]
    res = af_plan.resolve_steps(p, files)
    overrides = data.get("step_files") or {}      # user's dropdown picks
    # SECURITY: an override must be one of THIS step's scan candidates — a
    # free-form path here would let the client run an arbitrary .py on the
    # hardware through the chassis (the dropdown only ever offers candidates).
    resolved: dict[str, str] = {}
    for step in p.steps:
        entry = res.get(step.id) or {}
        pick = overrides.get(step.id)
        if pick and pick not in (entry.get("candidates") or []):
            return None, {"error": f"step {step.id!r}: override is not one of "
                          "the scanned candidates"}, 400
        pick = pick or entry.get("path")
        if not pick or entry.get("status") == "missing":
            return None, {"error": f"step {step.id!r} has no resolved node "
                          "file", "resolution": res}, 409
        resolved[step.id] = pick

    app = current_app._get_current_object()
    folder_key = ctx["path"]
    live_path = ctx.get("live_path") or ctx["path"]
    build_lock = _get_quam_build_lock(folder_key)

    def reconcile():
        # the CAPTURED ctx, never a call-time registry lookup — a mid-plan
        # /load could displace the name-keyed entry and silently no-op the
        # reconcile while the writer still holds this store/wc (audit E5)
        with app.app_context():
            try:
                # docs/87 — MACHINE caller: autofit's gates judge each fit against
                # the pre-update anchor (docs/47), so a stale store makes the
                # VERDICT wrong. A robot mid-loop does not stop to ask.
                _reconcile_cached_quam_ctx(ctx["path"], ctx, auto_adopt=True)
            except Exception:  # noqa: BLE001
                logger.exception("autofit reconcile failed for %s", live_path)

    def rescan_and_list_runs():
        with app.app_context():
            out = []
            for fol in _active_dataset_stores():
                st = fol["store"]
                try:
                    st.rescan_if_stale()
                except Exception:  # noqa: BLE001
                    logger.exception("autofit dataset rescan failed")
                with st._scan_lock:
                    out.extend(st.runs.values())
            out.sort(key=lambda r: (r.date or "", r.time or ""), reverse=True)
            return out

    handle = ChipHandle(store=ctx["store"], modifier=ctx["modifier"],
                        saver=ctx["saver"], wc=ctx["working_copy"],
                        build_lock=build_lock, live_path=str(live_path),
                        reconcile=reconcile)
    adapter = RealAdapter(instance_path=inst, reconcile=reconcile,
                          rescan_and_list_runs=rescan_and_list_runs)
    backend = RealBackend(adapter, resolved)
    hm = _history()

    def snapshot(label):
        with app.app_context():
            hm.check_and_snapshot(str(live_path), "manual", force=True,
                                   project=_scope_for(live_path, ctx))

    targets = list(p.targets)
    if not targets:
        store = ctx["store"]
        targets = list(store.qubit_pair_names if p.targets_kind == "qubit_pairs"
                       else store.qubit_names)

    def resolve_node_for(fam_key):
        # runtime escalation re-cal steps (docs/56 v2): resolve the family
        # against the SAME scanned candidate list the plan build used — the
        # engine only ever receives scan-derived paths
        try:
            fake = af_plan.Plan(name="escalation", targets_kind=p.targets_kind,
                                steps=[af_plan.Step(id="x", family=fam_key)])
            ent = af_plan.resolve_steps(fake, files).get("x") or {}
            return ent.get("path")
        except Exception:  # noqa: BLE001
            logger.exception("escalation node resolve failed")
            return None

    def history_points(path_map):
        """G5's trend series: what THIS chip has historically held at the fields
        the family is about to write (docs/78 P2d).

        Without this the drift gate is dead code — a node can hand back a number
        that is physically plausible in the abstract yet 40 robust-σ off what
        this qubit has read for months, and nothing notices. Reads the whole
        target set as ONE grid column so the snapshot store is parsed once per
        run, not once per qubit. An empty answer means "no trend" and the gate
        abstains: a history hiccup must never manufacture a verdict.
        """
        try:
            with app.app_context():
                series = hm.column_history(str(live_path), dict(path_map),
                                           scan_limit=60)
        except Exception:  # noqa: BLE001
            logger.warning("autofit history lookup failed", exc_info=True)
            return {}
        out = {}
        for row, rows in (series or {}).items():
            vals = [float(r[1]) for r in rows
                    if isinstance(r[1], (int, float))
                    and not isinstance(r[1], bool) and math.isfinite(r[1])]
            if len(vals) >= 3:          # below that G5 abstains anyway
                out[row] = vals
        return out

    eng = PlanEngine(inst, p, targets, backend, RealWriter(handle), auditor,
                     autonomy=p.autonomy, snapshot_fn=snapshot,
                     history_points_of=history_points,
                     resolve_node=resolve_node_for)
    return eng, None, 200


@bp.route("/autofit/abort", methods=["POST"])
def autofit_abort():
    eng = _autofit_engine_mod().get_engine(_sched_inst())
    if eng is None:
        return jsonify({"ok": False, "error": "no plan"}), 404
    eng.abort()
    return jsonify({"ok": True})


@bp.route("/autofit/ledger", methods=["GET"])
def autofit_ledger():
    """The active/most-recent plan run's ledger (report rendering)."""
    run_id = (request.args.get("run") or "").strip()
    eng = _autofit_engine_mod().get_engine(_sched_inst())
    if not run_id and eng is not None:
        run_id = eng.plan_run_id
    if not run_id:
        try:
            persisted = safe_io.read_json(Path(_sched_inst()) / "autofit_run.json")
            run_id = (persisted or {}).get("plan_run_id") or ""
        except (OSError, ValueError):
            run_id = ""
    if not re.match(r"^af_[a-f0-9]{6,}$", run_id or ""):
        return jsonify({"ok": False, "error": "no plan run"}), 404
    path = Path(_sched_inst()) / "autofit" / "runs" / run_id / "ledger.jsonl"
    events = []
    try:
        with open(path, encoding="utf-8") as fh:
            events = [json.loads(l) for l in fh]
    except (OSError, ValueError):
        return jsonify({"ok": False, "error": "ledger unreadable"}), 404
    return jsonify({"ok": True, "run": run_id, "events": events})


# ---- Autofit GUI diagnose (docs/56 §6R → GUI tier) -----------------------

@bp.route("/dataset/<uid>/autofit-diagnose", methods=["POST"])
def dataset_autofit_diagnose(uid):
    """Before/after diagnosis panel for one saved run: gate verdicts +
    node-faithful refit/replot + stored-vs-fresh values. Read-only over the
    archive; applies go through the audited /field/edit-batch path."""
    from quam_state_manager.core.autofit import replay as af_replay

    resolved = _resolve_run(uid)
    if not resolved:
        return render_template("_status.html", message="run not found",
                               level="error"), 404
    ds, run_id, _label = resolved
    run = ds.get_run(run_id)
    if not run:
        # mirror dataset_detail's brand-new-run fallback: one rescan, retry
        try:
            ds.rescan_if_stale()
            run = ds.get_run(run_id)
        except Exception:  # noqa: BLE001
            run = None
    if not run:
        return render_template("_status.html", message="run not found",
                               level="error"), 404
    folder = Path(run.get("folder_path") if isinstance(run, dict)
                  else run.folder_path)
    safe = re.sub(r"[^A-Za-z0-9_-]", "_", uid)
    out_dir = Path(current_app.instance_path) / "autofit" / "diagnose" / safe
    try:
        row = af_replay.evaluate_run(folder, out_dir, fix="none")
    except Exception as exc:  # noqa: BLE001
        logger.exception("autofit diagnose failed")
        return render_template("_status.html",
                               message=f"diagnose failed: {exc}",
                               level="error"), 500
    # refit figures are served by token = filename under the diagnose dir.
    # The runner may return WINDOWS paths (the QM env is a .exe) — normalize
    # separators before taking the basename, else POSIX Path.name keeps the
    # whole UNC string.
    refit_figs = [Path(str(f).replace("\\", "/")).name
                  for f in row.get("refit_figures") or []]
    # per-target apply payload lives in a JSON <script> block (tojson-escaped)
    # — NEVER inlined into an onclick attribute (a double-quoted attr can't
    # hold tojson's double quotes, and archive strings could break out)
    diag_data = {
        "family": row.get("family"),
        "parameters": row.get("parameters") or {},
        "targets": {q: (t.get("fresh_full") or t.get("fresh") or {})
                    for q, t in (row.get("targets") or {}).items()},
    }
    return render_template("_autofit_diagnose.html", uid=uid, row=row,
                           safe_uid=safe, refit_figs=refit_figs,
                           diag_data=diag_data,
                           chip_token=_active_chip_token() or "")


@bp.route("/dataset/<uid>/autofit-diagnose-fig/<name>")
def dataset_autofit_diagnose_fig(uid, name):
    """Serve a refit figure from the diagnose out dir (path-contained)."""
    safe = re.sub(r"[^A-Za-z0-9_-]", "_", uid)
    base = (Path(current_app.instance_path) / "autofit" / "diagnose"
            / safe).resolve()
    target = (base / name).resolve()
    if not str(target).startswith(str(base)) or not target.is_file() \
            or target.suffix != ".png":
        return "not found", 404
    from flask import send_file
    return send_file(target, mimetype="image/png")


@bp.route("/autofit/diagnose-rows", methods=["POST"])
def autofit_diagnose_rows():
    """Map a fresh refit result to writable state rows for the LOADED chip
    (families registry — never invents paths; ops like ceil4 applied).

    For the power-coupled rvp family the row set additionally carries the
    node-authored amplitude + shared-port FSP + feedline sibling rescales
    (docs/56 §6G — no silent partial write); when those can't be built the
    refusal reason rides back in ``power.skipped`` so the UI discloses that
    the frequency-only apply leaves power uncalibrated."""
    from quam_state_manager.core.autofit import families as af_families
    from quam_state_manager.core.autofit import power_rows as af_power

    data = request.get_json(silent=True) or {}
    fam = af_families.FAMILIES.get(str(data.get("family") or ""))
    target = str(data.get("target") or "")
    fresh = data.get("fresh") or {}
    store = _store()
    if fam is None or not target or store is None:
        return jsonify({"ok": False, "error": "no family/target/chip"}), 400

    def current_value_of(dotted):
        node = store.state
        for part in dotted.split("."):
            node = node[part]
        return node

    try:
        rows = af_families.resolve_updates(fam, target, dict(fresh),
                                           data.get("parameters") or {},
                                           current_value_of)
        power = None
        if fam.key == af_power.POWER_COUPLED_FAMILY:
            # the feedline port resolves through the wiring pointer chain
            # (`#/wiring/…/opx_output` → `#/ports/…`), so power rows MUST see
            # the MERGED state+wiring view — store.state alone lacks the
            # `wiring` key and every port lookup would (silently) refuse.
            pr = af_power.coupled_power_rows(fam.key, target, dict(fresh),
                                             store.merged)
            rows = rows + pr["rows"]
            power = {"applied": bool(pr["rows"]), "skipped": pr["skipped"],
                     "warnings": pr["warnings"]}
    except Exception as exc:  # noqa: BLE001
        return jsonify({"ok": False, "error": str(exc)}), 500
    return jsonify({"ok": True, "rows": rows, "power": power})
