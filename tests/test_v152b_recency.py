"""Metadata follows the chosen fidelity, including sibling timestamps."""
from tests.test_topology_pairs import _chip, _edge
import subprocess
from pathlib import Path


def test_irb_age_and_run_hint_follow_selected_metric(tmp_path):
    client = _chip(tmp_path, {"InterleavedRB": .90, "InterleavedRB_updated_at": "2020-01-01", "InterleavedRB_load_id": 412,
                              "IRB": .999, "IRB_updated_at": "2021-01-01", "IRB_load_id": 413})
    edge = _edge(client)
    assert edge["cz_fidelity"] == .999
    assert edge["cz_load_id"] == 413
    assert edge["cz_fidelity_updated_at"] == 1609459200000
    assert edge["metrics"]["cz_fidelity"]["updated_at"] == 1609459200000


def test_sibling_and_container_times(tmp_path):
    client = _chip(tmp_path, {"InterleavedRB": .991, "InterleavedRB_updated_at": "2020-01-01", "updated_at": "2022-01-01"})
    assert _edge(client)["cz_fidelity_updated_at"] == 1577836800000


def test_unknown_age_has_no_freshness_claim():
    root = Path(__file__).resolve().parents[1]
    result = subprocess.run(["node", str(root / "tests/v152b_recency.cjs")], capture_output=True, text=True,
                            timeout=300)
    assert result.returncode == 0, result.stdout + result.stderr


def test_each_fidelity_row_carries_only_its_own_time(tmp_path):
    """A row's time_ms is the time its own key records; a key with none gets None,
    never the block's updated_at or a sibling's (Bell, StandardRB) time."""
    client = _chip(tmp_path, {
        "Bell_State": {"Fidelity": .95, "updated_at": "2026-09-06 07:33:36 GMT+3"},
        "StandardRB": {"alpha": .9, "updated_at": "2026-09-06 07:45:29 GMT+3"},
        "InterleavedRB": .97, "IRB": .96, "IRB_updated_at": "2026-11-12 10:00:00 GMT+3",
        "updated_at": "2026-09-01 00:00:00 GMT+3"})
    rows = {gf["metric"]: gf for gf in _edge(client)["gate_fidelities"]}
    assert rows["Bell_State"]["time_ms"] == 1788669216000
    assert rows["StandardRB"]["time_ms"] == 1788669929000
    assert rows["IRB"]["time_ms"] == 1794466800000
    assert rows["InterleavedRB"]["time_ms"] is None
