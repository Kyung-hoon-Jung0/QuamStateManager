"""The printable chip report (docs/126 #21, customer request).

A printer icon beside "Chip Status" in the sidebar opens ``/chip-status/report``
— a STANDALONE page (no app chrome, forced dark theme — what users see in SM,
r3 feedback) with the component-map
drawing and read-only, unpaginated tables of all five component views. Its
toolbar offers Print and a self-contained .html download (the client serializes
the DOM after the map draws, inlining the stylesheet).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from quam_state_manager.web.app import create_app


def _chip(folder: Path) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "state.json").write_text(json.dumps({
        "qubits": {
            "q1": {"id": "q1", "f_01": 6.1e9,
                   "resonator": {"RF_frequency": 7.2e9,
                                 "operations": {"readout": {"amplitude": 0.1,
                                                            "length": 1000}}},
                   "z": {"joint_offset": 0.05, "flux_point": "joint"}},
            "q2": {"id": "q2", "f_01": 6.3e9},
        },
        "qubit_pairs": {"q1-q2": {"id": "q1-q2",
                                  "qubit_control": "#/qubits/q1",
                                  "qubit_target": "#/qubits/q2"}},
        "active_qubit_names": ["q1", "q2"],
    }), encoding="utf-8")
    (folder / "wiring.json").write_text(json.dumps(
        {"network": {"host": "1.2.3.4"}, "wiring": {"qubits": {}}}),
        encoding="utf-8")
    return folder


@pytest.fixture
def client(tmp_path):
    _chip(tmp_path / "quam_state")
    app = create_app(testing=True, instance_path=str(tmp_path / "_i"))
    c = app.test_client()
    c.post("/load", data={"folder": str(tmp_path / "quam_state")})
    return c


class TestSidebarAffordance:
    def test_printer_icon_sits_beside_chip_status(self, client):
        page = client.get("/").get_data(as_text=True)
        i = page.index(">Chip Status</a>")
        # in the SAME nav-sub-row, before the subnav toggle
        row_end = page.index("nav-sub-toggle", i)
        row = page[i:row_end]
        assert 'class="nav-print"' in row
        assert 'href="/chip-status/report"' in row
        assert 'target="_blank"' in row              # a report opens its own tab
        assert "ic-printer" in row                    # SVG, never an emoji

    def test_no_chip_is_a_friendly_page_not_an_error(self, tmp_path):
        app = create_app(testing=True, instance_path=str(tmp_path / "_i2"))
        r = app.test_client().get("/chip-status/report")
        assert r.status_code == 200
        assert "No chip is open" in r.get_data(as_text=True)


class TestReportContent:
    def test_standalone_dark_and_chromeless(self, client):
        b = client.get("/chip-status/report").get_data(as_text=True)
        # standalone: its own <html>, forced DARK (r3: the report shows what
        # users see in SM; print-color-adjust keeps the ink), no app chrome
        assert 'data-theme="dark"' in b
        assert "print-color-adjust: exact" in b
        assert "sidebar" not in b and "topbar" not in b
        assert 'id="pending-tray"' not in b

    def test_every_component_section_renders_unpaginated(self, client):
        """docs/188 split the one Qubits table into grouped sections; every
        component view still renders, for every entity, on one page."""
        b = client.get("/chip-status/report").get_data(as_text=True)
        for heading in ("Qubits &mdash; frequencies (2)",
                        "Qubits &mdash; coherence (2)",
                        "Qubits &mdash; single-qubit gates (2)",
                        "Readout (1)",                 # only q1 has a resonator
                        "Readout fidelity (1)",
                        "Flux lines (1)",              # only q1 has z
                        "Wiring &mdash; ports (2)",
                        "Qubit pairs (1)",
                        "Couplers (0)"):
            assert heading in b, heading
        assert "q1-q2" in b
        # honest empty state, not a bare heading
        assert "No pair on this chip has a tunable coupler." in b

    def test_map_mount_matches_the_component_pages(self, client):
        """The report draws through the SAME ComponentMap machinery — the
        mount shape must stay what component-map.js expects."""
        b = client.get("/chip-status/report").get_data(as_text=True)
        assert 'id="component-map"' in b and 'class="cmap"' in b
        assert "cmap-body" in b
        assert "component-map.js" in b and "topo-graph.js" in b
        assert "ComponentMap.mount" in b

    def test_toolbar_offers_print_and_selfcontained_download(self, client):
        b = client.get("/chip-status/report").get_data(as_text=True)
        assert "window.print()" in b
        assert 'id="rep-download"' in b
        # the serializer is exposed for the browser probe to pin its output
        assert "ChipReport" in b and "buildStandalone" in b
        # it must strip scripts and inline the stylesheet in the saved file
        assert "querySelectorAll('script')" in b
        assert "link[rel=\"stylesheet\"]" in b


# ---------------------------------------------------------------------------
# docs/188 - "the report should hold every value that can be extracted"
# ---------------------------------------------------------------------------
def _rich_chip(folder: Path) -> Path:
    """A chip carrying one of everything the report is meant to print.

    Deliberately awkward in four ways, each of which the plain fixture above
    cannot reach: a GEF confusion matrix (the derived three-state readout
    fidelities), a QDAC-biased qubit (a bias line that is NOT a flux line), a
    CZ macro whose ``flux_pulse_qubit`` is a POINTER (the only path on which
    the report's gate parameters differ from the topology's), and an
    InterleavedRB of 1.5345 - a real donor-chip value (docs/138) that must
    never print as a 153% gate fidelity.
    """
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "state.json").write_text(json.dumps({
        "qubits": {
            "q1": {
                "id": "q1", "f_01": 6.1e9, "f_12": 5.9e9,
                "anharmonicity": 2.0e8, "chi": -3.5e5,
                "T1": 3.0e-5, "T2ramsey": 2.0e-5, "T2echo": 4.1e-5,
                "gate_fidelity": {"averaged": 0.9991, "x180": 0.9989,
                                  "x90": 0.9994},
                "xy": {"RF_frequency": 6.1e9,
                       "opx_output": "#/wiring/qubits/q1/xy/opx_output",
                       "operations": {
                    "x180_DragCosine": {"amplitude": 0.31, "length": 40,
                                        "alpha": -0.05},
                    "x90_DragCosine": {"amplitude": 0.155},
                    "saturation": {"amplitude": 0.005}}},
                "resonator": {
                    "RF_frequency": 7.2e9,
                    "opx_output": "#/wiring/qubits/q1/rr/opx_output",
                    "confusion_matrix": [[0.96, 0.04], [0.11, 0.89]],
                    "gef_confusion_matrix": [[0.94, 0.04, 0.02],
                                             [0.09, 0.88, 0.03],
                                             [0.05, 0.07, 0.88]],
                    "operations": {"readout": {"amplitude": 0.1,
                                               "length": 1000,
                                               "threshold": 4.4588e-4}}},
                "z": {"joint_offset": 0.05, "independent_offset": 0.02,
                      "flux_point": "joint",
                      "opx_output": "#/wiring/qubits/q1/z/opx_output",
                      "operations": {"cz_flat": {"amplitude": 0.42,
                                                 "length": 100,
                                                 "flat_length": 52,
                                                 "smoothing_length": 20}}},
            },
            "q2": {
                "id": "q2", "f_01": 6.3e9,
                # a QDAC-II bias line: a channel + dc_offset and NO opx_output.
                # Structural detection (docs/136) - no env or class needed.
                "z": {"channel": 13, "dc_offset": 0.12, "trigger_port": "ext1",
                      "dwell": 1e-3, "slew_rate": 1e7, "output_range": "low",
                      "output_filter": "dc", "settle_time": 100},
            },
        },
        "qubit_pairs": {
            "q1-q2": {
                "id": "q1-q2",
                "qubit_control": "#/qubits/q1", "qubit_target": "#/qubits/q2",
                # QA F-24: a pair's detuning is a flux amplitude in V (the
                # rig chip's q1-2 value); it was pinned here as 1.5e8 Hz
                "detuning": -0.16586175268952874,
                "confusion": [[0.85, 0.05, 0.06, 0.04],
                              [0.07, 0.79, 0.08, 0.06],
                              [0.06, 0.09, 0.77, 0.08],
                              [0.05, 0.08, 0.10, 0.77]],
                "coupler": {"decouple_offset": -0.1, "interaction_offset": 0.3},
                "macros": {"cz_flat": {
                    # the pulse lives on the moving qubit's z line and the
                    # macro POINTS at it; `get_pair` dereferences that, the
                    # topology's `gate_details` does not.
                    "flux_pulse_qubit": "#/qubits/q1/z/operations/cz_flat",
                    "phase_shift_control": 0.134,
                    "phase_shift_target": 0.901,
                    "fidelity": {"StandardRB": 0.658,
                                 "StandardRB_alpha": 0.5445,
                                 "InterleavedRB": 1.5345,
                                 "StandardRB_load_id": 1477}}},
            },
        },
        "active_qubit_names": ["q1", "q2"],
        # the port objects the wiring points at -- the second hop
        "ports": {
            "mw_outputs": {"con1": {"3": {
                "1": {"controller_id": "con1", "fem_id": 3, "port_id": 1,
                      "full_scale_power_dbm": 0},
                "2": {"controller_id": "con1", "fem_id": 3, "port_id": 2,
                      "full_scale_power_dbm": 0}}}},
            "analog_outputs": {"con1": {"5": {
                "1": {"controller_id": "con1", "fem_id": 5,
                      "port_id": 1}}}},
        },
    }), encoding="utf-8")
    (folder / "wiring.json").write_text(json.dumps({
        "network": {"host": "1.2.3.4"},
        "wiring": {"qubits": {"q1": {
            "xy": {"opx_output": "#/ports/mw_outputs/con1/3/2"},
            "rr": {"opx_output": "#/ports/mw_outputs/con1/3/1"},
            "z": {"opx_output": "#/ports/analog_outputs/con1/5/1"}}}},
    }), encoding="utf-8")
    return folder


@pytest.fixture
def rich_client(tmp_path):
    _rich_chip(tmp_path / "quam_state")
    app = create_app(testing=True, instance_path=str(tmp_path / "_ir"))
    c = app.test_client()
    c.post("/load", data={"folder": str(tmp_path / "quam_state")})
    return c


class TestEverythingExtractable:
    """The customer's ask: every value SM can extract is in the printout."""

    def test_coherence_prints_t2_echo_and_the_ceiling_ratio(self, rich_client):
        b = rich_client.get("/chip-status/report").get_data(as_text=True)
        assert "T2 echo (&micro;s)" in b
        assert "41.00" in b                      # 4.1e-5 s -> 41.00 us
        # T2ramsey / 2*T1 - the T2 <= 2*T1 ceiling, a cross-check the chip
        # implies and stores nowhere
        assert "T2 Ramsey / 2&middot;T1" in b
        assert ">0.33<" in b or "0.33" in b      # 2.0e-5 / (2 * 3.0e-5)

    def test_a_column_is_never_dropped_because_this_chip_is_empty(self, client):
        """docs/94 / docs/148. The PLAIN chip records no T2 echo and no 1Q gate
        fidelity; the columns must still be there, because an absent column
        reads as "this chip has no such thing"."""
        b = client.get("/chip-status/report").get_data(as_text=True)
        for header in ("T2 echo (&micro;s)", "F x180", "F x90",
                       "x180 &alpha;<sub>DRAG</sub>", "IW angle (rad)",
                       "Threshold"):
            assert header in b, header

    def test_readout_fidelities_are_derived_and_gef_is_there(self, rich_client):
        b = rich_client.get("/chip-status/report").get_data(as_text=True)
        assert "Assignment (GEF)" in b
        assert "92.500%" in b                    # GE mean diag (0.96, 0.89)
        assert "90.000%" in b                    # GEF mean diag (.94, .88, .88)
        # nothing to apologise for on a chip that HAS the matrices
        assert "gef_confusion_matrix</code>;" not in b

    def test_an_absent_derivation_names_the_leaf_it_fills_from(self, client):
        """The plain chip stores no confusion matrix at all. The block must say
        which leaf it would have come from - the docs/148 rule."""
        b = client.get("/chip-status/report").get_data(as_text=True)
        assert "resonator.confusion_matrix</code>" in b
        assert "resonator.gef_confusion_matrix</code>" in b

    def test_rb_rows_say_what_they_measure(self, rich_client):
        """docs/138: StandardRB is per CLIFFORD, InterleavedRB is per GATE, and
        a decay alpha is a fit parameter, not a fidelity."""
        b = rich_client.get("/chip-status/report").get_data(as_text=True)
        assert "2Q randomized benchmarking" in b
        assert "2Q Clifford (SRB)" in b
        assert "2Q gate (IRB)" in b
        assert "RB fit (decay " in b           # ... and the alpha is a ROW
        assert "65.800%" in b                    # the SRB fidelity, as a percent
        assert "0.5445" in b                     # ... and alpha as a BARE number
        assert "54.450%" not in b
        assert "1477" in b                       # the run that produced them

    def test_an_unphysical_fidelity_is_shown_and_marked_never_as_a_percent(
            self, rich_client):
        b = rich_client.get("/chip-status/report").get_data(as_text=True)
        assert "1.5345" in b and "unphysical" in b
        assert "153.450%" not in b

    def test_gate_parameters_follow_a_pointer_to_the_pulse(self, rich_client):
        """The report reads the pulse through `get_pair`, which DEREFERENCES a
        macro's `flux_pulse_qubit` reference. The topology's `gate_details`
        reads the raw macro and would print the phase shifts and nothing
        else."""
        b = rich_client.get("/chip-status/report").get_data(as_text=True)
        assert "2Q gate parameters (1)" in b
        assert "0.4200" in b                     # amplitude, through the pointer
        assert ">52<" in b                       # flat_length
        assert "0.9010" in b                     # phase shift target

    def test_the_qdac_bias_component_prints(self, rich_client):
        b = rich_client.get("/chip-status/report").get_data(as_text=True)
        assert "QDAC-II bias (1)" in b
        assert "ext1" in b
        assert "Output filter" in b

    def test_a_chip_without_a_qdac_has_no_qdac_section(self, client):
        """A whole absent INSTRUMENT is not a value this chip failed to record
        - unlike a column, it is right to leave it out."""
        b = client.get("/chip-status/report").get_data(as_text=True)
        assert "QDAC-II bias" not in b

    def test_the_pair_row_names_what_measured_its_fidelity(self, rich_client):
        b = rich_client.get("/chip-status/report").get_data(as_text=True)
        assert "Best 2Q fidelity" in b and "Detuning (V)" in b
        assert "Detuning (MHz)" not in b
        assert "-0.1659" in b                    # volts, 4 decimals -- not "-0.00" MHz
        assert "Pair readout confusion (1)" in b

    def test_the_ports_each_channel_is_cabled_to_are_printed(self, rich_client):
        """A printed chip record that cannot answer "which port is q1's flux
        line" is not a record of the chip. The label is built by following the
        chip's own two-hop chain (state -> wiring -> ports), so it exists only
        on the topology node."""
        b = rich_client.get("/chip-status/report").get_data(as_text=True)
        assert "Wiring &mdash; ports (2)" in b
        assert "con1/fem3/p2" in b               # q1 drive
        assert "con1/fem3/p1" in b               # q1 readout
        assert "con1/fem5/p1" in b               # q1 flux
        # q2 is QDAC-biased: a channel NUMBER, not a flux port
        assert ">13<" in b

    def test_the_readout_power_that_leaves_the_instrument(self, rich_client):
        """docs/109 -- P = FSP + 20*log10|amp|, and it needs the resolved MW
        port, so it is real only once the cabling resolves."""
        b = rich_client.get("/chip-status/report").get_data(as_text=True)
        assert "P(RO) (dBm)" in b
        assert "-20.0 dBm" in b                  # 0 dBm FSP, amplitude 0.1

    def test_the_anharmonicity_caption_matches_the_stored_sign(self, rich_client):
        """QA F-25: the caption defined alpha as f12 - f01 (and spelt f01 as
        f10) over a column of POSITIVE anharmonicities. SM's convention
        (docs/162, chip_health's f_12 blurb) is a positive magnitude,
        f01 - f12; caption and the metric blurb now say the same."""
        import html as _html
        from quam_state_manager.core import chip_health
        b = _html.unescape(rich_client.get("/chip-status/report").get_data(as_text=True))
        i = b.index("Qubits — frequencies")
        cap = b[i:b.index("</p>", i)]
        assert "anharmonicity f₀₁−f₁₂" in cap, cap
        assert "f₁₂−f₀₁" not in cap, cap
        assert "f₁₀" not in cap, cap
        blurb = chip_health.METRIC_META["anharmonicity"]["blurb"]
        assert "negative" not in blurb and "f₀₁−f₁₂" in blurb, blurb

    def test_a_small_number_keeps_its_precision(self, rich_client):
        """The report must not invent a second number renderer: a %.4f pass
        rounded a 4.4588e-04 readout threshold to "0.0004"."""
        b = rich_client.get("/chip-status/report").get_data(as_text=True)
        assert "4.4588e-04" in b
        assert ">0.0004<" not in b


