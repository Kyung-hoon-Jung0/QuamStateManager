"""Virtual history links do not persist routing aliases."""
import json

import pytest

from quam_state_manager.core import history, safe_io


@pytest.mark.parametrize("existing", [False, True])
def test_virtual_history_link_does_not_write_alias_registry(tmp_path, existing):
    manager = history.HistoryManager(tmp_path / "instance")
    aliases = manager._alias_path()
    if existing:
        safe_io.atomic_write_json(aliases, {"paths": {"saved": {"dir": "chip_a", "source": "path"}}})
    before = aliases.read_bytes() if aliases.exists() else None
    virtual = tmp_path / "__chip_key__" / "chip_a" / "quam_state"
    for _ in range(2):
        assert manager.resolve_chip_dir(virtual)[1] == "chip_a"
        assert (aliases.read_bytes() if aliases.exists() else None) == before


def test_a_real_path_with_a_similar_component_is_remembered(tmp_path):
    manager = history.HistoryManager(tmp_path / "instance")
    live = tmp_path / "__chip_key__suffix" / "quam_state"
    safe_io.write_state_wiring(live, {"qubits": {"q1": {"T1": 0.00001}}}, {})
    result = manager.resolve_chip_dir(live)
    aliases = json.loads(manager._alias_path().read_text(encoding="utf-8"))
    assert aliases["paths"][str(live.resolve())]["dir"] == result[1]
