"""r9 SUPER-CRITICAL regression: the web edit path must work WITHOUT the CLI
stack, and the Diagnostics one-click value fix must actually edit.

cli.py imports typer at module level; ``type_policy.parse_with_expected``
used to lazily import ``cli._parse_value`` → in an env without typer (a
customer conda env running from source) EVERY ``/field/edit`` 500'd — direct
Explorer input and the Diagnostics affordances all looked silently dead."""

from __future__ import annotations

import json
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from quam_state_manager.web.app import create_app


def _chip(folder: Path) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "state.json").write_text(json.dumps(
        {"qubits": {"q1": {"id": "q1", "f_01": 5.002e9,
                           "xy": {"RF_frequency": 5.0e9}}},
         "qubit_pairs": {}, "active_qubit_names": ["q1"]}), encoding="utf-8")
    (folder / "wiring.json").write_text("{}", encoding="utf-8")
    return folder


def test_field_edit_works_with_typer_blocked(tmp_path):
    """End-to-end in a subprocess with `import typer` poisoned: create the
    app, load a chip, POST /field/edit — must be 200/ok and applied."""
    chip = _chip(tmp_path / "chip")
    code = textwrap.dedent(f"""
        import sys
        sys.modules["typer"] = None      # any `import typer` now raises
        from quam_state_manager.web.app import create_app
        app = create_app(testing=True, instance_path=r"{tmp_path / '_inst'}")
        c = app.test_client()
        r = c.post("/load", data={{"folder": r"{chip}"}})
        assert r.status_code in (200, 302), r.status_code
        r = c.post("/field/edit", data={{"dot_path": "qubits.q1.f_01",
                                         "value": "6.31e9"}})
        assert r.status_code == 200, (r.status_code,
                                      r.get_data(as_text=True)[:300])
        d = r.get_json()
        assert d and d.get("ok") is True, d
        name = app.config["active_context"]
        store = app.config["contexts"][name]["store"]
        assert store.merged["qubits"]["q1"]["f_01"] == 6.31e9
        print("EDIT-OK")
    """)
    out = subprocess.run([sys.executable, "-c", code], capture_output=True,
                         text=True, encoding="utf-8", timeout=300)
    assert out.returncode == 0, (out.stderr or "") + (out.stdout or "")
    assert "EDIT-OK" in out.stdout


class TestApplyFixSetValue:
    """The r9 'Update f_01 → carrier' one-click fix on /diagnostics/apply-fix
    (same live-revalidation doctrine as the downconverter relink)."""

    @pytest.fixture
    def loaded(self, tmp_path):
        chip = _chip(tmp_path / "chip")
        app = create_app(testing=True, instance_path=str(tmp_path / "_inst"))
        c = app.test_client()
        assert c.post("/load", data={"folder": str(chip)}).status_code in (200, 302)
        return {"app": app, "client": c}

    def _store(self, app):
        name = app.config["active_context"]
        return app.config["contexts"][name]["store"]

    def test_happy_path_updates_f01(self, loaded):
        r = loaded["client"].post("/diagnostics/apply-fix", data={
            "action": "set_value", "dot_path": "qubits.q1.f_01",
            "value": repr(5.0e9)})
        d = r.get_json()
        assert r.status_code == 200 and d["ok"] is True
        assert self._store(loaded["app"]).merged["qubits"]["q1"]["f_01"] == 5.0e9

    def test_stale_fix_is_409(self, loaded):
        c = loaded["client"]
        c.post("/diagnostics/apply-fix", data={
            "action": "set_value", "dot_path": "qubits.q1.f_01",
            "value": repr(5.0e9)})
        # the finding is resolved now — the identical re-POST must be refused
        r = c.post("/diagnostics/apply-fix", data={
            "action": "set_value", "dot_path": "qubits.q1.f_01",
            "value": repr(5.0e9)})
        assert r.status_code == 409
        assert "no longer valid" in r.get_json()["error"]

    def test_tampered_value_is_409(self, loaded):
        r = loaded["client"].post("/diagnostics/apply-fix", data={
            "action": "set_value", "dot_path": "qubits.q1.f_01",
            "value": "1.0"})            # not what the live finding offers
        assert r.status_code == 409

    def test_unknown_action_still_400(self, loaded):
        r = loaded["client"].post("/diagnostics/apply-fix", data={
            "action": "explode", "dot_path": "x", "value": "1"})
        assert r.status_code == 400

    def test_diagnostics_page_renders_update_button(self, loaded):
        body = loaded["client"].get(
            "/diagnostics", headers={"HX-Request": "true"}).get_data(as_text=True)
        assert 'data-action="set_value"' in body
        assert "Update f_01" in body
        assert "data-confirm-text" in body
