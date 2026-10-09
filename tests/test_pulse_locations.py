"""Pulses found by SHAPE, not by name (docs/217 pulse locations).

A lab hand-adds pulses where SM's whitelist never looked -- a second drive
channel, a coupler's own ``operations``, a TWPA pump, a new macro slot. These
pins hold three things at once:

* every new location type becomes a row, and every downstream surface accepts
  it (detail, field commit, synth, used_by, duplicate / rename / delete);
* the negatives stay non-rows (``extras``, lists, pointer strings outside an
  ``operations`` dict, unclassed dicts, a class the probe says is no pulse);
* the whitelisted enumeration is byte-identical: no existing row moves,
  changes or duplicates (synthetic here, and on real chips when present).
"""

from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path

import pytest

from quam_state_manager.core import pulse_catalog, pulse_index
from quam_state_manager.core.diagnostics import _starting_output_ref
from quam_state_manager.web.app import create_app

QC = "quam.components.pulses."

XY2 = "qubits.q1.xy2.operations"
CPL = "qubit_pairs.q1-2.coupler.operations"
PUMP = "twpas.twpaA.pump.operations"
SLOT = "qubit_pairs.q1-2.macros.cz_custom.flux_pulse_extra"
SPEC = "qubit_pairs.q1-2.macros.cz_custom.spectators.q3"


def _state() -> dict:
    return {
        "qubits": {
            "q1": {
                "anharmonicity": -200e6,
                "xy": {"operations": {
                    "x180": {"__class__": QC + "SquarePulse", "length": 40,
                             "amplitude": 0.2},
                    "x90": "#./x180",
                }},
                # a second drive line the whitelist has never heard of
                "xy2": {
                    "opx_output": "#/wiring/qubits/q1/xy2/opx_output",
                    "operations": {
                        "x180_b": {"__class__": QC + "SquarePulse", "length": 32,
                                   "amplitude": 0.1},
                        # a lab class whose name does NOT end in "Pulse": inside
                        # an operations dict the key is the evidence
                        "snz_lab": {"__class__": "lab_pkg.gates.SNZNewThing",
                                    "length": 60, "amplitude": 0.3,
                                    "t_phi": 2},
                        "x180_alias": "#./x180_b",
                        # negatives inside a new operations dict
                        "empty": None,
                        "number": 5,
                        "unclassed": {"length": 10, "amplitude": 0.1},
                    },
                },
                # an operations dict two levels under the owner
                "z_line": {"trigger": {"operations": {
                    "mark": {"__class__": QC + "SquarePulse", "length": 16,
                             "amplitude": 0.5}}}},
                # negatives outside any operations dict
                "notes_blob": {"length": 10, "amplitude": 0.1},
                "stash": [{"__class__": QC + "SquarePulse", "length": 4,
                           "amplitude": 0.1}],
                "macros": {"x": {"__class__": "lab_pkg.gates.XGateMacro",
                                 "pulse": "#/qubits/q1/xy/operations/x180"}},
            },
        },
        "qubit_pairs": {
            "q1-2": {
                "coupler": {
                    "opx_output": "#/ports/analog_outputs/con1/5/1",
                    "operations": {
                        "cz_coupler": {"__class__": QC + "FlatTopGaussianPulse",
                                       "length": 48, "amplitude": 0.05,
                                       "flat_length": 20},
                    },
                },
                "macros": {
                    "cz_unipolar": {
                        "flux_pulse_qubit": {"__class__": QC + "SquarePulse",
                                             "length": 80, "amplitude": 0.07},
                        # a whitelisted slot that POINTS INTO a discovered pulse:
                        # the discovered row's used_by must name it
                        "coupler_flux_pulse": "#/qubit_pairs/q1-2/coupler/operations/cz_coupler",
                    },
                    "cz_custom": {
                        "__class__": "lab_pkg.gates.CZGateCustom",
                        "flux_pulse_extra": {"__class__": "lab_pkg.pulses.GaussianNZPulse",
                                             "length": 64, "amplitude": 0.12},
                        "spectators": {"q3": {"__class__": QC + "SquarePulse",
                                              "length": 64, "amplitude": 0.01}},
                        # a pointer outside `operations` is never a row
                        "pulse_ref": "#/qubits/q1/xy2/operations/x180_b",
                    },
                    "cz": "#./cz_custom",
                },
            },
        },
        "twpas": {"twpaA": {"pump": {"operations": {
            "pump": {"__class__": QC + "SquarePulse", "length": 100,
                     "amplitude": 0.4}}}}},
        # free-form (docs/81) -- a pasted pulse here must never become a row
        "extras": {"old": {"__class__": QC + "SquarePulse", "length": 4,
                           "amplitude": 0.1}},
    }


