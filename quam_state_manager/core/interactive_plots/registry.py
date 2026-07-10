"""Dispatch a run to its interactive-plot recipe + capability detection.

Recipes are matched by ``node.json`` ``metadata.name`` prefix (read fresh,
falling back to the folder-derived ``experiment_name``), because the same
folder name can map to different ``ds_fit`` schemas — the recipe then
capability-detects from the variables actually present.
"""
from __future__ import annotations

import logging
import os
import re
import threading
from collections import OrderedDict
from pathlib import Path

from quam_state_manager.core import safe_io
from quam_state_manager.core.dataset import _extract_figure_names

from . import h5reader
from .plotbuild import jsonable
from .recipes import (
    all_xy, chevron, cz_2d_maps, cz_phase, drag, fallback, flux_qubitspec, flux_ramsey,
    flux_short_distortion, iq_blobs, iq_blobs_gef, power_rabi, qubit_spec_vs_flux,
    qubit_spectroscopy, ramsey, ramsey_vs_flux, rb, readout_opt, resonator, resonator_2d,
    time_of_flight, two_qubit_rb, xyz_delay,
)
from .recipes.base import Bundle, FigureSpec, split_key

logger = logging.getLogger(__name__)

# Ordered; first FAMILY-prefix match wins. fallback is implicit (empty menu).
# Note: more-specific prefixes must precede less-specific ones if they'd collide;
# the current node names don't collide across families.
_RECIPES = [
    resonator, power_rabi, flux_qubitspec, flux_ramsey,
    qubit_spectroscopy, ramsey, rb, all_xy, xyz_delay, drag, readout_opt,
    resonator_2d, qubit_spec_vs_flux, iq_blobs_gef, iq_blobs, time_of_flight,
    ramsey_vs_flux, flux_short_distortion, chevron, two_qubit_rb, cz_phase,
    cz_2d_maps,
]


def _normalize_node_name(name: str) -> str:
    """Strip the graph-launch prefix + case so both launch conventions match.

    The same lab produces ``1Q_03_resonator_spectroscopy`` (graph-launched) AND
    ``03_resonator_spectroscopy`` (standalone) for the SAME node — the recipes'
    FAMILY strings carry the graph prefix, so standalone runs matched NOTHING
    (0% on recent sessions). Normalize both sides: drop a leading ``1Q_``/``2Q_``
    and any leading numeric index (``03_``/``05b_``), lowercase."""
    import re as _re
    n = _re.sub(r"^[12]Q_", "", name)
    # cz_ graph-launch prefix (e.g. "cz_20d_..." / "cz_35_...") — strip ONLY
    # when followed by a node number so legit "cz_conditional..." names survive.
    n = _re.sub(r"^cz_(?=[0-9])", "", n)
    n = _re.sub(r"^[0-9]+(?:_[0-9]+)?[a-z]?_", "", n)
    return n.lower()


def _resolve(name: str):
    # TIER 1 — the original raw prefix match (unchanged semantics: the FAMILY
    # strings carry the graph prefix + numeric index, which disambiguates
    # variants like 05b_..._iq vs 05_...). Zero regression by construction.
    for recipe in _RECIPES:
        for prefix in getattr(recipe, "FAMILY", ()):
            if name.startswith(prefix):
                return recipe
    # TIER 2 — normalized match for STANDALONE-launched runs (same node, no
    # graph prefix: "03_resonator_spectroscopy_single" vs FAMILY
    # "1Q_03_resonator_spectroscopy") and case drift. Longest normalized prefix
    # wins, and the remainder must be a launch-variant suffix (_single/_pyloop/
    # _mw_fem), digits/underscores (level indices like chevron _1102), or empty —
    # an unknown word qualifier ("_vs_power", "_iq") is a DIFFERENT experiment
    # whose figures the recipe can't rebuild.
    norm = _normalize_node_name(name)
    best, best_len = None, -1
    for recipe in _RECIPES:
        for prefix in getattr(recipe, "FAMILY", ()):
            np_ = _normalize_node_name(prefix)
            if not norm.startswith(np_):
                continue
            rest = norm[len(np_):]
            # "_wide_pyloop"/"_wide_python_loop": per-qubit-panel wide scans the
            # resonator recipe already handles (absolute-assign contract). The
            # NON-pyloop "_wide" stays EXCLUDED: its panels are per-LINE-probe
            # (a clicked dip belongs to another feedline qubit) — matching it
            # would stage the wrong qubit's frequency.
            benign = (rest == "" or rest.startswith(("_single", "_pyloop", "_mw_fem",
                                                     "_wide_pyloop", "_wide_python_loop",
                                                     "_new", "_coarse"))
                      or re.fullmatch(r"[_0-9]+", rest) is not None)
            if benign and len(np_) > best_len:
                best, best_len = recipe, len(np_)
    return best if best is not None else fallback


