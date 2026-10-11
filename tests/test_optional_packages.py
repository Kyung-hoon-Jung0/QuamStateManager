"""v1.3.0 B3: SM keeps serving when a package it can live without cannot be imported.

scipy, h5py and tzdata are declared dependencies, but a partial or broken env
(a DLL that fails to load, an install made with --no-deps) leaves them
unimportable, and the code already guards them in most places. A clean-venv
walk with each one blocked found the places that did not: the Pulses page and
Live State Edit (a filtered pulse's preview), the z-line view, the N-D data
viewer, and every read of the change history (a time-zone lookup without
tzdata). Each case runs a fresh interpreter in which the package is
unimportable from the start, opens a small chip, and asks those routes.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from tests.test_physical_units import _QC, _state, _write_chip

_ROOT = Path(__file__).resolve().parent.parent

_PROBE = r'''
import json, sys
for name in [n for n in sys.argv[1].split(",") if n]:
    for key in [k for k in list(sys.modules) if k == name or k.startswith(name + ".")]:
        del sys.modules[key]
    sys.modules[name] = None
if "tzdata" in sys.argv[1].split(","):
    import zoneinfo   # a venv Python on Windows has no system zone database either
    zoneinfo.reset_tzpath(to=[])
chip, data, inst = sys.argv[2], sys.argv[3], sys.argv[4]
routes, posts = json.loads(sys.argv[5]), json.loads(sys.argv[6])
from quam_state_manager.web.app import create_app
app = create_app(testing=True, instance_path=inst)
c = app.test_client()
assert c.post("/load", data={"folder": chip}).status_code in (200, 302)
for path, form in posts:
    assert c.post(path, data=form).status_code == 200, path
uid = ""
if data != "-":
    c.post("/workspace/add", data={"folder": data})
    rows = json.loads(c.get("/datasets/changes-since?ts=0").get_data(as_text=True))["updated"]
    uid = "%s:%s" % (rows[0]["f"], rows[0]["id"])
out = {}
for r in routes:
    resp = c.get(r.replace("{uid}", uid))
    out[r] = [resp.status_code, resp.get_data(as_text=True)]
sys.stdout.write(json.dumps(out))
'''


def _chip(tmp_path: Path) -> Path:
    st = _state()
    # a shape whose preview needs scipy (gaussian_filter1d), on the flux line
    st["qubits"]["qA1"]["z"]["operations"]["gf"] = {
        "__class__": _QC + "GaussianFilteredSquarePulse", "amplitude": 0.05, "length": 120,
        "pulse_length": 100, "post_zero_padding_length": 20, "gaussian_filter_frequency_mhz": 50.0}
    folder = tmp_path / "chip"
    _write_chip(folder, st)
    return folder


def _data(tmp_path: Path) -> Path:
    h5py = pytest.importorskip("h5py")
    import numpy as np
    root = tmp_path / "data"
    run = root / "2026-10-10" / "#1_ramsey_120000"
    run.mkdir(parents=True)
    (run / "node.json").write_text(json.dumps({
        "metadata": {"name": "ramsey", "status": "successful",
                     "run_start": "2026-10-10T12:00:00", "run_end": "2026-10-10T12:00:01"},
        "data": {"parameters": {"model": {"qubits": ["qA1"]}}, "outcomes": {}},
        "id": 1, "parents": [], "created_at": "2026-10-10T12:00:00"}), encoding="utf-8")
    (run / "data.json").write_text(json.dumps({"fit_results": {}}), encoding="utf-8")
    with h5py.File(run / "ds_raw.h5", "w") as f:
        f.create_dataset("I", data=np.arange(12.0))
    return root


def _probe(tmp_path: Path, blocked: str, routes: list[str], *, data: Path | None = None,
           posts: list | None = None) -> dict:
    # the conftest isolation, for a child process: no real ~/.qualibrate, no real instance
    env = dict(os.environ, PYTHONPATH=str(_ROOT), PYTHONUTF8="1", USERPROFILE=str(tmp_path / "home"),
               HOME=str(tmp_path / "home"), LOCALAPPDATA=str(tmp_path / "appdata"),
               QUALIBRATE_CONFIG_FILE=str(tmp_path / "no-qualibrate" / "config.toml"))
    r = subprocess.run([sys.executable, "-c", _PROBE, blocked, str(_chip(tmp_path)),
                        str(data) if data else "-", str(tmp_path / "inst"), json.dumps(routes),
                        json.dumps(posts or [])],
                       capture_output=True, text=True, encoding="utf-8", env=env, cwd=str(tmp_path),
                       timeout=600)
    assert r.returncode == 0, r.stderr[-2000:]
    return json.loads(r.stdout)


def _outcome(fn, *args):
    """The return value, or the exception -- so a raise fails an assertion, not the test runner."""
    try:
        return fn(*args)
    except Exception as exc:  # noqa: BLE001
        return exc


class TestWithoutScipy:
    def test_pulse_pages_and_the_zline_view_answer(self, tmp_path):
        out = _probe(tmp_path, "scipy", ["/pulses", "/bulk", "/zline", "/zline/data?line=qubits.qA1.z"])
        assert out["/pulses"][0] == 200 and out["/bulk"][0] == 200
        status, html = out["/zline"]
        assert status == 200 and "needs scipy" in html
        status, body = out["/zline/data?line=qubits.qA1.z"]
        assert status == 503 and "needs scipy" in json.loads(body)["error"]

    def test_a_filtered_pulse_preview_says_why(self):
        code = ("import sys, json\nsys.modules['scipy'] = None\nsys.modules['scipy.ndimage'] = None\n"
                "from quam_state_manager.core.waveform_synth import synthesize\n"
                "p = synthesize('GaussianFilteredSquarePulse', {'pulse_length': 100, "
                "'post_zero_padding_length': 20, 'amplitude': 0.05, 'gaussian_filter_frequency_mhz': 50.0})\n"
                "sys.stdout.write(json.dumps([p['ok'], p['error']]))\n")
        r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                           env=dict(os.environ, PYTHONPATH=str(_ROOT)), timeout=300)
        assert r.returncode == 0, r.stderr[-1500:]
        ok, error = json.loads(r.stdout)
        assert ok is False and "needs scipy" in error


class TestWithoutH5py:
    def test_the_nd_viewer_answers_with_the_reason(self, tmp_path):
        data = _data(tmp_path)
        out = _probe(tmp_path, "h5py", ["/dataset/{uid}/ndview", "/dataset/{uid}/ndview/data?which=ds_raw.h5&var=I"],
                     data=data)
        status, html = out["/dataset/{uid}/ndview"]
        assert status == 200 and "needs h5py" in html
        status, body = out["/dataset/{uid}/ndview/data?which=ds_raw.h5&var=I"]
        cube = json.loads(body)
        assert status == 200 and cube["ok"] is False and "h5py" in cube["error"]


class TestWithoutTzdata:
    def test_the_history_reads_in_utc_and_this_pcs_zone(self, tmp_path):
        # an SM write puts one event in the chip's ledger; reading it back needs a zone
        posts = [["/field/edit", {"dot_path": "qubits.qA1.f_01", "value": "5.1e9"}],
                 ["/state/apply-to-live", {}]]
        routes = ["/field/history?path=qubits.qA1.f_01", "/state-history", "/journal"]
        out = _probe(tmp_path, "tzdata", routes, posts=posts)
        for route in routes:
            status, html = out[route]
            assert status == 200, route
            assert "could not be read" not in html, route
        assert "5,100,000,000" in out[routes[0]][1]

    def test_the_log_day_and_a_story_clock_need_no_zone_database(self, monkeypatch):
        import zoneinfo

        from quam_state_manager.core import story
        from quam_state_manager.web import journal_routes, routes

        def no_zone(name):
            raise zoneinfo.ZoneInfoNotFoundError(name)
        monkeypatch.setattr(zoneinfo, "ZoneInfo", no_zone)          # as without tzdata
        monkeypatch.setattr(routes, "_display_zone", lambda: {"zone": "Europe/Paris"})
        day, clock = _outcome(journal_routes._today), _outcome(story._hub_clock, 1_790_000_000_000_000, object())
        assert isinstance(day, str) and len(day) == 10, day          # this PC's day
        assert clock == "14:13:20", clock                            # UTC, exactly

    def test_tzinfo_for_never_raises(self, monkeypatch):
        from datetime import timezone

        from quam_state_manager.core import project_time
        monkeypatch.setattr(project_time, "_zoneinfo", lambda name: None)   # as without tzdata
        assert project_time.tzinfo_for("UTC") is timezone.utc
        assert project_time.tzinfo_for("Europe/Paris") is None             # = this PC's zone
        assert project_time.tzinfo_for("") is None