FOUND = {f"{XY2}.x180_b", f"{XY2}.snz_lab", f"{XY2}.x180_alias",
         f"{CPL}.cz_coupler", f"{PUMP}.pump", SLOT, SPEC,
         "qubits.q1.z_line.trigger.operations.mark"}
WHITELISTED = [
    "qubits.q1.xy.operations.x180",
    "qubits.q1.xy.operations.x90",
    "qubit_pairs.q1-2.macros.cz_unipolar.flux_pulse_qubit",
    "qubit_pairs.q1-2.macros.cz_unipolar.coupler_flux_pulse",
]


# ---------------------------------------------------------------------------
# Enumeration
# ---------------------------------------------------------------------------

class TestDiscovery:
    def test_every_new_location_type_is_a_row(self):
        rows = pulse_index.list_pulses(_state())
        found = {r["path"] for r in rows if r["found"]}
        assert found == FOUND

    def test_whitelist_rows_first_unchanged_and_never_duplicated(self):
        m = _state()
        rows = pulse_index.list_pulses(m)
        paths = [r["path"] for r in rows]
        assert len(paths) == len(set(paths))
        assert paths[:len(WHITELISTED)] == WHITELISTED
        assert all(not r["found"] for r in rows[:len(WHITELISTED)])
        assert all(r["found"] for r in rows[len(WHITELISTED):])
        legacy = pulse_index.list_pulses(m, discover=False)
        assert rows[:len(legacy)] == legacy

    def test_negatives_are_not_rows(self):
        paths = {r["path"] for r in pulse_index.list_pulses(_state())}
        for p in (f"{XY2}.empty", f"{XY2}.number", f"{XY2}.unclassed",
                  "qubits.q1.notes_blob", "qubits.q1.stash.0",
                  "qubits.q1.macros.x", "qubit_pairs.q1-2.macros.cz_custom",
                  "qubit_pairs.q1-2.macros.cz_custom.pulse_ref",
                  "qubit_pairs.q1-2.macros.cz", "extras.old"):
            assert p not in paths, p

    def test_probed_bases_veto_a_pulse_named_class(self):
        m = _state()
        m["qubits"]["q1"]["helper"] = {"__class__": "lab_pkg.x.FakePulse",
                                       "length": 1}
        assert "qubits.q1.helper" in {
            r["path"] for r in pulse_index.list_pulses(m)}
        pulse_catalog.apply_chip_classes({"lab_pkg.x.FakePulse": {
            "importable": True, "bases": ["quam.core.quam_classes.QuamComponent"]}})
        try:
            assert "qubits.q1.helper" not in {
                r["path"] for r in pulse_index.list_pulses(m)}
        finally:
            pulse_catalog.apply_chip_classes(None)

    @pytest.mark.parametrize("rec", [
        {"importable": False, "canonical": None, "bases": [],
         "error": "ModuleNotFoundError"},
        {"importable": True, "bases": []},
        {"importable": False, "bases": [QC + "Pulse"]},
    ])
    def test_unknown_bases_never_veto(self, rec):
        # An unimportable class is probed as bases: [] = UNKNOWN. It must not
        # decide "not a pulse" (verifier P2: a hand-added pulse of a class that
        # does not import dropped out of the list after the next edit).
        m = _state()
        cls = "labmissing.pulses.HandFluxPulse"
        m["qubit_pairs"]["q1-2"]["macros"]["cz_custom"]["hand"] = {
            "__class__": cls, "length": 40, "amplitude": 0.1}
        path = "qubit_pairs.q1-2.macros.cz_custom.hand"
        pulse_catalog.apply_chip_classes({cls: rec})
        try:
            assert path in {r["path"] for r in pulse_index.list_pulses(m)}
        finally:
            pulse_catalog.apply_chip_classes(None)

    def test_probe_landing_refreshes_cached_rows(self):
        # PulseIndex rows must re-derive when a probe installs new class info,
        # not only on the next mutation (validate-on-read token).
        class _S:
            import threading as _t
            _lock = _t.RLock()
            mutation_seq = 7
        s = _S()
        s.merged = _state()
        s.merged["qubits"]["q1"]["helper"] = {
            "__class__": "lab_pkg.x.FakePulse", "length": 1}
        idx = pulse_index.PulseIndex(s)
        assert idx.has_path("qubits.q1.helper")
        pulse_catalog.apply_chip_classes({"lab_pkg.x.FakePulse": {
            "importable": True,
            "bases": ["quam.core.quam_classes.QuamComponent"]}})
        try:
            assert not idx.has_path("qubits.q1.helper")
        finally:
            pulse_catalog.apply_chip_classes(None)
        assert idx.has_path("qubits.q1.helper")

    def test_row_metadata(self):
        by = {r["path"]: r for r in pulse_index.list_pulses(_state())}
        r = by[f"{XY2}.snz_lab"]
        assert (r["owner_kind"], r["owner"], r["channel"], r["op_name"]) == (
            "qubit", "q1", "xy2", "snz_lab")
        assert r["renamable"] and r["location"] == XY2 and not r["known"]
        r = by["qubits.q1.z_line.trigger.operations.mark"]
        assert (r["owner"], r["channel"], r["op_name"]) == ("q1", "z_line.trigger", "mark")
        r = by[f"{CPL}.cz_coupler"]
        assert (r["owner_kind"], r["owner"], r["channel"]) == ("pair", "q1-2", "coupler")
        r = by[SLOT]
        assert (r["gate"], r["channel"], r["op_name"], r["renamable"]) == (
            "cz_custom", "flux_pulse_extra", "cz_custom.flux_pulse_extra", False)
        r = by[SPEC]
        assert r["channel"] == "spectators.q3" and r["gate"] == "cz_custom"
        r = by[f"{PUMP}.pump"]
        assert (r["owner_kind"], r["owner"], r["channel"]) == ("component", "twpaA", "pump")
        r = by[f"{XY2}.x180_alias"]
        assert r["is_alias"] and r["alias_target"] == f"{XY2}.x180_b"

    def test_used_by_reaches_discovered_rows(self):
        by = {r["path"]: r for r in pulse_index.list_pulses(_state())}
        assert by[f"{CPL}.cz_coupler"]["used_by"] == [
            "qubit_pairs.q1-2.macros.cz_unipolar.coupler_flux_pulse"]
        assert by[f"{XY2}.x180_b"]["used_by"] == [
            "qubit_pairs.q1-2.macros.cz_custom.pulse_ref", f"{XY2}.x180_alias"]


