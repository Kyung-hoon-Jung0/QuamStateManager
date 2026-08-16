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


class TestThroughTheRoutes:
    @pytest.fixture
    def client(self, tmp_path):
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
