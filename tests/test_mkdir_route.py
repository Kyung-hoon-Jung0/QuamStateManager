"""The folder browser's 'New folder' action — POST /mkdir creates a subfolder
inside an existing directory, with name sanitization + system-path guards.

Backs the customer feedback: every folder picker should be able to CREATE a
folder, not only select an existing one.
"""
from __future__ import annotations

import pytest

from quam_state_manager.web.app import create_app


@pytest.fixture
def client():
    app = create_app()
    app.config["TESTING"] = True          # bypasses the CSRF origin check
    return app.test_client()


def test_mkdir_creates_subfolder(client, tmp_path):
    r = client.post("/mkdir", data={"path": str(tmp_path), "name": "fresh_out"})
    assert r.status_code == 200, r.data
    body = r.get_json()
    assert body["ok"] is True
    new = tmp_path / "fresh_out"
    assert new.is_dir()
    assert body["path"] == str(new)


def test_mkdir_is_idempotent(client, tmp_path):
    (tmp_path / "already").mkdir()
    r = client.post("/mkdir", data={"path": str(tmp_path), "name": "already"})
    assert r.status_code == 200, r.data
    assert r.get_json()["ok"] is True            # exist_ok — not an error


@pytest.mark.parametrize("name", ["", "..", ".", "a/b", "a\\b", "x/../y"])
def test_mkdir_rejects_bad_names(client, tmp_path, name):
    r = client.post("/mkdir", data={"path": str(tmp_path), "name": name})
    assert r.status_code == 400, (name, r.data)
    assert r.get_json()["ok"] is False
    # nothing leaked outside tmp_path
    assert list(tmp_path.iterdir()) == []


def test_mkdir_rejects_missing_parent(client, tmp_path):
    r = client.post("/mkdir", data={"path": str(tmp_path / "nope"), "name": "x"})
    assert r.status_code == 400
    assert r.get_json()["ok"] is False


def test_mkdir_requires_parent(client):
    r = client.post("/mkdir", data={"name": "x"})
    assert r.status_code == 400
    assert r.get_json()["ok"] is False


def test_mkdir_no_traversal_via_name(client, tmp_path):
    # a separator in the name is rejected outright, so it can't escape the parent
    sibling = tmp_path.parent / "ESCAPED_SIBLING"
    r = client.post("/mkdir", data={"path": str(tmp_path), "name": "../ESCAPED_SIBLING"})
    assert r.status_code == 400
    assert not sibling.exists()