# ---------------------------------------------------------------------------
# Real chips (skipped when absent): the whitelist enumeration is untouched
# ---------------------------------------------------------------------------

_GOLDEN = Path(__file__).parent / "golden" / "pulse_rows_identity.json"


def _golden():
    return json.loads(_GOLDEN.read_text(encoding="utf-8"))


def _chip_path(key: str) -> Path:
    """A golden chip key: "lab-X:<field>" resolves through the external lab map
    (tests run anywhere; the real folder exists only on the verification
    machine), anything else is a plain path."""
    if key.startswith("lab-") and ":" in key:
        from quam_state_manager.core.lab_map import lab_path
        lab, field = key.split(":", 1)
        return lab_path(lab, field)
    return Path(key)


@pytest.mark.parametrize("chip", sorted(_golden()["chips"]))
def test_real_chip_whitelist_identity_golden(chip):
    """The (path, owner, channel, op, gate, alias, used_by) sequence of the
    whitelisted rows equals what the pre-change enumerator produced."""
    if not (_chip_path(chip) / "state.json").is_file():
        pytest.skip("real chip not on this machine")
    from quam_state_manager.core.loader import QuamStore
    g = _golden()
    rows = [r for r in pulse_index.list_pulses(QuamStore(_chip_path(chip)).merged)
            if not r["found"]]
    ident = [[r[k] for k in g["keys"]] for r in rows]
    assert len(rows) == g["chips"][chip]["n"]
    assert hashlib.sha256(json.dumps(ident).encode()).hexdigest() == \
        g["chips"][chip]["sha256"]


