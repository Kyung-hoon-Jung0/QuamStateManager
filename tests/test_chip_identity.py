"""Chip-identity ladder (docs/20 v2): extras chip_name > fingerprint > path.

The wild failure this fixes: 7 different chips as sibling folders under one
parent — ``chip_name_for`` collapsed them all onto the parent's name, capture
fingerprint-forked snapshots into ``_alt_`` dirs while every READ (page,
drawer, field-history) kept using the path-derived dir, i.e. another chip's
history. The ladder makes every read and write resolve through ONE choke
point, with the user-declared ``state["extras"]["chip_name"]`` as tier 1."""

from __future__ import annotations

import itertools
import json
import os
import time
from pathlib import Path

import pytest

from quam_state_manager.core.history import (
    HistoryManager,
    extras_chip_name,
    extras_data_folder,
    identity_of,
)


def _state(qubit="qA1", chip_name=None, data_folder=None, f01=5.0e9):
    s = {"qubits": {qubit: {"id": qubit, "f_01": f01}}, "qubit_pairs": {}}
    extras = {}
    if chip_name is not None:
        extras["chip_name"] = chip_name
    if data_folder is not None:
        extras["data_folder"] = data_folder
    if extras:
        s["extras"] = extras
    return s


def _wiring(host):
    return {"wiring": {}, "network": {"host": host, "cluster_name": "C"}}


_MTIME_TICK = itertools.count(1)


def _write(folder: Path, state: dict, wiring: dict) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "state.json").write_text(json.dumps(state), encoding="utf-8")
    (folder / "wiring.json").write_text(json.dumps(wiring), encoding="utf-8")
    # Tests rewrite the same files within one second; advance mtimes so the
    # identity/fingerprint mtime caches always observe the change (production
    # rewrites go through os.replace with genuinely new mtimes).
    t = time.time() + 2 * next(_MTIME_TICK)
    for f in ("state.json", "wiring.json"):
        os.utime(folder / f, (t, t))
    return folder


@pytest.fixture
def hm(tmp_path):
    return HistoryManager(tmp_path / "inst", max_snapshots=50, cache_size=3)


class TestExtrasReaders:
    @pytest.mark.parametrize("state,expect", [
        (None, None),
        ([], None),
        ({}, None),
        ({"extras": None}, None),
        ({"extras": "gilboa"}, None),
        ({"extras": {}}, None),
        ({"extras": {"chip_name": None}}, None),
        ({"extras": {"chip_name": 42}}, None),
        ({"extras": {"chip_name": ""}}, None),
        ({"extras": {"chip_name": "   "}}, None),
        ({"extras": {"chip_name": " gilboa "}}, "gilboa"),
        ({"extras": {"chip_name": "x" * 200}}, "x" * 64),
    ])
    def test_chip_name_guards(self, state, expect):
        assert extras_chip_name(state) == expect

    @pytest.mark.parametrize("state,expect", [
        (None, []),
        ({"extras": {}}, []),
        ({"extras": {"data_folder": 42}}, []),
        ({"extras": {"data_folder": "D:/data"}}, ["D:/data"]),
        ({"extras": {"data_folder": ["a", "", 3, " b "]}}, ["a", "b"]),
    ])
    def test_data_folder_guards(self, state, expect):
        assert extras_data_folder(state) == expect

    def test_identity_of_missing_files(self, tmp_path):
        ident = identity_of(tmp_path / "nope" / "quam_state")
        assert ident.name is None and ident.fingerprint is None
        assert ident.path_name == "nope"
        assert ident.source == "path"


