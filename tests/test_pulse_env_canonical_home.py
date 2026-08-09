"""chip_qclass must never write a home the SELECTED env cannot import.

Found live during the docs/98 all-pulse verification: on a fresh quam-0.6.0
chip the majority module prefix of the chip's own pulses is
``quam.components.pulses.`` (SquarePulse & co. still live there), and the
static catalog's ``_BY_QCLASS`` still lists that LEGACY home for classes the
modern stack moved out (GaussianFilteredSquarePulse -> quam_builder.common).
The prefix branch therefore wrote ``quam.components.pulses
.GaussianFilteredSquarePulse`` — a path the selected env cannot import — and
the whole state stopped ``Quam.load``-ing in that very env.

The rule: with an env roster ACTIVE and the roster KNOWING the class, the
env's own ``homes`` list is the only acceptable verification for a
prefix-derived write; a rejected prefix falls through to the env-canonical
branch. With no roster (or an unknown class) the static behavior stays
byte-identical.
"""

from quam_state_manager.core.pulse_catalog import (
    PULSE_CATALOG,
    apply_env_overlay,
    chip_qclass,
    env_field_filter,
)

_QC = "quam.components.pulses"
_QB_COMMON = "quam_builder.common.pulses"


def _chip_with_qc_majority():
    """A chip whose classed pulses give a strict majority prefix of _QC
    (mirrors a fresh quam-0.6.0 build: SquarePulse everywhere), with NO
    existing pulse of the class under test."""
    op = {"__class__": f"{_QC}.SquarePulse", "length": 100, "amplitude": 0.1}
    return {
        "qubits": {
            "q1": {"xy": {"operations": {"a": dict(op), "b": dict(op)}},
                   "z": {"operations": {"const": dict(op)}}},
            "q2": {"xy": {"operations": {"c": dict(op)}}},
        }
    }


class TestEnvCanonicalBeatsLegacyPrefix:
    def teardown_method(self):
        apply_env_overlay(None)

    def test_roster_known_class_rejects_unimportable_prefix(self):
        """Roster active, class known, env home != chip majority prefix →
        the env canonical wins; the legacy prefix must NOT be written."""
        apply_env_overlay({
            "GaussianFilteredSquarePulse": {
                "homes": [_QB_COMMON],
                "canonical": f"{_QB_COMMON}.GaussianFilteredSquarePulse",
                "fields": None,
            },
            "SquarePulse": {"homes": [_QC],
                            "canonical": f"{_QC}.SquarePulse",
                            "fields": None},
        })
        spec = PULSE_CATALOG["GaussianFilteredSquarePulse"]
        qc, how = chip_qclass(_chip_with_qc_majority(), spec)
        assert how == "env"
        assert qc == f"{_QB_COMMON}.GaussianFilteredSquarePulse"
        assert not qc.startswith(_QC + ".")

    def test_roster_verified_prefix_still_wins(self):
        """When the env DOES place the class under the chip's majority
        prefix, the prefix derivation keeps working (chip evidence first)."""
        apply_env_overlay({
            "GaussianPulse": {
                "homes": [_QC, _QB_COMMON],
                "canonical": f"{_QB_COMMON}.GaussianPulse",
                "fields": None,
            },
        })
        spec = PULSE_CATALOG["GaussianPulse"]
        qc, how = chip_qclass(_chip_with_qc_majority(), spec)
        assert (qc, how) == (f"{_QC}.GaussianPulse", "prefix")

    def test_roster_unknown_class_keeps_static_behavior(self):
        """Roster active but silent about this class → the static catalog's
        registered homes stay authoritative (legacy path, byte-identical)."""
        apply_env_overlay({"SquarePulse": {"homes": [_QC],
                                           "canonical": f"{_QC}.SquarePulse",
                                           "fields": None}})
        spec = PULSE_CATALOG["GaussianFilteredSquarePulse"]
        qc, how = chip_qclass(_chip_with_qc_majority(), spec)
        assert (qc, how) == (f"{_QC}.GaussianFilteredSquarePulse", "prefix")

    def test_no_overlay_golden_unchanged(self):
        """No roster at all → exactly the pre-fix derivation."""
        apply_env_overlay(None)
        spec = PULSE_CATALOG["GaussianFilteredSquarePulse"]
        qc, how = chip_qclass(_chip_with_qc_majority(), spec)
        assert (qc, how) == (f"{_QC}.GaussianFilteredSquarePulse", "prefix")

    def test_reused_evidence_still_first(self):
        """An existing same-class pulse on the chip stays authoritative even
        against a disagreeing roster (mid-migration chips must not flip)."""
        apply_env_overlay({
            "GaussianFilteredSquarePulse": {
                "homes": [_QB_COMMON],
                "canonical": f"{_QB_COMMON}.GaussianFilteredSquarePulse",
                "fields": None,
            },
        })
        merged = _chip_with_qc_majority()
        merged["qubits"]["q1"]["xy"]["operations"]["gf"] = {
            "__class__": f"{_QC}.GaussianFilteredSquarePulse", "length": 16}
        spec = PULSE_CATALOG["GaussianFilteredSquarePulse"]
        qc, how = chip_qclass(merged, spec)
        assert (qc, how) == (f"{_QC}.GaussianFilteredSquarePulse", "reused")


