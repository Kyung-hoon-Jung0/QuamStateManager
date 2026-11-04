"""F8 (final walk): a project opened before its first run syncs its storage
location, so the runs that later land in its day folders reach the history."""
from __future__ import annotations

from unittest.mock import patch

from quam_state_manager.core import hub_sync


def test_an_empty_location_with_no_project_folder_is_the_root(tmp_path):
    loc = tmp_path / "data"
    loc.mkdir()
    (loc / "journal").mkdir()
    assert hub_sync.storage_root_for_open(str(loc), "pa") == str(loc)


def test_an_existing_project_folder_still_wins(tmp_path):
    loc = tmp_path / "data"
    (loc / "pa").mkdir(parents=True)
    assert hub_sync.storage_root_for_open(str(loc), "pa") == str(loc / "pa")


def test_a_shared_location_is_never_adopted(tmp_path):
    loc = tmp_path / "data"
    (loc / "pb" / "2026-11-04").mkdir(parents=True)
    assert hub_sync.storage_root_for_open(str(loc), "pa") == str(loc / "pa")


def test_the_chip_open_path_uses_it(tmp_path):
    from quam_state_manager.web import routes
    from quam_state_manager.web.app import create_app
    loc = tmp_path / "data"
    loc.mkdir()
    app = create_app(testing=True, instance_path=str(tmp_path / "_inst"))
    with app.test_request_context("/"):
        with patch.object(routes.qualibrate_config, "project_storage",
                          return_value={"native": str(loc), "exists": True}), \
             patch.object(routes, "_load_project_roots", return_value={"pa": [str(loc)]}):
            roots = routes._hub_roots_for({"qualibrate_project": "pa", "path": str(tmp_path / "chip")})
    assert [r for r, _src in roots] == [str(loc)]
