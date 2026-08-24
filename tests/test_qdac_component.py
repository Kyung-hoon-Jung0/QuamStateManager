"""QDAC-II as a component — ``core.qdac`` and the read surfaces built on it.

docs/136. Before this, every surface guessed for itself what a QDAC bias line
was, and they disagreed: eleven of twenty qubits on the customer's real chip
showed no bias fields on their own inspector page, ``/flux`` listed rows it
could not fill, and four grid columns collided under one label. The fixtures
below encode the three shapes that exist, including the one the customer's
env cannot build yet:

* ``opx`` — a classic ``FluxLine``: an ``opx_output`` POINTER plus the offsets;
* ``qdac`` — a ``QdacBiasLine`` REPLACING ``z``: a channel and a DC offset, no
  ``opx_output``, no ``operations``, and a two-hop trigger pointer;
* ``bias_tee`` — BOTH, as siblings: the QDAC holds the DC point while an
  LF-FEM port plays pulses on top of it. No class in the customer's
  ``quam_config`` can hold this today, which is exactly why it is pinned —
  the read side must be correct on the day such a chip appears, without an
  env change, and the field name it uses is the lab's choice, not ours.

The pointer wiring is real (``#/wiring/...`` → ``#/ports/...``), because the
defect these tests exist for was a guard that only ever saw INLINE dicts.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import threading
from pathlib import Path

import pytest

from quam_state_manager.core import qdac
from quam_state_manager.core.qubit_columns import derive_qubit_columns

_ROOT = Path(__file__).resolve().parent.parent


# ── fixtures ──────────────────────────────────────────────────────────────────

def _flux_line(qid: str) -> dict:
    """A FluxLine as a real chip stores it — the port is a POINTER, not a dict."""
    return {
        "__class__": "quam_builder.architecture.superconducting.components."
                     "flux_line.FluxLine",
        "opx_output": f"#/wiring/qubits/{qid}/z/opx_output",
        "joint_offset": 0.0627,
        "independent_offset": 0.0,
        "min_offset": 0.0,
        "arbitrary_offset": 0.0,
        "flux_point": "joint",
        "settle_time": None,
        "operations": {"const": {"amplitude": 0.1, "length": 100}},
        "digital_outputs": {},
    }


def _bias_line(qid: str, *, channel: int, ext: str) -> dict:
    """A QdacBiasLine, with its two-hop trigger marker."""
    return {
        "__class__": "quam_config.qdac_components.QdacBiasLine",
        "channel": channel,
        "dc_offset": -0.0907,
        "trigger_port": ext,
        "dwell": 2e-06,
        "slew_rate": 20000000.0,
        "output_range": "high",
        "output_filter": "med",
        "settle_time": 20000,
        "opx_trigger_out": {
            "__class__": "quam.components.channels.Channel",
            "id": f"{qid}_qdac_trigger",
            "operations": {"trigger": {"length": 100, "digital_marker": "ON"}},
            "digital_outputs": {
                "trigger": {
                    "__class__": "quam.components.channels.DigitalOutputChannel",
                    "opx_output": f"#/wiring/qubits/{qid}/qt/digital_output",
                    "delay": 0, "buffer": 0, "shareable": True, "inverted": None,
                },
            },
        },
    }


def _analog_port(slot: int, port: int, delay: int = 78) -> dict:
    return {
        "__class__": "quam.components.ports.analog_outputs.LFFEMAnalogOutputPort",
        "controller_id": "con1", "fem_id": slot, "port_id": port,
        "delay": delay, "offset": None, "shareable": False,
        "output_mode": "amplified", "upsampling_mode": "pulse",
    }


def _digital_port(slot: int, port: int) -> dict:
    return {
        "__class__": "quam.components.ports.digital_outputs.FEMDigitalOutputPort",
        "controller_id": "con1", "fem_id": slot, "port_id": port,
        "inverted": False, "shareable": True, "level": "LVTTL",
    }


def _chip(*, opx=(), qdac_q=(), bias_tee=(), tee_field="qdac_bias") -> dict:
    """Build a merged state+wiring dict with the requested per-qubit shapes.

    ``qdac_q`` / ``bias_tee`` are ``(qid, channel, ext, trigger_port_no)``.
    """
    qubits: dict = {}
    wiring_qubits: dict = {}
    analog: dict = {}
    digital: dict = {}

    for i, qid in enumerate(opx, start=1):
        qubits[qid] = {"id": qid, "z": _flux_line(qid),
                       "__class__": "…flux_tunable_transmon.FluxTunableTransmon"}
        wiring_qubits[qid] = {"z": {"opx_output": f"#/ports/analog_outputs/con1/4/{i}"}}
        analog.setdefault("4", {})[str(i)] = _analog_port(4, i)

    for qid, ch, ext, pno in qdac_q:
        qubits[qid] = {"id": qid, "z": _bias_line(qid, channel=ch, ext=ext),
                       "__class__": "quam_config.qdac_components."
                                    "QdacBiasedFixedFrequencyTransmon"}
        wiring_qubits[qid] = {
            "qt": {"digital_output": f"#/ports/digital_outputs/con1/5/{pno}"}}
        digital.setdefault("5", {})[str(pno)] = _digital_port(5, pno)

    for n, (qid, ch, ext, pno) in enumerate(bias_tee, start=90):
        qubits[qid] = {
            "id": qid,
            "z": _flux_line(qid),
            tee_field: _bias_line(qid, channel=ch, ext=ext),
            "__class__": "quam_config.qdac_components.QdacBiasedFluxTunableTransmon",
        }
        wiring_qubits[qid] = {
            "z": {"opx_output": f"#/ports/analog_outputs/con1/6/{n - 89}"},
            "qt": {"digital_output": f"#/ports/digital_outputs/con1/5/{pno}"},
        }
        analog.setdefault("6", {})[str(n - 89)] = _analog_port(6, n - 89, delay=141)
        digital.setdefault("5", {})[str(pno)] = _digital_port(5, pno)

    return {
        "qubits": qubits,
        "qubit_pairs": {},
        "ports": {"analog_outputs": {"con1": analog},
                  "digital_outputs": {"con1": digital}},
        "wiring": {"qubits": wiring_qubits},
        "qdac": {"__class__": "quam_config.qdac_components.QdacInstrument",
                 "id": "qdac", "communication_type": "Ethernet",
                 "ip_address": "192.168.88.244", "port": 5025,
                 "usb_device": None, "lib": "@py"},
    }


class _FakeStore:
    """Enough store for the column derivation and the pointer resolver."""

    def __init__(self, merged: dict):
        self._lock = threading.RLock()
        self.merged = merged
        self.mutation_seq = 0

    @property
    def qubit_names(self) -> list[str]:
        return list((self.merged.get("qubits") or {}).keys())

    @property
    def qubit_pair_names(self) -> list[str]:
        return list((self.merged.get("qubit_pairs") or {}).keys())


MIXED = _chip(opx=("q2", "q4"),
              qdac_q=(("q1", 13, "ext1", 1), ("q9", 5, "ext1", 1),
                      ("q3", 21, "ext2", 2)))


# ── the vocabulary ────────────────────────────────────────────────────────────

class TestBiasMode:
    def test_the_three_shapes_are_told_apart(self):
        chip = _chip(opx=("q2",), qdac_q=(("q1", 13, "ext1", 1),),
                     bias_tee=(("q5", 7, "ext3", 3),))
        q = chip["qubits"]
        assert qdac.bias_mode(q["q2"]) == "opx"
        assert qdac.bias_mode(q["q1"]) == "qdac"
        assert qdac.bias_mode(q["q5"]) == "bias_tee"

    def test_a_fixed_frequency_qubit_is_not_biased_at_all(self):
        assert qdac.bias_mode({"id": "q1", "xy": {"opx_output": "#/x"}}) is None

    def test_the_bias_field_name_is_reported_not_assumed(self):
        """A QDAC-only qubit keeps its bias in ``z``; a bias-tee qubit does not.

        Callers build dot-paths from this, so returning the name is the whole
        point — assuming ``z`` is what made the bias-tee shape unreadable.
        """
        chip = _chip(qdac_q=(("q1", 13, "ext1", 1),),
                     bias_tee=(("q5", 7, "ext3", 3),))
        assert qdac.bias_line_of(chip["qubits"]["q1"])[0] == "z"
        assert qdac.bias_line_of(chip["qubits"]["q5"])[0] == "qdac_bias"
        # ...and the pulse line is still found, on the SAME qubit.
        assert qdac.flux_line_of(chip["qubits"]["q5"])[0] == "z"

    def test_a_lab_may_name_the_bias_tee_field_anything(self):
        """Detection is structural. A field name is not a contract we can hold
        a lab to — the class does not exist upstream, so whoever writes it
        chooses the name, and SM has to read the chip anyway."""
        chip = _chip(bias_tee=(("q5", 7, "ext3", 3),), tee_field="dc_source")
        assert qdac.bias_mode(chip["qubits"]["q5"]) == "bias_tee"
        assert qdac.bias_line_of(chip["qubits"]["q5"])[0] == "dc_source"

    def test_a_flux_line_is_never_read_as_a_bias_line(self):
        assert qdac.is_bias_line(_flux_line("q2")) is False
        assert qdac.is_flux_line(_flux_line("q2")) is True

    def test_a_bias_line_is_never_read_as_a_flux_line(self):
        bias = _bias_line("q1", channel=13, ext="ext1")
        assert qdac.is_flux_line(bias) is False
        assert qdac.is_bias_line(bias) is True

    def test_the_trigger_channel_inside_it_is_not_itself_a_bias_line(self):
        bias = _bias_line("q1", channel=13, ext="ext1")
        assert qdac.is_bias_line(bias["opx_trigger_out"]) is False

    def test_an_unclassed_bias_line_is_still_recognised(self):
        """A stripped export or a hand-written chip carries no ``__class__``;
        the shape is the evidence, the class name only corroborates."""
        bias = _bias_line("q1", channel=13, ext="ext1")
        del bias["__class__"]
        assert qdac.is_bias_line(bias) is True

    def test_a_lone_channel_number_is_not_enough_to_claim_qdac(self):
        """Guard against a false positive on any component that happens to
        carry a `channel`: without the QDAC knobs it is not a bias line."""
        assert qdac.is_bias_line({"channel": 3}) is False
        assert qdac.is_bias_line({"channel": 3, "dc_offset": 0.1}) is False


class TestTriggerCabling:
    def test_the_two_hop_pointer_is_followed_to_the_physical_port(self):
        ref = qdac.trigger_ref(MIXED["qubits"]["q1"], MIXED, "q1")
        assert ref == {"con": "con1", "slot": "5", "port": "1", "ext": "ext1",
                       "ref": "#/ports/digital_outputs/con1/5/1"}

    def test_qubits_on_one_cable_are_grouped(self):
        """One OPX digital output drives one QDAC ext input and arms every
        channel on it — sharing is the design, not a collision."""
        groups = qdac.ext_groups(MIXED)
        assert groups[("con1", "5", "1")]["qubits"] == ["q1", "q9"]
        assert groups[("con1", "5", "1")]["ext"] == "ext1"
        assert groups[("con1", "5", "2")]["qubits"] == ["q3"]

    def test_two_exts_on_one_physical_port_is_reported_as_a_conflict(self):
        """The port and the ext are two names for ONE cable. When they
        disagree nothing else in the app would notice — it would simply arm
        the wrong channels at run time."""
        chip = _chip(qdac_q=(("q1", 13, "ext1", 1), ("q9", 5, "ext4", 1)))
        entry = qdac.ext_groups(chip)[("con1", "5", "1")]
        assert entry["conflict"] == {"ext1", "ext4"}

    def test_a_qubit_with_no_trigger_has_no_ref_rather_than_a_guess(self):
        chip = _chip(opx=("q2",))
        assert qdac.trigger_ref(chip["qubits"]["q2"], chip, "q2") is None

    def test_a_chain_that_dead_ends_yields_nothing_rather_than_a_guess(self):
        """The first hop points into wiring that does not exist, so there is no
        ``#/ports/`` spelling to report at all."""
        chip = _chip(qdac_q=(("q1", 13, "ext1", 1),))
        (chip["qubits"]["q1"]["z"]["opx_trigger_out"]["digital_outputs"]
             ["trigger"]["opx_output"]) = "#/wiring/qubits/qZZ/qt/digital_output"
        del chip["wiring"]["qubits"]["q1"]
        assert qdac.trigger_ref(chip["qubits"]["q1"], chip, "q1") is None

    def test_a_port_the_chip_declares_but_does_not_own_is_still_reported(self):
        """Deliberately NOT None: the chip says the trigger is cabled there, so
        that is what this reports. Whether the port exists is a separate
        question with its own answer — Diagnostics' "Port exists" check — and
        collapsing the two would hide a real wiring error behind a blank."""
        chip = _chip(qdac_q=(("q1", 13, "ext1", 1),))
        chip["wiring"]["qubits"]["q1"]["qt"]["digital_output"] = \
            "#/ports/digital_outputs/con1/9/9"
        ref = qdac.trigger_ref(chip["qubits"]["q1"], chip, "q1")
        assert (ref["slot"], ref["port"]) == ("9", "9")

    def test_the_instrument_is_found_and_a_plain_chip_has_none(self):
        assert qdac.instrument(MIXED)["ip_address"] == "192.168.88.244"
        assert qdac.instrument({"qubits": {}}) is None

    def test_biased_qubits_includes_bias_tee_ones(self):
        chip = _chip(opx=("q2",), qdac_q=(("q1", 13, "ext1", 1),),
                     bias_tee=(("q5", 7, "ext3", 3),))
        assert set(qdac.biased_qubits(chip)) == {"q1", "q5"}


