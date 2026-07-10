"""Guard the Datasets robustness fixes (the secondary findings from the dead-click
root-cause hunt). Each is JS/CSS behaviour pytest can't execute, or a route-signature
contract, so they're pinned as source-contract tripwires (the full suite + the existing
dataset route tests exercise the behaviour)."""

import re
from pathlib import Path

_PKG = Path(__file__).resolve().parent.parent / "quam_state_manager"
_DV = (_PKG / "web" / "static" / "dataset-virtual.js").read_text(encoding="utf-8")
_APP = (_PKG / "web" / "static" / "app.js").read_text(encoding="utf-8")
_CSS = (_PKG / "web" / "static" / "style.css").read_text(encoding="utf-8")
_DST = (_PKG / "web" / "templates" / "_datasets.html").read_text(encoding="utf-8")
_ROUTES = (_PKG / "web" / "routes.py").read_text(encoding="utf-8")


def _fn(src, marker, n=900):
    i = src.index(marker)
    return src[i:i + n]


class TestVirtualScrollerPressRace:
    def test_row_press_and_click_mark_interaction(self):
        # A clicker must count as interacting so the 60s delta-poll merge defers and
        # can't rebuild tbody.innerHTML between press and click (the press→click race).
        pd = _fn(_DV, "function onTbodyPointerDown", 900)
        assert "markInteraction()" in pd and "state.pressActive = true" in pd
        cl = _fn(_DV, "function onTbodyClick", 400)
        assert "markInteraction()" in cl and "clearPress()" in cl

    def test_render_deferred_during_live_press(self):
        sr = _fn(_DV, "function scheduleRender", 1100)
        assert "state.pressActive" in sr   # re-schedule while a press is live

    def test_scroll_frames_use_window_dedup(self):
        assert "scheduleRender(false)" in _DV   # onScroll skips rebuild on unchanged window

    def test_scroll_releases_the_press_freeze(self):
        # A touch finger-scroll is a pointerdown with no click; onScroll must drop the
        # freeze (else the table stays blank until the 1500ms safety timeout).
        sc = _fn(_DV, "function onScroll", 500)
        assert "clearPress()" in sc

    def test_pointerup_and_cancel_release_the_press(self):
        # Belt-and-suspenders: lifting the pointer / the browser taking the gesture for
        # scrolling (pointercancel) ends the freeze without waiting on the timeout.
        assert "addEventListener('pointerup', clearPress)" in _DV
        assert "addEventListener('pointercancel', clearPress)" in _DV

    def test_safety_timeout_is_per_press(self):
        # The 1500ms safety timeout id is stored and cleared on each new press / clearPress
        # so a stale prior-press timeout can't reset pressActive mid a later press.
        assert "clearTimeout(state._pressTimer)" in _DV
        assert "state._pressTimer = setTimeout(" in _DV


class TestDetailReissueBackstop:
    def test_reissue_skips_an_in_flight_load(self):
        # The 300ms backstop must NOT abort a slow-but-healthy first GET (htmx marks the
        # source pane .htmx-request while pending) — else it adds latency on the very
        # rescan-on-miss path it should let finish.
        od = _fn(_DV, "function openDatasetDetail", 1500)
        assert "classList.contains('htmx-request')" in od

    def test_reissue_one_shot_is_per_id(self):
        # A fast second click on a DIFFERENT run keeps its own backstop (per-id Set/map),
        # not a single global flag that one run's reissue would suppress.
        od = _fn(_DV, "function openDatasetDetail", 1500)
        assert "state._reissuedIds[id]" in od
        assert "_detailReissued" not in _DV   # the global one-shot must not creep back


class TestDatasetDetailScanFree:
    def test_resolve_run_defaults_to_no_rescan(self):
        assert re.search(r"def _resolve_run\(uid: str, rescan: bool = False\)", _ROUTES)

    def test_get_or_create_store_takes_rescan_flag(self):
        assert re.search(r"def _get_or_create_store\(folder: Path, rescan: bool = True\)", _ROUTES)

    def test_detail_rescans_only_on_miss(self):
        dd = _fn(_ROUTES, "def dataset_detail", 1100)
        assert "rescan_if_stale on dataset_detail miss" in dd

    def test_single_folder_drift_fallback_is_run_gated(self):
        # The lone-candidate fallback must only resolve when that folder ACTUALLY holds
        # run_id (so a drifted key whose run doesn't exist 404s honestly), and it must
        # log when it fires so a wrong-run collision is diagnosable.
        sf = _fn(_ROUTES, "def _store_for_folder_key", 3800)
        assert "if len(cands) == 1:" in sf
        assert "store.get_run(run_id) is not None" in sf
        assert "single-folder drift fallback" in sf
        # run_id is threaded from _resolve_run into the gate.
        assert "_store_for_folder_key(folder_key, rescan=rescan, run_id=run_id)" in _ROUTES


class TestOverlaysAndPanels:
    def test_zoom_dismisses_on_escape_and_outside_click(self):
        tz = _fn(_APP, "window.toggleFigureZoom", 1000)
        assert "'Escape'" in tz and "pointerdown" in tz

    def test_zoom_listeners_torn_down_on_htmx_swap(self):
        # If an htmx swap detaches a still-zoomed <img>, its capture-phase document
        # listeners must be cleaned up deterministically (not left dangling until the
        # next pointer/key event).
        assert "img.figure-zoomed" in _APP
        assert "_zoomCleanup()" in _APP

    def test_empty_review_overlay_cannot_trap_clicks(self):
        assert ".state-review-host > *)) { display: none" in _CSS

    def test_search_help_height_capped(self):
        block = _fn(_CSS, ".ds-search-help-panel {", 700)
        assert "max-height: 60vh" in block and "overflow-y: auto" in block

    def test_datasets_scroll_has_scroll_padding(self):
        assert "scroll-padding-top:" in _CSS


class TestSingleSearchBinding:
    def test_inline_oninput_removed(self):
        assert 'oninput="filterDatasetTable' not in _DST
        assert "addEventListener('input', onSearchInput)" in _DV
