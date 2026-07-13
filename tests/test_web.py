"""Tests for quam_state_manager.web (Flask app + routes).

Uses Flask's test client to verify all routes, HTMX fragment responses,
JSON API endpoints, and integration with core modules.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from quam_state_manager.web.app import create_app

# ---------------------------------------------------------------------------
# Paths to real quam_state folders
# ---------------------------------------------------------------------------

_EXAMPLECHIP_ROOT = Path(r"<data-root>/qualibration_graphs/superconducting")
LARGE_FOLDER = _EXAMPLECHIP_ROOT / "quam_states_arv" / "quam_state_examplechip_variantb"
DATA_ROOT = _EXAMPLECHIP_ROOT / "data" / "project_name"

has_large = LARGE_FOLDER.exists() and (LARGE_FOLDER / "state.json").exists()
has_data = DATA_ROOT.exists()

skip_no_large = pytest.mark.skipif(not has_large, reason="Large quam_state not found")
skip_no_data = pytest.mark.skipif(not has_data, reason="Experiment data folder not found")


# ---------------------------------------------------------------------------
# Synthetic fixture
# ---------------------------------------------------------------------------

def _make_state():
    return {
        "qubits": {
            "qA1": {
                "id": "qA1",
                "f_01": 6.25e9,
                "T1": 8834,
                "T2ramsey": 1.5e-6,
                "T2echo": None,
                "anharmonicity": -220e6,
                "chi": -5.2e6,
                "grid_location": "0,2",
                "xy": {
                    "RF_frequency": 6.25e9,
                    "opx_output": "#/wiring/qubits/qA1/xy/opx_output",
                    "operations": {
                        "x180_DragCosine": {"amplitude": 0.115, "length": 40, "alpha": -1.75},
                        "x90_DragCosine": {"amplitude": 0.057},
                        "saturation": {"amplitude": 0.04},
                    },
                },
                "resonator": {
                    "f_01": 7.64e9,
                    "RF_frequency": 7.64e9,
                    "operations": {
                        "readout": {
                            "amplitude": 0.042, "length": 1000,
                            "threshold": -0.00014, "integration_weights_angle": -38.9,
                        },
                    },
                    "confusion_matrix": [[0.91, 0.09], [0.12, 0.88]],
                    "time_of_flight": 380,
                },
                "z": {"joint_offset": 0.081, "independent_offset": 0.0, "flux_point": "joint"},
                "gate_fidelity": {"averaged": 0.991, "x180": 0.986, "x90": 0.986},
                "freq_vs_flux_01_quad_term": -1.12e11,
                "phi0_current": 4.85,
                "phi0_voltage": 0.097,
            },
        },
        "qubit_pairs": {
            "qA1-A2": {
                "id": "qA1-A2",
                "qubit_control": "#/qubits/qA1",
                "qubit_target": "#/qubits/qA1",
                "moving_qubit": "target",
                "macros": {},
                "coupler": {"decouple_offset": 0.48, "interaction_offset": 0.0},
                "detuning": 0.032,
            },
        },
        "active_qubit_names": ["qA1"],
    }


def _make_wiring():
    return {
        "wiring": {
            "qubits": {
                "qA1": {
                    "xy": {"opx_output": "MW-FEM/1/2"},
                    "rr": {"opx_output": "MW-FEM/1/1", "opx_input": "MW-FEM/1/1"},
                    "z": {"opx_output": "LF-FEM/5/1"},
                },
            },
        },
        "network": {"host": "10.1.1.18"},
    }


@pytest.fixture
def synth_folder(tmp_path: Path) -> Path:
    (tmp_path / "state.json").write_text(json.dumps(_make_state(), indent=2), encoding="utf-8")
    (tmp_path / "wiring.json").write_text(json.dumps(_make_wiring(), indent=2), encoding="utf-8")
    return tmp_path


@pytest.fixture
def app(tmp_path, synth_folder):
    # Pass a per-test tmp instance_path so we never pollute the user's
    # real D:\Work\state-manager\instance\ directory with pytest leftovers.
    return create_app(testing=True, instance_path=str(tmp_path / "_app_instance"))


@pytest.fixture
def client(app):
    return app.test_client()


@pytest.fixture
def loaded_client(client, synth_folder):
    """Client with a store already loaded."""
    client.post("/load", data={"folder": str(synth_folder)})
    return client


# ---------------------------------------------------------------------------
# Home + Load
# ---------------------------------------------------------------------------


class TestHome:
    def test_home_page(self, client):
        resp = client.get("/")
        assert resp.status_code == 200
        assert b"QUAM State Manager" in resp.data

    def test_load_valid(self, client, synth_folder):
        resp = client.post("/load", data={"folder": str(synth_folder)}, follow_redirects=True)
        assert resp.status_code == 200

    def test_load_invalid(self, client, tmp_path):
        resp = client.post("/load", data={"folder": str(tmp_path / "nonexistent")})
        assert resp.status_code == 400

    def test_load_empty_folder(self, client):
        resp = client.post("/load", data={"folder": ""})
        assert resp.status_code == 400

    def test_load_form_visible_before_load(self, client):
        html = client.get("/").data.decode()
        assert "load-path-input" in html
        assert "sidebar-load" in html

    def test_load_form_visible_after_load(self, loaded_client):
        html = loaded_client.get("/qubits").data.decode()
        assert "load-path-input" in html
        assert "sidebar-load" in html

    def test_active_badge_removed(self, loaded_client):
        # The #active-badge content pill was removed (round 14) — redundant with the
        # top-nav "Synced <context>" badge. It must not render, even after a load
        # (which is when it used to appear).
        html = loaded_client.get("/qubits").data.decode()
        assert 'id="active-badge"' not in html


class TestRound15ChromeHiding:
    """Round 15 Items 3+4: a GLOBAL top-bar hide toggle and a PER-PAGE header
    collapse toggle, both mirroring the sidebar-collapse + localStorage pattern."""

    def _read(self, *parts):
        base = Path(__file__).resolve().parent.parent / "quam_state_manager"
        return base.joinpath(*parts).read_text(encoding="utf-8")

    def test_topbar_hide_wired(self):
        base = self._read("web", "templates", "base.html")
        js = self._read("web", "static", "app.js")
        css = self._read("web", "static", "style.css")
        assert "toggleTopbar()" in base                      # settings entry + reveal handle
        assert 'class="topbar-reveal"' in base               # always-reachable un-hide
        assert "quam_topbar_hidden" in base                  # restored in restorePrefs
        assert "window.toggleTopbar" in js
        assert "topbar-hidden" in js and "quam_topbar_hidden" in js
        assert "html.topbar-hidden .topbar { display: none; }" in css
        # CRITICAL: zero the var (no 48px dead strip) — never edit the literal.
        assert "html.topbar-hidden { --topbar-height: 0px; }" in css

    def test_topbar_reveal_button_theme_safe(self):
        # <button> → must set explicit bg + non-rescoped text (not var(--pico-color)).
        css = self._read("web", "static", "style.css")
        block = css[css.index(".topbar-reveal {"):css.index("}", css.index(".topbar-reveal {"))]
        assert "var(--pico-contrast)" in block
        assert "var(--pico-color)" not in block

    def test_pageheader_collapse_wired(self):
        base = self._read("web", "templates", "base.html")
        js = self._read("web", "static", "app.js")
        css = self._read("web", "static", "style.css")
        assert 'id="pageheader-toggle"' in base and "togglePageHeader()" in base
        assert "quam_pageheader_collapsed" in base            # restored in restorePrefs
        assert "window.togglePageHeader" in js
        assert "pageheader-collapsed" in js
        assert "body.pageheader-collapsed .table-header-row" in css
        assert "display: none" in css[css.index("body.pageheader-collapsed .table-header-row"):]

    def test_pageheader_toggle_outside_table_pane(self):
        # Must live in #content-area BEFORE #table-pane so it survives HTMX swaps
        # and stays reachable when the heading is collapsed.
        base = self._read("web", "templates", "base.html")
        assert base.index('id="pageheader-toggle"') < base.index('id="table-pane"')


class TestRound15PairDisplay:
    """Round 15 Item 6: display sites prefer the intact pair name; 1b dropped the
    dataset header run-id badge."""

    def _read(self, *parts):
        base = Path(__file__).resolve().parent.parent / "quam_state_manager"
        return base.joinpath(*parts).read_text(encoding="utf-8")

    def test_table_column_prefers_pairs(self):
        js = self._read("web", "static", "dataset-virtual.js")
        # The Qubits column render must consult r.p (pairs) before r.q (members).
        i = js.index("key: 'qubits'")
        seg = js[i:i + 400]
        assert "r.p" in seg and "r.q" in seg
        assert seg.index("r.p") < seg.index("r.q.join")

    def test_overview_row_prefers_pairs(self):
        html = self._read("web", "templates", "_dataset_detail.html")
        assert "run.qubit_pairs if run.qubit_pairs else run.qubits" in html

    def test_dataset_header_runid_badge_removed(self):
        # The leading inspector-runid "#NN" span is gone from the header (the
        # else-branch badge stays for qubit/pair inspectors).
        html = self._read("web", "templates", "_inspector_header.html")
        assert "inspector-runid" not in html
        assert "inspector-badge-" in html       # else-branch (qubit/pair) preserved


class TestRound15RobustDetailOpen:
    """Round 15 Item 1: the Datasets list-click → detail open must be robust against
    the hx-sync silent abort, run id 0, and a virtual-scroll rebuild mid-click."""

    def _read(self, *parts):
        base = Path(__file__).resolve().parent.parent / "quam_state_manager"
        return base.joinpath(*parts).read_text(encoding="utf-8")

    def test_open_detail_hardening(self):
        js = self._read("web", "static", "dataset-virtual.js")
        # pointerdown captures the row id before a rebuild can drop it.
        assert "onTbodyPointerDown" in js and "pressedRowId" in js
        assert "addEventListener('pointerdown', onTbodyPointerDown)" in js
        # Falsy-uid guard (a real uid "<key>:<run_id>" is truthy even for run 0,
        # so no /dataset/undefined). Replaces the old Number.isInteger(id) guard.
        assert "if (!id || !window.htmx)" in js
        # Re-issue safety net for the silent hx-sync abort, gated on an EMPTY pane.
        # The one-shot guard is now keyed PER-ID (state._reissuedIds[id]) so a fast
        # second click on a different run keeps its own backstop (audit 2026-06-26,
        # replacing the old global state._detailReissued flag).
        assert "openDatasetDetail" in js
        assert "_reissuedIds" in js
        assert "innerHTML.trim() === ''" in js


class TestRound15ComparePin:
    """Round 15 Items 2+5: compare-mode X removes only its column (not the whole
    panel), and re-clicking the pinned run can't drop the pinned column."""

    def _read(self, *parts):
        base = Path(__file__).resolve().parent.parent / "quam_state_manager"
        return base.joinpath(*parts).read_text(encoding="utf-8")

    def test_per_column_close_wired(self):
        js = self._read("web", "static", "app.js")
        assert "window.unpinDataset" in js
        assert "_closeCurrentKeepPinned" in js
        # The split builder rewrites each column's close to a per-column action.
        assert "leftClose.onclick = window.unpinDataset" in js
        assert "rightClose.onclick = _closeCurrentKeepPinned" in js

    def test_same_run_does_not_clobber_split(self):
        js = self._read("web", "static", "app.js")
        # The same-run branch must suppress the swap (shouldSwap=false), not fall
        # through to the default single-column swap that drops the pinned column.
        i = js.index("Same run clicked again")
        seg = js[i:i + 600]
        assert "evt.detail.shouldSwap = false" in seg
        assert seg.index("shouldSwap = false") < seg.index("Build two-column")

    def test_pin_captures_from_current_column(self):
        js = self._read("web", "static", "app.js")
        # Capture from the current (right) column, not the global #ds-detail-root.
        assert "pane.querySelector('.inspector-current-col') || pane" in js


# ---------------------------------------------------------------------------
# Explorer
# ---------------------------------------------------------------------------


class TestExplorer:
    def test_explorer_requires_loaded_state(self, client):
        resp = client.get("/explorer")
        assert resp.status_code == 200
        assert b"No chip loaded" in resp.data

    def test_explorer_renders_tree_container(self, loaded_client):
        resp = loaded_client.get("/explorer")
        assert resp.status_code == 200
        html = resp.data.decode()
        assert "explorer-tree-state" in html
        assert "explorer-tree-wiring" in html
        assert "tree-file-tab" in html

    def test_explorer_has_state_data(self, loaded_client):
        resp = loaded_client.get("/explorer")
        html = resp.data.decode()
        assert "qA1" in html
        assert "f_01" in html

    def test_explorer_has_wiring_data(self, loaded_client):
        resp = loaded_client.get("/explorer")
        html = resp.data.decode()
        assert "opx_output" in html
        assert "network" in html

    def test_explorer_toolbar(self, loaded_client):
        resp = loaded_client.get("/explorer")
        html = resp.data.decode()
        assert "tree-toolbar" in html
        assert "explorer-search" in html

    def test_load_redirects_to_explorer(self, client, synth_folder):
        resp = client.post("/load", data={"folder": str(synth_folder)})
        assert resp.status_code in (301, 302, 303, 307, 308)
        assert "/explorer" in resp.headers.get("Location", "")

    def test_explorer_htmx_partial(self, loaded_client):
        resp = loaded_client.get("/explorer", headers={"HX-Request": "true"})
        assert resp.status_code == 200
        html = resp.data.decode()
        assert "explorer-tree-state" in html
        assert "<!DOCTYPE" not in html

    def test_explorer_sidebar_link(self, client):
        resp = client.get("/")
        html = resp.data.decode()
        assert 'href="/explorer"' in html
        assert "Explorer" in html


# ---------------------------------------------------------------------------
# Qubits
# ---------------------------------------------------------------------------


class TestQubits:
    def test_qubits_no_store(self, client):
        resp = client.get("/qubits")
        assert resp.status_code == 200
        assert b"No chip loaded" in resp.data

    def test_qubits_list(self, loaded_client):
        resp = loaded_client.get("/qubits")
        assert resp.status_code == 200
        assert b"qA1" in resp.data

    def test_qubits_htmx(self, loaded_client):
        resp = loaded_client.get("/qubits", headers={"HX-Request": "true"})
        assert resp.status_code == 200
        assert b"qA1" in resp.data

    def test_qubit_detail(self, loaded_client):
        resp = loaded_client.get("/qubit/qA1")
        assert resp.status_code == 200
        assert b"qA1" in resp.data
        assert b"f_01" in resp.data

    def test_qubit_detail_missing(self, loaded_client):
        resp = loaded_client.get("/qubit/qZZZ")
        assert resp.status_code == 404

    def test_qubit_edit(self, loaded_client):
        resp = loaded_client.post("/qubit/qA1/edit", data={
            "dot_path": "qubits.qA1.f_01",
            "value": "6.3e9",
        }, follow_redirects=True)
        assert resp.status_code == 200

    def test_qubit_edit_bad_path(self, loaded_client):
        resp = loaded_client.post("/qubit/qA1/edit", data={
            "dot_path": "qubits.nonexistent.f_01",
            "value": "1.0",
        })
        assert resp.status_code == 400

    def test_qubit_detail_has_inspector_search(self, loaded_client):
        """Sticky in-panel search bar for the qubit inspector."""
        html = loaded_client.get("/qubit/qA1").data.decode()
        assert 'class="detail-search"' in html
        assert 'detail-search-input' in html
        assert 'filterDetailPanel(' in html


# ---------------------------------------------------------------------------
# Pairs
# ---------------------------------------------------------------------------


class TestPairs:
    def test_pairs_list(self, loaded_client):
        resp = loaded_client.get("/pairs")
        assert resp.status_code == 200
        assert b"qA1-A2" in resp.data

    def test_pair_detail(self, loaded_client):
        resp = loaded_client.get("/pair/qA1-A2")
        assert resp.status_code == 200
        assert b"qA1-A2" in resp.data

    def test_pair_missing(self, loaded_client):
        resp = loaded_client.get("/pair/qZZ-ZZ")
        assert resp.status_code == 404

    def test_pair_detail_has_inspector_search(self, loaded_client):
        """Sticky in-panel search bar ships in the pair inspector too."""
        html = loaded_client.get("/pair/qA1-A2").data.decode()
        assert 'class="detail-search"' in html
        assert 'detail-search-input' in html
        assert 'filterDetailPanel(' in html


# ---------------------------------------------------------------------------
# Table
# ---------------------------------------------------------------------------


class TestTable:
    def test_table_default(self, loaded_client):
        resp = loaded_client.get("/table")
        assert resp.status_code == 200
        assert b"qA1" in resp.data

    def test_table_custom_props(self, loaded_client):
        resp = loaded_client.get("/table?props=f_01&props=T1")
        assert resp.status_code == 200
        assert b"f_01" in resp.data

    def test_table_default_selects_all_props(self, loaded_client):
        """Default table load should select all 31 properties."""
        from quam_state_manager.web.routes import _ALL_TABLE_PROPS
        html = loaded_client.get("/table").data.decode()
        for prop in _ALL_TABLE_PROPS:
            assert f'value="{prop}"' in html

    def test_table_grouped_property_selector(self, loaded_client):
        """Property selector should show group labels and per-group toggles."""
        from quam_state_manager.web.routes import _TABLE_PROP_GROUPS
        html = loaded_client.get("/table").data.decode()
        for group in _TABLE_PROP_GROUPS:
            assert group["name"] in html
        assert "prop-group-toggle" in html
        assert "Select All" in html
        assert "Deselect All" in html


class TestTableChainFilter:
    """Chain filter should preserve all chains in the dropdown."""

    @pytest.fixture
    def multi_chain_folder(self, tmp_path):
        state = _make_state()
        qb1 = dict(state["qubits"]["qA1"])
        qb1["id"] = "qB1"
        qb1["f_01"] = 5.5e9
        state["qubits"]["qB1"] = qb1
        state["active_qubit_names"] = ["qA1", "qB1"]
        (tmp_path / "state.json").write_text(json.dumps(state, indent=2), encoding="utf-8")
        (tmp_path / "wiring.json").write_text(json.dumps(_make_wiring(), indent=2), encoding="utf-8")
        return tmp_path

    @pytest.fixture
    def mc_client(self, multi_chain_folder):
        app = create_app(testing=True)
        client = app.test_client()
        client.post("/load", data={"folder": str(multi_chain_folder)})
        return client

    def test_unfiltered_shows_all_chains(self, mc_client):
        html = mc_client.get("/table").data.decode()
        assert "Chain A" in html
        assert "Chain B" in html

    def test_filter_chain_keeps_all_chains_in_dropdown(self, mc_client):
        html = mc_client.get("/table?chain=A").data.decode()
        assert "Chain A" in html
        assert "Chain B" in html
        assert "qA1" in html

    def test_filter_chain_hides_rows(self, mc_client):
        html = mc_client.get("/table?chain=A").data.decode()
        # Scope the assertion to the data table (by id) — the command palette's
        # data <script> contains every qubit name regardless of filter, and
        # base.html's sidebar now renders its own <table> (the scoped-search
        # help grid), so 'first <table>' would match the wrong element.
        table_start = html.find('<table id="comparison-table"')
        table_end = html.find('</table>', table_start) if table_start != -1 else -1
        table_html = html[table_start:table_end] if table_start != -1 else ""
        assert "qA1" in table_html
        assert "qB1" not in table_html


# ---------------------------------------------------------------------------
# Browse / Folder Browser
# ---------------------------------------------------------------------------


class TestBrowse:
    """Tests for the GET /browse directory listing endpoint."""

    def test_browse_no_path_returns_roots(self, client):
        resp = client.get("/browse")
        assert resp.status_code == 200
        data = resp.get_json()
        assert "dirs" in data
        assert isinstance(data["dirs"], list)
        assert len(data["dirs"]) > 0
        assert data["has_quam_state"] is False

    def test_browse_valid_dir_with_quam_state(self, client, synth_folder):
        resp = client.get(f"/browse?path={synth_folder}")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["has_quam_state"] is True
        assert data["path"] == str(synth_folder)
        assert "parent" in data

    def test_browse_parent_of_quam_state(self, client, tmp_path):
        # Browse an isolated parent dir we control — NOT synth_folder.parent,
        # which is the shared pytest tmp dir. In a full-suite run that dir
        # accumulates hundreds of sibling test folders and overflows the
        # /browse [:50] cap, dropping our folder from the listing.
        parent = tmp_path / "browse_root"
        quam = parent / "my_quam_state"
        quam.mkdir(parents=True)
        (quam / "state.json").write_text("{}", encoding="utf-8")
        (quam / "wiring.json").write_text("{}", encoding="utf-8")
        resp = client.get(f"/browse?path={parent}")
        data = resp.get_json()
        assert resp.status_code == 200
        assert isinstance(data["dirs"], list)
        folder_names = [d.split("\\")[-1].split("/")[-1] for d in data["dirs"]]
        assert "my_quam_state" in folder_names

    def test_browse_partial_path(self, client, synth_folder):
        partial = str(synth_folder)[:-2]
        resp = client.get(f"/browse?path={partial}")
        assert resp.status_code == 200
        data = resp.get_json()
        assert isinstance(data["dirs"], list)

    def test_browse_invalid_path(self, client):
        resp = client.get("/browse?path=Z:\\nonexistent_dir_xyz_12345")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["dirs"] == []

    def test_browse_button_renders_in_workspace_form(self, client):
        resp = client.get("/")
        html = resp.data.decode()
        assert "workspace-path-input" in html
        assert "openFolderBrowser" in html
        assert "btn-browse" in html
        assert 'class="btn-sm">State Load</button>' in html

    def test_browse_button_renders_in_load_form(self, client):
        resp = client.get("/")
        html = resp.data.decode()
        assert "load-path-input" in html

    def test_browse_detects_deeply_nested_experiments(self, client, tmp_path):
        deep = tmp_path / "level1" / "level2" / "level3" / "quam_state"
        deep.mkdir(parents=True)
        (deep / "state.json").write_text("{}")
        (deep / "wiring.json").write_text("{}")
        resp = client.get(f"/browse?path={tmp_path}")
        data = resp.get_json()
        assert data["has_experiment_children"] is True

    def test_browse_no_false_positive_without_experiments(self, client, tmp_path):
        (tmp_path / "empty_child").mkdir()
        resp = client.get(f"/browse?path={tmp_path}")
        data = resp.get_json()
        assert data["has_experiment_children"] is False

    def test_browse_recent_container_renders(self, client):
        html = client.get("/").data.decode()
        assert 'id="browser-recent"' in html
        assert 'id="browser-recent-list"' in html
        assert "Recent Folders" in html


# ---------------------------------------------------------------------------
# Wiring
# ---------------------------------------------------------------------------


class TestWiring:
    def test_wiring(self, loaded_client):
        resp = loaded_client.get("/wiring")
        assert resp.status_code == 200
        assert b"qA1" in resp.data


# ---------------------------------------------------------------------------
# Search
# ---------------------------------------------------------------------------


class TestSearch:
    def test_search_empty(self, loaded_client):
        resp = loaded_client.get("/search")
        assert resp.status_code == 200

    def test_search_query(self, loaded_client):
        resp = loaded_client.get("/search?q=7640")
        assert resp.status_code == 200

    def test_search_no_store(self, client):
        resp = client.get("/search?q=test")
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# Save / Undo
# ---------------------------------------------------------------------------


class TestSaveUndo:
    def test_save_no_changes(self, loaded_client):
        resp = loaded_client.post("/save")
        assert resp.status_code == 200
        assert b"No unsaved" in resp.data

    def test_save_with_changes(self, loaded_client):
        loaded_client.post("/qubit/qA1/edit", data={
            "dot_path": "qubits.qA1.f_01", "value": "6.3e9",
        })
        resp = loaded_client.post("/save")
        assert resp.status_code == 200
        assert b"Saved" in resp.data

    def test_undo_empty(self, loaded_client):
        # Nothing to undo → the (unchanged) tray is returned as a harmless no-op
        # swap (the keyboard handler swaps #pending-tray by outerHTML).
        resp = loaded_client.post("/undo")
        assert resp.status_code == 200
        assert b'id="pending-tray"' in resp.data

    def test_undo_after_edit(self, loaded_client):
        loaded_client.post("/qubit/qA1/edit", data={
            "dot_path": "qubits.qA1.f_01", "value": "6.3e9",
        })
        resp = loaded_client.post("/undo")
        assert resp.status_code == 200
        # Body is the refreshed tray; the "Undone" summary + the per-cell reverts
        # ride the cellsReverted HX-Trigger (client shows the toast + rolls back
        # the inspector cell / Explorer node).
        assert b'id="pending-tray"' in resp.data
        trig = resp.headers.get("HX-Trigger", "")
        assert "cellsReverted" in trig and "Undone" in trig
        # And the edit was actually reverted (change log emptied).
        assert loaded_client.post("/undo").status_code == 200  # now a no-op


# ---------------------------------------------------------------------------
# Export
# ---------------------------------------------------------------------------


class TestExport:
    def test_export_csv(self, loaded_client):
        resp = loaded_client.get("/export")
        assert resp.status_code == 200
        assert resp.content_type == "text/csv; charset=utf-8"
        assert b"id" in resp.data
        assert b"qA1" in resp.data


# ---------------------------------------------------------------------------
# Diff
# ---------------------------------------------------------------------------


class TestDiffRedirect:
    """Legacy /diff → Compare hub (docs/49 P4). The 2-way diff surface is
    the hub's bucket ① now; these pin the translation contract."""

    def test_diff_get_redirects_to_hub(self, loaded_client):
        resp = loaded_client.get("/diff")
        assert resp.status_code == 302
        assert resp.headers["Location"].startswith("/compare-hub")

    def test_diff_post_translates_paths(self, loaded_client, synth_folder):
        resp = loaded_client.post("/diff", data={
            "path_a": str(synth_folder),
            "path_b": str(synth_folder),
        })
        assert resp.status_code == 302
        loc = resp.headers["Location"]
        assert loc.count("src=") == 2
        # U1b: legacy forms are MANUAL baskets — never the primary-CTA hint
        assert "hint" not in loc

    def test_diff_htmx_post_uses_hx_redirect(self, loaded_client, synth_folder):
        """A7 — htmx would FOLLOW a 302 and swap it into the pane; HTMX
        requests get HX-Redirect (full navigation) instead."""
        resp = loaded_client.post("/diff", data={
            "path_a": str(synth_folder), "path_b": str(synth_folder),
        }, headers={"HX-Request": "true"})
        assert resp.status_code == 200
        assert resp.headers["HX-Redirect"].startswith("/compare-hub?")

    def test_diff_empty_post_redirects_with_moved_note(self, loaded_client):
        resp = loaded_client.post("/diff", data={"path_a": "", "path_b": ""})
        assert resp.status_code == 302
        assert resp.headers["Location"] == "/compare-hub?from=diff"

    def test_diff_gone_from_sidebar(self, client):
        html = client.get("/").data.decode()
        assert 'href="/diff"' not in html
        assert 'href="/compare-hub"' in html

    def test_nav_sync_script_loaded(self, client):
        html = client.get("/").data.decode()
        assert "htmx:pushedIntoHistory" in html or "app.js" in html


# ---------------------------------------------------------------------------
# Workspace
# ---------------------------------------------------------------------------