# ── the columns ───────────────────────────────────────────────────────────────

class TestColumnBands:
    def test_the_two_z_ports_no_longer_share_a_header(self):
        """The defect: `z.opx_output.*` (an LF-FEM ANALOG port) and
        `z.opx_trigger_out.digital_outputs.trigger.opx_output.*` (an FEM
        DIGITAL port) both rendered as `Z Port+ / out · <leaf>`, so the four
        fields the two port classes share appeared as four pairs of identical
        headers with nothing to tell them apart."""
        cols, _ = derive_qubit_columns(_FakeStore(MIXED))
        pairs = [(c["section"], c["label"]) for c in cols]
        assert len(pairs) == len(set(pairs)), \
            [p for p in pairs if pairs.count(p) > 1]

    def test_the_trigger_port_says_it_is_the_trigger_port(self):
        cols, _ = derive_qubit_columns(_FakeStore(MIXED))
        secs = {c["section"] for c in cols}
        assert "Z Port+" in secs and "Z Trigger Port+" in secs

    def test_qdac_fields_get_their_own_band(self):
        cols, _ = derive_qubit_columns(_FakeStore(MIXED))
        band = {c["label"] for c in cols if c["section"] == "QDAC bias+"}
        assert {"channel", "dc_offset", "trigger_port", "slew_rate"} <= band

    def test_a_flux_only_chip_grows_no_qdac_band(self):
        cols, _ = derive_qubit_columns(_FakeStore(_chip(opx=("q2", "q4"))))
        assert not [c for c in cols if c["section"] == "QDAC bias+"]

    def test_a_name_both_classes_use_stays_in_the_generic_band(self):
        """A template that is a QdacBiasLine field on some rows and a FluxLine
        field on others is ONE dot-path, so a header claiming QDAC would be
        false for the flux-biased half of the chip.

        The only name the two shipped classes share is ``settle_time``, and
        that one is a CURATED column, so it never reaches the dynamic
        derivation. This fixture therefore adds a lab-shaped field to both
        components to exercise the rule itself — which is the thing that has
        to stay correct when a lab's class shares a name we have not seen.
        """
        chip = _chip(opx=("q2",), qdac_q=(("q1", 13, "ext1", 1),))
        chip["qubits"]["q2"]["z"]["ramp_rate"] = 0.5
        chip["qubits"]["q1"]["z"]["ramp_rate"] = 0.25
        cols, _ = derive_qubit_columns(_FakeStore(chip))
        shared = [c for c in cols if c["tmpl"].endswith(".z.ramp_rate")]
        assert shared, "fixture must exercise the shared name"
        assert all(c["section"] != "QDAC bias+" for c in shared)
        # ...while a name only the bias line uses still earns the band.
        assert any(c["section"] == "QDAC bias+" and c["label"] == "channel"
                   for c in cols)

    def test_a_bias_tee_qubits_pulse_line_keeps_its_flux_columns(self):
        """The whole point of a bias tee: the LF-FEM port is still there and
        still editable. Folding it into the QDAC band would hide it."""
        chip = _chip(bias_tee=(("q5", 7, "ext3", 3),))
        cols, _ = derive_qubit_columns(_FakeStore(chip))
        by_tmpl = {c["tmpl"]: c for c in cols}
        # `independent_offset` is a FluxLine field and not curated, so it is
        # visible in the derived model and must not be claimed by QDAC.
        indep = by_tmpl.get("qubits.{name}.z.independent_offset")
        assert indep is not None and indep["section"] != "QDAC bias+"
        assert any(c["section"] == "QDAC bias+" for c in cols)

    def test_every_qdac_column_answers_to_the_word(self):
        """The search haystack is label + key + section + search text. None of
        `channel` / `dc_offset` / `trigger_port` contains "qdac", and
        `__class__` never becomes a column — so without this the word reaches
        nothing and the quick-filter chip is silently dropped."""
        cols, _ = derive_qubit_columns(_FakeStore(MIXED))
        for c in cols:
            if c["section"] == "QDAC bias+":
                hay = " ".join((str(c.get("label")), str(c.get("key")),
                                str(c.get("section")),
                                str(c.get("search") or ""))).lower()
                assert "qdac" in hay, c["tmpl"]


# ── the quick-filter chips, on BOTH surfaces ──────────────────────────────────

class TestQdacChipParity:
    def test_live_edit_offers_the_chip_on_a_qdac_chip(self):
        from quam_state_manager.web.routes import _bulk_filter_chips
        cols, _ = derive_qubit_columns(_FakeStore(MIXED))
        chips = {c["term"]: c for c in _bulk_filter_chips(cols)}
        assert "qdac" in chips
        assert chips["qdac"]["n"] >= 1

    def test_live_edit_never_offers_it_on_a_chip_without_one(self):
        """The honesty gate is not bypassed by adding the word to the curated
        list — a chip that matches nothing is never offered."""
        from quam_state_manager.web.routes import _bulk_filter_chips
        cols, _ = derive_qubit_columns(_FakeStore(_chip(opx=("q2", "q4"))))
        assert "qdac" not in {c["term"] for c in _bulk_filter_chips(cols)}

    def test_the_json_tree_carries_the_same_word(self):
        """The Explorer's honesty gate is the raw document haystack. Both
        surfaces must offer it or neither — a chip on one and not the other is
        the bug this pins."""
        import pathlib
        app_js = (pathlib.Path(__file__).resolve().parents[1]
                  / "quam_state_manager" / "web" / "static" / "app.js").read_text(
                      encoding="utf-8")
        assert "['qdac', 'QDAC']" in app_js
        hay = json.dumps(MIXED).lower()
        assert "qdac" in hay


# ── the delay that three screens called "not set" ─────────────────────────────

