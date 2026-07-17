"""Routes for the QUAlibrate Projects surface (docs/55) — READ-ONLY tier.

Pins the No-Conflict doctrine's testable core: hitting EVERY qualibrate route
(including Open in SM) leaves the ~/.qualibrate tree byte- and mtime-identical
— SM never writes qualibrate's files in this tier. Plus: the listing/doctor
payload, the sidebar subnav, the Open-in-SM gates (unknown → 404, dangling →
409), the [SM] loaded marker, and the topbar ⚗ badge's three colors.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

import pytest

from quam_state_manager.web.app import create_app


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _chip(folder: Path, name: str = "qA1") -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "state.json").write_text(json.dumps(
        {"qubits": {name: {"id": name, "f_01": 6.25e9}},
         "qubit_pairs": {}, "active_qubit_names": [name]}), encoding="utf-8")
    (folder / "wiring.json").write_text(json.dumps(
        {"network": {"host": "1.1.1.1"}}), encoding="utf-8")
    return folder


def _tree(tmp_path: Path) -> dict[str, Path]:
    """Synthetic .qualibrate mirroring the studied real folder's anomalies."""
    cfg = tmp_path / ".qualibrate"
    good = _chip(tmp_path / "chips" / "good_chip")
    other = _chip(tmp_path / "chips" / "other_chip", name="qB1")
    storage = tmp_path / "datasets"
    storage.mkdir()
    _write(cfg / "config.toml", f'''
[qualibrate]
project = "alpha"
version = 5

[qualibrate.storage]
location = "{storage}"

[quam]
state_path = "{good}"
version = 3
''')
    _write(cfg / "projects" / "alpha" / "config.toml",
           f'[quam]\nstate_path = "{tmp_path / "chips" / "missing"}"\n')
    _write(cfg / "projects" / "beta" / "config.toml",
           f'[quam]\nstate_path = "{good}"\n')
    _write(cfg / "projects" / "delta" / "config.toml",
           '[quam]\nstate_path = ""\n')
    return {"cfg": cfg, "good": good, "other": other}


def _snapshot(root: Path) -> dict[str, tuple[int, str]]:
    """{relpath: (mtime_ns, sha256)} over every file under root."""
    out: dict[str, tuple[int, str]] = {}
    for p in sorted(root.rglob("*")):
        if p.is_file():
            out[str(p.relative_to(root))] = (
                p.stat().st_mtime_ns,
                hashlib.sha256(p.read_bytes()).hexdigest())
    return out


@pytest.fixture
def env(tmp_path, monkeypatch):
    paths = _tree(tmp_path)
    monkeypatch.setenv("QUALIBRATE_CONFIG_FILE", str(paths["cfg"]))
    monkeypatch.delenv("QUALIBRATE_CONFIG_DIR", raising=False)
    app = create_app(testing=True, instance_path=str(tmp_path / "_inst"))
    paths["client"] = app.test_client()
    return paths


class TestListingAndPage:
    def test_api_projects_payload(self, env):
        r = env["client"].get("/api/qualibrate/projects")
        assert r.status_code == 200
        d = r.get_json()
        assert d["active"] == "alpha"
        assert d["versions"]["supported"] is True
        by = {p["name"]: p for p in d["projects"]}
        assert set(by) == {"alpha", "beta", "delta"}
        assert by["alpha"]["state_path"]["exists"] is False
        assert by["beta"]["state_path"]["exists"] is True
        assert all(p["loaded_in_sm"] is False for p in d["projects"])
        codes = {f["code"] for f in d["doctor"]}
        assert "state_path_dangling" in codes and "state_path_empty" in codes

    def test_page_renders_read_only_views(self, env):
        r = env["client"].get("/qualibrate")
        assert r.status_code == 200
        body = r.get_data(as_text=True)
        assert "QUAlibrate Projects" in body
        for name in ("alpha", "beta", "delta"):
            assert name in body
        # raw TOML viewer panes exist; deliberately NO editable TOML field
        # (check the bare fragment — base chrome has unrelated textareas)
        frag = env["client"].get("/qualibrate", headers={
            "HX-Request": "true"}).get_data(as_text=True)
        assert "qualibrate-toml" in frag
        assert "<textarea" not in frag

    def test_sidebar_and_subnav(self, env):
        home = env["client"].get("/").get_data(as_text=True)
        assert 'hx-get="/qualibrate/subnav"' in home     # lazy submenu
        sub = env["client"].get("/qualibrate/subnav").get_data(as_text=True)
        assert "beta" in sub
        # active dot on alpha, warn triangle on its dangling state_path
        assert "●" in sub and "&#9888;" in sub or "⚠" in sub

    def test_subnav_without_config(self, env, monkeypatch, tmp_path):
        monkeypatch.setenv("QUALIBRATE_CONFIG_FILE", str(tmp_path / "ghost"))
        sub = env["client"].get("/qualibrate/subnav").get_data(as_text=True)
        assert "no ~/.qualibrate config" in sub