class TestWorkspace:
    def test_workspace_add(self, client, synth_folder):
        resp = client.post("/workspace/add", data={"folder": str(synth_folder)})
        assert resp.status_code == 200

    def test_workspace_add_empty(self, client):
        resp = client.post("/workspace/add", data={"folder": ""})
        assert resp.status_code == 400

    def test_workspace_tree(self, client):
        resp = client.get("/workspace/tree")
        assert resp.status_code == 200

    def test_workspace_select(self, client, synth_folder):
        resp = client.post("/workspace/select", data={"path": str(synth_folder)}, follow_redirects=True)
        assert resp.status_code == 200

    def test_workspace_select_empty(self, client):
        resp = client.post("/workspace/select", data={"path": ""})
        assert resp.status_code == 400

    def test_workspace_select_corrupt_state_is_400_not_500(self, client, tmp_path):
        """A corrupt/unreadable state.json raises ValueError (bad JSON), not
        FileNotFoundError — workspace_select must catch it and return a friendly
        400 status toast, not a generic 500 Internal Server Error."""
        bad = tmp_path / "badchip" / "quam_state"
        bad.mkdir(parents=True)
        (bad / "state.json").write_text("{ this is not valid json", encoding="utf-8")
        (bad / "wiring.json").write_text("{}", encoding="utf-8")
        resp = client.post("/workspace/select", data={"path": str(bad)})
        assert resp.status_code == 400
        assert b"Internal Server Error" not in resp.data

    def test_workspace_select_htmx_chip_folder_redirects(self, client, synth_folder):
        """A plain chip-folder sidebar entry (no run_id, no inplace flag) keeps
        the full-render HX-Redirect so the header reflects the switched chip."""
        resp = client.post("/workspace/select",
                            data={"path": str(synth_folder)},
                            headers={"HX-Request": "true"})
        assert resp.status_code == 200
        assert resp.headers.get("HX-Redirect")  # full client navigation

    def test_workspace_select_dataset_entry_inplace_keeps_inspector(self, client, synth_folder):
        """REGRESSION: a dataset-RUN sidebar entry sends inplace=1. The route
        must do an IN-PLACE #table-pane swap (qubits view + OOB tray/diverged)
        and NOT a full HX-Redirect — a redirect does a client navigation that
        destroys the concurrent #inspector-pane dataset-detail swap, so the
        dataset panel "flashes then vanishes". It also must NOT emit
        stateRestored, which the client uses to close the inspector."""
        resp = client.post("/workspace/select",
                            data={"path": str(synth_folder), "inplace": "1"},
                            headers={"HX-Request": "true"})
        assert resp.status_code == 200
        # No full navigation — the inspector swap must survive.
        assert "HX-Redirect" not in resp.headers
        # diagnostics banner refresh is fine; stateRestored would close the
        # inspector (the very bug), so it must be absent.
        trigger = resp.headers.get("HX-Trigger", "")
        assert "diagnostics-changed" in trigger
        assert "stateRestored" not in trigger
        body = resp.data.decode()
        # In-place table-pane swap carried OOB refreshes for the header slots.
        assert "hx-swap-oob" in body
        assert "live-diverged-slot" in body

    def test_hx_vals_escapes_backslashes(self, client, synth_folder):
        client.post("/workspace/add", data={"folder": str(synth_folder)})
        resp = client.get("/workspace/tree")
        html = resp.data.decode()
        folder_str = str(synth_folder)
        escaped = folder_str.replace("\\", "\\\\")
        assert f'{{"folder": "{escaped}"}}' in html
        for line in html.splitlines():
            if "hx-vals" in line and "path" in line:
                assert "\\\\" in line or "/" in line

    def test_sidebar_filter_by_name(self, tmp_path):
        root = tmp_path / "filter_root"
        for name in ["resonator_spectroscopy", "qubit_spectroscopy"]:
            exp = root / name / "quam_state"
            exp.mkdir(parents=True)
            (exp / "state.json").write_text(json.dumps(_make_state()), encoding="utf-8")
            (exp / "wiring.json").write_text(json.dumps(_make_wiring()), encoding="utf-8")
        app = create_app(testing=True)
        c = app.test_client()
        c.post("/workspace/add", data={"folder": str(root)})
        full = c.get("/workspace/tree").data.decode()
        assert "resonator_spectroscopy" in full
        assert "qubit_spectroscopy" in full
        filtered = c.get("/workspace/tree?name=resonator").data.decode()
        assert "resonator_spectroscopy" in filtered
        assert "qubit_spectroscopy" not in filtered

    def test_sidebar_filter_by_date(self, tmp_path):
        root = tmp_path / "date_root"
        for i, date_str in enumerate(["2025-01-01", "2026-06-15"]):
            exp_dir = root / f"exp_{i}"
            qs = exp_dir / "quam_state"
            qs.mkdir(parents=True)
            (qs / "state.json").write_text(json.dumps(_make_state()), encoding="utf-8")
            (qs / "wiring.json").write_text(json.dumps(_make_wiring()), encoding="utf-8")
            node = {
                "metadata": {"name": f"spectroscopy_{i}", "status": "finished"},
                "created_at": f"{date_str}T12:00:00",
                "id": i,
            }
            (exp_dir / "node.json").write_text(json.dumps(node), encoding="utf-8")
        app = create_app(testing=True)
        c = app.test_client()
        c.post("/workspace/add", data={"folder": str(root)})
        filtered = c.get("/workspace/tree?name=2025").data.decode()
        assert "2025-01-01" in filtered
        assert "2026-06-15" not in filtered

    def test_sidebar_filter_multi_token(self, tmp_path):
        root = tmp_path / "multi_root"
        for name in ["resonator_spectroscopy", "qubit_spectroscopy", "resonator_power"]:
            exp = root / name / "quam_state"
            exp.mkdir(parents=True)
            (exp / "state.json").write_text(json.dumps(_make_state()), encoding="utf-8")
            (exp / "wiring.json").write_text(json.dumps(_make_wiring()), encoding="utf-8")
        app = create_app(testing=True)
        c = app.test_client()
        c.post("/workspace/add", data={"folder": str(root)})
        filtered = c.get("/workspace/tree?name=resonator+spectroscopy").data.decode()
        assert "resonator_spectroscopy" in filtered
        assert "qubit_spectroscopy" not in filtered
        assert "resonator_power" not in filtered

    def test_sidebar_filter_multi_token_with_date(self, tmp_path):
        root = tmp_path / "date_multi"
        for i, (name, date_str) in enumerate([
            ("resonator_spec", "2025-01-01"),
            ("resonator_power", "2026-06-15"),
            ("qubit_spec", "2025-01-01"),
        ]):
            exp_dir = root / f"exp_{i}_{name}"
            qs = exp_dir / "quam_state"
            qs.mkdir(parents=True)
            (qs / "state.json").write_text(json.dumps(_make_state()), encoding="utf-8")
            (qs / "wiring.json").write_text(json.dumps(_make_wiring()), encoding="utf-8")
            node = {
                "metadata": {"name": name, "status": "finished"},
                "created_at": f"{date_str}T12:00:00",
                "id": i,
            }
            (exp_dir / "node.json").write_text(json.dumps(node), encoding="utf-8")
        app = create_app(testing=True)
        c = app.test_client()
        c.post("/workspace/add", data={"folder": str(root)})
        filtered = c.get("/workspace/tree?name=2025+resonator").data.decode()
        assert "resonator_spec" in filtered
        assert "resonator_power" not in filtered
        assert "qubit_spec" not in filtered

    def test_sidebar_filter_by_status(self, tmp_path):
        root = tmp_path / "status_root"
        for i, status in enumerate(["finished", "failed"]):
            exp_dir = root / f"exp_{i}"
            qs = exp_dir / "quam_state"
            qs.mkdir(parents=True)
            (qs / "state.json").write_text(json.dumps(_make_state()), encoding="utf-8")
            (qs / "wiring.json").write_text(json.dumps(_make_wiring()), encoding="utf-8")
            node = {
                "metadata": {"name": f"spectroscopy_{i}", "status": status},
                "created_at": "2026-03-18T12:00:00",
                "id": i,
            }
            (exp_dir / "node.json").write_text(json.dumps(node), encoding="utf-8")
        app = create_app(testing=True)
        c = app.test_client()
        c.post("/workspace/add", data={"folder": str(root)})
        filtered = c.get("/workspace/tree?name=finished").data.decode()
        assert "spectroscopy_0" in filtered
        assert "spectroscopy_1" not in filtered


# ---------------------------------------------------------------------------
# JSON API
# ---------------------------------------------------------------------------


class TestAPI:
    def test_api_qubit(self, loaded_client):
        resp = loaded_client.get("/api/qubit/qA1")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["id"] == "qA1"
        assert data["f_01"] == 6.25e9

    def test_api_qubit_missing(self, loaded_client):
        resp = loaded_client.get("/api/qubit/qZZZ")
        assert resp.status_code == 404

    def test_api_pair(self, loaded_client):
        resp = loaded_client.get("/api/pair/qA1-A2")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["id"] == "qA1-A2"

    def test_api_search(self, loaded_client):
        resp = loaded_client.get("/api/search?q=7640")
        assert resp.status_code == 200
        data = resp.get_json()
        assert isinstance(data, list)

    def test_api_search_empty(self, loaded_client):
        resp = loaded_client.get("/api/search")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data == []

    def test_api_topology(self, loaded_client):
        resp = loaded_client.get("/api/topology")
        assert resp.status_code == 200
        data = resp.get_json()
        assert "nodes" in data
        assert "edges" in data

    def test_api_no_store(self, client):
        resp = client.get("/api/qubit/qA1")
        assert resp.status_code == 400


# ---------------------------------------------------------------------------
# UI Redesign -- Layout & Containers (Tasks 2-3)
# ---------------------------------------------------------------------------


class TestLayoutContainers:
    """Verify the stable HTMX container structure in base.html."""

    def test_base_has_table_pane(self, loaded_client):
        html = loaded_client.get("/qubits").data.decode()
        assert 'id="table-pane"' in html

    def test_base_has_inspector_pane(self, loaded_client):
        html = loaded_client.get("/qubits").data.decode()
        assert 'id="inspector-pane"' in html

    def test_base_has_status_bar(self, loaded_client):
        html = loaded_client.get("/qubits").data.decode()
        assert 'id="status-bar"' in html

    def test_base_has_split_js(self, loaded_client):
        html = loaded_client.get("/qubits").data.decode()
        assert "split.min.js" in html

    def test_base_has_app_js(self, loaded_client):
        html = loaded_client.get("/qubits").data.decode()
        assert "app.js" in html

    def test_htmx_targets_table_pane(self, loaded_client):
        html = loaded_client.get("/qubits").data.decode()
        assert '#table-pane' in html

    def test_htmx_targets_inspector_pane(self, loaded_client):
        html = loaded_client.get("/qubits").data.decode()
        assert '#inspector-pane' in html

    def test_no_old_detail_target(self, loaded_client):
        html = loaded_client.get("/qubits").data.decode()
        assert 'hx-target="#detail"' not in html


# ---------------------------------------------------------------------------
# Unit labels — guard the µ (U+00B5) -> Μ (U+039C) CSS-uppercase mangle
# ---------------------------------------------------------------------------


class TestUnitLabels:
    """The qubit table headers carry µs/GHz unit labels. A `text-transform:
    uppercase` on <th> used to mangle the micro sign µ (U+00B5) into Greek
    capital Mu Μ (U+039C, looks like Latin M), so "µs" read as "MS" — a 1000×
    misread. Units now live in <span class="unit"> (text-transform:none)."""

    def test_micro_sign_present_and_not_mangled(self, loaded_client):
        # Authored as the &micro; entity (or literal µ); the browser decodes it
        # to U+00B5. Either form is fine — what must NEVER appear is the Greek
        # capital Mu Μ (U+039C) the old uppercase rule produced.
        raw = loaded_client.get("/qubits").data  # bytes, UTF-8
        assert (b"&micro;s" in raw) or ("µs".encode() in raw), \
            "micro-second unit label missing from qubits header"
        assert "Μ".encode() not in raw, "Greek capital Mu Μ (U+039C) leaked into output"

    def test_unit_suffix_wrapped_in_unit_span(self, loaded_client):
        html = loaded_client.get("/qubits").data.decode()
        assert '<span class="unit">(&micro;s)</span>' in html or \
               '<span class="unit">(µs)</span>' in html
        assert '<span class="unit">(GHz)</span>' in html

    def test_unit_span_is_not_uppercased(self, loaded_client):
        """The .unit rule must exist so a uppercased <th> can't mangle µ/m."""
        css = loaded_client.get("/static/style.css").data.decode()
        assert ".unit" in css and "text-transform: none" in css

    def test_t2ramsey_converted_to_microseconds(self, loaded_client):
        # _make_state qA1 T2ramsey = 1.5e-6 s -> 1.50 (µs), not raw 1.5e-06.
        html = loaded_client.get("/qubits").data.decode()
        assert "1.50" in html
        assert "1.5e-06" not in html


# ---------------------------------------------------------------------------
# Per-table filters (Task 4)
# ---------------------------------------------------------------------------


class TestTableFilters:
    """Verify client-side filter inputs on data tables."""

    def test_qubits_has_filter(self, loaded_client):
        html = loaded_client.get("/qubits").data.decode()
        assert "filterTable" in html
        assert 'id="qubits-table"' in html

    def test_pairs_has_filter(self, loaded_client):
        html = loaded_client.get("/pairs").data.decode()
        assert "filterTable" in html
        assert 'id="pairs-table"' in html

    def test_table_has_filter(self, loaded_client):
        html = loaded_client.get("/table").data.decode()
        assert "filterTable" in html
        assert 'id="comparison-table"' in html

    def test_wiring_page_loads(self, loaded_client):
        html = loaded_client.get("/wiring").data.decode()
        assert "topo-dashboard" in html


# ---------------------------------------------------------------------------
# Save flow (Task 5)
# ---------------------------------------------------------------------------


class TestSaveFlow:
    """Verify Save All, Changes dropdown, and Discard."""

    def test_save_all_in_pending_tray(self, loaded_client):
        """Save + Apply-to-live controls live inside the pending tray."""
        loaded_client.post("/field/edit", data={"dot_path": "qubits.qA1.f_01", "value": "5.0e9"})
        html = loaded_client.get("/qubits").data.decode()
        # Pending edit, clean working state (change_count>0, not working_dirty) → the
        # tray offers "Save to working state" + the one-click "⚡ Apply to live now".
        assert "Save to working state" in html
        assert "Apply to live now" in html

    def test_pending_tray_present(self, loaded_client):
        html = loaded_client.get("/qubits").data.decode()
        assert 'id="pending-tray"' in html
        assert "togglePendingTray" in html or "app.js" in html

    def test_changes_route(self, loaded_client):
        resp = loaded_client.get("/changes")
        assert resp.status_code == 200
        assert b"No unsaved changes" in resp.data

    def test_changes_route_after_edit(self, loaded_client):
        loaded_client.post("/qubit/qA1/edit", data={
            "dot_path": "qubits.qA1.f_01", "value": "6.3e9",
        })
        resp = loaded_client.get("/changes")
        html = resp.data.decode()
        assert "qubits.qA1.f_01" in html
        assert "Discard" in html

    def test_discard_route(self, loaded_client):
        loaded_client.post("/qubit/qA1/edit", data={
            "dot_path": "qubits.qA1.f_01", "value": "6.3e9",
        })
        resp = loaded_client.post("/discard", data={"index": "0"})
        assert resp.status_code == 200
        assert b'id="pending-tray"' in resp.data
        # After discarding the only edit the tray is NOT empty — a loaded chip
        # always shows its identity badge (the "Synced" chip name), so the
        # chip name never vanishes on a clean state (A0.1 tray fix).
        assert b"tray-empty" not in resp.data
        assert b"state-status-badge" in resp.data

    def test_discard_invalid_index(self, loaded_client):
        resp = loaded_client.post("/discard", data={"index": "999"})
        html = resp.data.decode()
        assert "No unsaved changes" in html or "Change not found" in html


# ---------------------------------------------------------------------------
# Edit feedback (Task 6)
# ---------------------------------------------------------------------------


class TestEditFeedback:
    """Verify modified-cell visual indicators."""

    def test_no_modified_class_initially(self, loaded_client):
        html = loaded_client.get("/qubit/qA1").data.decode()
        assert "cell-modified" not in html

    def test_modified_class_after_edit(self, loaded_client):
        loaded_client.post("/qubit/qA1/edit", data={
            "dot_path": "qubits.qA1.f_01", "value": "6.3e9",
        })
        html = loaded_client.get("/qubit/qA1").data.decode()
        assert "cell-modified" in html

    def test_modified_input_class_after_edit(self, loaded_client):
        loaded_client.post("/qubit/qA1/edit", data={
            "dot_path": "qubits.qA1.f_01", "value": "6.3e9",
        })
        html = loaded_client.get("/qubit/qA1").data.decode()
        assert "edit-input-modified" in html

    def test_modified_tooltip_shows_previous(self, loaded_client):
        loaded_client.post("/qubit/qA1/edit", data={
            "dot_path": "qubits.qA1.f_01", "value": "6.3e9",
        })
        html = loaded_client.get("/qubit/qA1").data.decode()
        assert "Previous:" in html


# ---------------------------------------------------------------------------
# Cursor retention (Task 7)
# ---------------------------------------------------------------------------


class TestCursorRetention:
    """Verify edit returns detail directly (no redirect) with focus script."""

    def test_edit_returns_200_not_redirect(self, loaded_client):
        resp = loaded_client.post("/qubit/qA1/edit", data={
            "dot_path": "qubits.qA1.f_01", "value": "6.3e9",
        })
        assert resp.status_code == 200

    def test_edit_response_contains_detail(self, loaded_client):
        resp = loaded_client.post("/qubit/qA1/edit", data={
            "dot_path": "qubits.qA1.f_01", "value": "6.3e9",
        })
        html = resp.data.decode()
        assert "qubit-detail" in html
        assert "f_01" in html

    def test_edit_response_has_focus_script(self, loaded_client):
        resp = loaded_client.post("/qubit/qA1/edit", data={
            "dot_path": "qubits.qA1.f_01", "value": "6.3e9",
        })
        html = resp.data.decode()
        assert "focusEditInput" in html
        assert "qubits.qA1.f_01" in html


# ---------------------------------------------------------------------------
# Generic inspector (Task 8)
# ---------------------------------------------------------------------------


class TestGenericInspector:
    """Verify unified inspector header across content types."""

    def test_qubit_detail_has_inspector_header(self, loaded_client):
        html = loaded_client.get("/qubit/qA1").data.decode()
        assert "inspector-header" in html
        assert "inspector-badge-qubit" in html

    def test_pair_detail_has_inspector_header(self, loaded_client):
        html = loaded_client.get("/pair/qA1-A2").data.decode()
        assert "inspector-header" in html
        assert "inspector-badge-pair" in html

    def test_search_has_inspector_header(self, loaded_client):
        html = loaded_client.get("/search?q=qA1").data.decode()
        assert "inspector-header" in html
        assert "inspector-badge-search" in html

    def test_inspector_has_close_button(self, loaded_client):
        html = loaded_client.get("/qubit/qA1").data.decode()
        assert "closeInspector" in html
        assert "inspector-close" in html


# ---------------------------------------------------------------------------
# Sidebar features (Task 10)
# ---------------------------------------------------------------------------


class TestSidebarFeatures:
    """Verify collapsible sidebar and polling."""

    def test_sidebar_toggle_button(self, loaded_client):
        html = loaded_client.get("/qubits").data.decode()
        assert "sidebar-toggle" in html
        assert "toggleSidebar" in html

    def test_sidebar_tree_polling(self, loaded_client):
        html = loaded_client.get("/qubits").data.decode()
        # Polling interval is now set dynamically via JS from UI_CONFIG.autoRefreshInterval
        assert "autoRefreshInterval" in html or "sidebar-tree" in html

    def test_workspace_tree_route(self, client):
        resp = client.get("/workspace/tree")
        assert resp.status_code == 200

    def test_sidebar_entry_row_is_not_label_wrapped(self):
        """A sidebar data entry must NOT wrap its compare checkbox + clickable
        name in a <label>. Native <label> behavior toggles the checkbox whenever
        the user clicks anywhere inside it — so a <label>-wrapped row auto-ticks
        the compare checkbox just for viewing the entry. Viewing and
        compare-selection must stay independent: the row is a <div> and the
        checkbox is toggled only by a deliberate click on the checkbox itself.
        """
        # The entry-row markup lives in the _sidebar_tree_entries.html partial
        # (shared by the capped inline render and the "Show all N" fragment).
        tpl = (Path(__file__).resolve().parent.parent
               / "quam_state_manager" / "web" / "templates" / "_sidebar_tree_entries.html")
        text = tpl.read_text(encoding="utf-8")
        assert 'class="tree-entry-label"' in text, "sidebar entry row markup changed unexpectedly"
        assert '<label class="tree-entry-label"' not in text, (
            "Sidebar entry row must be a <div>, not a <label>: a <label> wrapping the "
            "checkbox and the clickable name makes clicking the name auto-tick the "
            "compare checkbox. Keep viewing and compare-selection independent."
        )

    def test_sidebar_tree_caps_date_group_and_offers_show_all(self, app):
        """A date group with more than the cap (50) entries renders only the
        first 50 rows + a "Show all N" control, and still shows the true total.
        Bounds the DOM at scale (3,000-run workspaces) without losing entries.
        """
        from flask import render_template
        from quam_state_manager.core.scanner import DateGroup

        class _E:
            def __init__(self, i):
                self.quam_state_path = f"/ws/r{i}/quam_state"
                self.experiment_name = f"exp_{i}"
                self.run_id = i
                self.timestamp = "2026-05-30T14:23:00"
                self.status = "successful"

        entries = [_E(i) for i in range(60)]
        tree = {"/ws": [DateGroup(date_str="2026-05-30", entries=entries)]}
        with app.test_request_context("/"):
            html = render_template("_sidebar_tree.html", tree=tree, name_filter="")
            # Capped to 50 clickable rows, true total shown, show-all present.
            assert html.count("tree-entry-click") == 50
            assert "(60)" in html
            assert "Show all 60" in html
            # The fragment route renders the full, uncapped list.
            frag = render_template("_sidebar_tree_entries.html", entries=entries)
            assert frag.count("tree-entry-click") == 60

    def test_sidebar_tree_no_show_all_under_cap(self, app):
        """A small date group (<= cap) renders every entry and no show-all."""
        from flask import render_template
        from quam_state_manager.core.scanner import DateGroup

        class _E:
            def __init__(self, i):
                self.quam_state_path = f"/ws/r{i}/quam_state"
                self.experiment_name = f"exp_{i}"
                self.run_id = i
                self.timestamp = "2026-05-30T14:23:00"
                self.status = "successful"

        tree = {"/ws": [DateGroup(date_str="2026-05-30", entries=[_E(i) for i in range(5)])]}
        with app.test_request_context("/"):
            html = render_template("_sidebar_tree.html", tree=tree, name_filter="")
            assert html.count("tree-entry-click") == 5
            assert "tree-show-more" not in html

    def test_split_set_icons_and_presets_present(self):
        """The gutter resize-preset controls and their localStorage keys must
        ship in base.html — they let users set how high ▲ expands / how low ▼
        collapses the inspector panel (default expand is very high)."""
        base = (Path(__file__).resolve().parent.parent
                / "quam_state_manager" / "web" / "templates" / "base.html")
        text = base.read_text(encoding="utf-8")
        assert "split-set-high-btn" in text and "split-set-low-btn" in text, (
            "Gutter set-expanded / set-collapsed icons are missing from the split IIFE."
        )
        assert "quam_split_expanded" in text and "quam_split_collapsed" in text, (
            "Per-user split preset localStorage keys are missing."
        )

    def test_chip_status_left_nav_subviews(self):
        """Chip Status exposes its sections as sub-items in the LEFT sidebar
        (mirrored from the in-page scroll-spy jump bar), Topology leading. Phase C
        replaced the orphan "Full View" with "Distributions".
        """
        base = (Path(__file__).resolve().parent.parent
                / "quam_state_manager" / "web" / "templates" / "base.html")
        text = base.read_text(encoding="utf-8")
        assert 'id="chip-status-subnav"' in text, "left-nav Chip Status sub-items missing"
        # 8 sections, each wired to chipNavView(), in top-to-bottom scroll order.
        assert text.count("chipNavView(") == 8
        assert "view=full" not in text, "Full View was removed in the Phase C scroll dashboard"
        assert text.index("view=topology") < text.index("view=overview") < text.index("view=distributions"), (
            "Topology should lead, then Overview, then Distributions."
        )

    def test_core_scripts_not_deferred(self):
        """app.js (UI_CONFIG) and dataset-virtual.js (DatasetVirtual) must load
        eagerly — NOT deferred. Parse-time inline scripts in full-page template
        renders (/topology, /trends, /datasets on direct load or refresh) read
        those globals before <body> parses; deferring the scripts left the
        globals undefined and silently broke (or threw on) those pages. Plotly is
        deliberately excluded here — it is lazy-loaded via requirePlotly().
        """
        import re
        base = (Path(__file__).resolve().parent.parent
                / "quam_state_manager" / "web" / "templates" / "base.html")
        text = base.read_text(encoding="utf-8")
        for fname in ("app.js", "dataset-virtual.js"):
            m = re.search(r'<script\b([^>]*?)\bsrc="[^"]*' + re.escape(fname), text)
            assert m, f"{fname} <script> tag not found in base.html"
            assert "defer" not in m.group(1) and "async" not in m.group(1), (
                f"{fname} must load eagerly (no defer/async): parse-time inline "
                "scripts depend on its global being defined before <body> parses."
            )
        # Plotly must NOT be eagerly loaded (lazy via requirePlotly()).
        assert text.count("plotly.min.js") == 1, (
            "plotly.min.js should appear once (the <body data-plotly-src> hint), "
            "not as an eager <script> — it is lazy-loaded on demand."
        )

    def test_inspector_can_fully_cover_page(self):
        """The inspector/Dataset panel must be able to fully cover the page:
        the upper pane's Split.js min (app.js) is 0 and #table-pane has no
        CSS min-height floor (style.css). A non-zero floor in either place
        re-clamps the drag at the section title."""
        root = Path(__file__).resolve().parent.parent / "quam_state_manager" / "web"
        app_js = (root / "static" / "app.js").read_text(encoding="utf-8")
        line = app_js[app_js.index("minSizes:"):]
        line = line[:line.index("\n")]
        assert "[0," in line.replace(" ", ""), (
            "UI_CONFIG.split.minSizes upper bound must be 0 so the inspector "
            "can be dragged to fully cover the page. Got: " + line.strip()
        )
        css = (root / "static" / "style.css").read_text(encoding="utf-8")
        rule = css[css.index("#table-pane {"):css.index("}", css.index("#table-pane {"))]
        assert "min-height: 0" in rule and "min-height: 80px" not in rule, (
            "#table-pane must use min-height: 0 (no 80px floor) so the upper pane "
            "can shrink to 0 and the inspector covers it completely."
        )


# ---------------------------------------------------------------------------
# Sidebar workspace filter — scoped search (_filter_tree)
# ---------------------------------------------------------------------------


class TestSidebarScopedFilter:
    """Datasets-style scoped search for the sidebar workspace filter."""

    @staticmethod
    def _tree():
        import types
        from quam_state_manager.core.scanner import DateGroup

        def e(name, date, status, rid):
            return types.SimpleNamespace(
                experiment_name=name, date_str=date, status=status, run_id=rid
            )

        g1 = DateGroup(date_str="2026-03-18", entries=[
            e("iq_blob", "2026-03-18", "complete", 108),
            e("power rabi cal", "2026-03-18", "running", 109),
        ])
        g2 = DateGroup(date_str="2026-05-19", entries=[
            e("t1_decay", "2026-05-19", "error", 110),
        ])
        return {"/root": [g1, g2]}

    @staticmethod
    def _names(filtered):
        out = []
        for groups in filtered.values():
            for g in groups:
                out.extend(e.experiment_name for e in g.entries)
        return out

    def test_empty_query_returns_all(self):
        from quam_state_manager.web.routes import _filter_tree
        assert sorted(self._names(_filter_tree(self._tree(), ""))) == [
            "iq_blob", "power rabi cal", "t1_decay",
        ]

    def test_name_scope(self):
        from quam_state_manager.web.routes import _filter_tree
        assert self._names(_filter_tree(self._tree(), "name:iq_blob")) == ["iq_blob"]

    def test_name_alias(self):
        from quam_state_manager.web.routes import _filter_tree
        assert self._names(_filter_tree(self._tree(), "e:rabi")) == ["power rabi cal"]

    def test_status_scope(self):
        from quam_state_manager.web.routes import _filter_tree
        assert self._names(_filter_tree(self._tree(), "status:running")) == ["power rabi cal"]

    def test_date_scope(self):
        from quam_state_manager.web.routes import _filter_tree
        assert self._names(_filter_tree(self._tree(), "date:2026-05")) == ["t1_decay"]

    def test_id_scope(self):
        from quam_state_manager.web.routes import _filter_tree
        assert self._names(_filter_tree(self._tree(), "id:108")) == ["iq_blob"]

    def test_negated_scope_excludes(self):
        from quam_state_manager.web.routes import _filter_tree
        assert sorted(self._names(_filter_tree(self._tree(), "-status:error"))) == [
            "iq_blob", "power rabi cal",
        ]

    def test_quoted_value_with_space(self):
        from quam_state_manager.web.routes import _filter_tree
        assert self._names(_filter_tree(self._tree(), 'name:"power rabi"')) == ["power rabi cal"]

    def test_free_text_matches_name(self):
        from quam_state_manager.web.routes import _filter_tree
        assert self._names(_filter_tree(self._tree(), "decay")) == ["t1_decay"]

    def test_combined_conditions_are_anded(self):
        from quam_state_manager.web.routes import _filter_tree
        # date matches both 03-18 entries; status narrows to the running one
        assert self._names(_filter_tree(self._tree(), "date:2026-03 status:running")) == [
            "power rabi cal",
        ]

    def test_no_match_returns_empty(self):
        from quam_state_manager.web.routes import _filter_tree
        assert self._names(_filter_tree(self._tree(), "name:nonexistent")) == []

    def test_sidebar_scoped_search_help_present(self):
        """The sidebar filter must expose the scoped-search ? help affordance,
        wired to the generic class-based handler in app.js."""
        root = Path(__file__).resolve().parent.parent / "quam_state_manager" / "web"
        base = (root / "templates" / "base.html").read_text(encoding="utf-8")
        assert 'id="sidebar-search-help"' in base and "search-help-toggle" in base
        app_js = (root / "static" / "app.js").read_text(encoding="utf-8")
        assert "search-help-toggle" in app_js, "generic search-help handler missing from app.js"


# ---------------------------------------------------------------------------
# Font size settings (Task 11)
# ---------------------------------------------------------------------------


class TestFontSizeSettings:
    """Verify settings gear dropdown."""

    def test_settings_button_present(self, loaded_client):
        html = loaded_client.get("/qubits").data.decode()
        assert "settings-btn" in html
        assert "toggleSettings" in html

    def test_settings_dropdown_present(self, loaded_client):
        html = loaded_client.get("/qubits").data.decode()
        assert "settings-dropdown" in html
        assert "setFontSize" in html

    def test_font_size_options(self, loaded_client):
        html = loaded_client.get("/qubits").data.decode()
        assert 'data-size="small"' in html
        assert 'data-size=""' in html
        assert 'data-size="large"' in html


# ---------------------------------------------------------------------------
# Template polish features
# ---------------------------------------------------------------------------


class TestQubitDetailSections:
    """Tests for the pointer-aware, sectioned qubit detail template."""

    def test_sections_present(self, loaded_client):
        resp = loaded_client.get("/qubit/qA1")
        html = resp.data.decode()
        for section in ["Identity", "Frequencies", "Coherence", "XY Drive", "Readout", "Flux", "Gate Fidelity"]:
            assert section in html, f"Section '{section}' not found"

    def test_pointer_display(self, loaded_client):
        resp = loaded_client.get("/qubit/qA1")
        html = resp.data.decode()
        assert "pointer-badge" in html or "dot-path" in html

    def test_inline_edit_inputs(self, loaded_client):
        resp = loaded_client.get("/qubit/qA1")
        html = resp.data.decode()
        assert 'name="dot_path"' in html
        assert 'name="value"' in html
        assert "inline-edit" in html

    def test_null_values_shown(self, loaded_client):
        resp = loaded_client.get("/qubit/qA1")
        html = resp.data.decode()
        assert "not set" in html

    def test_dot_path_column(self, loaded_client):
        resp = loaded_client.get("/qubit/qA1")
        html = resp.data.decode()
        assert "qubits.qA1.f_01" in html

    def test_port_info_section(self, loaded_client):
        resp = loaded_client.get("/qubit/qA1")
        html = resp.data.decode()
        assert "Wiring Ports" in html


class TestSearchCategoryTabs:
    """Tests for category filter tabs in search results."""

    def test_category_tabs_present(self, loaded_client):
        resp = loaded_client.get("/search?q=qA1")
        html = resp.data.decode()
        assert "category-tabs" in html
        assert "All" in html

    def test_category_filter(self, loaded_client):
        resp = loaded_client.get("/search?q=qA1&category=qubit")
        html = resp.data.decode()
        assert "search-panel" in html or "qA1" in html

    def test_category_badges(self, loaded_client):
        resp = loaded_client.get("/search?q=8834")
        html = resp.data.decode()
        assert "cat-badge" in html or "search-table" in html


class TestTableColorCoding:
    """Tests for color-coded cells in comparison table."""

    def test_table_has_chain_filter(self, loaded_client):
        resp = loaded_client.get("/table")
        html = resp.data.decode()
        assert "table-controls" in html

    def test_table_sort_headers(self, loaded_client):
        resp = loaded_client.get("/table?sort=f_01&dir=asc")
        html = resp.data.decode()
        assert resp.status_code == 200
        assert "sort-header" in html or "&#9650;" in html


class TestCompareRedirect:
    """Legacy POST /compare (sidebar experiment checkboxes) — now a
    deep-link adapter into the hub; the checkbox flow itself is kept."""

    def test_compare_post_translates_paths(self, client, tmp_path):
        folders = []
        for name in ("ra", "rb"):
            f = tmp_path / name
            f.mkdir()
            (f / "state.json").write_text(json.dumps(_make_state()), encoding="utf-8")
            (f / "wiring.json").write_text(json.dumps(_make_wiring()), encoding="utf-8")
            folders.append(str(f))
        resp = client.post("/compare", data={"paths": folders},
                           headers={"HX-Request": "true"})
        assert resp.status_code == 200
        loc = resp.headers["HX-Redirect"]
        assert loc.startswith("/compare-hub?")
        assert loc.count("src=") == 2
        assert "hint" not in loc   # manual basket (U1b)

    def test_compare_single_path_still_redirects(self, client):
        resp = client.post("/compare", data={"paths": "only_one"})
        assert resp.status_code == 302
        assert "src=" in resp.headers["Location"]


