"""F3: the empty Datasets page follows a Dataset Load without a full navigation,
and offers the open project's data folder when SM knows it."""
from __future__ import annotations

from unittest.mock import patch

import pytest

from quam_state_manager.web.app import create_app

H = {"HX-Request": "true"}


@pytest.fixture
def app(tmp_path):
    return create_app(testing=True, instance_path=str(tmp_path / "_inst"))


def test_the_empty_page_listens_for_a_roots_change(app):
    with app.test_client() as c:
        html = c.get("/datasets", headers=H).get_data(as_text=True)
    assert "Dataset Load" in html
    assert 'hx-trigger="workspaceRootsChanged from:body"' in html
    assert 'hx-get="/datasets"' in html and 'hx-target="#table-pane"' in html


def test_collections_refreshes_itself_not_datasets(app):
    with app.test_client() as c:
        html = c.get("/collections", headers=H).get_data(as_text=True)
    assert 'hx-get="/collections" hx-trigger="workspaceRootsChanged from:body"' in html


def test_the_known_project_data_folder_is_offered(app, tmp_path):
    folder = str(tmp_path / "data")
    with patch("quam_state_manager.web.routes._scope_storage_path", return_value=folder):
        with app.test_client() as c:
            html = c.get("/datasets", headers=H).get_data(as_text=True)
    assert "Load this data folder" in html and folder in html
    assert 'hx-post="/workspace/add"' in html


def test_dataset_load_fires_the_event_the_page_listens_for(app, tmp_path):
    run = tmp_path / "data" / "2026-11-04" / "#1_scan_120000"
    run.mkdir(parents=True)
    (run / "node.json").write_text('{"id": 1, "metadata": {"name": "scan"}}', encoding="utf-8")
    with app.test_client() as c:
        r = c.post("/workspace/add", data={"folder": str(tmp_path / "data")}, headers=H)
        assert "workspaceRootsChanged" in (r.headers.get("HX-Trigger") or "")
