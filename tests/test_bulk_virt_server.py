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
        i = js.index("var _tok = QMETA && (QMETA.chipKey || QMETA.chip);")
        assert "&chip=' + encodeURIComponent(_tok)" in js[i:i + 300]


class TestWidthMetricsMirror:
    """docs/141 4ac. `core/bulk_virt.py` says its numbers are the client's,
    mirrored. Nothing checked that, and every plan test derives its expected
    edge from those same constants, so all four could be changed with the
    suite green -- while the plan they produce decides how much of a real
    chip arrives empty."""

    @staticmethod
    def _js():
        return (Path(__file__).resolve().parent.parent
                / "quam_state_manager/web/static/bulk-edit.js").read_text(encoding="utf-8")

    def test_the_width_metrics_mirror_the_client(self):
        from quam_state_manager.core import bulk_virt

        js = self._js()
        m = re.search(r"_VIRT_EST_PAD\s*=\s*([\d.]+)", js)
        assert m, "the client's cell padding constant"
        assert bulk_virt.PAD_PX == float(m.group(1)), \
            f"PAD_PX {bulk_virt.PAD_PX} != the client's {m.group(1)}"

        # the header-label estimate: `<label length> * A + B`
        lab = re.search(r"\.length\s*\*\s*([\d.]+)\s*\+\s*([\d.]+)", js)
        assert lab, "the client's label width estimate"
        assert bulk_virt.LABEL_PX_PER_CHAR == float(lab.group(1))
        assert bulk_virt.LABEL_PAD_PX == float(lab.group(2))

        buf = re.search(r"_VIRT_BUFFER\s*=\s*([\d.]+)", js)
        assert buf and bulk_virt.BUFFER == float(buf.group(1))
        cells = re.search(r"_VIRT_MIN_CELLS\s*=\s*(\d+)", js)
        assert cells and bulk_virt.MIN_CELLS == int(cells.group(1))
        cold = re.search(r"_VIRT_MIN_COLD\s*=\s*(\d+)", js)
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
        js = self._js()
        i = js.index("var vw = ")
        assert "window.screen.availWidth" in js[i:i + 200], js[i:i + 120]
        # and the planner's own edge reads the same quantity
        j = js.index("var edge = ")
        assert "window.screen.availWidth" in js[j:j + 200], js[j:j + 120]
        assert "innerWidth" not in js[i:i + 200] and "innerWidth" not in js[j:j + 200]