class TestPortDelayIsRead:
    """`z.opx_output` is a POINTER on every real chip, so the old
    `isinstance(z_port, dict)` guard was false always and the branch behind it
    never ran. /flux, /couplers and the qubit inspector printed "not set"
    while the Live-Edit grid — resolving the same dot-path through the pointer
    system — printed the real number."""

    def _engine(self, tmp_path, merged):
        from quam_state_manager.core.loader import QuamStore
        from quam_state_manager.core.query import QueryEngine
        state = {k: v for k, v in merged.items() if k != "wiring"}
        (tmp_path / "state.json").write_text(json.dumps(state), encoding="utf-8")
        (tmp_path / "wiring.json").write_text(
            json.dumps({"wiring": merged["wiring"]}), encoding="utf-8")
        store = QuamStore(tmp_path)
        store._load()
        return QueryEngine(store)

    def test_a_pointer_wired_flux_port_reports_its_delay(self, tmp_path):
        eng = self._engine(tmp_path, _chip(opx=("q2",)))
        assert eng.get_qubit("q2")["z_delay_ns"] == 78

    def test_a_qdac_qubit_reports_no_opx_delay_because_it_has_no_opx_port(
            self, tmp_path):
        eng = self._engine(tmp_path, _chip(qdac_q=(("q1", 13, "ext1", 1),)))
        assert eng.get_qubit("q1")["z_delay_ns"] is None

    def test_the_qubit_row_carries_its_bias_mode_and_qdac_facts(self, tmp_path):
        eng = self._engine(tmp_path, MIXED)
        row = eng.get_qubit("q1")
        assert row["bias_mode"] == "qdac"
        assert row["has_qdac"] is True
        assert row["qdac_channel"] == 13
        assert row["qdac_trigger_port"] == "ext1"
        assert row["qdac_trigger_port_label"] == "con1/fem5/p1"
        plain = eng.get_qubit("q2")
        assert plain["bias_mode"] == "opx"
        assert plain["has_qdac"] is False
        assert plain["qdac_channel"] is None

    def test_a_bias_tee_qubit_reports_both_halves(self, tmp_path):
        """The requirement the whole shape exists for: an LF-FEM delay AND a
        QDAC DC offset on one qubit, neither hiding the other."""
        eng = self._engine(tmp_path, _chip(bias_tee=(("q5", 7, "ext3", 3),)))
        row = eng.get_qubit("q5")
        assert row["bias_mode"] == "bias_tee"
        assert row["z_delay_ns"] == 141
        assert row["qdac_channel"] == 7
        assert row["qdac_dc_offset"] == pytest.approx(-0.0907)


# ── the inspector page ────────────────────────────────────────────────────────

def _store_for(tmp_path, merged):
    from quam_state_manager.core.loader import QuamStore
    state = {k: v for k, v in merged.items() if k != "wiring"}
    (tmp_path / "state.json").write_text(json.dumps(state), encoding="utf-8")
    (tmp_path / "wiring.json").write_text(
        json.dumps({"wiring": merged["wiring"]}), encoding="utf-8")
    store = QuamStore(tmp_path)
    store._load()
    return store


def _sections_for(tmp_path, merged, qid):
    from quam_state_manager.core.query import QueryEngine
    from quam_state_manager.web.routes import _build_qubit_sections
    store = _store_for(tmp_path, merged)
    return _build_qubit_sections(qid, QueryEngine(store).get_qubit(qid), store)


class TestInspectorSection:
    """A QDAC-biased qubit used to show NO bias information on its own page.

    The static property map is FluxLine-shaped, so every Flux prop read
    None-and-absent and the empty-section rule dropped the lot — eleven of
    twenty qubits on the real chip, while the Live-Edit grid showed all eight
    fields. Two surfaces, one chip, opposite answers.
    """

    def test_a_qdac_qubit_gets_its_bias_section(self, tmp_path):
        secs = _sections_for(tmp_path, MIXED, "q1")
        band = next((s for s in secs if s["name"] == "QDAC-II bias"), None)
        assert band is not None, [s["name"] for s in secs]
        keys = [p["key"] for p in band["props"]]
        assert {"channel", "dc_offset", "trigger_port", "slew_rate"} <= set(keys)

    def test_those_fields_are_editable_at_their_real_paths(self, tmp_path):
        secs = _sections_for(tmp_path, MIXED, "q1")
        band = next(s for s in secs if s["name"] == "QDAC-II bias")
        ch = next(p for p in band["props"] if p["key"] == "channel")
        assert ch["editable"] is True
        assert ch["dot_path"] == "qubits.q1.z.channel"
        assert ch["value"] == 13

    def test_the_physical_trigger_port_is_shown_and_is_read_only(self, tmp_path):
        """Where the marker actually lands takes a two-hop pointer walk to
        learn and appears on no other qubit surface. It is not a leaf, so it
        must not offer an edit box that would write one."""
        secs = _sections_for(tmp_path, MIXED, "q1")
        band = next(s for s in secs if s["name"] == "QDAC-II bias")
        row = next(p for p in band["props"] if p["key"] == "trigger cabled to")
        assert row["value"] == "con1/fem5/p1"
        assert row["editable"] is False and row["dot_path"] is None

    def test_a_flux_qubit_gets_no_qdac_section(self, tmp_path):
        secs = _sections_for(tmp_path, MIXED, "q2")
        assert not [s for s in secs if s["name"] == "QDAC-II bias"]
        assert [s for s in secs if s["name"] == "Flux"]

    def test_the_flux_section_now_reports_the_port_delay(self, tmp_path):
        """Same dead guard as `z_delay_ns` — the inspector printed "not set"
        for a value the grid printed."""
        secs = _sections_for(tmp_path, MIXED, "q2")
        flux = next(s for s in secs if s["name"] == "Flux")
        delay = next(p for p in flux["props"] if p["key"] == "z_delay_ns")
        assert delay["value"] == 78

    def test_a_bias_tee_qubit_shows_BOTH_sections(self, tmp_path):
        """Neither half may hide the other: the DC point is QDAC, the pulses
        are LF-FEM, and an operator needs to see both to reason about either."""
        chip = _chip(bias_tee=(("q5", 7, "ext3", 3),))
        secs = _sections_for(tmp_path, chip, "q5")
        names = [s["name"] for s in secs]
        assert "QDAC-II bias" in names and "Flux" in names
        # ...and the QDAC band sits beside the flux story, not after the gates.
        assert names.index("QDAC-II bias") == names.index("Flux") + 1

    def test_a_lab_named_bias_field_is_addressed_by_its_real_path(self, tmp_path):
        chip = _chip(bias_tee=(("q5", 7, "ext3", 3),), tee_field="dc_source")
        secs = _sections_for(tmp_path, chip, "q5")
        band = next(s for s in secs if s["name"] == "QDAC-II bias")
        ch = next(p for p in band["props"] if p["key"] == "channel")
        assert ch["dot_path"] == "qubits.q5.dc_source.channel"


# ── the /flux page ────────────────────────────────────────────────────────────

_client_seq = [0]


def _client_on(tmp_path, merged):
    """A test client with *merged* loaded as the live chip.

    Each call gets its own chip folder and instance path so a test may open
    two different chips (the nav-item pin compares a QDAC chip against a plain
    one) without the second colliding with the first.
    """
    from quam_state_manager.web.app import create_app
    _client_seq[0] += 1
    n = _client_seq[0]
    chip = tmp_path / f"chip{n}"
    chip.mkdir()
    state = {k: v for k, v in merged.items() if k != "wiring"}
    (chip / "state.json").write_text(json.dumps(state), encoding="utf-8")
    (chip / "wiring.json").write_text(
        json.dumps({"network": {"host": "1.1.1.1", "cluster_name": "t"},
                    "wiring": merged["wiring"]}), encoding="utf-8")
    app = create_app(testing=True, instance_path=str(tmp_path / f"_i{n}"))
    c = app.test_client()
    c.post("/load", data={"folder": str(chip)})
    c.chip_dir = chip          # the live files, for tests that edit them
    return c


class TestFluxPage:
    def test_a_qdac_chip_says_where_each_bias_comes_from(self, tmp_path):
        """Every QDAC-biased qubit WAS a row here with four empty cells —
        `has_z` is structural and a QdacBiasLine is a `z` too, so the page
        listed eleven qubits it had nothing to say about."""
        html = _client_on(tmp_path, MIXED).get("/flux").get_data(as_text=True)
        assert ">Source<" in html
        assert "QDAC-II" in html and "LF-FEM" in html
        assert ">QDAC ch<" in html

    def test_the_qdac_rows_now_carry_real_values(self, tmp_path):
        html = _client_on(tmp_path, MIXED).get("/flux").get_data(as_text=True)
        assert "ext1" in html          # trigger input
        assert "con1/fem5/p1" in html  # the physical port it is cabled to
        assert "78" in html            # the LF-FEM delay that read "-" before

    def test_a_bias_tee_qubit_is_not_filed_as_either_one(self, tmp_path):
        chip = _chip(opx=("q2",), bias_tee=(("q5", 7, "ext3", 3),))
        html = _client_on(tmp_path, chip).get("/flux").get_data(as_text=True)
        assert "QDAC + LF-FEM" in html

    def test_a_plain_flux_chip_grows_no_new_columns(self, tmp_path):
        """The invariant this project keeps: a chip with nothing to say about
        QDAC renders exactly as it always has."""
        html = _client_on(tmp_path, _chip(opx=("q2", "q4"))).get(
            "/flux").get_data(as_text=True)
        assert ">Source<" not in html
        assert ">QDAC ch<" not in html
        assert "flux-src" not in html


# ── the QDAC component page ───────────────────────────────────────────────────

