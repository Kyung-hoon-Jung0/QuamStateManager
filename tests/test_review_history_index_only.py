"""Retained index rows remain recoverable without becoming snapshot chips."""
from quam_state_manager.core import history
from tests.test_history_migration_preservation import _index, _instance, _inventory, _snapshot


def test_v2_retained_index_only_directory_is_not_a_listed_chip(tmp_path):
    instance, source, target, moved_ts = _instance(tmp_path)
    assert history.migrate_legacy_histories_v2(instance)["moved"] == 1
    assert (target / moved_ts).is_dir()
    assert not any(p.is_dir() for p in source.iterdir())
    assert (source / "index.sqlite").exists()
    before = _inventory(instance)
    listed = history.HistoryManager(instance).list_chip_histories()
    assert {row["key"] for row in listed} == {target.name}
    assert _inventory(instance) == before
    assert (source / "ledger.sqlite").exists()


def test_an_index_memo_does_not_keep_a_chip_without_snapshot_folders(tmp_path):
    instance = tmp_path / "instance"
    directory = instance / "history" / "chip_a"
    stamp = "20261001_000000_001"
    _snapshot(directory, stamp, 10)
    _index(directory, [(stamp, 10)])
    manager = history.HistoryManager(instance)
    assert [row["key"] for row in manager.list_chip_histories()] == ["chip_a"]
    retained = (directory / "index.sqlite").read_bytes()
    (directory / stamp).rename(tmp_path / "saved_snapshot")
    manager._global_version += 1
    assert manager.list_chip_histories() == []
    assert (directory / "index.sqlite").read_bytes() == retained
    assert (tmp_path / "saved_snapshot" / "state.json").is_file()