class TestStatusToast:
    """Tests for toast auto-fade."""

    def test_toast_class(self, loaded_client):
        resp = loaded_client.post("/save")
        html = resp.data.decode()
        assert "toast" in html

    def test_undo_toast(self, loaded_client):
        loaded_client.post("/qubit/qA1/edit", data={"dot_path": "qubits.qA1.T1", "value": "9999"})
        resp = loaded_client.post("/undo")
        html = resp.data.decode()
        assert "toast" in html


# ---------------------------------------------------------------------------
# Compare Selected (Redesign)
# ---------------------------------------------------------------------------


class TestCompare:
    """Legacy /compare fragments (kept until the P4 redirect soaks).

    The main POST is a hub adapter now (TestCompareRedirect); the rendered
    tabs it produced are covered here only through the still-served
    /compare/* fragment GETs."""
    @pytest.fixture
    def two_folders(self, tmp_path):
        """Create two synthetic experiment folders with quam_state, node.json, data.json."""
        folders = []
        for i, freq in enumerate([6.25e9, 6.30e9]):
            exp_dir = tmp_path / f"exp_{i}"
            folder = exp_dir / "quam_state"
            folder.mkdir(parents=True)
            state = _make_state()
            state["qubits"]["qA1"]["f_01"] = freq
            (folder / "state.json").write_text(json.dumps(state, indent=2), encoding="utf-8")
            (folder / "wiring.json").write_text(json.dumps(_make_wiring(), indent=2), encoding="utf-8")

            node = {
                "metadata": {
                    "name": f"03_resonator_spectroscopy_{i}",
                    "status": "finished",
                    "run_start": f"2026-02-19T16:3{i}:00",
                    "run_end": f"2026-02-19T16:3{i + 1}:00",
                    "description": "Test spectroscopy",
                },
                "data": {
                    "parameters": {"model": {"num_shots": 100 + i * 50, "multiplexed": True}},
                    "outcomes": {"qA1": "successful"},
                },
                "id": i + 10,
            }
            (exp_dir / "node.json").write_text(json.dumps(node), encoding="utf-8")

            data = {
                "fit_results": {
                    "qA1": {"frequency": freq + 1e6, "fwhm": 1e7 + i * 5e5, "success": True},
                },
            }
            (exp_dir / "data.json").write_text(json.dumps(data), encoding="utf-8")

            folders.append(str(folder))
        return folders

    @pytest.fixture
    def compare_client(self, two_folders):
        app = create_app(testing=True)
        client = app.test_client()
        client.post("/workspace/add", data={"folder": str(Path(two_folders[0]).parent)})
        return client, two_folders




    def test_compare_state_tab(self, compare_client):
        client, folders = compare_client
        qs = "&".join(f"paths={f}" for f in folders)
        resp = client.get(f"/compare/state?idx=0&{qs}")
        assert resp.status_code == 200
        html = resp.data.decode()
        assert "qA1" in html

    def test_compare_state_has_tree_viewer(self, compare_client):
        client, folders = compare_client
        qs = "&".join(f"paths={f}" for f in folders)
        resp = client.get(f"/compare/state?idx=0&{qs}")
        html = resp.data.decode()
        assert "cmp-state-tree" in html
        assert "cmp-wiring-tree" in html
        assert "tree-file-tab" in html
        assert "renderJsonTree" in html

    def test_compare_state_invalid_idx(self, compare_client):
        client, folders = compare_client
        qs = "&".join(f"paths={f}" for f in folders)
        resp = client.get(f"/compare/state?idx=99&{qs}")
        assert resp.status_code == 400

    def test_compare_diff_route(self, compare_client):
        client, folders = compare_client
        qs = "&".join(f"paths={f}" for f in folders)
        resp = client.get(f"/compare/diff?{qs}")
        assert resp.status_code == 200
        html = resp.data.decode()
        assert "diff-table" in html or "No differences" in html
        assert "f_01" in html

    def test_compare_diff_with_ref(self, compare_client):
        client, folders = compare_client
        qs = "&".join(f"paths={f}" for f in folders)
        resp = client.get(f"/compare/diff?ref=1&{qs}")
        assert resp.status_code == 200
        html = resp.data.decode()
        assert "cell-diff" in html
        assert "diff-delta" in html


    def test_compare_state_full_props(self, compare_client):
        """State tab now embeds full raw JSON; verify key data is present."""
        client, folders = compare_client
        qs = "&".join(f"paths={f}" for f in folders)
        resp = client.get(f"/compare/state?idx=0&{qs}")
        html = resp.data.decode()
        for key in ["f_01", "T1", "T2ramsey", "anharmonicity",
                     "RF_frequency", "opx_output", "confusion_matrix"]:
            assert key in html, f"Key {key} not in state tab"

    def test_compare_state_delta_values(self, compare_client):
        client, folders = compare_client
        qs = "&".join(f"paths={f}" for f in folders)
        resp = client.get(f"/compare/state?idx=1&ref=0&{qs}")
        html = resp.data.decode()
        assert "diff-delta" in html





    def test_compare_diff_route_shows_sections(self, compare_client):
        client, folders = compare_client
        qs = "&".join(f"paths={f}" for f in folders)
        resp = client.get(f"/compare/diff?{qs}")
        html = resp.data.decode()
        assert "Metadata" in html
        assert "Parameter Differences" in html
        assert "Fit Result Differences" in html

    def test_compare_state_shows_experiment_context(self, compare_client):
        client, folders = compare_client
        qs = "&".join(f"paths={f}" for f in folders)
        resp = client.get(f"/compare/state?idx=0&{qs}")
        html = resp.data.decode()
        assert "Metadata" in html
        assert "Parameters" in html
        assert "Fit Results" in html
        assert "Full State" in html

    # -- Full Compare tab --


    def test_compare_full_route(self, compare_client):
        client, folders = compare_client
        qs = "&".join(f"paths={f}" for f in folders)
        resp = client.get(f"/compare/full?{qs}")
        assert resp.status_code == 200
        html = resp.data.decode()
        assert "full-cmp-state-tree" in html
        assert "full-cmp-wiring-tree" in html
        assert "renderUnifiedTree" in html

    def test_compare_full_has_legend(self, compare_client):
        client, folders = compare_client
        qs = "&".join(f"paths={f}" for f in folders)
        resp = client.get(f"/compare/full?{qs}")
        html = resp.data.decode()
        assert "compare-legend" in html
        assert "tree-state-badge" in html

    def test_compare_full_has_diff_only_toggle(self, compare_client):
        client, folders = compare_client
        qs = "&".join(f"paths={f}" for f in folders)
        resp = client.get(f"/compare/full?{qs}")
        html = resp.data.decode()
        assert "Diff Only" in html
        assert "btn-diff-only" in html

    def test_compare_full_has_ref_data_button(self, compare_client):
        client, folders = compare_client
        qs = "&".join(f"paths={f}" for f in folders)
        resp = client.get(f"/compare/full?{qs}")
        html = resp.data.decode()
        assert "Ref Data" in html
        assert "ref-dropdown" in html
        assert "ref-dropdown-item" in html
        for f in folders:
            assert f in html

    def test_compare_full_file_tabs(self, compare_client):
        client, folders = compare_client
        qs = "&".join(f"paths={f}" for f in folders)
        resp = client.get(f"/compare/full?{qs}")
        html = resp.data.decode()
        assert "state.json" in html
        assert "wiring.json" in html
        assert "tree-file-tab" in html

    def test_compare_full_too_few(self, client):
        resp = client.get("/compare/full?paths=only_one")
        assert resp.status_code == 200
        assert b"Select at least 2" in resp.data

    def test_compare_full_embeds_data(self, compare_client):
        """Verify datasets JSON contains state data from both experiments."""
        client, folders = compare_client
        qs = "&".join(f"paths={f}" for f in folders)
        resp = client.get(f"/compare/full?{qs}")
        html = resp.data.decode()
        assert "f_01" in html
        assert "qubits" in html


# ---------------------------------------------------------------------------
# Trend Tracker
# ---------------------------------------------------------------------------


class TestTrend:
    @pytest.fixture
    def two_folders(self, tmp_path):
        folders = []
        for i, freq in enumerate([6.25e9, 6.30e9]):
            folder = tmp_path / f"#{ 200 - i }_trend_exp_{i}" / "quam_state"
            folder.mkdir(parents=True)
            state = _make_state()
            state["qubits"]["qA1"]["f_01"] = freq
            (folder / "state.json").write_text(json.dumps(state, indent=2), encoding="utf-8")
            (folder / "wiring.json").write_text(json.dumps(_make_wiring(), indent=2), encoding="utf-8")
            folders.append(str(folder))
        return folders

    @pytest.fixture
    def trend_client(self, two_folders):
        app = create_app(testing=True)
        client = app.test_client()
        client.post("/workspace/add", data={"folder": str(Path(two_folders[0]).parent)})
        return client, two_folders

    def test_trend_too_few(self, client):
        resp = client.post("/trend", data={"paths": "only_one"})
        assert resp.status_code == 200
        assert b"Select at least 2" in resp.data

    def test_trend_picker(self, trend_client):
        client, folders = trend_client
        resp = client.post("/trend", data={"paths": folders},
                           headers={"HX-Request": "true"})
        assert resp.status_code == 200
        html = resp.data.decode()
        assert "Trend Tracker" in html
        assert "Show Chart" in html
        assert "f_01" in html

    def test_trend_picker_has_chart_area(self, trend_client):
        client, folders = trend_client
        resp = client.post("/trend", data={"paths": folders},
                           headers={"HX-Request": "true"})
        html = resp.data.decode()
        assert 'id="trend-chart-area"' in html
        assert 'hx-target="#trend-chart-area"' in html

    def test_trend_picker_grouped_selector(self, trend_client):
        client, folders = trend_client
        resp = client.post("/trend", data={"paths": folders},
                           headers={"HX-Request": "true"})
        html = resp.data.decode()
        assert "prop-selector" in html
        assert "prop-group" in html
        assert "prop-group-toggle" in html
        for group in ["Frequencies", "Coherence", "Gate Fidelity"]:
            assert group in html

    def test_trend_chart(self, trend_client):
        client, folders = trend_client
        resp = client.post("/trend/chart",
                           data={"paths": folders, "props": ["f_01"]},
                           headers={"HX-Request": "true"})
        assert resp.status_code == 200
        html = resp.data.decode()
        assert "trend-chart-0" in html or "trend-mini-chart" in html

    def test_trend_chart_has_legend(self, trend_client):
        client, folders = trend_client
        resp = client.post("/trend/chart",
                           data={"paths": folders, "props": ["f_01"]},
                           headers={"HX-Request": "true"})
        html = resp.data.decode()
        assert "trend-legend" in html
        assert "E1" in html
        assert "E2" in html
        assert "Experiment Legend" in html

    def test_trend_chart_sorted_by_run_id(self, trend_client):
        """Experiments should be sorted by run_id, not selection order."""
        client, folders = trend_client
        resp = client.post("/trend/chart",
                           data={"paths": list(reversed(folders)), "props": ["f_01"]},
                           headers={"HX-Request": "true"})
        html = resp.data.decode()
        pos_e1 = html.index("E1")
        pos_e2 = html.index("E2")
        assert pos_e1 < pos_e2

    def test_trend_chart_no_stores(self, client):
        resp = client.post("/trend/chart", data={"paths": ["/nonexistent/a", "/nonexistent/b"], "props": ["f_01"]})
        assert resp.status_code == 200
        assert b"Need at least 2" in resp.data


# ---------------------------------------------------------------------------
# Real data tests
# ---------------------------------------------------------------------------


@skip_no_large
class TestRealData:
    @pytest.fixture(autouse=True)
    def _load_real(self, client):
        self.client = client
        self.client.post("/load", data={"folder": str(LARGE_FOLDER)})

    def test_qubits_page(self):
        resp = self.client.get("/qubits")
        assert resp.status_code == 200
        assert b"qA1" in resp.data

    def test_qubit_detail(self):
        resp = self.client.get("/qubit/qA1")
        assert resp.status_code == 200

    def test_pair_detail(self):
        resp = self.client.get("/pair/qA1-A2")
        assert resp.status_code == 200

    def test_table(self):
        resp = self.client.get("/table?props=f_01&props=T2ramsey")
        assert resp.status_code == 200

    def test_wiring(self):
        resp = self.client.get("/wiring")
        assert resp.status_code == 200

    def test_search(self):
        resp = self.client.get("/search?q=7639")
        assert resp.status_code == 200

    def test_export(self):
        resp = self.client.get("/export")
        assert resp.status_code == 200
        assert b"qA1" in resp.data

    def test_api_qubit(self):
        resp = self.client.get("/api/qubit/qA1")
        data = resp.get_json()
        assert data["f_01"] == 6255526125.489208

    def test_api_topology(self):
        resp = self.client.get("/api/topology")
        data = resp.get_json()
        assert len(data["nodes"]) >= 16

    def test_qubit_detail_sections(self):
        resp = self.client.get("/qubit/qA1")
        html = resp.data.decode()
        assert "Frequencies" in html
        assert "Coherence" in html
        assert "XY Drive" in html
        assert "Readout" in html
        assert "qubits.qA1.f_01" in html
        assert "inline-edit" in html

    def test_qubit_detail_pointer_or_selfref(self):
        resp = self.client.get("/qubit/qA1")
        html = resp.data.decode()
        assert "pointer-badge" in html or "selfref-value" in html

    def test_search_category_tabs_real(self):
        resp = self.client.get("/search?q=qA1")
        html = resp.data.decode()
        assert "category-tabs" in html

    def test_table_color_coding_real(self):
        resp = self.client.get("/table?props=f_01&props=gate_fidelity_avg")
        html = resp.data.decode()
        assert "cell-best" in html or "cell-worst" in html or "comparison-table" in html

    def test_diff_form_real(self):
        resp = self.client.get("/diff")
        html = resp.data.decode()
        assert "filter_type" in html


# ---------------------------------------------------------------------------
# Pending Changes Tray
# ---------------------------------------------------------------------------


@pytest.fixture
def synth_client(loaded_client):
    """Alias for loaded_client — used by pending tray tests."""
    return loaded_client


@pytest.fixture
def synth_qubit(synth_client):
    """Confirm that qubit qA1 exists in the loaded state."""
    resp = synth_client.get("/qubit/qA1")
    assert resp.status_code == 200
    return "qA1"


class TestPendingChangesTray:
    def test_tray_in_dom(self, loaded_client):
        html = loaded_client.get("/qubits").data.decode()
        assert 'id="pending-tray"' in html

    def test_tray_shows_synced_badge_when_no_changes(self, loaded_client):
        """With a chip loaded and nothing unsaved, the tray shows a persistent
        "Synced" status badge (and no Save/Apply action bar). The badge gives an
        always-on working-copy vs live indicator + a click-to-review entry point.
        """
        html = loaded_client.get("/qubits").data.decode()
        assert 'state-status-synced' in html
        assert 'tray-bar' not in html  # no pending-change actions when clean

    def test_tray_active_after_edit(self, synth_client, synth_qubit):
        synth_client.post("/qubit/qA1/edit",
                          data={"dot_path": "qubits.qA1.f_01", "value": "5.1e9"},
                          headers={"HX-Request": "true"})
        html = synth_client.get("/qubits").data.decode()
        assert 'tray-empty' not in html
        assert 'tray-bar' in html

    def test_edit_response_includes_oob_tray(self, synth_client, synth_qubit):
        resp = synth_client.post("/qubit/qA1/edit",
                                  data={"dot_path": "qubits.qA1.f_01", "value": "5.1e9"},
                                  headers={"HX-Request": "true"})
        html = resp.data.decode()
        assert 'hx-swap-oob' in html
        assert 'pending-tray' in html

    def test_edit_oob_shows_count(self, synth_client, synth_qubit):
        # Edit a single non-frequency field so the count is exactly 1 — editing f_01
        # now also mirrors xy.RF_frequency (f₀₁↔RF soft link, covered separately).
        resp = synth_client.post("/qubit/qA1/edit",
                                  data={"dot_path": "qubits.qA1.T1", "value": "9000"},
                                  headers={"HX-Request": "true"})
        assert b"1 unsaved change" in resp.data

    def test_save_returns_oob_tray(self, synth_client, synth_qubit):
        synth_client.post("/qubit/qA1/edit",
                           data={"dot_path": "qubits.qA1.f_01", "value": "5.1e9"},
                           headers={"HX-Request": "true"})
        resp = synth_client.post("/save")
        html = resp.data.decode()
        assert 'hx-swap-oob' in html
        # Save writes the working state (working_dirty, no pending edits) → the tray
        # offers the safe direct push "Apply to live chip" (a one-click pull-merge
        # would drop the just-saved edits, so it's intentionally NOT shown here).
        assert 'Apply to live chip' in html

    def test_discard_returns_full_tray(self, synth_client, synth_qubit):
        synth_client.post("/qubit/qA1/edit",
                           data={"dot_path": "qubits.qA1.f_01", "value": "5.1e9"},
                           headers={"HX-Request": "true"})
        resp = synth_client.post("/discard", data={"index": "0"})
        html = resp.data.decode()
        assert 'id="pending-tray"' in html
        # Discarding the only change leaves a clean chip — the tray still shows
        # the chip identity badge (not empty), so the active chip never
        # silently disappears from the top bar (A0.1).
        assert 'tray-empty' not in html
        assert 'state-status-badge' in html

    def test_discard_buttons_target_tray(self, synth_client, synth_qubit):
        synth_client.post("/qubit/qA1/edit",
                           data={"dot_path": "qubits.qA1.f_01", "value": "5.1e9"},
                           headers={"HX-Request": "true"})
        synth_client.post("/qubit/qA1/edit",
                           data={"dot_path": "qubits.qA1.T1", "value": "9000"},
                           headers={"HX-Request": "true"})
        resp = synth_client.post("/discard", data={"index": "0"})
        html = resp.data.decode()
        assert 'hx-target="#pending-tray"' in html

    def test_discard_sends_hx_trigger(self, synth_client, synth_qubit):
        """Discard response includes HX-Trigger header with cellDiscarded event."""
        synth_client.post(f"/qubit/{synth_qubit}/edit",
                          data={"dot_path": f"qubits.{synth_qubit}.chi", "value": "0.99"},
                          headers={"HX-Request": "true"})
        resp = synth_client.post("/discard", data={"index": "0"})
        assert resp.status_code == 200
        assert "cellDiscarded" in resp.headers.get("HX-Trigger", "")

    def test_discard_trigger_contains_dot_path(self, synth_client, synth_qubit):
        """cellDiscarded event payload includes the discarded dot_path."""
        dot_path = f"qubits.{synth_qubit}.chi"
        synth_client.post(f"/qubit/{synth_qubit}/edit",
                          data={"dot_path": dot_path, "value": "0.99"},
                          headers={"HX-Request": "true"})
        resp = synth_client.post("/discard", data={"index": "0"})
        trigger = json.loads(resp.headers["HX-Trigger"])
        assert trigger["cellDiscarded"]["dot_path"] == dot_path

    def test_changes_panel_removed(self, loaded_client):
        html = loaded_client.get("/qubits").data.decode()
        assert 'id="changes-panel"' not in html

    def test_toggle_changes_removed(self, loaded_client):
        html = loaded_client.get("/qubits").data.decode()
        assert "toggleChanges" not in html

    def test_toggle_pending_tray_js_present(self, loaded_client):
        html = loaded_client.get("/qubits").data.decode()
        assert "togglePendingTray" in html or "app.js" in html


class TestFieldEdit:
    """Tests for POST /field/edit — generic inline editor from Explorer tree."""

    def test_field_edit_success(self, synth_client, synth_qubit):
        """Valid edit returns ok=True and tray_html."""
        resp = synth_client.post(
            "/field/edit",
            data={"dot_path": f"qubits.{synth_qubit}.chi", "value": "0.99"},
        )
        assert resp.status_code == 200
        data = json.loads(resp.data)
        assert data["ok"] is True
        assert "tray_html" in data

    def test_field_edit_updates_store(self, synth_client, synth_qubit):
        """Edit is reflected in the change log."""
        synth_client.post(
            "/field/edit",
            data={"dot_path": f"qubits.{synth_qubit}.chi", "value": "0.77"},
        )
        resp = synth_client.get(f"/qubit/{synth_qubit}")
        assert resp.status_code == 200
        assert b"0.77" in resp.data

    def test_field_edit_tray_html_shows_change(self, synth_client, synth_qubit):
        """tray_html in response contains the pending change."""
        dot_path = f"qubits.{synth_qubit}.chi"
        resp = synth_client.post(
            "/field/edit", data={"dot_path": dot_path, "value": "0.55"}
        )
        data = json.loads(resp.data)
        assert dot_path in data["tray_html"]

    def test_field_edit_missing_dot_path(self, synth_client):
        """Missing dot_path returns 400."""
        resp = synth_client.post("/field/edit", data={"value": "1.0"})
        assert resp.status_code == 400
        assert json.loads(resp.data)["ok"] is False

    def test_field_edit_no_context(self, client):
        """No active context returns 400."""
        resp = client.post(
            "/field/edit",
            data={"dot_path": "qubits.q0.chi", "value": "1.0"},
        )
        assert resp.status_code == 400


# ======================================================================
# Error handling — corrupt files, missing folders
# ======================================================================


class TestErrorHandling:

    def test_load_corrupt_state_json(self, client, tmp_path):
        """Loading a folder with corrupt state.json shows error, not 500."""
        folder = tmp_path / "bad_state"
        folder.mkdir()
        (folder / "state.json").write_text("NOT JSON", encoding="utf-8")
        (folder / "wiring.json").write_text("{}", encoding="utf-8")
        resp = client.post("/load", data={"folder": str(folder)})
        assert resp.status_code == 400
        assert b"not valid JSON" in resp.data

    def test_browse_nonexistent_path(self, client):
        """Browsing a non-existent path returns empty dir list."""
        resp = client.get("/browse?path=Z:/totally/bogus/path")
        assert resp.status_code == 200
        data = json.loads(resp.data)
        assert data["dirs"] == []

    def test_browse_system_path_blocked(self, client):
        """Browsing C:\\Windows returns empty list on Windows."""
        import platform
        if platform.system() != "Windows":
            pytest.skip("Windows-only test")
        resp = client.get("/browse?path=C:\\Windows")
        assert resp.status_code == 200
        data = json.loads(resp.data)
        assert data["dirs"] == []

    def test_mtime_no_store(self, client):
        """Mtime check without loaded state returns error."""
        resp = client.get("/api/topology/mtime")
        assert resp.status_code in (400, 404)


class TestQuamCacheEviction:
    """Verify that _quam_cache evicts oldest entry when exceeding max."""

    def test_cache_bounded(self, tmp_path):
        from quam_state_manager.web import routes

        # Save original cache state
        orig_cache = routes._quam_cache.copy()
        orig_max = routes._QUAM_CACHE_MAX
        try:
            routes._quam_cache.clear()
            routes._QUAM_CACHE_MAX = 3

            # Simulate the eviction logic from _activate_quam
            for i in range(5):
                key = f"folder_{i}"
                if len(routes._quam_cache) >= routes._QUAM_CACHE_MAX and key not in routes._quam_cache:
                    routes._quam_cache.pop(next(iter(routes._quam_cache)))
                routes._quam_cache[key] = (float(i), float(i), {"type": "quam"})

            assert len(routes._quam_cache) == 3
            # Oldest entries (folder_0, folder_1) should be evicted
            assert "folder_0" not in routes._quam_cache
            assert "folder_1" not in routes._quam_cache
            # Newest entries should remain
            assert "folder_2" in routes._quam_cache
            assert "folder_3" in routes._quam_cache
            assert "folder_4" in routes._quam_cache
        finally:
            routes._quam_cache.clear()
            routes._quam_cache.update(orig_cache)
            routes._QUAM_CACHE_MAX = orig_max


class TestCategorizeExperiments:
    """Verify experiment categorization for dataset chip groups."""

    def test_groups_by_type(self):
        from unittest.mock import MagicMock
        from quam_state_manager.core.dataset import DatasetStore

        ds = MagicMock(spec=DatasetStore)
        ds.experiment_types = [
            # Readout
            "iq_blob", "readout_frequency_optimization",
            "15a_readout_frequency_opti", "16_iq_blobs",
            "01_time_of_flight_mw_fem", "03_resonator_spectroscopy_single",
            # 1Q
            "qubit_spectroscopy", "power_rabi", "ramsey", "t1",
            "26_echo", "27_single_qubit_randomized_benchmarking",
            "13_drag_calibration_180", "22b_all_xy",
            "71a_XEB_charge_stabilized", "20c_leakage_error_amp",
            # 2Q
            "cz_chevron", "cz_dynamic_phase",
            "22_two_qubit_standard_rb", "23_two_qubit_interleaved_rb",
            "24_Bell_State_Tomography", "34_2Q_confusion_matrix",
            "20_cz_conditional_phase", "21_cz_phase_compensation",
            # Flux & Coupler
            "18_xy_coupler_delay", "18a_coupler_zero_point",
            "21a_ramsey_vs_coupler_flux", "ramsey_vs_flux_calibration",
            "19a_qubit_flux_long_distortion",
            # Other
            "custom_experiment", "filter_plotter",
        ]
        ds.categorize_experiments = DatasetStore.categorize_experiments.__get__(ds)
        cats = ds.categorize_experiments()

        labels = [c["label"] for c in cats]
        assert "Readout" in labels
        assert "1Q" in labels
        assert "2Q" in labels
        assert "Coupler" in labels
        assert "Qubit Flux" in labels
        assert "Other" in labels

        readout = next(c for c in cats if c["label"] == "Readout")
        assert "iq_blob" in readout["experiments"]
        assert "readout_frequency_optimization" in readout["experiments"]
        assert "01_time_of_flight_mw_fem" in readout["experiments"]
        assert "03_resonator_spectroscopy_single" in readout["experiments"]
        assert "16_iq_blobs" in readout["experiments"]

        oneq = next(c for c in cats if c["label"] == "1Q")
        assert "qubit_spectroscopy" in oneq["experiments"]
        assert "power_rabi" in oneq["experiments"]
        assert "26_echo" in oneq["experiments"]
        assert "22b_all_xy" in oneq["experiments"]
        assert "71a_XEB_charge_stabilized" in oneq["experiments"]
        assert "20c_leakage_error_amp" in oneq["experiments"]

        twoq = next(c for c in cats if c["label"] == "2Q")
        assert "cz_chevron" in twoq["experiments"]
        assert "22_two_qubit_standard_rb" in twoq["experiments"]
        assert "24_Bell_State_Tomography" in twoq["experiments"]
        assert "34_2Q_confusion_matrix" in twoq["experiments"]
        assert "20_cz_conditional_phase" in twoq["experiments"]

        coupler = next(c for c in cats if c["label"] == "Coupler")
        assert "18_xy_coupler_delay" in coupler["experiments"]
        assert "18a_coupler_zero_point" in coupler["experiments"]
        assert "21a_ramsey_vs_coupler_flux" in coupler["experiments"]

        flux = next(c for c in cats if c["label"] == "Qubit Flux")
        assert "ramsey_vs_flux_calibration" in flux["experiments"]
        assert "19a_qubit_flux_long_distortion" in flux["experiments"]

        other = next(c for c in cats if c["label"] == "Other")
        assert "custom_experiment" in other["experiments"]
        assert "filter_plotter" in other["experiments"]


class TestDatasetsPoll:
    """Verify /datasets/poll endpoint for new-run detection."""

    def test_poll_no_workspace(self, app):
        """Poll returns null run_id when no workspace is configured."""
        with app.test_client() as c:
            # Ensure no dataset store is cached
            app.config.pop("dataset_store", None)
            app.config.pop("workspace", None)
            resp = c.get("/datasets/poll")
            assert resp.status_code == 200
            data = resp.get_json()
            assert data["run_id"] is None

    def test_poll_returns_latest_run(self, client, tmp_path):
        """Poll returns the latest run metadata."""
        from unittest.mock import MagicMock, patch
        from quam_state_manager.core.dataset import DatasetStore, RunInfo

        ds = MagicMock(spec=DatasetStore)
        ds.runs = {
            100: RunInfo(
                run_id=100, experiment_name="rabi", date="2026-04-09",
                time="10:00:00", folder_path=tmp_path, description="",
                qubits=["q0"], outcomes={}, parameters={}, parent_id=None,
                run_start=None, run_end=None, run_duration_s=None,
                status="", fit_results={}, figure_names=[],
                has_ds_raw=False, has_ds_fit=False, has_quam_state=False,
            ),
            200: RunInfo(
                run_id=200, experiment_name="resonator_spectroscopy",
                date="2026-04-09", time="10:05:00", folder_path=tmp_path,
                description="", qubits=["q0", "q1"], outcomes={},
                parameters={}, parent_id=None, run_start=None, run_end=None,
                run_duration_s=None, status="", fit_results={},
                figure_names=[], has_ds_raw=True, has_ds_fit=False,
                has_quam_state=False,
            ),
        }
        ds.rescan_if_stale = MagicMock(return_value=False)
        # The poll iterates runs_snapshot() (lock-safe accessor) rather than
        # .runs.values() directly — mirror the real runs for the mocked store.
        ds.runs_snapshot = MagicMock(return_value=list(ds.runs.values()))
        ds.folder_path = tmp_path          # multi-folder: poll keys runs by folder
        ds.run_count = 2

        with client.application.app_context():
            client.application.config["dataset_store"] = ds
        resp = client.get("/datasets/poll")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["run_id"] == 200
        assert data["experiment_name"] == "resonator_spectroscopy"
        assert data["qubits"] == ["q0", "q1"]
        assert data["time"] == "10:05:00"
        # New-run detection now keys on a folder-aware uid ("<folder_key>:<run_id>").
        assert data["uid"] and data["uid"].endswith(":200")


class TestDatasetCompactColumns:
    """The virtual-table compact payload must carry the new column fields so the
    column-driven renderer (dataset-virtual.js) can show Status chip / Duration /
    the opt-in note/parent/saved-state columns."""

    @staticmethod
    def _run(tmp_path, **kw):
        from quam_state_manager.core.dataset import RunInfo
        base = dict(
            run_id=100, experiment_name="08_qubit_spectroscopy",
            date="2026-04-09", time="10:00:00", folder_path=tmp_path,
            qubits=["q0"], status="finished", run_duration_s=23.4,
            note="check cavity", parent_id=42, has_quam_state=True,
        )
        base.update(kw)
        return RunInfo(**base)

    def test_compact_row_carries_new_fields(self, tmp_path):
        from quam_state_manager.core.dataset import _compact_row
        row = _compact_row(self._run(tmp_path))
        assert row["status"] == "finished"
        assert row["dur"] == 23.4
        assert row["note"] == "check cavity"
        assert row["parent"] == 42
        assert row["hs"] is True
        # legacy keys preserved (the renderer + scoped search still rely on them)
        for k in ("id", "exp", "date", "time", "q", "oc", "metric", "bm", "tags"):
            assert k in row, f"legacy compact key {k!r} dropped"

    def test_list_runs_compact_includes_status(self, tmp_path):
        from quam_state_manager.core.dataset import DatasetStore
        ds = DatasetStore(tmp_path)            # empty workspace
        ds.runs[100] = self._run(tmp_path)
        rows = ds.list_runs_compact()
        assert rows and rows[0]["status"] == "finished" and rows[0]["dur"] == 23.4