def _inferred_if_chip(folder: Path) -> Path:
    """The xy shape a modern quam_builder chip stores (the customer's 5Q chip):
    the IF is quam's ``#./inferred_intermediate_frequency`` alias, a Python
    property no JSON pointer resolves, and the LO is the MW port's upconverter.
    """
    def xy(qid, **kw):
        d = {"opx_output": f"#/wiring/qubits/{qid}/xy/opx_output",
             "LO_frequency": "#./upconverter_frequency"}
        d.update(kw)
        return d
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "state.json").write_text(json.dumps({
        "qubits": {
            # RF literal, IF inferred, LO = port scalar  ->  195.43 MHz
            "q1": {"id": "q1", "f_01": 4.9e9,
                   "xy": xy("q1", RF_frequency=4895431254.26,
                            intermediate_frequency="#./inferred_intermediate_frequency")},
            # the port states no LO at all  ->  '-' (never the pointer)
            "q2": {"id": "q2", "f_01": 4.9e9,
                   "xy": xy("q2", RF_frequency=4.9e9,
                            intermediate_frequency="#./inferred_intermediate_frequency")},
            # dual-LO port: upconverters[upconverter].frequency  ->  -50.00 MHz
            "q3": {"id": "q3", "f_01": 4.95e9,
                   "xy": xy("q3", RF_frequency=4.95e9, upconverter=2,
                            intermediate_frequency="#./inferred_intermediate_frequency")},
            # the other direction: IF literal, RF inferred  ->  4.7500 GHz
            "q4": {"id": "q4", "f_01": 4.75e9,
                   "xy": xy("q4", intermediate_frequency=5.0e7,
                            RF_frequency="#./inferred_RF_frequency")},
        },
        "qubit_pairs": {},
        "active_qubit_names": ["q1", "q2", "q3", "q4"],
        "ports": {"mw_outputs": {"con1": {"3": {
            "1": {"port_id": 1, "upconverter_frequency": 4.7e9},
            "2": {"port_id": 2},
            "3": {"port_id": 3, "upconverter_frequency": None,
                  "upconverters": {"1": {"frequency": 4.7e9},
                                   "2": {"frequency": 5.0e9}}}}}}},
    }), encoding="utf-8")
    (folder / "wiring.json").write_text(json.dumps({
        "network": {"host": "1.2.3.4"},
        "wiring": {"qubits": {
            "q1": {"xy": {"opx_output": "#/ports/mw_outputs/con1/3/1"}},
            "q2": {"xy": {"opx_output": "#/ports/mw_outputs/con1/3/2"}},
            "q3": {"xy": {"opx_output": "#/ports/mw_outputs/con1/3/3"}},
            "q4": {"xy": {"opx_output": "#/ports/mw_outputs/con1/3/1"}}}},
    }), encoding="utf-8")
    return folder


