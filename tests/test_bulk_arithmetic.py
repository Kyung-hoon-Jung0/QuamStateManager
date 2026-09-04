"""Selection arithmetic on the Live State Edit grid (docs/167).

"Raise readout amplitude 10% on all 20 qubits" was twenty calculations and
twenty typed numbers. The grid already had multi-select, Ctrl+D and multi-line
paste; only arithmetic over a selection was missing.

The behaviour lives in `bulk-edit.js` and is driven under jsdom by
`bulk_arith_selfcheck.cjs`. What is asserted HERE is the part that is a
decision rather than a behaviour: that the server learned nothing, and that
the output shape the client promises is the shape the server actually parses.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent
_SELFCHECK = _ROOT / "tests" / "bulk_arith_selfcheck.cjs"


@pytest.mark.skipif(shutil.which("node") is None, reason="node not on PATH")
def test_bulk_arith_selfcheck_passes():
    r = subprocess.run(
        ["node", str(_SELFCHECK)],
        capture_output=True, text=True, encoding="utf-8", cwd=str(_ROOT),
    )
    if r.returncode == 2:
        pytest.skip("jsdom not installed (run `npm install jsdom`)")
    assert r.returncode == 0, (r.stdout + r.stderr)
    assert "all checks passed" in r.stdout, (r.stdout + r.stderr)


class TestTheServerLearnedNothing:
    """The central design decision, pinned as an absence.

    `parse_value` is shared by /field/edit, /field/edit-batch, the CLI, the
    type-fix offer and the pull REPLAY. A relative grammar there would reach
    every one of them, and a replay that re-multiplies compounds. Computing on
    the client also keeps `+5e6` meaning what it means today — an absolute
    literal a cell already accepts.
    """

    def _src(self, rel: str) -> str:
        return (_ROOT / "quam_state_manager" / rel).read_text(encoding="utf-8")

    def test_parse_value_never_learned_an_operator(self):
        tp = self._src("core/type_policy.py")
        body = tp[tp.index("def parse_value"):]
        body = body[:body.index("\ndef ", 10)]
        for op in ("'*'", '"*"', "startswith(\"*\")", "startswith('*')"):
            assert op not in body, f"parse_value must not learn {op}"

    def test_the_edit_routes_are_untouched(self):
        routes = self._src("web/routes.py")
        assert "arith" not in routes.lower(), (
            "the arithmetic is client-side; a server mention means a relative "
            "expression can reach a live write path"
        )

    def test_the_grid_markup_is_still_server_byte_identical(self):
        """The bar is JS-injected, so /bulk's HTML does not move (docs/111)."""
        tpl = (_ROOT / "quam_state_manager" / "web" / "templates"
               / "_bulkedit.html").read_text(encoding="utf-8")
        assert "bulk-arith" not in tpl


class TestTheOutputShapeIsTheContract:
    """The client promises plain grouped decimal; the server strips exactly
    those commas. Both halves are asserted against each other, so a change to
    either one that breaks the pair is visible here rather than in a lab."""

    def test_the_client_mirrors_the_servers_own_pattern(self):
        js = (_ROOT / "quam_state_manager" / "web" / "static"
              / "bulk-edit.js").read_text(encoding="utf-8")
        tp = (_ROOT / "quam_state_manager" / "core"
              / "type_policy.py").read_text(encoding="utf-8")
        assert r"var _PLAIN_GROUPED = /^[+-]?\d[\d,]*(\.\d+)?$/;" in js
        assert r'_PLAIN_GROUPED_NUMBER = re.compile(r"^[+-]?\d[\d,]*(\.\d+)?$")' in tp

    @pytest.mark.parametrize("text,expect", [
        ("0.2365", 0.2365),
        ("1,100,000,000", 1100000000),
        ("5,000,100", 5000100),
        ("0.000001", 0.000001),
        ("-0.49", -0.49),
        ("110", 110),
    ])
    def test_the_server_parses_what_the_client_writes(self, text, expect):
        """Every one of these is a literal the selfcheck asserts the client
        produces. If `parse_value` ever stopped accepting one, the feature
        would fail at the Apply, not here — which is why it is checked here."""
        from quam_state_manager.core.type_policy import parse_value
        assert parse_value(text) == pytest.approx(expect)

    def test_an_exponential_result_would_NOT_round_trip(self):
        """Why the client never emits exponential form, stated as a fact about
        the server rather than as a preference."""
        from quam_state_manager.core.type_policy import parse_value
        assert parse_value("1.1e9") == 1.1e9          # bare, it is fine …
        # … but the grouped form the grid displays is not exponential, and a
        # comma'd exponential is not a number to anybody:
        assert isinstance(parse_value("1,1e9"), str)
