"""Project-lens scope core (docs/63): derivation, pinning, persistence.

The scope (``ctx["qualibrate_project"]``) is DERIVED by reverse-matching the
loaded folder against the stat-cached project→state_path index; an explicit
POST /qualibrate/open PINS the user's choice. The ctx field is a memo — LRU
eviction / restart re-derive on the next activation.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from quam_state_manager.core import path_match
from quam_state_manager.core import qualibrate_config as qc
from quam_state_manager.web import routes as routes_mod
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


@pytest.fixture
def scoped(tmp_path, monkeypatch):
    """cfg tree: alpha (ACTIVE) → chip_a; beta + gamma → shared chip (the
    ambiguity pair); plus a standalone chip in no project.

    Paths are interpolated ``as_posix()``: backslashes are ESCAPES inside a
    TOML basic string, so a raw ``WindowsPath`` (``C:\\Users\\…`` → ``\\U``)
    makes the whole config unparseable on native Windows and every
    derivation silently returns None. ``C:/…`` stays drive-recognisable to
    native_path/same_folder, and on POSIX as_posix() is the identity."""
    cfg = tmp_path / ".qualibrate"
    chip_a = _chip(tmp_path / "chips" / "chip_a")
    shared = _chip(tmp_path / "chips" / "shared", name="qB1")
    standalone = _chip(tmp_path / "chips" / "standalone", name="qC1")
    storage = tmp_path / "datasets"
    storage.mkdir()
    _write(cfg / "config.toml", f'''
[qualibrate]
project = "alpha"
version = 5

[qualibrate.storage]
location = "{storage.as_posix()}"

[quam]
state_path = "{chip_a.as_posix()}"
version = 3
''')
    _write(cfg / "projects" / "alpha" / "config.toml",
           f'[quam]\nstate_path = "{chip_a.as_posix()}"\n')
    _write(cfg / "projects" / "beta" / "config.toml",
           f'[quam]\nstate_path = "{shared.as_posix()}"\n')
    _write(cfg / "projects" / "gamma" / "config.toml",
           f'[quam]\nstate_path = "{shared.as_posix()}"\n')
    _write(cfg / "projects" / "delta" / "config.toml",
           f'[quam]\nstate_path = "{(tmp_path / "chips" / "missing").as_posix()}"\n')
    monkeypatch.setenv("QUALIBRATE_CONFIG_FILE", str(cfg))
    monkeypatch.delenv("QUALIBRATE_CONFIG_DIR", raising=False)
    qc._state_index_cache.clear()

    inst = tmp_path / "_inst"
    app = create_app(testing=True, instance_path=str(inst))
    return {"cfg": cfg, "chip_a": chip_a, "shared": shared,
            "standalone": standalone, "inst": inst,
            "app": app, "client": app.test_client()}


def _active_scope(app) -> str | None:
    name = app.config.get("active_context")
    ctx = (app.config.get("contexts") or {}).get(name) if name else None
    return (ctx or {}).get("qualibrate_project")


def _session(inst: Path) -> dict:
    p = inst / "last_session.json"
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}


class TestScopeDerivation:
    def test_plain_load_acquires_unique_project(self, scoped):
        scoped["client"].post("/load", data={"folder": str(scoped["chip_a"])})
        assert _active_scope(scoped["app"]) == "alpha"

    def test_last_project_persisted_on_acquire(self, scoped):
        scoped["client"].post("/load", data={"folder": str(scoped["chip_a"])})
        assert _session(scoped["inst"]).get("last_project") == "alpha"

    def test_standalone_folder_gets_no_scope(self, scoped):
        scoped["client"].post("/load", data={"folder": str(scoped["standalone"])})
        assert _active_scope(scoped["app"]) is None

    def test_standalone_load_does_not_clear_last_project(self, scoped):
        c = scoped["client"]
        c.post("/load", data={"folder": str(scoped["chip_a"])})
        c.post("/load", data={"folder": str(scoped["standalone"])})
        assert _session(scoped["inst"]).get("last_project") == "alpha"

    def test_ambiguous_state_path_refuses_to_guess(self, scoped):
        # beta AND gamma point at `shared`; neither is qualibrate-active
        scoped["client"].post("/load", data={"folder": str(scoped["shared"])})
        assert _active_scope(scoped["app"]) is None

    def test_active_project_wins_ambiguity(self, scoped):
        # flip qualibrate's active project to beta → the tie resolves to it
        _write(scoped["cfg"] / "config.toml", f'''
[qualibrate]
project = "beta"
version = 5

[quam]
state_path = "{scoped["chip_a"].as_posix()}"
version = 3
''')
        qc._state_index_cache.clear()
        scoped["client"].post("/load", data={"folder": str(scoped["shared"])})
        assert _active_scope(scoped["app"]) == "beta"


class TestScopePinning:
    def test_explicit_open_pins_ambiguous_chip(self, scoped):
        c = scoped["client"]
        r = c.post("/qualibrate/open", data={"project": "gamma"})
        assert r.status_code in (200, 302)
        assert _active_scope(scoped["app"]) == "gamma"
        assert _session(scoped["inst"]).get("last_project") == "gamma"

    def test_pin_survives_cache_hit_reactivation(self, scoped):
        c = scoped["client"]
        c.post("/qualibrate/open", data={"project": "gamma"})
        # plain /load of the same folder is a cache hit — must NOT re-derive
        # (the reverse-matcher would refuse the beta/gamma tie → None)
        c.post("/load", data={"folder": str(scoped["shared"])})
        assert _active_scope(scoped["app"]) == "gamma"

    def test_pin_reaches_snapshot_stamps(self, scoped):
        """Header truth == stamp truth (audit): beta/gamma share one folder,
        so the pure reverse match abstains — but the ctx's explicit pin must
        reach the meta.json stamp (_scope_for is memo-first)."""
        c = scoped["client"]
        c.post("/qualibrate/open", data={"project": "gamma"})
        r = c.post("/state-history/snapshot")
        assert r.status_code == 200
        hm = scoped["app"].config["history_manager"]
        snaps = hm.list_snapshots(scoped["shared"])
        assert snaps and snaps[0].project == "gamma"

    def test_reopen_refreshes_last_project(self, scoped):
        """Re-opening an already-scoped project must refresh the landing
        highlight even though the ctx memo already carries the name (audit:
        the memo guard used to skip _remember_last_project)."""
        c = scoped["client"]
        c.post("/qualibrate/open", data={"project": "gamma"})
        c.post("/load", data={"folder": str(scoped["chip_a"])})  # derive → alpha
        assert _session(scoped["inst"]).get("last_project") == "alpha"
        c.post("/qualibrate/open", data={"project": "gamma"})
        assert _session(scoped["inst"]).get("last_project") == "gamma"

    def test_eviction_rebuild_rederives(self, scoped):
        c = scoped["client"]
        c.post("/load", data={"folder": str(scoped["chip_a"])})
        assert _active_scope(scoped["app"]) == "alpha"
        # simulate LRU eviction / restart: drop the in-memory context wholesale
        with routes_mod._quam_cache_lock:
            routes_mod._quam_cache.clear()
        scoped["app"].config["contexts"].clear()
        scoped["app"].config["active_context"] = None
        c.post("/load", data={"folder": str(scoped["chip_a"])})
        assert _active_scope(scoped["app"]) == "alpha"


class TestReverseIndex:
    def test_index_lists_effective_paths(self, scoped):
        idx = qc.project_state_paths()
        by_name = dict(idx["projects"])
        assert idx["active"] == "alpha"
        assert Path(by_name["alpha"]) == scoped["chip_a"]
        assert Path(by_name["beta"]) == scoped["shared"]

    def test_index_is_stat_cached(self, scoped):
        qc.project_state_paths()
        entry1 = qc._state_index_cache.get("entry")
        qc.project_state_paths()
        assert qc._state_index_cache.get("entry") is entry1  # no rebuild

    def test_index_invalidates_on_overlay_change(self, scoped):
        qc.project_state_paths()
        _write(scoped["cfg"] / "projects" / "beta" / "config.toml",
               f'[quam]\nstate_path = "{scoped["standalone"].as_posix()}"\n')
        idx = qc.project_state_paths()
        assert Path(dict(idx["projects"])["beta"]) == scoped["standalone"]

    def test_missing_config_is_empty(self, tmp_path, monkeypatch):
        monkeypatch.setenv("QUALIBRATE_CONFIG_FILE", str(tmp_path / "ghost"))
        qc._state_index_cache.clear()
        assert qc.project_state_paths() == {"active": None, "projects": []}


class TestSidebarReorg:
    """Step-4 pins (docs/63): Projects first, State Load beneath, subnav
    expanded + restore-registered, palette entry."""

    _ROOT = Path(__file__).resolve().parent.parent

    def _src(self, rel):
        return (self._ROOT / rel).read_text(encoding="utf-8")

    def test_projects_block_first_then_load_then_generate(self):
        src = self._src("quam_state_manager/web/templates/base.html")
        assert (src.index('id="qualibrate-subnav"')
                < src.index('id="load-form"')
                < src.index('>Generate Config</a>'))

    def test_subnav_renders_expanded_and_is_restore_registered(self):
        base = self._src("quam_state_manager/web/templates/base.html")
        # the old page-gated collapse conditional is gone — server renders open
        assert "nav-subitems{% if page != 'qualibrate'" not in base
        # …and the restore registry now round-trips the collapse choice
        appjs = self._src("quam_state_manager/web/static/app.js")
        assert "{ id: 'qualibrate-subnav'" in appjs
        assert "quam_qualibrate_nav_collapsed" in base and \
               "quam_qualibrate_nav_collapsed" in appjs

    def test_command_palette_lists_projects(self):
        base = self._src("quam_state_manager/web/templates/base.html")
        assert '{"label": "Projects",          "url": "/qualibrate"}' in base

    def test_deleted_scope_hint_renders(self, scoped):
        c = scoped["client"]
        c.post("/qualibrate/open", data={"project": "beta"})
        # remove beta from qualibrate entirely
        import shutil
        shutil.rmtree(scoped["cfg"] / "projects" / "beta")
        qc._state_index_cache.clear()
        body = c.get("/qualibrate/subnav").get_data(as_text=True)
        assert "no longer a qualibrate project" in body
        assert "beta" in body


class TestHistoryLens:
    """Step-6 pins (docs/63): snapshots are STAMPED with the project scope at
    capture (meta.json-only, display-only); Param/State History gain scoped
    headers; every persisted key/URL contract stays raw."""

    def test_snapshot_meta_round_trip(self, tmp_path):
        from quam_state_manager.core.history import HistoryManager
        chip = _chip(tmp_path / "chip_rt")
        hm = HistoryManager(tmp_path / "inst_rt")
        meta = hm.check_and_snapshot(chip, "manual", force=True, project="alpha")
        assert meta is not None and meta.project == "alpha"
        # a FRESH manager re-reads meta.json from disk — the field survives
        hm2 = HistoryManager(tmp_path / "inst_rt")
        snaps = hm2.list_snapshots(chip)
        assert snaps and snaps[0].project == "alpha"

    def test_unstamped_snapshot_defaults_none(self, tmp_path):
        from quam_state_manager.core.history import HistoryManager
        chip = _chip(tmp_path / "chip_un")
        hm = HistoryManager(tmp_path / "inst_un")
        meta = hm.check_and_snapshot(chip, "manual", force=True)
        assert meta is not None and meta.project is None

    def test_pre_lens_meta_still_loads(self, tmp_path):
        """meta.json written BEFORE the field existed (no ``project`` key)
        must load with project=None — not disappear from State History."""
        from quam_state_manager.core.history import HistoryManager
        chip = _chip(tmp_path / "chip_old")
        hm = HistoryManager(tmp_path / "inst_old")
        meta = hm.check_and_snapshot(chip, "manual", force=True, project="alpha")
        meta_file = (tmp_path / "inst_old" / "history" / hm._key_for(chip)
                     / meta.timestamp / "meta.json")
        data = json.loads(meta_file.read_text(encoding="utf-8"))
        del data["project"]
        meta_file.write_text(json.dumps(data), encoding="utf-8")
        hm2 = HistoryManager(tmp_path / "inst_old")
        snaps = hm2.list_snapshots(chip)
        assert snaps and snaps[0].project is None

    def test_project_survives_annotate_rewrite(self, tmp_path):
        """label/pin updates rewrite meta.json in place — the stamp must
        survive the round-trip (annotate must keep raw-dict round-tripping,
        never reconstruct SnapshotMeta field-by-field)."""
        from quam_state_manager.core.history import HistoryManager
        chip = _chip(tmp_path / "chip_an")
        hm = HistoryManager(tmp_path / "inst_an")
        meta = hm.check_and_snapshot(chip, "manual", force=True, project="alpha")
        hm.annotate_snapshot(chip, meta.timestamp, label="baseline", pinned=True)
        hm.annotate_snapshot(chip, meta.timestamp, label=None, pinned=False)
        hm2 = HistoryManager(tmp_path / "inst_an")
        snaps = hm2.list_snapshots(chip)
        assert snaps and snaps[0].project == "alpha"

    def test_state_history_row_badge_and_header(self, scoped):
        c = scoped["client"]
        c.post("/qualibrate/open", data={"project": "alpha"})
        r = c.post("/state-history/snapshot")
        assert r.status_code == 200
        body = c.get("/state-history",
                     headers={"HX-Request": "true"}).get_data(as_text=True)
        assert "sh-project-badge" in body
        assert "scope-suffix" in body
        assert "alpha" in body

    def test_param_history_scoped_display_keeps_raw_keys(self, scoped):
        """The header + loaded selector chip show the project, but
        data-loaded-chip-key / ?chip_key= keep the RAW history key —
        the persisted URL/JS contract (docs/63 decision 7)."""
        import re
        c = scoped["client"]
        c.post("/qualibrate/open", data={"project": "alpha"})
        body = c.get("/param-history",
                     headers={"HX-Request": "true"}).get_data(as_text=True)
        hm = scoped["app"].config["history_manager"]
        raw_key = hm._key_for(scoped["chip_a"])
        m = re.search(r'data-loaded-chip-key="([^"]*)"', body)
        assert m and m.group(1) == raw_key
        assert f"alpha · {raw_key}" in body          # scoped display name
        assert f"/param-history?chip_key={raw_key}" in body  # raw URL contract
        assert "scope-suffix" in body and "Param History" in body

    def test_param_history_unscoped_is_plain(self, scoped):
        c = scoped["client"]
        c.post("/load", data={"folder": str(scoped["standalone"])})
        body = c.get("/param-history",
                     headers={"HX-Request": "true"}).get_data(as_text=True)
        assert "scope-suffix" not in body

    def test_hist_ref_resolves_for_stamped_snapshot(self, scoped):
        """hist:<chip>/<ts> compare refs must keep resolving for snapshots
        that carry the new meta field."""
        from quam_state_manager.core import compare_sources as cs
        c = scoped["client"]
        c.post("/qualibrate/open", data={"project": "alpha"})
        c.post("/state-history/snapshot")
        hm = scoped["app"].config["history_manager"]
        key = hm._key_for(scoped["chip_a"])
        snaps = hm.list_snapshots(scoped["chip_a"])
        assert snaps and snaps[0].project == "alpha"  # the route stamped it
        src = cs.resolve_source(
            f"hist:{key}/{snaps[0].timestamp}", cs.SourcePool(),
            history_root=scoped["inst"] / "history")
        assert src.chip_name == key


def _seed_run(root: Path, run_id: int, *, date="2026-05-28", hhmmss="010000",
              name="test_experiment", qubits=None) -> Path:
    """One qualibrate-shaped run folder under ``root/<date>/#<id>_<name>_<hhmmss>``."""
    qubits = qubits or [f"q{run_id}"]
    run = root / date / f"#{run_id}_{name}_{hhmmss}"
    run.mkdir(parents=True)
    (run / "node.json").write_text(json.dumps({
        "metadata": {"name": name, "status": "successful",
                     "run_start": f"{date}T01:00:00", "run_end": f"{date}T01:00:01"},
        "data": {"parameters": {"model": {"qubits": qubits}}, "outcomes": {}},
        "id": run_id, "parents": [], "created_at": f"{date}T01:00:00",
    }), encoding="utf-8")
    (run / "data.json").write_text(json.dumps(
        {"fit_results": {qubits[0]: {"T1": 8.0e-6}}}), encoding="utf-8")
    return run


