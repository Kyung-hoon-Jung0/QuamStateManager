"""docs/141 §4n — server-side column virtualization of the Live-Edit qubit grid.

The server renders the columns past the client's look-ahead window as EMPTY
tds plus a value map, and GET /bulk/cells fills them through the SAME cell
macro the page used. Pinned here:

- the planner mirrors the client's estimate (gates, hidden columns, the edge)
  and is conservative with an absent/garbage viewport hint
- a cold td is empty and keeps its identity (ck-N, flags, data-col-key); the
  cold map carries every cold cell's display + paths in row order; the hot
  cells are untouched; a small chip renders NOTHING cold (byte-identical)
- /bulk/cells returns, for every cold column, cell contents byte-identical
  to what the page renders for that cell when it is hot (the hydration
  identity — the one property the whole design rests on)
- the grid memo: a second request reuses the page's grid; an edit in between
  is reflected (the key changed); the chip guard answers 409
- the jsdom harness (bulk_virt_server_selfcheck.cjs) pins the client half
"""
from __future__ import annotations

import json
import re
from urllib.parse import quote
import shutil
import subprocess
from pathlib import Path

import pytest

from quam_state_manager.core import bulk_virt
from quam_state_manager.web.app import create_app


# ---------------------------------------------------------------------------
# the planner
# ---------------------------------------------------------------------------
def _cols(n, maxlen=12, default_on=True, label="col"):
    return [{"key": f"c{i}", "label": f"{label} {i}", "maxlen": maxlen, "default_on": default_on} for i in range(n)]


class TestPlan:
    def test_below_the_cell_gate_nothing_is_cold(self):
        assert bulk_virt.plan(_cols(50), 10, 800) == set()          # 500 cells < 600

    def test_below_the_cold_gate_nothing_is_cold(self):
        # 100 columns × 8 rows = 800 cells; at vw=800 the edge is 2,000 px and a
        # 12-char column is 124 px wide → ~16 hot, 84 cold → 672 cold cells < 800
        assert bulk_virt.plan(_cols(100), 8, 800) == set()

    def test_the_edge_is_the_left_edge_against_viewport_times_buffer(self):
        cols = _cols(100)
        cold = bulk_virt.plan(cols, 20, 800)
        w = bulk_virt.column_width_px(cols[0])
        first_cold = min(int(k[1:]) for k in cold)
        # the first cold column is the first whose left edge x = i*w exceeds 2000
        assert first_cold * w > 2000 >= (first_cold - 1) * w
        assert all(int(k[1:]) >= first_cold for k in cold)

    def test_a_hidden_column_is_cold_and_takes_no_width(self):
        cols = _cols(100)
        cols[0]["default_on"] = False
        cold = bulk_virt.plan(cols, 20, 800)
        assert "c0" in cold
        # c0 took no width, so the edge lands one column later than without it
        cold_all_on = bulk_virt.plan(_cols(100), 20, 800)
        assert min(int(k[1:]) for k in cold - {"c0"}) == min(int(k[1:]) for k in cold_all_on) + 1

    def test_the_label_can_be_the_wider_estimate(self):
        wide = _cols(100, maxlen=6, label="a very long column label indeed")
        narrow = _cols(100, maxlen=6)
        assert len(bulk_virt.plan(wide, 20, 800)) > len(bulk_virt.plan(narrow, 20, 800))

    @pytest.mark.parametrize("hint,expect", [
        (None, bulk_virt.DEFAULT_VIEWPORT_PX), ("", bulk_virt.DEFAULT_VIEWPORT_PX),
        ("abc", bulk_virt.DEFAULT_VIEWPORT_PX), ("-5", bulk_virt.DEFAULT_VIEWPORT_PX),
        ("0", bulk_virt.DEFAULT_VIEWPORT_PX), ("100", bulk_virt.MIN_VIEWPORT_PX),
        ("99999", bulk_virt.MAX_VIEWPORT_PX), ("1536", 1536), ("1536.0", 1536),
    ])
    def test_the_viewport_hint_is_clamped_and_defaults_wide(self, hint, expect):
        assert bulk_virt.viewport_px(hint) == expect

    def test_a_wider_viewport_never_adds_cold_columns(self):
        cols = _cols(120)
        a = bulk_virt.plan(cols, 20, 800)
        b = bulk_virt.plan(cols, 20, 1600)
        c = bulk_virt.plan(cols, 20, None)
        assert b <= a and c <= a

    def test_cold_map_shape(self):
        cols = _cols(3)
        rows = [{"id": "q1", "cells": [{"display": "1", "dot_path": "a", "resolved_path": "a"},
                                       {"display": "2", "dot_path": "b", "resolved_path": "B"},
                                       {"display": None, "dot_path": "", "resolved_path": ""}]},
                {"id": "q2", "cells": [{"display": "3", "dot_path": "c", "resolved_path": "c"},
                                       {"display": "4", "dot_path": "d", "resolved_path": "d"},
                                       {"display": "5", "dot_path": "e", "resolved_path": "e"}]}]
        m = bulk_virt.cold_map(cols, rows, {"c1", "c2"})
        assert m == {"rows": ["q1", "q2"],
                     "cols": {"c1": [["2", "b", "B"], ["4", "d", 0]],
                              "c2": [["", "", 0], ["5", "e", 0]]}}

    def test_parse_cols(self):
        assert bulk_virt.parse_cols("a,b,,a, c ,zz", ["a", "b", "c"]) == (["a", "b", "c"], ["zz"])
        assert bulk_virt.parse_cols(None, ["a"]) == ([], [])
        ok, bad = bulk_virt.parse_cols(",".join(f"k{i}" for i in range(1000)), [f"k{i}" for i in range(1000)], limit=10)
        assert len(ok) == 10 and not bad


# ---------------------------------------------------------------------------
# the route, on a synthetic chip wide enough to trip the gates
# ---------------------------------------------------------------------------
N_Q = 20
N_LEAVES = 60      # derived columns per qubit → with the curated ones, ~1,600+ cells


def _wide_state() -> dict:
    def _q(i):
        q = {"id": f"q{i}", "f_01": 5e9 + i * 1e6, "anharmonicity": -220e6, "T1": 2.4e-5,
             "xy": {"RF_frequency": 5e9 + i * 1e6,
                    "operations": {"x180": "#./x180_DragCosine",
                                   "x180_DragCosine": {"amplitude": 0.1 + i * 1e-3, "length": 40}}},
             "resonator": {"RF_frequency": 7e9 + i * 1e6, "confusion_matrix": [[0.9, 0.1], [0.1, 0.9]],
                           "operations": {"readout": {"amplitude": 0.04, "length": 1000}}},
             "z": {"joint_offset": 0.05},
             "extras_calibration": {f"leaf_{k:02d}": (i + 1) * 1000 + k for k in range(N_LEAVES)}}
        return q
    return {"qubits": {f"q{i}": _q(i) for i in range(N_Q)}, "qubit_pairs": {},
            "active_qubit_names": [f"q{i}" for i in range(N_Q)]}


def _small_state() -> dict:
    s = _wide_state()
    keep = ["q0", "q1"]
    s["qubits"] = {k: s["qubits"][k] for k in keep}
    for q in s["qubits"].values():
        q["extras_calibration"] = {"leaf_00": 1}
    s["active_qubit_names"] = keep
    return s