class TestXyFrequenciesAreNumbers:
    """QA F-13: the frequencies table printed ``#./inferred_intermediate_frequency``
    in a column headed MHz. quam's own arithmetic (``RF - LO``, LO from the MW
    port's upconverter) is what the report prints now, or '-'."""

    @pytest.fixture
    def body(self, tmp_path):
        _inferred_if_chip(tmp_path / "quam_state")
        app = create_app(testing=True, instance_path=str(tmp_path / "_if"))
        c = app.test_client()
        c.post("/load", data={"folder": str(tmp_path / "quam_state")})
        return c.get("/chip-status/report").get_data(as_text=True)

    @staticmethod
    def _freq_row(body, qid):
        i = body.index("Qubits &mdash; frequencies")
        j = body.index(f"<strong>{qid}</strong>", i)
        return body[j:body.index("</tr>", j)]

    @staticmethod
    def _cells(row):
        import re
        return [re.sub(r"<[^>]+>", "", c).strip()
                for c in re.findall(r"<td[^>]*>(.*?)</td>", row, flags=re.S)]

    def test_no_inferred_alias_reaches_the_frequency_table(self, body):
        i = body.index("<table", body.index("Qubits &mdash; frequencies"))
        table = body[i:body.index("</table>", i)]
        assert "#./inferred" not in table, table

    def test_the_if_is_rf_minus_the_port_lo(self, body):
        # 4 895 431 254.26 - 4.7e9 Hz
        assert self._cells(self._freq_row(body, "q1"))[-2] == "195.43"

    def test_an_unreadable_lo_prints_a_dash(self, body):
        assert self._cells(self._freq_row(body, "q2"))[-2] == "-"

    def test_the_dual_lo_port_uses_the_channel_upconverter(self, body):
        assert self._cells(self._freq_row(body, "q3"))[-2] == "-50.00"

    def test_an_inferred_rf_is_lo_plus_if(self, body):
        cells = self._cells(self._freq_row(body, "q4"))
        assert cells[-3] == "4.7500" and cells[-2] == "50.00", cells

    def test_the_engine_still_carries_the_editable_pointer(self, tmp_path):
        """The report reads a side map: the engine's qubit dict -- which the
        inspector and the grids show as the pointer you would edit -- keeps
        the alias, and the helper is what turns it into a number."""
        from quam_state_manager.core import cr_semantics
        from quam_state_manager.core.loader import QuamStore
        from quam_state_manager.core.query import QueryEngine

        folder = _inferred_if_chip(tmp_path / "quam_state")
        state = json.loads((folder / "state.json").read_text(encoding="utf-8"))
        wiring = json.loads((folder / "wiring.json").read_text(encoding="utf-8"))
        store = QuamStore.from_dicts(state, wiring)
        q1 = QueryEngine(store).get_qubit("q1")
        assert q1["xy_intermediate_frequency"] == "#./inferred_intermediate_frequency"
        rf, if_ = cr_semantics.channel_effective_rf_if(
            store, store.merged["qubits"]["q1"]["xy"], ("qubits", "q1", "xy"))
        assert rf == 4895431254.26
        assert abs(if_ - 195431254.26) < 1e-3



