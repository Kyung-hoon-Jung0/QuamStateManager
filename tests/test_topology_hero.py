"""Chip Status hero map (docs/92 P1) — server-render pin + the jsdom selfcheck.

The hero map is built client-side (chip-status.js buildHeroMap) from the same
topology payload as the card diagram; the server's job is only to mount it.
Pins: the #topo-hero mount exists on /topology and LEADS the section (sits
before the card wrap), and the behavioral selfcheck (honesty modes, edge-colour
parity with the cards, value text on nodes, coincident-cell fan-out) passes
against the real shipped JS.
"""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

from quam_state_manager.web.app import create_app

_ROOT = Path(__file__).resolve().parent.parent
_SELFCHECK = _ROOT / "tests" / "chip_status_hero_selfcheck.cjs"


def _client(tmp_path, moving="__unset__"):
    # `moving` writes qubit_pairs[..].moving_qubit (docs/120 item 11). It is a
    # parameter rather than something a test edits afterwards because this
    # helper REWRITES state.json on every call, so a post-hoc edit would be
    # silently clobbered by the next _client().
    state = {
        "qubits": {
            "qA1": {"id": "qA1", "grid_location": "0,0", "T1": 2.4e-5},
            "qA2": {"id": "qA2", "grid_location": "1,0", "T1": 1.8e-5},
        },
        "qubit_pairs": {
            "qA2-qA1": {
                "id": "qA2-qA1",
                "qubit_control": "#/qubits/qA2",
                "qubit_target": "#/qubits/qA1",
                "macros": {"cz": {"fidelity": {"Bell_State": {"Fidelity": 0.96}}}},
            },
        },
    }
    if moving != "__unset__":
        for _p in state["qubit_pairs"].values():
            _p["moving_qubit"] = moving
    (tmp_path / "state.json").write_text(json.dumps(state), encoding="utf-8")
    (tmp_path / "wiring.json").write_text(
        json.dumps({"wiring": {"qubits": {}}, "network": {"host": "10.0.0.1"}}),
        encoding="utf-8")
    app = create_app(testing=True, instance_path=str(tmp_path / "_inst"))
    client = app.test_client()
    client.post("/load", data={"folder": str(tmp_path)})
    return client


def test_topology_page_mounts_exactly_one_chip_map(tmp_path):
    """docs/120 item 11 — ONE map, and it is the hero.

    This used to assert the hero mounted BEFORE the card diagram, because both
    rendered. That stacking is precisely what the customer reported ("the qubit
    layout appears twice ... why does the first one exist?"), so the card host
    is gone and the assertion is the stronger one: it is not there at all.
    """
    body = _client(tmp_path).get("/topology").get_data(as_text=True)
    assert 'id="topo-hero"' in body
    assert 'id="topo-html-wrap"' not in body
    assert "topo-node-card" not in body


def test_the_map_explains_its_own_symbols(tmp_path):
    """C/T/M are new vocabulary; a legend the user has to guess at is not one."""
    body = _client(tmp_path).get("/topology").get_data(as_text=True)
    assert "control" in body and "target" in body
    assert "moving" in body or "moves" in body


@pytest.mark.skipif(shutil.which("node") is None, reason="node not on PATH")
def test_hero_selfcheck_passes():
    r = subprocess.run(
        ["node", str(_SELFCHECK)],
        capture_output=True, text=True, encoding="utf-8", cwd=str(_ROOT), timeout=120,
    )
    if r.returncode == 2:
        pytest.skip("jsdom not installed (run `npm install jsdom`)")
    assert r.returncode == 0, (r.stdout + r.stderr)
    assert "all checks passed" in r.stdout, (r.stdout + r.stderr)


# ── docs/120 item 11: the pair information the cards used to carry ────────

def test_topology_edges_carry_the_moving_qubit_role(tmp_path):
    """`moving_qubit` reaches the map. It is a ROLE ("control"/"target"), never
    a qubit id -- quam_builder writes it that way and defaults it to the
    higher-f_01 qubit, so the mover always coincides with C or T."""
    topo = _client(tmp_path).get("/api/topology").get_json()
    assert topo["edges"], "fixture must have pairs for this to mean anything"
    for e in topo["edges"]:
        assert "moving_qubit" in e
        assert e["moving_qubit"] in ("control", "target", None)


def test_a_declared_role_is_carried_through(tmp_path):
    topo = _client(tmp_path, moving="target").get("/api/topology").get_json()
    assert [e["moving_qubit"] for e in topo["edges"]] == ["target"]


def test_a_bogus_moving_qubit_is_dropped_not_passed_through(tmp_path):
    """Anything that is not one of the two roles becomes None. A qubit id here
    would make the map draw M at a position the value never meant."""
    topo = _client(tmp_path, moving="qA1").get("/api/topology").get_json()
    assert topo["edges"], "fixture must have pairs for this to mean anything"
    assert all(e["moving_qubit"] is None for e in topo["edges"])


class TestTheHeroMapIsKeyboardReachable:
    """`_wiring.html` promises "Tab into the grid, ←↑↓→ to move, Enter to
    inspect" — and neither worked on the hero map.

    docs/120 item 11 repointed the roving-grid selector from `.topo-node-card`
    (HTML divs) to `[data-hero-qubit]`, which are SVG `<g>` nodes. Two
    HTMLElement-only APIs came along for the ride: `offsetParent` (undefined on
    SVGElement, so the neighbour search scored all 20 stones "hidden" and
    nearest() always returned null) and `.click()` (absent, so Enter threw
    "cell.click is not a function" and the inspector never opened).

    Verified in real Chrome after the fix: q4 -> q5/q3/q9/q1 on the four
    arrows, Enter opens "QUBIT q4" with zero page errors.
    """

    def _js(self):
        from pathlib import Path
        return Path("quam_state_manager/web/static/chip-status.js").read_text(encoding="utf-8")

    def test_visibility_uses_a_predicate_that_exists_on_svg(self):
        src = self._js()
        i = src.index("function nearest(")
        body = src[i:i + 1200]
        assert "getClientRects().length" in body
        assert "c.offsetParent" not in body, "offsetParent is HTMLElement-only"

    def test_enter_dispatches_a_click_rather_than_calling_one(self):
        src = self._js()
        assert "cell.click()" not in src, "SVGElement has no .click()"
        assert "cell.dispatchEvent(new MouseEvent('click'" in src
        i = src.index("cell.dispatchEvent(new MouseEvent('click'")
        assert "bubbles: true" in src[i:i + 160], "the delegated handler needs it to bubble"

    def test_the_tip_that_promises_this_still_exists(self):
        """If the promise is removed instead of kept, this test should be the
        thing that says so."""
        from pathlib import Path
        tpl = Path("quam_state_manager/web/templates/_wiring.html").read_text(encoding="utf-8")
        assert "<kbd>Enter</kbd> to inspect" in tpl