def _client(tmp_path: Path, state: dict):
    (tmp_path / "state.json").write_text(json.dumps(state), encoding="utf-8")
    (tmp_path / "wiring.json").write_text(json.dumps({"wiring": {"qubits": {}}, "network": {"host": "10.1.1.1"}}),
                                          encoding="utf-8")
    app = create_app(testing=True, instance_path=str(tmp_path / "_i"))
    c = app.test_client()
    assert c.post("/load", data={"folder": str(tmp_path)}).status_code in (200, 302)
    return c


_TD = re.compile(r'<td class="(bulk-td[^"]*)" data-col-key="([^"]*)">(.*?)</td>', re.S)


def _qubit_grid_cells(html: str) -> dict[tuple[str, str], tuple[str, str]]:
    """(row id, column key) -> (td class, inner html) for the QUBIT grid."""
    out = {}
    tbl = html[html.index('id="bulk-table"'):]
    tbl = tbl[:tbl.index("</table>")]
    for row in re.finditer(r'<tr data-qubit="([^"]*)">(.*?)</tr>', tbl, re.S):
        rid = row.group(1)
        for m in _TD.finditer(row.group(2)):
            out[(rid, m.group(2))] = (m.group(1), m.group(3))
    return out


def _cold_map(html: str) -> dict | None:
    m = re.search(r'id="bulk-cold-map">(.*?)</script>', html, re.S)
    return json.loads(m.group(1)) if m else None


@pytest.fixture
def wide(tmp_path: Path):
    return _client(tmp_path, _wide_state())


class TestColdRender:
    def test_a_narrow_viewport_renders_cold_columns_and_the_map(self, wide):
        html = wide.get("/bulk?vw=800").get_data(as_text=True)
        cells = _qubit_grid_cells(html)
        cold = {k for k, (cls, inner) in cells.items() if "bulk-td-cold" in cls}
        hot = {k for k, (cls, inner) in cells.items() if "bulk-td-cold" not in cls}
        assert len(cold) >= bulk_virt.MIN_COLD and hot, (len(cold), len(hot))
        for k in cold:
            cls, inner = cells[k]
            assert inner == "", f"a cold td must be empty: {k} {inner[:80]!r}"
            assert re.search(r"\bck-\d+\b", cls), cls
        for k in hot:
            assert '<input' in cells[k][1] or 'bulk-cell-list' in cells[k][1]
        m = _cold_map(html)
        assert m is not None
        assert m["rows"] == [f"q{i}" for i in range(N_Q)]
        cold_keys = {k for (_, k) in cold}
        assert set(m["cols"]) == cold_keys
        for k, entries in m["cols"].items():
            assert len(entries) == N_Q
            for e in entries:
                assert isinstance(e, list) and len(e) == 3

    def test_the_cold_map_display_is_what_the_hot_render_shows(self, wide):
        cold_html = wide.get("/bulk?vw=800").get_data(as_text=True)
        m = _cold_map(cold_html)
        assert m
        # the same chip rendered with the widest viewport: far fewer cold
        # columns; compare where the wide render is hot
        wide_html = wide.get(f"/bulk?vw={bulk_virt.MAX_VIEWPORT_PX}").get_data(as_text=True)
        wide_cells = _qubit_grid_cells(wide_html)
        checked = 0
        for k, entries in m["cols"].items():
            for rid, (disp, dp, rp) in zip(m["rows"], entries):
                cls, inner = wide_cells[(rid, k)]
                if "bulk-td-cold" in cls:
                    continue
                val = re.search(r'value="([^"]*)"', inner)
                shown = val.group(1) if val else re.search(r">([^<]*)</span>", inner).group(1)
                assert shown == disp, (rid, k, shown, disp)
                if dp:
                    assert f'data-dot-path="{dp}"' in inner or f'data-path="{dp}"' in inner, (rid, k)
                checked += 1
        assert checked > 100

    def test_hot_cells_and_headers_are_untouched_by_the_plan(self, wide):
        a = wide.get("/bulk?vw=800").get_data(as_text=True)
        b = wide.get(f"/bulk?vw={bulk_virt.MAX_VIEWPORT_PX}").get_data(as_text=True)
        ca, cb = _qubit_grid_cells(a), _qubit_grid_cells(b)
        assert set(ca) == set(cb)
        both_hot = [k for k in ca if "bulk-td-cold" not in ca[k][0] and "bulk-td-cold" not in cb[k][0]]
        assert both_hot
        for k in both_hot:
            assert ca[k] == cb[k], k
        assert re.findall(r'<th scope="col" class="bulk-col-head[^>]*>', a) == re.findall(r'<th scope="col" class="bulk-col-head[^>]*>', b)
        assert 'data-maxlen="' in a

    def test_a_small_chip_renders_nothing_cold(self, tmp_path):
        c = _client(tmp_path, _small_state())
        html = c.get("/bulk?vw=320").get_data(as_text=True)
        assert "bulk-td-cold" not in html and 'id="bulk-cold-map"' not in html

    def test_no_hint_means_the_wide_default(self, wide):
        none = _qubit_grid_cells(wide.get("/bulk").get_data(as_text=True))
        narrow = _qubit_grid_cells(wide.get("/bulk?vw=800").get_data(as_text=True))
        cold_none = {k for k, v in none.items() if "bulk-td-cold" in v[0]}
        cold_narrow = {k for k, v in narrow.items() if "bulk-td-cold" in v[0]}
        assert cold_none <= cold_narrow and len(cold_none) < len(cold_narrow)