class TestLadderTier1:
    def test_named_chip_gets_pretty_dir_from_day_one(self, hm, tmp_path):
        p = _write(tmp_path / "labX" / "quam_a",
                   _state(chip_name="gilboa"), _wiring("10.0.0.1"))
        meta = hm.check_and_snapshot(p, "manual", force=True)
        assert meta is not None
        root = tmp_path / "inst" / "history"
        assert (root / "gilboa" / meta.timestamp).is_dir()
        assert hm._key_for(p) == "gilboa"
        assert hm.display_name_for_dir("gilboa") == "gilboa"

    def test_two_named_siblings_never_collapse(self, hm, tmp_path):
        """The user's 7-sibling scenario, with names: each chip gets its own
        pretty dir even though chip_name_for keys both to the parent."""
        pa = _write(tmp_path / "labX" / "quam_a",
                    _state("qA1", chip_name="gilboa"), _wiring("10.0.0.1"))
        pb = _write(tmp_path / "labX" / "quam_b",
                    _state("qB1", chip_name="deborah"), _wiring("10.0.0.2"))
        ma = hm.check_and_snapshot(pa, "manual", force=True)
        mb = hm.check_and_snapshot(pb, "manual", force=True)
        root = tmp_path / "inst" / "history"
        assert (root / "gilboa" / ma.timestamp).is_dir()
        assert (root / "deborah" / mb.timestamp).is_dir()
        assert hm._key_for(pa) == "gilboa" and hm._key_for(pb) == "deborah"

    def test_adoption_keeps_history_continuity(self, hm, tmp_path):
        """A chip with an EXISTING fingerprint-forked alt dir gains a name:
        the ladder adopts the old dir (display renamed) — never orphans."""
        pa = _write(tmp_path / "labX" / "quam_a",
                    _state("qA1"), _wiring("10.0.0.1"))
        pb = _write(tmp_path / "labX" / "quam_b",
                    _state("qB1"), _wiring("10.0.0.2"))
        hm.check_and_snapshot(pa, "manual", force=True)     # base dir "labX"
        mb = hm.check_and_snapshot(pb, "manual", force=True)  # alt fork
        root = tmp_path / "inst" / "history"
        alt = next(d for d in root.iterdir()
                   if d.is_dir() and d.name != "labX")
        assert (alt / mb.timestamp).is_dir()
        # Now the user names chip B.
        _write(tmp_path / "labX" / "quam_b",
               _state("qB1", chip_name="deborah", f01=5.1e9), _wiring("10.0.0.2"))
        assert hm._key_for(pb) == alt.name, "adopt the existing dir"
        assert hm.display_name_for_dir(alt.name) == "deborah"
        mb2 = hm.check_and_snapshot(pb, "manual", force=True)
        assert (alt / mb2.timestamp).is_dir(), "new snapshots continue there"

    def test_rename_follows_fingerprint_and_keeps_old_alias(self, hm, tmp_path):
        p = _write(tmp_path / "labX" / "quam_a",
                   _state(chip_name="gilboa"), _wiring("10.0.0.1"))
        hm.check_and_snapshot(p, "manual", force=True)
        _write(tmp_path / "labX" / "quam_a",
               _state(chip_name="mount_g", f01=5.2e9), _wiring("10.0.0.1"))
        assert hm._key_for(p) == "gilboa", \
            "rename adopts the OLD dir via fingerprint (history continuity)"
        assert hm.display_name_for_dir("gilboa") == "mount_g"
        # legacy URL with the old name key still resolves to the same dir
        legacy = Path("/__chip_key__") / "gilboa" / "quam_state"
        assert hm._history_dir(legacy).name == "gilboa"

    def test_name_conflict_never_merges_two_chips(self, hm, tmp_path):
        pa = _write(tmp_path / "labX" / "quam_a",
                    _state("qA1", chip_name="gilboa"), _wiring("10.0.0.1"))
        hm.check_and_snapshot(pa, "manual", force=True)
        pb = _write(tmp_path / "labY" / "quam_b",
                    _state("qB1", chip_name="gilboa"), _wiring("10.0.0.9"))
        dir_b, key_b, source_b, swap_b = hm.resolve_chip_dir(pb)
        assert key_b != "gilboa", "second fingerprint must not enter the claimed dir"
        assert (swap_b or {}).get("type") == "name_conflict"
        mb = hm.check_and_snapshot(pb, "manual", force=True)
        assert not (tmp_path / "inst" / "history" / "gilboa" / mb.timestamp).is_dir()

    def test_content_based_routing_beats_stale_live_reads(self, hm, tmp_path):
        """Capture routes by the CONTENT it snapshots (race-proof)."""
        p = _write(tmp_path / "labX" / "quam_a",
                   _state("qA1"), _wiring("10.0.0.1"))
        d, key, source, swap = hm.resolve_chip_dir_for_content(
            p, _state("qZ9", chip_name="other"), _wiring("10.9.9.9"))
        assert key == "other" and source == "extras"


class TestLadderLegacyParity:
    def test_unnamed_chips_route_exactly_as_before(self, hm, tmp_path):
        """No extras names → byte-identical legacy behavior: path key for the
        first chip, fingerprint alt-fork for the colliding sibling."""
        pa = _write(tmp_path / "labX" / "quam_a",
                    _state("qA1"), _wiring("10.0.0.1"))
        pb = _write(tmp_path / "labX" / "quam_b",
                    _state("qB1"), _wiring("10.0.0.2"))
        ma = hm.check_and_snapshot(pa, "manual", force=True)
        mb = hm.check_and_snapshot(pb, "manual", force=True)
        root = tmp_path / "inst" / "history"
        assert (root / "labX" / ma.timestamp).is_dir()
        assert mb.chip_swap_detected is not None
        alt_key = mb.chip_swap_detected["to_key"]
        assert alt_key.startswith("labX_alt_")
        assert (root / alt_key / mb.timestamp).is_dir()
        # READS now follow the routed dirs too (the gilboa bug):
        assert hm._key_for(pa) == "labX"
        assert hm._key_for(pb) == alt_key

    def test_synthetic_chip_key_path_passthrough(self, hm, tmp_path):
        pa = _write(tmp_path / "labX" / "quam_a",
                    _state("qA1"), _wiring("10.0.0.1"))
        hm.check_and_snapshot(pa, "manual", force=True)
        synthetic = Path("/__chip_key__") / "labX" / "quam_state"
        assert hm._key_for(synthetic) == "labX"
        assert hm._history_dir(synthetic) == tmp_path / "inst" / "history" / "labX"