class TestEnvFieldFilter:
    """A field the selected env's class model doesn't know must never be
    written (unknown attribute = hard Quam.load failure; the live case was
    ``post_zero_padding_length`` vs the renamed ``padding_length``)."""

    def teardown_method(self):
        apply_env_overlay(None)

    def _tpl(self):
        return {"__class__": f"{_QB_COMMON}.GaussianFilteredSquarePulse",
                "amplitude": 0.1, "length": 100, "pulse_length": 100,
                "gaussian_filter_frequency_mhz": 200.0,
                "post_zero_padding_length": 0, "sample_rate": 1.0}

    def test_unknown_env_field_dropped_and_reported(self):
        roster = {"GaussianFilteredSquarePulse": {
            "homes": [_QB_COMMON], "canonical": None,
            "fields": {"amplitude": {}, "length": {}, "pulse_length": {},
                       "gaussian_filter_frequency_mhz": {},
                       "padding_length": {}, "sample_rate": {},
                       "axis_angle": {}, "id": {}, "digital_marker": {}},
        }}
        tpl = self._tpl()
        dropped = env_field_filter(tpl, "GaussianFilteredSquarePulse",
                                   roster=roster)
        assert dropped == ["post_zero_padding_length"]
        assert "post_zero_padding_length" not in tpl
        assert tpl["pulse_length"] == 100          # known fields survive
        assert tpl["__class__"].endswith("GaussianFilteredSquarePulse")

    def test_unprobed_fields_are_a_noop(self):
        """fields=None (probe couldn't dump them) ⇒ never guess."""
        roster = {"GaussianFilteredSquarePulse": {
            "homes": [_QB_COMMON], "canonical": None, "fields": None}}
        tpl = self._tpl()
        assert env_field_filter(tpl, "GaussianFilteredSquarePulse",
                                roster=roster) == []
        assert tpl == self._tpl()

    def test_no_roster_is_a_noop(self):
        apply_env_overlay(None)
        tpl = self._tpl()
        assert env_field_filter(tpl, "GaussianFilteredSquarePulse") == []
        assert tpl == self._tpl()


class TestNoneSlotCreateIndexesLeaves:
    """Filling an explicit-null gate slot goes through set_value, which used
    to index only the SLOT path — the new pulse's own leaves were invisible
    to search and later edits warned "not found in index" (docs/98)."""

    def test_created_leaves_enter_the_search_index(self, tmp_path):
        import importlib
        routes_tests = importlib.import_module("tests.test_pulses_routes")
        # reuse that module's synthetic chip + app plumbing
        folder = tmp_path / "chip"
        folder.mkdir()
        import json as _json
        (folder / "state.json").write_text(
            _json.dumps(routes_tests._make_state()), encoding="utf-8")
        (folder / "wiring.json").write_text(
            _json.dumps(routes_tests._make_wiring()), encoding="utf-8")
        from quam_state_manager.web.app import create_app
        app = create_app()
        app.config["TESTING"] = True
        app.instance_path = str(tmp_path / "inst")
        c = app.test_client()
        assert c.post("/load", data={"folder": str(folder)}).status_code \
            in (200, 302)
        store = routes_tests._store_of(app)
        # a builder-emitted explicit-null slot (the live case: cz_SNZ)
        store.state["qubit_pairs"]["qA1-qA2"]["macros"]["cz_unipolar"][
            "coupler_flux_pulse"] = None
        r = c.post("/api/pulse/create", data={
            "target_kind": "pair", "pair": "qA1-qA2", "gate": "cz_unipolar",
            "slot": "coupler_flux_pulse", "pulse_type": "SquarePulse",
            "length": "100", "amplitude": "0.1"})
        assert r.status_code == 200, r.get_data(as_text=True)[:200]
        target = ("qubit_pairs.qA1-qA2.macros.cz_unipolar"
                  ".coupler_flux_pulse.amplitude")
        assert any(e.dot_path == target for e in store.search_index.entries)