def _node_name(run) -> tuple[str, dict]:
    """(metadata.name, node.json) read fresh; fall back to folder experiment_name."""
    try:
        node = safe_io.read_json(Path(run.folder_path) / "node.json")
        name = ((node.get("metadata") or {}).get("name")) or ""
        if name:
            return name, node
    except (OSError, ValueError):
        pass
    return getattr(run, "experiment_name", "") or "", {}


def _saved_figures(run) -> list[str]:
    """Saved-figure keys from the run's ``data.json`` (same set the Figures tab shows)."""
    try:
        data = safe_io.read_json(Path(run.folder_path) / "data.json")
    except (OSError, ValueError):
        return []
    return _extract_figure_names(data) if isinstance(data, dict) else []


def _saved_base(key: str, qnames) -> str:
    """Normalize a ``data.json`` figure key to a recipe *base* name.

    Drops the container prefix (``figures.raw_data`` → ``raw_data``) and a
    trailing ``_<qubit>`` (``raw_qA1`` → ``raw``) so saved-figure keys line up
    with the per-qubit ``<base>::<qubit>`` keys recipes emit.
    """
    s = key.split(".", 1)[1] if "." in key else key
    for q in qnames:
        if q and s.endswith("_" + q):
            return s[: -(len(q) + 1)]
    return s


def _merge_static(specs, saved, qnames) -> list:
    """Make the Interactive tab a superset of the Figures tab.

    Each recipe-reconstructed figure stays interactive. Any saved figure not
    covered by an *available* interactive tile is appended as a static-PNG tile.
    A recipe figure that is *unavailable* but has a matching saved PNG is dropped
    in favor of that static tile (the PNG is more useful than a greyed stub).
    """
    avail_bases = {split_key(s.key)[0] for s in specs if s.available}
    saved_bases = {_saved_base(s, qnames) for s in saved}

    out = []
    for spec in specs:
        base = split_key(spec.key)[0]
        if spec.available or base not in saved_bases:
            out.append(spec)  # interactive, or a greyed stub with no PNG to fall back to

    seen = set()
    for fig in saved:
        if fig in seen or _saved_base(fig, qnames) in avail_bases:
            continue
        seen.add(fig)
        title = (fig.split(".", 1)[1] if "." in fig else fig).replace("_", " ")
        out.append(FigureSpec(key=fig, title=title, kind="static"))
    return out


def list_interactive_figures(run) -> list[dict]:
    """Cheap figure *menu* for a run: ``[{key,title,kind,available,reason,static}]``.

    Interactive (recipe-reconstructed) figures first, then static-PNG tiles for
    any saved figure the recipe doesn't reproduce.
    """
    name, node_meta = _node_name(run)
    raw_p = h5reader.probe_vars(run, "ds_raw") or {"vars": {}, "coords": {}}
    fit_p = h5reader.probe_vars(run, "ds_fit") or {"vars": {}, "coords": {}}
    iqb_p = h5reader.probe_vars(run, "ds_iq_blobs") or {"vars": {}}
    qnames = (raw_p.get("qubits") or fit_p.get("qubits")
              or [str(q) for q in (getattr(run, "qubits", None) or [])])
    bundle = Bundle(
        run=run, node_meta=node_meta, fit_results=getattr(run, "fit_results", {}) or {},
        raw_vars=set(raw_p["vars"]), fit_vars=set(fit_p["vars"]),
        raw_coords=set(raw_p.get("coords", {})), fit_coords=set(fit_p.get("coords", {})),
        raw_shapes=raw_p["vars"], fit_shapes=fit_p["vars"],
        iqblobs_vars=set(iqb_p["vars"]),
        qubit_names=qnames,
    )
    recipe = _resolve(name)
    try:
        specs = list(recipe.menu(bundle))
    except Exception:  # noqa: BLE001 — a broken recipe must not 500 the menu
        logger.exception("interactive menu failed for %s", name)
        specs = []
    # Static-PNG fallback runs even for the empty-menu fallback recipe, so a run
    # with figures is never blank in the Interactive tab.
    specs = _merge_static(specs, _saved_figures(run), [str(q) for q in qnames])
    return [{"key": s.key, "title": s.title, "kind": s.kind,
             "available": s.available, "reason": s.reason,
             "static": s.kind == "static"} for s in specs]


