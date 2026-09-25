"""Datasets > Trends routes (design ram_design.md §2b, P3).

/trends/data is a light shell (no <img>, no table, no inline data);
/trends/series is gzipped JSON with an ETag that changes exactly when the
data does; /trends/param-diff shows only differing rows over the newest 20
runs with exact counts and an "all" option; /debug/ram reports the memos.
Every existing behaviour of the old fragment is kept: no-dataset / no-
experiment messages, the cross-chip refusal, the qubit filter, the folder
selection.
"""
from __future__ import annotations

import gzip
import json
import re
from pathlib import Path

import pytest

from quam_state_manager.core import trend_index as ti
from quam_state_manager.web import routes as routes_mod
from quam_state_manager.web.app import create_app

A = "05_rabi"


def _run(root: Path, rid: int, exp: str = A, *, date: str = "2026-09-01",
         qubits=("q1", "q2"), shots=100, extra_param=None, amp=None, freq=None) -> Path:
    run = root / date / f"#{rid}_{exp}_{rid % 24:02d}{rid % 60:02d}00"
    run.mkdir(parents=True, exist_ok=True)
    params = {"qubits": list(qubits), "shots": shots, "span": 3}
    if extra_param is not None:
        params["gain"] = extra_param
    if freq is not None:
        params["freq"] = freq
    (run / "node.json").write_text(json.dumps({
        "metadata": {"name": exp, "status": "successful"},
        "data": {"parameters": {"model": params}, "outcomes": {}},
        "id": rid, "parents": []}), encoding="utf-8")
    fit = {q: {"amp": (amp if amp is not None else 0.01 * rid) + i} for i, q in enumerate(qubits)}
    (run / "data.json").write_text(json.dumps({"fit_results": fit, "figure": "./figure.png"}),
                                   encoding="utf-8")
    return run


@pytest.fixture(autouse=True)
def _fresh():
    for m in (ti.INDEX_MEMO, ti.SERIES_MEMO, ti.PARAMS_MEMO, routes_mod._TRENDS_SAME_CHIP):
        m.clear()
    yield


@pytest.fixture
def app_client(tmp_path):
    data = tmp_path / "data"
    for rid in range(1, 31):
        _run(data, rid, shots=100 if rid <= 25 else 200, extra_param=1.0 if rid != 7 else 1,
             # 1e-13 relative: the same value under differ's tolerance; 6e9 is not
             freq=5e9 * (1 + 1e-13) if rid == 12 else 6e9 if rid == 28 else 5e9)
    app = create_app(testing=True, instance_path=str(tmp_path / "_inst"))
    c = app.test_client()
    assert c.post("/workspace/add", data={"folder": str(data)}).status_code in (200, 302)
    return c, data, routes_mod._folder_key(data)


HX = {"HX-Request": "true"}
GZ = {"Accept-Encoding": "gzip"}


def _series(c, url, headers=None):
    r = c.get(url, headers=GZ if headers is None else headers)
    body = gzip.decompress(r.data) if r.headers.get("Content-Encoding") == "gzip" else r.data
    return r, (json.loads(body) if r.status_code == 200 else None)


