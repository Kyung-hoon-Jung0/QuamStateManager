"""The diff workbench's stress-walk fixes (2026-11-03).

DS-4: the loaded chip is labelled "with unsaved edits" only when the working
copy really holds something the chip file does not (a tray entry, a staged
snapshot, saved-but-unapplied content).
DS-2: 2-5 sources stay readable: figure columns keep a minimum width and the
figure name is a full-width row heading; pane titles and values wrap; a
sideways-scrolling block carries a hint naming the sources out of view
(client half: tests/diff_xscroll_selfcheck.cjs).
DS-5: a comparison request that never reached the app says so in the pane
with a Retry (same selfcheck).
"""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from quam_state_manager.web.app import create_app

_ROOT = Path(__file__).resolve().parent.parent
_WIRING = {"wiring": {}, "network": {}}


def _write(folder: Path, t1=1.0e-5):
    folder.mkdir(parents=True, exist_ok=True)
    state = {"qubits": {"qA1": {"id": "qA1", "T1": t1}}, "qubit_pairs": {},
             "active_qubit_names": ["qA1"]}
    (folder / "state.json").write_text(json.dumps(state), encoding="utf-8")
    (folder / "wiring.json").write_text(json.dumps(_WIRING), encoding="utf-8")


@pytest.fixture
def client(tmp_path):
    live = tmp_path / "chip"
    _write(live)
    app = create_app(testing=True, instance_path=str(tmp_path / "_inst"))
    c = app.test_client()
    assert c.post("/load", data={"folder": str(live)}).status_code in (200, 302)
    return app, c


def _label(c) -> str:
    html = c.get("/diff").get_data(as_text=True)
    assert "Loaded chip" in html
    return html


class TestWorkingLabel:
    def test_a_clean_chip_is_not_called_edited(self, client):
        _app, c = client
        html = _label(c)
        assert "with unsaved edits" not in html

    def test_an_edit_makes_the_label_say_so_and_apply_clears_it(self, client):
        _app, c = client
        r = c.post("/field/edit", data={"dot_path": "qubits.qA1.T1", "value": "2e-05"})
        assert r.status_code == 200
        assert "Loaded chip (with unsaved edits)" in _label(c)
        assert c.post("/state/apply-to-live", data={"force": "1"}).status_code == 200
        assert "with unsaved edits" not in _label(c)

    def test_saved_but_unapplied_content_counts(self, client):
        app, c = client
        ctx = next(iter(app.config["contexts"].values()))
        ctx["working_dirty"] = True
        assert "Loaded chip (with unsaved edits)" in _label(c)
        ctx["working_dirty"] = False
        assert "with unsaved edits" not in _label(c)

    def test_a_pending_reapply_stash_counts(self, client):
        app, c = client
        ctx = next(iter(app.config["contexts"].values()))
        ctx["pending_reapply"] = {"qubits.qA1.T1": 2e-05}
        assert "Loaded chip (with unsaved edits)" in _label(c)

    def test_the_pane_header_says_what_the_picker_says(self, client):
        app, c = client
        live = next(iter(app.config["contexts"].values()))["path"]
        from urllib.parse import quote
        r = c.post("/field/edit", data={"dot_path": "qubits.qA1.T1", "value": "2e-05"})
        assert r.status_code == 200
        html = c.get("/diff?a=" + quote("working:" + live) + "&b=" + quote("working:" + live)
                     + "&tab=state&view=panes").get_data(as_text=True)
        assert "· working" not in html
        assert html.count("Loaded chip (with unsaved edits)") >= 2