def _inferred_len_chip(folder: Path) -> Path:
    """A pair whose gate pulses store ``length`` as the quam alias -- one by
    reference (the customer's 5Q layout), one inline, one of a lab's own
    class SM has no formula for."""
    folder.mkdir(parents=True, exist_ok=True)
    ft = {"__class__": "quam.components.pulses._FlatTopGaussianPulse",
          "length": "#./inferred_total_length", "amplitude": 0.4,
          "flat_length": 52, "smoothing_length": 20,
          "post_zero_padding_length": 20}
    (folder / "state.json").write_text(json.dumps({
        "qubits": {"q1": {"id": "q1", "z": {"operations": {"cz_ft": ft}}},
                   "q2": {"id": "q2"}},
        "qubit_pairs": {"q1-q2": {
            "id": "q1-q2", "qubit_control": "#/qubits/q1",
            "qubit_target": "#/qubits/q2",
            "macros": {
                "cz_flattop": {"flux_pulse_qubit": "#/qubits/q1/z/operations/cz_ft",
                               "phase_shift_control": 0.1,
                               "phase_shift_target": 0.2},
                "cz_erf": {"flux_pulse_qubit": {
                    "__class__": "quam_builder.architecture.superconducting."
                                 "components.pulses.ErfSquarePulse",
                    "length": "#./inferred_length", "amplitude": 0.4,
                    "flat_length": 100, "risetime_samples": 16,
                    "post_zero_padding_length": 20},
                    "phase_shift_control": 0.0, "phase_shift_target": 0.0},
                "cz_lab": {"flux_pulse_qubit": {
                    "__class__": "lab_config.two_flux_gate.SNZTwoFluxPulse",
                    "length": "#./inferred_length", "amplitude": 0.3,
                    "flat_length": 78, "padding": 4},
                    "phase_shift_control": 0.0, "phase_shift_target": 0.0},
            }}},
        "active_qubit_names": ["q1", "q2"],
    }), encoding="utf-8")
    (folder / "wiring.json").write_text(json.dumps(
        {"network": {"host": "1.2.3.4"}, "wiring": {"qubits": {}}}),
        encoding="utf-8")
    return folder


