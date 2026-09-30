"""docs/232: SM's state never lives inside a conda env, and what an env kept
there is carried to the per-user folder once, losing nothing.

Customer 2026-09-30: ``dash-bootstrap-components`` ships a ``pyproject.toml``
into ``site-packages``; ``default_instance_path()`` took ANY pyproject beside
the package for a repo checkout, so the ``kriss_arbel`` env kept its own
instance folder in ``<env>/var``. There, the arbel chip's history sat in a
folder named ``quam_states`` -- the same name the per-user folder already used
for the KRISS_CZ chip (647 snapshots): a naive copy would have merged two
chips' histories.
"""
from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

import pytest

from quam_state_manager.core import instance_migrate as M
from quam_state_manager.core.history import HistoryManager
from quam_state_manager.web import app as app_mod


def _w(p: Path, data) -> Path:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(data if isinstance(data, str) else json.dumps(data), encoding="utf-8")
    return p


def _r(p: Path):
    return json.loads(p.read_text(encoding="utf-8"))


# ------------------------------------------------------ the checkout test
class TestIsSmCheckout:
    def test_our_own_pyproject(self):
        assert app_mod.is_sm_checkout(Path(app_mod.__file__).resolve().parents[2] / "pyproject.toml")

    def test_another_packages_pyproject_is_not_a_checkout(self, tmp_path):
        p = _w(tmp_path / "pyproject.toml",
               '[build-system]\nrequires = ["hatchling"]\n\n[project]\nname = "dash-bootstrap-components"\n')
        assert not app_mod.is_sm_checkout(p)

    def test_spellings_of_our_name(self, tmp_path):
        for n in ("quam-state-manager", "quam_state_manager", "Quam.State.Manager"):
            assert app_mod.is_sm_checkout(_w(tmp_path / n / "pyproject.toml", f'[project]\nname = "{n}"\n'))

    def test_missing_or_nameless(self, tmp_path):
        assert not app_mod.is_sm_checkout(tmp_path / "nope.toml")
        assert not app_mod.is_sm_checkout(_w(tmp_path / "pyproject.toml", "[tool.black]\nline-length = 99\n"))


@pytest.mark.real_instance_path
def test_a_foreign_pyproject_in_site_packages_uses_the_user_folder_and_moves_the_env_state(
        tmp_path, monkeypatch):
    site = tmp_path / "env" / "Lib" / "site-packages"
    (site / "quam_state_manager" / "web").mkdir(parents=True)
    _w(site / "pyproject.toml", '[project]\nname = "dash-bootstrap-components"\n')
    monkeypatch.setattr(app_mod, "__file__", str(site / "quam_state_manager" / "web" / "app.py"))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "appdata"))
    monkeypatch.setattr(sys, "prefix", str(tmp_path / "env"))
    legacy = tmp_path / "env" / "var" / "quam_state_manager.web.app-instance"
    _w(legacy / "project_envs.json", {"projects": {"arbel": {"python": "X", "at": 1, "how": "changed"}}})
    got = app_mod.default_instance_path()
    assert got is not None and "QUAM State Manager" in got
    assert _r(Path(got) / "project_envs.json")["projects"]["arbel"]["python"] == "X"
    assert (legacy / M.MARKER).exists()


# -------------------------------------------------------------- the move
@pytest.fixture
def two(tmp_path):
    return tmp_path / "legacy", tmp_path / "user"


def test_nothing_to_move(two):
    legacy, user = two
    assert M.migrate(legacy, user) is None
    legacy.mkdir()
    assert M.migrate(legacy, legacy) is None


def test_settings_merge_and_the_user_folder_wins(two):
    legacy, user = two
    _w(legacy / "project_envs.json", {
        "projects": {"arbel": {"python": "A"}, "shared": {"python": "OLD"}},
        "last_used": {"python": "A", "project": "arbel"}})
    _w(user / "project_envs.json", {
        "projects": {"KRISS_CZ": {"python": "K"}, "shared": {"python": "NEW"}},
        "last_used": {"python": "K", "project": "KRISS_CZ"}})
    _w(legacy / "workspace_roots.json", [r"D:\data\arbel", r"D:\data\KH"])
    _w(user / "workspace_roots.json", [r"D:\data\KH", r"D:\data\KRISS"])
    _w(legacy / "project_dataset_roots.json", {"arbel": [r"D:\data\arbel"], "KRISS_CZ": [r"D:\data\x"]})
    _w(user / "project_dataset_roots.json", {"KRISS_CZ": [r"D:\data\KRISS"]})
    _w(legacy / "config_generator.json", {"selected_env": "A"})
    _w(user / "config_generator.json", {"selected_env": "K"})
    r = M.migrate(legacy, user)
    pe = _r(user / "project_envs.json")
    assert pe["projects"]["arbel"]["python"] == "A"
    assert pe["projects"]["shared"]["python"] == "NEW"          # user wins
    assert pe["last_used"]["project"] == "KRISS_CZ"
    assert _r(user / "workspace_roots.json") == [r"D:\data\KH", r"D:\data\KRISS", r"D:\data\arbel"]
    pdr = _r(user / "project_dataset_roots.json")
    assert pdr["arbel"] == [r"D:\data\arbel"] and pdr["KRISS_CZ"] == [r"D:\data\KRISS", r"D:\data\x"]
    assert _r(user / "config_generator.json")["selected_env"] == "K"
    assert "config_generator.json" in r["kept_user"]


