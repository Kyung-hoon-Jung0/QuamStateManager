"""Param-history filter form UX (perf/param-history-filter).

The defect: ``hx-trigger="change"`` on ``#param-history-filters`` fired ONE
full dashboard re-render per checkbox flip — /param-history is the app's
slowest route (app.js SLOW_PREFIXES; every render redraws every sparkline),
and the Qubits row alone can hold ~21 checkboxes. Unchecking 18 qubits cost
18 full server round-trips, each pushing a history entry.

The fix, pinned here:
  - the submit is DEBOUNCED (``change delay:500ms``) with
    ``hx-sync="this:replace"`` so a burst of flips collapses into one request
    carrying the FINAL form state (htmx serializes at send time);
  - deliberately NOT the htmx ``changed`` modifier: htmx 2 keys it on
    ``event.target.value``, which for a checkbox is the static value
    attribute — a re-toggle of the same box would never fire again;
  - All/None togglers on the Properties and Qubits rows flip the whole row
    then dispatch exactly ONE change event (``paramHistoryFilterSetRow``),
    so the debounced submit fires once for the whole flip;
  - the chips' lit state is echoed instantly client-side (the inputs are
    ``display:none`` — without the echo a click shows nothing for 500ms).

Server route/contract unchanged — this was template/JS only.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from quam_state_manager.web.app import create_app

_ROOT = Path(__file__).resolve().parent.parent
_STATIC = _ROOT / "quam_state_manager" / "web" / "static"


def _make_state():
    return {
        "qubits": {
            "qA1": {"id": "qA1", "f_01": 6.25e9, "T1": 8834},
            "qA2": {"id": "qA2", "f_01": 6.31e9, "T1": 9120},
        },
        "active_qubit_names": ["qA1", "qA2"],
    }


def _make_wiring():
    return {
        "wiring": {"qubits": {"qA1": {}, "qA2": {}}},
        "network": {"host": "10.1.1.18"},
    }


@pytest.fixture
def filter_form(tmp_path):
    folder = tmp_path / "quam_state"
    folder.mkdir()
    (folder / "state.json").write_text(json.dumps(_make_state()), encoding="utf-8")
    (folder / "wiring.json").write_text(json.dumps(_make_wiring()), encoding="utf-8")
    app = create_app(testing=True, instance_path=str(tmp_path / "_i"))
    client = app.test_client()
    client.post("/load", data={"folder": str(folder)})
    body = client.get(
        "/param-history", headers={"HX-Request": "true"}
    ).get_data(as_text=True)
    m = re.search(r'<form id="param-history-filters".*?</form>', body, re.S)
    assert m, "filter form missing from /param-history render"
    return m.group(0)


class TestDebouncedTrigger:
    def test_change_trigger_is_debounced(self, filter_form):
        assert 'hx-trigger="change delay:500ms"' in filter_form

    def test_burst_collapses_via_sync_replace(self, filter_form):
        """A click during the (slow) in-flight render replaces it — the last
        settled state always wins, never an intermediate one."""
        assert 'hx-sync="this:replace"' in filter_form

    def test_not_the_changed_modifier(self, filter_form):
        """htmx 2's `changed` compares event.target.value per element; a
        checkbox's value attr never changes, so the SECOND toggle of the same
        box would never trigger again. The debounce must stay `delay` only."""
        m = re.search(r'hx-trigger="([^"]*)"', filter_form)
        assert m is not None
        assert "changed" not in m.group(1)

    def test_contract_otherwise_unchanged(self, filter_form):
        """Same route, same swap, and the URL still becomes canonical (with
        the debounce the pushes collapse to one per settled state, so
        hx-push-url stays). docs/158 moved the TARGET: a filter change swaps
        only #param-history-results (hx-select picks it out of the full
        render) so the form the user just set never re-renders under them."""
        tag = re.match(r"<form[^>]*>", filter_form).group(0)
        assert 'hx-get="/param-history"' in tag
        assert 'hx-target="#param-history-results"' in tag
        assert 'hx-select="#param-history-results"' in tag
        assert 'hx-target="#param-history-root"' not in tag
        assert 'hx-swap="outerHTML"' in tag
        assert 'hx-push-url="true"' in tag
        # Reset filters (inside the form) still re-renders the WHOLE root —
        # it resets the chips too, so the form must re-render there
        assert 'Reset filters' in filter_form
        assert 'hx-target="#param-history-root"' in filter_form[filter_form.index("Reset filters") - 300:]


class TestAllNoneTogglers:
    def test_exactly_two_rows_carry_the_pair(self, filter_form):
        assert filter_form.count("paramHistoryFilterSetRow(this, true)") == 2
        assert filter_form.count("paramHistoryFilterSetRow(this, false)") == 2
        assert filter_form.count('class="phf-allnone"') == 2

    def test_on_the_properties_and_qubits_rows(self, filter_form):
        for label in (">Properties</label>", ">Qubits</label>"):
            i = filter_form.index(label)
            seg = filter_form[i : i + 500]
            assert "paramHistoryFilterSetRow(this, true)" in seg, label
            assert "paramHistoryFilterSetRow(this, false)" in seg, label

    def test_not_on_the_date_or_source_rows(self, filter_form):
        """Date is a radio group and Source is four chips — neither wants a
        row-flip. Keep the togglers where the row is long enough to hurt."""
        date_row = filter_form[
            filter_form.index(">Date</label>") : filter_form.index(">Source</label>")
        ]
        source_row = filter_form[
            filter_form.index(">Source</label>") : filter_form.index(">Properties</label>")
        ]
        assert "phf-allnone" not in date_row
        assert "phf-allnone" not in source_row

    def test_buttons_never_submit_the_form(self, filter_form):
        for m in re.finditer(r"<button[^>]*paramHistoryFilterSetRow[^>]*>", filter_form):
            assert 'type="button"' in m.group(0)

    def test_buttons_use_house_button_style(self, filter_form):
        for m in re.finditer(r"<button[^>]*paramHistoryFilterSetRow[^>]*>", filter_form):
            assert "btn-xs" in m.group(0)


class TestClientWiring:
    """The JS half lives in app.js (the param-history section) — static
    assertions on the shipped file, same idiom as test_sidebar_tools.py."""

    @pytest.fixture(scope="class")
    def js(self):
        return (_STATIC / "app.js").read_text(encoding="utf-8")

    def test_row_setter_exists_and_fires_one_change(self, js):
        assert "function paramHistoryFilterSetRow(" in js
        body = js[js.index("function paramHistoryFilterSetRow(") :][:900]
        # one dispatched change event per row flip — not one per checkbox
        assert body.count("dispatchEvent(new Event('change', { bubbles: true }))") == 1

    def test_instant_active_echo_listener(self, js):
        """The chip inputs are display:none, so the lit state IS the label's
        .active class. With the debounce the server render arrives late — a
        delegated change listener must echo the class immediately."""
        assert "closest('#param-history-filters')" in js
        i = js.index("closest('#param-history-filters')")
        seg = js[max(0, i - 400) : i + 1200]
        assert "classList.toggle('active'" in seg
        # radios (the Date row) resync the whole group — the browser unchecks
        # the sibling without an event
        assert 'input[type="radio"][name="' in seg