class TestV3IndexMigration:
    """The wild corruption, synthesized exactly: base dir holds chipA's
    folders plus BOTH chips' index rows; the alt dir holds chipB's folders
    and NO index. v3 moves provably-foreign rows next to their folders,
    keeps orphans (pruned snapshots), skips ambiguous timestamps."""

    TS_A = "20260101_000000_0001"      # chipA — stays in base
    TS_B = "20260102_000000_0002"      # chipB — must move to the alt dir
    TS_ORPHAN = "20260103_000000_0003"  # no folder anywhere — pruned, keep
    TS_AMBIG = "20260104_000000_0004"   # folder in TWO other dirs — skip

    def _insert(self, idx, ts, qubit):
        import sqlite3
        con = sqlite3.connect(str(idx))
        try:
            con.execute(
                "INSERT OR REPLACE INTO param_history VALUES (?,?,?,?,?,?,?,?)",
                (ts, qubit, "f_01", 5.0e9, None, "auto", None, None))
            con.commit()
        finally:
            con.close()

    def _ts_set(self, idx):
        import sqlite3
        if not idx.exists():
            return set()
        con = sqlite3.connect(str(idx))
        try:
            return {r[0] for r in con.execute(
                "SELECT DISTINCT timestamp FROM param_history")}
        finally:
            con.close()

    def _seed(self, tmp_path):
        from quam_state_manager.core.history import _ensure_param_history_schema
        inst = tmp_path / "inst"
        root = inst / "history"
        base, alt = root / "labX", root / "labX_alt_10_0_0_2_1q"
        other = root / "labY"
        for d, ts_list, qubit, host in (
                (base, [self.TS_A], "qA1", "10.0.0.1"),
                (alt, [self.TS_B, self.TS_AMBIG], "qB1", "10.0.0.2"),
                (other, [self.TS_AMBIG], "qC1", "10.0.0.3")):
            for ts in ts_list:
                _write(d / ts, _state(qubit), _wiring(host))
        idx = base / "index.sqlite"
        _ensure_param_history_schema(idx)
        for ts, q in ((self.TS_A, "qA1"), (self.TS_B, "qB1"),
                      (self.TS_ORPHAN, "qA1"), (self.TS_AMBIG, "qA1")):
            self._insert(idx, ts, q)
        return inst, base, alt

    def test_moves_foreign_rows_next_to_their_folders(self, tmp_path):
        from quam_state_manager.core.history import migrate_index_attribution_v3
        inst, base, alt = self._seed(tmp_path)
        out = migrate_index_attribution_v3(inst)
        assert out["status"] == "migrated"
        assert out["moved_timestamps"] == 1
        assert out["ambiguous_skipped"] == 1
        base_ts = self._ts_set(base / "index.sqlite")
        assert self.TS_B not in base_ts, "foreign rows moved out"
        assert {self.TS_A, self.TS_ORPHAN, self.TS_AMBIG} <= base_ts, \
            "own + orphan (pruned) + ambiguous rows all kept"
        alt_ts = self._ts_set(alt / "index.sqlite")
        assert alt_ts == {self.TS_B}, "alt dir gained its own index"
        # idempotence: flag written, second run is a no-op
        assert (inst / "migrated_v3.flag").exists()
        again = migrate_index_attribution_v3(inst)
        assert again["status"] == "already_migrated"
        assert self._ts_set(base / "index.sqlite") == base_ts

    def test_indexless_dir_visible_in_chip_histories(self, tmp_path):
        hm = HistoryManager(tmp_path / "inst", max_snapshots=50, cache_size=3)
        root = tmp_path / "inst" / "history"
        alt = root / "labX_alt_10_0_0_2_1q"
        _write(alt / "20260102_000000_0002", _state("qB1"), _wiring("10.0.0.2"))
        rows = hm.list_chip_histories()
        row = next((r for r in rows if r["key"] == alt.name), None)
        assert row is not None, "a dir with folders but no index must be listed"
        assert row["snapshot_count"] == 1
        assert row["latest_timestamp"] == "20260102_000000_0002"