def test_caches_are_not_moved_and_the_env_folder_is_left_alone(two):
    legacy, user = two
    _w(legacy / "state_schema_cache.json", {"x": 1})
    _w(legacy / "workspace_cache" / "ws_1.json", {"x": 1})
    _w(legacy / "instances" / "123.json", {"pid": 123})
    _w(legacy / "working_state" / "chip-abc.meta.json", {"k": 1})
    before = {p.relative_to(legacy).as_posix(): p.read_bytes()
              for p in legacy.rglob("*") if p.is_file()}
    r = M.migrate(legacy, user)
    assert not (user / "state_schema_cache.json").exists()
    assert not (user / "workspace_cache").exists() and not (user / "instances").exists()
    assert (user / "working_state" / "chip-abc.meta.json").exists()
    after = {p.relative_to(legacy).as_posix(): p.read_bytes()
             for p in legacy.rglob("*") if p.is_file()}
    assert set(after) - set(before) == {M.MARKER}
    assert all(after[k] == v for k, v in before.items())
    assert _r(legacy / M.MARKER)["copied"] == r["copied"]
    assert M.migrate(legacy, user) is None                       # once


def test_a_working_copy_the_user_folder_has_is_kept(two):
    legacy, user = two
    _w(legacy / "working_state" / "chip-abc.meta.json", {"v": "legacy"})
    _w(user / "working_state" / "chip-abc.meta.json", {"v": "user"})
    r = M.migrate(legacy, user)
    assert _r(user / "working_state" / "chip-abc.meta.json")["v"] == "user"
    assert "working_state/chip-abc.meta.json" in r["kept_user"]


def test_a_history_index_in_wal_mode_arrives_whole(two):
    legacy, user = two
    db = legacy / "history" / "arbel" / "index.sqlite"
    db.parent.mkdir(parents=True)
    con = sqlite3.connect(str(db))
    con.execute("pragma journal_mode=wal")
    con.execute("create table t (x)")
    con.executemany("insert into t values (?)", [(i,) for i in range(500)])
    con.commit()                        # in the WAL, not yet checkpointed
    assert (db.parent / "index.sqlite-wal").stat().st_size > 0
    try:
        M.migrate(legacy, user)
    finally:
        con.close()
    out = sqlite3.connect(str(user / "history" / "arbel" / "index.sqlite"))
    assert out.execute("select count(*) from t").fetchone()[0] == 500
    assert out.execute("pragma integrity_check").fetchone()[0] == "ok"
    out.close()
    assert not (user / "history" / "arbel" / "index.sqlite-wal").exists()


# ------------------------------------ the chip-folder collision, end to end
def _chip(folder: Path, name: str, qubits: list[str], host: str) -> Path:
    state = {"qubits": {q: {"id": q, "f_01": 5e9} for q in qubits},
             "extras": {"chip_name": name}}
    wiring = {"network": {"host": host, "cluster_name": name}, "wiring": {}}
    _w(folder / "state.json", state)
    _w(folder / "wiring.json", wiring)
    return folder


def test_two_chips_with_one_folder_name_stay_two_chips(tmp_path):
    """The customer's exact shape: both instance folders hold a history chip
    folder called ``quam_states`` -- arbel in the env's, KRISS_CZ in the
    user's. After the move each chip still opens ITS OWN history."""
    legacy, user = tmp_path / "legacy", tmp_path / "user"
    arbel = _chip(tmp_path / "chips" / "quam_states" / "arbel_kriss", "arbel", ["qA1", "qB1"], "10.0.0.1")
    kriss = _chip(tmp_path / "chips2" / "quam_states" / "KRS_5Q", "KRISS_CZ", ["q1", "q2"], "10.0.0.2")
    hl, hu = HistoryManager(legacy), HistoryManager(user)
    for i in range(3):
        s = json.loads((arbel / "state.json").read_text(encoding="utf-8"))
        s["qubits"]["qA1"]["f_01"] = 5e9 + i
        _w(arbel / "state.json", s)
        hl.check_and_snapshot(arbel, "manual", force=True)
    hu.check_and_snapshot(kriss, "manual", force=True)
    a_key = hl.resolve_chip_dir(arbel)[1]
    k_key = hu.resolve_chip_dir(kriss)[1]
    n_a, n_k = len(hl.list_snapshots(arbel)), len(hu.list_snapshots(kriss))
    assert n_a == 3 and n_k == 1
    # force the collision the customer had: both chips in a folder of ONE name
    if a_key != k_key:
        (legacy / "history" / a_key).rename(legacy / "history" / k_key)
        al = _r(legacy / "history" / "_chip_aliases.json")
        al["names"]["arbel"]["dir"] = k_key
        al["dirs"] = {k_key: {"display": "arbel"}}
        _w(legacy / "history" / "_chip_aliases.json", al)
    r = M.migrate(legacy, user)
    assert r["renamed"] == {k_key: "arbel"}
    hm = HistoryManager(user)
    assert hm.resolve_chip_dir(arbel)[1] == "arbel"
    assert len(hm.list_snapshots(arbel)) == 3
    assert hm.resolve_chip_dir(kriss)[1] == k_key
    assert len(hm.list_snapshots(kriss)) == 1


def test_a_concurrent_move_is_skipped(two):
    legacy, user = two
    _w(legacy / "project_envs.json", {"projects": {}})
    user.mkdir(parents=True)
    (user / ".migrate.lock").write_text("", encoding="utf-8")
    assert M.migrate(legacy, user) is None
    assert not (legacy / M.MARKER).exists()
