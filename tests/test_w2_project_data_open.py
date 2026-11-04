"""W2-4: an opened project's data folder counts as an open dataset folder for
the agent (run listing, run detail, check_fit, attribution) -- no Dataset Load
press needed."""
from __future__ import annotations

from unittest.mock import patch

from quam_state_manager.web.app import create_app
from tests.test_alignment_job import _chip, _run


def test_the_projects_data_folder_is_open_for_the_agent(tmp_path):
    loaded = _chip(tmp_path / "loaded")
    data = tmp_path / "data"
    for rid in range(1, 4):
        _run(data, "2026-03-01", rid)
    app = create_app(testing=True, instance_path=str(tmp_path / "_inst"))
    c = app.test_client()
    assert c.post("/load", data={"folder": str(loaded)}).status_code in (200, 302)
    assert c.get("/api/agent/run/2").status_code == 409          # nothing declares it
    with patch("quam_state_manager.web.routes._scope_storage_path", return_value=str(data)):
        r = c.get("/api/agent/run/2")
        assert r.status_code == 200, r.get_data(as_text=True)[:300]
        assert c.get("/api/agent/runs").get_json()["count"] == 3