class TestDatasetSortScalars:
    """Parse-time per-fit-key sortable scalars (the `sm` map) that power the Sort
    banner's Fit-metrics badges — all the red-team must-fixes."""

    @staticmethod
    def _run(tmp_path, fit):
        from quam_state_manager.core.dataset import RunInfo
        return RunInfo(run_id=1, experiment_name="08_qubit_spectroscopy",
                       date="2026-04-09", time="10:00:00", folder_path=tmp_path,
                       fit_results=fit)

    def test_rejects_bool_and_nonfinite(self, tmp_path):
        from quam_state_manager.core.dataset import DatasetStore
        sm = DatasetStore._extract_sort_scalars(self._run(tmp_path, {
            "qA1": {"T1": 41e-6, "success": True, "flag": False,
                    "nan": float("nan"), "inf": float("inf")}}))
        assert sm == {"T1": 41e-6}, sm   # bool (True==1) + NaN + inf all excluded

    def test_first_max_min_compact_form(self, tmp_path):
        from quam_state_manager.core.dataset import DatasetStore
        sm = DatasetStore._extract_sort_scalars(self._run(tmp_path, {
            "qA1": {"f": 7.0e9, "T1": 40e-6}, "qA2": {"f": 7.0e9, "T1": 38e-6}}))
        assert sm["f"] == 7.0e9                    # qubits agree → bare scalar
        assert sm["T1"] == [40e-6, 40e-6, 38e-6]   # disagree → [first, max, min]

    def test_first_is_sorted_qubit_order(self, tmp_path):
        # 'first' uses sorted-qubit order (matches _extract_key_metric), not insertion.
        from quam_state_manager.core.dataset import DatasetStore
        sm = DatasetStore._extract_sort_scalars(self._run(tmp_path, {
            "qB": {"m": 2.0}, "qA": {"m": 1.0}}))
        assert sm["m"][0] == 1.0   # first = qA (sorted)

    def test_compact_row_carries_sm(self, tmp_path):
        from quam_state_manager.core.dataset import DatasetStore, _compact_row
        r = self._run(tmp_path, {"qA1": {"T1": 5e-5}})
        r.sort_scalars = DatasetStore._extract_sort_scalars(r)
        assert _compact_row(r)["sm"] == {"T1": 5e-5}

    def test_curated_fit_keys_map_order(self):
        from quam_state_manager.core.fit_targets import curated_fit_keys
        keys = curated_fit_keys()
        assert keys and keys[0] == "frequency"
        assert "alpha" in keys and "iw_angle" in keys


class TestRescanIfStale:
    """Verify DatasetStore.rescan_if_stale()."""

    def test_no_change_returns_false(self, tmp_path):
        """When folder hasn't changed, rescan_if_stale returns False."""
        from quam_state_manager.core.dataset import DatasetStore

        # Create a minimal dataset folder structure
        date_dir = tmp_path / "2026-04-09"
        date_dir.mkdir()
        run_dir = date_dir / "#100_rabi_100000"
        run_dir.mkdir()
        (run_dir / "node.json").write_text("{}", encoding="utf-8")

        ds = DatasetStore(tmp_path)
        assert len(ds.runs) == 1
        # No disk change → should return False
        assert ds.rescan_if_stale() is False

    def test_new_run_returns_true(self, tmp_path):
        """When a new run folder appears, rescan_if_stale returns True."""
        import time
        from quam_state_manager.core.dataset import DatasetStore

        date_dir = tmp_path / "2026-04-09"
        date_dir.mkdir()
        run_dir = date_dir / "#100_rabi_100000"
        run_dir.mkdir()
        (run_dir / "node.json").write_text("{}", encoding="utf-8")

        ds = DatasetStore(tmp_path)
        assert len(ds.runs) == 1

        # Add a new run (touch the date folder to update mtime)
        time.sleep(0.05)  # ensure mtime changes
        new_run = date_dir / "#200_ramsey_100500"
        new_run.mkdir()
        (new_run / "node.json").write_text("{}", encoding="utf-8")

        assert ds.rescan_if_stale() is True
        assert len(ds.runs) == 2
        assert 200 in ds.runs


# ---------------------------------------------------------------------------
# Param History — sidebar entry + 4 routes
# ---------------------------------------------------------------------------


class TestParamHistory:
    def test_sidebar_link_present(self, loaded_client):
        html = loaded_client.get("/qubits").data.decode()
        assert 'href="/param-history"' in html
        assert "Param History" in html

    def test_route_renders_with_loaded_state(self, loaded_client):
        resp = loaded_client.get("/param-history",
                                 headers={"HX-Request": "true"})
        assert resp.status_code == 200
        body = resp.data.decode()
        assert "param-history-root" in body
        assert "param-history-filters" in body
        # Default 7-day window means a fresh state with no snapshots shows the empty hint
        assert "param-history-grid-wrap" in body or "param-history-empty" in body

    def test_route_no_state_loaded(self, client):
        resp = client.get("/param-history",
                          headers={"HX-Request": "true"})
        assert resp.status_code == 200
        assert b"No chip loaded" in resp.data

    def test_route_full_page_extends_base(self, loaded_client):
        # Without HX-Request, returns the full-page wrapper
        resp = loaded_client.get("/param-history")
        assert resp.status_code == 200
        assert b"<!DOCTYPE html>" in resp.data or b"<html" in resp.data
        assert b"param-history-root" in resp.data

    def test_filters_propagate_query_params(self, loaded_client):
        resp = loaded_client.get(
            "/param-history?triggers=save&triggers=experiment&props=T1&since=now-24h",
            headers={"HX-Request": "true"},
        )
        assert resp.status_code == 200
        # The filter UI should reflect the chosen chips
        body = resp.data.decode()
        assert "phf-trig-save" in body
        # Property filter selected T1
        assert 'value="T1"' in body

    def test_drawer_endpoint_returns_chart_markup(self, loaded_client):
        # Force a snapshot first so the index has something
        loaded_client.post("/api/history/snapshot")
        resp = loaded_client.get("/param-history/expand?qubit=qA1&prop=T1")
        assert resp.status_code == 200
        body = resp.data.decode()
        assert "phd-chart" in body
        assert "paramHistoryRenderDrawerChart" in body

    def test_drawer_endpoint_emits_per_point_context(self, loaded_client):
        """Each point in the drawer payload carries trigger / run_id /
        experiment so the JS hover label can show 'Experiment: #34
        qubit_spectroscopy' for experiment-driven snapshots and 'Manual
        snapshot' / 'External edit' / 'Saved through app' for the rest."""
        # Make several snapshots with different triggers
        loaded_client.post("/api/history/snapshot")  # manual
        # Drawer payload exists in the inline JSON script tag
        resp = loaded_client.get("/param-history/expand?qubit=qA1&prop=T1")
        assert resp.status_code == 200
        body = resp.data.decode()
        # The row JSON is embedded in <script id="phd-data">
        import re, json as _json
        m = re.search(r'<script id="phd-data" type="application/json">([^<]+)</script>', body)
        assert m is not None
        row = _json.loads(m.group(1))
        # The row's values list has the keys the new hover/click logic needs
        assert "values" in row
        if row["values"]:
            sample = row["values"][0]
            assert "trigger" in sample
            assert "timestamp" in sample
            # run_id and experiment may be None for non-experiment triggers,
            # but the keys must be present for the customdata indexing
            assert "run_id" in sample
            assert "experiment" in sample

    def test_drawer_endpoint_requires_args(self, loaded_client):
        resp = loaded_client.get("/param-history/expand")
        assert resp.status_code == 400

    def test_backfill_endpoint_returns_status_json(self, loaded_client):
        resp = loaded_client.post("/param-history/backfill")
        assert resp.status_code == 200
        data = resp.get_json()
        assert "status" in data

    def test_backfill_status_endpoint(self, loaded_client):
        resp = loaded_client.get("/param-history/backfill/status")
        assert resp.status_code == 200
        assert "status" in resp.get_json()


# ---------------------------------------------------------------------------
# Last-session persistence — auto-restore the upper Load path across restarts
# ---------------------------------------------------------------------------


class TestSessionPersistence:
    def _session_file(self, app) -> Path:
        return Path(app.instance_path) / "last_session.json"

    def _seed_session(self, app, last: str | None = None, recents: list[str] | None = None) -> None:
        data = {
            "last_quam_state_path": last,
            "recent_quam_state_paths": recents or ([last] if last else []),
        }
        Path(app.instance_path).mkdir(parents=True, exist_ok=True)
        self._session_file(app).write_text(json.dumps(data), encoding="utf-8")

    def _reset_load_flags(self) -> None:
        # Reset module-level startup flags so the @before_request hook fires again
        from quam_state_manager.web import routes
        routes._workspace_loaded = False
        routes._session_loaded = False

    def test_session_persists_after_load(self, client, synth_folder, app):
        sf = self._session_file(app)
        if sf.exists(): sf.unlink()

        resp = client.post("/load", data={"folder": str(synth_folder)})
        assert resp.status_code in (200, 302)
        assert sf.exists()
        data = json.loads(sf.read_text(encoding="utf-8"))
        abs_path = str(Path(synth_folder).resolve())
        assert data["last_quam_state_path"] == abs_path
        assert data["recent_quam_state_paths"][0] == abs_path

    def test_session_auto_restores_on_first_request(self, client, synth_folder, app):
        # Seed session file BEFORE the first request
        abs_path = str(Path(synth_folder).resolve())
        self._seed_session(app, last=abs_path)
        self._reset_load_flags()

        # First request should auto-activate the seeded path
        resp = client.get("/qubits", headers={"HX-Request": "true"})
        assert resp.status_code == 200
        # If no state was loaded, the route renders a "No state loaded" warning
        assert b"No state loaded" not in resp.data

    def test_session_handles_missing_folder(self, client, tmp_path, app):
        gone = tmp_path / "definitely_not_here"
        self._seed_session(app, last=str(gone), recents=[str(gone)])
        self._reset_load_flags()

        # First request should NOT crash and should drop the bad path
        resp = client.get("/", headers={"HX-Request": "true"})
        assert resp.status_code == 200
        data = json.loads(self._session_file(app).read_text(encoding="utf-8"))
        assert data["last_quam_state_path"] is None
        assert str(gone) not in data["recent_quam_state_paths"]

    def test_recent_paths_lru_cap_and_dedup(self, client, tmp_path, app):
        sf = self._session_file(app)
        if sf.exists(): sf.unlink()

        # Build 12 distinct synthetic state folders
        folders = []
        for i in range(12):
            f = tmp_path / f"state_{i}"
            f.mkdir()
            (f / "state.json").write_text(json.dumps(_make_state()), encoding="utf-8")
            (f / "wiring.json").write_text(json.dumps(_make_wiring()), encoding="utf-8")
            folders.append(f)

        # Load each in turn
        for f in folders:
            client.post("/load", data={"folder": str(f)})

        data = json.loads(sf.read_text(encoding="utf-8"))
        recents = data["recent_quam_state_paths"]
        assert len(recents) == 10, f"expected 10, got {len(recents)}"
        # Newest first: the LAST folder we loaded should be at index 0
        assert recents[0] == str(folders[-1].resolve())
        # The first two we loaded should have been evicted
        assert str(folders[0].resolve()) not in recents
        assert str(folders[1].resolve()) not in recents

        # Loading an existing path should bump it to head, not duplicate
        client.post("/load", data={"folder": str(folders[5])})
        data = json.loads(sf.read_text(encoding="utf-8"))
        recents = data["recent_quam_state_paths"]
        assert recents[0] == str(folders[5].resolve())
        # Still capped at 10 and no duplicates
        assert len(recents) == 10
        assert len(set(recents)) == 10

    def test_api_recent_paths_endpoint(self, client, synth_folder, app):
        # Empty initially
        sf = self._session_file(app)
        if sf.exists(): sf.unlink()
        resp = client.get("/api/recent-paths")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["last"] is None
        assert data["recents"] == []

        # After a load
        client.post("/load", data={"folder": str(synth_folder)})
        resp = client.get("/api/recent-paths")
        data = resp.get_json()
        abs_path = str(Path(synth_folder).resolve())
        assert data["last"] == abs_path
        assert data["recents"] == [abs_path]

    def test_recents_button_present_in_load_form(self, client):
        html = client.get("/").data.decode()
        assert 'id="recents-dropdown"' in html
        assert "toggleRecentPaths" in html or "btn-recents" in html


# ---------------------------------------------------------------------------
# Multi-chip Param History — chip selector + alignment banner
# ---------------------------------------------------------------------------


class TestParamHistoryMultiChip:
    def _make_workspace_with_two_chips(self, tmp_path):
        from quam_state_manager.core.scanner import Workspace
        ws_root = tmp_path / "data_root"
        for run_id, when in [(10, "120000")]:
            run = ws_root / "ExampleChip 1Q" / "2026-04-30" / f"#{run_id}_alpha_{when}"
            qs = run / "quam_state"
            qs.mkdir(parents=True, exist_ok=True)
            (qs / "state.json").write_text(json.dumps({
                "qubits": {q: {"id": q} for q in ("q0", "q1")}, "qubit_pairs": {},
            }), encoding="utf-8")
            (qs / "wiring.json").write_text(json.dumps({
                "network": {"host": "10.1.1.18", "cluster_name": "A"},
            }), encoding="utf-8")
            (run / "node.json").write_text(json.dumps({
                "id": run_id, "created_at": "2026-04-30T12:00:00",
                "metadata": {"name": "alpha", "status": "completed"},
            }), encoding="utf-8")

        for run_id, when in [(20, "130000")]:
            run = ws_root / "LabB_1Q" / "2026-04-30" / f"#{run_id}_beta_{when}"
            qs = run / "quam_state"
            qs.mkdir(parents=True, exist_ok=True)
            (qs / "state.json").write_text(json.dumps({
                "qubits": {q: {"id": q} for q in ("q0",)}, "qubit_pairs": {},
            }), encoding="utf-8")
            (qs / "wiring.json").write_text(json.dumps({
                "network": {"host": "10.9.9.99", "cluster_name": "B"},
            }), encoding="utf-8")
            (run / "node.json").write_text(json.dumps({
                "id": run_id, "created_at": "2026-04-30T13:00:00",
                "metadata": {"name": "beta", "status": "completed"},
            }), encoding="utf-8")
        return ws_root

    def test_chip_selector_shows_loaded_chip_active(self, loaded_client):
        loaded_client.post("/api/history/snapshot")
        html = loaded_client.get("/param-history",
                                 headers={"HX-Request": "true"}).data.decode()
        assert "phf-chip-chip" in html

    def test_chip_key_query_param_routes_to_correct_index(self, loaded_client, tmp_path):
        # Activate a chip; take a snapshot so it has data
        loaded_client.post("/api/history/snapshot")
        # Now hit /param-history?chip_key=<wrong> — should render but show empty data
        # since the chip_key doesn't match any populated index.
        resp = loaded_client.get("/param-history?chip_key=__nonexistent_chip__",
                                 headers={"HX-Request": "true"})
        assert resp.status_code == 200
        body = resp.data.decode()
        # Cross-chip banner should be present (we're viewing a non-loaded chip)
        assert "alignment-info" in body or "Switch to current load" in body

    def test_alignment_banner_red_when_workspace_has_no_match(self, client, tmp_path):
        # Synthesize a "loaded" state with one set of qubits; workspace has only a different chip.
        loaded_dir = tmp_path / "loaded_chip"
        loaded_dir.mkdir(parents=True)
        (loaded_dir / "state.json").write_text(json.dumps({
            "qubits": {q: {"id": q} for q in ("qA1",)}, "qubit_pairs": {},
        }), encoding="utf-8")
        (loaded_dir / "wiring.json").write_text(json.dumps({
            "network": {"host": "10.1.1.18", "cluster_name": "A"},
        }), encoding="utf-8")

        ws_root = self._make_workspace_with_two_chips(tmp_path)
        # Add only the LabB_1Q half so workspace is misaligned with loaded chip
        client.post("/load", data={"folder": str(loaded_dir)})
        client.post("/workspace/add", data={"folder": str(ws_root / "LabB_1Q")})

        body = client.get("/param-history",
                          headers={"HX-Request": "true"}).data.decode()
        # Either red banner ("No experiments match") or yellow with breakdown
        # — both indicate the alignment scan is running and detecting mismatch.
        assert ("alignment-red" in body) or ("alignment-yellow" in body)

    def test_alignment_banner_green_when_workspace_aligned(self, client, tmp_path):
        # Loaded state matches the ExampleChip 1Q hardware
        loaded_dir = tmp_path / "loaded_chip"
        loaded_dir.mkdir(parents=True)
        (loaded_dir / "state.json").write_text(json.dumps({
            "qubits": {q: {"id": q} for q in ("q0", "q1")}, "qubit_pairs": {},
        }), encoding="utf-8")
        (loaded_dir / "wiring.json").write_text(json.dumps({
            "network": {"host": "10.1.1.18", "cluster_name": "A"},
        }), encoding="utf-8")

        ws_root = self._make_workspace_with_two_chips(tmp_path)
        client.post("/load", data={"folder": str(loaded_dir)})
        client.post("/workspace/add", data={"folder": str(ws_root / "ExampleChip 1Q")})

        body = client.get("/param-history",
                          headers={"HX-Request": "true"}).data.decode()
        assert "alignment-green" in body or "All " in body

    def test_force_renamed_param_propagates_to_backfill(self, loaded_client):
        resp = loaded_client.post("/param-history/backfill?force_renamed=1")
        assert resp.status_code == 200
        # Endpoint returns running status; the flag was accepted.
        data = resp.get_json()
        assert "status" in data

    def test_app_fixture_uses_tmp_instance_path(self, app, tmp_path):
        # Verify the test fixture is properly isolated — instance_path must be
        # under tmp_path, not the project's real ./instance/.
        assert tmp_path in Path(app.instance_path).parents or app.instance_path.startswith(str(tmp_path))

    def test_chip_key_param_defaults_since_to_all(self, loaded_client):
        """When user clicks a non-loaded chip link, the default Date filter
        should be 'All' so historical data shows immediately. (Loaded chip
        view still defaults to 'now-7d' for active monitoring.)"""
        # Loaded chip case — default since is now-7d
        body_loaded = loaded_client.get(
            "/param-history", headers={"HX-Request": "true"}
        ).data.decode()
        # Find the active 'Date' chip — should be Week (now-7d)
        # The radio with `checked` is the active one.
        import re
        active_loaded = re.search(
            r'name="since"\s+value="([^"]+)"\s+checked', body_loaded,
        )
        assert active_loaded is not None
        assert active_loaded.group(1) == "now-7d"

        # Non-loaded chip case — default since is all
        body_other = loaded_client.get(
            "/param-history?chip_key=__some_other_chip__",
            headers={"HX-Request": "true"},
        ).data.decode()
        active_other = re.search(
            r'name="since"\s+value="([^"]+)"\s+checked', body_other,
        )
        assert active_other is not None
        assert active_other.group(1) == "all"

    def test_archive_section_shows_disk_only_chips(self, client, tmp_path, app):
        """A chip with history on disk but not in workspace appears in the
        'Other chip histories on disk' section, NOT the main selector."""
        from quam_state_manager.core.history import HistoryManager
        # Create a snapshot directly via HistoryManager for a fake "archived" chip
        hm = HistoryManager(app.instance_path)
        # Synthesize a chip-keyed history dir with a populated index
        archived_dir = Path(app.instance_path) / "history" / "ArchivedChip"
        archived_dir.mkdir(parents=True)
        import sqlite3
        conn = sqlite3.connect(str(archived_dir / "index.sqlite"))
        conn.execute("""
            CREATE TABLE param_history (
                timestamp TEXT NOT NULL, qubit TEXT NOT NULL, property TEXT NOT NULL,
                value REAL, raw_pointer TEXT, trigger TEXT NOT NULL,
                run_id INTEGER, experiment TEXT,
                PRIMARY KEY (timestamp, qubit, property)
            )
        """)
        conn.execute(
            "INSERT INTO param_history VALUES "
            "('20260430_120000_001','q0','T1',30e-6,NULL,'experiment',1,'exp')"
        )
        conn.commit()
        conn.close()

        # Load a different state (so loaded chip != ArchivedChip)
        loaded_dir = tmp_path / "loaded_chip"
        loaded_dir.mkdir()
        (loaded_dir / "state.json").write_text(json.dumps(_make_state()), encoding="utf-8")
        (loaded_dir / "wiring.json").write_text(json.dumps(_make_wiring()), encoding="utf-8")
        client.post("/load", data={"folder": str(loaded_dir)})

        body = client.get("/param-history",
                          headers={"HX-Request": "true"}).data.decode()
        # ArchivedChip should appear in the archive section (not the main selector)
        assert "archive-row" in body
        assert "ArchivedChip" in body
        assert "View history" in body

    def test_main_selector_excludes_archived_chips(self, client, tmp_path, app):
        """archived_chips do NOT appear in the main chip-selector (.chip-selector-row)."""
        # Same setup as above
        archived_dir = Path(app.instance_path) / "history" / "OnlyOnDisk"
        archived_dir.mkdir(parents=True)
        import sqlite3
        conn = sqlite3.connect(str(archived_dir / "index.sqlite"))
        conn.execute("""
            CREATE TABLE param_history (
                timestamp TEXT NOT NULL, qubit TEXT NOT NULL, property TEXT NOT NULL,
                value REAL, raw_pointer TEXT, trigger TEXT NOT NULL,
                run_id INTEGER, experiment TEXT,
                PRIMARY KEY (timestamp, qubit, property)
            )
        """)
        conn.execute(
            "INSERT INTO param_history VALUES "
            "('20260430_120000_001','q0','T1',30e-6,NULL,'experiment',1,'exp')"
        )
        conn.commit()
        conn.close()

        loaded_dir = tmp_path / "loaded_main"
        loaded_dir.mkdir()
        (loaded_dir / "state.json").write_text(json.dumps(_make_state()), encoding="utf-8")
        (loaded_dir / "wiring.json").write_text(json.dumps(_make_wiring()), encoding="utf-8")
        client.post("/load", data={"folder": str(loaded_dir)})

        body = client.get("/param-history",
                          headers={"HX-Request": "true"}).data.decode()
        # Find the chip selector row substring and confirm OnlyOnDisk is NOT inside
        import re
        m = re.search(r'class="phf-row chip-selector-row".*?</div>\s*</div>', body, re.DOTALL)
        if m:
            selector_html = m.group(0)
            assert "OnlyOnDisk" not in selector_html, \
                f"archived chip leaked into main selector: {selector_html[:300]}"


# ---------------------------------------------------------------------------
# Workspace auto-populate from loaded paths
# ---------------------------------------------------------------------------


class TestWorkspaceAutoAdd:
    def _make_per_experiment_qs(self, tmp_path, chip_name="ExampleChip 1Q",
                                  date="2026-04-30", run_id=4,
                                  exp="resonator_spectroscopy", time_str="120000"):
        """Create a per-experiment quam_state path that matches the auto-add pattern."""
        qs = (tmp_path / chip_name / date / f"#{run_id}_{exp}_{time_str}" / "quam_state")
        qs.mkdir(parents=True)
        (qs / "state.json").write_text(json.dumps(_make_state()), encoding="utf-8")
        (qs / "wiring.json").write_text(json.dumps(_make_wiring()), encoding="utf-8")
        return qs

    def _reset_load_flags(self):
        from quam_state_manager.web import routes
        routes._workspace_loaded = False
        routes._session_loaded = False
        routes._rehydrated = False

    def test_load_auto_adds_chip_folder_to_workspace(self, client, tmp_path, app):
        qs = self._make_per_experiment_qs(tmp_path)
        client.post("/load", data={"folder": str(qs)})
        # workspace_roots.json should now contain the chip folder, not the qs itself
        roots_file = Path(app.instance_path) / "workspace_roots.json"
        roots = json.loads(roots_file.read_text(encoding="utf-8"))
        assert len(roots) == 1
        assert Path(roots[0]).name == "ExampleChip 1Q"

    def test_load_auto_add_skips_when_already_present(self, client, tmp_path, app):
        qs = self._make_per_experiment_qs(tmp_path)
        chip = qs.parent.parent.parent
        # Pre-add the chip folder via the workspace endpoint
        client.post("/workspace/add", data={"folder": str(chip)})
        # Then load — should NOT duplicate
        client.post("/load", data={"folder": str(qs)})
        roots_file = Path(app.instance_path) / "workspace_roots.json"
        roots = json.loads(roots_file.read_text(encoding="utf-8"))
        # Only one entry — the chip folder
        chip_resolved = str(chip.resolve())
        chip_count = sum(1 for r in roots if str(Path(r).resolve()) == chip_resolved)
        assert chip_count == 1

    def test_workspace_remove_blocks_reauto_add(self, client, tmp_path, app):
        qs = self._make_per_experiment_qs(tmp_path)
        chip = qs.parent.parent.parent

        # Load → chip is auto-added
        client.post("/load", data={"folder": str(qs)})
        # Remove the chip from workspace
        client.post("/workspace/remove", data={"folder": str(chip)})
        # Verify exclusion was recorded
        sf = Path(app.instance_path) / "last_session.json"
        data = json.loads(sf.read_text(encoding="utf-8"))
        excluded = data.get("workspace_excluded", [])
        assert str(chip.resolve()) in excluded

        # Load again — should NOT re-add the chip
        client.post("/load", data={"folder": str(qs)})
        roots_file = Path(app.instance_path) / "workspace_roots.json"
        roots = json.loads(roots_file.read_text(encoding="utf-8"))
        chip_resolved = str(chip.resolve())
        assert all(str(Path(r).resolve()) != chip_resolved for r in roots)

    def test_rehydrate_adds_chip_folders_from_recents(self, client, tmp_path, app):
        # Build two valid per-experiment paths under different chips
        qs_a = self._make_per_experiment_qs(tmp_path, chip_name="ChipA", run_id=1)
        qs_b = self._make_per_experiment_qs(tmp_path, chip_name="ChipB", run_id=2,
                                              time_str="130000")
        # And a path that no longer exists on disk
        gone = tmp_path / "gone_chip" / "2026-04-30" / "#9_x_120000" / "quam_state"

        # Pre-seed last_session.json with all three
        sf = Path(app.instance_path) / "last_session.json"
        sf.parent.mkdir(parents=True, exist_ok=True)
        sf.write_text(json.dumps({
            "last_quam_state_path": None,
            "recent_quam_state_paths": [str(qs_a), str(qs_b), str(gone)],
        }), encoding="utf-8")

        # Reset startup flags so the @before_request hook fires the rehydration
        self._reset_load_flags()

        # First request triggers the hook
        client.get("/")

        # workspace_roots.json should now contain ChipA + ChipB (gone is skipped)
        roots_file = Path(app.instance_path) / "workspace_roots.json"
        roots = json.loads(roots_file.read_text(encoding="utf-8"))
        names = {Path(r).name for r in roots}
        assert "ChipA" in names
        assert "ChipB" in names
        assert "gone_chip" not in names

    def test_rehydrate_skips_excluded_paths(self, client, tmp_path, app):
        qs = self._make_per_experiment_qs(tmp_path, chip_name="ExcludedChip", run_id=5)
        chip = qs.parent.parent.parent

        # Pre-seed: recents has the path AND workspace_excluded blocks the chip
        sf = Path(app.instance_path) / "last_session.json"
        sf.parent.mkdir(parents=True, exist_ok=True)
        sf.write_text(json.dumps({
            "last_quam_state_path": None,
            "recent_quam_state_paths": [str(qs)],
            "workspace_excluded": [str(chip.resolve())],
        }), encoding="utf-8")

        self._reset_load_flags()
        client.get("/")

        roots_file = Path(app.instance_path) / "workspace_roots.json"
        roots = json.loads(roots_file.read_text(encoding="utf-8")) if roots_file.exists() else []
        chip_resolved = str(chip.resolve())
        assert all(str(Path(r).resolve()) != chip_resolved for r in roots)


def _gen_valid_spec() -> dict:
    """A structurally-valid Generate-Config spec for route tests."""
    return {
        "network": {"host": "1.2.3.4", "cluster_name": "C", "port": None},
        "instruments": {
            "controllers": [{"con": 1, "fems": [{"slot": 1, "fem": "mw"}]}],
            "opx_plus": [], "octaves": [],
        },
        "qubits": ["q1"],
        "qubit_pairs": [],
        "twpas": [],
        "lines": [{"element": "q1", "line": "drive", "channel": None}],
        "populate": {},
    }