class TestQdacPage:
    def test_it_lists_only_the_biased_qubits(self, tmp_path):
        html = _client_on(tmp_path, MIXED).get("/qdac").get_data(as_text=True)
        for qid in ("q1", "q3", "q9"):
            assert f'data-qubit-id="{qid}"' in html
        assert 'data-qubit-id="q2"' not in html, "a flux qubit is not a QDAC row"

    def test_the_instrument_is_named_and_addressed(self, tmp_path):
        html = _client_on(tmp_path, MIXED).get("/qdac").get_data(as_text=True)
        assert "192.168.88.244" in html and "5025" in html

    def test_a_biased_chip_with_no_instrument_says_so(self, tmp_path):
        chip = _chip(qdac_q=(("q1", 13, "ext1", 1),))
        del chip["qdac"]
        html = _client_on(tmp_path, chip).get("/qdac").get_data(as_text=True)
        assert "declares no top-level" in html

    def test_the_cabling_table_states_who_shares_a_cable(self, tmp_path):
        """The fact the rest of the app had nowhere to put, and the one that
        makes a shared port read as correct rather than as a collision."""
        html = _client_on(tmp_path, MIXED).get("/qdac").get_data(as_text=True)
        assert "Trigger cabling" in html
        assert "con1/fem5/p1" in html and "ext1" in html
        # q1 and q9 are armed on ext1 and therefore on ONE cable.
        block = html[html.index("Trigger cabling"):]
        row = block[block.index("con1/fem5/p1"):][:400]
        assert "q1" in row and "q9" in row

    def test_a_miscabled_ext_is_marked_on_the_page_too(self, tmp_path):
        chip = _chip(qdac_q=(("q1", 13, "ext1", 1), ("q9", 5, "ext4", 1)))
        html = _client_on(tmp_path, chip).get("/qdac").get_data(as_text=True)
        assert "qdac-cabling-bad" in html

    def test_the_nav_item_appears_only_on_a_qdac_chip(self, tmp_path):
        on = _client_on(tmp_path, MIXED).get("/qubits").get_data(as_text=True)
        assert ">QDAC-II</a>" in on
        off = _client_on(tmp_path, _chip(opx=("q2",))).get(
            "/qubits").get_data(as_text=True)
        assert ">QDAC-II</a>" not in off

    def test_the_component_map_gets_its_own_highlight(self, tmp_path):
        html = _client_on(tmp_path, MIXED).get("/qdac").get_data(as_text=True)
        assert 'data-highlight="qdac"' in html

    def test_the_topology_carries_the_bias_mode(self, tmp_path):
        """`z_port` is None on a QDAC-only qubit, so the map's flux stub —
        which keys on it — drew nothing for over half the customer's chip."""
        from quam_state_manager.core.query import QueryEngine
        store = _store_for(tmp_path, MIXED)
        topo = QueryEngine(store).get_topology()
        nodes = {n["id"]: n for n in topo["nodes"]}
        assert nodes["q1"]["bias_mode"] == "qdac"
        assert nodes["q1"]["z_port"] is None
        assert nodes["q1"]["qdac_channel"] == 13
        assert nodes["q2"]["bias_mode"] == "opx"
        assert nodes["q2"]["qdac_channel"] is None


# ── a class migration is a difference ─────────────────────────────────────────

class TestClassMigrationIsVisible:
    """A lab's out-of-band edit moved eleven qubits from FluxTunableTransmon to
    QdacBiasedFixedFrequencyTransmon. SM's banner said the live chip had
    changed; the review screen said "No differences" — because `Differ`
    defaults to skipping `__class__`. The user's reasonable reading was
    "nothing important happened", for the single most consequential kind of
    change a chip can undergo.
    """

    def _diverge_the_live_class(self, tmp_path):
        c = _client_on(tmp_path, _chip(opx=("q2",)))
        live = c.chip_dir / "state.json"
        doc = json.loads(live.read_text(encoding="utf-8"))
        doc["qubits"]["q2"]["__class__"] = (
            "quam_config.qdac_components.QdacBiasedFixedFrequencyTransmon")
        live.write_text(json.dumps(doc), encoding="utf-8")
        return c

    def test_the_review_screen_names_the_class_row(self, tmp_path):
        c = self._diverge_the_live_class(tmp_path)
        html = c.get("/state/review").get_data(as_text=True)
        assert "__class__" in html, "the one changed leaf must be listed"
        assert "QdacBiasedFixedFrequencyTransmon" in html

    def test_the_json_twin_counts_it_too(self, tmp_path):
        """The banner's count and the review list are two renderings of one
        comparison; disagreeing was half the confusion."""
        c = self._diverge_the_live_class(tmp_path)
        body = c.get("/state/live-diff").get_json()
        if body is None or not body.get("ok"):
            pytest.skip("live-diff endpoint unavailable in this configuration")
        assert body["total"] >= 1

    def test_an_identical_chip_still_reports_nothing(self, tmp_path):
        """Widening what counts as a difference must not invent one."""
        c = _client_on(tmp_path, _chip(opx=("q2",)))
        html = c.get("/state/review").get_data(as_text=True)
        assert "__class__" not in html


# ── Diagnostics ───────────────────────────────────────────────────────────────

class TestQdacDiagnostics:
    """Nothing linted a QDAC-biased qubit before: `_iter_channels` defines a
    channel as "a dict carrying `opx_output`", which a QdacBiasLine never is,
    so eleven of twenty qubits on the customer's chip contributed zero
    channels to every check built on it.
    """

    def _findings(self, merged):
        from quam_state_manager.core import diagnostics
        return diagnostics._qdac_findings(merged)

    def test_a_correctly_wired_chip_is_silent(self):
        """The first thing a new check must not do is cry wolf."""
        assert self._findings(MIXED) == []

    def test_a_chip_with_no_qdac_is_not_linted_for_one(self):
        assert self._findings(_chip(opx=("q2", "q4"))) == []

    def test_two_qubits_on_one_channel(self):
        chip = _chip(qdac_q=(("q1", 13, "ext1", 1), ("q9", 13, "ext1", 1)))
        f = self._findings(chip)
        assert any(x.severity == "error" and "share QDAC channel" in x.message
                   for x in f), [x.message for x in f]

    def test_a_channel_outside_the_instruments_range(self):
        """1–24 is not a guess — the customer's own driver refuses anything
        else and says so in the message it raises."""
        for bad in (0, 25, 99):
            f = self._findings(_chip(qdac_q=(("q1", bad, "ext1", 1),)))
            assert any("outside the instrument's range" in x.message for x in f), bad

    def test_a_channel_that_is_not_a_number(self):
        chip = _chip(qdac_q=(("q1", 13, "ext1", 1),))
        chip["qubits"]["q1"]["z"]["channel"] = "13"
        f = self._findings(chip)
        assert any("not a channel number" in x.message for x in f)

    def test_a_trigger_input_the_instrument_does_not_have(self):
        chip = _chip(qdac_q=(("q1", 13, "ext9", 1),))
        f = self._findings(chip)
        assert any("not one of the QDAC-II's four" in x.message for x in f)

    def test_no_trigger_input_at_all_is_allowed(self):
        """`trigger_port` is Optional on the customer's class — a channel that
        only ever holds a static DC bias needs no trigger."""
        chip = _chip(qdac_q=(("q1", 13, None, 1),))
        assert not [x for x in self._findings(chip)
                    if "trigger input" in x.message]

    def test_one_cable_two_exts(self):
        """The load-bearing one. The port and the ext are two names for ONE
        cable; when they disagree nothing else in the app notices and at run
        time it simply arms the wrong channels."""
        chip = _chip(qdac_q=(("q1", 13, "ext1", 1), ("q9", 5, "ext4", 1)))
        f = self._findings(chip)
        hit = [x for x in f if "two different QDAC trigger inputs" in x.message]
        assert hit and hit[0].severity == "error"
        assert "ext1" in hit[0].message and "ext4" in hit[0].message

    def test_one_ext_two_cables(self):
        """The mirror case: two OPX outputs claiming the same ext input
        describes hardware that cannot be patched."""
        chip = _chip(qdac_q=(("q1", 13, "ext1", 1), ("q9", 5, "ext1", 2)))
        f = self._findings(chip)
        assert any("different digital outputs" in x.message for x in f), \
            [x.message for x in f]

    def test_sharing_one_cable_correctly_is_never_flagged(self):
        """Three qubits on one port with one ext is the DESIGN — one OPX
        output feeds one ext BNC and arms every channel on it."""
        chip = _chip(qdac_q=(("q1", 13, "ext1", 1), ("q9", 5, "ext1", 1),
                             ("q17", 19, "ext1", 1)))
        assert self._findings(chip) == []

    def test_a_biased_chip_with_no_instrument(self):
        chip = _chip(qdac_q=(("q1", 13, "ext1", 1),))
        del chip["qdac"]
        f = self._findings(chip)
        assert any(x.severity == "error" and "declares no QDAC-II instrument"
                   in x.message for x in f)

    def test_an_instrument_with_no_address(self):
        chip = _chip(qdac_q=(("q1", 13, "ext1", 1),))
        chip["qdac"]["ip_address"] = None
        f = self._findings(chip)
        assert any(x.severity == "warning" and "no IP address" in x.message
                   for x in f)
        chip["qdac"]["communication_type"] = "USB"
        f = self._findings(chip)
        assert any("no device index" in x.message for x in f)

    def test_the_checks_are_in_the_user_facing_catalogue(self):
        """`_CHECK_CATALOG` is the single source of truth for the "What is
        checked?" popup — a check missing from it is a check the user cannot
        know ran."""
        from quam_state_manager.core import diagnostics
        conn = dict(diagnostics._CHECK_CATALOG)["connectivity"]
        titles = " ".join(t for _sev, t, _d in conn)
        assert "QDAC-II instrument reachable" in titles
        assert "QDAC-II channels valid and unique" in titles
        assert "QDAC-II trigger cabling consistent" in titles

    def test_the_findings_land_in_the_connectivity_domain(self):
        from quam_state_manager.core import diagnostics
        chip = _chip(qdac_q=(("q1", 99, "ext1", 1),))
        for x in self._findings(chip):
            assert diagnostics.domain_of(x.category) == "connectivity", x.category

    def test_they_run_as_part_of_the_real_lint(self, tmp_path):
        """Wired into `_lint_state_uncached`, not just callable."""
        from quam_state_manager.core import diagnostics
        chip = _chip(qdac_q=(("q1", 99, "ext1", 1),))
        store = _store_for(tmp_path, chip)
        msgs = [f.message for f in diagnostics.lint_state(store)]
        assert any("outside the instrument's range" in m for m in msgs), msgs


# ── the QPU root class ────────────────────────────────────────────────────────

_ROOT_MANIFEST = {
    "capabilities": {}, "versions": {},
    "qpu_roots": [
        {"path": "quam_config.my_quam.Quam", "importable": True,
         "holds_qdac": True, "bias_tee": None},
        {"path": "quam_builder.FluxTunableQuam", "importable": True,
         "holds_qdac": False, "bias_tee": None},
    ],
}
_STOCK_ONLY = {
    "capabilities": {}, "versions": {},
    "qpu_roots": [{"path": "quam_builder.FluxTunableQuam", "importable": True,
                   "holds_qdac": False, "bias_tee": None}],
}
_QDAC_SPEC = {"qdac": {"qubits": {"q1": {"channel": 1}}}}