# ---------------------------------------------------------------------------
# Downstream surfaces
# ---------------------------------------------------------------------------

def test_dac_range_diag_uses_the_holding_component():
    m = _state()
    by = {r["path"]: r for r in pulse_index.list_pulses(m)}
    assert _starting_output_ref(m, by[f"{CPL}.cz_coupler"]) == \
        "#/ports/analog_outputs/con1/5/1"
    assert _starting_output_ref(m, by[SLOT]) is None  # a slot is never guessed


def test_config_element_for_a_discovered_op():
    from quam_state_manager.web.routes import _config_op_for_pulse_path
    cfg = {"elements": {"q1.xy2": {"operations": {"x180_b": "p"}},
                        "twpaA.pump": {"operations": {"pump": "p"}}}}
    m = _state()
    assert _config_op_for_pulse_path(cfg, f"{XY2}.x180_b", m) == ("q1.xy2", "x180_b")
    assert _config_op_for_pulse_path(cfg, f"{PUMP}.pump", m) == ("twpaA.pump", "pump")
    assert _config_op_for_pulse_path(cfg, f"{CPL}.cz_coupler", m) == (None, None)
    assert _config_op_for_pulse_path(cfg, SLOT, m) == (None, None)


@pytest.fixture
def client(tmp_path):
    folder = tmp_path / "chip"
    folder.mkdir()
    (folder / "state.json").write_text(json.dumps(_state()), encoding="utf-8")
    (folder / "wiring.json").write_text(json.dumps({"wiring": {}}), encoding="utf-8")
    app = create_app(testing=True, instance_path=str(tmp_path / "_inst"))
    c = app.test_client()
    c.post("/load", data={"folder": str(folder)})
    return c


def _table(html: str) -> str:
    import re
    t = re.search(r"<table[^>]*pulse[^>]*>.*?</table>", html, re.S | re.I)
    return t.group(0) if t else html