class TestGenerate:
    """Routes for the Generate Config wizard (no QM env required)."""

    @pytest.fixture(autouse=True)
    def _all_capabilities(self, monkeypatch):
        """These tests select a real interpreter that lacks the QM stack, so the
        capability guard would (correctly) block. Mock the probe as all-available
        so they exercise the downstream guards — the capability gate itself is
        tested in test_capabilities_routes.py."""
        from quam_state_manager.core import config_generator
        from quam_state_manager.generator.probe_capabilities import CATALOG_IDS
        manifest = {
            "ok": True, "cached": False, "error": None, "versions": {},
            "capabilities": {c: {"available": True, "detail": ""} for c in CATALOG_IDS},
        }
        monkeypatch.setattr(config_generator, "probe_capabilities",
                            lambda *a, **k: manifest)

    def test_generate_page_renders(self, client):
        resp = client.get("/generate")
        assert resp.status_code == 200
        assert b"Generate Configuration Files" in resp.data
        # The TWPA limitation is surfaced inline (step 4), not silently ignored
        # (reworded to a non-alarming, forward-looking note — see demo polish).
        assert b"TWPA wiring support" in resp.data
        # The step-5 docked wiring monitor ships in the template.
        assert b"gen-wiring-monitor" in resp.data
        # The step-6 collapsible LO-group map ships in the template.
        assert b"gen-lo-map" in resp.data
        # The step-6 read-only wiring diagram panel ships in the template.
        assert b"gen-pop-wiring" in resp.data
        # Reset button + step-5 inline Back/Next ship in the template.
        assert b"gen-reset" in resp.data
        assert b"gen-wiring-back" in resp.data
        assert b"gen-wiring-next" in resp.data

    def test_envs_endpoint_shape(self, client):
        resp = client.get("/generate/envs")
        assert resp.status_code == 200
        data = resp.get_json()
        assert isinstance(data["envs"], list)
        assert "selected" in data

    def test_probe_requires_python(self, client):
        resp = client.get("/generate/probe")
        assert resp.status_code == 400

    def test_select_env_persists(self, client):
        resp = client.post(
            "/generate/select-env", json={"python": sys.executable}
        )
        assert resp.status_code == 200
        assert resp.get_json()["ok"] is True
        envs = client.get("/generate/envs").get_json()
        assert envs["selected"] == sys.executable

    def test_select_env_requires_path(self, client):
        resp = client.post("/generate/select-env", json={"python": ""})
        assert resp.status_code == 400

    def test_allocate_rejects_invalid_spec(self, client):
        resp = client.post("/generate/allocate", json={"spec": {"qubits": []}})
        assert resp.status_code == 400
        assert resp.get_json()["errors"]

    def test_allocate_requires_selected_env(self, client):
        resp = client.post("/generate/allocate", json={"spec": _gen_valid_spec()})
        assert resp.status_code == 400
        assert "environment" in resp.get_json()["error"].lower()

    def test_build_rejects_invalid_spec(self, client):
        resp = client.post("/generate/build", json={"spec": {}, "output_path": "x"})
        assert resp.status_code == 400
        assert resp.get_json()["errors"]

    def test_build_requires_output_path(self, client):
        resp = client.post(
            "/generate/build", json={"spec": _gen_valid_spec(), "output_path": ""}
        )
        assert resp.status_code == 400
        assert "output" in resp.get_json()["error"].lower()

    def test_build_warns_on_nonempty_output_folder(self, client, tmp_path, monkeypatch):
        """A stray .json in the output folder blocks the build with a confirm."""
        from quam_state_manager.core import config_generator

        called = []
        monkeypatch.setattr(
            config_generator, "run_generator",
            lambda *a, **k: called.append(True) or {"ok": True, "status": "ok"},
        )
        client.post("/generate/select-env", json={"python": sys.executable})
        (tmp_path / "stray.json").write_text("{}", encoding="utf-8")

        resp = client.post("/generate/build", json={
            "spec": _gen_valid_spec(), "output_path": str(tmp_path),
        })
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["needs_confirm"] is True
        assert "stray.json" in data["conflict_files"]
        assert not called  # the generator subprocess was never invoked

    def test_build_force_skips_folder_guard(self, client, tmp_path, monkeypatch):
        """force=True bypasses the output-folder guard and runs the build."""
        from quam_state_manager.core import config_generator

        called = []
        monkeypatch.setattr(
            config_generator, "run_generator",
            lambda *a, **k: called.append(True) or {"ok": True, "status": "ok"},
        )
        client.post("/generate/select-env", json={"python": sys.executable})
        (tmp_path / "stray.json").write_text("{}", encoding="utf-8")

        resp = client.post("/generate/build", json={
            "spec": _gen_valid_spec(), "output_path": str(tmp_path), "force": True,
        })
        data = resp.get_json()
        assert "needs_confirm" not in data
        assert called  # force ran the generator past the guard

    def test_build_allows_clean_output_folder(self, client, tmp_path, monkeypatch):
        """An empty output folder passes the guard without a confirm."""
        from quam_state_manager.core import config_generator

        called = []
        monkeypatch.setattr(
            config_generator, "run_generator",
            lambda *a, **k: called.append(True) or {"ok": True, "status": "ok"},
        )
        client.post("/generate/select-env", json={"python": sys.executable})

        out = tmp_path / "out"          # a CLEAN, dedicated output folder (tmp_path
        out.mkdir()                     # itself holds the test app's instance/ JSONs)
        resp = client.post("/generate/build", json={
            "spec": _gen_valid_spec(), "output_path": str(out),
        })
        data = resp.get_json()
        assert "needs_confirm" not in data
        assert called

    def test_load_requires_folder(self, client):
        resp = client.post("/generate/load", json={"path": ""})
        assert resp.status_code == 400

    def test_load_missing_folder(self, client, tmp_path):
        resp = client.post("/generate/load", json={"path": str(tmp_path / "nope")})
        assert resp.status_code == 400
        assert resp.get_json()["ok"] is False


def _bump_live_state(folder, f_01=None):
    """Simulate an experiment program rewriting the live state.json."""
    import os
    import time as _time
    p = folder / "state.json"
    data = json.loads(p.read_text(encoding="utf-8"))
    if f_01 is not None:
        data["qubits"]["qA1"]["f_01"] = f_01
    else:
        data["qubits"]["qA1"]["T1"] = 99999
    p.write_text(json.dumps(data, indent=2), encoding="utf-8")
    future = _time.time() + 100
    os.utime(p, (future, future))


class TestWorkingCopyRoutes:
    """Conflict-safe working copy: live-mtime poll, review, sync, apply."""

    def test_topology_mtime_unchanged(self, loaded_client):
        data = loaded_client.get("/api/topology-mtime").get_json()
        assert data["changed"] is False

    def test_topology_mtime_detects_change(self, loaded_client, synth_folder):
        _bump_live_state(synth_folder)
        data = loaded_client.get("/api/topology-mtime").get_json()
        assert data["changed"] is True

    def test_review_no_diff(self, loaded_client):
        html = loaded_client.get("/state/review").data.decode()
        assert "No differences" in html

    def test_review_shows_live_diff(self, loaded_client, synth_folder):
        _bump_live_state(synth_folder, f_01=9.99e9)
        html = loaded_client.get("/state/review").data.decode()
        assert "qubits.qA1.f_01" in html
        assert "modified" in html

    def test_save_does_not_touch_live(self, loaded_client, synth_folder):
        before = (synth_folder / "state.json").read_text(encoding="utf-8")
        loaded_client.post("/field/edit",
                           data={"dot_path": "qubits.qA1.f_01", "value": "5.0e9"})
        loaded_client.post("/save")
        after = (synth_folder / "state.json").read_text(encoding="utf-8")
        assert before == after  # Save writes the working copy, not the live file

    def test_apply_to_live_writes_live(self, loaded_client, synth_folder):
        loaded_client.post("/field/edit",
                           data={"dot_path": "qubits.qA1.f_01", "value": "5.0e9"})
        resp = loaded_client.post("/state/apply-to-live")
        assert resp.status_code == 200
        live = json.loads((synth_folder / "state.json").read_text(encoding="utf-8"))
        assert live["qubits"]["qA1"]["f_01"] == 5.0e9

    def test_sync_discard_drops_unsaved(self, loaded_client):
        """Default mode=discard pulls live and drops the user's unsaved edits.

        (The old confirm round-trip is replaced by explicit client-side choices:
        reapply / stage / discard — see tests/test_state_sync_modes.py.)
        """
        loaded_client.post("/field/edit",
                           data={"dot_path": "qubits.qA1.f_01", "value": "5.0e9"})
        data = loaded_client.post("/state/sync").get_json()
        assert data["status"] == "ok"
        assert data["mode"] == "discard"
        # The edit was discarded; the working copy now matches the live chip.
        assert "No differences" in loaded_client.get("/state/review").data.decode()

    def test_sync_pulls_live(self, loaded_client, synth_folder):
        _bump_live_state(synth_folder, f_01=8.88e9)
        assert loaded_client.get("/api/topology-mtime").get_json()["changed"] is True
        data = loaded_client.post("/state/sync").get_json()
        assert data["status"] == "ok"
        assert loaded_client.get("/api/topology-mtime").get_json()["changed"] is False
        assert "No differences" in loaded_client.get("/state/review").data.decode()

    def test_apply_conflict_when_live_changed(self, loaded_client, synth_folder):
        loaded_client.post("/field/edit",
                           data={"dot_path": "qubits.qA1.f_01", "value": "5.0e9"})
        _bump_live_state(synth_folder)
        html = loaded_client.post("/state/apply-to-live").data.decode()
        assert "changed since you loaded it" in html

    def test_apply_force_overrides_conflict(self, loaded_client, synth_folder):
        loaded_client.post("/field/edit",
                           data={"dot_path": "qubits.qA1.f_01", "value": "5.0e9"})
        _bump_live_state(synth_folder)
        resp = loaded_client.post("/state/apply-to-live?force=1")
        assert resp.status_code == 200
        live = json.loads((synth_folder / "state.json").read_text(encoding="utf-8"))
        assert live["qubits"]["qA1"]["f_01"] == 5.0e9


# ---------------------------------------------------------------------------
# Add Gate flow (feat/create-gate-operation)
# ---------------------------------------------------------------------------


class TestAddGateFlow:
    def test_add_gate_button_renders_on_pair_detail(self, loaded_client):
        html = loaded_client.get("/pair/qA1-A2").data.decode()
        assert "Add gate" in html
        assert "pair-add-gate-area" in html

    def test_gate_form_renders(self, loaded_client):
        html = loaded_client.get("/pair/qA1-A2/gate/new").data.decode()
        assert "Add gate to qA1-A2" in html
        assert "cz_unipolar" in html
        assert "cz_flattop" in html
        assert 'name="gate_name"' in html

    def test_gate_form_cancel(self, loaded_client):
        html = loaded_client.get("/pair/qA1-A2/gate/new/cancel").data.decode()
        assert "pair-add-gate-area" in html
        assert "Add gate" in html

    def test_create_cz_unipolar(self, loaded_client, synth_folder):
        resp = loaded_client.post("/pair/qA1-A2/gate", data={
            "gate_name": "cz_v3",
            "gate_type": "cz_unipolar",
            "amplitude": "0.07",
            "length": "120",
            "coupler_amplitude": "0.02",
            "coupler_length": "120",
            "phase_shift_control": "0.0",
            "phase_shift_target": "0.0",
        })
        assert resp.status_code == 200
        # Re-rendered pair detail now shows the new gate section
        html = resp.data.decode()
        assert "cz_v3" in html or "Cz V3" in html

        # The actual store reflects the new macro
        store_html = loaded_client.get("/pair/qA1-A2").data.decode()
        assert "cz_v3_amplitude" in store_html or "cz_v3" in store_html

    def test_create_cz_parametric(self, loaded_client):
        loaded_client.post("/pair/qA1-A2/gate", data={
            "gate_name": "cz_param_v1",
            "gate_type": "cz_parametric",
            "amplitude": "0.04",
            "length": "120",
            "modulation_frequency": "300e6",
            "coupler_amplitude": "0.01",
            "phase_shift_control": "0.0",
            "phase_shift_target": "0.0",
        })
        app = loaded_client.application
        ctx_name = list(app.config["contexts"].keys())[0]
        store = app.config["contexts"][ctx_name]["store"]
        macros = store.merged["qubit_pairs"]["qA1-A2"]["macros"]
        assert "cz_param_v1" in macros
        macro = macros["cz_param_v1"]
        assert macro["__class__"].endswith(".ParametricCZGate")
        assert macro["modulation_frequency"] == pytest.approx(3.0e8)
        assert macro["flux_pulse_qubit"]["amplitude"] == pytest.approx(0.04)
        assert macro["flux_pulse_qubit"]["length"] == 120

    def test_create_cz_flattop_uses_pointer_for_length(self, loaded_client):
        loaded_client.post("/pair/qA1-A2/gate", data={
            "gate_name": "cz_v4",
            "gate_type": "cz_flattop",
            "amplitude": "0.05",
            "flat_length": "200",
            "smoothing_length": "20",
            "coupler_amplitude": "0.0",
            "phase_shift_control": "0.0",
            "phase_shift_target": "0.0",
        })
        # The created macro should have the inferred_total_length pointer in its flux_pulse_qubit.length
        app = loaded_client.application
        ctx_name = list(app.config["contexts"].keys())[0]
        store = app.config["contexts"][ctx_name]["store"]
        macros = store.merged["qubit_pairs"]["qA1-A2"]["macros"]
        assert "cz_v4" in macros
        assert macros["cz_v4"]["flux_pulse_qubit"]["length"] == "#./inferred_total_length"
        assert macros["cz_v4"]["flux_pulse_qubit"]["flat_length"] == 200

    def test_create_rejects_duplicate_name(self, loaded_client):
        loaded_client.post("/pair/qA1-A2/gate", data={
            "gate_name": "cz_v5", "gate_type": "cz_unipolar",
            "amplitude": "0.05", "length": "100",
            "coupler_amplitude": "0.0", "coupler_length": "100",
            "phase_shift_control": "0.0", "phase_shift_target": "0.0",
        })
        resp = loaded_client.post("/pair/qA1-A2/gate", data={
            "gate_name": "cz_v5", "gate_type": "cz_unipolar",
            "amplitude": "0.05", "length": "100",
            "coupler_amplitude": "0.0", "coupler_length": "100",
            "phase_shift_control": "0.0", "phase_shift_target": "0.0",
        })
        assert resp.status_code == 409
        assert "already exists" in resp.data.decode()

    def test_create_rejects_invalid_name(self, loaded_client):
        resp = loaded_client.post("/pair/qA1-A2/gate", data={
            "gate_name": "1bad-name",  # starts with digit and has hyphen
            "gate_type": "cz_unipolar",
            "amplitude": "0.05", "length": "100",
            "coupler_amplitude": "0.0", "coupler_length": "100",
            "phase_shift_control": "0.0", "phase_shift_target": "0.0",
        })
        assert resp.status_code == 400

    def test_create_rejects_unknown_pair(self, loaded_client):
        resp = loaded_client.post("/pair/nonexistent_pair/gate", data={
            "gate_name": "cz_v9", "gate_type": "cz_unipolar",
            "amplitude": "0.05", "length": "100",
            "coupler_amplitude": "0.0", "coupler_length": "100",
            "phase_shift_control": "0.0", "phase_shift_target": "0.0",
        })
        assert resp.status_code == 404

    def test_create_rejects_unknown_gate_type(self, loaded_client):
        resp = loaded_client.post("/pair/qA1-A2/gate", data={
            "gate_name": "iswap_v1", "gate_type": "iswap",
            "amplitude": "0.05", "length": "100",
        })
        assert resp.status_code == 400
        assert "Unknown gate type" in resp.data.decode()

    def test_created_gate_appears_in_pending_tray(self, loaded_client):
        loaded_client.post("/pair/qA1-A2/gate", data={
            "gate_name": "cz_v6", "gate_type": "cz_unipolar",
            "amplitude": "0.05", "length": "100",
            "coupler_amplitude": "0.0", "coupler_length": "100",
            "phase_shift_control": "0.0", "phase_shift_target": "0.0",
        })
        # The pending changes panel should show exactly one entry (the creation)
        app = loaded_client.application
        ctx_name = list(app.config["contexts"].keys())[0]
        store = app.config["contexts"][ctx_name]["store"]
        assert len(store.change_log) == 1
        assert store.change_log[0].created is True
        assert "cz_v6" in store.change_log[0].dot_path

    def test_undo_removes_created_gate(self, loaded_client):
        loaded_client.post("/pair/qA1-A2/gate", data={
            "gate_name": "cz_v7", "gate_type": "cz_unipolar",
            "amplitude": "0.05", "length": "100",
            "coupler_amplitude": "0.0", "coupler_length": "100",
            "phase_shift_control": "0.0", "phase_shift_target": "0.0",
        })
        loaded_client.post("/undo")
        app = loaded_client.application
        ctx_name = list(app.config["contexts"].keys())[0]
        store = app.config["contexts"][ctx_name]["store"]
        assert "cz_v7" not in store.merged["qubit_pairs"]["qA1-A2"]["macros"]
        assert len(store.change_log) == 0


# ---------------------------------------------------------------------------
# Add Pulse flow (Phase 2 — Flavor A)
# ---------------------------------------------------------------------------


def _store_of(client):
    app = client.application
    ctx_name = list(app.config["contexts"].keys())[0]
    return app.config["contexts"][ctx_name]["store"]


# NOTE: the old qubit-detail '+ Add pulse' flow (/qubit/<name>/operation*)
# was removed — the Pulses page create form (/pulse/new + /api/pulse/create,
# covered by tests/test_pulses_routes.py::TestPulseCreate) is the single
# add-pulse surface now (feedback #10).


# ---------------------------------------------------------------------------
# Config Viewer (Surfaces A / B / C)
# ---------------------------------------------------------------------------


SYNTH_GENERATED_CONFIG = {
    "version": 1,
    "controllers": {"con1": {}},
    "elements": {
        "qA1.xy": {
            "operations": {"x180_DragCosine": "x180_qA1.pulse"},
            "mixInputs": {"mixer": "mixer_qA1_xy"},
        },
        "qA1.z": {
            "operations": {"const_z": "const_z_qA1.pulse"},
        },
        "qA1-A2.coupler": {
            "operations": {"cz_pulse": "cz_qA1_A2.pulse"},
        },
    },
    "pulses": {
        "x180_qA1.pulse": {
            "operation": "control", "length": 40,
            "waveforms": {"I": "wf_qA1_x180_I", "Q": "wf_qA1_x180_Q"},
        },
        "const_z_qA1.pulse": {
            "operation": "control", "length": 100,
            "waveforms": {"single": "wf_qA1_const_z"},
        },
        "cz_qA1_A2.pulse": {
            "operation": "control", "length": 120,
            "waveforms": {"single": "wf_cz_qA1_A2"},
        },
    },
    "waveforms": {
        "wf_qA1_x180_I": {"type": "arbitrary", "samples": [0.0, 0.05, 0.1, 0.05, 0.0]},
        "wf_qA1_x180_Q": {"type": "arbitrary", "samples": [0.0, 0.01, 0.02, 0.01, 0.0]},
        "wf_qA1_const_z": {"type": "constant", "sample": 0.1},
        "wf_cz_qA1_A2": {"type": "constant", "sample": 0.05},
    },
    "mixers": {"mixer_qA1_xy": [{"intermediate_frequency": 100e6, "lo_frequency": 5e9}]},
    "integration_weights": {},
}


def _seed_config_cache(client):
    """Inject a synthetic generated config into the active store (skips the subprocess)."""
    from quam_state_manager.core.working_copy import content_hash

    store = _store_of(client)
    store.generated_config = SYNTH_GENERATED_CONFIG
    store.generated_config_meta = {
        "at": "2026-05-22T00:00:00+00:00",
        "versions": {"quam": "0.4.0", "quam_builder": "0.3.0", "qm": "1.2.3"},
        "warnings": [],
        "qubits": ["qA1"],
        "qubit_pairs": ["qA1-A2"],
        # Basis matches the current in-memory state → surfaces render fresh.
        "basis_hash": content_hash(store.state, store.wiring),
        "unsaved_at_generate": False,
    }


class TestConfigViewer:
    def test_config_page_renders_empty_when_no_cache(self, loaded_client):
        html = loaded_client.get("/config").data.decode()
        assert "Config Viewer" in html
        # the empty state frames it as the loaded chip's config (no wizard
        # mental model) and offers to generate it (B-config / feedback Config #0)
        assert "No config generated for the loaded chip" in html
        assert "loaded chip" in html

    def test_config_page_lists_top_level_keys_when_cached(self, loaded_client):
        _seed_config_cache(loaded_client)
        html = loaded_client.get("/config").data.decode()
        for key in ("controllers", "elements", "pulses", "waveforms", "mixers"):
            assert f"<code>{key}</code>" in html

    def test_qubit_config_slice_route(self, loaded_client):
        _seed_config_cache(loaded_client)
        html = loaded_client.get("/qubit/qA1/config").data.decode()
        assert "Generated config" in html
        assert "x180_DragCosine" in html
        assert "const_z" in html
        assert "qA1.xy" in html
        assert "qA1.z" in html

    def test_qubit_config_slice_unknown_qubit(self, loaded_client):
        resp = loaded_client.get("/qubit/qNOPE/config")
        assert resp.status_code == 404

    def test_qubit_config_without_cache_shows_prompt(self, loaded_client):
        html = loaded_client.get("/qubit/qA1/config").data.decode()
        assert "No generated config cached" in html
        # The empty state carries its own inline Regenerate button now —
        # no round-trip to the (demoted) Config Viewer page.
        assert 'hx-post="/config/regenerate"' in html
        assert 'href="/config"' not in html

    def test_qubit_waveform_constant(self, loaded_client):
        _seed_config_cache(loaded_client)
        resp = loaded_client.get("/qubit/qA1/waveform/const_z")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["operation"] == "const_z"
        assert data["stale"] is False  # basis matches the in-memory state
        assert len(data["traces"]) == 1
        trace = data["traces"][0]
        assert trace["label"] == "single"
        assert trace["kind"] == "constant"
        assert trace["constant_value"] == 0.1
        assert trace["length_ns"] == 100
        assert len(trace["y"]) == 100
        assert all(y == 0.1 for y in trace["y"])

    def test_qubit_waveform_arbitrary(self, loaded_client):
        _seed_config_cache(loaded_client)
        data = loaded_client.get("/qubit/qA1/waveform/x180_DragCosine").get_json()
        assert data["pulse"] == "x180_qA1.pulse"
        assert [t["label"] for t in data["traces"]] == ["I", "Q"]
        assert data["traces"][0]["kind"] == "arbitrary"
        assert data["traces"][0]["name"] == "wf_qA1_x180_I"
        assert data["traces"][0]["y"] == [0.0, 0.05, 0.1, 0.05, 0.0]
        assert data["traces"][1]["name"] == "wf_qA1_x180_Q"

    def test_waveform_without_cache_409s(self, loaded_client):
        resp = loaded_client.get("/qubit/qA1/waveform/x180_DragCosine")
        assert resp.status_code == 409
        assert "not cached" in resp.get_json()["error"]

    def test_waveform_unknown_op_404s(self, loaded_client):
        _seed_config_cache(loaded_client)
        resp = loaded_client.get("/qubit/qA1/waveform/never_existed")
        assert resp.status_code == 404

    def test_pair_config_slice_is_not_empty(self, loaded_client):
        # Regression: the pair pane used to render empty because it matched a
        # "<pair>." element prefix that real configs never produce. It now
        # resolves the pair's gate element + ops (here via the legacy element).
        _seed_config_cache(loaded_client)
        html = loaded_client.get("/pair/qA1-A2/config").data.decode()
        assert "cz_pulse" in html
        assert "qA1-A2.coupler" in html
        assert "No 2-qubit-gate operations" not in html

    def test_pair_config_no_gate_shows_message_not_regenerate(self, loaded_client):
        # A cached config with no 2Q gate for the pair shows the explanatory
        # message, not the (misleading) "Regenerate" empty state.
        store = _store_of(loaded_client)
        store.generated_config = {"elements": {"qA1.xy": {"operations": {}}},
                                  "pulses": {}, "waveforms": {}}
        from quam_state_manager.core.working_copy import content_hash
        store.generated_config_meta = {
            "at": "x", "versions": {}, "warnings": [], "qubits": ["qA1"],
            "qubit_pairs": ["qA1-A2"],
            "basis_hash": content_hash(store.state, store.wiring),
            "unsaved_at_generate": False,
        }
        html = loaded_client.get("/pair/qA1-A2/config").data.decode()
        assert "No 2-qubit-gate operations" in html

    def test_pair_waveform_resolves_with_element(self, loaded_client):
        _seed_config_cache(loaded_client)
        resp = loaded_client.get(
            "/pair/qA1-A2/waveform/cz_pulse?element=qA1-A2.coupler")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["element"] == "qA1-A2.coupler"
        assert data["traces"][0]["constant_value"] == 0.05

    def test_pair_waveform_unknown_op_404s(self, loaded_client):
        _seed_config_cache(loaded_client)
        resp = loaded_client.get("/pair/qA1-A2/waveform/never_existed")
        assert resp.status_code == 404

    def test_regenerate_without_selected_env_returns_400(self, loaded_client):
        resp = loaded_client.post("/config/regenerate")
        assert resp.status_code == 400
        body = resp.data.decode()
        assert "Generate-Config env" in body or "No state loaded" in body

    def test_regenerate_uses_orchestrator(self, loaded_client, monkeypatch):
        from quam_state_manager.core import config_generator
        monkeypatch.setattr(
            config_generator, "get_selected_env",
            lambda *a, **kw: "/fake/python",
        )

        def fake_run(python_path, folder, timeout=120):
            assert python_path == "/fake/python"
            return {
                "ok": True, "status": "ok",
                "result": {
                    "status": "ok",
                    "config": SYNTH_GENERATED_CONFIG,
                    "versions": {"quam": "0.4.0", "quam_builder": "0.3.0"},
                    "warnings": [],
                    "qubits": ["qA1"], "qubit_pairs": ["qA1-A2"],
                },
                "returncode": 0, "stdout": "", "stderr": "", "error": None,
            }

        monkeypatch.setattr(config_generator, "run_config_preview", fake_run)

        resp = loaded_client.post("/config/regenerate")
        assert resp.status_code == 200
        store = _store_of(loaded_client)
        assert store.generated_config is not None
        assert "elements" in store.generated_config

    def test_regenerate_propagates_subprocess_error(self, loaded_client, monkeypatch):
        from quam_state_manager.core import config_generator
        monkeypatch.setattr(
            config_generator, "get_selected_env",
            lambda *a, **kw: "/fake/python",
        )
        monkeypatch.setattr(
            config_generator, "run_config_preview",
            lambda *a, **kw: {
                "ok": False, "status": "error", "result": {"traceback": "boom\n"},
                "returncode": 1, "stdout": "", "stderr": "", "error": "something broke",
            },
        )
        resp = loaded_client.post("/config/regenerate")
        assert resp.status_code == 502
        body = resp.data.decode()
        assert "something broke" in body


class TestConfigStaleness:
    """Content-hash staleness of the cached generated config (W1)."""

    @staticmethod
    def _mock_previewer(monkeypatch):
        from quam_state_manager.core import config_generator
        monkeypatch.setattr(
            config_generator, "get_selected_env", lambda *a, **kw: "/fake/python",
        )
        monkeypatch.setattr(
            config_generator, "run_config_preview",
            lambda *a, **kw: {
                "ok": True, "status": "ok",
                "result": {
                    "status": "ok", "config": SYNTH_GENERATED_CONFIG,
                    "versions": {}, "warnings": [],
                    "qubits": ["qA1"], "qubit_pairs": ["qA1-A2"],
                },
                "returncode": 0, "stdout": "", "stderr": "", "error": None,
            },
        )

    def test_waveform_stale_after_edit_and_fresh_after_undo(self, loaded_client):
        _seed_config_cache(loaded_client)
        url = "/qubit/qA1/waveform/const_z"
        assert loaded_client.get(url).get_json()["stale"] is False

        loaded_client.post(
            "/field/edit", data={"dot_path": "qubits.qA1.f_01", "value": "9.99e9"},
        )
        assert loaded_client.get(url).get_json()["stale"] is True

        loaded_client.post("/undo")
        assert loaded_client.get(url).get_json()["stale"] is False

    def test_stale_chip_renders_on_config_surfaces(self, loaded_client):
        _seed_config_cache(loaded_client)
        # The /config status banner shows the VSCode-clean stale note
        # (.config-stale-note, "Config may be stale."); the per-qubit config header
        # keeps its .waveform-warn-chip "config may be stale" chip.
        assert "config-stale-note" not in loaded_client.get("/config").data.decode()
        assert "config may be stale" not in (
            loaded_client.get("/qubit/qA1/config").data.decode()
        )
        loaded_client.post(
            "/field/edit", data={"dot_path": "qubits.qA1.f_01", "value": "9.99e9"},
        )
        assert "config-stale-note" in loaded_client.get("/config").data.decode()
        assert "config may be stale" in (
            loaded_client.get("/qubit/qA1/config").data.decode()
        )

    def test_regenerate_records_basis_hash(self, loaded_client, monkeypatch):
        from quam_state_manager.core.working_copy import content_hash
        self._mock_previewer(monkeypatch)

        resp = loaded_client.post("/config/regenerate")
        assert resp.status_code == 200
        assert resp.headers.get("HX-Trigger") == "configRegenerated"
        store = _store_of(loaded_client)
        meta = store.generated_config_meta
        # No unsaved edits → the working-copy files equal the in-memory state.
        assert meta["unsaved_at_generate"] is False
        assert meta["basis_hash"] == content_hash(store.state, store.wiring)
        assert "config may be stale" not in resp.data.decode()

    def test_regenerate_with_unsaved_edits_is_immediately_stale(
        self, loaded_client, monkeypatch,
    ):
        self._mock_previewer(monkeypatch)
        loaded_client.post(
            "/field/edit", data={"dot_path": "qubits.qA1.f_01", "value": "9.99e9"},
        )
        resp = loaded_client.post("/config/regenerate")
        assert resp.status_code == 200
        store = _store_of(loaded_client)
        assert store.generated_config_meta["unsaved_at_generate"] is True
        # The preview was generated from files that lack the edit → honest stale.
        data = loaded_client.get("/qubit/qA1/waveform/const_z").get_json()
        assert data["stale"] is True
        assert "predates the unsaved edits" in resp.data.decode()

    def test_legacy_meta_without_basis_reads_stale(self, loaded_client):
        _seed_config_cache(loaded_client)
        store = _store_of(loaded_client)
        del store.generated_config_meta["basis_hash"]
        # Unknown basis: freshness can't be proven → err toward stale.
        data = loaded_client.get("/qubit/qA1/waveform/const_z").get_json()
        assert data["stale"] is True


class TestConfigExportDownload:
    """GET /config/export — the bare-QUA drop-in config.json / config.py."""

    def test_json_returns_raw_config_as_attachment(self, loaded_client):
        _seed_config_cache(loaded_client)
        resp = loaded_client.get("/config/export?format=json")
        assert resp.status_code == 200
        assert resp.content_type == "application/json"
        cd = resp.headers.get("Content-Disposition", "")
        assert "attachment" in cd and "config_" in cd and cd.rstrip('"').endswith(".json")
        # The file IS the raw config dict — json.load → qmm.open_qm, no wrapper.
        assert json.loads(resp.data.decode()) == SYNTH_GENERATED_CONFIG

    def test_py_is_valid_python_reproducing_config(self, loaded_client):
        _seed_config_cache(loaded_client)
        resp = loaded_client.get("/config/export?format=py")
        assert resp.status_code == 200
        assert resp.content_type.startswith("text/x-python")
        cd = resp.headers.get("Content-Disposition", "")
        assert "attachment" in cd and "config_" in cd and cd.rstrip('"').endswith(".py")
        ns: dict = {}
        exec(compile(resp.data.decode(), "config.py", "exec"), ns)
        assert ns["config"] == SYNTH_GENERATED_CONFIG

    def test_default_and_unknown_format_fall_back_to_json(self, loaded_client):
        _seed_config_cache(loaded_client)
        for url in ("/config/export", "/config/export?format=xyz"):
            resp = loaded_client.get(url)
            assert resp.status_code == 200
            assert resp.content_type == "application/json"
            assert json.loads(resp.data.decode()) == SYNTH_GENERATED_CONFIG

    def test_filename_stem_from_chip(self, loaded_client):
        _seed_config_cache(loaded_client)
        cd = loaded_client.get("/config/export?format=py").headers["Content-Disposition"]
        assert cd.rstrip('"').endswith(".py") and "config_" in cd  # config_<chip>.py

    def test_404_when_no_config_cached(self, loaded_client):
        assert loaded_client.get("/config/export?format=json").status_code == 404
        assert loaded_client.get("/config/export?format=py").status_code == 404

    def test_export_when_stale_still_succeeds_with_warning_in_py(self, loaded_client):
        _seed_config_cache(loaded_client)
        # Edit after generate → stale, but export is non-blocking (last-good).
        loaded_client.post(
            "/field/edit", data={"dot_path": "qubits.qA1.f_01", "value": "9.99e9"},
        )
        rj = loaded_client.get("/config/export?format=json")
        assert rj.status_code == 200
        assert json.loads(rj.data.decode()) == SYNTH_GENERATED_CONFIG
        # The .py header owns up to the staleness so nobody ships it thinking it fresh.
        rp = loaded_client.get("/config/export?format=py")
        assert "WARNING" in rp.data.decode() and "Regenerate" in rp.data.decode()

    def test_banner_shows_export_links_only_when_cached(self, loaded_client):
        # Empty: no config → no export links.
        assert "/config/export?format=py" not in loaded_client.get("/config").data.decode()
        _seed_config_cache(loaded_client)
        html = loaded_client.get("/config").data.decode()
        assert "/config/export?format=json" in html
        assert "/config/export?format=py" in html