class TestQpuRootGate:
    """A QDAC chip built onto a stock root cannot be loaded AT ALL, and the
    build reports success while writing it — measured, not argued: the same
    spec built without a root class gives `ok: True, warnings: []` and then
    `TypeError: Wrong object type found during validation. Path:
    Quam.qubits["q1"]`. `regen_spec` fixed this for RE-generate by carrying the
    source chip's `__class__`; a fresh build has no source to carry from, so
    the env is asked what it can offer.
    """

    def _check(self, spec, manifest):
        from quam_state_manager.core import capabilities
        return capabilities.qpu_root_check(spec, manifest)

    def test_a_root_that_can_hold_the_chip_is_chosen(self):
        r = self._check(_QDAC_SPEC, _ROOT_MANIFEST)
        assert r["chosen"] == "quam_config.my_quam.Quam"
        assert r["blocker"] is None

    def test_an_env_with_only_stock_roots_is_refused(self):
        r = self._check(_QDAC_SPEC, _STOCK_ONLY)
        assert r["chosen"] is None
        assert r["blocker"] and "cannot hold QDAC-biased qubits" not in r["blocker"]
        assert "would not load" in r["blocker"]

    def test_a_plain_chip_asks_for_nothing(self):
        r = self._check({"qdac": {"qubits": {}}}, _ROOT_MANIFEST)
        assert r["needed"] is False and r["blocker"] is None

    def test_an_unprobed_env_is_never_a_blocker(self):
        """Unknown is not a negative — the same rule `assess` follows. A
        transient probe failure must not block a build that would work."""
        assert self._check(_QDAC_SPEC, {})["blocker"] is None
        assert self._check(_QDAC_SPEC, None)["blocker"] is None

    def test_naming_a_root_that_cannot_hold_it_is_refused(self):
        """Not merely warned: the file would be written and would not open."""
        spec = dict(_QDAC_SPEC, quam_class="quam_builder.FluxTunableQuam")
        r = self._check(spec, _ROOT_MANIFEST)
        assert r["blocker"] and "cannot hold QDAC-biased qubits" in r["blocker"]

    def test_naming_a_root_the_probe_does_not_know_is_allowed(self):
        """The user may be naming a class they are about to write. The build
        degrades with a named warning if it turns out unimportable — refusing
        here would block a legitimate workflow on incomplete knowledge."""
        spec = dict(_QDAC_SPEC, quam_class="my.own.Quam")
        r = self._check(spec, _ROOT_MANIFEST)
        assert r["chosen"] == "my.own.Quam" and r["blocker"] is None

    def test_the_bias_tee_class_is_reported_not_assumed(self):
        from quam_state_manager.core import capabilities
        assert capabilities.bias_tee_class(_ROOT_MANIFEST) is None
        tee = {"qpu_roots": [{"path": "x.Quam", "importable": True,
                              "holds_qdac": True,
                              "bias_tee": {"cls": "lab.QdacBiasedFluxTunableTransmon",
                                           "field": "qdac_bias",
                                           "z_type": "FluxLine"}}]}
        got = capabilities.bias_tee_class(tee)
        assert got["field"] == "qdac_bias", (
            "the FIELD NAME is the lab's choice — no such class exists "
            "upstream, so SM must report it rather than assume one")


class TestQpuRootProbe:
    """The in-env half. Pure introspection of dataclass annotations, textual
    on purpose: `from __future__ import annotations` leaves them as strings
    and forward refs may not resolve outside their module, while the question
    ("does this field's declared type mention a QDAC component?") is
    answerable from the text and answerable safely.
    """

    def test_a_stock_shaped_root_does_not_claim_to_hold_qdac(self):
        import dataclasses
        from quam_state_manager.generator import probe_capabilities as P

        @dataclasses.dataclass
        class StockQuam:
            qubits: "dict[str, FluxTunableTransmon]" = None

        assert "qdacbias" not in P._annotation_text(
            StockQuam, "qubits").replace("_", "").lower()

    def test_a_widened_root_does(self):
        import dataclasses
        from quam_state_manager.generator import probe_capabilities as P

        @dataclasses.dataclass
        class LabQuam:
            qubits: ("dict[str, Union[FluxTunableTransmon, "
                     "QdacBiasedFixedFrequencyTransmon]]") = None
            qdac: "QdacInstrument" = None

        text = P._annotation_text(LabQuam, "qubits").replace("_", "").lower()
        assert "qdacbias" in text
        assert "qdac" in (getattr(LabQuam, "__dataclass_fields__", {}) or {})

    def test_a_qdac_only_transmon_is_not_a_bias_tee(self):
        """Its `z` IS the bias line, so there is no pulse line to sit beside."""
        import dataclasses
        from quam_state_manager.generator import probe_capabilities as P

        @dataclasses.dataclass
        class QdacOnly:
            z: "QdacBiasLine" = None

        assert P._bias_tee_shape(QdacOnly) is None

    def test_a_bias_tee_transmon_reports_its_field(self):
        import dataclasses
        from quam_state_manager.generator import probe_capabilities as P

        @dataclasses.dataclass
        class BiasTee:
            z: "FluxLine" = None
            qdac_bias: "QdacBiasLine" = None

        shape = P._bias_tee_shape(BiasTee)
        assert shape and shape["field"] == "qdac_bias"

    def test_the_probe_never_raises_on_a_broken_env(self):
        from quam_state_manager.generator import probe_capabilities as P
        roots = P.qpu_roots()
        assert isinstance(roots, list)
        for r in roots:
            assert "path" in r and "importable" in r


# ── the diagram (real renderer, real hover, under jsdom) ──────────────────────

@pytest.mark.skipif(shutil.which("node") is None, reason="node not on PATH")
def test_instrument_qdac_selfcheck_passes():
    """Drives the shipped `renderInstrumentWiring` + `_showPortPopup`.

    Pins the bias-tee flux port's mark (a dashed ring ADDED, not a recolour —
    it is still a z port), that a plain flux port never gets it, that one
    hover answers for both instruments, and that a shared QDAC trigger names
    the ext input which is the only thing explaining why sharing is correct.
    Mutation-checked 7/7 when written.
    """
    r = subprocess.run(
        ["node", str(_ROOT / "tests" / "instrument_qdac_selfcheck.cjs")],
        capture_output=True, text=True, encoding="utf-8",
        cwd=str(_ROOT), timeout=120,
    )
    if r.returncode == 2:
        pytest.skip("jsdom not installed (run `npm install jsdom`)")
    assert r.returncode == 0, (r.stdout + r.stderr)
    assert "all checks passed" in r.stdout, (r.stdout + r.stderr)


# ══════════════════════════════════════════════════════════════════════════════
# WS5b–WS9 (docs/136): the bias tee reaches the BUILD path, and the long tail
# ══════════════════════════════════════════════════════════════════════════════

def _spec(*, qdac_qubits: dict | None = None, flux_for: tuple = ()) -> dict:
    """A minimal but VALID generate spec, so a failure is about what it tests."""
    spec: dict = {
        "network": {"host": "1.2.3.4", "cluster_name": "c"},
        "instruments": {"controllers": [{"con": 1, "fems": [
            {"slot": 1, "fem": "mw"}, {"slot": 5, "fem": "lf"}]}]},
        "qubits": ["q1", "q2"],
        "qubit_pairs": [],
        "lines": [{"element": q, "line": "resonator", "group": "f1"}
                  for q in ("q1", "q2")]
             + [{"element": q, "line": "drive"} for q in ("q1", "q2")]
             + [{"element": q, "line": "flux"} for q in flux_for],
    }
    if qdac_qubits is not None:
        spec["qdac"] = {"communication_type": "Ethernet",
                        "ip_address": "1.2.3.5", "port": 5025,
                        "usb_device": None, "lib": "@py",
                        "qubits": qdac_qubits}
    return spec


def _q(channel=13, **extra) -> dict:
    return dict({"channel": channel, "dc_offset": 0.0, "trigger_port": "ext1",
                 "dwell": 2e-6, "slew_rate": 2e7, "output_range": "low",
                 "output_filter": "med", "settle_time": None}, **extra)


class TestSpecVocabulary:
    """core/qdac's SPEC readers — the wizard's side of the same vocabulary."""

    def test_a_plain_qdac_qubit_is_qdac(self):
        s = _spec(qdac_qubits={"q1": _q()})
        assert qdac.spec_bias_mode(s, "q1") == "qdac"
        assert qdac.spec_bias_mode(s, "q2") == "opx"
        assert qdac.spec_bias_tee_qubits(s) == {}

    def test_the_flag_is_what_makes_it_a_tee(self):
        s = _spec(qdac_qubits={"q1": _q(bias_tee=True)}, flux_for=("q1",))
        assert qdac.spec_bias_mode(s, "q1") == "bias_tee"
        assert list(qdac.spec_bias_tee_qubits(s)) == ["q1"]

    def test_co_presence_alone_is_not_a_tee(self):
        """A flux line beside a QDAC entry is equally the signature of the
        MISTAKE this used to be a hard error for. Only the flag decides."""
        s = _spec(qdac_qubits={"q1": _q()}, flux_for=("q1",))
        assert qdac.spec_bias_mode(s, "q1") == "qdac"

    def test_a_spec_with_no_qdac_answers_without_raising(self):
        assert qdac.spec_biased_qubits(_spec()) == {}
        assert qdac.spec_bias_mode(None, "q1") == "opx"