class TestCellsRoute:
    def test_hydration_is_byte_identical_to_the_hot_render(self, wide):
        """The property the design rests on: a cell filled from /bulk/cells is
        exactly the cell the page would have rendered hot."""
        cold_html = wide.get("/bulk?vw=800").get_data(as_text=True)
        m = _cold_map(cold_html)
        cold_keys = list(m["cols"])
        hot_html = wide.get(f"/bulk?vw={bulk_virt.MAX_VIEWPORT_PX}").get_data(as_text=True)
        hot_cells = _qubit_grid_cells(hot_html)
        r = wide.get("/bulk/cells?cols=" + ",".join(cold_keys))
        assert r.status_code == 200, r.data[:200]
        d = r.get_json()
        assert d["ok"] and set(d["cells"]) == set(cold_keys) and d["unknown"] == []
        compared = 0
        for k in cold_keys:
            assert set(d["cells"][k]) == set(m["rows"])
            for rid, inner in d["cells"][k].items():
                cls, hot_inner = hot_cells[(rid, k)]
                if "bulk-td-cold" in cls:
                    continue          # still cold at the widest viewport: nothing to compare
                assert inner == hot_inner, (rid, k, inner[:120], hot_inner[:120])
                compared += 1
        assert compared > 200, compared

    def test_unknown_columns_are_named_and_an_empty_ask_is_400(self, wide):
        wide.get("/bulk?vw=800")
        r = wide.get("/bulk/cells?cols=nope,f_01")
        assert r.status_code == 200 and r.get_json()["unknown"] == ["nope"] and "f_01" in r.get_json()["cells"]
        r = wide.get("/bulk/cells?cols=nope")
        assert r.status_code == 400 and r.get_json()["unknown"] == ["nope"]
        r = wide.get("/bulk/cells")
        assert r.status_code == 400

    def test_an_unknown_column_is_LOGGED_not_only_answered(self, wide, caplog):
        """docs/141 4ae B-9. The unknown names went into a response body no
        client renders and NOWHERE else -- which is why 4ad's own "a 400 `no
        known column named`, seen once, never reproduced" could not be
        diagnosed, and why the next occurrence would have been equally
        undiagnosable. The route logs one warning naming them."""
        import logging

        wide.get("/bulk?vw=800")
        with caplog.at_level(logging.WARNING, logger="quam_state_manager.web.routes"):
            r = wide.get("/bulk/cells?cols=nope,f_01")
        assert r.status_code == 200
        hits = [rec for rec in caplog.records if "/bulk/cells" in rec.getMessage()]
        assert hits, "a partially-unknown ask logged nothing"
        assert "nope" in hits[-1].getMessage() and "unknown" in hits[-1].getMessage()
        # the all-unknown ask -- the 400 that was seen once and never reproduced
        caplog.clear()
        with caplog.at_level(logging.WARNING, logger="quam_state_manager.web.routes"):
            r = wide.get("/bulk/cells?cols=nope&grid=pair")
        assert r.status_code == 400
        hits = [rec for rec in caplog.records if "/bulk/cells" in rec.getMessage()]
        assert hits and "nope" in hits[-1].getMessage() and "pair" in hits[-1].getMessage()
        # a fully-known ask stays quiet: a log line every hydration would be
        # noise, and noise is how the one that matters gets missed
        caplog.clear()
        with caplog.at_level(logging.WARNING, logger="quam_state_manager.web.routes"):
            wide.get("/bulk/cells?cols=f_01")
        assert not [rec for rec in caplog.records if "/bulk/cells" in rec.getMessage()]

    def test_the_chip_guard(self, wide):
        wide.get("/bulk?vw=800")
        r = wide.get("/bulk/cells?cols=f_01&chip=someone-else")
        assert r.status_code == 409 and "different chip" in r.get_json()["error"]
        ident = wide.get("/bulk/cells?cols=f_01").get_json()["chip"]
        r = wide.get(f"/bulk/cells?cols=f_01&chip={ident}")
        assert r.status_code == 200

    def test_the_grid_memo_is_reused_and_invalidated_by_an_edit(self, wide, monkeypatch):
        from quam_state_manager.web import routes as R
        calls = []
        real = R._qubit_bulk_grid

        def counted(*a, **kw):
            calls.append(1)
            return real(*a, **kw)
        monkeypatch.setattr(R, "_qubit_bulk_grid", counted)
        wide.get("/bulk?vw=800")
        n0 = len(calls)
        wide.get("/bulk/cells?cols=f_01")
        assert len(calls) == n0, "the hydration reused the page's grid"
        # an edit changes the key: the next hydration rebuilds and shows the edit
        r = wide.post("/field/edit", data={"dot_path": "qubits.q3.f_01", "value": "4123456789"})
        assert r.status_code == 200, r.data[:200]
        d = wide.get("/bulk/cells?cols=f_01").get_json()
        assert len(calls) == n0 + 1
        assert re.search(r'value="4,123,456,789(\.0)?"', d["cells"]["f_01"]["q3"]), d["cells"]["f_01"]["q3"][:160]
        assert "bulk-cell-modified" in d["cells"]["f_01"]["q3"]
        # a different hidden set is a different grid
        wide.get("/bulk/cells?cols=f_01&dynhide=dyn__extras_calibration_leaf_00")
        assert len(calls) == n0 + 2

    def test_gzip_when_asked(self, wide):
        wide.get("/bulk?vw=800")
        r = wide.get("/bulk/cells?cols=f_01", headers={"Accept-Encoding": "gzip"})
        assert r.headers.get("Content-Encoding") == "gzip"
        import gzip as _gz
        assert json.loads(_gz.decompress(r.data))["ok"]


@pytest.mark.skipif(shutil.which("node") is None, reason="node not available")
def test_bulk_virt_server_selfcheck():
    """The client half against the REAL bulk-edit.js under jsdom."""
    node = shutil.which("node")
    root = Path(__file__).resolve().parent.parent
    try:
        subprocess.run([node, "-e", "require('jsdom')"], check=True, capture_output=True, timeout=30)
    except Exception:
        pytest.skip("jsdom not installed")
    r = subprocess.run([node, str(root / "tests" / "bulk_virt_server_selfcheck.cjs")],
                       capture_output=True, text=True, encoding="utf-8", timeout=180, cwd=str(root))
    if r.returncode == 2:
        pytest.skip("jsdom not installed")
    assert r.returncode == 0, r.stdout + r.stderr
    assert r.stdout.count("ok - ") >= 28, r.stdout


class TestChipGateIdentity:
    """docs/141 4ac (C1-2). `/bulk/cells` promised "a different chip open in
    this context now answers 409 rather than cells from the wrong chip", and
    compared the DISPLAY NAME -- which, for a plain folder, is the basename.
    A chip and its backup, or two labs' `quam_state` folders added as separate
    workspace roots, were one chip to that gate: the completeness critic
    measured B's values hydrating a page rendered from A, 20/20 rows, into
    editable inputs whose `data-orig` still held A's values.
    """

    @staticmethod
    def _chip(root, name, bump):
        d = root / name / "CHIP"
        d.mkdir(parents=True)
        (d / "state.json").write_text(json.dumps({
            "qubits": {f"q{i}": {"T1": 1e-5 + bump + i * 1e-7,
                                 "f_01": 5e9 + bump,
                                 "xy": {"operations": {"x180": {"amplitude": 0.1 + bump}}}}
                       for i in range(6)},
            "qubit_pairs": {},
            "active_qubit_names": [f"q{i}" for i in range(6)]}), encoding="utf-8")
        (d / "wiring.json").write_text(json.dumps({"wiring": {}, "network": {"host": "1.1.1.1"}}), encoding="utf-8")
        return d

    def test_two_chips_with_the_same_folder_name_do_not_hydrate_each_other(self, tmp_path):
        a = self._chip(tmp_path, "labA", 0.0)
        b = self._chip(tmp_path, "backup", 7.0)
        assert a.name == b.name, "fixture: the same basename in two parents"

        app = create_app(testing=True, instance_path=str(tmp_path / "_i"))
        c = app.test_client()
        assert c.post("/load", data={"folder": str(a)}).status_code in (200, 302)
        html = c.get("/bulk", headers={"HX-Request": "true"}).get_data(as_text=True)
        m = re.search(r"'chipKey':\s*'([^']*)'", html) or re.search(r'"chipKey":\s*"([^"]*)"', html)
        assert m, "the page must ship a gate token"
        tok_a = m.group(1)
        assert tok_a, "and it must not be empty"

        # the SAME context now holds the other chip
        assert c.post("/load", data={"folder": str(b)}).status_code in (200, 302)
        r = c.get(f"/bulk/cells?cols=T1&chip={quote(tok_a)}")
        assert r.status_code == 409, \
            f"chip B must refuse A's token, got {r.status_code}: {r.get_data(as_text=True)[:200]}"

        # and B's own token still works, so the gate is not simply always-on
        html_b = c.get("/bulk", headers={"HX-Request": "true"}).get_data(as_text=True)
        mb = re.search(r"'chipKey':\s*'([^']*)'", html_b) or re.search(r'"chipKey":\s*"([^"]*)"', html_b)
        assert mb and mb.group(1) and mb.group(1) != tok_a, "the two tokens differ"
        assert c.get(f"/bulk/cells?cols=T1&chip={quote(mb.group(1))}").status_code == 200

    def test_the_display_name_is_still_accepted(self, tmp_path):
        """An older page (rendered before this change) sends the bare name and
        must still be able to hydrate itself."""
        a = self._chip(tmp_path, "solo", 0.0)
        app = create_app(testing=True, instance_path=str(tmp_path / "_i"))
        c = app.test_client()
        c.post("/load", data={"folder": str(a)})
        assert c.get(f"/bulk/cells?cols=T1&chip={quote(a.name)}").status_code == 200

    def test_the_storage_key_prefix_did_not_move(self, tmp_path):
        """`QMETA.chip` is also the localStorage prefix for the Qubits/Pairs
        pickers (`quam_bulk_qhidden:<chip>`), so it must stay the DISPLAY name
        -- folding the path into it would silently drop every persisted set."""
        js = (Path(__file__).resolve().parent.parent / "quam_state_manager/web/static/bulk-edit.js").read_text(encoding="utf-8")
        assert "QHIDE_PREFIX + (QMETA.chip || 'chip')" in js
        assert "QMETA.chipKey" in js, "the token rides beside it"
        # docs/141 4ad: the token now rides the binding's urlParams()
        i = js.index("var tok = QMETA && (QMETA.chipKey || QMETA.chip);")
        assert "&chip=' + encodeURIComponent(tok)" in js[i:i + 300]