class TestChipNamePrompt:
    """First-open banner: live unnamed chips only; staging goes through the
    working copy (live bytes untouched until Apply); declines are
    fingerprint-keyed and never nag again."""

    def _env(self, tmp_path, state=None):
        from quam_state_manager.web.app import create_app
        live = _write(tmp_path / "chips" / "live",
                      state or _state("qA1"), _wiring("10.0.0.1"))
        app = create_app(testing=True, instance_path=str(tmp_path / "_inst"))
        c = app.test_client()
        r = c.post("/load", data={"folder": str(live)})
        assert r.status_code in (200, 302)
        return app, c, live

    def test_banner_shows_for_unnamed_live_chip(self, tmp_path):
        _app, c, _live = self._env(tmp_path)
        html = c.get("/qubits").data.decode()
        assert "chip-name-banner" in html
        assert 'hx-post="/chip-name/set"' in html

    def test_named_chip_never_prompts(self, tmp_path):
        _app, c, _live = self._env(tmp_path, _state("qA1", chip_name="gilboa"))
        assert "chip-name-banner" not in c.get("/qubits").data.decode()

    def test_set_stages_into_working_copy_only(self, tmp_path):
        app, c, live = self._env(tmp_path)
        live_bytes = (live / "state.json").read_bytes()
        r = c.post("/chip-name/set", data={"name": "gilboa",
                                           "data_folder": "D:/data/root"})
        assert r.status_code == 200
        # staged in the store (working copy), live bytes untouched
        name = app.config["contexts"]
        ctx = next(iter(name.values()))
        st = ctx["store"].state
        assert st["extras"]["chip_name"] == "gilboa"
        assert st["extras"]["data_folder"] == "D:/data/root"
        assert (live / "state.json").read_bytes() == live_bytes, \
            "never a direct live write — Apply is the only path"
        # the NAME prompt is gone on the next render; the unreachable folder
        # staged above now surfaces the r10 FIXABLE dangling strip instead
        html = c.get("/qubits").data.decode()
        assert 'hx-post="/chip-name/set"' not in html
        assert "cnb-df-form" in html

    def test_decline_memo_survives_reactivation(self, tmp_path):
        _app, c, live = self._env(tmp_path)
        html = c.get("/qubits").data.decode()
        import re as _re
        token = _re.search(r'name="token" value="([^"]+)"', html).group(1)
        c.post("/chip-name/decline", data={"token": token})
        assert "chip-name-banner" not in c.get("/qubits").data.decode()
        # re-activation (fresh /load) re-evaluates the gate — still declined
        c.post("/load", data={"folder": str(live)})
        assert "chip-name-banner" not in c.get("/qubits").data.decode()
        memo = json.loads(
            (tmp_path / "_inst" / "chip_name_prompts.json").read_text())
        assert token in memo

    def test_archive_ctx_never_prompts_unit(self, tmp_path):
        """Origin gate at the unit level: an archive ctx must clear the memo."""
        from quam_state_manager.web.app import create_app
        from quam_state_manager.web import routes as routes_mod
        app = create_app(testing=True, instance_path=str(tmp_path / "_inst2"))
        with app.test_request_context("/"):
            ctx = {"type": "quam", "origin": "dataset_archive",
                   "path": str(tmp_path / "x"), "store": object(),
                   "chip_name_prompt": {"stale": True}}
            routes_mod._maybe_chip_name_prompt(ctx)
            assert ctx["chip_name_prompt"] is None


class TestExtrasDataFolderPairing:
    """extras.data_folder auto-pairs the chip with its experiment data:
    declared roots become workspace roots on activation (existence-gated,
    dialect-bridged); unreachable values surface as a muted note only."""

    def _env(self, tmp_path, state):
        from quam_state_manager.web.app import create_app
        live = _write(tmp_path / "chips" / "live", state, _wiring("10.0.0.1"))
        app = create_app(testing=True, instance_path=str(tmp_path / "_inst"))
        c = app.test_client()
        r = c.post("/load", data={"folder": str(live)})
        assert r.status_code in (200, 302)
        ctx = next(iter(app.config["contexts"].values()))
        return app, c, ctx

    def test_declared_folder_becomes_workspace_root(self, tmp_path):
        data_root = tmp_path / "data"
        (data_root / "2026-07-01").mkdir(parents=True)
        app, _c, ctx = self._env(tmp_path, _state(
            "qA1", chip_name="gilboa", data_folder=str(data_root)))
        roots = {str(p) for p in app.config["workspace"].root_folders}
        assert str(data_root) in roots
        assert ctx["extras_data_roots"] == [str(data_root)]
        assert ctx["extras_data_dangling"] == []

    def test_dangling_folder_muted_note_only(self, tmp_path):
        app, c, ctx = self._env(tmp_path, _state(
            "qA1", chip_name="gilboa",
            data_folder=str(tmp_path / "nope" / "missing")))
        assert ctx["extras_data_roots"] == []
        assert len(ctx["extras_data_dangling"]) == 1
        html = c.get("/qubits").data.decode()
        assert "cnb-dangling" in html
        assert "isn't reachable" in html

    def test_archive_ctx_never_adopts_unit(self, tmp_path):
        from quam_state_manager.web.app import create_app
        from quam_state_manager.web import routes as routes_mod
        app = create_app(testing=True, instance_path=str(tmp_path / "_inst2"))
        with app.test_request_context("/"):
            ctx = {"type": "quam", "origin": "dataset_archive",
                   "path": str(tmp_path / "x"), "store": object()}
            routes_mod._adopt_extras_data_folders(ctx)
            assert ctx["extras_data_roots"] == []


# ── r10: data-folder validate / fix / suggest ──────────────────────────────

def _qcfg(tmp_path, monkeypatch, live, storage):
    """Fabricate a qualibrate config whose active project 'alpha' points at
    *live* with *storage* as its data location (as_posix — backslashes are
    TOML escapes)."""
    import quam_state_manager.core.qualibrate_config as qc
    cfg = tmp_path / ".qualibrate"
    (cfg / "projects" / "alpha").mkdir(parents=True, exist_ok=True)
    (cfg / "config.toml").write_text(f'''
[qualibrate]
project = "alpha"
version = 5

[qualibrate.storage]
location = "{storage.as_posix()}"

[quam]
state_path = "{live.as_posix()}"
version = 3
''', encoding="utf-8")
    (cfg / "projects" / "alpha" / "config.toml").write_text(
        f'[quam]\nstate_path = "{live.as_posix()}"\n', encoding="utf-8")
    monkeypatch.setenv("QUALIBRATE_CONFIG_FILE", str(cfg))
    monkeypatch.delenv("QUALIBRATE_CONFIG_DIR", raising=False)
    qc._state_index_cache.clear()


