"""docs/114 (#15/#16) — failures that explain themselves.

Pins:
  - a non-chip folder answers with a PERSISTENT inline panel (not a toast
    that vanishes in 6 s) naming what was looked for, and offers any
    immediate subfolder that ACTUALLY holds a state.json as a one-click
    load (the old hint only fired on literal "quam_state" names);
  - a dangling pointer says DANGLING instead of "Resolves to: <itself>";
  - a read-only live folder is announced in the tray at OPEN time, not
    discovered at apply time after 20 minutes of edits;
  - the value-history clock is visible at rest.
"""

from __future__ import annotations

import json
import os
import stat
import sys
from pathlib import Path

import pytest

from quam_state_manager.web.app import create_app

_WIRING = {"network": {"host": "1.1.1.1", "cluster_name": "C1"}}


def _state(dangling: bool = False):
    q = {"id": "qA1", "f_01": 5.0e9, "T1": 1e-5}
    if dangling:
        q["anharmonicity"] = "#/qubits/qNOPE/f_01"
    return {"qubits": {"qA1": q}, "qubit_pairs": {}, "active_qubit_names": ["qA1"]}


def _write_chip(folder: Path, state: dict):
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "state.json").write_text(json.dumps(state), encoding="utf-8")
    (folder / "wiring.json").write_text(json.dumps(_WIRING), encoding="utf-8")


@pytest.fixture
def client(tmp_path):
    app = create_app(testing=True, instance_path=str(tmp_path / "_inst"))
    return app.test_client()


class TestLoadFailureIsPersistent:
    def test_non_chip_folder_explains_and_offers_candidates(self, client, tmp_path):
        """The reported first-run experience: pick the PARENT of the chip
        folder. The answer must stay on screen and hand you the way out."""
        parent = tmp_path / "experiments"
        _write_chip(parent / "quam_state", _state())
        _write_chip(parent / "some_other_name", _state())   # not literally named
        (parent / "notes").mkdir()                          # no state.json → not offered

        r = client.post("/load", data={"folder": str(parent)})
        assert r.status_code == 400
        html = r.data.decode("utf-8")
        assert "load-failed-panel" in html, "must be a persistent panel, not a toast"
        assert "not a QUAM state" in html
        assert str(parent) in html
        assert "state.json" in html          # says WHAT it looked for
        assert "some_other_name" in html, \
            "a subfolder holding state.json must be offered whatever it is named"
        assert "quam_state" in html
        assert "notes" not in html.split("load-failed-candidates")[-1]
        assert 'hx-post="/load"' in html     # one click loads it

    def test_missing_folder_still_explains(self, client, tmp_path):
        r = client.post("/load", data={"folder": str(tmp_path / "nope")})
        assert r.status_code == 400
        assert "load-failed-panel" in r.data.decode("utf-8")


class TestDanglingPointerHonesty:
    def test_inspector_says_dangling_not_resolves_to_itself(self, client, tmp_path):
        live = tmp_path / "chip"
        _write_chip(live, _state(dangling=True))
        assert client.post("/load", data={"folder": str(live)}).status_code in (200, 302)
        html = client.get("/qubit/qA1").data.decode("utf-8")
        assert "#/qubits/qNOPE/f_01" in html
        assert "ptr-dangling" in html, "a dangling pointer must be marked"
        assert "DANGLING pointer" in html
        assert 'title="Resolves to: #/qubits/qNOPE/f_01"' not in html, \
            "the tooltip must never claim a pointer resolves to itself"

    def test_a_resolving_pointer_is_unchanged(self, client, tmp_path):
        live = tmp_path / "chip2"
        st = _state()
        st["qubits"]["qA1"]["alias_f"] = "#/qubits/qA1/f_01"
        _write_chip(live, st)
        assert client.post("/load", data={"folder": str(live)}).status_code in (200, 302)
        html = client.get("/qubit/qA1").data.decode("utf-8")
        if "alias_f" in html:      # only when the property map surfaces it
            assert "ptr-dangling" not in html


class TestWritePermissionPreflight:
    @pytest.mark.skipif(sys.platform == "win32",
                        reason="chmod read-only is not enforced for the owner on Windows")
    def test_readonly_live_folder_is_announced_in_the_tray(self, client, tmp_path):
        live = tmp_path / "ro_chip"
        _write_chip(live, _state())
        os.chmod(live, stat.S_IRUSR | stat.S_IXUSR)
        try:
            assert client.post("/load", data={"folder": str(live)}).status_code in (200, 302)
            html = client.get("/state/tray").data.decode("utf-8")
            assert "tray-ro-lock" in html
            assert "read-only" in html.lower()
        finally:
            os.chmod(live, stat.S_IRWXU)

    def test_a_writable_chip_shows_no_lock(self, client, tmp_path):
        live = tmp_path / "rw_chip"
        _write_chip(live, _state())
        assert client.post("/load", data={"folder": str(live)}).status_code in (200, 302)
        assert "tray-ro-lock" not in client.get("/state/tray").data.decode("utf-8")


class TestClockVisibleAtRest:
    def test_field_history_button_is_not_invisible(self):
        css = (Path(__file__).resolve().parent.parent / "quam_state_manager"
               / "web" / "static" / "style.css").read_text(encoding="utf-8")
        block = css.split(".field-hist-btn {", 1)[1].split("}", 1)[0]
        assert "opacity: 0;" not in block, \
            "the value-history clock must be discoverable at rest (docs/114)"
        assert "opacity: .3" in block
