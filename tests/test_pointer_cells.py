"""A reference is a value (docs/121).

Customer, on Live State Edit: *"control / target are not shown in the cell — it
just says 'not set', so I always have to go over to Json Tree View to check.
Is it because it's a string pointer? Is there a reason, or is it a bug? It
should be visible AND editable, like the tree."*

It was a bug, and one line caused it. ``_build_bulk_cell`` asked
``resolve_field_target`` for a ``resolved_value``, which is scalar-nulled for
containers and absent for a pointer that resolves to nothing, then read that
null as *the field is not set*. "Does this pointer reach a scalar" is simply
not the question "does this field have a value".

Measured on the customer's 20-qubit chip, three real populations were rendering
blank while the tree showed their value right there:

  * pointer -> entity dict  — ``qubit_control``/``qubit_target`` (60 cells) and
    every operation alias (``xy.operations.x180 = "#./x180_DragCosine"``, 120)
  * dangling pointer        — resolves to nothing on this chip
  * resolvable list         — ``mutual_flux_bias = [0, 0]``; it reached the
    list-cell swap but was ALSO flagged missing

The flag matters beyond looks: docs/88 made ``missing`` mean *genuinely absent*
because the grids turn it into ``create: true``.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from quam_state_manager.core import edit_policy
from quam_state_manager.core.loader import QuamStore
from quam_state_manager.web.app import create_app
from quam_state_manager.web.routes import _build_bulk_cell


def _state() -> dict:
    return {
        "qubits": {
            "q1": {
                "id": "q1", "f_01": 6.1e9,
                "xy": {
                    "operations": {
                        # alias -> the real pulse dict (a pointer to a CONTAINER)
                        "x180": "#./x180_DragCosine",
                        "x180_DragCosine": {"amplitude": 0.4, "length": 40,
                                            "digital_marker": None},
                        # pointer -> a scalar: value-mode, and must stay untouched
                        "x90_amp_ref": "#/qubits/q1/xy/operations/x180_DragCosine/amplitude",
                    },
                    # a `#./` self-ref quam computes at runtime — the shape of
                    # `#./upconverter_frequency` / `#./inferred_intermediate_frequency`
                    "LO_ref": "#./upconverter_frequency",
                    # unresolvable and NOT a self-ref: genuinely dangling
                    "broken_ref": "#/qubits/qZZ/f_01",
                },
            },
            "q2": {"id": "q2", "f_01": 6.3e9},
        },
        "qubit_pairs": {
            "q1-2": {
                "id": "q1-2",
                "qubit_control": "#/qubits/q1",
                "qubit_target": "#/qubits/q2",
                "moving_qubit": "target",
                "mutual_flux_bias": [0, 0],
            }
        },
        "active_qubit_names": ["q1", "q2"],
    }


@pytest.fixture
def merged():
    return QuamStore.from_dicts(_state(), {"network": {"host": "1.2.3.4"}}).merged


def _cell(merged, alias):
    return _build_bulk_cell(merged, alias, {}, {}, "owner")


class TestAReferenceIsAValue:
    def test_control_and_target_show_their_pointer(self, merged):
        """The report, exactly: these two rendered blank."""
        for alias, want in (("qubit_pairs.q1-2.qubit_control", "#/qubits/q1"),
                            ("qubit_pairs.q1-2.qubit_target", "#/qubits/q2")):
            c = _cell(merged, alias)
            assert c["display"] == want, c
            assert c["missing"] is False
            assert c["ptr_kind"] == "dict"

    def test_an_operation_alias_shows_its_pointer(self, merged):
        c = _cell(merged, "qubits.q1.xy.operations.x180")
        assert c["display"] == "#./x180_DragCosine"
        assert c["missing"] is False
        assert c["ptr_kind"] == "dict"

    def test_a_dangling_pointer_says_so_rather_than_going_blank(self, merged):
        """docs/114's rule, which the grid was not applying: a pointer that
        resolves to nothing still HAS a value — the pointer."""
        c = _cell(merged, "qubits.q1.xy.broken_ref")
        assert c["display"] == "#/qubits/qZZ/f_01"
        assert c["missing"] is False
        assert c["ptr_kind"] == "dangling"

    def test_a_self_ref_quam_computes_is_RUNTIME_not_dangling(self):
        """`#./upconverter_frequency` does not resolve statically because the
        component computes it — `qubit_columns` has always classified that
        shape as `runtime`, so the derived LO_frequency column read "computed
        at runtime" while the curated sibling read "dangling". One shape, two
        verdicts, one of them false."""
        m = QuamStore.from_dicts(_state(), {"network": {"host": "1.2.3.4"}}).merged
        c = _cell(m, "qubits.q1.xy.LO_ref")
        assert c["ptr_kind"] == "runtime"
        assert c["missing"] is False

    def test_a_pointer_to_a_NULL_scalar_stays_in_value_mode(self):
        """The customer-role audit's find, and the sharpest line in this file.

        `-x90.digital_marker = "#../x180_DragCosine/digital_marker"` resolves
        PERFECTLY — to a target holding null. The first cut called that
        "dangling" and told the user to type a pointer, while
        `resolve_edit_path` still ran value-mode: typing `ON` was accepted and
        written to the SHARED x180 pulse, a path the user never named. Screen
        and behaviour said opposite things. Dangling means the RESOLUTION
        failed, never that the value found is null."""
        m = QuamStore.from_dicts(_state(), {"network": {"host": "1.2.3.4"}}).merged
        c = _cell(m, "qubits.q1.xy.operations.marker_ref")
        assert c["ptr_kind"] is None, "a resolvable pointer is not dangling"
        assert c["missing"] is True, "its value is null — the pre-docs/121 cell"
        assert c["display"] == ""

    def test_a_list_leaf_is_not_missing(self, merged):
        """It already reached the list-cell swap; it was ALSO claiming to be
        absent, which is what would have made an edit try to CREATE it."""
        c = _cell(merged, "qubit_pairs.q1-2.mutual_flux_bias")
        assert c["is_list"] is True
        assert c["missing"] is False

    def test_a_genuinely_absent_field_is_still_missing(self, merged):
        """docs/88's invariant is the reason `missing` exists — it is what the
        grids turn into `create: true`. Narrowing it must not empty it."""
        c = _cell(merged, "qubits.q1.T1")
        assert c["missing"] is True
        assert c["ptr_kind"] is None

    def test_scalars_are_byte_identical(self, merged):
        """Nothing about an ordinary cell may move."""
        for alias in ("qubits.q1.f_01",
                      "qubits.q1.xy.operations.x180_DragCosine.amplitude",
                      "qubits.q1.xy.operations.x90_amp_ref"):   # ptr -> scalar
            c = _cell(merged, alias)
            assert c["missing"] is False
            assert c["ptr_kind"] is None, alias
            assert c["display"] not in ("", None), alias


class TestTheWriteLandsOnTheReference:
    """`resolve_edit_path` followed a leaf pointer to its target even when the
    target was a CONTAINER — so a write aimed at the `qubit_control` cell was
    aimed at the whole `qubits.q1` object. Only the type judge ("Expected dict,
    got str") stood between a typed qubit name and a chip whose q1 became a
    string."""

    @pytest.fixture
    def store(self):
        return QuamStore.from_dicts(_state(), {"network": {"host": "1.2.3.4"}})

    def test_a_container_pointer_is_not_followed(self, store):
        assert (edit_policy.resolve_edit_path(store, "qubit_pairs.q1-2.qubit_control")
                == "qubit_pairs.q1-2.qubit_control")

    def test_a_dangling_pointer_is_not_followed(self, store):
        assert (edit_policy.resolve_edit_path(store, "qubits.q1.xy.LO_ref")
                == "qubits.q1.xy.LO_ref")

    def test_a_scalar_pointer_IS_still_followed(self, store):
        """Value-mode is the long-standing promise for these and is untouched."""
        assert (edit_policy.resolve_edit_path(store, "qubits.q1.xy.operations.x90_amp_ref")
                == "qubits.q1.xy.operations.x180_DragCosine.amplitude")


class TestPlainTextNeverBreaksALinkSilently:
    """Showing an editable reference without this guard would be worse than
    showing nothing. Measured on the real chip before it existed: typing ``q3``
    stored the literal ``"q3"`` (a `Quam.load()` failure met days later) and a
    number typed over a dangling pointer stored the STRING ``"6100000000.0"``.
    Both returned 200."""

    @pytest.fixture
    def store(self):
        return QuamStore.from_dicts(_state(), {"network": {"host": "1.2.3.4"}})

    def test_plain_text_into_a_reference_is_refused(self, store):
        why = edit_policy.pointer_cell_refusal(
            store, "qubit_pairs.q1-2.qubit_control", "q3")
        assert why and "reference" in why
        assert "#/qubits/q1" in why          # names the current link
        assert "Pulses" in why               # names the deliberate way out

    def test_a_pointer_is_accepted(self, store):
        assert edit_policy.pointer_cell_refusal(
            store, "qubit_pairs.q1-2.qubit_control", "#/qubits/q2") is None

    def test_a_number_over_a_dangling_reference_is_refused(self, store):
        assert edit_policy.pointer_cell_refusal(
            store, "qubits.q1.xy.LO_ref", "6.1e9") is not None

    def test_value_mode_is_never_refused(self, store):
        """A pointer that reaches a real number keeps value-mode: typing a
        number there writes the number AT THE TARGET, which is correct and is
        not this guard's business."""
        assert edit_policy.pointer_cell_refusal(
            store, "qubits.q1.xy.operations.x90_amp_ref", "0.42") is None

    def test_an_ordinary_field_is_never_refused(self, store):
        assert edit_policy.pointer_cell_refusal(store, "qubits.q1.f_01", "6.2e9") is None


@pytest.fixture
def client(tmp_path):
    """Module-level since docs/120 — the later classes drive the same routes."""
    folder = tmp_path / "quam_state"
    folder.mkdir()
    (folder / "state.json").write_text(json.dumps(_state()), encoding="utf-8")
    (folder / "wiring.json").write_text(
        json.dumps({"network": {"host": "1.2.3.4"}, "wiring": {"qubits": {}}}),
        encoding="utf-8")
    app = create_app(testing=True, instance_path=str(tmp_path / "_i"))
    c = app.test_client()
    c.post("/load", data={"folder": str(folder)})
    c._app = app
    return c


class TestThroughTheRoutes:
    def _state_of(self, client):
        app = client._app
        ctx = (app.config.get("contexts") or {}).get(app.config.get("active_context"))
        return ctx["store"].state

    def test_the_grid_renders_the_reference(self, client):
        body = client.get("/bulk").get_data(as_text=True)
        assert "#/qubits/q1" in body
        # ...and does not claim it is unset
        i = body.find('data-dot-path="qubit_pairs.q1-2.qubit_control"')
        assert i != -1
        cell = body[body.rfind("<td", 0, i):body.find("</td>", i)]
        assert "data-missing" not in cell, cell
        assert 'placeholder="not set"' not in cell, cell

    def test_repointing_works_and_leaves_the_target_alone(self, client):
        r = client.post("/field/edit", data={
            "dot_path": "qubit_pairs.q1-2.qubit_control", "value": "#/qubits/q2"})
        assert r.status_code == 200, r.get_data(as_text=True)
        st = self._state_of(client)
        assert st["qubit_pairs"]["q1-2"]["qubit_control"] == "#/qubits/q2"
        assert isinstance(st["qubits"]["q1"], dict)      # never stringified

    def test_plain_text_is_refused_by_the_route(self, client):
        r = client.post("/field/edit", data={
            "dot_path": "qubit_pairs.q1-2.qubit_control", "value": "q2"})
        assert r.status_code == 400
        assert "reference" in (r.get_json() or {}).get("error", "")
        st = self._state_of(client)
        assert st["qubit_pairs"]["q1-2"]["qubit_control"] == "#/qubits/q1"

    def test_the_batch_path_refuses_it_too(self, client):
        """Four generic value-edit surfaces share the rule; a side door around
        it is how the older audits describe this exact class of hole."""
        r = client.post("/field/edit-batch", data={
            "updates": json.dumps([
                {"dot_path": "qubit_pairs.q1-2.qubit_control", "value": "q2"}])})
        st = self._state_of(client)
        assert st["qubit_pairs"]["q1-2"]["qubit_control"] == "#/qubits/q1"
        assert r.status_code in (200, 400)
        if r.status_code == 200:
            body = r.get_json() or {}
            assert not body.get("ok") or any(
                not row.get("ok") for row in (body.get("results") or []))


class TestTheInspectorNamesWhatThePointerReaches:
    """docs/120 item 1 — the report's second half.

    Fixing the GRID left the inspector still answering ``[19 items]`` for
    ``qubit_control``: the template's container branch ran first and threw away
    the fact — carried on the row's own ``row-pointer`` class — that this was a
    reference at all. Counting a target's keys is not naming it, and the user
    was still sent to the Json Tree View to learn which qubits a pair couples.
    """

    def test_pointer_to_an_entity_resolves_to_its_name(self, merged):
        from quam_state_manager.core.pointer_resolver import pointer_target_name
        got = pointer_target_name(
            merged, "#/qubits/q1", ("qubit_pairs", "q1-2", "qubit_control"))
        assert got == "q1"

    def test_a_two_hop_chain_is_followed_to_the_end(self):
        """A modern quam_builder chip stores the reference through wiring
        (docs/118) — one hop lands on the literal field name, not the qubit."""
        from quam_state_manager.core.pointer_resolver import pointer_target_name
        st = _state()
        st["wiring"] = {"qubit_pairs": {"q1-2": {"c": {
            "control_qubit": "#/qubits/q2"}}}}
        st["qubit_pairs"]["q1-2"]["qubit_control"] = \
            "#/wiring/qubit_pairs/q1-2/c/control_qubit"
        merged = QuamStore.from_dicts(st, {}).merged
        got = pointer_target_name(
            merged, st["qubit_pairs"]["q1-2"]["qubit_control"],
            ("qubit_pairs", "q1-2", "qubit_control"))
        assert got == "q2", "one-hop split would have answered 'control_qubit'"

    def test_a_positional_target_is_not_named(self, merged):
        """`#/…/con1/1/2` would render as "2", which is worse than the pointer:
        None means the caller keeps what it was already showing."""
        from quam_state_manager.core.pointer_resolver import pointer_target_name
        st = _state()
        st["ports"] = {"mw_outputs": {"con1": {"1": {"2": {"band": 1}}}}}
        merged2 = QuamStore.from_dicts(st, {}).merged
        assert pointer_target_name(
            merged2, "#/ports/mw_outputs/con1/1/2", ("qubits", "q1", "p")) is None

    def test_nothing_nameable_is_never_invented(self, merged):
        from quam_state_manager.core.pointer_resolver import pointer_target_name
        assert pointer_target_name(merged, "#/qubits/qZZ/f_01", ("a", "b")) is None
        assert pointer_target_name(merged, "not a pointer", ("a", "b")) is None
        assert pointer_target_name(merged, None, ("a", "b")) is None

    def test_the_pair_inspector_renders_the_name(self, client):
        body = client.get("/pair/q1-2").get_data(as_text=True)
        i = body.find("qubit_control</code>")
        assert i != -1
        row = body[i:body.find("</tr>", i)]
        assert "items]" not in row, row
        assert "q1" in row, row


class TestADanglingReferenceCanBeCleared:
    """docs/120 item 18 — refusing plain text on a dangling pointer is right;
    refusing ``null`` too LOCKED the field, leaving a broken reference the user
    could neither repair nor remove."""

    def test_null_clears_a_dangling_pointer(self, client):
        r = client.post("/field/edit", data={
            "dot_path": "qubits.q1.xy.broken_ref", "value": "null"})
        assert r.status_code == 200, r.get_data(as_text=True)

    def test_plain_text_is_still_refused_there(self, client):
        r = client.post("/field/edit", data={
            "dot_path": "qubits.q1.xy.broken_ref", "value": "hello"})
        assert r.status_code == 400

    def test_a_live_container_pointer_still_refuses_null(self, client):
        """Clearing a WORKING link destroys real structure — that belongs in
        the explicit pointer editor, not in a grid cell."""
        r = client.post("/field/edit", data={
            "dot_path": "qubit_pairs.q1-2.qubit_control", "value": "null"})
        assert r.status_code == 400, r.get_data(as_text=True)


class TestOneAliasedSiblingCannotDisableTheGuard:
    """docs/120 item 17 — a sibling holding a POINTER counted as `other`, and
    the unanimity gate is `other == 0`, so ONE aliased qubit silently switched
    this protection off for that leaf across the whole chip."""

    def _store(self, q3_value):
        st = _state()
        st["qubits"]["q1"]["T1"] = 1.2e-5
        st["qubits"]["q2"]["T1"] = 1.5e-5
        st["qubits"]["q3"] = {"id": "q3", "T1": q3_value}
        st["qubits"]["q4"] = {"id": "q4", "T1": None}
        return QuamStore.from_dicts(st, {})

    def test_a_pointer_sibling_no_longer_votes_other(self):
        store = self._store("#/qubits/q1/T1")
        why = edit_policy.sibling_type_refusal(store, "qubits.q4.T1", "abc")
        assert why is not None and "number" in why

    def test_a_genuinely_non_numeric_sibling_still_abstains(self):
        store = self._store("very long")
        assert edit_policy.sibling_type_refusal(store, "qubits.q4.T1", "abc") is None

    def test_a_dangling_alias_is_no_evidence_either_way(self):
        store = self._store("#/qubits/qZZ/T1")
        why = edit_policy.sibling_type_refusal(store, "qubits.q4.T1", "abc")
        assert why is not None, "a broken alias must not vote against the guard"

    def test_field_create_is_guarded_too(self, tmp_path):
        """The rule protected the four edit surfaces and not creation — which
        is exactly where a typo has no prior value to contradict it.

        Needs its OWN chip: the shared fixture carries no numeric T1 at all, so
        there the guard rightly abstains (no evidence ⇒ no opinion) and a pin
        written against it would have passed while proving nothing.
        """
        st = _state()
        st["qubits"]["q1"]["T1"] = 1.2e-5
        st["qubits"]["q2"]["T1"] = 1.5e-5
        st["qubits"]["q3"] = {"id": "q3"}          # the leaf is ABSENT here
        folder = tmp_path / "quam_state"
        folder.mkdir()
        (folder / "state.json").write_text(json.dumps(st), encoding="utf-8")
        (folder / "wiring.json").write_text(
            json.dumps({"network": {"host": "1.2.3.4"}}), encoding="utf-8")
        app = create_app(testing=True, instance_path=str(tmp_path / "_i"))
        c = app.test_client()
        c.post("/load", data={"folder": str(folder)})

        r = c.post("/field/create", data={"dot_path": "qubits.q3.T1",
                                          "value": "abc"})
        assert r.status_code == 400, r.get_data(as_text=True)
        assert (r.get_json() or {}).get("error_kind") == "sibling_type"
        # ...and a number still creates normally.
        assert c.post("/field/create", data={"dot_path": "qubits.q3.T1",
                                             "value": "1.1e-5"}).status_code == 200