def _df_env(tmp_path, state, *, monkeypatch=None, storage=None):
    """App + loaded live chip; optionally under a qualibrate project scope."""
    from quam_state_manager.web.app import create_app
    live = _write(tmp_path / "chips" / "live", state, _wiring("10.0.0.1"))
    if monkeypatch is not None and storage is not None:
        _qcfg(tmp_path, monkeypatch, live, storage)
    app = create_app(testing=True, instance_path=str(tmp_path / "_inst"))
    c = app.test_client()
    r = c.post("/load", data={"folder": str(live)})
    assert r.status_code in (200, 302)
    ctx = next(iter(app.config["contexts"].values()))
    return app, c, live, ctx


class TestDataFolderValidator:
    def test_kinds(self, tmp_path):
        from quam_state_manager.web.app import create_app
        from quam_state_manager.web import routes as routes_mod
        app = create_app(testing=True, instance_path=str(tmp_path / "_i"))
        with app.test_request_context("/"):
            v = routes_mod._validate_data_folder(str(tmp_path))
            assert v["ok"] and v["kind"] == "ok"
            assert routes_mod._validate_data_folder(
                "gilboa_iqcc")["kind"] == "not_a_path"
            assert routes_mod._validate_data_folder(
                "data/sub")["kind"] == "not_a_path"
            assert routes_mod._validate_data_folder(
                "/nonexistent_xyz_123/gilboa")["kind"] == "cross_machine"
            assert routes_mod._validate_data_folder("")["kind"] == "not_a_path"
            assert routes_mod._validate_data_folder(
                "C:" + "\\" + "no_such_dir_zz")["kind"] == "cross_machine"
            # garbage must classify, never raise
            weird = "/bad" + chr(0) + "byte"
            assert routes_mod._validate_data_folder(weird)["kind"] in (
                "not_a_path", "cross_machine")


class TestChipDataFolderSet:
    def test_valid_dir_stages_and_adopts(self, tmp_path):
        data_root = tmp_path / "data"
        (data_root / "2026-07-01").mkdir(parents=True)
        app, c, live, ctx = _df_env(tmp_path, _state("qA1", chip_name="g"))
        live_bytes = (live / "state.json").read_bytes()
        r = c.post("/chip-data-folder/set", data={"value": str(data_root)})
        assert r.status_code == 200, r.data
        assert ctx["store"].state["extras"]["data_folder"] == str(data_root)
        assert (live / "state.json").read_bytes() == live_bytes, \
            "stage only — Apply is the only live write"
        roots = {str(p) for p in app.config["workspace"].root_folders}
        assert str(data_root) in roots, "reachable value pairs Datasets NOW"
        assert ctx["extras_data_dangling"] == []

    def test_cross_machine_confirm_roundtrip(self, tmp_path):
        app, c, _live, ctx = _df_env(tmp_path, _state("qA1", chip_name="g"))
        missing = str(tmp_path / "missing_zz")
        r = c.post("/chip-data-folder/set", data={"value": missing})
        assert r.status_code == 409
        body = r.data.decode()
        assert "force_cross" in body and missing in body
        assert len(ctx["store"].change_log) == 0, "nothing staged pre-confirm"
        r2 = c.post("/chip-data-folder/set",
                    data={"value": missing, "force_cross": "1"})
        assert r2.status_code == 200
        assert ctx["store"].state["extras"]["data_folder"] == missing
        html = c.get("/qubits").data.decode()
        assert "cnb-df-form" in html, "dangling banner now carries the fix form"

    def test_not_a_path_rejected(self, tmp_path):
        _app, c, _live, ctx = _df_env(tmp_path, _state("qA1", chip_name="g"))
        r = c.post("/chip-data-folder/set", data={"value": "gilboa_iqcc"})
        assert r.status_code == 400
        assert "looks like a name" in r.data.decode()
        assert len(ctx["store"].change_log) == 0
        # force never overrides the not-a-path gate
        r2 = c.post("/chip-data-folder/set",
                    data={"value": "gilboa_iqcc", "force_cross": "1"})
        assert r2.status_code == 400

    def test_clear_deletes_and_memoizes(self, tmp_path):
        data_root = tmp_path / "data"
        data_root.mkdir()
        _app, c, _live, ctx = _df_env(
            tmp_path, _state("qA1", chip_name="g", data_folder=str(data_root)))
        r = c.post("/chip-data-folder/set", data={"clear": "1"})
        assert r.status_code == 200
        assert "data_folder" not in ctx["store"].state.get("extras", {})
        memo = json.loads(
            (tmp_path / "_inst" / "chip_name_prompts.json").read_text())
        assert any(k.endswith("::datafolder") for k in memo), \
            "explicit clear must not immediately re-suggest"
        assert "cnb-suggest" not in c.get("/qubits").data.decode()

    def test_clear_without_key_is_noop(self, tmp_path):
        _app, c, _live, ctx = _df_env(tmp_path, _state("qA1", chip_name="g"))
        r = c.post("/chip-data-folder/set", data={"clear": "1"})
        assert r.status_code == 200
        assert "nothing to clear" in r.data.decode()
        assert len(ctx["store"].change_log) == 0

    def test_empty_submit_is_never_a_clear(self, tmp_path):
        data_root = tmp_path / "data"
        data_root.mkdir()
        _app, c, _live, ctx = _df_env(
            tmp_path, _state("qA1", chip_name="g", data_folder=str(data_root)))
        r = c.post("/chip-data-folder/set", data={"value": ""})
        assert r.status_code == 400
        assert ctx["store"].state["extras"]["data_folder"] == str(data_root)

    def test_archive_origin_409(self, tmp_path):
        _app, c, _live, ctx = _df_env(tmp_path, _state("qA1", chip_name="g"))
        ctx["origin"] = "dataset_archive"
        r = c.post("/chip-data-folder/set", data={"value": str(tmp_path)})
        assert r.status_code == 409

    def test_list_value_replaced_wholesale(self, tmp_path):
        data_root = tmp_path / "data"
        data_root.mkdir()
        _app, c, _live, ctx = _df_env(
            tmp_path, _state("qA1", chip_name="g",
                             data_folder=["nameA", "nameB"]))
        r = c.post("/chip-data-folder/set", data={"value": str(data_root)})
        assert r.status_code == 200, r.data
        assert ctx["store"].state["extras"]["data_folder"] == str(data_root)