def _script_json(body: str, el_id: str):
    import re
    m = re.search(rf'id="{el_id}"[^>]*>(.*?)</script>', body, re.S)
    assert m, f"missing <script id={el_id}>"
    return json.loads(m.group(1))


@pytest.fixture
def ds_scoped(scoped):
    """``scoped`` + alpha gets its OWN storage holding one dataset root."""
    tmp = scoped["cfg"].parent
    storage_alpha = tmp / "storage_alpha"
    data_a = storage_alpha / "data_a"
    _seed_run(data_a, 1, qubits=["q0"])
    _write(scoped["cfg"] / "projects" / "alpha" / "config.toml", f'''
[quam]
state_path = "{scoped["chip_a"].as_posix()}"

[qualibrate.storage]
location = "{storage_alpha.as_posix()}"
''')
    qc._state_index_cache.clear()
    scoped["storage_alpha"] = storage_alpha
    scoped["data_a"] = data_a
    return scoped


class TestDatasetLens:
    """Step-7 pins (docs/63): per-project dataset roots recorded on open
    (instance/project_dataset_roots.json), Datasets ships the scope payload,
    Trends pre-selects the scope's roots, remove strips the map — and
    scope=None stays byte-identical to today."""

    def _roots_map(self, inst: Path) -> dict:
        p = inst / "project_dataset_roots.json"
        return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}

    def test_open_records_project_roots(self, ds_scoped):
        c = ds_scoped["client"]
        c.post("/qualibrate/open", data={"project": "alpha"})
        m = self._roots_map(ds_scoped["inst"])
        assert [path_match.fs_key(r) for r in m.get("alpha", [])] == \
               [path_match.fs_key(ds_scoped["data_a"])]

    def test_open_empty_storage_records_nothing(self, ds_scoped):
        # gamma inherits the ROOT storage (an empty dir) → 0 roots → no entry
        c = ds_scoped["client"]
        c.post("/qualibrate/open", data={"project": "gamma"})
        assert "gamma" not in self._roots_map(ds_scoped["inst"])

    def test_datasets_page_ships_scope_payload(self, ds_scoped):
        c = ds_scoped["client"]
        c.post("/qualibrate/open", data={"project": "alpha"})
        other = ds_scoped["cfg"].parent / "other_data"
        _seed_run(other, 5, qubits=["q7"])
        c.post("/workspace/add", data={"folder": str(other)})
        body = c.get("/datasets", headers={"HX-Request": "true"}).get_data(as_text=True)
        assert 'data-scope="alpha"' in body
        key_a = routes_mod._folder_key(ds_scoped["data_a"])
        assert _script_json(body, "ds-scope-folders") == [key_a]
        assert "scoped to alpha" in body   # proper-subset hint

    def test_full_cover_scope_hides_hint(self, ds_scoped):
        # scope covers EVERY present folder → All ≡ scope → no hint
        c = ds_scoped["client"]
        c.post("/qualibrate/open", data={"project": "alpha"})
        body = c.get("/datasets", headers={"HX-Request": "true"}).get_data(as_text=True)
        assert 'data-scope="alpha"' in body
        assert "scoped to alpha" not in body

    def test_scope_none_identity(self, ds_scoped):
        c = ds_scoped["client"]
        c.post("/load", data={"folder": str(ds_scoped["standalone"])})
        other = ds_scoped["cfg"].parent / "other_data"
        _seed_run(other, 5, qubits=["q7"])
        c.post("/workspace/add", data={"folder": str(other)})
        body = c.get("/datasets", headers={"HX-Request": "true"}).get_data(as_text=True)
        assert 'data-scope=""' in body
        assert _script_json(body, "ds-scope-folders") == []
        assert "scoped to" not in body

    def test_remove_strips_project_map(self, ds_scoped):
        c = ds_scoped["client"]
        c.post("/qualibrate/open", data={"project": "alpha"})
        assert "alpha" in self._roots_map(ds_scoped["inst"])
        c.post("/workspace/remove", data={"folder": str(ds_scoped["data_a"])})
        assert "alpha" not in self._roots_map(ds_scoped["inst"])

    def test_trends_preselects_scope_folder(self, ds_scoped):
        c = ds_scoped["client"]
        c.post("/qualibrate/open", data={"project": "alpha"})
        other = ds_scoped["cfg"].parent / "other_data"
        _seed_run(other, 5, qubits=["q7"])
        c.post("/workspace/add", data={"folder": str(other)})
        body = c.get("/trends", headers={"HX-Request": "true"}).get_data(as_text=True)
        key_a = routes_mod._folder_key(ds_scoped["data_a"])
        key_o = routes_mod._folder_key(other)
        assert f'class="folder-chip active" data-folder-key="{key_a}"' in body
        assert f'class="folder-chip" data-folder-key="{key_o}"' in body
        assert "scoped to alpha" in body

    def test_trends_same_chip_scope_selects_all(self, ds_scoped):
        # second scope root, SAME qubit set → same chip → both pre-selected
        data_b = ds_scoped["storage_alpha"] / "data_b"
        _seed_run(data_b, 9, date="2026-06-01", qubits=["q0"])
        c = ds_scoped["client"]
        c.post("/qualibrate/open", data={"project": "alpha"})
        body = c.get("/trends", headers={"HX-Request": "true"}).get_data(as_text=True)
        for key in (routes_mod._folder_key(ds_scoped["data_a"]),
                    routes_mod._folder_key(data_b)):
            assert f'class="folder-chip active" data-folder-key="{key}"' in body

    def test_trends_cross_chip_scope_picks_newest(self, ds_scoped):
        # second scope root with a DIFFERENT chip → only the newest starts selected
        data_b = ds_scoped["storage_alpha"] / "data_b"
        _seed_run(data_b, 9, date="2026-06-01", qubits=["q9"])
        c = ds_scoped["client"]
        c.post("/qualibrate/open", data={"project": "alpha"})
        body = c.get("/trends", headers={"HX-Request": "true"}).get_data(as_text=True)
        key_a = routes_mod._folder_key(ds_scoped["data_a"])
        key_b = routes_mod._folder_key(data_b)
        assert f'class="folder-chip active" data-folder-key="{key_b}"' in body
        assert f'class="folder-chip" data-folder-key="{key_a}"' in body

    def test_trends_unscoped_keeps_first_folder_default(self, ds_scoped):
        c = ds_scoped["client"]
        c.post("/load", data={"folder": str(ds_scoped["standalone"])})
        other = ds_scoped["cfg"].parent / "other_data"
        _seed_run(other, 5, qubits=["q7"])
        c.post("/workspace/add", data={"folder": str(other)})
        c.post("/workspace/add", data={"folder": str(ds_scoped["data_a"])})
        body = c.get("/trends", headers={"HX-Request": "true"}).get_data(as_text=True)
        assert body.count('class="folder-chip active"') == 1   # today's default

    def test_client_seeding_source_pins(self):
        js = (Path(__file__).resolve().parent.parent
              / "quam_state_manager/web/static/dataset-virtual.js"
              ).read_text(encoding="utf-8")
        assert "data.getAttribute('data-scope')" in js   # scope joins the identity key
        assert "ds-scope-folders" in js                  # seeding source element
        assert "sk.length < known" in js                 # proper-subset guard