class TestShell:
    def test_the_shell_is_light_and_carries_the_data_urls(self, app_client):
        c, _data, key = app_client
        r = c.get(f"/trends/data?experiment={A}", headers=HX)
        html = r.get_data(as_text=True)
        assert r.status_code == 200
        live = re.sub(r"<template\b.*?</template>", "", html, flags=re.S)
        assert "<img" not in live and "<td" not in live and "<table" not in live
        assert "var trend =" not in html and "fit_results" not in html
        assert len(html.encode()) < 6000, len(html.encode())
        m = re.search(r'data-series-url="([^"]+)"', html)
        assert m and f"folders={key}" in m.group(1) and f"experiment={A}" in m.group(1)
        assert 'data-params-url="/trends/param-diff?' in html
        # the figure timeline is a closed <details>, the strip item a <template>
        assert re.search(r'<details class="trends-section trends-figtl"[^>]*hidden', html)
        assert "toggleFigureZoom(this)" in html

    def test_messages_kept(self, app_client, tmp_path):
        c, _data, _key = app_client
        r = c.get("/trends/data", headers=HX)
        assert "Select an experiment type" in r.get_data(as_text=True)
        empty = create_app(testing=True, instance_path=str(tmp_path / "_i2")).test_client()
        r = empty.get(f"/trends/data?experiment={A}", headers=HX)
        assert "No dataset loaded" in r.get_data(as_text=True)
        r = empty.get(f"/trends/series?experiment={A}")
        assert r.status_code == 404

    def test_a_cross_chip_selection_is_refused_everywhere(self, app_client, tmp_path):
        c, _data, key = app_client
        other = tmp_path / "other"
        _run(other, 1, qubits=("q7",))
        c.post("/workspace/add", data={"folder": str(other)})
        both = f"{key},{routes_mod._folder_key(other)}"
        html = c.get(f"/trends/data?experiment={A}&folders={both}", headers=HX).get_data(as_text=True)
        assert "trendUseSingleFolder" in html and "trends-view" not in html
        assert c.get(f"/trends/series?experiment={A}&folders={both}").status_code == 409
        assert "different chips" in c.get(f"/trends/param-diff?experiment={A}&folders={both}",
                                          headers=HX).get_data(as_text=True)

    def test_the_data_requests_never_walk_the_candidate_folders(self, app_client, monkeypatch):
        """The validated candidate list costs ~100 ms on the 4,121-run archive;
        the picker page already did it. The three Trends requests use the
        cached list."""
        c, _data, key = app_client
        real = routes_mod._dataset_candidate_folders
        calls = []
        monkeypatch.setattr(routes_mod, "_dataset_candidate_folders",
                            lambda *, fast=False: calls.append(fast) or real(fast=fast))
        c.get(f"/trends/data?experiment={A}&folders={key}", headers=HX)
        c.get(f"/trends/series?experiment={A}&folders={key}")
        c.get(f"/trends/param-diff?experiment={A}&folders={key}", headers=HX)
        assert calls and all(calls), calls


class TestSeries:
    def test_gzip_json_and_its_etag(self, app_client):
        c, _data, key = app_client
        url = f"/trends/series?experiment={A}&folders={key}"
        r, p = _series(c, url)
        assert r.headers["Content-Encoding"] == "gzip" and r.headers["Content-Type"] == "application/json"
        assert r.headers["ETag"] == f'"{p["v"]}"' and r.headers["Cache-Control"] == "no-cache"
        assert p["n_runs"] == 30 and len(p["series"]) == 2
        r2, p2 = _series(c, url, headers={})
        assert "Content-Encoding" not in r2.headers and p2 == p
        r3 = c.get(url, headers={"If-None-Match": r.headers["ETag"]})
        assert r3.status_code == 304 and r3.data == b""

    def test_a_new_run_changes_the_etag_and_is_in_the_answer(self, app_client):
        c, data, key = app_client
        url = f"/trends/series?experiment={A}&folders={key}"
        r, p = _series(c, url)
        _run(data, 31, date="2026-09-02")
        r2 = c.get(url, headers={**GZ, "If-None-Match": r.headers["ETag"]})
        assert r2.status_code == 200, "a stale ETag was confirmed after a new run"
        p2 = json.loads(gzip.decompress(r2.data))
        assert p2["n_runs"] == 31 and p2["runs"][-1][0] == 31 and p2["v"] != p["v"]

    def test_the_qubit_filter(self, app_client, tmp_path):
        c, data, key = app_client
        _run(data, 40, qubits=("q3",))
        _r, p = _series(c, f"/trends/series?experiment={A}&folders={key}&qubit=q3")
        assert [x[0] for x in p["runs"]] == [40] and [s["q"] for s in p["series"]] == ["q3"]

    def test_warming_is_a_202_never_an_old_answer(self, app_client, monkeypatch):
        c, _data, key = app_client
        from quam_state_manager.core import ramcache

        def busy(*a, **kw):
            raise ramcache.Warming("trends.series", "slot", 0.25)

        monkeypatch.setattr(ti, "series_blob", busy)
        r = c.get(f"/trends/series?experiment={A}&folders={key}")
        assert r.status_code == 202 and r.get_json() == {"warming": True}