class TestDanglingBannerActions:
    def test_fix_form_with_project_candidate(self, tmp_path, monkeypatch):
        storage = tmp_path / "datasets"
        storage.mkdir()
        _app, c, _live, ctx = _df_env(
            tmp_path, _state("qA1", chip_name="g", data_folder="gilboa_iqcc"),
            monkeypatch=monkeypatch, storage=storage)
        assert ctx.get("qualibrate_project") == "alpha"
        html = c.get("/qubits").data.decode()
        assert 'hx-post="/chip-data-folder/set"' in html
        assert 'name="use"' in html
        assert storage.as_posix() in html
        assert "Browse" in html and "Clear" in html

    def test_fixable_without_candidates(self, tmp_path):
        _app, c, _live, _ctx = _df_env(
            tmp_path, _state("qA1", chip_name="g", data_folder="gilboa_iqcc"))
        html = c.get("/qubits").data.decode()
        assert "cnb-df-form" in html
        assert 'name="use"' not in html

    def test_unnamed_dangling_shows_name_prompt_only(self, tmp_path):
        _app, c, _live, _ctx = _df_env(
            tmp_path, _state("qA1", data_folder="gilboa_iqcc"))
        html = c.get("/qubits").data.decode()
        assert 'hx-post="/chip-name/set"' in html
        assert "cnb-df-form" not in html


class TestDataFolderSuggest:
    def test_suggest_banner_renders(self, tmp_path, monkeypatch):
        storage = tmp_path / "datasets"
        storage.mkdir()
        _app, c, _live, _ctx = _df_env(
            tmp_path, _state("qA1", chip_name="g"),
            monkeypatch=monkeypatch, storage=storage)
        html = c.get("/qubits").data.decode()
        assert "cnb-suggest" in html
        assert storage.as_posix() in html
        assert "Record" in html

    def test_record_stages_candidate(self, tmp_path, monkeypatch):
        storage = tmp_path / "datasets"
        storage.mkdir()
        _app, c, _live, ctx = _df_env(
            tmp_path, _state("qA1", chip_name="g"),
            monkeypatch=monkeypatch, storage=storage)
        html = c.get("/qubits").data.decode()
        import re as _re
        use = _re.search(r'name="use" value="([^"]+)"', html).group(1)
        r = c.post("/chip-data-folder/set", data={"use": use})
        assert r.status_code == 200, r.data
        assert ctx["store"].state["extras"]["data_folder"] == use
        assert "cnb-suggest" not in c.get("/qubits").data.decode()

    def test_decline_memo_survives_reload(self, tmp_path, monkeypatch):
        storage = tmp_path / "datasets"
        storage.mkdir()
        _app, c, live, _ctx = _df_env(
            tmp_path, _state("qA1", chip_name="g"),
            monkeypatch=monkeypatch, storage=storage)
        html = c.get("/qubits").data.decode()
        import re as _re
        token = _re.search(r'name="token" value="([^"]+)"', html).group(1)
        c.post("/chip-data-folder/decline", data={"token": token})
        assert "cnb-suggest" not in c.get("/qubits").data.decode()
        c.post("/load", data={"folder": str(live)})
        assert "cnb-suggest" not in c.get("/qubits").data.decode()
        memo = json.loads(
            (tmp_path / "_inst" / "chip_name_prompts.json").read_text())
        assert f"{token}::datafolder" in memo
        assert token not in memo, "the NAME decline memo is untouched"

    def test_unnamed_gets_name_prompt_not_suggest(self, tmp_path, monkeypatch):
        storage = tmp_path / "datasets"
        storage.mkdir()
        _app, c, _live, _ctx = _df_env(
            tmp_path, _state("qA1"), monkeypatch=monkeypatch, storage=storage)
        html = c.get("/qubits").data.decode()
        assert 'hx-post="/chip-name/set"' in html
        assert "cnb-suggest" not in html

    def test_no_candidates_no_banner(self, tmp_path):
        _app, c, _live, _ctx = _df_env(tmp_path, _state("qA1", chip_name="g"))
        assert "cnb-suggest" not in c.get("/qubits").data.decode()

    def test_candidates_exclude_adopted_roots(self, tmp_path, monkeypatch):
        storage = tmp_path / "datasets"
        storage.mkdir()
        from quam_state_manager.web import routes as routes_mod
        app, _c, _live, _ctx = _df_env(
            tmp_path, _state("qA1", chip_name="g"),
            monkeypatch=monkeypatch, storage=storage)
        with app.test_request_context("/"):
            cands = routes_mod._data_folder_candidates(
                {"qualibrate_project": "alpha",
                 "extras_data_roots": [str(storage)]})
            assert all(c_["value"] != str(storage)
                       and c_["value"] != storage.as_posix()
                       for c_ in cands)