class TestTrayBadge:
    """Step-8 pins (docs/63 decision 10): the ⚗ badge shows SM's scope, appends
    the qualibrate-active project muted when they differ, never re-colors for
    a mere scope difference, and hides only without config or (no active AND
    no scope)."""

    def test_scope_shown_with_differing_active(self, scoped):
        c = scoped["client"]
        c.post("/qualibrate/open", data={"project": "gamma"})   # active is alpha
        body = c.get("/qubits").get_data(as_text=True)
        assert "qualibrate-tray-badge" in body
        assert "gamma" in body
        assert "(qualibrate: alpha)" in body

    def test_scope_equal_active_no_note(self, scoped):
        c = scoped["client"]
        c.post("/qualibrate/open", data={"project": "alpha"})
        body = c.get("/qubits").get_data(as_text=True)
        assert "qualibrate-tray-badge" in body
        assert "(qualibrate:" not in body
        # same chip as qualibrate's write target → no mismatch color either
        assert "qualibrate-tray-warn" not in body

    def test_scope_only_badge_when_no_active(self, scoped):
        # qualibrate has NO active project — a scope-only badge, never danger
        _write(scoped["cfg"] / "config.toml", f'''
[qualibrate]
version = 5

[quam]
state_path = "{scoped["chip_a"].as_posix()}"
version = 3
''')
        qc._tray_cache.clear()
        qc._state_index_cache.clear()
        c = scoped["client"]
        c.post("/qualibrate/open", data={"project": "gamma"})
        body = c.get("/qubits").get_data(as_text=True)
        assert "qualibrate-tray-badge" in body
        assert "gamma" in body
        assert "qualibrate-tray-danger" not in body
        assert "(qualibrate:" not in body

    def test_no_badge_without_config(self, tmp_path, monkeypatch):
        monkeypatch.setenv("QUALIBRATE_CONFIG_FILE", str(tmp_path / "ghost"))
        qc._tray_cache.clear()
        qc._state_index_cache.clear()
        app = create_app(testing=True, instance_path=str(tmp_path / "_inst"))
        body = app.test_client().get("/").get_data(as_text=True)
        assert "qualibrate-tray-badge" not in body