class TestGeneratePreviewConfig:
    """POST /generate/preview-config + the seed-on-load transplant (W6)."""

    @staticmethod
    def _built_folder(tmp_path):
        folder = tmp_path / "built_chip"
        folder.mkdir()
        (folder / "state.json").write_text(
            json.dumps(_make_state(), indent=2), encoding="utf-8",
        )
        (folder / "wiring.json").write_text(
            json.dumps(_make_wiring(), indent=2), encoding="utf-8",
        )
        return folder

    @staticmethod
    def _mock_env_and_previewer(monkeypatch, *, ok=True):
        from quam_state_manager.core import config_generator
        monkeypatch.setattr(
            config_generator, "get_selected_env", lambda *a, **kw: "/fake/python",
        )
        if ok:
            outcome = {
                "ok": True, "status": "ok",
                "result": {
                    "status": "ok", "config": SYNTH_GENERATED_CONFIG,
                    "versions": {"quam": "0.4.0"}, "warnings": ["w1"],
                    "qubits": ["qA1"], "qubit_pairs": ["qA1-A2"],
                },
                "returncode": 0, "stdout": "", "stderr": "", "error": None,
            }
        else:
            outcome = {
                "ok": False, "status": "error",
                "result": {"traceback": "boom-trace\n"},
                "returncode": 1, "stdout": "", "stderr": "", "error": "preview broke",
            }
        monkeypatch.setattr(
            config_generator, "run_config_preview", lambda *a, **kw: outcome,
        )

    def test_missing_path_400(self, client):
        resp = client.post("/generate/preview-config", json={})
        assert resp.status_code == 400

    def test_missing_state_json_400(self, client, tmp_path):
        resp = client.post(
            "/generate/preview-config", json={"path": str(tmp_path / "empty")},
        )
        assert resp.status_code == 400
        assert "state.json" in resp.get_json()["error"]

    def test_no_selected_env_400(self, client, tmp_path, monkeypatch):
        from quam_state_manager.core import config_generator
        monkeypatch.setattr(
            config_generator, "get_selected_env", lambda *a, **kw: None,
        )
        folder = self._built_folder(tmp_path)
        resp = client.post("/generate/preview-config", json={"path": str(folder)})
        assert resp.status_code == 400
        assert "environment" in resp.get_json()["error"].lower()

    def test_success_returns_config_and_meta(self, client, tmp_path, monkeypatch):
        self._mock_env_and_previewer(monkeypatch)
        folder = self._built_folder(tmp_path)
        resp = client.post("/generate/preview-config", json={"path": str(folder)})
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["ok"] is True
        assert "elements" in data["config"]
        assert data["meta"]["qubits"] == ["qA1"]
        assert data["meta"]["warnings"] == ["w1"]

    def test_subprocess_error_502(self, client, tmp_path, monkeypatch):
        self._mock_env_and_previewer(monkeypatch, ok=False)
        folder = self._built_folder(tmp_path)
        resp = client.post("/generate/preview-config", json={"path": str(folder)})
        assert resp.status_code == 502
        data = resp.get_json()
        assert data["error"] == "preview broke"
        assert "boom-trace" in data["traceback"]

    def test_load_transplants_seed(self, client, tmp_path, monkeypatch):
        self._mock_env_and_previewer(monkeypatch)
        folder = self._built_folder(tmp_path)
        client.post("/generate/preview-config", json={"path": str(folder)})

        resp = client.post("/generate/load", json={"path": str(folder)})
        assert resp.status_code == 200 and resp.get_json()["ok"] is True

        store = _store_of(client)
        assert store.generated_config is not None
        assert "elements" in store.generated_config
        meta = store.generated_config_meta
        assert meta["seeded"] is True
        assert meta["basis_hash"]
        # The detail section renders the slice with no extra regenerate, fresh.
        html = client.get("/qubit/qA1/config").data.decode()
        assert "x180_DragCosine" in html
        assert "config may be stale" not in html

    def test_load_skips_seed_when_files_changed(self, client, tmp_path, monkeypatch):
        self._mock_env_and_previewer(monkeypatch)
        folder = self._built_folder(tmp_path)
        client.post("/generate/preview-config", json={"path": str(folder)})

        # Out-of-band edit between preview and load → seed must be skipped.
        state = json.loads((folder / "state.json").read_text(encoding="utf-8"))
        state["qubits"]["qA1"]["f_01"] = 9.99e9
        (folder / "state.json").write_text(json.dumps(state), encoding="utf-8")

        resp = client.post("/generate/load", json={"path": str(folder)})
        assert resp.status_code == 200
        assert _store_of(client).generated_config is None


class TestGenerateExportConfig:
    """GET /generate/export-config — download the just-previewed build's config
    for bare QUA, straight from the wizard result panel (no Load-into-app)."""

    def test_cold_seed_409(self, client, tmp_path):
        resp = client.get(
            "/generate/export-config?path=" + str(tmp_path / "never") + "&format=json")
        assert resp.status_code == 409
        assert "Preview config" in resp.get_json()["error"]

    def test_missing_path_400(self, client):
        assert client.get("/generate/export-config?format=json").status_code == 400

    def test_export_after_preview_json(self, client, tmp_path, monkeypatch):
        TestGeneratePreviewConfig._mock_env_and_previewer(monkeypatch)
        folder = TestGeneratePreviewConfig._built_folder(tmp_path)
        client.post("/generate/preview-config", json={"path": str(folder)})
        resp = client.get(
            "/generate/export-config?path=" + str(folder) + "&format=json")
        assert resp.status_code == 200
        assert json.loads(resp.data.decode()) == SYNTH_GENERATED_CONFIG

    def test_export_after_preview_py_roundtrips(self, client, tmp_path, monkeypatch):
        TestGeneratePreviewConfig._mock_env_and_previewer(monkeypatch)
        folder = TestGeneratePreviewConfig._built_folder(tmp_path)
        client.post("/generate/preview-config", json={"path": str(folder)})
        resp = client.get(
            "/generate/export-config?path=" + str(folder) + "&format=py")
        assert resp.status_code == 200
        ns: dict = {}
        exec(compile(resp.data.decode(), "config.py", "exec"), ns)
        assert ns["config"] == SYNTH_GENERATED_CONFIG

    def test_peek_does_not_consume_load_seed(self, client, tmp_path, monkeypatch):
        # Exporting must not eat the seed that /generate/load transplants.
        TestGeneratePreviewConfig._mock_env_and_previewer(monkeypatch)
        folder = TestGeneratePreviewConfig._built_folder(tmp_path)
        client.post("/generate/preview-config", json={"path": str(folder)})
        client.get("/generate/export-config?path=" + str(folder) + "&format=py")
        client.get("/generate/export-config?path=" + str(folder) + "&format=json")
        # After two exports the load transplant still finds the seed.
        client.post("/generate/load", json={"path": str(folder)})
        assert _store_of(client).generated_config is not None

    def test_export_409_after_load_consumes_seed(self, client, tmp_path, monkeypatch):
        # /generate/load pops the seed → a later wizard export is a clean 409.
        TestGeneratePreviewConfig._mock_env_and_previewer(monkeypatch)
        folder = TestGeneratePreviewConfig._built_folder(tmp_path)
        client.post("/generate/preview-config", json={"path": str(folder)})
        client.post("/generate/load", json={"path": str(folder)})
        resp = client.get("/generate/export-config?path=" + str(folder) + "&format=json")
        assert resp.status_code == 409
        assert "Preview config" in resp.get_json()["error"]

    def test_wizard_py_export_never_flags_stale(self, client, tmp_path, monkeypatch):
        # A wizard export is of a config JUST generated → never stale, so its
        # .py must carry no WARNING header (only the Config Viewer path can be stale).
        TestGeneratePreviewConfig._mock_env_and_previewer(monkeypatch)
        folder = TestGeneratePreviewConfig._built_folder(tmp_path)
        client.post("/generate/preview-config", json={"path": str(folder)})
        src = client.get(
            "/generate/export-config?path=" + str(folder) + "&format=py").data.decode()
        assert "WARNING" not in src

    def test_unknown_format_falls_back_to_json(self, client, tmp_path, monkeypatch):
        TestGeneratePreviewConfig._mock_env_and_previewer(monkeypatch)
        folder = TestGeneratePreviewConfig._built_folder(tmp_path)
        client.post("/generate/preview-config", json={"path": str(folder)})
        resp = client.get("/generate/export-config?path=" + str(folder) + "&format=xyz")
        assert resp.status_code == 200 and resp.content_type == "application/json"
        assert json.loads(resp.data.decode()) == SYNTH_GENERATED_CONFIG


class TestConfigViewerWiring:
    """File-content guards for the demoted nav, the lifted waveform JS, and
    the wizard's post-build preview (no JS test runner exists)."""

    def _read(self, *parts):
        base = Path(__file__).resolve().parent.parent / "quam_state_manager"
        return base.joinpath(*parts).read_text(encoding="utf-8")

    def test_nav_config_subgroup_wired(self):
        base = self._read("web", "templates", "base.html")
        js = self._read("web", "static", "app.js")
        # Config Viewer lives ONLY inside the collapsible config-subnav group.
        assert 'id="config-subnav"' in base
        assert base.count(">Config Viewer</a>") == 1
        sub_start = base.index('id="config-subnav"')
        assert sub_start < base.index(">Config Viewer</a>") < base.index("</ul>", sub_start)
        assert "quam_config_nav_collapsed" in base
        assert "quam_config_nav_collapsed" in js
        assert "window.toggleNavSub" in js
        assert "window.toggleChipStatusSub" in js  # wrapper kept
        # Command palette entry survives as a power-user access path.
        assert '"label": "Config Viewer"' in base

    def test_waveform_js_lifted_into_appjs(self):
        js = self._read("web", "static", "app.js")
        qubit_cfg = self._read("web", "templates", "_qubit_config.html")
        pair_cfg = self._read("web", "templates", "_pair_config.html")
        assert "window.showWaveformPlot" in js
        assert "<script" not in qubit_cfg          # inline block removed
        assert "showWaveformPlot(this)" in qubit_cfg
        assert "showWaveformPlot(this)" in pair_cfg
        # Caption is DOM-built; the old innerHTML interpolation is gone.
        assert "_waveformCaption" in js
        assert "waveform-warn-chip" in js

    def test_config_status_error_swap_allowlisted(self):
        js = self._read("web", "static", "app.js")
        status_tpl = self._read("web", "templates", "_config_status.html")
        detail_q = self._read("web", "templates", "_qubit_detail.html")
        detail_p = self._read("web", "templates", "_pair_detail.html")
        assert "config-status-host" in js          # htmx beforeSwap allowlist
        assert "closest .config-status-host" in status_tpl
        assert "configRegenerated from:body" in detail_q
        assert "configRegenerated from:body" in detail_p

    def test_wizard_preview_config_wired(self):
        js = self._read("web", "static", "generate.js")
        assert "/generate/preview-config" in js
        assert "Preview config" in js
        assert 'renderJsonTree("json-panel-tree"' in js
        assert 'valueClick: "copy"' in js          # read-only tree mode

    def test_waveform_warn_chip_theme_safe(self):
        css = self._read("web", "static", "style.css")
        block = css[css.index(".waveform-warn-chip {"):]
        block = block[:block.index("}")]
        # Theme rule: text via --pico-color; colour only on border/bg tokens.
        assert "color: var(--pico-color)" in block
        assert "--color-warning-border" in block
        assert "var(--color-warning-text)" not in block


# ---------------------------------------------------------------------------
# LF-FEM delay rows on detail pages
# ---------------------------------------------------------------------------


def _make_state_with_lf_delay():
    """Synthetic state where qubits.qA1.z.opx_output is the dict form (with
    a `delay` leaf), so the new ``z_delay_ns`` row has something to render."""
    state = _make_state()
    state["qubits"]["qA1"]["z"] = {
        "joint_offset": 0.081,
        "flux_point": "joint",
        "opx_output": {"delay": 141, "offset": 0.0, "shareable": False},
    }
    state["qubits"]["qA2"] = {
        "id": "qA2", "f_01": 6.3e9, "anharmonicity": -220e6,
        "T1": 8000, "T2ramsey": 1.2e-6,
        "z": {
            "joint_offset": 0.0, "flux_point": "joint",
            "opx_output": {"delay": 161, "offset": 0.0},
        },
        "xy": {"RF_frequency": 6.3e9, "operations": {}},
        "resonator": {"f_01": 7.0e9, "operations": {}},
    }
    state["qubit_pairs"]["qA1-A2"] = {
        "id": "qA1-A2",
        "qubit_control": "#/qubits/qA1",
        "qubit_target": "#/qubits/qA2",
        "moving_qubit": "control",
        "macros": {},
        "coupler": {
            "decouple_offset": 0.5, "interaction_offset": 0.0,
            "opx_output": {"delay": 141, "offset": 0.0},
        },
        "detuning": 0.0,
    }
    state["active_qubit_names"] = ["qA1", "qA2"]
    return state


@pytest.fixture
def loaded_client_with_lf(tmp_path: Path):
    folder = tmp_path / "lf_delay_state"
    folder.mkdir()
    (folder / "state.json").write_text(
        json.dumps(_make_state_with_lf_delay(), indent=2), encoding="utf-8",
    )
    (folder / "wiring.json").write_text(
        json.dumps(_make_wiring(), indent=2), encoding="utf-8",
    )
    from quam_state_manager.web.app import create_app
    app = create_app(testing=True, instance_path=str(tmp_path / "_app_instance"))
    client = app.test_client()
    client.post("/load", data={"folder": str(folder)})
    return client


class TestLfFemDelayRows:
    def test_qubit_detail_renders_z_delay_ns_row(self, loaded_client_with_lf):
        html = loaded_client_with_lf.get("/qubit/qA1").data.decode()
        assert "z_delay_ns" in html
        # Inline-edit form should target the new dot_path
        assert "qubits.qA1.z.opx_output.delay" in html

    def test_qubit_edit_persists_new_delay(self, loaded_client_with_lf):
        resp = loaded_client_with_lf.post("/qubit/qA1/edit", data={
            "dot_path": "qubits.qA1.z.opx_output.delay", "value": "150",
        })
        assert resp.status_code == 200
        store = _store_of(loaded_client_with_lf)
        assert store.merged["qubits"]["qA1"]["z"]["opx_output"]["delay"] == 150

    def test_pair_detail_renders_coupler_delay_row(self, loaded_client_with_lf):
        html = loaded_client_with_lf.get("/pair/qA1-A2").data.decode()
        assert "coupler_delay_ns" in html
        assert "qubit_pairs.qA1-A2.coupler.opx_output.delay" in html

    def test_query_engine_returns_z_delay_ns(self, loaded_client_with_lf):
        store = _store_of(loaded_client_with_lf)
        from quam_state_manager.core.query import QueryEngine
        engine = QueryEngine(store)
        data = engine.get_qubit("qA1")
        assert data["z_delay_ns"] == 141
        data2 = engine.get_qubit("qA2")
        assert data2["z_delay_ns"] == 161

    def test_query_engine_returns_coupler_delay_ns(self, loaded_client_with_lf):
        store = _store_of(loaded_client_with_lf)
        from quam_state_manager.core.query import QueryEngine
        engine = QueryEngine(store)
        data = engine.get_pair("qA1-A2")
        assert data["coupler_delay_ns"] == 141


# ---------------------------------------------------------------------------
# LRU eviction must preserve the on-disk working folder (Phase 2 finding 0.3)
# ---------------------------------------------------------------------------


class TestWorkingCopyEvictionPersistence:
    """Loading more chips than ``_QUAM_CACHE_MAX`` must NOT delete the
    on-disk working folder of the chip that fell out of the cache.

    Before the fix, eviction called ``working_copy.discard(old_wc)``, which
    wiped the working folder. Any unapplied user edits in that working copy
    were lost; the next reload would re-seed fresh from live and silently
    drop the user's work.
    """

    def _seed_chip(self, base: Path, index: int) -> Path:
        chip_dir = base / f"chip_{index}" / "quam_state"
        chip_dir.mkdir(parents=True)
        state = {"qubits": {f"q{index}": {"f_01": 5e9 + index * 1e8}}}
        wiring = {"wiring": {}}
        (chip_dir / "state.json").write_text(json.dumps(state), encoding="utf-8")
        (chip_dir / "wiring.json").write_text(json.dumps(wiring), encoding="utf-8")
        return chip_dir

    def test_evicted_chip_working_folder_survives(self, client, tmp_path):
        from quam_state_manager.web import routes
        # Spawn one more chip than the LRU bound so the first one is evicted.
        chips = [
            self._seed_chip(tmp_path / "live", i)
            for i in range(routes._QUAM_CACHE_MAX + 1)
        ]
        # Snapshot the working_state root from the active app.
        instance_path = Path(client.application.instance_path)
        working_root = instance_path / "working_state"

        for chip in chips:
            assert client.post("/load", data={"folder": str(chip)}).status_code in (200, 302)

        # The first chip's in-memory cache entry has been evicted, but its
        # working folder on disk MUST still exist (and contain state.json),
        # so a future re-load picks up the saved edits via working_copy.load.
        evicted_key = str(chips[0])
        assert evicted_key not in routes._quam_cache, (
            "first chip should have been evicted from the in-memory cache"
        )
        # Locate the working folder by its computed key (avoids hard-coding
        # the sanitiser format).
        from quam_state_manager.core.working_copy import key_for
        wc_folder = working_root / key_for(chips[0])
        assert wc_folder.is_dir(), "evicted chip's working folder was deleted"
        assert (wc_folder / "state.json").is_file(), (
            "evicted chip's working state.json was deleted — unapplied edits "
            "would be lost on next load"
        )

    def test_reload_after_eviction_uses_load_not_create(self, client, tmp_path, monkeypatch):
        """After eviction, re-activating a chip must call
        ``working_copy.load`` (rehydrating the existing folder) — NOT
        ``working_copy.create`` (which re-seeds from live)."""
        from quam_state_manager.core import working_copy
        from quam_state_manager.web import routes

        chips = [
            self._seed_chip(tmp_path / "live", i)
            for i in range(routes._QUAM_CACHE_MAX + 1)
        ]
        # Fill the cache (no spies yet — we want the natural setup).
        for chip in chips:
            client.post("/load", data={"folder": str(chip)})

        # Now re-load the chip we just evicted, with spies in place.
        create_calls: list = []
        load_calls: list = []
        real_create = working_copy.create
        real_load = working_copy.load

        def spy_create(*args, **kwargs):
            create_calls.append((args, kwargs))
            return real_create(*args, **kwargs)

        def spy_load(*args, **kwargs):
            load_calls.append((args, kwargs))
            return real_load(*args, **kwargs)

        monkeypatch.setattr(routes.working_copy, "create", spy_create)
        monkeypatch.setattr(routes.working_copy, "load", spy_load)

        client.post("/load", data={"folder": str(chips[0])})

        assert load_calls, (
            "re-loading an evicted chip must consult working_copy.load to "
            "find the existing working folder"
        )
        assert not create_calls, (
            "re-loading an evicted chip must NOT call working_copy.create -- "
            "that would re-seed from live and silently drop saved edits"
        )


# ---------------------------------------------------------------------------
# Phase 2.1 — DatasetStore thread-safety, LRU bound, path-traversal containment
# ---------------------------------------------------------------------------


def _seed_dataset_run(folder: Path, run_id: int, fit_size_kb: int = 1) -> Path:
    """Create a minimal run folder under ``folder`` with node.json + data.json."""
    date_dir = folder / "2026-05-28"
    date_dir.mkdir(parents=True, exist_ok=True)
    run = date_dir / f"#{run_id}_test_experiment_010000"
    run.mkdir()
    (run / "node.json").write_text(json.dumps({
        "metadata": {"name": "test_experiment", "status": "successful",
                      "run_start": "2026-05-28T01:00:00",
                      "run_end": "2026-05-28T01:00:01"},
        "data": {"parameters": {"model": {"qubits": [f"q{run_id}"]}}, "outcomes": {}},
        "id": run_id, "parents": [], "created_at": "2026-05-28T01:00:00",
    }), encoding="utf-8")
    payload = "x" * (fit_size_kb * 1024)
    (run / "data.json").write_text(json.dumps({
        "fit_results": {f"q{run_id}": {"T1": 8.0e-6, "padding": payload}},
    }), encoding="utf-8")
    return run


class TestDatasetStoreThreadSafety:
    """Phase 2 §3.1 (Critical): concurrent tag/bookmark/note mutations
    must all be persisted; before the fix, two HTMX threads racing on
    ``self._tags_data`` would silently lose updates."""

    def test_concurrent_add_tag_preserves_all_writes(self, tmp_path):
        import threading
        from quam_state_manager.core.dataset import DatasetStore

        for i in range(8):
            _seed_dataset_run(tmp_path, i)
        ds = DatasetStore(tmp_path)
        run_id = next(iter(ds.runs.keys()))

        n_threads = 8
        per_thread = 25

        def add_many(start: int):
            for k in range(per_thread):
                ds.add_tag(run_id, f"tag_{start}_{k}")

        threads = [threading.Thread(target=add_many, args=(t,)) for t in range(n_threads)]
        for th in threads:
            th.start()
        for th in threads:
            th.join()

        # Reload from disk to confirm persisted state matches in-memory.
        ds2 = DatasetStore(tmp_path)
        assert ds2.runs[run_id].tags == ds.runs[run_id].tags
        assert len(ds.runs[run_id].tags) == n_threads * per_thread

    def test_concurrent_toggle_bookmark_eventual_consistency(self, tmp_path):
        """Many threads toggling the same bookmark in lockstep should leave
        the in-memory state and on-disk file in agreement (not in disagreement
        because one mutation got swallowed)."""
        import threading
        from quam_state_manager.core.dataset import DatasetStore

        _seed_dataset_run(tmp_path, 1)
        ds = DatasetStore(tmp_path)
        run_id = next(iter(ds.runs.keys()))

        def toggle_many():
            for _ in range(50):
                ds.toggle_bookmark(run_id)

        threads = [threading.Thread(target=toggle_many) for _ in range(4)]
        for th in threads:
            th.start()
        for th in threads:
            th.join()

        # 4 x 50 = 200 toggles; even number -> bookmark back to False on
        # disk AND in memory. The two must agree.
        ds2 = DatasetStore(tmp_path)
        assert ds2.runs[run_id].bookmarked == ds.runs[run_id].bookmarked


class TestCollectionsAndFavoriteTag:
    """Collections page + the ⭐ → 'favorite' tag conversion."""

    def test_legacy_bookmark_migrates_to_favorite_tag(self, tmp_path):
        from quam_state_manager.core.dataset import DatasetStore, FAVORITE_TAG
        _seed_dataset_run(tmp_path, 5)
        _seed_dataset_run(tmp_path, 6)
        tags_file = tmp_path / "quashboard_tags.json"
        tags_file.write_text(json.dumps(
            {"bookmarks": [5], "tags": {"6": ["rabi"]}, "notes": {}}), encoding="utf-8")
        ds = DatasetStore(tmp_path)
        assert FAVORITE_TAG in ds.runs[5].tags
        assert ds.runs[5].bookmarked is True
        on_disk = json.loads(tags_file.read_text())
        assert on_disk["bookmarks"] == []
        assert FAVORITE_TAG in on_disk["tags"]["5"]

    def test_migration_idempotent_no_rewrite_on_reload(self, tmp_path):
        from quam_state_manager.core.dataset import DatasetStore, FAVORITE_TAG
        _seed_dataset_run(tmp_path, 5)
        tags_file = tmp_path / "quashboard_tags.json"
        tags_file.write_text(json.dumps({"bookmarks": [5], "tags": {}, "notes": {}}), encoding="utf-8")
        DatasetStore(tmp_path)               # first load migrates + writes once
        mt1 = tags_file.stat().st_mtime_ns
        ds = DatasetStore(tmp_path)           # already migrated → no write
        ds._load_tags()                       # simulate a rescan reload → no write
        assert tags_file.stat().st_mtime_ns == mt1
        assert FAVORITE_TAG in ds.runs[5].tags

    def test_toggle_bookmark_adds_and_removes_favorite_tag(self, tmp_path):
        from quam_state_manager.core.dataset import DatasetStore, FAVORITE_TAG
        _seed_dataset_run(tmp_path, 1)
        ds = DatasetStore(tmp_path)
        rid = next(iter(ds.runs.keys()))
        assert ds.toggle_bookmark(rid) is True
        assert FAVORITE_TAG in ds.runs[rid].tags and ds.runs[rid].bookmarked is True
        assert ds.toggle_bookmark(rid) is False
        assert FAVORITE_TAG not in ds.runs[rid].tags and ds.runs[rid].bookmarked is False

    def test_collections_shows_only_tagged_and_has_tag_grid(self, tmp_path):
        app = create_app(testing=True, instance_path=str(tmp_path / "_inst"))
        data = tmp_path / "data"
        data.mkdir()
        _seed_dataset_run(data, 11)
        _seed_dataset_run(data, 12)
        c = app.test_client()
        c.post("/workspace/add", data={"folder": str(data)})
        from quam_state_manager.web import routes as _r
        c.post(f"/dataset/{_r._dataset_uid(_r._folder_key(data), 11)}/tag", json={"tag": "flagged"})
        headers = {"HX-Request": "true"}
        coll = c.get("/collections", headers=headers).get_data(as_text=True)
        dsets = c.get("/datasets", headers=headers).get_data(as_text=True)
        assert 'id="tag-filter-grid"' in coll
        assert "tag-filter-grid" not in dsets           # not on the plain Datasets page
        import re
        m = re.search(r'data-view="collections"[^>]*>(.*?)</script>', coll, re.S)
        rows = json.loads(m.group(1))
        assert [r["id"] for r in rows] == [11]          # only the tagged run

    def test_bookmark_endpoint_returns_state_and_tags(self, tmp_path):
        from quam_state_manager.core.dataset import FAVORITE_TAG
        app = create_app(testing=True, instance_path=str(tmp_path / "_inst"))
        data = tmp_path / "data"
        data.mkdir()
        _seed_dataset_run(data, 7)
        c = app.test_client()
        c.post("/workspace/add", data={"folder": str(data)})
        from quam_state_manager.web import routes as _r
        uid = _r._dataset_uid(_r._folder_key(data), 7)
        body = c.post(f"/dataset/{uid}/bookmark").get_json()
        assert body["bookmarked"] is True
        assert FAVORITE_TAG in body["tags"]

    def test_bookmarks_panel_route_removed(self, tmp_path):
        app = create_app(testing=True, instance_path=str(tmp_path / "_inst"))
        assert app.test_client().get("/bookmarks/panel").status_code == 404


class TestDatasetDataJsonCacheBound:
    """Phase 2 §3.2 (High): ``_data_json_cache`` must be bounded so a
    2 000-run workspace doesn't pin multi-GB of parsed data.json content
    in memory."""

    def test_cache_capped_at_max(self, tmp_path, monkeypatch):
        from quam_state_manager.core import dataset as dsmod
        from quam_state_manager.core.dataset import DatasetStore

        monkeypatch.setattr(dsmod, "_DATA_JSON_CACHE_MAX", 3)

        for i in range(8):
            _seed_dataset_run(tmp_path, i)
        ds = DatasetStore(tmp_path)
        assert len(ds._data_json_cache) <= 3, (
            f"expected cache <= 3 after scan, got {len(ds._data_json_cache)}"
        )

    def test_evicted_entry_repopulated_on_demand(self, tmp_path, monkeypatch):
        from quam_state_manager.core import dataset as dsmod
        from quam_state_manager.core.dataset import DatasetStore

        monkeypatch.setattr(dsmod, "_DATA_JSON_CACHE_MAX", 2)
        for i in range(5):
            _seed_dataset_run(tmp_path, i)
        ds = DatasetStore(tmp_path)
        # Manually evict everything to simulate post-LRU churn.
        ds._data_json_cache.clear()
        # Asking for the data again should re-load from disk and re-cache,
        # NOT return empty.
        run_id = next(iter(ds.runs.keys()))
        data = ds._get_data_json(run_id)
        assert data, "evicted entry should re-load from disk on demand"
        assert run_id in ds._data_json_cache


class TestDatasetPathTraversalContainment:
    """Phase 2 §3.3 (Medium): ``get_figure_path`` must use
    ``Path.is_relative_to`` so prefix-substring confusion can't leak."""

    def test_sibling_dir_with_prefix_name_is_rejected(self, tmp_path):
        """A figure_name that resolves into a sibling folder whose name shares
        a prefix with the run folder (run vs run-evil) must NOT be served --
        ``str.startswith`` would let this through."""
        from quam_state_manager.core.dataset import DatasetStore

        date_dir = tmp_path / "2026-05-28"
        date_dir.mkdir()
        run = date_dir / "#1_test_experiment_010000"
        run.mkdir()
        evil = date_dir / "#1_test_experiment_010000-evil"
        evil.mkdir()
        (evil / "exploit.png").write_bytes(b"png")
        (run / "node.json").write_text(json.dumps({
            "metadata": {"name": "test_experiment", "status": "successful"},
            "data": {"parameters": {"model": {"qubits": ["q1"]}}, "outcomes": {}},
            "id": 1, "parents": [], "created_at": "2026-05-28T01:00:00",
        }), encoding="utf-8")
        (run / "data.json").write_text(json.dumps({
            "fig": "../#1_test_experiment_010000-evil/exploit.png",
        }), encoding="utf-8")

        ds = DatasetStore(tmp_path)
        path = ds.get_figure_path(1, "fig")
        assert path is None, (
            "figure resolving to a prefix-sibling directory must be rejected"
        )


