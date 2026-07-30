"""Chip-identity ladder (docs/20 v2): extras chip_name > fingerprint > path.

The wild failure this fixes: 7 different chips as sibling folders under one
parent — ``chip_name_for`` collapsed them all onto the parent's name, capture
fingerprint-forked snapshots into ``_alt_`` dirs while every READ (page,
drawer, field-history) kept using the path-derived dir, i.e. another chip's
history. The ladder makes every read and write resolve through ONE choke
point, with the user-declared ``state["extras"]["chip_name"]`` as tier 1."""

from __future__ import annotations

import json
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


def _write(folder: Path, state: dict, wiring: dict) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "state.json").write_text(json.dumps(state), encoding="utf-8")
    (folder / "wiring.json").write_text(json.dumps(wiring), encoding="utf-8")
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