class TestRoutes:
    def test_list_shows_rows_label_and_tab(self, client):
        html = client.get("/pulses?per_page=0").data.decode()
        body = _table(html)
        for p in FOUND:
            assert f'data-pulse-path="{p}"' in body, p
        assert "found at <code>qubit_pairs.q1-2.coupler.operations</code>" in body
        assert "channel=found" in html and ">Other places<" in html
        only = _table(client.get("/pulses?rows=1&channel=found&per_page=0").data.decode())
        assert 'data-pulse-path="qubits.q1.xy.operations.x180"' not in only
        assert f'data-pulse-path="{PUMP}.pump"' in only

    def test_whitelisted_chip_gets_no_new_tab(self, tmp_path):
        folder = tmp_path / "plain"
        folder.mkdir()
        st = _state()
        for k in ("xy2", "z_line", "notes_blob", "stash", "macros"):
            st["qubits"]["q1"].pop(k)
        st["qubit_pairs"]["q1-2"].pop("coupler")
        st["qubit_pairs"]["q1-2"]["macros"].pop("cz_custom")
        st["qubit_pairs"]["q1-2"]["macros"]["cz_unipolar"]["coupler_flux_pulse"] = None
        st.pop("twpas")
        (folder / "state.json").write_text(json.dumps(st), encoding="utf-8")
        (folder / "wiring.json").write_text(json.dumps({"wiring": {}}), encoding="utf-8")
        c = create_app(testing=True, instance_path=str(tmp_path / "_i")).test_client()
        c.post("/load", data={"folder": str(folder)})
        html = c.get("/pulses").data.decode()
        assert ">Other places<" not in html and "found at" not in html

    @pytest.mark.parametrize("path", sorted(FOUND))
    def test_detail_opens_every_discovered_row(self, client, path):
        resp = client.get(f"/pulse/detail?path={path}")
        assert resp.status_code == 200, path
        html = resp.data.decode()
        assert "pulse-detail-root" in html
        if not path.endswith("x180_alias"):
            assert "Found at <code>" in html

    def test_detail_still_refuses_non_pulses(self, client):
        for p in ("extras.old", "qubits.q1.notes_blob", "qubit_pairs.q1-2.macros.cz_custom"):
            assert client.get(f"/pulse/detail?path={p}").status_code == 404, p
        assert client.post("/api/pulse/delete", data={"path": "extras.old"}).status_code == 400

    def test_field_commit_on_discovered_rows(self, client):
        for path in (f"{XY2}.x180_b", SLOT, f"{PUMP}.pump", f"{XY2}.snz_lab"):
            resp = client.post("/pulse/edit", data={
                "path": path, "dot_path": f"{path}.amplitude",
                "mode": "value", "value": "0.0123"})
            assert resp.status_code == 200, (path, resp.data[:300])
            peek = client.get(f"/field/peek?dot_path={path}.amplitude").get_json()
            assert peek["resolved"][f"{path}.amplitude"]["resolved_value"] == 0.0123, path

    def test_synth_draws_a_known_class_in_a_new_place(self, client):
        out = client.post("/api/pulse/synth", json={"path": f"{CPL}.cz_coupler"}).get_json()
        assert out["ok"], out
        out = client.post("/api/pulse/synth", json={"path": f"{XY2}.snz_lab"}).get_json()
        assert not out["ok"] and "unrecognized" in out["error"]  # honest, not a crash

    def test_duplicate_rename_delete_in_a_new_operations_dict(self, client):
        r = client.post("/api/pulse/duplicate", data={"path": f"{XY2}.x180_b",
                                                      "new_name": "x180_c"})
        assert r.status_code == 200
        r = client.post("/api/pulse/rename", data={"path": f"{XY2}.x180_b",
                                                   "new_name": "x180_d", "retarget": "1"})
        assert r.status_code == 200 and b"re-pointed" in r.data
        peek = client.get(f"/field/peek?dot_path={XY2}.x180_alias").get_json()
        assert peek["resolved"][f"{XY2}.x180_alias"]["resolved_path"] == f"{XY2}.x180_d"
        html = _table(client.get("/pulses?per_page=0").data.decode())
        assert f'data-pulse-path="{XY2}.x180_d"' in html
        assert f'data-pulse-path="{XY2}.x180_c"' in html
        assert f'data-pulse-path="{XY2}.x180_b"' not in html
        r = client.post("/api/pulse/delete", data={"path": f"{XY2}.x180_c"})
        assert r.status_code == 200
        html = _table(client.get("/pulses?per_page=0").data.decode())
        assert f'data-pulse-path="{XY2}.x180_c"' not in html

    def test_slot_rows_are_not_renamable(self, client):
        for ep in ("/api/pulse/rename", "/api/pulse/duplicate"):
            r = client.post(ep, data={"path": SLOT, "new_name": "zz"})
            assert r.status_code == 400, ep
        html = client.get(f"/pulse/detail?path={SLOT}").data.decode()
        assert "startRename" not in html
        html = client.get(f"/pulse/detail?path={XY2}.x180_b").data.decode()
        assert "startRename" in html

    def test_delete_of_a_referenced_discovered_pulse_is_guarded(self, client):
        r = client.post("/api/pulse/delete", data={"path": f"{CPL}.cz_coupler"})
        assert r.status_code == 409 and b"coupler_flux_pulse" in r.data