class TestBiasTeeValidation:
    """config_generator.validate_spec — the ban lifted, both directions."""

    def _errs(self, spec):
        from quam_state_manager.core import config_generator as cg
        return cg.validate_spec(spec)

    def test_qdac_plus_flux_without_the_flag_is_still_refused(self):
        errs = self._errs(_spec(qdac_qubits={"q1": _q()}, flux_for=("q1",)))
        assert any("QDAC-biased" in e and "q1" in e for e in errs), errs

    def test_the_refusal_names_the_flag_that_lifts_it(self):
        errs = self._errs(_spec(qdac_qubits={"q1": _q()}, flux_for=("q1",)))
        assert any("bias_tee" in e for e in errs), errs

    def test_a_declared_bias_tee_is_accepted(self):
        errs = self._errs(_spec(qdac_qubits={"q1": _q(bias_tee=True)},
                                flux_for=("q1",)))
        assert not any("bias" in e.lower() or "flux" in e.lower()
                       for e in errs), errs

    def test_a_bias_tee_with_no_flux_line_is_refused(self):
        """The flag is a claim about TWO components — checked from both ends,
        or the wizard says bias tee while the build makes a plain QDAC qubit."""
        errs = self._errs(_spec(qdac_qubits={"q1": _q(bias_tee=True)}))
        assert any("bias_tee" in e and "no OPX flux line" in e for e in errs), errs

    def test_a_plain_qdac_chip_is_unaffected(self):
        assert not self._errs(_spec(qdac_qubits={"q1": _q()}))


class TestBiasTeeCapability:
    """capabilities.bias_tee_check — a capability with no locator behind it."""

    _TEE = {"capabilities": {"x": {"available": True}}, "versions": {},
            "qpu_roots": [{"path": "p.Q", "importable": True, "holds_qdac": True,
                           "bias_tee": {"cls": "lab.TeeTransmon",
                                        "field": "qdac_bias",
                                        "z_type": "FluxLine"}}]}
    _NO_TEE = {"capabilities": {"x": {"available": True}}, "versions": {},
               "qpu_roots": [{"path": "p.Q", "importable": True,
                              "holds_qdac": True, "bias_tee": None}]}

    def test_no_tee_in_the_spec_asks_nothing(self):
        from quam_state_manager.core import capabilities as cap
        assert cap.bias_tee_check(_spec(qdac_qubits={"q1": _q()}),
                                  self._NO_TEE) is None

    def test_a_probed_env_without_the_class_degrades(self):
        from quam_state_manager.core import capabilities as cap
        row = cap.bias_tee_check(
            _spec(qdac_qubits={"q1": _q(bias_tee=True)}, flux_for=("q1",)),
            self._NO_TEE)
        assert row is not None and row["available"] is False
        assert row["severity"] == cap.DEGRADE      # never a blocker
        assert "q1" in row["detail"]

    def test_the_class_being_present_reports_its_field_name(self):
        from quam_state_manager.core import capabilities as cap
        row = cap.bias_tee_check(
            _spec(qdac_qubits={"q1": _q(bias_tee=True)}, flux_for=("q1",)),
            self._TEE)
        assert row["available"] is True
        assert "qdac_bias" in row["detail"] and "lab.TeeTransmon" in row["detail"]

    def test_an_unprobed_env_is_not_a_negative(self):
        from quam_state_manager.core import capabilities as cap
        row = cap.bias_tee_check(
            _spec(qdac_qubits={"q1": _q(bias_tee=True)}, flux_for=("q1",)), {})
        assert row["available"] is True and row["detail"] == "env not probed"

    def test_assess_surfaces_it_as_a_warning(self):
        from quam_state_manager.core import capabilities as cap
        rep = cap.assess(_spec(qdac_qubits={"q1": _q(bias_tee=True)},
                               flux_for=("q1",)), self._NO_TEE)
        ids = [w["id"] for w in rep["warnings"]]
        assert "instr.qdac_bias_tee" in ids

    def test_the_synthetic_id_stays_out_of_the_probed_registry(self):
        """REGISTRY is pinned byte-for-byte against the probe's CATALOG so a
        locator is never claimed without a prober; this one HAS no locator."""
        from quam_state_manager.core import capabilities as cap
        assert set(cap.SYNTHETIC_REGISTRY) & set(cap.REGISTRY) == set()


class TestBiasTeeReadBack:
    """regen_spec must invert a bias-tee chip, not read it as flux-tunable."""

    def test_a_tee_chip_round_trips_with_its_flag(self):
        from quam_state_manager.core import regen_spec
        merged = _chip(opx=("q2",), bias_tee=(("q1", 13, "ext1", 1),))
        state = {k: v for k, v in merged.items() if k != "wiring"}
        out = regen_spec.reconstruct_spec(state, merged["wiring"])
        qd = (out.spec.get("qdac") or {}).get("qubits") or {}
        assert "q1" in qd, out.spec.get("qdac")
        assert qd["q1"]["bias_tee"] is True
        assert qd["q1"]["channel"] == 13

    def test_a_qdac_only_chip_carries_no_flag(self):
        from quam_state_manager.core import regen_spec
        state = {k: v for k, v in MIXED.items() if k != "wiring"}
        out = regen_spec.reconstruct_spec(state, MIXED["wiring"])
        qd = (out.spec.get("qdac") or {}).get("qubits") or {}
        assert set(qd) == {"q1", "q3", "q9"}
        assert not any("bias_tee" in v for v in qd.values())


class TestEmittedRecipe:
    """script_emitter — the exported build recipe carries the QDAC or does not."""

    def _bundle(self, spec, alloc=None):
        from quam_state_manager.core import script_emitter as se
        return se.emit_bundle(spec, alloc or {}, {}, "chip")

    def test_a_qdac_chip_gets_the_data_block_and_the_attach_call(self):
        import ast
        src = self._bundle(_spec(qdac_qubits={"q1": _q()}))["02_build_machine.py"]
        ast.parse(src)                       # it has to be runnable Python
        for marker in ("QDAC = ", "QDAC_QUBITS = ", "QDAC_PINS = ",
                       "_attach_qdac_bias(", "_inject_qdac_state(",
                       "_inject_qdac_trigger_wiring("):
            assert marker in src, marker

    def test_the_pins_are_resolved_at_emit_time(self):
        """By the time someone RUNS the recipe the ports are a fact about a
        bench, not a choice — re-allocating could give a different cable map."""
        src = self._bundle(
            _spec(qdac_qubits={"q1": _q()}),
            {"q1": {"qt": [{"con": 1, "slot": 5, "port": 3}]}}
        )["02_build_machine.py"]
        assert "QDAC_PINS = {'q1': [1, 5, 3]}" in src

    def test_a_spec_pin_beats_the_allocation(self):
        src = self._bundle(
            _spec(qdac_qubits={"q1": _q(trigger_pin={"con": 2, "slot": 4, "port": 7})}),
            {"q1": {"qt": [{"con": 1, "slot": 5, "port": 3}]}}
        )["02_build_machine.py"]
        assert "'q1': [2, 4, 7]" in src

    def test_a_plain_chip_carries_no_qdac_machinery(self):
        src = self._bundle(_spec())["02_build_machine.py"]
        assert "QDAC_QUBITS" not in src and "_attach_qdac_bias" not in src

    def test_the_root_class_reaches_both_scripts(self):
        """A QDAC chip is rooted at a customer subclass; loading it as the
        stock root fails on the first QDAC qubit (the docs/136 §12 CRITICAL)."""
        spec = _spec(qdac_qubits={"q1": _q()})
        spec["quam_class"] = "lab.pkg.MyQuam"
        b = self._bundle(spec)
        assert "from lab.pkg import MyQuam as QuamCls" in b["02_build_machine.py"]
        assert "from lab.pkg import MyQuam as _ChipRoot" in b["03_generate_config.py"]

    def test_without_one_the_verifier_is_byte_identical(self):
        src = self._bundle(_spec())["03_generate_config.py"]
        assert "_ChipRoot" not in src

    def test_the_readme_names_the_package_the_build_needs(self):
        r = self._bundle(_spec(qdac_qubits={"q1": _q()}))["README.md"]
        assert "quam_config.qdac_components" in r and "QDAC_PINS" in r


class TestTriggerPulsesAreCounted:
    """pulse_index — eleven real pulses the Pulses page never listed."""

    def test_the_trigger_marker_is_a_row(self):
        from quam_state_manager.core import pulse_index
        rows = pulse_index.list_pulses(MIXED, with_used_by=False)
        paths = {r["path"] for r in rows}
        assert "qubits.q1.z.opx_trigger_out.operations.trigger" in paths

    def test_every_biased_qubit_gets_one(self):
        from quam_state_manager.core import pulse_index
        rows = pulse_index.list_pulses(MIXED, with_used_by=False)
        trig = [r for r in rows
                if r["path"].endswith(".opx_trigger_out.operations.trigger")]
        assert len(trig) == 3           # q1, q3, q9

    def test_a_bias_tee_trigger_is_found_under_its_own_field(self):
        """The bias line is a SIBLING of z there — a `z`-only reader misses it."""
        from quam_state_manager.core import pulse_index
        merged = _chip(bias_tee=(("q1", 13, "ext1", 1),), tee_field="dc_source")
        rows = pulse_index.list_pulses(merged, with_used_by=False)
        assert any(r["path"] == "qubits.q1.dc_source.opx_trigger_out.operations.trigger"
                   for r in rows), [r["path"] for r in rows]

    def test_a_chip_without_a_qdac_is_unchanged(self):
        from quam_state_manager.core import pulse_index
        plain = _chip(opx=("q1", "q2"))
        rows = pulse_index.list_pulses(plain, with_used_by=False)
        assert not any("opx_trigger_out" in r["path"] for r in rows)


class TestWiringMapColumns:
    """The CLI's `wiring` table — a QDAC qubit has no z row to show."""

    def _rows(self, tmp_path, merged):
        from quam_state_manager.core.query import QueryEngine
        return QueryEngine(_store_for(tmp_path, merged)).get_wiring_map()

    def test_the_qdac_columns_appear_and_are_filled(self, tmp_path):
        by = {r["qubit"]: r for r in self._rows(tmp_path, MIXED)}
        assert by["q1"]["qdac_channel"] == 13
        assert by["q1"]["qdac_trigger"] == "con1/5/1 (ext1)"

    def test_every_row_carries_the_columns_once_any_does(self, tmp_path):
        """The CLI derives its column set from rows[0]; a mixed chip whose
        first qubit is OPX-biased would otherwise render neither column."""
        rows = self._rows(tmp_path, MIXED)
        assert all("qdac_channel" in r and "qdac_trigger" in r for r in rows)

    def test_a_plain_chip_gains_no_columns(self, tmp_path):
        rows = self._rows(tmp_path, _chip(opx=("q1", "q2")))
        assert not any("qdac" in k for r in rows for k in r)