class TestChipNameSetHardening:
    def test_rejects_bare_name_folder(self, tmp_path):
        _app, c, _live, ctx = _df_env(tmp_path, _state("qA1"))
        r = c.post("/chip-name/set",
                   data={"name": "gilboa", "data_folder": "gilboa_iqcc"})
        assert r.status_code == 400
        assert "looks like a name" in r.data.decode()
        assert "extras" not in ctx["store"].state or \
            "chip_name" not in ctx["store"].state.get("extras", {}), \
            "nothing staged on rejection"

    def test_cross_machine_stages_with_note(self, tmp_path):
        _app, c, _live, ctx = _df_env(tmp_path, _state("qA1"))
        missing = str(tmp_path / "missing_zz")
        r = c.post("/chip-name/set",
                   data={"name": "gilboa", "data_folder": missing})
        assert r.status_code == 200
        assert "not reachable" in r.data.decode()
        assert ctx["store"].state["extras"]["data_folder"] == missing

    def test_datalist_offers_candidates(self, tmp_path, monkeypatch):
        storage = tmp_path / "datasets"
        storage.mkdir()
        _app, c, _live, _ctx = _df_env(
            tmp_path, _state("qA1"), monkeypatch=monkeypatch, storage=storage)
        html = c.get("/qubits").data.decode()
        assert "cnb-df-options" in html
        assert storage.as_posix() in html


class TestAdoptBridgeUsesPublicNativePath:
    def test_home_style_value_bridged(self, tmp_path, monkeypatch):
        """A non-/mnt POSIX value must go through the PUBLIC native_path
        (wsl_root share anchoring) — the old private _to_native call never
        bridged it (fails on the pre-r10 code)."""
        import quam_state_manager.core.qualibrate_config as qc
        real = tmp_path / "bridged_data"
        real.mkdir()
        orig = qc.native_path

        def fake(raw):
            if raw == "/home/lab/data":
                return real
            return orig(raw)

        monkeypatch.setattr(qc, "native_path", fake)
        _app, _c, _live, ctx = _df_env(
            tmp_path, _state("qA1", chip_name="g",
                             data_folder="/home/lab/data"))
        assert ctx["extras_data_roots"] == [str(real)]
        assert ctx["extras_data_dangling"] == []


class TestLadderDriftHeal:
    """audit-r10 F-F: routine same-chip evolution (add a qubit, move host)
    changes the fingerprint token — tier 1 must HEAL (align/label-judged,
    token refreshed), never fork a named chip's history. A provably
    different chip claiming the name still refuses."""

    def test_add_qubit_keeps_named_dir(self, hm, tmp_path):
        p = _write(tmp_path / "labX" / "quam_a",
                   _state("qA1", chip_name="gilboa"), _wiring("10.0.0.1"))
        m1 = hm.check_and_snapshot(p, "manual", force=True)
        st = _state("qA1", chip_name="gilboa")
        st["qubits"]["qB1"] = {"id": "qB1", "f_01": 6.0e9}
        _write(tmp_path / "labX" / "quam_a", st, _wiring("10.0.0.1"))
        m2 = hm.check_and_snapshot(p, "manual", force=True)
        root = tmp_path / "inst" / "history"
        assert (root / "gilboa" / m1.timestamp).is_dir()
        assert (root / "gilboa" / m2.timestamp).is_dir(),             "adding a qubit must not fork the named chip's history"
        assert hm._key_for(p) == "gilboa"

    def test_host_move_keeps_named_dir(self, hm, tmp_path):
        p = _write(tmp_path / "labX" / "quam_a",
                   _state("qA1", chip_name="gilboa"), _wiring("10.0.0.1"))
        m1 = hm.check_and_snapshot(p, "manual", force=True)
        _write(tmp_path / "labX" / "quam_a",
               _state("qA1", chip_name="gilboa"), _wiring("10.9.9.9"))
        m2 = hm.check_and_snapshot(p, "manual", force=True)
        root = tmp_path / "inst" / "history"
        assert (root / "gilboa" / m1.timestamp).is_dir()
        assert (root / "gilboa" / m2.timestamp).is_dir(),             "a host/cluster move must not fork the named chip's history"

    def test_provably_different_chip_still_refused(self, hm, tmp_path):
        pa = _write(tmp_path / "labX" / "quam_a",
                    _state("qA1", chip_name="gilboa"), _wiring("10.0.0.1"))
        ma = hm.check_and_snapshot(pa, "manual", force=True)
        # different network AND different labels — a true impostor
        pb = _write(tmp_path / "labY" / "quam_z",
                    _state("qZ9", chip_name="gilboa"), _wiring("172.16.0.9"))
        mb = hm.check_and_snapshot(pb, "manual", force=True)
        root = tmp_path / "inst" / "history"
        assert (root / "gilboa" / ma.timestamp).is_dir()
        assert not (root / "gilboa" / mb.timestamp).is_dir(),             "two physical chips must never merge into one dir"