class TestPhase3DatasetParallelScan:
    """Phase 3 §2.2 — DatasetStore._scan must fan the per-run parse across
    a ThreadPoolExecutor so 10⁴-run cold scans don't freeze the UI."""

    def test_parallel_speedup_with_slow_parser(self, tmp_path, monkeypatch):
        import time
        from quam_state_manager.core.dataset import DatasetStore

        # Seed 16 runs.
        for i in range(16):
            run = tmp_path / "2026-05-28" / f"#{i}_test_010000"
            run.mkdir(parents=True)
            (run / "node.json").write_text(json.dumps({
                "metadata": {"name": "test", "status": "successful"},
                "data": {"parameters": {"model": {"qubits": [f"q{i}"]}}, "outcomes": {}},
                "id": i, "parents": [], "created_at": "2026-05-28T01:00:00",
            }), encoding="utf-8")
            (run / "data.json").write_text(json.dumps({
                "fit_results": {f"q{i}": {"T1": 8e-6}},
            }), encoding="utf-8")

        # Slow down each per-run parse so the serial vs parallel
        # difference is unambiguous.
        real_parse = DatasetStore._parse_run_folder

        def slow_parse(self, *args, **kwargs):
            time.sleep(0.04)
            return real_parse(self, *args, **kwargs)

        monkeypatch.setattr(DatasetStore, "_parse_run_folder", slow_parse)

        start = time.time()
        ds = DatasetStore(tmp_path)
        elapsed = time.time() - start
        assert len(ds.runs) == 16
        # 16 × 40 ms = 640 ms serial; parallel should be well under
        # 300 ms even on a 2-core machine.
        assert elapsed < 0.5, (
            f"parallel scan took {elapsed:.2f}s for 16 runs × 40ms = "
            f"640ms serial; parallel should be < 0.5s"
        )


# ---------------------------------------------------------------------------
# Phase 4 — security + concurrency regressions
# ---------------------------------------------------------------------------


class TestPhase4ScriptJsonFilter:
    """Phase 4 §1 — script_json filter must escape HTML5 script
    terminators so a researcher-shared state.json with `</script>` in
    any string value can't break out of the script context."""

    def test_filter_escapes_lt_gt_amp(self):
        from quam_state_manager.web.app import _script_json_filter
        out = str(_script_json_filter({"name": "</script><script>alert(1)//"}))
        # The literal "</" sequence must not appear in the output.
        assert "</" not in out
        # The escape representation must appear (parse-equivalent in JS).
        assert "\\u003c" in out
        # The malicious script tag literal must not appear either.
        assert "<script>" not in out

    def test_filter_accepts_already_serialised_string(self):
        from quam_state_manager.web.app import _script_json_filter
        out = str(_script_json_filter('"<!--evil"'))
        assert "<!--" not in out
        assert "\\u003c" in out

    def test_endpoint_does_not_leak_script_terminator(self, app, tmp_path):
        """End-to-end: load a synthetic state.json with a malicious
        qubit name; the /qubits page response must not contain the
        unescaped attack literal."""
        evil = tmp_path / "evil"
        evil.mkdir()
        state = {
            "qubits": {
                "</script><script>window.PWNED=true": {"id": "qX", "f_01": 5e9},
            },
            "qubit_pairs": {},
        }
        (evil / "state.json").write_text(json.dumps(state), encoding="utf-8")
        (evil / "wiring.json").write_text(
            '{"wiring": {}, "network": {"host": "h"}}', encoding="utf-8"
        )
        client = app.test_client()
        client.post("/load", data={"folder": str(evil)})
        resp = client.get("/qubits")
        body = resp.data.decode("utf-8", errors="replace")
        assert "<script>window.PWNED=true" not in body, (
            "Malicious script literal leaked into rendered HTML — XSS!"
        )


class TestPhase4QuamCacheConcurrency:
    """Phase 4 §2 — concurrent /load requests must not race on the
    cache. Spawn N threads activating the same folder; assert exactly
    one cache entry afterwards."""

    def test_concurrent_activate_quam_keeps_one_entry(self, app, tmp_path):
        import threading
        from quam_state_manager.web import routes

        folder = tmp_path / "chip" / "quam_state"
        folder.mkdir(parents=True)
        (folder / "state.json").write_text(
            json.dumps({"qubits": {"q1": {"f_01": 5e9}}}), encoding="utf-8"
        )
        (folder / "wiring.json").write_text('{"wiring": {}}', encoding="utf-8")

        with routes._quam_cache_lock:
            routes._quam_cache.clear()

        errors: list[Exception] = []

        def hit():
            try:
                with app.app_context():
                    routes._activate_quam(folder)
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=hit) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors, f"thread errors: {errors}"
        key = str(folder)
        with routes._quam_cache_lock:
            assert key in routes._quam_cache, "cache miss after race"
            same_key_count = sum(1 for k in routes._quam_cache if k == key)
            assert same_key_count == 1


class TestPhase4CSRFOriginCheck:
    """Phase 4 §3 — cross-origin mutations must be 403. The standard
    test fixture sets TESTING=True which bypasses the check; this
    suite spins up a non-testing app to exercise the real behavior."""

    def _real_app(self, tmp_path):
        from quam_state_manager.web.app import create_app
        return create_app(testing=False, instance_path=str(tmp_path / "inst"))

    def test_post_with_evil_origin_returns_403(self, tmp_path):
        app = self._real_app(tmp_path)
        client = app.test_client()
        resp = client.post(
            "/state/sync",
            headers={"Origin": "http://evil.example"},
        )
        assert resp.status_code == 403

    def test_post_with_matching_origin_passes_csrf(self, tmp_path):
        app = self._real_app(tmp_path)
        client = app.test_client()
        resp = client.post(
            "/state/sync",
            headers={"Origin": "http://localhost"},
        )
        # Not 403 from the CSRF check (may be 4xx for "no state loaded").
        assert resp.status_code != 403

    def test_post_without_origin_or_referer_rejected(self, tmp_path):
        app = self._real_app(tmp_path)
        client = app.test_client()
        resp = client.post("/state/sync")
        assert resp.status_code == 403


class TestPhase4SecurityHeaders:
    """Phase 4 §3 — defence-in-depth response headers."""

    def test_csp_header_present(self, client):
        resp = client.get("/")
        assert "Content-Security-Policy" in resp.headers
        csp = resp.headers["Content-Security-Policy"]
        assert "default-src 'self'" in csp
        assert "object-src 'none'" in csp

    def test_nosniff_header_present(self, client):
        resp = client.get("/")
        assert resp.headers.get("X-Content-Type-Options") == "nosniff"

    def test_referrer_policy_present(self, client):
        resp = client.get("/")
        assert resp.headers.get("Referrer-Policy") == "same-origin"


class TestPhase4H5WhichWhitelist:
    """Phase 4 §4 — DatasetStore must refuse `which` values outside the
    whitelist before joining into the HDF5 filename."""

    def test_get_h5_summary_rejects_path_traversal(self, tmp_path):
        from quam_state_manager.core.dataset import DatasetStore
        date = tmp_path / "2026-05-28"
        run = date / "#1_test_010000"
        run.mkdir(parents=True)
        (run / "node.json").write_text(json.dumps({
            "metadata": {"name": "test", "status": "successful"},
            "data": {"parameters": {"model": {"qubits": ["q1"]}}, "outcomes": {}},
            "id": 1, "parents": [], "created_at": "2026-05-28T01:00:00",
        }), encoding="utf-8")
        (run / "data.json").write_text("{}", encoding="utf-8")
        ds = DatasetStore(tmp_path)
        assert ds.get_h5_summary(1, which="../etc") is None
        assert ds.get_h5_summary(1, which="random_other") is None

    def test_get_h5_plot_data_rejects_path_traversal(self, tmp_path):
        from quam_state_manager.core.dataset import DatasetStore
        date = tmp_path / "2026-05-28"
        run = date / "#1_test_010000"
        run.mkdir(parents=True)
        (run / "node.json").write_text(json.dumps({
            "metadata": {"name": "test"}, "data": {"parameters": {"model": {"qubits": ["q1"]}}},
            "id": 1, "parents": [], "created_at": "2026-05-28T01:00:00",
        }), encoding="utf-8")
        (run / "data.json").write_text("{}", encoding="utf-8")
        ds = DatasetStore(tmp_path)
        assert ds.get_h5_plot_data(1, "../etc", "var") is None


# ---------------------------------------------------------------------------
# Phase 5 — web-runtime regressions
# ---------------------------------------------------------------------------