class TestWidthMetricsMirror:
    """docs/141 4ac. `core/bulk_virt.py` says its numbers are the client's,
    mirrored. Nothing checked that, and every plan test derives its expected
    edge from those same constants, so all four could be changed with the
    suite green -- while the plan they produce decides how much of a real
    chip arrives empty."""

    @staticmethod
    def _js():
        """The client half of the width estimate. docs/141 4ad moved the
        mechanism into grid-virt.js so the pair grid could share it; the
        arithmetic this pin mirrors went with it, and the qubit grid's
        BINDING to it stayed in bulk-edit.js."""
        return (Path(__file__).resolve().parent.parent
                / "quam_state_manager/web/static/grid-virt.js").read_text(encoding="utf-8")

    @staticmethod
    def _owner_js():
        return (Path(__file__).resolve().parent.parent
                / "quam_state_manager/web/static/bulk-edit.js").read_text(encoding="utf-8")

    def test_the_width_metrics_mirror_the_client(self):
        from quam_state_manager.core import bulk_virt

        js = self._js()
        m = re.search(r"EST_PAD\s*=\s*([\d.]+)", js)
        assert m, "the client's cell padding constant"
        assert bulk_virt.PAD_PX == float(m.group(1)), \
            f"PAD_PX {bulk_virt.PAD_PX} != the client's {m.group(1)}"

        # the header-label estimate: `<label length> * A + B`
        lab = re.search(r"\.length\s*\*\s*([\d.]+)\s*\+\s*([\d.]+)", js)
        assert lab, "the client's label width estimate"
        assert bulk_virt.LABEL_PX_PER_CHAR == float(lab.group(1))
        assert bulk_virt.LABEL_PAD_PX == float(lab.group(2))

        buf = re.search(r"BUFFER\s*=\s*([\d.]+)", js)
        assert buf and bulk_virt.BUFFER == float(buf.group(1))
        cells = re.search(r"MIN_CELLS\s*=\s*(\d+)", js)
        assert cells and bulk_virt.MIN_CELLS == int(cells.group(1))
        cold = re.search(r"MIN_COLD\s*=\s*(\d+)", js)
        assert cold and bulk_virt.MIN_COLD == int(cold.group(1))

    def test_the_server_never_assumes_a_wider_glyph_than_the_client_default(self):
        """The one direction that can be asserted. `server <= client` in
        general is NOT a property -- a drag-resized column (docs/111) overrides
        the client's estimate and the server cannot know it -- but the server
        must not exceed what the client computes at its DEFAULT font scale."""
        from quam_state_manager.core import bulk_virt

        js = self._js()
        m = re.search(r"rootPx\s*\*\s*([\d.]+)\s*\*\s*fs\s*\*\s*([\d.]+)", js)
        assert m, "the client's px/char formula (_virtPxPerChar)"
        a, b = float(m.group(1)), float(m.group(2))
        default_root, default_fs = 16.0, 1.0
        client_default = default_root * a * default_fs * b
        assert bulk_virt.PX_PER_CHAR <= client_default + 1e-9, (
            f"the server would plan colder than the client's own default "
            f"({bulk_virt.PX_PER_CHAR} > {client_default:.3f})")

    def test_the_hint_is_the_quantity_the_client_measures_with(self):
        """The plan matches the client exactly only because both use
        `screen.availWidth` -- not `innerWidth`, which forces layout in Blink
        (docs/141 4i) and is a different number besides."""
        owner = self._owner_js()
        i = owner.index("var vw = ")
        assert "window.screen.availWidth" in owner[i:i + 200], owner[i:i + 120]
        # and the core's own edge reads the same quantity
        core = self._js()
        j = core.index("var edge = ")
        assert "window.screen.availWidth" in core[j:j + 200], core[j:j + 120]
        assert "innerWidth" not in owner[i:i + 200] and "innerWidth" not in core[j:j + 200]