class TestParamDiff:
    def test_only_differing_rows_over_the_newest_20_with_exact_counts(self, app_client):
        c, _data, key = app_client
        html = c.get(f"/trends/param-diff?experiment={A}&folders={key}", headers=HX).get_data(as_text=True)
        text = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html))
        # newest 20 = runs 11..30: shots differs (100 -> 200 at 26); gain is
        # 1.0 everywhere in the window (run 7's int 1 is outside it);
        # qubits, span identical -> 3 identical rows
        assert "showing 20 of 30 runs · 3 identical rows hidden" in text
        heads = re.findall(r"<th title=\"#(\d+) ", html)
        assert heads == [str(i) for i in range(11, 31)], heads
        assert "<code>shots</code>" in html and "<code>span</code>" not in html
        assert 'hx-get="/trends/param-diff?experiment=' in html and "window=all" in html
        assert "show all 30 runs" in text

    def test_all_runs_on_request(self, app_client):
        c, _data, key = app_client
        html = c.get(f"/trends/param-diff?experiment={A}&folders={key}&window=all",
                     headers=HX).get_data(as_text=True)
        text = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html))
        assert "showing all 30 runs · 3 identical rows hidden" in text
        assert len(re.findall(r"<th title=\"#", html)) == 30
        assert "newest 20 only" in text

    def test_a_cell_is_highlighted_by_the_one_comparison_rule(self, app_client):
        """docs/118: a row's verdict and its cells use differ.compare_equal --
        1 and 1.0 are the same value (run 7's int gain is no difference), and
        so are two floats within the comparison tolerance: in a row that DOES
        differ, such a cell is not highlighted (a plain != would light it)."""
        c, data, key = app_client
        html = c.get(f"/trends/param-diff?experiment={A}&folders={key}&window=all",
                     headers=HX).get_data(as_text=True)
        assert "<code>gain</code>" not in html
        row = re.search(r"<code>freq</code>(.*?)</tr>", html, re.S).group(1)
        lit = [i + 1 for i, x in enumerate(re.findall(r"<td( class=\"cell-diff\")?>", row)) if x]
        assert lit == [28], lit
        row = re.search(r"<code>shots</code>(.*?)</tr>", html, re.S).group(1)
        cells = re.findall(r"<td( class=\"cell-diff\")?>", row)
        assert [bool(x) for x in cells] == [False] * 25 + [True] * 5

    def test_the_fragment_carries_the_series_version(self, app_client):
        c, _data, key = app_client
        _r, p = _series(c, f"/trends/series?experiment={A}&folders={key}")
        html = c.get(f"/trends/param-diff?experiment={A}&folders={key}", headers=HX).get_data(as_text=True)
        assert f'data-trends-v="{p["v"]}"' in html


def test_debug_ram_reports_the_trend_memos(app_client):
    c, _data, key = app_client
    _series(c, f"/trends/series?experiment={A}&folders={key}")
    snap = c.get("/debug/ram").get_json()
    names = {m["name"]: m for m in snap["memos"]}
    assert names["trends.series"]["entries"] >= 1 and names["trends.index"]["entries"] >= 1
    assert snap["total_bytes"] == snap["entry_bytes_sum"]
    assert snap["budget_bytes"] == 384 * 1024 * 1024


# ---------------------------------------------------------------------------
# the same-chip memo: its two key components are not vacuous
# ---------------------------------------------------------------------------

def _stores(tmp_path, spec):
    from quam_state_manager.core.dataset import DatasetStore
    out = []
    for name, qubits in spec:
        _run(tmp_path / name, 1, qubits=qubits)
        out.append({"key": name, "store": DatasetStore(tmp_path / name)})
    return out


def _chip_gens_scenario(tmp_path) -> bool:
    a, b = _stores(tmp_path, [("a", ("q1",)), ("b", ("q1",))])
    assert routes_mod._trends_same_chip([a, b]) == "same"
    _run(tmp_path / "b", 2, qubits=("q9",))
    b["store"]._last_mtime = (0.0, -2)
    b["store"].rescan_if_stale()
    return routes_mod._trends_same_chip([a, b]) != routes_mod._folders_same_chip([a, b])


def _chip_stores_scenario(tmp_path) -> bool:
    a, b, c = _stores(tmp_path, [("a", ("q1",)), ("b", ("q1",)), ("c", ("q2",))])
    assert routes_mod._trends_same_chip([a, b]) == "same"
    return routes_mod._trends_same_chip([a, c]) != routes_mod._folders_same_chip([a, c])


@pytest.mark.parametrize("component,scenario", [("chip_gens", _chip_gens_scenario),
                                                ("chip_stores", _chip_stores_scenario)])
def test_same_chip_memo_key_sweep(component, scenario, tmp_path, monkeypatch):
    assert scenario(tmp_path / "real") is False
    routes_mod._TRENDS_SAME_CHIP.clear()
    real = ti._key
    monkeypatch.setattr(ti, "_key", lambda **kw: real(**{k: v for k, v in kw.items() if k != component}))
    assert scenario(tmp_path / "dropped") is True
