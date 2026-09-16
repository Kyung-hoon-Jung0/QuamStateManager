"""The Pulses stress round's third batch (docs/190 F34/F39/F44/F47).

Four findings from driving the page in real Chrome, each pinned here on the
same surface a user reaches:

- **F34** the one page devoted to pulses was the only entity surface with no
  physical amplitude. An MW amplitude is a scale factor, not a voltage, and
  the row said ``V``.
- **F39** the address carried the search, the channel, the owner pick and the
  page number — everything except the pulse actually open.
- **F44** a redo whose timeline a foreign edit had forked did nothing and said
  nothing.
- **F47** the env strip's "25 pulse classes discovered" sat directly above a
  list of 16, with nothing anywhere reconciling the two.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from quam_state_manager.core import pulse_catalog
from quam_state_manager.web.app import create_app

_WIRING = {
    "network": {"host": "1.1.1.1", "cluster_name": "C1"},
    "wiring": {"qubits": {"qA1": {
        "xy": {"opx_output": "#/ports/mw_outputs/con1/1/2"},
        "rr": {"opx_output": "#/ports/mw_outputs/con1/1/1"},
        "z": {"opx_output": "#/ports/analog_outputs/con1/5/1"},
    }}},
}

_QC = "quam.components.pulses."


def _state() -> dict:
    """One qubit wired three ways: an MW drive port carrying an FSP, an MW
    readout port, and an LF flux port. The three amplitudes below are the
    three answers docs/109 can give (dBm, dBm, volts)."""
    return {
        "qubits": {"qA1": {
            "id": "qA1", "f_01": 5.0e9,
            "xy": {
                "opx_output": "#/wiring/qubits/qA1/xy/opx_output",
                "operations": {
                    "x180": {"amplitude": 0.1, "length": 100,
                             "__class__": _QC + "SquarePulse"},
                },
            },
            "z": {
                "opx_output": "#/wiring/qubits/qA1/z/opx_output",
                "operations": {"const": {"amplitude": 0.012, "length": 200,
                                         "__class__": _QC + "SquarePulse"}},
            },
            "resonator": {
                "opx_output": "#/wiring/qubits/qA1/rr/opx_output",
                "operations": {"readout": {
                    "amplitude": 0.1, "length": 800,
                    "integration_weights": "#./default_integration_weights",
                    "__class__": _QC + "SquareReadoutPulse"}},
            },
        }},
        "qubit_pairs": {},
        "active_qubit_names": ["qA1"],
        "ports": {
            "mw_outputs": {"con1": {"1": {
                "1": {"band": 1, "full_scale_power_dbm": 0},
                "2": {"band": 2, "full_scale_power_dbm": 0}}}},
            "analog_outputs": {"con1": {"5": {"1": {
                "output_mode": "direct", "offset": None}}}},
        },
    }


@pytest.fixture
def chip(tmp_path: Path) -> Path:
    folder = tmp_path / "chip"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "state.json").write_text(json.dumps(_state()), encoding="utf-8")
    (folder / "wiring.json").write_text(json.dumps(_WIRING), encoding="utf-8")
    return folder


@pytest.fixture
def client(tmp_path, chip):
    app = create_app(testing=True, instance_path=str(tmp_path / "_inst"))
    c = app.test_client()
    c.post("/load", data={"folder": str(chip)})
    c.application.config["_test_app"] = app
    return c


def _detail(client, path: str) -> str:
    r = client.get(f"/pulse/detail?path={path}", headers={"HX-Request": "true"})
    assert r.status_code == 200, r.data[:300]
    return r.get_data(as_text=True)


# ---------------------------------------------------------------- F34

class TestPhysicalAmplitude:
    """docs/190 F34 — what actually leaves the instrument, on the Pulses page."""

    def test_mw_amplitude_reads_dbm(self, client):
        html = _detail(client, "qubits.qA1.xy.operations.x180")
        assert "-20.0 dBm" in html
        assert "phys-note" in html

    def test_readout_amplitude_reads_dbm_too(self, client):
        html = _detail(client, "qubits.qA1.resonator.operations.readout")
        assert "-20.0 dBm" in html

    def test_flux_amplitude_reads_volts_never_dbm(self, client):
        html = _detail(client, "qubits.qA1.z.operations.const")
        assert "12 mV" in html
        # a flux amplitude IS volts; calling it dBm would be a different lie
        assert "dBm" not in html

    def test_the_physical_line_replaces_the_static_unit_label(self, client):
        """The catalog's flat ``V`` is what made the MW row wrong. Where the
        chain resolves, the measured line stands in its place — two labels,
        one of them false, is worse than the one that was there."""
        html = _detail(client, "qubits.qA1.xy.operations.x180")
        # the amplitude row carries the phys note and no unit chip of its own
        row = html.split('data-param="amplitude"')[0].rsplit("<tr", 1)[-1]
        after = html.split('data-param="amplitude"')[1][:600]
        assert "phys-note" in after, after[:200]
        assert '<span class="unit-preview muted">V</span>' not in after
        assert row is not None

    def test_a_length_row_is_untouched(self, client):
        """Only amplitudes annotate. The ``ns`` chip on length must survive —
        this fix must not silently strip every unit on the page."""
        html = _detail(client, "qubits.qA1.xy.operations.x180")
        after = html.split('data-param="length"')[1][:600]
        assert '<span class="unit-preview muted">ns</span>' in after

    def test_no_port_chain_keeps_the_old_row(self, tmp_path):
        """A chip whose channel resolves to nothing renders exactly what it
        rendered before — blank, never an invented number (docs/109)."""
        st = _state()
        st["qubits"]["qA1"]["xy"]["opx_output"] = "#/ports/mw_outputs/con9/9/9"
        folder = tmp_path / "chip2"
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "state.json").write_text(json.dumps(st), encoding="utf-8")
        (folder / "wiring.json").write_text(json.dumps(_WIRING), encoding="utf-8")
        app = create_app(testing=True, instance_path=str(tmp_path / "_inst2"))
        c = app.test_client()
        c.post("/load", data={"folder": str(folder)})
        html = _detail(c, "qubits.qA1.xy.operations.x180")
        after = html.split('data-param="amplitude"')[1][:600]
        assert "phys-note" not in after
        assert '<span class="unit-preview muted">V</span>' in after


# ---------------------------------------------------------------- F39

class TestTheUrlCarriesTheOpenPulse:
    """docs/190 F39 — reload, Back, or a link to a colleague landed on the
    right table beside an empty inspector."""

    def test_a_known_pulse_is_reopened(self, client):
        r = client.get("/pulses?pulse=qubits.qA1.xy.operations.x180")
        html = r.get_data(as_text=True)
        # keyed on the LOADER, not on the path: every row in the table already
        # carries that same /pulse/detail url (it is how a click opens one)
        loader = html.split('id="pulse-open-loader"')
        assert len(loader) == 2, "no auto-open loader rendered"
        assert "qubits.qA1.xy.operations.x180" in loader[1][:300]
        assert 'hx-trigger="load"' in loader[1][:300]

    def test_an_unknown_pulse_says_so_and_opens_nothing(self, client):
        r = client.get("/pulses?pulse=qubits.qZZ.xy.operations.nope")
        html = r.get_data(as_text=True)
        assert "pulse-open-missing" in html
        assert "qubits.qZZ.xy.operations.nope" in html
        # and it must NOT try to fetch it — a 404 painted over the pane is
        # what resolving server-side exists to avoid
        assert 'id="pulse-open-loader"' not in html

    def test_no_pulse_parameter_changes_nothing(self, client):
        html = client.get("/pulses").get_data(as_text=True)
        assert "pulse-open-missing" not in html
        assert 'id="pulse-open-loader"' not in html

    def test_a_non_pulse_path_never_reaches_the_detail_route(self, client):
        """`pulse=` is resolved against the chip's own index, so a path that
        is not a pulse at all is named, not fetched."""
        html = client.get("/pulses?pulse=qubits.qA1.f_01").get_data(as_text=True)
        assert "pulse-open-missing" in html
        assert 'id="pulse-open-loader"' not in html

    def test_the_rows_partial_is_byte_identical_with_and_without_it(self, client):
        """The rows-only response replaces #pulses-rows-wrap on every
        keystroke — an auto-open loader there would re-fetch the inspector on
        each one and take the user's place in it. Pinned as byte identity
        rather than as the loader's absence: `_pulse_rows.html` has no loader
        to render, so only a change that ROUTED one into it could break this,
        and absence alone would stay green through such a change."""
        plain = client.get("/pulses?rows=1").get_data(as_text=True)
        with_arg = client.get(
            "/pulses?rows=1&pulse=qubits.qA1.xy.operations.x180"
        ).get_data(as_text=True)
        assert plain == with_arg
        assert 'id="pulse-open-loader"' not in with_arg


# ---------------------------------------------------------------- F44

class TestForkedRedoSaysWhy:
    """docs/190 F44 — every editor drops the redo timeline when you type after
    an undo, but there the new edit was your own and visible. Here it was
    another window."""

    @staticmethod
    def _edit_and_undo(client):
        client.post("/pulse/edit", data={
            "path": "qubits.qA1.xy.operations.x180",
            "dot_path": "qubits.qA1.xy.operations.x180.length",
            "mode": "value", "value": "123"})
        r = client.post("/undo")
        assert r.status_code == 200, r.data[:200]

    @staticmethod
    def _foreign_mutation(client):
        """Another window edits something else through the same context."""
        client.post("/pulse/edit", data={
            "path": "qubits.qA1.z.operations.const",
            "dot_path": "qubits.qA1.z.operations.const.length",
            "mode": "value", "value": "321"})

    def test_a_forked_redo_names_the_reason(self, client):
        self._edit_and_undo(client)
        self._foreign_mutation(client)
        r = client.post("/redo")
        assert r.status_code == 200
        trig = json.loads(r.headers.get("HX-Trigger") or "{}")
        msg = (trig.get("cellsReverted") or {}).get("message") or ""
        assert "no longer applies" in msg, msg
        assert (trig["cellsReverted"]).get("stopped") == "forked"

    def test_an_ordinary_empty_redo_stays_quiet(self, client):
        """Nothing to redo because nothing was undone is not a fork, and must
        not claim the chip moved."""
        r = client.post("/redo")
        assert r.status_code == 200
        trig = json.loads(r.headers.get("HX-Trigger") or "{}")
        msg = (trig.get("cellsReverted") or {}).get("message") or ""
        assert "no longer applies" not in msg

    def test_an_unforked_redo_still_redoes(self, client):
        """The message must not cost the ordinary path its work."""
        self._edit_and_undo(client)
        r = client.post("/redo")
        trig = json.loads(r.headers.get("HX-Trigger") or "{}")
        payload = trig.get("cellsReverted") or {}
        assert "Redone" in (payload.get("message") or ""), payload
        assert payload.get("stopped") != "forked"


def _pairs_state() -> dict:
    """A pair carrying one macro, so "that name is taken" has something to be
    taken BY (the F34/F39 chip above has no pairs at all)."""
    return {
        "qubits": {
            "q1": {"id": "q1", "f_01": 4.8e9, "z": {"operations": {}},
                   "xy": {"operations": {}}},
            "q2": {"id": "q2", "f_01": 5.1e9, "z": {"operations": {}},
                   "xy": {"operations": {}}},
        },
        "qubit_pairs": {
            "q1-q2": {
                "qubit_control": "#/qubits/q1",
                "qubit_target": "#/qubits/q2",
                "coupler": {"decouple_offset": 0.1},
                "macros": {"cz_unipolar": {
                    "flux_pulse_qubit": {"amplitude": 0.05, "length": 100},
                    "coupler_flux_pulse": None}},
            },
        },
    }


@pytest.fixture
def pairs_client(tmp_path):
    folder = tmp_path / "pchip"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "state.json").write_text(json.dumps(_pairs_state()), encoding="utf-8")
    (folder / "wiring.json").write_text(json.dumps(_WIRING), encoding="utf-8")
    app = create_app(testing=True, instance_path=str(tmp_path / "_pinst"))
    c = app.test_client()
    assert c.post("/load", data={"folder": str(folder)}).status_code in (200, 302)
    return c


# ---------------------------------------------------------------- F48

class TestTheGateNameBoxIsWired:
    """docs/190 F48 — the jsdom pin next door proves the check WORKS; this
    proves the form actually calls it (the shipped handler is an attribute in
    the markup, which no jsdom harness of ours executes)."""

    def test_the_name_box_validates_as_you_type(self, client):
        html = client.get("/pulse/new", headers={"HX-Request": "true"}
                          ).get_data(as_text=True)
        box = html.split('id="pulse-create-newgate-name"')[1][:400]
        assert 'oninput="PulsesPage.createValidateGateName()"' in box, box

    def test_the_server_refusal_is_still_the_backstop(self, pairs_client):
        """A name that got past the box (an old page, a script, a paste that
        fired no input event) is still refused BY NAME — the box is a
        courtesy, never the gate. Nothing about this pin was true before
        docs/190 F48 either; it was simply never written down."""
        r = pairs_client.post("/api/pulse/create", data={
            "target_kind": "pair", "pair": "q1-q2", "gate": "__new__:cz_unipolar",
            "new_gate_name": "cz_unipolar", "slot": "flux_pulse_qubit",
            "pulse_type": "SquarePulse"})
        assert r.status_code == 409, r.status_code
        assert "already exists on q1-q2" in r.get_data(as_text=True)

    def test_a_free_name_on_the_same_pair_is_created(self, pairs_client):
        """The refusal must be about the NAME, not about creating gates."""
        r = pairs_client.post("/api/pulse/create", data={
            "target_kind": "pair", "pair": "q1-q2", "gate": "__new__:cz_unipolar",
            "new_gate_name": "cz_brand_new", "slot": "flux_pulse_qubit",
            "pulse_type": "SquarePulse"})
        assert r.status_code == 200, r.get_data(as_text=True)[:300]


# ---------------------------------------------------------------- F47

class TestRosterArithmetic:
    """docs/190 F47 — the strip's count and the form's list are derived from
    ONE classifier, so they cannot drift apart."""

    @staticmethod
    def _roster():
        """The shape the KRISS_CZ env really returns: catalog types, aliases
        for catalog types, base classes and deprecated spellings."""
        fields = {"length": {"type": {"base": "int"}, "default": 100},
                  "amplitude": {"type": {"base": "float"}}}
        return {
            "SquarePulse": {"canonical": _QC + "SquarePulse", "fields": fields},
            "GaussianPulse": {"canonical": _QC + "GaussianPulse", "fields": fields},
            "DragPulse": {"canonical": _QC + "DragPulse", "fields": fields},
            "ReadoutPulse": {"canonical": _QC + "ReadoutPulse", "fields": fields},
            "_FlatTopGaussianPulse": {"canonical": _QC + "_FlatTopGaussianPulse",
                                      "fields": fields, "deprecated": True},
            "LabOwnPulse": {"canonical": "lab.pulses.LabOwnPulse", "fields": fields},
            "NoSchemaPulse": {"canonical": "lab.pulses.NoSchemaPulse", "fields": None},
        }

    def test_the_buckets_add_up(self):
        b = pulse_catalog.env_roster_breakdown(self._roster())
        assert b["total"] == 7
        assert b["listed"] + b["other"] == b["total"]

    def test_every_bucket_lands_where_it_belongs(self):
        b = pulse_catalog.env_roster_breakdown(self._roster())
        assert b["offered"] == 2          # SquarePulse, GaussianPulse
        assert b["creatable"] == 1        # LabOwnPulse — the env's own
        assert b["alias"] == 1            # DragPulse
        assert b["base"] == 1             # ReadoutPulse
        assert b["deprecated"] == 1       # _FlatTopGaussianPulse
        assert b["no_schema"] == 1        # NoSchemaPulse

    def test_the_verdict_is_what_the_form_builds_from(self):
        """The load-bearing property: a leaf counted as ``creatable`` is
        exactly a leaf that becomes its own option, for EVERY leaf."""
        roster = self._roster()
        specs = pulse_catalog.env_creatable_specs(roster)
        for leaf, rec in roster.items():
            verdict = pulse_catalog.env_leaf_verdict(leaf, rec)
            assert (verdict == "creatable") == (leaf in specs), (leaf, verdict)

    def test_listed_equals_what_the_form_offers(self):
        """``listed`` is the number of these classes a user can pick, so it
        must equal the catalog's own creatable set restricted to the roster,
        plus the synthesized ones."""
        roster = self._roster()
        b = pulse_catalog.env_roster_breakdown(roster)
        offered = sum(1 for leaf in roster
                      if (pulse_catalog.PULSE_CATALOG.get(leaf) is not None
                          and pulse_catalog.PULSE_CATALOG[leaf].creatable))
        assert b["listed"] == offered + len(pulse_catalog.env_creatable_specs(roster))

    def test_no_roster_is_all_zeroes(self):
        """The empty-overlay byte-identity fence (docs/71 §2) reaches the
        strip too — with no env selected it renders no arithmetic at all."""
        b = pulse_catalog.env_roster_breakdown({})
        assert b["total"] == 0 and b["other"] == 0 and b["listed"] == 0

    @staticmethod
    def _render_strip(app, roster):
        """The strip's WARM branch, rendered directly. The route's own fixture
        cannot reach it (nothing has selected an interpreter, so the template
        takes the "static catalog" branch and the sentence is unreachable —
        the first version of this pin was vacuous for exactly that reason)."""
        card = {"selected": "C:/envs/lab/python.exe", "selected_exists": True,
                "probing": False, "warm": True,
                "versions": {"quam": "0.6.0", "quam_builder": "0.4.0"}}
        with app.app_context():
            return app.jinja_env.get_template("_pulse_env_strip.html").render(
                env_card=card, env_class_count=len(roster),
                env_roster=pulse_catalog.env_roster_breakdown(roster))

    def test_the_note_names_where_the_rest_went(self):
        note = pulse_catalog.env_roster_note(
            pulse_catalog.env_roster_breakdown(self._roster()))
        assert note == ("1 other name for one of them, 1 base class nothing is "
                        "built from, 1 older spelling SM reads but will not "
                        "write and 1 with no readable schema"), note

    def test_the_note_counts_in_the_plural_when_it_should(self):
        note = pulse_catalog.env_roster_note(
            {"other": 5, "alias": 2, "base": 3, "deprecated": 0, "no_schema": 0})
        assert note == ("2 other names for one of them and 3 base classes "
                        "nothing is built from"), note

    def test_the_note_is_empty_when_everything_is_listed(self):
        """No sentence at all, rather than a limp "7 of them are on the list
        below" with nothing after it."""
        assert pulse_catalog.env_roster_note({"other": 0, "alias": 3}) == ""
        assert pulse_catalog.env_roster_note({}) == ""

    def test_the_strip_states_the_reconciliation(self, client):
        html = self._render_strip(client.application, self._roster())
        assert "<strong>7</strong> pulse class" in html
        assert "<strong>3</strong>" in html and "the other 4:" in html
        assert "1 other name for one of them" in html
        assert "pulse-env-recon" in html

    def test_the_strip_says_nothing_when_there_is_nothing_to_reconcile(self, client):
        roster = {k: v for k, v in self._roster().items()
                  if k in ("SquarePulse", "GaussianPulse", "LabOwnPulse")}
        html = self._render_strip(client.application, roster)
        assert "<strong>3</strong> pulse class" in html
        assert "pulse-env-recon" not in html
        assert "of them" not in html

    def test_no_env_renders_no_arithmetic(self, client):
        html = client.get("/pulse/new", headers={"HX-Request": "true"}
                          ).get_data(as_text=True)
        assert "pulse-env-strip" in html
        # with no env probed there is no roster and nothing to reconcile
        assert "of them are on the list below" not in html