class TestLanding:
    """Step-5 pins (docs/63): project-first landing with lazy cards; welcome
    verbatim without a config; Resume card; /qubits redirect."""

    def test_landing_shell_with_config(self, scoped):
        body = scoped["client"].get("/").get_data(as_text=True)
        assert 'id="landing-cards"' in body
        assert 'hx-get="/landing/projects"' in body
        # the shell must NOT inline the project list (lazy fragment only)
        assert "landing-card-grid" not in body
        # greeting + question header (user feedback), NOT the legacy welcome
        # page (its lead paragraph is unique to _landing_welcome.html)
        assert "what's your project?" in body
        assert "welcome-lead" not in body
        # the manual rides below the picker as a collapsible section
        assert 'class="landing-gs"' in body
        assert "Getting started" in body

    def test_getting_started_open_only_on_first_run(self, scoped):
        c = scoped["client"]
        # fresh instance, no session history at all → manual starts expanded
        body = c.get("/").get_data(as_text=True)
        assert '<details class="landing-gs" open>' in body
        # any history (a load) collapses it on the next landing render
        c.post("/load", data={"folder": str(scoped["chip_a"])})
        body = c.get("/").get_data(as_text=True)
        assert '<details class="landing-gs">' in body
        assert '<details class="landing-gs" open>' not in body

    def test_home_tolerates_type_corrupt_session(self, scoped):
        """last_session.json is hand-editable — non-string path entries must
        degrade to 'no history', never TypeError GET / (audit)."""
        (scoped["inst"] / "last_session.json").write_text(json.dumps({
            "last_quam_state_path": 123,
            "recent_quam_state_paths": ["not-a-chip", 7, None],
            "last_project": ["weird"],
        }), encoding="utf-8")
        r = scoped["client"].get("/")
        assert r.status_code == 200

    def test_landing_cards_fragment(self, scoped):
        body = scoped["client"].get("/landing/projects").get_data(as_text=True)
        assert "landing-card-grid" in body
        for name in ("alpha", "beta", "gamma", "delta"):
            assert name in body
        # the dangling project's Open button is disabled with guidance
        assert "disabled" in body and "fix it in QUAlibrate first" in body

    def test_landing_welcome_without_config(self, tmp_path, monkeypatch):
        monkeypatch.setenv("QUALIBRATE_CONFIG_FILE", str(tmp_path / "ghost"))
        qc._state_index_cache.clear()
        qc._tray_cache.clear()
        app = create_app(testing=True, instance_path=str(tmp_path / "_inst"))
        body = app.test_client().get("/").get_data(as_text=True)
        assert "Welcome to QUAM State Manager" in body
        assert 'id="landing-cards"' not in body

    def test_resume_card_after_a_session(self, scoped):
        c = scoped["client"]
        c.post("/load", data={"folder": str(scoped["chip_a"])})
        body = c.get("/").get_data(as_text=True)
        assert "landing-resume" in body
        assert "Resume" in body and "alpha" in body

    def test_continue_highlight_on_last_project(self, scoped):
        c = scoped["client"]
        c.post("/qualibrate/open", data={"project": "beta"})
        body = c.get("/landing/projects").get_data(as_text=True)
        assert "landing-card-last" in body
        assert "Continue</button>" in body

    def test_open_redirects_to_qubits(self, scoped):
        r = scoped["client"].post("/qualibrate/open", data={"project": "alpha"},
                                  headers={"HX-Request": "true"})
        assert r.status_code == 200
        assert r.headers.get("HX-Redirect", "").endswith("/qubits")
