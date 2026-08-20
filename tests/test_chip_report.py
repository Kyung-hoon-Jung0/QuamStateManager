"""The printable chip report (docs/126 #21, customer request).

A printer icon beside "Chip Status" in the sidebar opens ``/chip-status/report``
— a STANDALONE page (no app chrome, forced dark theme — what users see in SM,
r3 feedback) with the component-map
drawing and read-only, unpaginated tables of all five component views. Its
toolbar offers Print and a self-contained .html download (the client serializes
the DOM after the map draws, inlining the stylesheet).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from quam_state_manager.web.app import create_app


def _chip(folder: Path) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "state.json").write_text(json.dumps({
        "qubits": {
            "q1": {"id": "q1", "f_01": 6.1e9,
                   "resonator": {"RF_frequency": 7.2e9,
                                 "operations": {"readout": {"amplitude": 0.1,
                                                            "length": 1000}}},
                   "z": {"joint_offset": 0.05, "flux_point": "joint"}},
            "q2": {"id": "q2", "f_01": 6.3e9},
        },
        "qubit_pairs": {"q1-q2": {"id": "q1-q2",
                                  "qubit_control": "#/qubits/q1",
                                  "qubit_target": "#/qubits/q2"}},
        "active_qubit_names": ["q1", "q2"],
    }), encoding="utf-8")
    (folder / "wiring.json").write_text(json.dumps(
        {"network": {"host": "1.2.3.4"}, "wiring": {"qubits": {}}}),
        encoding="utf-8")
    return folder


@pytest.fixture
def client(tmp_path):
    _chip(tmp_path / "quam_state")
    app = create_app(testing=True, instance_path=str(tmp_path / "_i"))
    c = app.test_client()
    c.post("/load", data={"folder": str(tmp_path / "quam_state")})
    return c


class TestSidebarAffordance:
    def test_printer_icon_sits_beside_chip_status(self, client):
        page = client.get("/").get_data(as_text=True)
        i = page.index(">Chip Status</a>")
        # in the SAME nav-sub-row, before the subnav toggle
        row_end = page.index("nav-sub-toggle", i)
        row = page[i:row_end]
        assert 'class="nav-print"' in row
        assert 'href="/chip-status/report"' in row
        assert 'target="_blank"' in row              # a report opens its own tab
        assert "ic-printer" in row                    # SVG, never an emoji

    def test_no_chip_is_a_friendly_page_not_an_error(self, tmp_path):
        app = create_app(testing=True, instance_path=str(tmp_path / "_i2"))
        r = app.test_client().get("/chip-status/report")
        assert r.status_code == 200
        assert "No chip is open" in r.get_data(as_text=True)


class TestReportContent:
    def test_standalone_dark_and_chromeless(self, client):
        b = client.get("/chip-status/report").get_data(as_text=True)
        # standalone: its own <html>, forced DARK (r3: the report shows what
        # users see in SM; print-color-adjust keeps the ink), no app chrome
        assert 'data-theme="dark"' in b
        assert "print-color-adjust: exact" in b
        assert "sidebar" not in b and "topbar" not in b
        assert 'id="pending-tray"' not in b

    def test_all_five_component_sections_render_unpaginated(self, client):
        b = client.get("/chip-status/report").get_data(as_text=True)
        assert "Qubits (2)" in b
        assert "Qubit pairs (1)" in b
        assert "Resonators (1)" in b          # only q1 has one
        assert "Flux lines (1)" in b          # only q1 has z
        assert "Couplers (0)" in b
        assert "q1-q2" in b
        # honest empty state, not a bare heading
        assert "No pair on this chip has a tunable coupler." in b

    def test_map_mount_matches_the_component_pages(self, client):
        """The report draws through the SAME ComponentMap machinery — the
        mount shape must stay what component-map.js expects."""
        b = client.get("/chip-status/report").get_data(as_text=True)
        assert 'id="component-map"' in b and 'class="cmap"' in b
        assert "cmap-body" in b
        assert "component-map.js" in b and "topo-graph.js" in b
        assert "ComponentMap.mount" in b

    def test_toolbar_offers_print_and_selfcontained_download(self, client):
        b = client.get("/chip-status/report").get_data(as_text=True)
        assert "window.print()" in b
        assert 'id="rep-download"' in b
        # the serializer is exposed for the browser probe to pin its output
        assert "ChipReport" in b and "buildStandalone" in b
        # it must strip scripts and inline the stylesheet in the saved file
        assert "querySelectorAll('script')" in b
        assert "link[rel=\"stylesheet\"]" in b