class TestIdentityConfirm:
    """r12: an unnamed live chip whose HISTORY remembers a declared identity
    gets the conservative "Is this chip 'X'?" banner — never an automatic
    restore (same-architecture chips can be many; even data folders can
    coincide). Yes stages via the validated /chip-name/set; No memoizes and
    falls back to the fill-in prompt."""

    def _wiped_env(self, tmp_path, *, data_folder=None):
        """Chip named + snapshotted, then extras WIPED on disk (the gilboa
        regeneration incident), then loaded fresh."""
        from quam_state_manager.web.app import create_app
        named = _state("qA1", chip_name="gilboa", data_folder=data_folder)
        live = _write(tmp_path / "chips" / "live", named, _wiring("10.0.0.7"))
        app0 = create_app(testing=True, instance_path=str(tmp_path / "_inst"))
        hm = app0.config["history_manager"]
        hm.check_and_snapshot(str(live), "manual", force=True)
        wiped = _state("qA1")
        wiped["extras"] = {}
        _write(tmp_path / "chips" / "live", wiped, _wiring("10.0.0.7"))
        c = app0.test_client()
        r = c.post("/load", data={"folder": str(live)})
        assert r.status_code in (200, 302)
        ctx = next(iter(app0.config["contexts"].values()))
        return app0, c, live, ctx, hm

    def test_remembered_identity_from_snapshots(self, tmp_path):
        data_root = tmp_path / "df"
        data_root.mkdir()
        _app, _c, live, _ctx, hm = self._wiped_env(
            tmp_path, data_folder=str(data_root))
        rem = hm.remembered_identity(str(live))
        assert rem and rem["name"] == "gilboa"
        assert rem["data_folder"] == str(data_root)

    def test_confirm_banner_renders_and_yes_stages(self, tmp_path):
        data_root = tmp_path / "df"
        data_root.mkdir()
        _app, c, _live, ctx, _hm = self._wiped_env(
            tmp_path, data_folder=str(data_root))
        html = c.get("/qubits").data.decode()
        assert "This chip appears to be" in html
        assert "gilboa" in html and "Is this correct?" in html
        assert 'hx-post="/chip-identity/decline"' in html
        # the Yes form routes through the EXISTING validated path
        assert 'hx-post="/chip-name/set"' in html
        r = c.post("/chip-name/set", data={"name": "gilboa",
                                           "data_folder": str(data_root)})
        assert r.status_code == 200
        assert ctx["store"].state["extras"]["chip_name"] == "gilboa"
        assert "This chip appears to be" not in c.get("/qubits").data.decode()

    def test_not_a_path_data_folder_never_reoffered(self, tmp_path):
        _app, c, _live, ctx, _hm = self._wiped_env(
            tmp_path, data_folder="gilboa_iqcc")
        html = c.get("/qubits").data.decode()
        assert "This chip appears to be" in html
        assert ctx["identity_confirm"]["data_folder"] is None, \
            "a remembered not-a-path mistake must not be re-suggested"
        assert 'name="data_folder" value="gilboa_iqcc"' not in html

    def test_no_falls_back_to_fill_in_prompt(self, tmp_path):
        _app, c, live, _ctx, _hm = self._wiped_env(tmp_path)
        html = c.get("/qubits").data.decode()
        import re as _re
        token = _re.search(r'name="token" value="([^"]+)"', html).group(1)
        r = c.post("/chip-identity/decline", data={"token": token})
        assert r.status_code == 200
        body = r.data.decode()
        assert "This chip appears to be" not in body
        assert 'hx-post="/chip-name/set"' in body, \
            "the decline response IS the fill-in prompt"
        assert "Browse" in body, "manual path picking is offered"
        # survives a fresh re-activation
        c.post("/load", data={"folder": str(live)})
        html2 = c.get("/qubits").data.decode()
        assert "This chip appears to be" not in html2
        assert 'hx-post="/chip-name/set"' in html2

    def test_never_for_chips_without_history(self, tmp_path):
        from quam_state_manager.web.app import create_app
        live = _write(tmp_path / "chips" / "live", _state("qA1"),
                      _wiring("10.0.0.8"))
        app = create_app(testing=True, instance_path=str(tmp_path / "_inst"))
        c = app.test_client()
        c.post("/load", data={"folder": str(live)})
        html = c.get("/qubits").data.decode()
        assert "This chip appears to be" not in html
        assert 'hx-post="/chip-name/set"' in html   # plain prompt instead


class TestAuditR10Pins:
    def test_extras_editors_in_scheduler_mutator_set(self):
        from quam_state_manager.web import routes as routes_mod
        s = routes_mod._SCHEDULER_MUTATOR_ENDPOINTS
        assert "main.chip_name_set" in s
        assert "main.chip_data_folder_set" in s

    def test_chip_name_banner_route(self, tmp_path):
        _app, c, _live, _ctx = _df_env(tmp_path, _state("qA1", chip_name="g"))
        r = c.get("/chip-name/banner")
        assert r.status_code == 200