class TestGridVirtBinding:
    """docs/141 4ad. The mechanism is shared now, so WHICH grid an instance is
    driving is a fact that lives only in the binding -- and it is exactly what
    a second consumer can get wrong (the wrong table, the wrong row attribute,
    a shared element id that lets one grid wipe the other's width rules)."""

    @staticmethod
    def _read(name):
        return (Path(__file__).resolve().parent.parent
                / "quam_state_manager/web/static" / name).read_text(encoding="utf-8")

    @staticmethod
    def _code(js):
        """The file with its comments removed -- the claim below is about what
        the CODE names, and the module's prose legitimately explains which two
        grids it was lifted out of."""
        out, i, n = [], 0, len(js)
        while i < n:
            if js.startswith("/*", i):
                i = js.find("*/", i)
                i = n if i < 0 else i + 2
            elif js.startswith("//", i):
                j = js.find("\n", i)
                i = n if j < 0 else j
            else:
                out.append(js[i])
                i += 1
        return "".join(out)

    def test_the_core_knows_nothing_about_qubits_or_pairs(self):
        core = self._code(self._read("grid-virt.js"))
        # `.bulk-table-wrap` is NOT in this list on purpose: the pair wrap
        # carries that class too (`class="bulk-table-wrap bulk-pair-table-wrap"`),
        # so it is the shared frame, not one grid's fact.
        for word in ("qubit", "data-pair", "#bulk-table", "#bulk-pair-table",
                     "bulk-pair-table-wrap", "QMETA", "dynhide",
                     "BulkEdit", "BulkPairEdit"):
            assert word not in core, f"grid-virt.js's CODE must not name {word!r}"

    def test_the_qubit_binding_names_its_own_dom(self):
        owner = self._read("bulk-edit.js")
        i = owner.index("window.GridVirt.create({")
        block = owner[i:i + 1400]
        assert "styleId: 'bulk-virt-width-style'" in block
        assert "noteId: 'bulk-virt-note'" in block
        assert "mapId: 'bulk-cold-map'" in block
        assert "tableSel: '#bulk-table'" in block
        assert "rowAttr: 'data-qubit'" in block

    def test_the_missing_module_explains_itself(self):
        """docs/141 4ae B-7. `need(names).then(go, go)` never strands a
        navigation on a lost script and `asset_url` falls back to a plain URL,
        so a grid-virt.js that does not arrive left BOTH grids with a
        permanently blank cold half -- no console error, no note, no recovery
        (measured on the 20Q chip: 3,960 of 4,480 qubit cells and 2,730 of
        3,330 pair cells empty, still empty after scrolling to 8,000 px).
        The helper lives in app.js because app.js is a CORE script."""
        app = self._read("app.js")
        assert "/* GRIDVIRT-MISSING-NOTE:BEGIN" in app and "/* GRIDVIRT-MISSING-NOTE:END */" in app, (
            "the sentinels bulk_virt_server_selfcheck.cjs slices between are gone -- "
            "the harness would eval an empty string and pass vacuously")
        assert "window.GridVirtMissingNote" in app
        for name, note_id in (("bulk-edit.js", "bulk-virt-note"),
                              ("pair-edit.js", "bulk-pair-virt-note")):
            js = self._code(self._read(name))
            assert f"window.GridVirtMissingNote(table(), '{note_id}')" in js, (
                f"{name} does not say anything when GridVirt is absent")

    def test_the_three_blank_states_do_not_look_alike(self):
        """docs/141 4ae B-10. LOADING (`bulk-td-cold`), NEVER-COMING
        (`bulk-td-dead`) and GENUINELY-ABSENT (an ordinary empty cell) were
        pixel-identical: same borders, same background, no skeleton, no
        placeholder, no title, no aria. The loading treatment is deliberately a
        FLAT background plus a cursor and nothing else -- measured on the real
        20Q chip's 7,200 cold cells, 20 style-recalc toggles cost 569 ms and
        ZERO layout, while a per-cell shimmer skeleton (::after + gradient +
        animation) cost 3,886 ms and 814 ms of layout, i.e. 6.8x, on top of a
        permanent compositor animation. The heavier treatment is on the DEAD
        cells only, whose count is bounded by what one refusal names."""
        css = (Path(__file__).resolve().parent.parent
               / "quam_state_manager/web/static/style.css").read_text(encoding="utf-8")
        import re

        cold = re.search(r"^\.bulk-td-cold \{([^}]*)\}", css, re.M)
        assert cold, "no .bulk-td-cold rule: a loading cell looks like an empty one"
        assert "background" in cold.group(1) and "cursor" in cold.group(1)
        # the cost claim, as a rule: nothing per-cell beyond a paint
        assert "animation" not in cold.group(1) and "content" not in cold.group(1)
        assert not re.search(r"^\.bulk-td-cold::(after|before)", css, re.M), (
            "a pseudo-element on every cold cell is the 6.8x design that was measured and rejected")
        dead = re.search(r"^\.bulk-td-dead \{([^}]*)\}", css, re.M)
        assert dead and "background" in dead.group(1), "no .bulk-td-dead rule"
        assert cold.group(1).strip() != dead.group(1).strip(), (
            "loading and never-coming must not render alike")
        assert re.search(r"^\.bulk-td-dead::after", css, re.M), (
            "the dead cells carry the glyph -- their count is bounded")
        # and the class is actually stamped, by the two places a column retires
        core = self._code(self._read("grid-virt.js"))
        assert core.count("markDead(") >= 3, "markDead is not called from both retire paths"
        assert "bulk-td-dead" in core and "title" in core

    def test_the_two_grids_share_no_element_id(self):
        """One <style> for both would let each grid erase the other's frozen
        widths; one note element would make one grid's failure line appear
        over the other's table."""
        owner = self._read("bulk-edit.js")
        pair = self._read("pair-edit.js")
        if "window.GridVirt.create({" not in pair:
            pytest.skip("the pair grid does not use GridVirt yet")
        def ids(js):
            i = js.index("window.GridVirt.create({")
            b = js[i:i + 1400]
            return {k: re.search(k + r": '([^']+)'", b).group(1)
                    for k in ("styleId", "noteId", "mapId", "tableSel")}
        a, b = ids(owner), ids(pair)
        for k in a:
            assert a[k] != b[k], f"both grids use {k}={a[k]!r}"

    def test_the_scroll_binding_is_per_instance(self):
        """Both grids scroll the SAME element (#table-pane, docs/141 4q), so a
        single `_virtScrollBound` flag on it would let whichever grid mounted
        first silence the other's hydration."""
        core = self._read("grid-virt.js")
        assert "'_virtScrollBound_' + styleId" in core, \
            "the scroll-bound flag must be per instance, not per element"


