"""Wiring size control (docs/290).

The readout port labels on the Instrument Wiring rack (multiplexed feedline
sub-circles, drawn at 7 px) could not be read, and the Generate Config wizard
draws the same rack. A surface that passes ``sizeControl`` to
``renderInstrumentWiring`` gets a size bar above its rack -- the Chip Status
map's zoom control -- and remembers its own size.

Client behaviour is pinned by tests/wiring_size_selfcheck.cjs (real app.js +
generate.js under jsdom); this file drives it, and pins the server half: the
two Instrument Wiring templates opt in, and the mounts that must keep their
plain rack (the shareable report's frame, the side-by-side compare) do not.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent
_SELFCHECK = _ROOT / "tests" / "wiring_size_selfcheck.cjs"
_TEMPLATES = _ROOT / "quam_state_manager" / "web" / "templates"

_OPT_IN = "sizeControl: 'instrument'"


@pytest.mark.skipif(shutil.which("node") is None, reason="node not on PATH")
def test_wiring_size_selfcheck_passes():
    r = subprocess.run(
        ["node", str(_SELFCHECK)],
        capture_output=True, text=True, encoding="utf-8", cwd=str(_ROOT),
    )
    if r.returncode == 2:
        pytest.skip("jsdom not installed (run `npm install jsdom`)")
    assert r.returncode == 0, (r.stdout + r.stderr)
    assert "all checks passed" in r.stdout, (r.stdout + r.stderr)


def _state() -> dict:
    return {
        "qubits": {
            "q1": {"id": "q1", "xy": {"opx_output": "#/wiring/qubits/q1/xy/opx_output"}},
            "q2": {"id": "q2", "xy": {"opx_output": "#/wiring/qubits/q2/xy/opx_output"}},
        },
        "ports": {"mw_outputs": {"con1": {"1": {"1": {"band": 1}, "2": {"band": 1}}}}},
    }


def _wiring() -> dict:
    return {
        "wiring": {"qubits": {
            "q1": {"xy": {"opx_output": "#/ports/mw_outputs/con1/1/1"}},
            "q2": {"xy": {"opx_output": "#/ports/mw_outputs/con1/1/2"}},
        }},
        "network": {"host": "10.0.0.1"},
    }


@pytest.fixture
def chip_folder(tmp_path: Path) -> Path:
    folder = tmp_path / "chipA"
    folder.mkdir()
    (folder / "state.json").write_text(json.dumps(_state()), encoding="utf-8")
    (folder / "wiring.json").write_text(json.dumps(_wiring()), encoding="utf-8")
    return folder


@pytest.fixture
def client(tmp_path: Path):
    from quam_state_manager.web.app import create_app
    app = create_app(testing=True, instance_path=str(tmp_path / "_inst"))
    return app.test_client()


class TestInstrumentSurfacesOptIn:
    def test_instrument_page_carries_the_size_control(self, client, chip_folder):
        client.post("/load", data={"folder": str(chip_folder)})
        r = client.get("/instrument")
        assert r.status_code == 200
        body = r.get_data(as_text=True)
        assert "renderInstrumentWiring('instrument-diagram'" in body
        assert _OPT_IN in body

    def test_dropped_chip_preview_carries_it_too(self, client):
        r = client.post("/instrument/preview",
                        json={"state": _state(), "wiring": _wiring(), "label": "chipA"})
        assert r.status_code == 200
        assert _OPT_IN in r.get_data(as_text=True)

    @pytest.mark.parametrize("name", ["chip_report_frame_wiring.html",
                                      "_instrument_compare.html"])
    def test_report_and_compare_mounts_stay_plain(self, name):
        # The report frame lifts the drawn svg into a printable page and the
        # compare view sets racks side by side -- a size bar belongs to
        # neither, so neither may opt in.
        assert "sizeControl" not in (_TEMPLATES / name).read_text(encoding="utf-8")
