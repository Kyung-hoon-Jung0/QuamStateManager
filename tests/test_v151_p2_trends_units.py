"""v1.5.1 T1-F7: a Datasets > Trends y axis says its unit.

The t1 chart read "15k / 10k" under the title "t1" (the fit stores it in ns).
SM never guesses a unit (core/units.py), but the run's OWN fit file declares
it: qualibration keeps the fit result ``t1`` as the ds_fit variable ``tau``
with ``long_name="T1"``, ``units="ns"``. ``/trends/units`` reads that from the
newest runs of the selection; the chart title and label become "t1 (ns)" and
the numbers follow ``PlotTheme.axisNumberFormat``. Client side pinned in
``tests/v151_trends_units_selfcheck.cjs`` on the REAL shell/series/units.
"""

from __future__ import annotations

import gzip
import json
import shutil
import subprocess
from pathlib import Path

import pytest

h5py = pytest.importorskip("h5py")
np = pytest.importorskip("numpy")

from quam_state_manager.core import trend_index as ti  # noqa: E402
from quam_state_manager.core.units import fit_metric_units  # noqa: E402
from quam_state_manager.web import routes as routes_mod  # noqa: E402
from quam_state_manager.web.app import create_app  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
EXP = "25_T1"


def _run(root: Path, rid: int, *, with_fit: bool = True) -> None:
    run = root / "2026-10-14" / f"#{rid}_{EXP}_0{rid % 10}1013"
    run.mkdir(parents=True)
    (run / "node.json").write_text(json.dumps({
        "id": rid, "created_at": f"2026-10-14T0{rid % 10}:10:13+09:00",
        "metadata": {"name": EXP, "status": "finished"},
        "data": {"parameters": {"model": {"qubits": ["q1", "q2"]}}, "outcomes": {}}}), encoding="utf-8")
    fit = {q: {"t1": 12000.0 + 100 * rid + i, "t1_error": 400.0 + i, "amp": 0.1 * rid, "success": True}
           for i, q in enumerate(("q1", "q2"))}
    (run / "data.json").write_text(json.dumps({"fit_results": fit}), encoding="utf-8")
    if with_fit:
        with h5py.File(run / "ds_fit.h5", "w") as f:
            tau = f.create_dataset("tau", data=np.array([12000.0, 13000.0]))
            tau.attrs["long_name"] = "T1"
            tau.attrs["units"] = "ns"
            err = f.create_dataset("tau_error", data=np.array([400.0, 401.0]))
            err.attrs["long_name"] = "T1 error"
            err.attrs["units"] = "ns"
            f.create_dataset("amp", data=np.array([0.1, 0.2]))      # no unit declared


@pytest.fixture(autouse=True)
def _fresh():
    for m in (ti.INDEX_MEMO, ti.SERIES_MEMO, ti.PARAMS_MEMO, routes_mod._TRENDS_SAME_CHIP):
        m.clear()
    routes_mod._TRENDS_FIT_UNITS.clear()
    yield


@pytest.fixture
def client(tmp_path):
    data = tmp_path / "data"
    for rid in (1, 2, 3):
        _run(data, rid)
    _run(data, 4, with_fit=False)
    app = create_app(testing=True, instance_path=str(tmp_path / "_inst"))
    c = app.test_client()
    assert c.post("/workspace/add", data={"folder": str(data)}).status_code in (200, 302)
    return c


def test_fit_metric_units_reads_the_name_or_the_long_name_and_never_guesses():
    vs = [{"name": "tau", "long_name": "T1", "units": "ns"},
          {"name": "tau_error", "long_name": "T1 error", "units": "ns"},
          {"name": "freq", "long_name": "detuning", "units": "Hz"},
          {"name": "x", "long_name": "Detuning", "units": "MHz"},     # a clash: two units
          {"name": "amp", "long_name": None, "units": None}]
    got = fit_metric_units(vs, ["t1", "t1_error", "detuning", "amp", "tau", "unknown"])
    assert got == {"t1": "ns", "t1_error": "ns", "tau": "ns"}, got


def test_the_units_route_answers_from_the_runs_own_fit_files(client):
    j = client.get(f"/trends/units?experiment={EXP}").get_json()
    assert j == {"units": {"t1": "ns", "t1_error": "ns"}}, j


@pytest.mark.skipif(shutil.which("node") is None, reason="node not available")
def test_the_trends_axis_carries_the_unit(client, tmp_path):
    shell = client.get(f"/trends/data?experiment={EXP}", headers={"HX-Request": "true"}).get_data(as_text=True)
    assert 'id="trends-view"' in shell
    r = client.get(f"/trends/series?experiment={EXP}", headers={"Accept-Encoding": "gzip"})
    body = gzip.decompress(r.data) if r.headers.get("Content-Encoding") == "gzip" else r.data
    series = json.loads(body)
    units = client.get(f"/trends/units?experiment={EXP}").get_json()
    fx = tmp_path / "fx.json"
    fx.write_text(json.dumps({"shell": shell, "series": series, "units": units, "bare_metric": "amp"}),
                  encoding="utf-8")
    p = subprocess.run(["node", str(ROOT / "tests" / "v151_trends_units_selfcheck.cjs"), str(fx)],
                       capture_output=True, text=True, encoding="utf-8", cwd=str(ROOT), timeout=120)
    if p.returncode == 2 and "jsdom not installed" in (p.stderr or ""):
        pytest.skip("jsdom not installed")
    assert p.returncode == 0, f"stdout:\n{p.stdout}\nstderr:\n{p.stderr}"