class TestPairGridVirt:
    """docs/141 4ad -- the PAIR grid is virtualized by the same mechanism.

    §4n left it whole on purpose ("generalize the mechanism into a shared
    module before a second consumer appears"), and §4ac then measured what
    that cost: 1,489,999 of the document's 2,809,432 bytes on the PJ 20Q chip
    -- 53%, the largest single block left once the qubit grid had been
    slimmed. `core/bulk_virt` needed no change at all: it takes columns + rows
    and the pair grid's rows already had the shape it reads.
    """

    @staticmethod
    def _chip(tmp_path, n_pairs=30, n_macros=14):
        """A chip with enough pair cells to cross BOTH client gates:
        MIN_CELLS (columns x rows) and MIN_COLD (cold cells). 30 pairs x
        58 derived columns clears them with room; 1 pair does not, which
        is what `test_a_small_chip_is_left_alone` rides."""
        qubits = {}
        pairs = {}
        names = [f"q{i}" for i in range(n_pairs + 1)]
        for q in names:
            qubits[q] = {"T1": 1e-5, "f_01": 5e9,
                         "xy": {"operations": {"x180": {"amplitude": 0.1}}},
                         "resonator": {"operations": {"readout": {"amplitude": 0.04}}}}
        for i in range(n_pairs):
            a, b = names[i], names[i + 1]
            pairs[f"{a}-{b}"] = {
                "qubit_control": f"#/qubits/{a}", "qubit_target": f"#/qubits/{b}",
                "gate_fidelity": {"averaged": 0.99 + i * 1e-4},
                "confusion": [[0.98, 0.02], [0.03, 0.97]],
                "macros": {f"cz_{k}": {"amplitude": 0.1 + k * 0.01,
                                       "duration": 40 + k,
                                       "phase_shift": 0.01 * k,
                                       "detuning": 1e6 + k}
                           for k in range(n_macros)},
            }
        d = tmp_path / "chip"
        d.mkdir()
        (d / "state.json").write_text(json.dumps({
            "qubits": qubits, "qubit_pairs": pairs,
            "active_qubit_names": names,
            "active_qubit_pair_names": list(pairs)}), encoding="utf-8")
        (d / "wiring.json").write_text(json.dumps(
            {"wiring": {}, "network": {"host": "1.1.1.1"}}), encoding="utf-8")
        return d

    @staticmethod
    def _mixed_chip(tmp_path, n_pairs=30, n_macros=14):
        """The same chip plus an ``extras`` band carrying one column of EVERY
        pair-cell kind ``pair_cell`` branches on: a list, a ``#./`` runtime
        self-ref, a leaf only half the pairs have (-> the blank ``missing``
        cell) and a plain scalar.

        ``extras`` sorts LAST (``pair_columns._order_key``: base 900), so all
        four land COLD -- which is the whole point. A plain scalar renders
        byte-identically through ``qubit_cell`` and ``pair_cell`` (their
        scalar branches differ only in the LO-peer glyph, which needs wired
        ports), so a scalar-only fixture would let a swapped macro pass every
        assert below and the two pins would be weaker than they read.
        """
        d = TestPairGridVirt._chip(tmp_path, n_pairs=n_pairs, n_macros=n_macros)
        f = d / "state.json"
        st = json.loads(f.read_text(encoding="utf-8"))
        for i, p in enumerate(st["qubit_pairs"].values()):
            ex = {"zz_scalar": 0.5 + i * 1e-3,
                  "zz_matrix": [[0.9, 0.1], [0.1, 0.9]],
                  "zz_runtime": "#./gate_fidelity"}
            if i % 2 == 0:                      # -> a `missing` cell on the odd pairs
                ex["zz_sometimes"] = 1.0 + i
            p["extras"] = ex
        f.write_text(json.dumps(st), encoding="utf-8")
        return d

    @staticmethod
    def _pair_grid_cells(html):
        """(pair id, column key) -> (td class, inner html) for the PAIR grid.

        The pair <tr> carries data-qubit AND data-pair (both the row id), so
        the qubit grid's own `<tr data-qubit="...">` reader cannot see these
        rows and this one cannot see its.
        """
        out = {}
        tbl = html[html.index('id="bulk-pair-table"'):]
        tbl = tbl[:tbl.index("</table>")]
        for row in re.finditer(r'<tr [^>]*data-pair="([^"]*)"[^>]*>(.*?)</tr>', tbl, re.S):
            rid = row.group(1)
            for m in _TD.finditer(row.group(2)):
                out[(rid, m.group(2))] = (m.group(1), m.group(3))
        return out

    def _client(self, tmp_path):
        d = self._chip(tmp_path)
        app = create_app(testing=True, instance_path=str(tmp_path / "_i"))
        c = app.test_client()
        assert c.post("/load", data={"folder": str(d)}).status_code in (200, 302)
        return c

    def _mixed_client(self, tmp_path):
        d = self._mixed_chip(tmp_path)
        app = create_app(testing=True, instance_path=str(tmp_path / "_i"))
        c = app.test_client()
        assert c.post("/load", data={"folder": str(d)}).status_code in (200, 302)
        return c

    @staticmethod
    def _pair_cold_map(html):
        m = re.search(r'id="bulk-pair-cold-map">(.*?)</script>', html, re.S)
        return json.loads(m.group(1)) if m else None

    def test_the_planner_reads_pair_columns_unchanged(self, tmp_path):
        """The reason this was a lift and not a rebuild."""
        from quam_state_manager.core import bulk_virt
        from quam_state_manager.web import routes as R

        d = self._chip(tmp_path)
        app = create_app(testing=True, instance_path=str(tmp_path / "_i"))
        c = app.test_client()
        c.post("/load", data={"folder": str(d)})
        with app.test_request_context("/bulk"):
            cols, _g, rows = R._pair_bulk_grid(R._store(), R._modified_map())
        assert cols and rows
        for col in cols:
            assert "key" in col and "maxlen" in col and "default_on" in col
        assert all(set(r) >= {"id", "cells"} for r in rows)
        cold = bulk_virt.plan(cols, len(rows), 1200)
        assert cold and cold <= {c0["key"] for c0 in cols}

    def test_a_cold_pair_column_ships_empty_with_its_value_in_the_map(self, tmp_path):
        c = self._client(tmp_path)
        html = c.get("/bulk?vw=800", headers={"HX-Request": "true"}).get_data(as_text=True)
        assert 'id="bulk-pair-cold-map"' in html, "the pair grid must ship its OWN map"
        m = re.search(r'id="bulk-pair-cold-map">(.*?)</script>', html, re.S)
        cmap = json.loads(m.group(1))
        assert cmap["cols"] and cmap["rows"]
        # every cold pair column's tds are empty, and keep their identity
        pair_block = html[html.index('id="bulk-pair-table"'):html.index("</table>", html.index('id="bulk-pair-table"'))]
        for key in cmap["cols"]:
            hits = re.findall(
                r'<td class="[^"]*bulk-td-cold[^"]*" data-col-key="' + re.escape(key) + r'"></td>',
                pair_block)
            assert hits, f"{key} should render as empty cold tds"
            assert len(hits) == len(cmap["rows"])
        # and a HOT pair column still carries its input
        assert 'class="bulk-cell' in pair_block

    def test_the_two_grids_do_not_share_a_cold_map(self, tmp_path):
        """One id would let each grid read the other's values into its search
        haystack -- and the row ids do not even overlap. Checked on the REAL
        chip when it is here (both grids virtualized there); on the synthetic
        one the qubit grid stays whole, which is itself worth asserting."""
        c = self._client(tmp_path)
        html = c.get("/bulk?vw=800", headers={"HX-Request": "true"}).get_data(as_text=True)
        assert html.count('id="bulk-pair-cold-map"') == 1, "one map per grid"
        assert html.count('id="bulk-cold-map"') <= 1
        pm = json.loads(re.search(r'id="bulk-pair-cold-map">(.*?)</script>', html, re.S).group(1))
        qmm = re.search(r'id="bulk-cold-map">(.*?)</script>', html, re.S)
        if qmm:
            qm = json.loads(qmm.group(1))
            assert not (set(qm["rows"]) & set(pm["rows"])), \
                "qubit ids and pair ids are different rows"
        # the pair map's rows ARE the pair ids, never a qubit id
        assert all("-" in r for r in pm["rows"]), pm["rows"][:4]

    def test_the_route_serves_the_pair_grid_through_the_pair_macro(self, tmp_path):
        c = self._client(tmp_path)
        html = c.get("/bulk?vw=800", headers={"HX-Request": "true"}).get_data(as_text=True)
        pm = json.loads(re.search(r'id="bulk-pair-cold-map">(.*?)</script>', html, re.S).group(1))
        key = sorted(pm["cols"])[0]
        r = c.get(f"/bulk/cells?grid=pair&cols={quote(key)}")
        assert r.status_code == 200
        d = r.get_json()
        assert d["ok"] and d["grid"] == "pair"
        assert set(d["cells"][key]) == set(pm["rows"]), "one cell per PAIR row"
        # the pair macro's own marks, which the qubit macro never emits
        joined = " ".join(d["cells"][key].values())
        assert "bulk-cell" in joined

    def test_pair_hydration_is_byte_identical_to_the_hot_render(self, tmp_path):
        """docs/141 4ae (C5) -- the pair analogue of
        ``TestCellsRoute::test_hydration_is_byte_identical_to_the_hot_render``,
        the one property the whole design rests on.

        Nothing caught serving the pair branch through the QUBIT macro: the
        route's own pin asserted only that "bulk-cell" appears in the joined
        markup, a string ``qubit_cell`` emits too, and swapping the branch to
        ``macros.qubit_cell(cell, col)`` left 1,660 of 3,000 hydrated pair
        cells wrong on the real 20Q chip with the suite green (by kind:
        1,653 `missing` + 7 `list`; ZERO scalars -- hence _mixed_chip).
        """
        c = self._mixed_client(tmp_path)
        cold_html = c.get("/bulk?vw=800", headers={"HX-Request": "true"}).get_data(as_text=True)
        pm = self._pair_cold_map(cold_html)
        assert pm, "fixture: the narrow render must ship a pair cold map"
        cold_keys = sorted(pm["cols"])

        hot_html = c.get(f"/bulk?vw={bulk_virt.MAX_VIEWPORT_PX}",
                         headers={"HX-Request": "true"}).get_data(as_text=True)
        assert 'id="bulk-pair-cold-map"' not in hot_html, \
            "fixture: at the widest viewport the pair grid must render whole"
        hot = self._pair_grid_cells(hot_html)

        r = c.get("/bulk/cells?grid=pair&cols=" + quote(",".join(cold_keys)))
        assert r.status_code == 200, r.data[:200]
        d = r.get_json()
        assert d["ok"] and d["grid"] == "pair" and set(d["cells"]) == set(cold_keys)

        compared = 0
        for k in cold_keys:
            assert set(d["cells"][k]) == set(pm["rows"]), k
            for rid, inner in d["cells"][k].items():
                cls, hot_inner = hot[(rid, k)]
                assert "bulk-td-cold" not in cls, (rid, k)
                assert inner == hot_inner, (rid, k, inner[:160], hot_inner[:160])
                compared += 1
        assert compared > 1000, compared

        # ...and the fixture really did carry a cold cell of each kind, so the
        # comparison above was able to see the branches that differ. Without
        # this block the test is a scalar-only test wearing a stronger name.
        joined = "\n".join(inner for k in cold_keys for inner in d["cells"][k].values())
        assert "BulkPairEdit.openPair(" in joined, "fixture: no cold LIST cell"
        assert "bulk-cell-runtime" in joined, "fixture: no cold RUNTIME cell"
        assert 'placeholder="—"' in joined, "fixture: no cold MISSING cell"
        assert re.search(r'<input type="text" class="bulk-cell" ', joined), \
            "fixture: no cold plain SCALAR cell"

    def test_every_hydrated_pair_cell_names_its_own_row(self, tmp_path):
        """docs/141 4ae (C5), the absolute half. ``pair_cell``'s ``row_id`` is
        load-bearing on ``kind == 'list'``: the cell is read-only and the arrow
        beside it is the only way to edit that list, via
        ``BulkPairEdit.openPair(<row>)``. Byte-identity against the hot render
        cannot see a ``row_id`` that is wrong in BOTH halves (one shared
        ``row.id`` typo, or a render lambda closing over the wrong row) -- this
        can, because it names no other render as its authority.
        """
        c = self._mixed_client(tmp_path)
        cold_html = c.get("/bulk?vw=800", headers={"HX-Request": "true"}).get_data(as_text=True)
        pm = self._pair_cold_map(cold_html)
        assert pm
        d = c.get("/bulk/cells?grid=pair&cols="
                  + quote(",".join(sorted(pm["cols"])))).get_json()
        opened = 0
        for k, per_row in d["cells"].items():
            for rid, inner in per_row.items():
                for arg in re.findall(r"BulkPairEdit\.openPair\('([^']*)'\)", inner):
                    assert arg == rid, ("the arrow opens the WRONG pair", k, rid, arg)
                    opened += 1
                # every path the cell carries is that pair's own, never a neighbour's
                for dp in re.findall(r'data-dot-path="([^"]*)"', inner):
                    if dp:
                        assert dp.split(".")[:2] == ["qubit_pairs", rid], (k, rid, dp)
        assert opened >= len(pm["rows"]), (
            "not one hydrated cell offered the inspector arrow -- a LIST pair "
            "cell served through a macro that does not emit it", opened)

    def test_an_unknown_grid_is_refused_by_name(self, tmp_path):
        c = self._client(tmp_path)
        r = c.get("/bulk/cells?grid=banana&cols=x")
        assert r.status_code == 400 and "banana" in r.get_json()["error"]

    def test_no_grid_parameter_still_means_the_qubit_grid(self, tmp_path):
        """An older page's request must mean exactly what it always did."""
        c = self._client(tmp_path)
        html = c.get("/bulk?vw=800", headers={"HX-Request": "true"}).get_data(as_text=True)
        # a QUBIT column key, from the qubit table's own headers (this chip's
        # qubit grid is small enough to render whole, so there is no map)
        qtab = html[html.index('id="bulk-table"'):html.index('id="bulk-pair-table"')]
        key = re.search(r'<th scope="col" class="bulk-col-head[^"]*"\s+data-col-key="([^"]+)"', qtab).group(1)
        r = c.get(f"/bulk/cells?cols={quote(key)}")
        assert r.status_code == 200
        d = r.get_json()
        assert d["grid"] == "qubit", d
        rows = set(d["cells"][key])
        assert rows and all(not x.count("-") for x in rows), rows

    def test_a_pair_column_is_not_reachable_from_the_qubit_grid(self, tmp_path):
        """The two column namespaces are separate; asking the wrong grid for a
        key must be an honest 'no known column named', never someone else's."""
        c = self._client(tmp_path)
        html = c.get("/bulk?vw=800", headers={"HX-Request": "true"}).get_data(as_text=True)
        pm = json.loads(re.search(r'id="bulk-pair-cold-map">(.*?)</script>', html, re.S).group(1))
        pair_key = sorted(pm["cols"])[0]
        r = c.get(f"/bulk/cells?cols={quote(pair_key)}")     # no grid= -> qubit
        assert r.status_code == 400
        assert pair_key in (r.get_json().get("unknown") or [])

    def test_a_small_chip_is_left_alone(self, tmp_path):
        """The gate every small chip rides: below it the pair render is what it
        always was, cold map and all absent."""
        d = self._chip(tmp_path, n_pairs=1, n_macros=2)
        app = create_app(testing=True, instance_path=str(tmp_path / "_i"))
        c = app.test_client()
        c.post("/load", data={"folder": str(d)})
        html = c.get("/bulk?vw=800", headers={"HX-Request": "true"}).get_data(as_text=True)
        assert 'id="bulk-pair-cold-map"' not in html
        pair_block = html[html.index('id="bulk-pair-table"'):html.index("</table>", html.index('id="bulk-pair-table"'))]
        assert "bulk-td-cold" not in pair_block

    def test_the_pair_grid_is_memoized_and_invalidated_by_an_edit(self, tmp_path):
        """A hydration a moment after the render must read the very dicts the
        page rendered from -- and must NOT after a mutation."""
        from quam_state_manager.web import routes as R

        d = self._chip(tmp_path)
        app = create_app(testing=True, instance_path=str(tmp_path / "_i"))
        c = app.test_client()
        c.post("/load", data={"folder": str(d)})
        calls = []
        real = R._pair_bulk_grid

        def counted(store, modified):
            calls.append(1)
            return real(store, modified)

        R._pair_bulk_grid = counted
        try:
            c.get("/bulk?vw=800", headers={"HX-Request": "true"})
            n_after_render = len(calls)
            c.get("/bulk/cells?grid=pair&cols=" + quote("__none__"))
            assert len(calls) == n_after_render, "the hydration reused the memo"
            html = c.get("/bulk?vw=800", headers={"HX-Request": "true"}).get_data(as_text=True)
            pm = json.loads(re.search(r'id="bulk-pair-cold-map">(.*?)</script>', html, re.S).group(1))
            key = sorted(pm["cols"])[0]
            path = pm["cols"][key][0][1]
            before = len(calls)
            ed = c.post("/field/edit", data={"dot_path": path, "value": "0.5"},
                        headers={"HX-Request": "true"})
            assert ed.status_code in (200, 204), (ed.status_code, ed.get_data(as_text=True)[:200])
            c.get(f"/bulk/cells?grid=pair&cols={quote(key)}")
            assert len(calls) > before, "an edit must invalidate the memo"
        finally:
            R._pair_bulk_grid = real