class TestOpenInSM:
    def test_unknown_project_404(self, env):
        r = env["client"].post("/qualibrate/open", data={"project": "nope"})
        assert r.status_code == 404

    def test_dangling_state_path_409(self, env):
        r = env["client"].post("/qualibrate/open", data={"project": "alpha"})
        assert r.status_code == 409
        assert "does not exist" in r.get_data(as_text=True)

    def test_empty_state_path_409(self, env):
        r = env["client"].post("/qualibrate/open", data={"project": "delta"})
        assert r.status_code == 409

    def test_open_loads_chip_and_marks_listing(self, env):
        c = env["client"]
        r = c.post("/qualibrate/open", data={"project": "beta"})
        assert r.status_code == 302                      # non-HTMX → redirect
        d = c.get("/api/qualibrate/projects").get_json()
        by = {p["name"]: p for p in d["projects"]}
        assert by["beta"]["loaded_in_sm"] is True
        assert by["alpha"]["loaded_in_sm"] is False
        # the chip is actually active in SM
        assert "qA1" in c.get("/").get_data(as_text=True)

    def test_open_htmx_gets_hx_redirect(self, env):
        r = env["client"].post("/qualibrate/open", data={"project": "beta"},
                               headers={"HX-Request": "true"})
        assert r.status_code == 200
        assert r.headers.get("HX-Redirect")


class TestReadOnlyGuarantee:
    def test_no_route_touches_the_qualibrate_tree(self, env):
        """The doctrine's testable core: every P1 route leaves ~/.qualibrate
        byte- AND mtime-identical (a same-content rewrite would still be a
        conflict window against qualibrate's non-atomic writer)."""
        c = env["client"]
        before = _snapshot(env["cfg"])
        c.get("/api/qualibrate/projects")
        c.get("/qualibrate")
        c.get("/qualibrate/subnav")
        c.post("/qualibrate/open", data={"project": "nope"})
        c.post("/qualibrate/open", data={"project": "alpha"})
        c.post("/qualibrate/open", data={"project": "beta"})
        c.get("/")
        c.get("/workbench/match")
        assert _snapshot(env["cfg"]) == before


class TestTrayBadge:
    def test_dangling_active_is_red(self, env):
        body = env["client"].get("/").get_data(as_text=True)
        assert "qualibrate-tray-badge" in body
        assert "qualibrate-tray-danger" in body          # alpha dangling
        assert ">alpha<" not in body                     # rendered inline, not a row
        assert "alpha" in body

    def test_match_and_mismatch_colors(self, tmp_path, monkeypatch):
        # active project = beta whose state exists → match/mismatch observable
        paths = _tree(tmp_path)
        root = paths["cfg"] / "config.toml"
        root.write_text(root.read_text(encoding="utf-8").replace(
            'project = "alpha"', 'project = "beta"'), encoding="utf-8")
        monkeypatch.setenv("QUALIBRATE_CONFIG_FILE", str(paths["cfg"]))
        app = create_app(testing=True, instance_path=str(tmp_path / "_i2"))
        c = app.test_client()

        # no chip loaded → neutral badge (match unknown)
        body = c.get("/").get_data(as_text=True)
        assert "qualibrate-tray-badge" in body
        assert "qualibrate-tray-danger" not in body
        assert "qualibrate-tray-warn" not in body

        # SM opens the SAME chip qualibrate writes → still neutral
        c.post("/qualibrate/open", data={"project": "beta"})
        body = c.get("/").get_data(as_text=True)
        assert "qualibrate-tray-warn" not in body

        # SM switches to a DIFFERENT chip → amber mismatch
        c.post("/load", data={"folder": str(paths["other"])})
        body = c.get("/").get_data(as_text=True)
        assert "qualibrate-tray-warn" in body

    def test_no_config_hides_badge(self, tmp_path, monkeypatch):
        monkeypatch.setenv("QUALIBRATE_CONFIG_FILE", str(tmp_path / "ghost"))
        app = create_app(testing=True, instance_path=str(tmp_path / "_i3"))
        body = app.test_client().get("/").get_data(as_text=True)
        assert "qualibrate-tray-badge" not in body


class TestWorkbenchProjectAware:
    def test_match_payload_carries_active_project(self, env):
        d = env["client"].get("/workbench/match").get_json()
        assert d["qb_project"] == "alpha"