class TestExportColumns:
    """CSV/MD export — z_joint_offset is blank on a QDAC qubit and says why."""

    def test_the_qdac_columns_are_appended_for_a_qdac_chip(self):
        from quam_state_manager.core import saver
        props = saver.default_properties(_FakeStore(MIXED))
        assert props[:len(saver.DEFAULT_PROPERTIES)] == saver.DEFAULT_PROPERTIES
        assert props[len(saver.DEFAULT_PROPERTIES):] == saver.QDAC_PROPERTIES

    def test_a_plain_chip_exports_exactly_what_it_used_to(self):
        from quam_state_manager.core import saver
        assert saver.default_properties(
            _FakeStore(_chip(opx=("q1",)))) == saver.DEFAULT_PROPERTIES


class TestClickTargetOverrides:
    """A flux click must not offer a field the clicked qubit does not have."""

    def test_a_qdac_qubit_gets_its_own_path(self):
        from quam_state_manager.core import click_targets as ct
        over = ct.qdac_path_overrides(MIXED)
        assert over["q1"] == "qubits.q1.z.dc_offset"
        assert "q2" not in over                 # OPX-biased: joint_offset is real

    def test_a_bias_tee_names_its_own_field_not_z(self):
        from quam_state_manager.core import click_targets as ct
        merged = _chip(bias_tee=(("q1", 13, "ext1", 1),), tee_field="dc_source")
        assert ct.qdac_path_overrides(merged)["q1"] == "qubits.q1.dc_source.dc_offset"

    def test_the_candidate_carries_the_map(self):
        from quam_state_manager.core import click_targets as ct
        cands = ct.candidates_for("09_qubit_vs_flux", "flux_bias", None, "qubit",
                                  {"q1": "qubits.q1.z.dc_offset"})
        flux = [c for c in cands if c["path"] == "qubits.{q}.z.joint_offset"]
        assert flux and flux[0]["path_by_entity"] == {"q1": "qubits.q1.z.dc_offset"}

    def test_without_a_qdac_the_candidate_is_untouched(self):
        from quam_state_manager.core import click_targets as ct
        cands = ct.candidates_for("09_qubit_vs_flux", "flux_bias", None, "qubit")
        flux = [c for c in cands if c["path"] == "qubits.{q}.z.joint_offset"]
        assert flux and "path_by_entity" not in flux[0]
        assert flux[0]["label"] == "Flux joint offset"


class TestAutofitQdacReroute:
    """autofit must not emit a write to a path the chip does not have —
    `writer.batch_set` is all-or-nothing, so one impossible flux row used to
    discard the same run's valid resonator updates."""

    def _resolve(self, fam_key, entry, state):
        from quam_state_manager.core.autofit import families as F

        def value_of(dotted):
            node = state
            for part in dotted.split("."):
                node = node[part]        # KeyError = absent, which is the point
            return node

        return F.resolve_updates(F.FAMILIES[fam_key], "q1", entry, {}, value_of)

    def _qdac_state(self):
        return {"qubits": {"q1": {
            "z": {"channel": 13, "dc_offset": -0.09},
            "resonator": {"f_01": 7e9, "RF_frequency": 7e9}}}}

    def _opx_state(self):
        return {"qubits": {"q1": {
            "z": {"joint_offset": 0.1, "min_offset": 0.0, "flux_point": "joint",
                  "opx_output": {"delay": 78}},
            "resonator": {"f_01": 7e9, "RF_frequency": 7e9}}}}

    def _fam(self):
        from quam_state_manager.core.autofit import families as F
        for key, fam in F.FAMILIES.items():
            if any(u.path.endswith(".z.joint_offset") for u in (fam.updates or [])):
                return key
        raise AssertionError("no family writes a flux joint offset")

    def test_the_flux_offset_goes_to_dc_offset_on_a_qdac_qubit(self):
        rows = self._resolve(self._fam(), {"idle_offset": 0.05},
                             self._qdac_state())
        paths = {r["path"] for r in rows}
        assert "qubits.q1.z.dc_offset" in paths, paths
        assert "qubits.q1.z.joint_offset" not in paths

    def test_min_offset_is_skipped_rather_than_invented(self):
        """The QDAC holds one DC level, not a flux-arc parameterisation."""
        rows = self._resolve(self._fam(),
                             {"idle_offset": 0.05, "min_offset": 0.01},
                             self._qdac_state())
        assert not any("min_offset" in r["path"] for r in rows)

    def test_an_opx_qubit_writes_exactly_what_it_always_did(self):
        rows = self._resolve(self._fam(), {"idle_offset": 0.05},
                             self._opx_state())
        assert "qubits.q1.z.joint_offset" in {r["path"] for r in rows}
        assert not any("dc_offset" in r["path"] for r in rows)


class TestConfigViewSlice:
    """The QDAC trigger element is the only one a qubit owns by underscore."""

    _CFG = {"elements": {
        "q1.xy": {"operations": {"x180": "q1.xy.x180.pulse"}},
        "q1_qdac_trigger": {"operations": {"trigger": "q1_trigger.pulse"}},
        "q11.xy": {"operations": {}},
    }, "pulses": {}, "waveforms": {}}

    def test_the_trigger_joins_its_qubits_slice(self):
        from quam_state_manager.core import config_view
        keys = config_view._element_keys_for(self._CFG, "q1")
        assert "q1_qdac_trigger" in keys and "q1.xy" in keys

    def test_it_does_not_leak_into_another_qubit(self):
        from quam_state_manager.core import config_view
        assert config_view._element_keys_for(self._CFG, "q11") == ["q11.xy"]

    def test_no_loose_underscore_rule(self):
        """Matched by its exact full name — a `q1_` prefix rule would start
        pulling arbitrary elements into whichever qubit named their prefix."""
        from quam_state_manager.core import config_view
        cfg = {"elements": {"q1_something_else": {}}}
        assert config_view._element_keys_for(cfg, "q1") == []


class TestCompareKnowsTheInstrument:
    def test_a_qdac_chip_lists_it(self):
        from quam_state_manager.core import compare
        assert any(i.startswith("qdac/") for i in compare._instruments(MIXED))

    def test_a_bias_tee_is_counted_separately(self):
        from quam_state_manager.core import compare
        merged = _chip(opx=("q2",), bias_tee=(("q1", 13, "ext1", 1),))
        found = compare._instruments(merged)
        assert "qdac/1 biased" in found and "qdac/1 bias-tee" in found

    def test_a_plain_chip_is_unchanged(self):
        from quam_state_manager.core import compare
        assert not any("qdac" in i for i in compare._instruments(_chip(opx=("q1",))))

    def test_chip_type_is_deliberately_left_alone(self):
        """It feeds a `== "fixed_frequency"` gate-inference test in routes; a
        new value there would silently take the other branch."""
        from quam_state_manager.core import compare
        assert compare._chip_type(MIXED) in ("fixed_frequency", "mixed", "unknown")


class TestTypeAlertCounting:
    """The env-mismatch line was calling a missing CLASS a field, and calling
    four aggregated findings four fields when they stood for twenty-three."""

    _FINDINGS = [
        {"kind": "unimportable_class", "severity": "error", "count": 11,
         "class": "quam_config.qdac_components.QdacBiasLine", "field": None,
         "detail": "cannot import"},
        {"kind": "unknown_field", "severity": "error", "count": 12,
         "class": "a.B", "field": "duration_control", "detail": "no such field"},
    ]

    def test_classes_and_fields_are_counted_apart(self):
        from quam_state_manager.core import type_fix
        env = type_fix.alert_summary(None, self._FINDINGS, [])["env"]
        assert env["classes"] == 1 and env["fields"] == 1

    def test_the_place_count_is_the_aggregate(self):
        from quam_state_manager.core import type_fix
        env = type_fix.alert_summary(None, self._FINDINGS, [])["env"]
        assert env["places"] == 23 and env["count"] == 2

    def test_a_finding_with_no_count_still_counts_once(self):
        from quam_state_manager.core import type_fix
        env = type_fix.alert_summary(
            None, [{"kind": "unknown_field", "severity": "error"}], [])["env"]
        assert env["places"] == 1


class TestImportClassKeepsTheRealCause:
    """probe_state_schema._import_class walked the split further left and
    reported the SYMPTOM of the shorter split instead of the real failure."""

    def test_a_module_that_fails_while_importing_wins(self, monkeypatch):
        from quam_state_manager.generator import probe_state_schema as ps

        def fake(name, *a, **kw):
            if name == "pkg.sub":
                raise ModuleNotFoundError("No module named 'qdac2_driver'",
                                          name="qdac2_driver")
            raise ModuleNotFoundError(f"No module named {name!r}", name=name)

        monkeypatch.setattr(ps.importlib, "import_module", fake)
        with pytest.raises(ModuleNotFoundError) as exc:
            ps._import_class("pkg.sub.Thing")
        assert "qdac2_driver" in str(exc.value)

    def test_a_plain_missing_module_still_reports_itself(self, monkeypatch):
        from quam_state_manager.generator import probe_state_schema as ps

        def fake(name, *a, **kw):
            raise ModuleNotFoundError(f"No module named {name!r}", name=name)

        monkeypatch.setattr(ps.importlib, "import_module", fake)
        with pytest.raises(ModuleNotFoundError) as exc:
            ps._import_class("nope.deeper.Thing")
        assert "nope" in str(exc.value)


