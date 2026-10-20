"""R6e: first use of the hash cache never logs a missing-directory traceback."""
import json
from quam_state_manager.core.history import HistoryManager


def test_first_hash_cache_use_creates_its_parent(tmp_path, caplog):
    h = HistoryManager(tmp_path / "instance")
    folder = h._root / "fresh_device"
    assert not folder.exists()
    assert h._known_hashes_for_chip(folder) == set()
    assert not [r for r in caplog.records if "Could not persist hash sidecar" in r.message]
    assert json.loads((folder / "_hashes.json").read_text()) == {"hashes": []}
    h._hash_cache[str(folder)].add("abc")
    h._persist_known_hashes(folder)
    assert json.loads((folder / "_hashes.json").read_text()) == {"hashes": ["abc"]}