class TestGateLengthsAreNumbers:
    """QA F-13 remainder: the 2Q gate table's Length column printed
    ``#./inferred_total_length`` / ``#./inferred_length`` for every gate whose
    pulse stores the quam alias. The report prints quam's number, or '-'."""

    @pytest.fixture
    def body(self, tmp_path):
        _inferred_len_chip(tmp_path / "quam_state")
        app = create_app(testing=True, instance_path=str(tmp_path / "_len"))
        c = app.test_client()
        c.post("/load", data={"folder": str(tmp_path / "quam_state")})
        return c.get("/chip-status/report").get_data(as_text=True)

    @staticmethod
    def _gate_cells(body, gate):
        import re
        i = body.index("<table", body.index("2Q gate parameters"))
        table = body[i:body.index("</table>", i)]
        j = table.index(f">{gate}</td>")
        row = table[table.rindex("<tr>", 0, j):table.index("</tr>", j)]
        return [re.sub(r"<[^>]+>", "", c).strip()
                for c in re.findall(r"<td[^>]*>(.*?)</td>", row, flags=re.S)]

    def test_no_pointer_reaches_the_gate_table(self, body):
        i = body.index("<table", body.index("2Q gate parameters"))
        assert "#./inferred" not in body[i:body.index("</table>", i)]

    def test_a_referenced_flattop_is_flat_plus_smoothing_plus_padding(self, body):
        assert self._gate_cells(body, "cz_flattop")[4] == "92"

    def test_an_inline_erf_pulse_is_ceil4_of_its_parts(self, body):
        assert self._gate_cells(body, "cz_erf")[4] == "136"

    def test_a_lab_class_sm_has_no_formula_for_prints_a_dash(self, body):
        assert self._gate_cells(body, "cz_lab")[4] == "-"
