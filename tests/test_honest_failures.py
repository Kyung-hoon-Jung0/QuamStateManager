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


class TestIntegrationAuditFixes:
    """The cross-feature audit's confirmed findings, pinned so they cannot
    return (each was invisible to the per-stream pins)."""

    def test_load_failure_body_is_allowed_to_swap(self):
        """htmx drops 4xx bodies unless a beforeSwap allowance permits the
        target — without it the whole persistent-panel feature never reached
        the DOM and the user fell back to a vanishing toast."""
        app_js = (Path(__file__).resolve().parent.parent / "quam_state_manager"
                  / "web" / "static" / "app.js").read_text(encoding="utf-8")
        assert "load-failed-panel" in app_js,             "the /load 400 body needs an explicit htmx swap allowance"
        i = app_js.index("load-failed-panel")
        window = app_js[i - 400:i + 400]
        assert "table-pane" in window and "shouldSwap = true" in window

    def test_readonly_probe_is_real_not_os_access(self):
        """os.access(dir, W_OK) is attribute-only on Windows — it reports a
        read-only share as writable, i.e. it is blind to the exact case the
        hint exists for."""
        import ast
        src = (Path(__file__).resolve().parent.parent / "quam_state_manager"
               / "web" / "routes.py").read_text(encoding="utf-8")
        fn = next(n for n in ast.parse(src).body
                  if isinstance(n, ast.FunctionDef) and n.name == "_probe_readonly")
        # AST, not text: the docstring legitimately NAMES the rejected designs
        calls = [ast.unparse(n.func) for n in ast.walk(fn) if isinstance(n, ast.Call)]
        assert "os.access" not in calls,             "the probe must not CALL os.access (attribute-only for dirs on Windows)"
        code = ast.unparse(fn)
        body_only = code.split('"""', 2)[-1] if '"""' in code else code
        assert "sm_write_probe" not in body_only,             "the probe must NOT create files in the customer's live folder (docs/28)"
        assert "'r+'" in body_only or '"r+"' in body_only,             "it opens the existing state.json for update instead"

    def test_readonly_probe_creates_nothing(self, tmp_path):
        """docs/28: SM touches the live files only on an explicit Apply — a
        permission HINT may not litter a lab's version-controlled state dir."""
        from quam_state_manager.web.routes import _probe_readonly
        _write_chip(tmp_path, _state())
        before = sorted(p.name for p in tmp_path.iterdir())
        stat_before = (tmp_path / "state.json").stat()
        assert _probe_readonly(tmp_path) is False
        assert sorted(p.name for p in tmp_path.iterdir()) == before
        stat_after = (tmp_path / "state.json").stat()
        assert (stat_after.st_mtime, stat_after.st_size) ==                (stat_before.st_mtime, stat_before.st_size),             "the probe must not touch content, size or mtime"

    def test_readonly_probe_answers_false_on_a_writable_folder(self, tmp_path):
        from quam_state_manager.web.routes import _probe_readonly
        assert _probe_readonly(tmp_path) is False
        assert not list(tmp_path.iterdir()), "the probe must clean up after itself"

    def test_readonly_probe_never_raises_on_a_surprise(self, tmp_path):
        """A HINT must degrade, never break activation."""
        from quam_state_manager.web.routes import _probe_readonly
        assert isinstance(_probe_readonly(tmp_path / "nope"), bool)

    def test_readonly_lock_renders_on_a_FULL_page_too(self, client, tmp_path):
        """Same class as the mutation_seq beacon: _render_tray stamped the
        flag but _ctx did not, so the lock only ever appeared after an OOB
        tray swap — never on the render that FOLLOWS opening the chip."""
        import quam_state_manager.web.routes as R
        live = tmp_path / "ro_full"
        _write_chip(live, _state())
        assert client.post("/load", data={"folder": str(live)}).status_code in (200, 302)
        ctx = next(iter(client.application.config["contexts"].values()))
        ctx["live_readonly_hint"] = True          # as a real read-only share would
        html = client.get("/explorer").data.decode("utf-8")
        assert "tray-ro-lock" in html,             "the full-page tray must carry the read-only lock"

    def test_tray_lock_is_labelled_not_a_bare_glyph(self):
        tray = (Path(__file__).resolve().parent.parent / "quam_state_manager"
                / "web" / "templates" / "_pending_tray.html").read_text(encoding="utf-8")
        assert "tray-ro-text" in tray and "read-only" in tray


class TestClockVisibleAtRest:
    def test_field_history_button_is_not_invisible(self):
        css = (Path(__file__).resolve().parent.parent / "quam_state_manager"
               / "web" / "static" / "style.css").read_text(encoding="utf-8")
        block = css.split(".field-hist-btn {", 1)[1].split("}", 1)[0]
        assert "opacity: 0;" not in block, \
            "the value-history clock must be discoverable at rest (docs/114)"
        assert "opacity: .3" in block
