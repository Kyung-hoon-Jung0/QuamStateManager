"""Templates: anything a Jinja template orders for a human counts digit runs
as NUMBERS (customer rule 2026-09-09 — `q2` before `q10`, `.101` before
`.1009`).

The templates area has exactly one such ordering: `_compare_state.html`'s Fit
Results section, which loops the fit-result dict's keys. Those keys are chip
ids — on the customer's own 20-qubit chip they are `q1 … q20`, `q10-11`,
`coupler_q9_q10`. Jinja's `|sort` compared them character by character, so the
per-qubit blocks rendered q1, q10, q11, …, q2.

The pin drives the real route (`GET /compare/state`) and reads the order out
of the rendered HTML, not out of a helper.
"""

import json
import re
from pathlib import Path

import pytest

from quam_state_manager.web.app import create_app

# The ids the customer's 20Q chip actually reports fit results for (docs/126
# baseline chip): single-digit, double-digit, pairs, and a coupler whose name
# carries two numbers. Written in an order that is neither the string order
# nor the natural one, so neither can win by accident.
FIT_IDS = ["q2", "q10", "q1", "q9-10", "q1-2", "coupler_q9_q10", "q20"]

# What the customer says the page must read.
EXPECTED = ["coupler_q9_q10", "q1", "q1-2", "q2", "q9-10", "q10", "q20"]


def _state():
    return {
        "qubits": {
            "q1": {
                "id": "q1",
                "f_01": 6.25e9,
                "T1": 8834,
                "xy": {"RF_frequency": 6.25e9,
                       "operations": {"x180": {"amplitude": 0.115, "length": 40}}},
                "resonator": {"RF_frequency": 7.64e9,
                              "operations": {"readout": {"amplitude": 0.042}}},
            },
        },
        "qubit_pairs": {},
        "active_qubit_names": ["q1"],
    }


def _wiring():
    return {"wiring": {"qubits": {"q1": {"xy": {"opx_output": "MW-FEM/1/2"}}}}}


@pytest.fixture
def compare_client(tmp_path):
    """Two experiment folders whose data.json reports fit results per chip id."""
    folders = []
    for i, freq in enumerate([6.25e9, 6.30e9]):
        exp_dir = tmp_path / f"exp_{i}"
        folder = exp_dir / "quam_state"
        folder.mkdir(parents=True)
        state = _state()
        state["qubits"]["q1"]["f_01"] = freq
        (folder / "state.json").write_text(json.dumps(state), encoding="utf-8")
        (folder / "wiring.json").write_text(json.dumps(_wiring()), encoding="utf-8")

        node = {
            "metadata": {"name": f"03_resonator_spectroscopy_{i}", "status": "finished",
                         "run_start": "2026-02-19T16:30:00", "run_end": "2026-02-19T16:31:00",
                         "description": ""},
            "data": {"parameters": {"model": {"num_shots": 100}},
                     "outcomes": {qid: "successful" for qid in FIT_IDS}},
            "id": i + 10,
        }
        (exp_dir / "node.json").write_text(json.dumps(node), encoding="utf-8")

        data = {"fit_results": {qid: {"frequency": freq + 1e6, "success": True}
                                for qid in FIT_IDS}}
        (exp_dir / "data.json").write_text(json.dumps(data), encoding="utf-8")
        folders.append(str(folder))

    app = create_app(testing=True)
    client = app.test_client()
    client.post("/workspace/add", data={"folder": str(Path(folders[0]).parent)})
    return client, folders


def _headings(html: str) -> list[str]:
    """The fit-result qubit headings, in the order the page renders them."""
    return [m.strip() for m in
            re.findall(r'class="compare-qubit-heading">\s*([^\s<]+)', html)]


class TestCompareStateFitResultOrder:
    """`_compare_state.html` — Fit Results, one heading per chip id."""

    def test_fit_result_headings_are_in_natural_order(self, compare_client):
        client, folders = compare_client
        qs = "&".join(f"paths={f}" for f in folders)
        resp = client.get(f"/compare/state?idx=0&{qs}")
        assert resp.status_code == 200
        html = resp.data.decode()

        order = _headings(html)
        assert order == EXPECTED, (
            "Fit Results headings are not in natural order.\n"
            f"  got:      {order}\n  expected: {EXPECTED}"
        )

    def test_q10_renders_after_q2_not_between_q1_and_q2(self, compare_client):
        """The customer's screenshot complaint, stated as the one relation."""
        client, folders = compare_client
        qs = "&".join(f"paths={f}" for f in folders)
        html = client.get(f"/compare/state?idx=0&{qs}").data.decode()
        order = _headings(html)
        assert order.index("q2") < order.index("q10") < order.index("q20")

    def test_every_id_still_renders_exactly_once(self, compare_client):
        """Ordering only — the fix must not drop or duplicate a heading."""
        client, folders = compare_client
        qs = "&".join(f"paths={f}" for f in folders)
        html = client.get(f"/compare/state?idx=0&{qs}").data.decode()
        assert sorted(_headings(html)) == sorted(FIT_IDS)


class TestNatsortFilterIsTheOneHelper:
    """The Jinja `natsort` filter is `core.loader.natural_key`, not a second
    implementation — a template that sorts ids uses it by name."""

    def test_filter_is_registered_and_counts_digit_runs(self):
        app = create_app(testing=True)
        f = app.jinja_env.filters["natsort"]
        assert f(["a.b.1009", "a.b.101", "a.b.1011", "a.b.1010"]) == [
            "a.b.101", "a.b.1009", "a.b.1010", "a.b.1011"]
        assert f({"q10": 1, "q2": 2, "q1": 3}) == ["q1", "q2", "q10"]

    def test_template_uses_the_filter(self):
        tpl = (Path(__file__).resolve().parents[1] / "quam_state_manager" / "web"
               / "templates" / "_compare_state.html").read_text(encoding="utf-8")
        assert "exp_ctx.fit_results | natsort" in tpl