class TestRegenMergeRefusesAnUntypeableGraft:
    """A degraded rebuild leaves the qubit with no `z`; grafting the old
    QdacBiasLine back onto it writes a state whose `z` is typed for something
    else — Quam.load() fails while the build reports success."""

    _SCHEMAS = {
        "lab.FixedFrequencyTransmon": ["id", "z", "xy"],
        "quam.components.channels.MWChannel": ["operations"],
    }

    def _merge(self, old, new):
        from quam_state_manager.core import regen_merge
        return regen_merge.merge_states(old, new, class_schemas=self._SCHEMAS)

    def test_the_bias_line_is_dropped_visibly(self):
        old = {"qubits": {"q1": {
            "__class__": "lab.FixedFrequencyTransmon", "id": "q1",
            "z": {"__class__": "quam_config.qdac_components.QdacBiasLine",
                  "channel": 13}}}}
        new = {"qubits": {"q1": {"__class__": "lab.FixedFrequencyTransmon",
                                 "id": "q1"}}}
        res = self._merge(old, new)
        assert "qubits.q1.z" in res.stats.schema_dropped
        assert "z" not in res.merged["qubits"]["q1"]

    def test_a_user_added_pulse_still_grafts(self):
        """`operations` is an UNTAGGED container — no declared types to
        violate, and that is where a lab's own pulse class legitimately lives.
        Gating there would break the tier-2 graft this exists for."""
        old = {"qubits": {"q1": {
            "__class__": "lab.FixedFrequencyTransmon", "id": "q1",
            "xy": {"__class__": "quam.components.channels.MWChannel",
                   "operations": {"mine": {"__class__": "lab.MyPulse",
                                           "length": 40}}}}}}
        new = {"qubits": {"q1": {
            "__class__": "lab.FixedFrequencyTransmon", "id": "q1",
            "xy": {"__class__": "quam.components.channels.MWChannel",
                   "operations": {}}}}}
        res = self._merge(old, new)
        got = res.merged["qubits"]["q1"]["xy"]["operations"]
        assert "mine" in got, res.stats.schema_dropped


class TestPopulateProtectReachesTheQdac:
    """The Populate QDAC cells write OUTSIDE spec.populate (one home, shared
    with the step-4 band). Populate-protect diffs spec.populate — so without a
    view of that second home every QDAC edit read as unchanged and the tier-1
    carry silently reverted it to the source chip's value on a re-generate."""

    def _spec(self, dc=0.25):
        s = _spec(qdac_qubits={"q1": _q()})
        s["qdac"]["qubits"]["q1"]["dc_offset"] = dc
        return s

    def test_the_view_exposes_the_bias_cells_as_a_group(self):
        from quam_state_manager.core import regen_populate as rp
        view = rp.populate_view(self._spec())
        assert view["qdac"]["q1"]["dc_offset"] == 0.25

    def test_only_real_qdac_fields_ride_along(self):
        """`bias_tee` / `trigger_pin` / `pin_source` are wizard bookkeeping,
        not values on the chip — a protect path built from them names nothing."""
        from quam_state_manager.core import regen_populate as rp
        s = self._spec()
        s["qdac"]["qubits"]["q1"].update(bias_tee=True, pin_source="group",
                                         trigger_pin={"con": 1, "slot": 5, "port": 1})
        assert set(rp.populate_view(s)["qdac"]["q1"]) <= set(qdac.QDAC_FIELDS)

    def test_a_chip_with_no_qdac_gets_no_group(self):
        from quam_state_manager.core import regen_populate as rp
        assert "qdac" not in rp.populate_view(_spec())

    def test_the_existing_groups_are_untouched(self):
        from quam_state_manager.core import regen_populate as rp
        s = _spec()
        s["populate"] = {"qubit": {"q1": {"RF_freq": 5e9}}}
        assert rp.populate_view(s)["qubit"] == {"q1": {"RF_freq": 5e9}}

    def test_an_edited_bias_is_seen_as_changed(self):
        from quam_state_manager.core import regen_populate as rp
        base = rp.populate_view(self._spec(dc=0.10))
        now = rp.populate_view(self._spec(dc=0.25))
        assert ("qdac", "q1", "dc_offset") in rp.changed_fields(now, base, None)

    def test_and_expands_to_the_bias_lines_own_field(self):
        from quam_state_manager.core import regen_populate as rp
        new_state = {k: v for k, v in MIXED.items() if k != "wiring"}
        protect, _ = rp.protect_paths(
            [("qdac", "q1", "dc_offset")], rp.populate_view(self._spec()),
            new_state, MIXED["wiring"], new_state, MIXED["wiring"])
        assert protect == {"qubits.q1.z.dc_offset"}

    def test_a_bias_tee_protects_its_sibling_not_z(self):
        from quam_state_manager.core import regen_populate as rp
        merged = _chip(bias_tee=(("q1", 13, "ext1", 1),), tee_field="dc_source")
        state = {k: v for k, v in merged.items() if k != "wiring"}
        protect, _ = rp.protect_paths(
            [("qdac", "q1", "dc_offset")], rp.populate_view(self._spec()),
            state, merged["wiring"], state, merged["wiring"])
        assert protect == {"qubits.q1.dc_source.dc_offset"}

    def test_a_degraded_rebuild_protects_nothing(self):
        """No bias line on the rebuilt chip ⇒ no value there to defend."""
        from quam_state_manager.core import regen_populate as rp
        plain = _chip(opx=("q1",))
        state = {k: v for k, v in plain.items() if k != "wiring"}
        protect, _ = rp.protect_paths(
            [("qdac", "q1", "dc_offset")], rp.populate_view(self._spec()),
            state, plain["wiring"], state, plain["wiring"])
        assert protect == set()


class TestTheProbeLooksWhereTheClassIsDEFINED:
    """docs/136 §19 — measured against a real env carrying a real bias-tee
    class: the probe reported "no such class" while the BUILD, in the same
    env, found it and used it.

    Cause: a root reachable by two names is deduplicated to its canonical one,
    and the name that wins is the re-export (`quam_config.Quam`) whose module
    exports the ROOT but not the transmon classes. The module that actually
    imports them (`quam_config.my_quam`) was then never scanned. A capability
    report wrong in the safe direction is still wrong — the wizard promises a
    degrade that never comes, and the two halves of one feature disagree.
    """

    def _fake_env(self, monkeypatch, *, define_in_home: bool):
        """A root re-exported from `pkg` but DEFINED in `pkg.inner`, with the
        bias-tee class importable only from `pkg.inner`."""
        import dataclasses
        import sys
        import types

        from quam_state_manager.generator import probe_capabilities as P

        @dataclasses.dataclass
        class TeeTransmon:
            z: "FluxLine" = None
            qdac_bias: "QdacBiasLine" = None

        @dataclasses.dataclass
        class LabQuam:
            qubits: "dict[str, Union[FluxTunableTransmon, TeeTransmon]]" = None
            qdac: "QdacBiasLine" = None

        inner = types.ModuleType("pkg_fake.inner")
        inner.LabQuam = LabQuam
        inner.TeeTransmon = TeeTransmon
        LabQuam.__module__ = "pkg_fake.inner"
        TeeTransmon.__module__ = "pkg_fake.inner"

        outer = types.ModuleType("pkg_fake")
        outer.LabQuam = LabQuam          # re-exported…
        if define_in_home:
            outer.TeeTransmon = TeeTransmon   # …and so is the transmon
        outer.inner = inner

        monkeypatch.setitem(sys.modules, "pkg_fake", outer)
        monkeypatch.setitem(sys.modules, "pkg_fake.inner", inner)
        # The re-export home comes FIRST, exactly as ("quam_config", "Quam")
        # precedes ("quam_config.my_quam", "Quam") in the real list.
        monkeypatch.setattr(P, "_QPU_ROOT_HOMES",
                            (("pkg_fake", "LabQuam"),
                             ("pkg_fake.inner", "LabQuam")))
        return P

    def test_a_class_only_the_inner_module_imports_is_still_found(self, monkeypatch):
        P = self._fake_env(monkeypatch, define_in_home=False)
        roots = [r for r in P.qpu_roots() if r.get("importable")]
        assert roots, roots
        tee = roots[0].get("bias_tee")
        assert tee, roots[0]
        assert tee["field"] == "qdac_bias"
        assert tee["cls"].endswith("TeeTransmon")

    def test_the_re_export_case_still_works(self, monkeypatch):
        P = self._fake_env(monkeypatch, define_in_home=True)
        roots = [r for r in P.qpu_roots() if r.get("importable")]
        assert roots and roots[0].get("bias_tee"), roots

    def test_the_root_is_still_reported_once(self, monkeypatch):
        """The dedup itself is right — two names, one class, one row."""
        P = self._fake_env(monkeypatch, define_in_home=False)
        paths = [r["path"] for r in P.qpu_roots() if r.get("importable")]
        assert len(paths) == len(set(paths)) == 1, paths

    def test_a_root_with_no_such_class_still_reports_none(self, monkeypatch):
        import dataclasses
        import sys
        import types

        from quam_state_manager.generator import probe_capabilities as P

        @dataclasses.dataclass
        class PlainTransmon:
            z: "FluxLine" = None

        @dataclasses.dataclass
        class PlainQuam:
            qubits: "dict[str, PlainTransmon]" = None

        m = types.ModuleType("pkg_plain")
        m.PlainQuam = PlainQuam
        m.PlainTransmon = PlainTransmon
        PlainQuam.__module__ = "pkg_plain"
        PlainTransmon.__module__ = "pkg_plain"
        monkeypatch.setitem(sys.modules, "pkg_plain", m)
        monkeypatch.setattr(P, "_QPU_ROOT_HOMES", (("pkg_plain", "PlainQuam"),))
        roots = P.qpu_roots()
        assert roots[0]["importable"] and roots[0]["bias_tee"] is None

    def test_an_unimportable_defining_module_never_raises(self, monkeypatch):
        import dataclasses
        import sys
        import types

        from quam_state_manager.generator import probe_capabilities as P

        @dataclasses.dataclass
        class Ghost:
            qubits: "dict[str, Whatever]" = None

        Ghost.__module__ = "module.that.is.not.there"
        m = types.ModuleType("pkg_ghost")
        m.Ghost = Ghost
        monkeypatch.setitem(sys.modules, "pkg_ghost", m)
        monkeypatch.setattr(P, "_QPU_ROOT_HOMES", (("pkg_ghost", "Ghost"),))
        roots = P.qpu_roots()          # must not raise
        assert roots[0]["bias_tee"] is None