class TestReadableLayout:
    def test_figure_columns_keep_a_minimum_and_the_name_spans_the_row(self):
        tpl = (_ROOT / "quam_state_manager/web/templates/_diff_workbench.html").read_text(encoding="utf-8")
        css = (_ROOT / "quam_state_manager/web/static/ui-kit.css").read_text(encoding="utf-8")
        assert "minmax(var(--ui-diff-fig-min), 1fr)" in tpl
        assert "--ui-diff-fig-min: 240px;" in css
        assert "max-content repeat(" not in tpl          # the old name column
        assert ".ui-figs .diff-fig-name { grid-column: 1 / -1;" in css
        assert 'class="diff-wb-figs compare-figure-grid ui-figs"' in tpl
        assert 'class="diff-xscroll ui-xscroll" data-xscroll' in tpl
        assert 'data-xcol="{{ col.slot | upper }}"' in tpl

    def test_pane_titles_and_values_wrap_and_keys_stay_pinned(self):
        tpl = (_ROOT / "quam_state_manager/web/templates/_diff_panes.html").read_text(encoding="utf-8")
        css = (_ROOT / "quam_state_manager/web/static/ui-kit.css").read_text(encoding="utf-8")
        assert '<div class="diff-panes-scroll ui-xscroll-both" data-xscroll>' in tpl
        assert '<table class="diff-panes-table ui-dpanes" id="diff-panes-table">' in tpl
        assert '<div class="diff-xscroll-hint ui-xhint" hidden></div>' in tpl
        assert ".ui-dpanes .dp-key-col { min-width: 9rem; max-width: 15rem; position: sticky; left: 0;" in css
        assert "white-space: normal; overflow-wrap: break-word; }" in css
        # F2: a deep key wraps instead of being cut with an ellipsis
        assert "white-space: normal; overflow: visible; text-overflow: clip; overflow-wrap: break-word; }" in css
        # F11: the pane scroller scrolls both axes, so sticky titles hold
        assert ".ui-xscroll-both { max-height: max(18rem," in css
        assert ".ui-printing .ui-xscroll, .ui-printing .ui-xscroll-both { overflow: visible; max-height: none; }" in css
        js = (_ROOT / "quam_state_manager/web/static/diff-panes.js").read_text(encoding="utf-8")
        assert "'beforeprint'" in js and "'afterprint'" in js and "ui-printing" in js

    def test_the_sticky_key_column_is_opaque_and_pane_letters_stay(self):
        """2026-11-04: a folder row's key cell mixed a TRANSLUCENT border colour
        into the background, so panes scrolling under it showed through; and a
        pane partly under the key column lost its letter."""
        css = (_ROOT / "quam_state_manager/web/static/ui-kit.css").read_text(encoding="utf-8")
        start = css.index(".ui-dpanes tr.dp-dir > td.dp-key-col {")
        rule = css[start:css.index("}", start)]
        assert "background-color: var(--ui-surface-0);" in rule
        assert "linear-gradient(var(--ui-ds-dir-tint), var(--ui-ds-dir-tint))" in rule
        assert "--ui-ds-dir-tint: color-mix(in srgb, var(--pico-muted-border-color) 28%, transparent);" in css
        assert (".ui-dpanes .dp-pane-head .dp-slot { position: sticky; "
                "left: calc(var(--dp-keys-w, 0px) + 0.3rem); align-self: flex-start; }") in css


def test_diff_xscroll_selfcheck():
    node = shutil.which("node")
    if node is None or subprocess.run([node, "-e", "require('jsdom')"], capture_output=True, cwd=str(_ROOT)).returncode != 0:
        pytest.skip("jsdom not installed for node")
    res = subprocess.run([node, str(_ROOT / "tests" / "diff_xscroll_selfcheck.cjs")],
                         capture_output=True, text=True, encoding="utf-8", cwd=str(_ROOT))
    assert res.returncode == 0, res.stdout + res.stderr
    assert "ok - the hint names the panes out of view" in res.stdout
    assert "ok - but a pane whose header is cut is named as partly out of view" in res.stdout
    assert "ok - a single column cut at the right edge (its header cut) gets a hint" in res.stdout
    assert "ok - a failed compare request leaves a message and a Retry" in res.stdout
    assert "ok - a picker GET retries through the form, so its sources ride along" in res.stdout