# ──────────────────────────────────────────────────────────────────────────
# Bundle-input cache — the figure-build hot path re-read EVERY input file per
# tile request (ds_raw/ds_fit/ds_iq_blobs fully materialized + node.json +
# state.json/wiring.json), so warm == cold (~250–320 ms/tile on 9p). Run
# archives are write-once after a run completes; key on the folder + an
# mtime/size fingerprint of exactly the files the build reads (same discipline
# as the ndview cube cache — if a lab rewrites analysis in place, mtime moves
# and the key heals). Cached arrays are plain in-memory numpy (load_dataset
# materializes via ``np.asarray(d[()])`` and closes the file) and recipes only
# ever read/slice them (np.take copies), so entries are safe to share across
# requests/threads. Entry count 4 bounds memory (same data the ndview cube LRU
# already holds decimated copies of).
# ──────────────────────────────────────────────────────────────────────────

_BUNDLE_CACHE_MAX = 4
_bundle_cache: OrderedDict[str, tuple[tuple, dict]] = OrderedDict()
_bundle_cache_lock = threading.Lock()

# Exactly the per-run files build_interactive_figure reads.
_BUNDLE_INPUT_FILES = (
    "ds_raw.h5", "ds_fit.h5", "ds_iq_blobs.h5", "node.json",
    os.path.join("quam_state", "state.json"),
    os.path.join("quam_state", "wiring.json"),
)


def _bundle_fingerprint(folder: str) -> tuple:
    """(mtime_ns, size) per input file; (None, None) for absent files — a file
    appearing/vanishing changes the fingerprint just like a rewrite does."""
    parts = []
    for rel in _BUNDLE_INPUT_FILES:
        try:
            st = os.stat(os.path.join(folder, rel))
            parts.append((st.st_mtime_ns, st.st_size))
        except OSError:
            parts.append((None, None))
    return tuple(parts)


def _load_bundle_inputs(run) -> dict:
    """One full disk pass: everything build_interactive_figure needs."""
    name, node_meta = _node_name(run)
    return {
        "name": name,
        "node_meta": node_meta,
        "raw": h5reader.load_dataset(run, "ds_raw"),
        "fit": h5reader.load_dataset(run, "ds_fit"),
        "iqb": h5reader.load_dataset(run, "ds_iq_blobs"),
        "quam_state": h5reader.load_quam_state(run),
    }


def _bundle_inputs(run) -> dict:
    """Fingerprint-validated, thread-safe LRU over ``_load_bundle_inputs``."""
    folder = str(getattr(run, "folder_path", "") or "")
    fp = _bundle_fingerprint(folder)
    with _bundle_cache_lock:
        hit = _bundle_cache.get(folder)
        if hit is not None and hit[0] == fp:
            _bundle_cache.move_to_end(folder)
            return hit[1]
    inputs = _load_bundle_inputs(run)   # build OUTSIDE the lock (slow 9p I/O)
    with _bundle_cache_lock:
        _bundle_cache[folder] = (fp, inputs)
        _bundle_cache.move_to_end(folder)
        while len(_bundle_cache) > _BUNDLE_CACHE_MAX:
            _bundle_cache.popitem(last=False)
    return inputs


def _shapes_of(ds: dict | None) -> dict:
    """``{var: shape}`` from a loaded dataset's attrs — the exact payload
    probe_vars returned (attrs carries ``shape`` even for oversized-skipped
    vars), so the two extra probe_vars h5 opens per build were pure waste."""
    if not ds:
        return {}
    return {n: meta["shape"] for n, meta in ds.get("attrs", {}).items()
            if isinstance(meta, dict) and "shape" in meta}


def build_interactive_figure(run, key: str) -> dict | None:
    """Full Plotly JSON for one figure key: ``{data, layout, kind, title, clickable}``."""
    inputs = _bundle_inputs(run)
    name, node_meta = inputs["name"], inputs["node_meta"]
    recipe = _resolve(name)
    raw, fit, iqb = inputs["raw"], inputs["fit"], inputs["iqb"]
    bundle = Bundle(
        run=run, node_meta=node_meta, fit_results=getattr(run, "fit_results", {}) or {},
        raw=raw, fit=fit, iqblobs=iqb,
        raw_vars=set(raw["vars"]) if raw else set(),
        fit_vars=set(fit["vars"]) if fit else set(),
        raw_coords=set(raw["coords"]) if raw else set(),
        fit_coords=set(fit["coords"]) if fit else set(),
        iqblobs_vars=set(iqb["vars"]) if iqb else set(),
        raw_shapes=_shapes_of(raw), fit_shapes=_shapes_of(fit),
        quam_state=inputs["quam_state"],
    )
    try:
        spec = recipe.build(bundle, key)
    except Exception:  # noqa: BLE001 — one bad figure must not 500 the tab
        logger.exception("interactive build failed for %s key=%s", name, key)
        return None
    if spec is None or spec.figure is None or not spec.available:
        return None
    fig = spec.figure
    return {
        "kind": spec.kind,
        "title": spec.title,
        "clickable": spec.clickable,
        "data": jsonable(fig.get("data", [])),
        "layout": jsonable(fig.get("layout", {})),
    }