class TestPairChipToken:
    """docs/141 4ae (C13). 4ac fixed a CRITICAL -- ``/bulk/cells`` compared the
    folder BASENAME, so a chip and its backup hydrated each other -- by giving
    the page a token (``<name>#<8 hex of the path>``). The QUBIT grid's token
    rides ``QMETA.chipKey`` and ``TestChipGateIdentity`` pins it. The PAIR
    grid's rides a SECOND publication, ``window.__bulkChipKey``, which
    ``pair-edit.js``'s ``urlParams`` reads -- and nothing pinned that one:
    changing it to the display name was green across the whole suite.

    The server still accepts the bare display name on purpose (an older page
    must be able to hydrate itself -- ``test_the_display_name_is_still_accepted``
    above), so the CLIENT is the only thing standing between the fix and the
    CRITICAL's return.
    """

    @staticmethod
    def _chip(root, name, bump):
        """A pair-carrying chip at ``<root>/<name>/CHIP`` -- the display name
        is "CHIP" for every value of ``name``, which is the whole trap."""
        d = root / name / "CHIP"
        d.mkdir(parents=True)
        (d / "state.json").write_text(json.dumps({
            "qubits": {"q0": {"T1": 1e-5 + bump}, "q1": {"T1": 2e-5 + bump}},
            "qubit_pairs": {"q0-q1": {"qubit_control": "#/qubits/q0",
                                      "qubit_target": "#/qubits/q1",
                                      "gate_fidelity": {"averaged": 0.99 + bump}}},
            "active_qubit_names": ["q0", "q1"],
            "active_qubit_pair_names": ["q0-q1"]}), encoding="utf-8")
        (d / "wiring.json").write_text(json.dumps(
            {"wiring": {}, "network": {"host": "1.1.1.1"}}), encoding="utf-8")
        return d

    @staticmethod
    def _pair_token(html):
        """What ``pair-edit.js`` will read, parsed the way the browser does."""
        m = re.search(r"window\.__bulkChipKey\s*=\s*(.+?);", html)
        if not m:
            return None
        try:
            return json.loads(m.group(1))
        except ValueError:
            return None

    @staticmethod
    def _pair_col_key(html):
        """A real derived pair column, from the pair table's own headers
        (`__id__` is the row-label column, not one `/bulk/cells` serves)."""
        i = html.index('id="bulk-pair-table"')
        for k in re.findall(r'data-col-key="([^"]+)"', html[i:]):
            if not k.startswith("__"):
                return k
        raise AssertionError("fixture: the pair grid derived no columns")

    def test_the_pair_grid_publishes_the_token_not_the_display_name(self, tmp_path):
        a = self._chip(tmp_path, "labA", 0.0)
        b = self._chip(tmp_path, "backup", 7.0)
        assert a.name == b.name, "fixture: one basename, two parents"

        app = create_app(testing=True, instance_path=str(tmp_path / "_i"))
        c = app.test_client()
        assert c.post("/load", data={"folder": str(a)}).status_code in (200, 302)
        html = c.get("/bulk", headers={"HX-Request": "true"}).get_data(as_text=True)
        tok_a = self._pair_token(html)
        assert tok_a, "pair-edit.js reads window.__bulkChipKey -- the page must set it"
        assert tok_a != a.name, (
            "the pair grid published the DISPLAY NAME %r: a chip and its backup "
            "share it, which is exactly the identity the 4ac gate cannot tell "
            "apart" % (tok_a,))
        key = self._pair_col_key(html)
        assert c.get(f"/bulk/cells?grid=pair&cols={quote(key)}&chip={quote(tok_a)}"
                     ).status_code == 200, "A's own token must hydrate A"

        # the SAME context now holds the other chip
        assert c.post("/load", data={"folder": str(b)}).status_code in (200, 302)
        r = c.get(f"/bulk/cells?grid=pair&cols={quote(key)}&chip={quote(tok_a)}")
        assert r.status_code == 409, (
            "chip B served pair cells to a page rendered from A: "
            f"{r.status_code} {r.get_data(as_text=True)[:200]}")

        html_b = c.get("/bulk", headers={"HX-Request": "true"}).get_data(as_text=True)
        tok_b = self._pair_token(html_b)
        assert tok_b and tok_b != tok_a, "the two tokens differ"
        assert c.get(f"/bulk/cells?grid=pair&cols={quote(key)}&chip={quote(tok_b)}"
                     ).status_code == 200, "the gate is not simply always-on"

    def test_the_pair_binding_sends_that_token_as_chip(self):
        """The two halves of the publication are only useful together: the
        template sets ``window.__bulkChipKey``, ``pair-edit.js`` puts it on the
        wire. Either half alone leaves the pair grid ungated."""
        js = (Path(__file__).resolve().parent.parent
              / "quam_state_manager/web/static/pair-edit.js").read_text(encoding="utf-8")
        i = js.index("urlParams: function ()")
        block = js[i:i + 400]
        assert "window.__bulkChipKey" in block, block[:200]
        assert "&chip=' + encodeURIComponent(tok)" in block, block[:200]
        assert "&grid=pair" in block, block[:200]


@pytest.mark.skipif(shutil.which("node") is None, reason="node not available")
def test_pair_virt_server_selfcheck():
    """docs/141 4ae (C12). The PAIR half of the client, against the REAL
    pair-edit.js + grid-virt.js under jsdom.

    ``tests/pair_virt_server_selfcheck.cjs`` shipped with no pytest driver, so
    ``pytest tests/`` never executed one of its 39 asserts -- the harness was
    green only because nothing ran it. Same shape as
    ``test_bulk_virt_server_selfcheck`` above.
    """
    node = shutil.which("node")
    root = Path(__file__).resolve().parent.parent
    try:
        subprocess.run([node, "-e", "require('jsdom')"], check=True, capture_output=True, timeout=30)
    except Exception:
        pytest.skip("jsdom not installed")
    r = subprocess.run([node, str(root / "tests" / "pair_virt_server_selfcheck.cjs")],
                       capture_output=True, text=True, encoding="utf-8", timeout=180, cwd=str(root))
    if r.returncode == 2:
        pytest.skip("jsdom not installed")
    assert r.returncode == 0, r.stdout + r.stderr
    assert r.stdout.count("ok - ") >= 36, r.stdout