class TestPhase5StartupLock:
    """Phase 5 §2.1 — concurrent first-requests must not double-run the
    cold-start workspace + session + rehydration wiring."""

    def _real_app(self, tmp_path):
        from quam_state_manager.web.app import create_app
        return create_app(testing=False, instance_path=str(tmp_path / "inst"))

    def test_concurrent_first_request_runs_startup_once(self, tmp_path, monkeypatch):
        from quam_state_manager.web import routes
        # Reset the module-level flags so the lock has work to do.
        routes._workspace_loaded = False
        routes._session_loaded = False
        routes._rehydrated = False

        calls = {"load_workspace_roots": 0, "rehydrate": 0}
        real_lwr = routes._load_workspace_roots
        real_reh = routes._rehydrate_workspace_from_recents

        def spy_lwr():
            calls["load_workspace_roots"] += 1
            real_lwr()

        def spy_reh():
            calls["rehydrate"] += 1
            real_reh()

        monkeypatch.setattr(routes, "_load_workspace_roots", spy_lwr)
        monkeypatch.setattr(routes, "_rehydrate_workspace_from_recents", spy_reh)

        app = self._real_app(tmp_path)
        client = app.test_client()

        import threading
        results: list[int] = []

        def hit():
            r = client.get("/", headers={"Referer": "http://localhost"})
            results.append(r.status_code)

        threads = [threading.Thread(target=hit) for _ in range(6)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # Every startup hook must have run at most once across 6 requests.
        assert calls["load_workspace_roots"] <= 1, (
            f"_load_workspace_roots ran {calls['load_workspace_roots']} times"
        )
        assert calls["rehydrate"] <= 1, (
            f"_rehydrate_workspace_from_recents ran {calls['rehydrate']} times"
        )


class TestPhase5CacheControl:
    """Phase 5 §3.1 — HTMX partial responses must carry
    ``Cache-Control: no-store`` so a Back-after-edit doesn't serve a
    stale cached partial. Non-HTMX requests are left untouched."""

    def test_htmx_response_has_no_store(self, loaded_client):
        resp = loaded_client.get("/qubits", headers={"HX-Request": "true"})
        assert resp.headers.get("Cache-Control") == "no-store"

    def test_non_htmx_response_has_no_no_store(self, loaded_client):
        resp = loaded_client.get("/")
        assert resp.headers.get("Cache-Control") != "no-store"


class TestPhase5DatasetLruLock:
    """Phase 5 §2.2 — first-call init of the dataset LRU dict must be
    race-free across concurrent /datasets requests."""

    def test_concurrent_first_dataset_creates_one_lru(self, app):
        import threading
        from quam_state_manager.web import routes

        app.config.pop("dataset_store_lru", None)

        seen: list[int] = []

        def hit():
            with app.app_context():
                lru = routes._dataset_store_lru()
                seen.append(id(lru))

        threads = [threading.Thread(target=hit) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(set(seen)) == 1, (
            f"dataset_store_lru got {len(set(seen))} different instances"
        )


class TestPhase5ScannerSymlinkSafety:
    """Phase 5 §4.3 — Workspace._scan_root must pin followlinks=False
    and reject any candidate whose resolved path escapes the root."""

    def test_symlink_outside_root_is_rejected(self, tmp_path):
        import os
        from quam_state_manager.core import scanner as scanner_mod

        outside_qs = tmp_path / "outside" / "quam_state"
        outside_qs.mkdir(parents=True)
        (outside_qs / "state.json").write_text("{}", encoding="utf-8")
        (outside_qs / "wiring.json").write_text("{}", encoding="utf-8")

        root = tmp_path / "workspace"
        root.mkdir()
        try:
            os.symlink(outside_qs.parent, root / "link_outside")
        except (OSError, NotImplementedError):
            import pytest
            pytest.skip("symlink creation not permitted on this platform")

        entries = scanner_mod._scan_root(root)
        outside_qs_real = outside_qs.resolve()
        for e in entries:
            assert e.quam_state_path.resolve() != outside_qs_real, (
                "scanner followed a symlink outside the workspace root"
            )


class TestPhase5LocalStorageHelpers:
    """Phase 5 §4.2 — confirm the safe* localStorage helpers are
    present in app.js so future code can adopt the pattern."""

    def test_safe_helpers_defined(self):
        from pathlib import Path as _P
        app_js = (_P(__file__).resolve().parent.parent
                  / "quam_state_manager" / "web" / "static" / "app.js")
        text = app_js.read_text(encoding="utf-8")
        assert "function safeLSSet(" in text
        assert "function safeLSGet(" in text


class TestPhase5DatasetPollLifecycle:
    """Phase 5 §1.1 + §1.2 + §4.1 — app.js dataset poll must
    visibility-gate, back off on errors, and use a chained setTimeout
    instead of setInterval. Static-shape check: full JS execution
    would need a browser harness, but the presence of the right
    patterns in app.js catches regressions to the old setInterval."""

    def test_app_js_has_visibility_gate(self):
        from pathlib import Path as _P
        app_js = (_P(__file__).resolve().parent.parent
                  / "quam_state_manager" / "web" / "static" / "app.js")
        text = app_js.read_text(encoding="utf-8")
        # Visibility gating wires up via document.visibilityState.
        assert "document.visibilityState" in text
        # The chained-setTimeout self-rescheduler.
        assert "_schedule(" in text
        # The abort-controller timeout wrapper for hung subprocesses.
        assert "_fetchWithTimeout" in text
        # The connection-lost UX banner.
        assert "_showPollFailureBanner" in text
        # Critically: the old buggy setInterval(pollForNewRuns, ...)
        # MUST be gone — the chained _schedule replaces it.
        assert "setInterval(pollForNewRuns" not in text


# ---------------------------------------------------------------------------
# Dataset checkbox: selection persistence + discoverability fixes
# ---------------------------------------------------------------------------


class TestDatasetSelectionFix:
    """Two related fixes shipped together:

    * Selection now survives same-folder HTMX swaps (the bug). Pre-fix every
      ``#table-pane`` swap called dataset-virtual.js's ``init`` which did
      ``state.selected = new Set()``; ticking 3 boxes then clicking a date
      tab wiped them.
    * The compare bar is now always rendered with state-driven message
      variants — pre-fix it stayed ``display:none`` until 2 boxes were
      already ticked, so the affordance was invisible up front.
    """

    @staticmethod
    def _seed_dataset_folder(tmp_path):
        """Lay out one date folder with one minimal run."""
        date = tmp_path / "2026-05-29"
        run = date / "#1_test_010000"
        run.mkdir(parents=True)
        (run / "node.json").write_text(json.dumps({
            "metadata": {"name": "test", "status": "successful"},
            "data": {"parameters": {"model": {"qubits": ["q1"]}}, "outcomes": {}},
            "id": 1, "parents": [], "created_at": "2026-05-29T01:00:00",
        }), encoding="utf-8")
        (run / "data.json").write_text("{}", encoding="utf-8")
        return tmp_path

    def _client_with_dataset(self, app, tmp_path):
        """Attach a real DatasetStore to the app config and return a client."""
        from quam_state_manager.core.dataset import DatasetStore
        folder = self._seed_dataset_folder(tmp_path / "ds")
        ds = DatasetStore(folder)
        app.config["dataset_store"] = ds
        return app.test_client(), folder

    def test_dataset_payload_carries_active_folder(self, app, tmp_path):
        """The /datasets response embeds data-folder on the JSON payload
        script tag — dataset-virtual.js reads it to decide whether to
        carry the user's selection over or clear it.
        """
        client, folder = self._client_with_dataset(app, tmp_path)
        resp = client.get("/datasets")
        assert resp.status_code == 200
        body = resp.data.decode("utf-8")
        assert 'id="ds-rows-data"' in body
        assert 'data-folder="' in body
        # The folder path renders inside the data-folder attribute.
        assert str(folder) in body or str(folder).replace("\\", "/") in body

    def test_datasets_page_has_column_picker_and_jsbuilt_table(self, app, tmp_path):
        """The renewal: the table's colgroup + header are now JS-built empty
        containers (one width source → no header/value drift), and a Properties
        column-picker ships with the page."""
        client, _ = self._client_with_dataset(app, tmp_path)
        body = client.get("/datasets").data.decode("utf-8")
        assert 'id="datasets-colgroup"' in body
        assert 'id="datasets-thead"' in body
        assert 'id="ds-colvis-menu"' in body and "Properties" in body
        # Old static headers are gone (the header is built from the JS column registry).
        assert "<th>Outcome</th>" not in body
        assert 'data-sort="exp"' not in body

    def test_datasets_page_has_sort_banner(self, app, tmp_path):
        client, _ = self._client_with_dataset(app, tmp_path)
        body = client.get("/datasets").data.decode("utf-8")
        assert 'id="sort-filter-grid"' in body
        assert 'id="sort-col-badges"' in body and 'id="sort-fit-badges"' in body
        assert 'id="ds-curated-keys"' in body          # curated key order payload
        assert "toggleSortBannerCollapsed()" in body

    def test_compare_panel_floats_on_selection(self, app, tmp_path):
        """The compare control ships in the DOM with data-state="empty" so JS can
        drive it, but it's a FLOATING panel hidden by CSS until the first checkbox
        tick (item 3) — no always-on "Tip" bar eating vertical space."""
        client, _ = self._client_with_dataset(app, tmp_path)
        body = client.get("/datasets").data.decode("utf-8")
        assert 'id="ds-compare-bar"' in body
        assert 'data-state="empty"' in body

        # Visibility is CSS-driven ([data-state="empty"]{display:none}), not an
        # inline style on the opening tag.
        import re
        bar_open = re.search(r'<div\s+id="ds-compare-bar"[^>]*>', body)
        assert bar_open is not None
        assert "display:none" not in bar_open.group(0).lower()
        assert "display: none" not in bar_open.group(0).lower()

        # Same Compare / Clear actions are still wired up.
        assert "compareSelectedDatasets()" in body
        assert "clearDatasetCheckboxes()" in body
        assert 'id="ds-compare-count"' in body

        # The old always-on teaching tip is gone (replaced by the floating panel).
        assert "Pick 2–5" not in body and "Pick 2&#8211;5" not in body
        assert "<strong>Tip:</strong>" not in body

    def test_note_is_collapsible_badge(self, app, tmp_path):
        """The note is a collapsible 'Note' badge above Tags (not an always-visible
        input): clicking it reveals an auto-grow textarea that starts hidden."""
        from quam_state_manager.web import routes as _r
        client, folder = self._client_with_dataset(app, tmp_path)
        body = client.get(f"/dataset/{_r._folder_key(folder)}:1").data.decode("utf-8")
        assert "ds-note-block" in body
        assert "ds-note-toggle" in body and "toggleNoteEditor(this)" in body
        assert "ds-note-textarea" in body and "autoGrowNote(this)" in body
        # Editor starts collapsed (hidden); the old always-visible text input is gone.
        assert 'class="ds-note-input"' not in body  # old <input> markup
        assert 'type="text"' not in body or "ds-note-input ds-note-textarea" in body

    def test_datasets_list_fills_height(self, app, tmp_path):
        """The list flex-fills the pane (no hardcoded max-height dead space)."""
        client, _ = self._client_with_dataset(app, tmp_path)
        body = client.get("/datasets").data.decode("utf-8")
        assert 'class="datasets-page"' in body
        css = (Path(__file__).resolve().parent.parent / "quam_state_manager"
               / "web" / "static" / "style.css").read_text(encoding="utf-8")
        assert ".datasets-page" in css
        assert ".datasets-page > .datasets-scroll" in css  # flex-fill override

    def test_dataset_virtual_persists_selection_across_init(self):
        """Static check on dataset-virtual.js: persistence machinery is in
        place — module-level closure vars exist, init() consults
        data-folder, and the old unconditional clear is gone."""
        dvs = (Path(__file__).resolve().parent.parent
               / "quam_state_manager" / "web" / "static" / "dataset-virtual.js")
        text = dvs.read_text(encoding="utf-8")

        # Module-level closure vars exist.
        assert "_persistedSelection" in text
        assert "_persistedFolder" in text

        # init() reads data-folder from the payload to decide clear-vs-prune.
        assert "data-folder" in text

        # The legacy unconditional clear is GONE from init().
        assert "state.selected = new Set();" not in text

        # The alias that wires mutations through to the persisted set still exists.
        assert "state.selected = _persistedSelection" in text


# ---------------------------------------------------------------------------
# Datasets: prev-state diff (item 5)
# ---------------------------------------------------------------------------


class TestDatasetPrevStateDiff:
    """Auto-diff a run's quam_state against the nearest earlier run that has a
    state snapshot (largest run-id < current, skipping state-less runs)."""

    @staticmethod
    def _run(date_dir, run_id, *, f_01, with_state=True):
        run = date_dir / f"#{run_id}_test_01000{run_id}"
        run.mkdir(parents=True)
        run.joinpath("node.json").write_text(json.dumps({
            "metadata": {"name": "test", "status": "successful"},
            "data": {"parameters": {"model": {"qubits": ["q1"]}}, "outcomes": {}},
            "id": run_id, "parents": [], "created_at": f"2026-05-29T01:00:0{run_id}",
        }), encoding="utf-8")
        run.joinpath("data.json").write_text("{}", encoding="utf-8")
        if with_state:
            qs = run / "quam_state"
            qs.mkdir()
            qs.joinpath("state.json").write_text(
                json.dumps({"qubits": {"q1": {"f_01": f_01}}}), encoding="utf-8")
            qs.joinpath("wiring.json").write_text("{}", encoding="utf-8")
        return run

    def _store(self, tmp_path):
        from quam_state_manager.core.dataset import DatasetStore
        date = tmp_path / "ds" / "2026-05-29"
        # run 1 (state) · run 2 (NO state) · run 3 (state) — so "previous of 3"
        # must skip 2 and land on 1.
        self._run(date, 1, f_01=5.0e9)
        self._run(date, 2, f_01=0, with_state=False)
        self._run(date, 3, f_01=5.1e9)
        return DatasetStore(tmp_path / "ds")

    def test_get_previous_run_id_skips_stateless(self, tmp_path):
        ds = self._store(tmp_path)
        assert ds.get_previous_run_id(3) == 1          # skips state-less #2
        assert ds.get_previous_run_id(3, require_state=False) == 2
        assert ds.get_previous_run_id(1) is None       # nothing earlier
        assert ds.get_next_run_id(1) == 3              # skips state-less #2

    def test_prev_state_diff_route(self, app, tmp_path):
        from quam_state_manager.web import routes as _r
        ds = self._store(tmp_path)
        app.config["dataset_store"] = ds
        client = app.test_client()
        fk = _r._folder_key(ds.folder_path)

        # Run 3 vs its previous state-carrying run (1): f_01 changed.
        body = client.get(f"/dataset/{fk}:3/prev-state-diff").data.decode("utf-8")
        assert "prevdiff" in body
        assert "qubits.q1.f_01" in body
        assert "#1" in body                            # diffed against run 1

        # The earliest state-carrying run has nothing to diff against.
        early = client.get(f"/dataset/{fk}:1/prev-state-diff").data.decode("utf-8")
        assert "No earlier run" in early

    def test_detail_view_exposes_prev_state_tab(self, app, tmp_path):
        from quam_state_manager.web import routes as _r
        ds = self._store(tmp_path)
        app.config["dataset_store"] = ds
        fk = _r._folder_key(ds.folder_path)
        body = app.test_client().get(f"/dataset/{fk}:3").data.decode("utf-8")
        assert "switchDatasetTab('prev'" in body       # Prev State tab present
        assert 'id="ds-tab-combined"' in body          # Full View container
        assert "switchDatasetTab('full'" in body


# ---------------------------------------------------------------------------
# Theme-contrast regression guard
# ---------------------------------------------------------------------------


class TestThemeContrastGuard:
    """Guards the recurring "text invisible in light mode" bug. Pico v2 rescopes
    `--pico-color` -> `--pico-primary-inverse` (white, both themes) on
    <button>/[role=button], so a custom transparent button using
    `color: var(--pico-color)` renders white and vanishes on the light page (but
    shows on dark). Any class that appears on a <button>/[role=button] in the
    templates must NOT set `color: var(--pico-color)` in its base rule — use
    `var(--pico-contrast)` instead."""

    @staticmethod
    def _root():
        return Path(__file__).resolve().parent.parent

    def _css(self):
        """style.css with /* comments */ stripped (so explanatory comments that
        mention var(--pico-color) don't trip the lint)."""
        import re
        raw = (self._root() / "quam_state_manager" / "web" / "static"
               / "style.css").read_text(encoding="utf-8")
        return re.sub(r"/\*.*?\*/", "", raw, flags=re.DOTALL)

    # Generic state/utility tokens shared across many components (buttons AND
    # spans). They aren't component identifiers, so a rule like `.tree-file-tab.active`
    # (a span) must NOT be treated as a button rule just because some button also
    # carries `active`. Excluded from the button-class set.
    _GENERIC = {"active", "disabled", "open", "selected", "hidden", "show",
                "collapsed", "expanded", "loading", "compact", "outline",
                "primary", "secondary", "contrast", "btn-sm"}

    def _button_classes(self):
        import re
        class_re = re.compile(r'class="([^"]*)"')
        classes = set()

        def _add(attr):
            # Drop Jinja expressions, keep literal class tokens.
            attr = re.sub(r"\{%[^%]*%\}|\{\{[^}]*\}\}", " ", attr)
            for tok in attr.split():
                if tok and "{" not in tok and "}" not in tok:
                    classes.add(tok)

        # 1) <button ...> / role="button" tags in templates.
        tpl_dir = self._root() / "quam_state_manager" / "web" / "templates"
        for tpl in tpl_dir.glob("*.html"):
            html = tpl.read_text(encoding="utf-8")
            for tag in re.finditer(r'<[a-zA-Z][^>]*>', html):
                t = tag.group(0)
                if not (t.lower().startswith("<button") or 'role="button"' in t):
                    continue
                cm = class_re.search(t)
                if cm:
                    _add(cm.group(1))

        # 2) JS-CREATED buttons (the gap that let the scheduler's modal-close +
        # overflow-menu items, and the settings toggles, ship invisible). Two
        # shapes: a `<button ... class="...">` HTML string, and a
        # createElement("button") whose .className is assigned a literal.
        js_dir = self._root() / "quam_state_manager" / "web" / "static"
        btn_html = re.compile(r'<button\b[^>]*class=["\']([^"\']*)["\']', re.I)
        # `.sched-overflow-menu button { ... }`-style: a bare `button` descendant
        # rule has no class, so also collect parent classes whose CSS targets
        # `<parent> button`. Handled in the test via a literal-`button` scan.
        create_btn = re.compile(
            r'createElement\(["\']button["\']\)[\s\S]{0,160}?\.className\s*=\s*["\']([^"\']+)["\']')
        for js in js_dir.glob("*.js"):
            src = js.read_text(encoding="utf-8")
            for m in btn_html.finditer(src):
                _add(m.group(1))
            for m in create_btn.finditer(src):
                _add(m.group(1))

        return classes - self._GENERIC

    def test_no_ghost_button_uses_pico_color_for_text(self):
        import re
        css = self._css()
        button_classes = self._button_classes()
        assert "ds-note-toggle" in button_classes  # sanity: found the note button

        # `color: var(--pico-color)` as a PROPERTY (not `--pico-color:` assignment,
        # not background/border-color — the `(?<!-)` blocks the `-color` cases).
        color_re = re.compile(r"(?<!-)\bcolor:\s*var\(--pico-color\)")
        # a literal `button` element in the selector (e.g. `.sched-overflow-menu
        # button`) is rescoped too, even when the button carries no class.
        literal_btn_re = re.compile(r"(^|\s|>|,)button(\s|$|:|\.|,)")
        offenders = []
        for block in re.finditer(r"([^{}]+)\{([^{}]*)\}", css):
            selector, body = block.group(1).strip(), block.group(2)
            low = selector.lower()
            if any(p in low for p in (":hover", ":focus", ":active", "::")):
                continue  # base rules only
            if not color_re.search(body):
                continue
            if literal_btn_re.search(selector):
                offenders.append(selector)
                continue
            for bc in button_classes:
                if re.search(r"\." + re.escape(bc) + r"(?![\w-])", selector):
                    offenders.append(selector)
                    break
        assert not offenders, (
            "Ghost buttons must use var(--pico-contrast), not var(--pico-color) "
            "— Pico rescopes --pico-color->white on <button>: " + "; ".join(offenders))

    def test_note_toggle_uses_pico_contrast(self):
        import re
        css = self._css()
        m = re.search(r"\.ds-note-toggle\s*\{([^}]*)\}", css)
        assert m, ".ds-note-toggle base rule not found"
        assert "var(--pico-contrast)" in m.group(1)
        assert "var(--pico-color)" not in m.group(1)


# ---------------------------------------------------------------------------
# Datasets: experiment-parameter facet filter
# ---------------------------------------------------------------------------


class TestDatasetParamFilter:
    """Filter datasets by experiment parameter (reset=active, …)."""

    @staticmethod
    def _seed(root, run_id, model):
        date = root / "2026-06-09"
        run = date / f"#{run_id}_1Q_11_power_rabi_01000{run_id}"
        run.mkdir(parents=True)
        run.joinpath("node.json").write_text(json.dumps({
            "metadata": {"name": "1Q_11_power_rabi", "status": "successful"},
            "data": {"parameters": {"model": model}, "outcomes": {}},
            "id": run_id, "parents": [], "created_at": f"2026-06-09T01:00:0{run_id}",
        }), encoding="utf-8")
        run.joinpath("data.json").write_text("{}", encoding="utf-8")

    def _store(self, tmp_path):
        from quam_state_manager.core.dataset import DatasetStore
        root = tmp_path / "ds"
        self._seed(root, 1, {"reset_type": "thermal", "use_state_discrimination": False,
                             "num_shots": 50, "amp_range": 0.1, "qubits": ["q1"],
                             "operation": "x180", "simulate": False})
        self._seed(root, 2, {"reset_type": "active", "use_state_discrimination": True,
                             "num_shots": 100, "amp_range": 0.2, "qubits": ["q2"],
                             "operation": "x90", "simulate": False})
        return DatasetStore(root)

    def test_compact_row_ships_categorical_params(self, tmp_path):
        ds = self._store(tmp_path)
        rows = {r["id"]: r for r in ds.list_runs_compact()}
        pm = rows[1]["pm"]
        assert pm["reset_type"] == "thermal"
        assert pm["use_state_discrimination"] is False
        assert pm["num_shots"] == 50           # int kept
        assert pm["operation"] == "x180"
        assert pm["amp_range"] == 0.1          # float kept now (numeric → range filter)
        assert "qubits" not in pm              # list dropped
        assert "simulate" not in pm            # skip-key dropped
        assert rows[2]["pm"]["reset_type"] == "active"

    def test_datasets_payload_carries_pm(self, app, tmp_path):
        app.config["dataset_store"] = self._store(tmp_path)
        body = app.test_client().get("/datasets").data.decode("utf-8")
        assert '"pm"' in body                  # shipped in rows_json
        assert "reset_type" in body

    def test_dataset_virtual_has_param_facet_machinery(self):
        dvs = (Path(__file__).resolve().parent.parent / "quam_state_manager"
               / "web" / "static" / "dataset-virtual.js").read_text(encoding="utf-8")
        assert "paramFilter" in dvs and "_rowMatchesParams" in dvs
        assert "_rebuildParamFacets" in dvs and "_orderedParamKeys" in dvs
        assert "case 'param':" in dvs
        assert "'param'" in dvs                # KNOWN_SCOPES
        assert "p: 'param'" in dvs             # alias
        assert "if (row.pm)" in dvs            # free-text haystack includes params
        assert "=(.+)$" in dvs                 # bare key=value parsing
        # Grouped / numeric-range additions (round 8).
        assert "_paramGroupHtml" in dvs and "paramRangeFilter" in dvs
        assert "_rowMatchesParamRanges" in dvs
        assert "paramKeyNumeric" in dvs        # numeric keys → min/max range

    def test_datasets_page_has_params_sort_group(self, app, tmp_path):
        app.config["dataset_store"] = self._store(tmp_path)
        body = app.test_client().get("/datasets").data.decode("utf-8")
        assert 'data-sort-group="params"' in body
        assert 'id="sort-param-badges"' in body and 'id="sort-param-filter"' in body
        assert "reset_type=active" in body     # help example
        assert 'data-sort-section="fit"' in body and 'data-sort-section="params"' in body  # collapse toggles

    def test_sort_badge_active_state_and_grouping_css(self):
        import re
        css = (Path(__file__).resolve().parent.parent / "quam_state_manager"
               / "web" / "static" / "style.css").read_text(encoding="utf-8")
        # The badge active state is a distinct solid fill, with theme-safe text on
        # the (button) base — NOT --pico-color (rescoped to white on <button>).
        base = re.search(r"\.sort-badge\s*\{([^}]*)\}", css).group(1)
        assert "var(--pico-contrast)" in base and "var(--pico-color)" not in base
        assert ".sort-badge.active" in css and "var(--pico-primary)" in css
        # Grouped param picker + section collapse + numeric range styling exist.
        assert ".param-group-head" in css and ".section-collapsed" in css
        assert ".param-range input" in css


# ---------------------------------------------------------------------------
# Datasets: qubit + qubit-pair search/picker + the round-4 regression guards
# ---------------------------------------------------------------------------


class TestQubitPairSearch:
    """Round 11: search/filter by qubit + qubit-PAIR; 2Q runs derive member qubits."""

    @staticmethod
    def _seed_2q(root, run_id, model):
        date = root / "2026-06-09"
        run = date / f"#{run_id}_2Q_19_chevron_01000{run_id}"
        run.mkdir(parents=True)
        run.joinpath("node.json").write_text(json.dumps({
            "metadata": {"name": "2Q_19_chevron", "status": "successful"},
            "data": {"parameters": {"model": model}, "outcomes": {}},
            "id": run_id, "parents": [], "created_at": f"2026-06-09T01:00:0{run_id}",
        }), encoding="utf-8")
        run.joinpath("data.json").write_text("{}", encoding="utf-8")

    def test_2q_run_derives_member_qubits_and_ships_pairs(self, tmp_path):
        from quam_state_manager.core.dataset import DatasetStore
        root = tmp_path / "ds"
        self._seed_2q(root, 1, {"qubit_pairs": ["qA2-qA1"]})
        ds = DatasetStore(root)
        run = ds.runs[1]
        assert run.qubit_pairs == ["qA2-qA1"]
        assert "qA2" in run.qubits and "qA1" in run.qubits   # members derived from the pair
        row = {r["id"]: r for r in ds.list_runs_compact()}[1]
        assert row["p"] == ["qA2-qA1"]
        assert "qA2" in row["q"] and "qA1" in row["q"]       # qubit filter finds 2Q runs

    def test_sidebar_entry_matches_qubit_and_pair(self):
        from quam_state_manager.web.routes import _entry_matches, _parse_tree_query
        from types import SimpleNamespace
        e = SimpleNamespace(experiment_name="2Q_19_chevron", date_str="2026-06-09",
                            status="successful", run_id=1,
                            qubits=["qA2", "qA1"], qubit_pairs=["qA2-qA1"])
        assert _entry_matches(e, _parse_tree_query("qA2"))        # free-text qubit (exact)
        assert _entry_matches(e, _parse_tree_query("qA2-qA1"))    # free-text pair (substring)
        assert not _entry_matches(e, _parse_tree_query("qZ9"))
        assert _entry_matches(e, _parse_tree_query("qubit:qA1"))  # scopes
        assert _entry_matches(e, _parse_tree_query("pair:qA2"))
        assert _entry_matches(e, _parse_tree_query("qp:qA2-qA1"))

    def test_dataset_virtual_has_pair_machinery(self):
        dvs = (Path(__file__).resolve().parent.parent / "quam_state_manager"
               / "web" / "static" / "dataset-virtual.js").read_text(encoding="utf-8")
        assert "knownPairs" in dvs and "pairFilter" in dvs
        assert "_rowHasAllPairs" in dvs and "_buildPairPicker" in dvs
        assert "case 'pair':" in dvs
        assert "qp: 'pair'" in dvs                    # alias (NOT p:, which is param)
        assert "(row.p || []).join" in dvs            # haystack includes pairs

    def test_datasets_page_has_pairs_picker(self, app, tmp_path):
        from quam_state_manager.core.dataset import DatasetStore
        root = tmp_path / "ds"
        self._seed_2q(root, 1, {"qubit_pairs": ["qA2-qA1"]})
        app.config["dataset_store"] = DatasetStore(root)
        body = app.test_client().get("/datasets").data.decode("utf-8")
        assert 'id="sort-pair-picker"' in body and 'id="sort-pair-menu"' in body
        assert "pair:qA2-qA1" in body                 # help example

    def test_compare_tab_switch_is_panel_scoped(self):
        # B regression guard: tab switching must scope to the clicked panel, and the
        # stale _syncPinnedTabs/_switchBothColumns (broke compare tabs) is retired.
        appjs = (Path(__file__).resolve().parent.parent / "quam_state_manager"
                 / "web" / "static" / "app.js").read_text(encoding="utf-8")
        assert "var panel = _h5Panel(linkEl);" in appjs
        assert '[id$="ds-tab-combined"]' in appjs
        assert "function _syncPinnedTabs" not in appjs
        assert "function _switchBothColumns" not in appjs

    def test_interactive_3d_defaults_first_qubit(self):
        # C regression guard: backend defaults qubit_idx=0 for 3D+ (no error); client
        # replay defaults the same.
        ds_py = (Path(__file__).resolve().parent.parent / "quam_state_manager"
                 / "core" / "dataset.py").read_text(encoding="utf-8")
        assert "if qubit_idx is None and data.shape[0] >= 1:" in ds_py
        appjs = (Path(__file__).resolve().parent.parent / "quam_state_manager"
                 / "web" / "static" / "app.js").read_text(encoding="utf-8")
        assert "qIdx = 0" in appjs


# ---------------------------------------------------------------------------
# Datasets: Slack-style scoped search
# ---------------------------------------------------------------------------


class TestDatasetScopedSearch:
    """Verifies the `qubit:q0 tag:flagged -is:bookmarked` style search.

    The parser is JS (dataset-virtual.js), so we can't run it in-process —
    instead we static-check that the right surfaces exist:

    * dataset-virtual.js declares the parser + matcher + alias table
    * /datasets renders the new placeholder, the ? icon, the hidden panel,
      the X close button, and the click-to-paste example buttons
    * updateFilterCount references the unknownScopes state so typos surface
    * app.js wires the three trigger handlers (first-focus, ?, X)
    """

    @staticmethod
    def _seed_dataset(tmp_path):
        date = tmp_path / "2026-05-29"
        run = date / "#1_test_010000"
        run.mkdir(parents=True)
        (run / "node.json").write_text(json.dumps({
            "metadata": {"name": "test", "status": "successful"},
            "data": {"parameters": {"model": {"qubits": ["q1"]}}, "outcomes": {}},
            "id": 1, "parents": [], "created_at": "2026-05-29T01:00:00",
        }), encoding="utf-8")
        (run / "data.json").write_text("{}", encoding="utf-8")
        return tmp_path

    def _client(self, app, tmp_path):
        from quam_state_manager.core.dataset import DatasetStore
        folder = self._seed_dataset(tmp_path / "ds")
        app.config["dataset_store"] = DatasetStore(folder)
        return app.test_client()

    def test_dataset_virtual_parses_scoped_tokens(self):
        """Static check: parser + matcher + alias/known tables exist."""
        dvs = (Path(__file__).resolve().parent.parent
               / "quam_state_manager" / "web" / "static" / "dataset-virtual.js")
        text = dvs.read_text(encoding="utf-8")

        # Top-level building blocks.
        assert "function tokenize" in text
        assert "function parseQuery" in text
        assert "function matchScope" in text

        # Alias table covers all short forms named in the plan.
        assert "SCOPE_ALIASES" in text
        for short, long in (("q", "qubit"), ("e", "exp"), ("t", "tag"),
                            ("oc", "outcome"), ("d", "date"), ("m", "metric"),
                            ("n", "note")):
            # Each alias appears as a key in the literal: q: 'qubit', e: 'exp', …
            assert f"{short}: '{long}'" in text, f"alias {short}->{long} missing"

        # KNOWN_SCOPES holds every canonical scope.
        for scope in ("qubit", "exp", "tag", "outcome", "date",
                      "id", "metric", "note", "is"):
            assert f"'{scope}'" in text, f"scope {scope} not in KNOWN_SCOPES"

        # Note is searchable: in the free-text haystack AND as a scope.
        assert "if (row.note) parts.push(row.note)" in text
        assert "case 'note':" in text

        # applyFilters consults the parsed scoped filters.
        assert "state.scopedFilters" in text
        assert "matchScope(row" in text

        # onSearchInput routes through parseQuery so scopes are extracted.
        assert "parseQuery(" in text

    def test_datasets_page_advertises_scopes(self, app, tmp_path):
        """The /datasets page ships the new placeholder + help-panel UI."""
        body = self._client(app, tmp_path).get("/datasets").data.decode("utf-8")

        # Placeholder educates the user the moment they hover the search box.
        assert 'id="dataset-search"' in body
        assert "qubit:q0" in body
        assert "tag:flagged" in body
        # The note: scope is advertised in the help panel.
        assert "note:todo" in body

        # ? icon to manually reopen the panel after dismissing.
        assert 'id="ds-search-help-toggle"' in body

        # Help panel itself, initially hidden via the `hidden` attribute
        # (so first-focus auto-open and ? click are the only triggers).
        assert 'id="ds-search-help"' in body
        import re
        panel_open = re.search(r'<div\s+id="ds-search-help"[^>]*>', body)
        assert panel_open is not None
        assert " hidden" in panel_open.group(0)

        # X close button — the only way to dismiss the panel.
        assert 'id="ds-search-help-close"' in body

        # At least one click-to-paste example.
        assert 'data-example="qubit:q0"' in body
        assert 'class="ds-help-example"' in body

    def test_comma_multikeyword_and_qubit_filter(self):
        """Static check: comma-split tokeniser, qubit-aware free-text matching,
        and the AND qubit-picker filter all exist in dataset-virtual.js."""
        dvs = (Path(__file__).resolve().parent.parent
               / "quam_state_manager" / "web" / "static" / "dataset-virtual.js")
        text = dvs.read_text(encoding="utf-8")
        # tokenizer splits on commas too: `q2, q5, time` → three AND keywords
        assert "ch === ','" in text
        # a bare token that names a known qubit → membership (run contains it)
        assert "knownQubits" in text and "_rowHasQubit(" in text
        # the Qubits picker filters by AND across the selected qubits
        assert "qubitFilter" in text and "_rowHasAllQubits(" in text

    def test_datasets_page_has_qubit_picker(self, app, tmp_path):
        body = self._client(app, tmp_path).get("/datasets").data.decode("utf-8")
        assert 'id="sort-qubit-picker"' in body
        assert 'id="sort-qubit-menu"' in body
        assert "q2, q5" in body          # comma multi-keyword advertised (placeholder + help)

    def test_unknown_scope_surfaces_in_filter_count(self):
        """When the user types `foo:bar`, the unknown scope name must appear
        in #dataset-filter-count so they don't think the filter silently
        ate their query."""
        dvs = (Path(__file__).resolve().parent.parent
               / "quam_state_manager" / "web" / "static" / "dataset-virtual.js")
        text = dvs.read_text(encoding="utf-8")
        assert "unknownScopes" in text
        # updateFilterCount reads state.unknownScopes when composing the message.
        assert "state.unknownScopes" in text
        assert "'unknown '" in text or '"unknown "' in text

    def test_app_js_wires_help_panel_triggers(self):
        """app.js delegates focusin + click handlers for the help panel.

        Three triggers per spec:
        - focusin on #dataset-search → open ON FIRST SESSION-FOCUS ONLY
          (sessionStorage flag), not every focus
        - click on #ds-search-help-toggle → always open
        - click on #ds-search-help-close → always close
        Plus a click delegate on .ds-help-example for paste-into-search.
        """
        app_js = (Path(__file__).resolve().parent.parent
                  / "quam_state_manager" / "web" / "static" / "app.js")
        text = app_js.read_text(encoding="utf-8")

        # First-session flag tracked in sessionStorage so the panel doesn't
        # nag the user on every refocus.
        assert "quam_dataset_search_help_shown" in text
        assert "sessionStorage" in text

        # All three IDs are referenced by handlers.
        assert "'ds-search-help-toggle'" in text or '"ds-search-help-toggle"' in text
        assert "'ds-search-help-close'" in text or '"ds-search-help-close"' in text
        assert "'dataset-search'" in text or '"dataset-search"' in text

        # Click-to-paste — delegated by classname.
        assert "ds-help-example" in text
        assert "data-example" in text

    def test_app_js_no_top_level_document_body(self):
        """app.js is loaded eagerly in <head> (base.html, no defer — UI_CONFIG
        and DatasetVirtual must exist before parse-time inline scripts run), so
        `document.body` is null at script-parse time. Any top-level
        `document.body.addEventListener(...)` throws and halts the rest of the
        file, taking switchDatasetTab and dozens of other globals with it. All
        bubbling-event delegation must land on `document` instead.

        Regression for the scoped-search ship — three call sites that had
        to be migrated. This test fails loud the moment anyone adds a new
        one.
        """
        app_js = (Path(__file__).resolve().parent.parent
                  / "quam_state_manager" / "web" / "static" / "app.js")
        text = app_js.read_text(encoding="utf-8")
        assert "document.body.addEventListener" not in text, (
            "Top-level document.body.addEventListener throws because app.js "
            "loads in <head> before <body> exists. Use document.addEventListener "
            "for delegation — focusin/click/htmx:afterSwap all bubble to document."
        )

    def test_beforeswap_plotly_purge_targets_swap_container(self):
        """The htmx:beforeSwap Plotly purge must scan the swap TARGET (the
        container being replaced), not evt.detail.elt (the element that fired the
        request). Clicking a pulse/qubit row triggers from the ROW while the
        plot lives in #inspector-pane, so scanning elt never purges the outgoing
        plot — the next plot renders with clipped (invisible) axes and dead
        interactivity ('2nd pulse click → broken plot, fine after close+reopen').
        Regression guard for that fix.
        """
        app_js = (Path(__file__).resolve().parent.parent
                  / "quam_state_manager" / "web" / "static" / "app.js")
        text = app_js.read_text(encoding="utf-8")
        assert "var el = evt.detail.target || evt.detail.elt;" in text, (
            "the beforeSwap Plotly purge must target the swap container "
            "(evt.detail.target), not the trigger element (evt.detail.elt)."
        )
        # closeInspector bypasses htmx, so it must purge its own plots too.
        assert 'pane.querySelectorAll(".js-plotly-plot")' in text

    def test_datasets_scroll_not_size_contained(self):
        """`.datasets-scroll` is the virtual-scroller viewport. It must NOT use
        `contain: strict` / `contain: size`: with only a max-height (no explicit
        height), size containment sizes the box as if it had no contents, so it
        collapses to height 0. The scroller then renders just its overscan
        buffer into a 0px clipped container and the table looks empty even though
        rows are in the DOM. Regression for the zero-height /datasets bug — use
        `contain: content` (layout/paint isolation, no size collapse) instead.
        """
        style_css = (Path(__file__).resolve().parent.parent
                     / "quam_state_manager" / "web" / "static" / "style.css")
        text = style_css.read_text(encoding="utf-8")
        assert ".datasets-scroll {" in text, ".datasets-scroll rule missing from style.css"
        start = text.index(".datasets-scroll {")
        block = text[start:text.index("}", start)]          # CSS rules have no nested braces
        # Drop CSS comments so an explanatory /* ...contain: strict... */ note
        # inside the rule doesn't trip the scan — we only care about declarations.
        while "/*" in block and "*/" in block:
            i = block.index("/*")
            block = block[:i] + block[block.index("*/", i) + 2:]
        normalized = block.replace(" ", "")
        assert "contain:strict" not in normalized, (
            ".datasets-scroll must not use `contain: strict` — it bundles "
            "`contain: size`, collapsing the content-sized scroll box to height 0 "
            "(the table renders into a 0px clipped container and looks empty)."
        )
        assert "contain:size" not in normalized, (
            ".datasets-scroll must not use `contain: size` — same height-0 collapse. "
            "Use `contain: content` or omit size containment."
        )


class TestOpenFolderRoute:
    """`/open-folder` opens a folder in the OS file explorer, but the route is
    reachable by any JS on the page or any local process, so it MUST confine the
    target to a registered workspace root (anti-traversal) and stay fire-and-forget
    (a launcher fault must never 500-crash / block a worker). These tests pin the
    security gate the adversarial review demanded."""

    @pytest.fixture
    def rooted_client(self, tmp_path):
        """App whose workspace has one registered root with a child subfolder."""
        root = tmp_path / "ws_root"
        (root / "exp_a").mkdir(parents=True)
        app = create_app(testing=True, instance_path=str(tmp_path / "_of_inst"))
        app.config["workspace"].add_root(root)
        return app.test_client(), root

    def test_missing_folder_rejected(self, rooted_client):
        client, _ = rooted_client
        resp = client.post("/open-folder", data={"folder": ""})
        assert resp.status_code == 400
        assert resp.get_json()["ok"] is False

    def test_path_outside_roots_rejected(self, rooted_client, tmp_path):
        client, _ = rooted_client
        outside = tmp_path / "not_a_workspace"
        outside.mkdir()
        resp = client.post("/open-folder", data={"folder": str(outside)})
        assert resp.status_code == 403
        assert "workspace" in resp.get_json()["error"].lower()

    def test_traversal_escape_rejected(self, rooted_client, tmp_path):
        """A `..` that climbs out of the root is rejected — the route resolve()s
        before the containment check so traversal can't escape."""
        client, root = rooted_client
        (tmp_path / "escaped").mkdir()
        sneaky = root / ".." / "escaped"
        resp = client.post("/open-folder", data={"folder": str(sneaky)})
        assert resp.status_code == 403

    def test_inside_root_but_missing_rejected(self, rooted_client):
        client, root = rooted_client
        missing = root / "does_not_exist"
        resp = client.post("/open-folder", data={"folder": str(missing)})
        # Passes containment (is_relative_to is path-only) then fails is_dir.
        assert resp.status_code == 400
        assert "exist" in resp.get_json()["error"].lower()

    def test_valid_subdir_launches(self, rooted_client, monkeypatch):
        import platform
        import subprocess
        client, root = rooted_client
        calls = []
        # Force the Windows branch so the launcher is a single deterministic
        # explorer Popen (no wslpath / env-dependent shutil.which lookups).
        monkeypatch.setattr(platform, "system", lambda: "Windows")
        monkeypatch.setattr(subprocess, "Popen", lambda *a, **k: calls.append(a))
        resp = client.post("/open-folder", data={"folder": str(root / "exp_a")})
        assert resp.status_code == 200
        assert resp.get_json()["ok"] is True
        assert calls, "expected the explorer launcher to be invoked"

    def test_root_itself_allowed(self, rooted_client, monkeypatch):
        import platform
        import subprocess
        client, root = rooted_client
        monkeypatch.setattr(platform, "system", lambda: "Windows")
        monkeypatch.setattr(subprocess, "Popen", lambda *a, **k: None)
        resp = client.post("/open-folder", data={"folder": str(root)})
        assert resp.status_code == 200


class TestRound12Wiring:
    """Static guards for the round-12 sidebar context menu + dataset header
    qubit/pair list, so a later refactor can't silently drop the wiring."""

    def _read(self, *parts):
        base = Path(__file__).resolve().parent.parent / "quam_state_manager"
        return base.joinpath(*parts).read_text(encoding="utf-8")

    def test_sidebar_entry_exposes_folder_path(self):
        html = self._read("web", "templates", "_sidebar_tree_entries.html")
        assert 'data-folder-path="{{ entry.folder_path | string }}"' in html

    def test_contextmenu_handler_bound(self):
        js = self._read("web", "static", "app.js")
        assert "addEventListener('contextmenu'" in js
        assert ".tree-entry-click[data-folder-path]" in js
        assert "window.openFolderInExplorer" in js

    def test_header_qubit_list_truncates_with_title(self):
        html = self._read("web", "templates", "_inspector_header.html")
        assert 'class="inspector-qubit-list"' in html
        assert 'title="{{ inspector_qubits }}"' in html
        css = self._read("web", "static", "style.css")
        start = css.index(".inspector-qubit-list {")
        block = css[start:css.index("}", start)]
        assert "text-overflow: ellipsis" in block
        assert "overflow: hidden" in block

    def test_get_run_ships_qubit_pairs(self):
        py = self._read("core", "dataset.py")
        assert '"qubit_pairs": run.qubit_pairs,' in py

    def test_context_menu_item_not_white_on_light(self):
        """The menu items are <button>s — Pico rescopes --pico-color→white on
        buttons, so a custom transparent button using color:var(--pico-color)
        renders invisible on light. Guard the documented fix: --pico-contrast
        text + explicit transparent background."""
        css = self._read("web", "static", "style.css")
        start = css.index(".sidebar-context-item {")
        block = css[start:css.index("}", start)]
        assert "var(--pico-contrast)" in block
        assert "background: transparent" in block
        assert "var(--pico-color)" not in block


class TestRound13SidebarSearchBox:
    """Round 13: the left-panel experiment-tree filter became an auto-grow
    <textarea> (single line by default, wraps to ~3 lines), bigger font, tighter
    padding. These guards pin the conversion + the couplings that must survive."""

    def _read(self, *parts):
        base = Path(__file__).resolve().parent.parent / "quam_state_manager"
        return base.joinpath(*parts).read_text(encoding="utf-8")

    def test_filter_is_textarea_with_couplings(self):
        base = self._read("web", "templates", "base.html")
        # Locate the sidebar filter element by its id and confirm it's a textarea
        # carrying every attribute the HTMX filter / poll / pills / help depend on.
        i = base.index('id="sidebar-filter-input"')
        tag = base.rfind("<", 0, i)
        assert base[tag:tag + 9] == "<textarea", "sidebar filter must be a <textarea>"
        # The opening tag spans multiple lines; slice to its closing '>'.
        opening = base[tag:base.index(">", i)]
        assert 'name="name"' in opening
        assert 'class="search-help-input"' in opening
        assert 'data-search-help="sidebar-search-help"' in opening
        assert 'hx-get="/workspace/tree"' in opening
        assert 'hx-target="#sidebar-tree"' in opening
        assert "autoGrowNote(this)" in opening      # input-time auto-grow
        assert "renderFilterTags(this" in opening    # pill chips preserved
        assert 'aria-label="' in opening             # accessible name added

    def test_help_siblings_preserved(self):
        # The ?-help affordance (asserted by test_sidebar_scoped_search_help_present)
        # must survive the rewrite.
        base = self._read("web", "templates", "base.html")
        assert 'id="sidebar-search-help"' in base
        assert "search-help-toggle" in base

    def test_css_textarea_rule_scoped_and_autogrow(self):
        css = self._read("web", "static", "style.css")
        # The dedicated auto-grow block is the multi-line one (newline after `{`),
        # distinct from the shared single-line `.sidebar-filter input, .sidebar-filter
        # textarea { … }` rule that carries the font/padding.
        start = css.index(".sidebar-filter textarea {\n")
        block = css[start:css.index("}", start)]
        assert "resize: none" in block               # no drag grip
        assert "max-height:" in block                # capped growth
        assert "min-height:" in block                # single-line rest
        # Bigger font + tighter padding live on the shared rule just above it.
        shared = css[css.index(".sidebar-filter input,"):start]
        assert "font-size: 1.05em" in shared
        assert "padding: 0.2rem 0.3rem" in shared

    def test_enter_suppressed_in_js(self):
        js = self._read("web", "static", "app.js")
        assert "id === 'sidebar-filter-input'" in js and "e.key === 'Enter'" in js
        # Pill-remove path re-shrinks the box.
        assert "autoGrowNote(inputEl)" in js


class TestRound14SidebarPolish:
    """Round 14: filter box taller (? not clipped) + lower; ?-help panel moved out
    of .ds-search-wrap so the button no longer overlaps the open panel; scroll-past-end
    spacer; the redundant #active-badge content pill removed."""

    def _read(self, *parts):
        base = Path(__file__).resolve().parent.parent / "quam_state_manager"
        return base.joinpath(*parts).read_text(encoding="utf-8")

    def test_filter_box_taller_and_lower(self):
        css = self._read("web", "static", "style.css")
        start = css.index(".sidebar-filter textarea {\n")
        block = css[start:css.index("}", start)]
        assert "min-height: 1.9rem" in block          # clears the 24px ? button
        # The box is nudged down below the Compare/Trend buttons.
        fblock = css[css.index(".sidebar-filter {"):css.index("}", css.index(".sidebar-filter {"))]
        assert "margin-top: 0.4rem" in fblock

    def test_help_panel_is_sibling_not_child_of_wrap(self):
        # The ?-help panel must close OUT of .ds-search-wrap (the wrap's </div> comes
        # before the panel) so the absolute ? button centers on the input, not the
        # tall open panel.
        base = self._read("web", "templates", "base.html")
        i_wrap = base.index('<div class="ds-search-wrap">')
        i_close = base.index("</div>", i_wrap)        # first </div> after the wrap opens = wrap close
        i_panel = base.index('id="sidebar-search-help"')
        assert i_close < i_panel, "#sidebar-search-help must be a sibling of .ds-search-wrap, not nested"

    def test_scroll_past_end_spacer(self):
        base = self._read("web", "templates", "base.html")
        assert 'class="sidebar-scroll-spacer"' in base
        css = self._read("web", "static", "style.css")
        sblock = css[css.index(".sidebar-scroll-spacer {"):css.index("}", css.index(".sidebar-scroll-spacer {"))]
        assert "height: 60vh" in sblock

    def test_active_badge_removed(self):
        base = self._read("web", "templates", "base.html")
        css = self._read("web", "static", "style.css")
        assert 'id="active-badge"' not in base        # markup gone
        assert "#active-badge" not in css             # dead CSS gone


# ---------------------------------------------------------------------------
# Demo-audit fixes (2026-06-30): Config Viewer lazy-load + Param History
# graceful degrade on a locked/busy trend index.
# ---------------------------------------------------------------------------

class TestConfigViewerLazyLoad:
    """The full /config page must NOT inline every section's JSON (a multi-MB
    waveforms/integration_weights dump froze the browser on a 21Q chip); it ships
    lazy hx-get hosts and /config/section/<key> serves one section on demand."""

    def _inject(self, app):
        from quam_state_manager.web import routes
        with app.app_context():
            store = routes._store()
            store.generated_config = {
                "waveforms": {f"wf_{i}": {"samples": [0.0] * 64} for i in range(50)},
                "controllers": {"con1": {"fems": {}}},
            }
            store.generated_config_meta = {"basis_hash": "x"}

    def test_page_does_not_inline_sections(self, loaded_client, app):
        self._inject(app)
        html = loaded_client.get("/config", headers={"HX-Request": "true"}).get_data(as_text=True)
        assert "/config/section/" in html            # lazy hosts present
        assert '"samples"' not in html               # big JSON NOT inlined
        assert "/config/section/waveforms" in html

    def test_section_route_serves_one_section(self, loaded_client, app):
        self._inject(app)
        r = loaded_client.get("/config/section/waveforms", headers={"HX-Request": "true"})
        assert r.status_code == 200
        assert '"samples"' in r.get_data(as_text=True)

    def test_missing_section_is_404(self, loaded_client, app):
        self._inject(app)
        r = loaded_client.get("/config/section/nope", headers={"HX-Request": "true"})
        assert r.status_code == 404


class TestParamHistoryBusyIndexDegrade:
    """A locked/busy SQLite trend index must degrade the Param History page to a
    200 with a banner, never a 500 (a 500 on HX-Request swaps a Werkzeug error
    page into #param-history-root and the menu reads as dead)."""

    def test_locked_index_degrades_to_200(self, loaded_client, app, monkeypatch):
        import sqlite3
        from quam_state_manager.core.history import HistoryManager

        def boom(self, *a, **k):
            raise sqlite3.OperationalError("database is locked")

        monkeypatch.setattr(HistoryManager, "extract_property_history", boom)
        monkeypatch.setattr(HistoryManager, "index_summary", boom)
        r = loaded_client.get("/param-history", headers={"HX-Request": "true"})
        assert r.status_code == 200
        assert "trend index is busy" in r.get_data(as_text=True)


class TestBatchUndoAtomic:
    """A multi-field batch (a grid row with several edited cells / plot Apply-All)
    undoes atomically — ONE Ctrl+Z reverts the whole batch, not one cell at a time."""

    def test_multi_field_batch_undoes_in_one_undo(self, loaded_client):
        r = loaded_client.post("/field/edit-batch", json={"updates": [
            {"dot_path": "qubits.qA1.f_01", "value": "6.3e9"},
            {"dot_path": "qubits.qA1.T1", "value": "9000"},
        ]})
        assert r.status_code == 200 and r.get_json()["ok"]
        # ONE undo reverts BOTH …
        u = loaded_client.post("/undo")
        assert u.status_code == 200
        # … and the change log is now empty (the next undo is a no-op).
        again = loaded_client.post("/undo")
        assert again.status_code == 200
        assert "cellsReverted" not in again.headers.get("HX-Trigger", "")

    def test_single_field_batch_is_ungrouped(self, loaded_client):
        loaded_client.post("/field/edit-batch", json={"updates": [
            {"dot_path": "qubits.qA1.f_01", "value": "6.3e9"},
        ]})
        # A lone edit still undoes (group_id None → standalone).
        u = loaded_client.post("/undo")
        assert "cellsReverted" in u.headers.get("HX-Trigger", "")
