"""The open chip's project keeps its quam state elsewhere; no chip open.

1. Runs a QUAlibrate project saves are made from the project's configured
   ``[quam] state_path``. When that folder is not the chip open in SM, the
   Datasets page, a run's detail, Chip Status and the live-change panel say
   so in one notice naming both folders. One folder rule (no per-run compare).
2. With no chip open, the sidebar project row, the project and env badges and
   the Projects page say "not open" instead of reading as an open chip.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from quam_state_manager.core import config_generator
from quam_state_manager.core import qualibrate_config as qc
from quam_state_manager.web import routes
from quam_state_manager.web.app import create_app

pytestmark = pytest.mark.usefixtures("any_project_env_chosen")

MARK = 'data-project-state-mismatch="1"'
HX = {"HX-Request": "true"}


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _chip(folder: Path, name: str = "q1", f01: float = 5.3e9) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "state.json").write_text(json.dumps(
        {"qubits": {name: {"id": name, "f_01": f01}},
         "qubit_pairs": {}, "active_qubit_names": [name]}), encoding="utf-8")
    (folder / "wiring.json").write_text(json.dumps(
        {"network": {"host": "1.1.1.1"}}), encoding="utf-8")
    return folder


def _run(root: Path, run_id: int, f01: float) -> Path:
    folder = root / "2026-11-20" / f"#{run_id}_scan_090000"
    _chip(folder / "quam_state", f01=f01)
    (folder / "node.json").write_text(json.dumps({
        "id": run_id, "created_at": "2026-11-20T09:00:00+09:00",
        "metadata": {"name": "scan", "status": "finished"}}), encoding="utf-8")
    return folder


@pytest.fixture
def proj(tmp_path, monkeypatch):
    """Project alpha (active) keeps its state in chip_a; chip_b is another
    copy of the device's state; lone belongs to no project."""
    cfg = tmp_path / ".qualibrate"
    chip_a = _chip(tmp_path / "chips" / "chip_a")
    chip_b = _chip(tmp_path / "chips" / "chip_b", f01=5.2e9)
    lone = _chip(tmp_path / "chips" / "lone")
    _write(cfg / "config.toml", f'''
[qualibrate]
project = "alpha"
version = 5

[quam]
state_path = "{chip_a.as_posix()}"
version = 3
''')
    _write(cfg / "projects" / "alpha" / "config.toml",
           f'[quam]\nstate_path = "{chip_a.as_posix()}"\n')
    monkeypatch.setenv("QUALIBRATE_CONFIG_FILE", str(cfg))
    monkeypatch.delenv("QUALIBRATE_CONFIG_DIR", raising=False)
    qc._state_index_cache.clear()
    monkeypatch.setattr(routes, "_warm_state_schema_async", lambda *a, **k: None)
    inst = tmp_path / "_inst"
    app = create_app(testing=True, instance_path=str(inst))
    return {"app": app, "c": app.test_client(), "cfg": cfg, "inst": inst,
            "a": chip_a, "b": chip_b, "lone": lone, "tmp": tmp_path}


def _point_alpha_at(proj, folder_text: str) -> None:
    """The project's config moves on (the user re-pointed it, or another
    copy of the state is used): rewrite the overlay with a new mtime."""
    overlay = proj["cfg"] / "projects" / "alpha" / "config.toml"
    _write(overlay, f'[quam]\nstate_path = "{folder_text}"\n')
    st = overlay.stat()
    os.utime(overlay, ns=(st.st_atime_ns, st.st_mtime_ns + 2_000_000_000))
    qc._state_index_cache.clear()


def _mismatch(proj):
    with proj["app"].test_request_context("/"):
        return routes._project_state_mismatch()


class TestProjectStateNotice:
    def test_project_state_elsewhere_names_both_folders_on_every_page(self, proj):
        c = proj["c"]
        c.post("/load", data={"folder": str(proj["a"])})
        assert _mismatch(proj) is None                      # same folder: quiet
        _point_alpha_at(proj, proj["b"].as_posix())
        m = _mismatch(proj)
        assert m and m["project"] == "alpha"
        assert Path(m["project_state"]).name == "chip_b"
        assert Path(m["chip_folder"]).name == "chip_a"
        root = proj["tmp"] / "data"
        _run(root, 7, 5.2e9)
        c.post("/workspace/add", data={"folder": str(root)})
        for url in ("/datasets", "/topology"):
            html = c.get(url, headers=HX).get_data(as_text=True)
            assert MARK in html, url
            assert "chip_b" in html and "Runs come from another state." in html, url
        detail = c.get("/dataset/by-run/7", headers=HX)
        if detail.status_code in (301, 302, 303):
            detail = c.get(detail.headers["Location"], headers=HX)
        assert MARK in detail.get_data(as_text=True)
        with proj["app"].test_request_context("/"):
            html = routes.render_template("_project_state_notice.html")
        assert MARK in html and "chip_a" in html and "chip_b" in html

    def test_case_variant_spelling_is_the_same_folder(self, proj):
        if os.name != "nt":
            pytest.skip("case-insensitive folder identity is a Windows rule")
        proj["c"].post("/load", data={"folder": str(proj["a"])})
        _point_alpha_at(proj, proj["a"].as_posix().upper())
        assert _mismatch(proj) is None

    def test_project_with_no_state_path_says_nothing(self, proj):
        proj["c"].post("/load", data={"folder": str(proj["a"])})
        _point_alpha_at(proj, "")
        assert _mismatch(proj) is None
        html = proj["c"].get("/topology", headers=HX).get_data(as_text=True)
        assert MARK not in html

    def test_chip_in_no_project_is_unchanged(self, proj):
        proj["c"].post("/load", data={"folder": str(proj["lone"])})
        assert _mismatch(proj) is None
        html = proj["c"].get("/topology", headers=HX).get_data(as_text=True)
        assert MARK not in html

    def test_no_chip_open_has_no_notice(self, proj):
        assert _mismatch(proj) is None


class TestNoChipOpen:
    def test_badges_and_project_rows_say_not_open(self, proj):
        py = proj["tmp"] / "envs" / "E1" / ("python.exe" if os.name == "nt" else "python")
        py.parent.mkdir(parents=True)
        py.write_bytes(b"")
        config_generator.set_selected_env(str(proj["inst"]), str(py))
        c = proj["c"]
        with proj["app"].test_request_context("/"):
            assert routes._active_ctx() is None
            assert routes._qualibrate_tray_badge()["no_chip"] is True
        badges = c.get("/sidebar/folder-badges").get_data(as_text=True)
        assert 'data-no-chip="1"' in badges and "no chip open" in badges
        subnav = c.get("/qualibrate/subnav").get_data(as_text=True)
        assert 'data-no-chip="1"' in subnav and "not open" in subnav
        page = c.get("/qualibrate", headers=HX).get_data(as_text=True)
        # the Active project line and the active project's row both say so
        assert page.count('data-no-chip="1"') == 2 and "not open in SM" in page
        # a chip opens: every "not open" goes away
        c.post("/load", data={"folder": str(proj["a"])})
        for url in ("/sidebar/folder-badges", "/qualibrate/subnav"):
            html = c.get(url).get_data(as_text=True)
            assert 'data-no-chip="1"' not in html, url
        assert "not open in SM" not in c.get("/qualibrate", headers=HX).get_data(as_text=True)
