"""v1.3.0 B2 -- the every-page layout sweep (1600x950 and 1280x800, light and dark).

Each pin below is a defect seen in real Chrome on a customer-chip copy and fixed in
CSS / a template / one JS line. Layout itself cannot be measured here (no browser),
so the pins hold the rule that made the difference; the browser evidence is in the
B2 report.
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
WEB = ROOT / "quam_state_manager" / "web"


def _css() -> str:
    return (WEB / "static" / "style.css").read_text(encoding="utf-8")


def _tpl(name: str) -> str:
    return (WEB / "templates" / name).read_text(encoding="utf-8")


def _block(css: str, head: str) -> str:
    """The declaration block that starts at the (unique) selector text `head`."""
    assert css.count(head) == 1, (head, css.count(head))
    i = css.index(head)
    return css[i:css.index("}", i)]


class TestAutoSyncLabel:
    def test_the_label_is_one_flex_item(self):
        """The pill is inline-flex with a gap, so a bare "Auto" text node and the
        "-Sync" span were two flex items and read "Auto -Sync"."""
        tpl = _tpl("_auto_sync_pill.html")
        assert tpl.count('<span class="aa-label">Auto<span class="aa-long">-Sync</span></span>') == 3
        assert "}} Auto<span" not in tpl


class TestParamHistoryChanges:
    def test_the_writer_column_wraps_between_its_phrases(self):
        css = _css()
        who = _block(css, ".ph-change-who { font-size")
        assert "nowrap" not in who and "width: 1%" not in who
        sub = _block(css, ".ph-change-who .vh-sub {")
        assert "inline-block" in sub and "nowrap" in sub

    def test_the_values_wrap_as_units_and_the_path_has_a_floor(self):
        css = _css()
        vals = _block(css, ".ph-change-old, .ph-change-new {")
        assert "nowrap" not in vals
        piece = _block(css, ".ph-change-new .ba-old, .ph-change-new .ba-new,")
        assert "inline-block" in piece and "overflow-wrap: anywhere" in piece
        path = _block(css, ".ph-change-path {")
        assert "min-width" in path and "45%" not in path


class TestDiffList:
    def test_values_may_break_and_the_path_has_a_floor(self):
        css = _css()
        vals = _block(css, ".diff-list-a, .diff-list-b {")
        assert "nowrap" not in vals and "overflow-wrap: anywhere" in vals
        path = _block(css, ".diff-list-path {")
        assert "min-width" in path and "45%" not in path
        assert "nowrap" in _block(css, ".diff-list-take {")


class TestQualibratePage:
    def test_an_unset_version_never_reads_none(self):
        from quam_state_manager.core import qualibrate_config as qc
        found = qc.lint({"config_exists": True, "ok": True, "projects": [],
                         "versions": {"qualibrate": 6, "quam": None, "supported": False}})
        msg = [f["message"] for f in found if f["code"] == "version_drift"][0]
        assert "quam=not set" in msg and "None" not in msg
        tpl = _tpl("_qualibrate.html")
        assert "quam v{{ listing.versions.quam }}" not in tpl
        assert "(version not set)" in tpl

    def test_paths_break_and_the_open_button_does_not(self):
        css = _css()
        assert "overflow-wrap: anywhere" in _block(css, "#qualibrate-table .qualibrate-path code {")
        assert "nowrap" in _block(css, "#qualibrate-table button {")


class TestContrast:
    @pytest.mark.parametrize("head", [
        ".topo-hero-zbtn.active, .iw-size-btn.active {",
        ".bulk-chip.active {",
        ".fh-now {",
        ".ch-chip-now {",
        ".topo-hero-mbtn.active {",
        ".topo-trend-chip.active {",
    ])
    def test_active_fills_use_the_button_blue(self, head):
        """White on the dark theme's link cyan (--pico-primary) measured 2.56:1;
        the button blue is the same colour as --pico-primary in light."""
        block = _block(_css(), head)
        assert "background: var(--pico-primary-background)" in block
        assert "background: var(--pico-primary);" not in block

    def test_the_selected_font_size_hint_is_readable(self):
        block = _block(_css(), ".settings-opt-active .muted {")
        assert "color: inherit" in block

    def test_light_topbar_metadata_clears_three_to_one(self):
        css = _css()
        block = _block(css, '[data-theme="light"] .state-status-name, [data-theme="light"] .state-version-count {')
        op = float(re.search(r"opacity:\s*([\d.]+)", block).group(1))
        assert op >= 0.85


class TestWording:
    def test_prev_state_counts_carry_their_word(self):
        tpl = _tpl("_dataset_prev_diff.html")
        assert "~{{ summary.modified }}" not in tpl
        assert "{{ summary.modified }} changed</span>" in tpl
        assert "{{ summary.added }} added</span>" in tpl
        assert "{{ summary.removed }} removed</span>" in tpl

    def test_an_empty_collection_does_not_blame_filters(self):
        tpl = _tpl("_datasets.html")
        i = tpl.index('<p id="datasets-empty"')
        block = tpl[i:tpl.index("</p>", i)]
        assert "{% if not total and is_collections %}" in block
        assert "No tagged runs yet." in block
        assert block.index("No tagged runs yet.") < block.index("Clear all filters")

    def test_the_title_notes_are_not_heading_size(self):
        block = _block(_css(), ".ds-head-title .ds-tz-note, .ds-head-title .ds-scan-note {")
        assert "font-size: .5em" in block and "font-weight: 400" in block


class TestChipStatus:
    def test_ten_tabs_fit_one_row_at_1280(self):
        """At .7rem side padding the bar overflowed by under a pixel at 1280 px
        and "Calibration" wrapped alone; the taller bar then hid every jumped-to
        section header (the scroll margin is one row)."""
        block = _block(_css(), ".topo-subnav-btn {\n")
        pad = re.search(r"padding:\s*[\d.]+rem\s+([\d.]+)rem", block)
        assert pad and float(pad.group(1)) <= 0.6

    def test_tile_sub_pieces_never_break_inside(self):
        assert "white-space: nowrap" in _block(_css(), ".topo-card-sub .tcs-k {")
        proc = subprocess.run(["node", str(ROOT / "tests" / "topo_tile_sub_selfcheck.cjs")],
                              capture_output=True, text=True, encoding="utf-8", timeout=120)
        assert proc.returncode == 0, proc.stdout + proc.stderr
        assert "assertions passed" in proc.stdout
