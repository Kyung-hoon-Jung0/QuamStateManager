"""A band conflict is re-judged when the thing it accuses is fixed.

Customer, 2026-09-10: moving every XY band from 2 to 1 one cell at a time. Each
edit raised "Band 1 conflicts with LO peer q2 (band 2)" -- correct at the time
-- and when every cell had been changed the warnings were all still on screen
with the toolbar still reading "6 band issues".

The verdict was read off `data-peer-band` / `data-band`, which are the values
the SERVER rendered and which nothing writes to. The client half of the fix
(read the peer's own cell; re-judge the whole LO pair on an edit) is driven
under jsdom by ``bulk_band_live_selfcheck.cjs``. What is pinned HERE is the
half the client cannot do without: the server has to say which port a cell
belongs to and which pair that port is in, or the cells cannot find each other.

Deliberately NOT done, per the report itself: nothing re-runs Diagnostics. The
re-check is four inputs already on the page.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from quam_state_manager.web.app import create_app

_ROOT = Path(__file__).resolve().parent.parent
_SELFCHECK = _ROOT / "tests" / "bulk_band_live_selfcheck.cjs"


@pytest.mark.skipif(shutil.which("node") is None, reason="node not on PATH")
def test_band_live_selfcheck_passes():
    r = subprocess.run(
        ["node", str(_SELFCHECK)],
        capture_output=True, text=True, encoding="utf-8", cwd=str(_ROOT),
    )
    if r.returncode == 2:
        pytest.skip("jsdom not installed (run `npm install jsdom`)")
    assert r.returncode == 0, (r.stdout + r.stderr)
    assert "all checks passed" in r.stdout, (r.stdout + r.stderr)


class TestTheCellsCanFindEachOther:
    """con1/fem1 out2 and out3 share one LO. Both qubits' band cells must name
    the same group, and each must name its own port and its peer's."""

    @pytest.fixture
    def lo_client(self, tmp_path: Path):
        def _q(qid, freq):
            return {"id": qid, "f_01": freq,
                    "xy": {"opx_output": "#/wiring/qubits/%s/xy/opx_output" % qid,
                           "operations": {"x180": "#./x180_DragCosine",
                                          "x180_DragCosine": {"amplitude": 0.1}}}}
        state = {"qubits": {"qA1": _q("qA1", 5.05e9), "qA2": _q("qA2", 5.8e9)},
                 "qubit_pairs": {},
                 "ports": {"mw_outputs": {"con1": {"1": {
                     "2": {"band": 2, "upconverter_frequency": 5.05e9},
                     "3": {"band": 2, "upconverter_frequency": 5.8e9}}}}}}
        wiring = {"wiring": {"qubits": {
            "qA1": {"xy": {"opx_output": "#/ports/mw_outputs/con1/1/2"}},
            "qA2": {"xy": {"opx_output": "#/ports/mw_outputs/con1/1/3"}}}},
            "network": {"host": "1.1.1.1"}}
        (tmp_path / "state.json").write_text(json.dumps(state), encoding="utf-8")
        (tmp_path / "wiring.json").write_text(json.dumps(wiring), encoding="utf-8")
        app = create_app(testing=True, instance_path=str(tmp_path / "_i"))
        c = app.test_client()
        c.post("/load", data={"folder": str(tmp_path)})
        return c

    @staticmethod
    def _cell(body: str, qid: str, leaf: str) -> str:
        row = re.search(r'data-qubit="%s"(.*?)</tr>' % qid, body, re.S).group(1)
        return re.search(
            r'<input[^>]*data-dot-path="qubits\.%s\.xy\.opx_output\.%s"[^>]*>'
            % (qid, leaf), row).group(0)

    @staticmethod
    def _attr(cell: str, name: str) -> str | None:
        m = re.search(r'%s="([^"]*)"' % name, cell)
        return m.group(1) if m else None

    def test_the_pair_shares_one_group_and_each_names_both_ports(self, lo_client):
        body = lo_client.get("/bulk", headers={"HX-Request": "true"}).get_data(as_text=True)
        a = self._cell(body, "qA1", "band")
        b = self._cell(body, "qA2", "band")

        ga, gb = self._attr(a, "data-lo-group"), self._attr(b, "data-lo-group")
        assert ga and ga == gb, "the two ends of one LO pair must agree on the group"

        pa, pb = self._attr(a, "data-lo-port"), self._attr(b, "data-lo-port")
        assert pa and pb and pa != pb, "each cell names its OWN port"
        assert self._attr(a, "data-lo-peer-port") == pb
        assert self._attr(b, "data-lo-peer-port") == pa
        # The group is symmetric by construction, not by luck: it is built from
        # the two port keys sorted, so both ends compute the same string.
        assert ga == "|".join(sorted([pa, pb]))

    def test_the_frequency_cell_is_in_the_same_group(self, lo_client):
        """A band edit changes whether the LO frequency is inside its band, so
        the frequency cell has to be reachable from the band cell."""
        body = lo_client.get("/bulk", headers={"HX-Request": "true"}).get_data(as_text=True)
        band = self._cell(body, "qA1", "band")
        freq = self._cell(body, "qA1", "upconverter_frequency")
        assert self._attr(freq, "data-lo-group") == self._attr(band, "data-lo-group")
        assert self._attr(freq, "data-lo-port") == self._attr(band, "data-lo-port")

    def test_the_server_snapshot_is_still_there(self, lo_client):
        """The live read falls back to these when the peer's cell is not on the
        page (a hidden column, an unhydrated one), so they must not be dropped
        as 'now redundant' -- silence would be a worse answer than a stale one.
        """
        body = lo_client.get("/bulk", headers={"HX-Request": "true"}).get_data(as_text=True)
        a = self._cell(body, "qA1", "band")
        assert self._attr(a, "data-peer-band") == "2"
        assert self._attr(a, "data-peer") == "qA2"
        assert self._attr(a, "data-band") == "2"


class TestNothingReRunsDiagnostics:
    """The report's own constraint: the previous design ran diagnostics on every
    edit and was changed because it was too heavy. The fix must not bring that
    back — it re-reads cells, and asks the server nothing."""

    def test_the_client_fetches_nothing_to_re_judge_a_band(self):
        js = (_ROOT / "quam_state_manager" / "web" / "static" / "bulk-edit.js").read_text(
            encoding="utf-8")
        start = js.index("function _validateBandGroup(")
        body = js[start:js.index("\n    function ", start + 10)]
        for forbidden in ("fetch(", "htmx.ajax", "XMLHttpRequest", "diagnostics"):
            assert forbidden not in body, (
                "re-judging a band group must stay on the page: found %r" % forbidden)
        # ...and it is the GROUP, not the grid: the cells come from the group
        # selector, never from a whole-table sweep.
        assert "_loCellsIn(cell.getAttribute('data-lo-group'))" in body
