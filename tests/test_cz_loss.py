"""CZ gate plan: measured population loss joined to the chip (A14 S1-S5).

core/cz_loss.py reads the lab's population-loss runs from the chip's own data folders
(file-derived, never the ledger), splits each non-monotonic frequency axis into pieces,
names the run the stored defect list came from by CONTENT, and checks the idle the lab
used. /cz-plan/loss serves it; /cz-plan/data tags each note with its figure and carries
the [derived] gate loss block. Synthetic runs are written with h5py the way the lab's
node saves them (ds_fit.h5 + data.json + node.json + quam_state).
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

import numpy as np
import pytest

h5py = pytest.importorskip("h5py")

from quam_state_manager.core import cz_loss as L  # noqa: E402
from quam_state_manager.web.app import create_app  # noqa: E402

MHZ = 1e6
FAMILY = "25c_population_loss_vs_flux"


@pytest.fixture(autouse=True)
def _fresh_caches():
    L.clear_caches()
    yield
    L.clear_caches()


# ---------------------------------------------------------------- run folders
def _age(folder: Path, seconds: float = 3600.0) -> None:
    """Every file of the run older than hub_sync.FRESH_S (a finished copy)."""
    t = time.time() - seconds
    for p in sorted(folder.rglob("*"), reverse=True) + [folder]:
        os.utime(p, (t, t))


def pl_run(root: Path, rid: int, curves: dict, *, fits: dict | None = None, t_ns=100,
           day: str = "2026-01-01", status: str = "finished", chip_name: str | None = "device",
           age: bool = True, h5: bool = True) -> Path:
    """A population-loss run: *curves* ``{qubit: {"f": [...], "loss": [...], "det": [...]?}}``
    (one length per run), *fits* the data.json fit_results (default: every curve, no defects)."""
    hh = f"{rid % 24:02d}0000"
    folder = root / day / f"#{rid}_{FAMILY}_{hh}"
    folder.mkdir(parents=True)
    names = list(curves)
    model = {"qubits": names}
    if t_ns is not None:
        model["interaction_time_ns"] = t_ns
    node = {"created_at": f"{day}T{hh[:2]}:00:00+00:00", "id": rid,
            "metadata": {"name": FAMILY, "status": status, "run_end": f"{day}T{hh[:2]}:00:00+00:00"},
            "data": {"parameters": {"model": model}}}
    (folder / "node.json").write_text(json.dumps(node), encoding="utf-8")
    fits = {q: {"defects": []} for q in names} if fits is None else fits
    (folder / "data.json").write_text(json.dumps({"fit_results": fits, "ds_fit": "./ds_fit.h5"}),
                                      encoding="utf-8")
    if h5:
        with h5py.File(folder / "ds_fit.h5", "w") as f:
            f["qubit"] = np.array(names, dtype=h5py.string_dtype())
            f["p_loss"] = np.array([curves[q]["loss"] for q in names], dtype=float)
            f["frequency"] = np.array([curves[q]["f"] for q in names], dtype=float)
            f["detuning"] = np.array([curves[q].get("det", np.asarray(curves[q]["f"]) - curves[q]["f"][0])
                                      for q in names], dtype=float)
            f["flux_conversion"] = np.array(["model"] * len(names), dtype=h5py.string_dtype())
    if chip_name is not None:
        (folder / "quam_state").mkdir()
        (folder / "quam_state" / "state.json").write_text(
            json.dumps({"extras": {"chip_name": chip_name}, "qubits": {}}), encoding="utf-8")
        (folder / "quam_state" / "wiring.json").write_text("{}", encoding="utf-8")
    if age:
        _age(folder)
    return folder


def sweep(f0, n=11, step=10 * MHZ, peak_at=None, peak=0.5):
    f = f0 + step * np.arange(n)
    loss = np.full(n, 0.01)
    if peak_at is not None:
        loss[peak_at] = peak
    return {"f": list(f), "loss": list(loss)}


def defect(f, fwhm=4e6, peak=0.4, strong=False):
    return {"frequency_hz": f, "fwhm_hz": fwhm, "peak_loss": peak, "strong": strong, "edge_truncated": False}


def of(curve, key="run_id"):
    """A curve's field, None when there is no curve (so a missing curve fails an assert)."""
    return (curve or {}).get(key)


def chip_qubits(**stored):
    out = {"q1": {"f01_hz": 5.0e9}, "q2": {"f01_hz": 4.6e9}}
    for q, v in stored.items():
        out[q]["stored"] = v
    return out


# ---------------------------------------------------------------- pieces
def test_pieces_split_at_a_jump_and_a_turn_but_not_at_a_zero_step():
    f = np.array([0, -1, -2, -3, 40, 41, 42, 42, 43, 42, 41], dtype=float) * MHZ
    # -1 -1 -1 | +43 (a jump AND a turn) | +1 +1 0 +1 | -1 (a turn) -1; the zero step is no turn
    assert L.split_pieces(f) == [(0, 4), (4, 9), (9, 11)]
    jump_only = np.array([0, 1, 2, 3, 40, 41], dtype=float) * MHZ
    assert L.split_pieces(jump_only) == [(0, 4), (4, 6)]
    saturated = np.array([0, 1, 2, 2, 2, 3], dtype=float) * MHZ    # a quad axis at the model's reach
    assert L.split_pieces(saturated) == [(0, 6)]


def test_decimation_keeps_the_peak_and_the_ends():
    rng = np.random.default_rng(3)
    loss = rng.uniform(0.0, 0.05, 1000)
    loss[777] = 0.9
    keep = L.decimate(loss)
    assert len(keep) <= L.MAX_POINTS and 777 in keep and keep[0] == 0 and keep[-1] == 999
    assert list(L.decimate(loss[:300])) == list(range(300))


# ---------------------------------------------------------------- [derived] helpers
PIECES = [{"frequency_hz": [0, 1e6, 2e6, 3e6, 4e6], "loss": [0.0, 0.1, 0.2, 0.3, 0.4]},
          {"frequency_hz": [1.5e6, 2.5e6, 6.5e6, 7.5e6], "loss": [0.5, 0.6, 0.7, 0.8]}]


def test_loss_at_interpolates_inside_one_piece_and_reports_every_bracketing_piece():
    two = L.loss_at(PIECES, 2.0e6)
    assert two["loss"] == pytest.approx(0.55) and two["reason"] is None
    assert [(p["piece"], p["loss"]) for p in two["pieces"]] == [(0, pytest.approx(0.2)), (1, pytest.approx(0.55))]
    # piece 1 brackets 3 MHz only across its 4 MHz gap (> 2 median steps): refused, piece 0 answers
    three = L.loss_at(PIECES, 3.0e6)
    assert three["loss"] == pytest.approx(0.3)
    assert [p["loss"] for p in three["pieces"]] == [pytest.approx(0.3), None]
    assert "gap" in three["pieces"][1]["reason"]


def test_loss_at_refuses_a_gap_and_says_not_measured():
    five = L.loss_at(PIECES, 5.0e6)
    assert five["loss"] is None and five["reason"].startswith(L.NOT_MEASURED) and "4.0 MHz gap" in five["reason"]
    assert [p["piece"] for p in five["pieces"]] == [1]
    outside = L.loss_at(PIECES, 9e6)
    # outside every piece the reason names what the scan does cover
    assert outside["loss"] is None and outside["pieces"] == []
    assert outside["reason"].startswith(L.NOT_MEASURED) and "the scan covers" in outside["reason"]


def test_defects_swept_counts_a_defect_within_half_its_width_of_the_excursion():
    ds = [defect(4.1e9), defect(4.205e9, fwhm=12e6, strong=True), defect(4.207e9, fwhm=12e6),
          {"frequency_hz": 3.99e9}, {"frequency_hz": 4.2e9, "fwhm_hz": None}]
    hit = L.defects_swept(ds, 4.2e9, 4.0e9)            # either order
    assert [d["frequency_hz"] for d in hit] == [4.1e9, 4.205e9, 4.2e9]
    assert [d["inside"] for d in hit] == [True, False, True]
    assert hit[1]["strong"] and hit[1]["distance_hz"] == pytest.approx(5e6)


# ---------------------------------------------------------------- which run
def test_the_marker_source_is_the_newest_run_whose_defects_equal_the_stored_list(tmp_path):
    root = tmp_path / "data"
    stored = json.dumps([defect(4.62e9), defect(4.70e9)])
    pl_run(root, 20, {"q1": sweep(4.5e9)})                                       # newest scan, no defects
    # fit_results carries q1's list, but ds_fit never measured q1: never named
    pl_run(root, 19, {"q2": sweep(4.4e9)}, fits={"q1": {"defects": [defect(4.62e9 + .5), defect(4.70e9)]},
                                                  "q2": {"defects": []}})
    # measured q2 only (its saved state may re-save q1's list): never named
    pl_run(root, 18, {"q2": sweep(4.4e9)})
    pl_run(root, 17, {"q1": sweep(4.5e9, peak_at=3)},
           fits={"q1": {"defects": [defect(4.70e9 - .4), defect(4.62e9 + .4)]}})     # order-free, < 1 Hz
    pl_run(root, 16, {"q1": sweep(4.5e9)}, fits={"q1": {"defects": [defect(4.62e9), defect(4.70e9)]}})
    out = L.build([root], chip_qubits(q1=stored))
    q1 = out["curves"]["q1"]
    assert of(q1["scan"], "run_id") == 20
    assert q1["marker"]["status"] == "match" and q1["marker"]["run_id"] == 17 and q1["marker"]["matches"] == 2
    assert of(q1["marker_curve"], "run_id") == 17 and not q1["marker"]["same_as_scan"]


def test_an_empty_stored_list_matching_several_runs_is_named_ambiguous(tmp_path):
    root = tmp_path / "data"
    for rid in (31, 32, 33):
        pl_run(root, rid, {"q1": sweep(4.5e9)})
    pl_run(root, 34, {"q1": sweep(4.5e9)}, fits={"q1": {"defects": [defect(4.6e9)]}})
    m = L.build([root], chip_qubits(q1="[]"))["curves"]["q1"]["marker"]
    assert m["status"] == "ambiguous" and m["run_id"] == 33 and m["matches"] == 3
    assert m["label"].startswith("ambiguous (empty list matches 3 runs)")


def test_no_matching_run_says_the_source_is_not_in_the_data_folder(tmp_path):
    root = tmp_path / "data"
    pl_run(root, 40, {"q1": sweep(4.5e9)}, fits={"q1": {"defects": [defect(4.61e9)]}})
    out = L.build([root], chip_qubits(q1=json.dumps([defect(4.62e9)])))
    m = out["curves"]["q1"]["marker"]
    assert m["status"] == "no_match" and m["run_id"] is None
    assert m["label"] == "source of the stored defects not in the data folder"
    assert out["curves"]["q2"]["marker"]["status"] == "none"            # nothing stored


def test_the_idle_the_lab_used_is_checked_against_today(tmp_path):
    root = tmp_path / "data"
    moved = sweep(4.845e9)
    moved["det"] = list(np.asarray(moved["f"]) - 4.845e9)          # detuning 0 at the old idle
    pl_run(root, 50, {"q1": moved})
    same = sweep(4.6e9)
    same["det"] = list(np.asarray(same["f"]) - 4.6e9)
    pl_run(root, 49, {"q2": same})
    out = L.build([root], chip_qubits())
    q1 = out["curves"]["q1"]["scan"]
    assert q1["idle_at_run_hz"] == pytest.approx(4.845e9) and q1["idle_moved_hz"] == pytest.approx(155e6)
    assert q1["idle_note"] == "idle then 4.8450 GHz, now 5.0000 GHz (+155.0 MHz)"
    assert of(out["curves"]["q2"]["scan"], "idle_note") is None


def test_each_curve_carries_its_own_scan_duration_and_unclipped_loss(tmp_path):
    root = tmp_path / "data"
    q1 = sweep(4.5e9, n=5)
    q1["loss"] = [0.0, -0.11, 0.5, 1.86, 0.2]
    pl_run(root, 60, {"q1": q1}, t_ns=1000)
    pl_run(root, 61, {"q2": sweep(4.4e9, n=5)}, t_ns=100)
    pl_run(root, 59, {"q1": sweep(4.4e9, n=5)}, t_ns=None, day="2025-12-31")
    out = L.build([root], chip_qubits())
    a, b = out["curves"]["q1"]["scan"], out["curves"]["q2"]["scan"]
    assert (a["interaction_time_ns"], b["interaction_time_ns"]) == (1000, 100)
    assert a["pieces"][0]["loss"] == [0.0, -0.11, 0.5, 1.86, 0.2]
    assert (a["loss_min"], a["loss_max"]) == (pytest.approx(-0.11), pytest.approx(1.86))
    old = L.read_run(next((root / "2025-12-31").iterdir()))
    assert old["interaction_time_ns"] is None


def test_unfinished_fresh_and_truncated_runs_are_skipped_and_never_cached(tmp_path):
    root = tmp_path / "data"
    running = pl_run(root, 70, {"q1": sweep(4.5e9)}, status="running")
    fresh = pl_run(root, 71, {"q1": sweep(4.5e9)}, age=False)
    broken = pl_run(root, 72, {"q1": sweep(4.5e9)})
    raw = (broken / "ds_fit.h5").read_bytes()
    (broken / "ds_fit.h5").write_bytes(raw[:200])
    netcdf = pl_run(root, 73, {"q1": sweep(4.5e9)})
    (netcdf / "ds_fit.h5").write_bytes(b"CDF\x01" + b"\x00" * 64)
    _age(broken)
    _age(netcdf)
    out = L.build([root], chip_qubits())
    reasons = {s["run_id"]: s["reason"] for s in out["skipped"]}
    assert "not finished" in reasons.get(70, "") and reasons.get(71) == "still being written"
    assert "cannot be read" in reasons.get(72, "") and "not an HDF5" in reasons.get(73, "")
    assert out["curves"]["q1"]["scan"] is None
    # once each is finished / complete, the next read sees it (no failure was kept)
    node = json.loads((running / "node.json").read_text())
    node["metadata"]["status"] = "finished"
    (running / "node.json").write_text(json.dumps(node))
    (broken / "ds_fit.h5").write_bytes(raw)
    _age(running)
    _age(broken)
    _age(fresh)
    os.utime(broken / "ds_fit.h5", (time.time() - 100, time.time() - 100 - 7))  # a new file version
    os.utime(broken, (time.time() - 3600, time.time() - 3600))
    again = L.build([root], chip_qubits())
    assert not {70, 71, 72} & {s["run_id"] for s in again["skipped"]}
    assert of(again["curves"]["q1"]["scan"], "run_id") == 72


def test_a_transient_read_failure_is_not_kept_for_unchanged_files(tmp_path, monkeypatch):
    from quam_state_manager.core import safe_io
    root = tmp_path / "data"
    folder = pl_run(root, 75, {"q1": sweep(4.5e9)})
    real = safe_io.scan_bytes
    monkeypatch.setattr(safe_io, "scan_bytes", lambda p: None)       # a locked file, say
    assert L.read_run(folder) == {"ok": False, "reason": "ds_fit.h5 is missing or unreadable"}
    monkeypatch.setattr(safe_io, "scan_bytes", real)
    run = L.read_run(folder)                                         # the same files, read again
    assert run["ok"] and run["qubits"]["q1"]["pieces"]
    assert L.read_run(folder) is run                                 # a success is kept


def test_another_chips_run_and_a_run_of_unknown_names_are_skipped(tmp_path):
    root = tmp_path / "data"
    pl_run(root, 81, {"q1": sweep(4.5e9)})
    pl_run(root, 82, {"q1": sweep(4.5e9)})

    def identity(folder):
        return ("#82_" not in str(folder), "chip_name", "saved by a chip of another name")

    class Unknown:
        known = False
    out = L.build([root], chip_qubits(), identity=identity)
    assert of(out["curves"]["q1"]["scan"], "run_id") == 81
    assert {s["run_id"]: s["reason"] for s in out["skipped"]} == {82: "saved by a chip of another name"}
    none = L.build([root], chip_qubits(), names=lambda folder: Unknown())
    assert none["curves"]["q1"]["scan"] is None and len(none["skipped"]) == 2
    assert all("renamed" in s["reason"] for s in none["skipped"])


def test_a_run_saved_before_a_rename_lands_on_todays_qubit(tmp_path):
    root = tmp_path / "data"
    pl_run(root, 90, {"q2": sweep(4.5e9, peak_at=2)})       # the run's q2 is today's q1

    class Shifted:
        known = True

        @staticmethod
        def current(name):
            return {"q2": "q1", "q3": "q2"}.get(name)
    out = L.build([root], chip_qubits(), names=lambda folder: Shifted())
    assert of(out["curves"]["q1"]["scan"], "run_id") == 90 and of(out["curves"]["q1"]["scan"], "run_qubit") == "q2"
    assert out["curves"]["q2"]["scan"] is None


def test_no_roots_is_said_once_per_qubit():
    out = L.build([], chip_qubits(q1="[]"))
    assert out["reason"] == L.NO_ROOTS and out["curves"]["q1"]["reason"] == L.NO_ROOTS
    assert out["curves"]["q1"]["marker"]["status"] == "no_match"


# ---------------------------------------------------------------- the routes
def _chip_state(data_folder=None, renames=None):
    flux = {"__class__": "quam.components.pulses.SquarePulse", "length": 40, "amplitude": 0.12}
    qubits = {
        "q1": {"id": "q1", "f_01": 5.0e9, "anharmonicity": -200e6, "freq_vs_flux_01_quad_term": -8e9,
               "grid_location": "0,0",
               "z": {"flux_point": "joint", "joint_offset": 0.01, "operations": {"cz_flux": flux}},
               "extras": {"population_loss_defects_json": json.dumps([defect(4.95e9, strong=True)])}},
        "q2": {"id": "q2", "f_01": 4.0e9, "anharmonicity": -210e6, "freq_vs_flux_01_quad_term": 9e9,
               "grid_location": "1,0", "z": {"flux_point": "min"}},
        "q3": {"id": "q3", "f_01": 4.5e9, "anharmonicity": -200e6, "grid_location": "2,0",
               "z": {"flux_point": "joint"}},
    }
    state = {"qubits": qubits, "extras": {"chip_name": "device"},
             "qubit_pairs": {
                 "q1-2": {"id": "q1-2", "qubit_control": "#/qubits/q1", "qubit_target": "#/qubits/q2",
                          "moving_qubit": "control", "macros": {
                              "cz": {"__class__": "x.CZGate", "flux_pulse_qubit": "#/qubits/q1/z/operations/cz_flux"}}},
                 "q2-3": {"id": "q2-3", "qubit_control": "#/qubits/q2", "qubit_target": "#/qubits/q3",
                          "macros": {}}}}
    if data_folder is not None:
        state["extras"]["data_folder"] = str(data_folder)
    if renames:
        state["extras"]["qubit_renames"] = renames
    return state


def _client(tmp_path, state=None, roots=None):
    folder = tmp_path / "chips" / "live"
    folder.mkdir(parents=True)
    (folder / "state.json").write_text(json.dumps(state or _chip_state()), encoding="utf-8")
    (folder / "wiring.json").write_text(json.dumps({"wiring": {}}), encoding="utf-8")
    app = create_app(testing=True, instance_path=str(tmp_path / "_inst"))
    c = app.test_client()
    assert c.post("/load", data={"folder": str(folder)}).status_code in (200, 302)
    ctx = app.config["contexts"][app.config["active_context"]]
    if roots is not None:
        ctx["hub_roots"] = [(str(r), src) for r, src in roots]
    return app, c, ctx


def _q1_scan(root, rid=100, **kw):
    # q1 idles at 5.0 GHz and the cz moves it to 5.0 - 8e9 * 0.12**2 = 4.8848 GHz
    f = 4.80e9 + 5 * MHZ * np.arange(41)
    loss = np.full(41, 0.02)
    loss[17] = 0.3                      # 4.885 GHz, the sample right above the operating point
    return pl_run(root, rid, {"q1": {"f": list(f), "loss": list(loss), "det": list(f - 5.0e9)}}, **kw)


def test_two_roots_are_both_searched_and_the_curve_comes_from_the_second(tmp_path):
    declared, storage = tmp_path / "declared", tmp_path / "storage"
    (declared / "2026-01-01").mkdir(parents=True)
    _q1_scan(storage)
    _app, c, _ctx = _client(tmp_path, roots=[(declared, "declared"), (storage, "project_storage")])
    d = c.get("/cz-plan/loss").get_json()
    assert d["ok"] and [r["source"] for r in d["roots"]] == ["declared", "project_storage"]
    assert of(d["curves"]["q1"]["scan"], "run_id") == 100 and d["runs_listed"] == 1


def test_an_archive_without_registered_roots_asks_the_roots_it_would_declare(tmp_path, monkeypatch):
    from quam_state_manager.web import routes
    storage = tmp_path / "storage"
    _q1_scan(storage)
    _app, c, ctx = _client(tmp_path)
    ctx.pop("hub_roots", None)
    monkeypatch.setattr(routes, "_hub_roots_for", lambda ctx: [(str(storage), "declared")])
    assert of(c.get("/cz-plan/loss").get_json()["curves"]["q1"]["scan"], "run_id") == 100

    def broken(ctx):
        raise OSError("unreachable")
    monkeypatch.setattr(routes, "_hub_roots_for", broken)
    d = c.get("/cz-plan/loss").get_json()
    assert d["ok"] and d["reason"] == L.NO_ROOTS and d["curves"]["q1"]["scan"] is None


def test_curves_draw_while_the_ledger_is_building_and_staleness_says_so(tmp_path, monkeypatch):
    from quam_state_manager.web import routes
    storage = tmp_path / "storage"
    _q1_scan(storage)
    _q1_scan(storage, rid=101, chip_name=None, day="2025-12-30")
    _app, c, _ctx = _client(tmp_path, roots=[(storage, "declared")])
    monkeypatch.setattr(routes, "_value_history", lambda ctx, pm, limit=None, **kw: {"mode": "building"})
    d = c.get("/cz-plan/loss").get_json()
    assert of(d["curves"]["q1"]["scan"], "run_id") == 100
    assert d["staleness"]["mode"] == "building" and d["staleness"]["qubits"] == {}
    assert d["skipped"] == []


def test_a_run_of_another_chip_name_is_skipped_when_the_ledger_cannot_judge(tmp_path):
    storage = tmp_path / "storage"
    _q1_scan(storage, rid=100, chip_name="other device")
    _q1_scan(storage, rid=99)
    _app, c, _ctx = _client(tmp_path, roots=[(storage, "declared")])
    d = c.get("/cz-plan/loss").get_json()
    assert of(d["curves"]["q1"]["scan"], "run_id") == 99
    assert d["skipped"] == [{"run_id": 100, "name": "#100_" + FAMILY + "_040000",
                             "reason": "saved by a chip of another name"}]


def test_staleness_names_flux_model_inputs_changed_after_the_run(tmp_path, monkeypatch):
    from quam_state_manager.web import routes
    storage = tmp_path / "storage"
    _q1_scan(storage)                  # 2026-01-01T04:00:00Z
    _app, c, _ctx = _client(tmp_path, roots=[(storage, "declared")])
    run_us = 1767240000 * 10**6
    seen = {}

    def history(ctx, path_map, limit=None, **kw):
        seen["paths"], seen["limit"] = sorted(path_map), limit
        pt = lambda dt, rid: {"t_us": run_us + dt, "t": "x", "run_id": rid, "provenance": "run_saved"}
        return {"mode": "ledger", "rows": {
            "qubits.q1.freq_vs_flux_01_quad_term": {"effective": [pt(-5 * 10**6, 90), pt(3600 * 10**6, 120)]},
            "qubits.q1.z.joint_offset": {"effective": [pt(7200 * 10**6, 130)]},
            "qubits.q1.f_01": {"effective": [pt(9000 * 10**6, 140)]}}}
    monkeypatch.setattr(routes, "_value_history", history)
    d = c.get("/cz-plan/loss").get_json()
    # f_01 and the anharmonicity are not asked (the idle check covers f_01)
    assert seen["paths"] == ["qubits.q1.freq_vs_flux_01_quad_term", "qubits.q1.z.joint_offset"]
    assert seen["limit"] == 3
    (line,) = d["staleness"]["qubits"]["q1"]
    assert line["text"] == "axis of run #100 predates 2 flux-model inputs changed (run #130)"
    assert line["fields"] == ["freq_vs_flux_01_quad_term", "z.joint_offset"] and line["which"] == "scan"


def test_a_renamed_chip_reads_an_old_run_in_todays_names(tmp_path):
    from tests.test_rename_datasets import REC
    storage = tmp_path / "storage"
    old = sweep(4.5e9, n=5)
    pl_run(storage, 100, {"q2": old})                # saved before the rename: q1 -> q0, q2 -> q1
    state = _chip_state(renames=[REC])
    state["qubits"] = {"q0": dict(state["qubits"]["q1"], id="q0"), "q1": dict(state["qubits"]["q2"], id="q1")}
    state["qubit_pairs"] = {}
    _app, c, _ctx = _client(tmp_path, state=state, roots=[(storage, "declared")])
    d = c.get("/cz-plan/loss").get_json()
    assert of(d["curves"]["q1"]["scan"], "run_id") == 100 and of(d["curves"]["q1"]["scan"], "run_qubit") == "q2"
    assert d["curves"]["q0"]["scan"] is None


def test_the_ops_memo_is_reused_and_invalidated_by_an_edit(tmp_path, monkeypatch):
    from quam_state_manager.core import cz_gate_plan as czp
    calls = []
    real = czp.operations
    monkeypatch.setattr(czp, "operations", lambda *a, **kw: calls.append(1) or real(*a, **kw))
    _app, c, ctx = _client(tmp_path)
    first = c.get("/cz-plan/map").get_json()
    c.get("/cz-plan/loss")
    c.get("/cz-plan/data?op=qubit_pairs.q1-2.macros.cz")
    assert len(calls) == 1
    ctx["modifier"].set_value("qubits.q1.z.operations.cz_flux.amplitude", 0.05)
    second = c.get("/cz-plan/map").get_json()
    assert len(calls) == 2
    seg = lambda m: next(s for s in m["segments"] if s["qubit"] == "q1")["frequency_hz"]
    assert seg(first) == pytest.approx(5e9 - 8e9 * 0.12 ** 2) and seg(second) == pytest.approx(5e9 - 8e9 * 0.05 ** 2)


def test_the_gate_block_reads_the_scan_at_the_operating_point_and_the_defects_on_the_way(tmp_path):
    storage = tmp_path / "storage"
    _q1_scan(storage)
    _app, c, _ctx = _client(tmp_path, roots=[(storage, "declared")])
    d = c.get("/cz-plan/data?op=qubit_pairs.q1-2.macros.cz").get_json()
    g1, g2 = d["gate_loss"]
    f_op = 5e9 - 8e9 * 0.12 ** 2                       # 4.8848 GHz, between 4.880 and 4.885
    assert g1["qubit"] == "q1" and g1["operating_hz"] == pytest.approx(f_op)
    assert of(g1["scan"], "run_id") == 100 and g1["scan"]["interaction_time_ns"] == 100
    assert g1["scan"]["loss"] == pytest.approx(0.02 + (0.3 - 0.02) * (f_op - 4.880e9) / 5e6, abs=1e-4)
    # the square pulse sits at the operating point; the excursion runs from there to idle
    assert g1["sweep"]["source"] == "trajectory"
    assert (g1["sweep"]["f_min_hz"], g1["sweep"]["f_max_hz"]) == (pytest.approx(f_op), 5e9)
    assert [x["frequency_hz"] for x in g1["defects_touched"]] == [4.95e9] and g1["defects_touched"][0]["strong"]
    assert "today's flux conversion" in g1["sweep"]["note"]
    assert g2["qubit"] == "q2" and g2["reason"] == "stays at idle" and g2["scan"] is None


def test_notes_are_tagged_with_the_figure_they_explain(tmp_path, monkeypatch):
    from quam_state_manager.core.loader import QuamStore
    from quam_state_manager.web import routes
    s = _chip_state()
    s["qubit_pairs"]["q1-2"]["macros"]["cz"]["coupler_flux_pulse"] = {"amplitude": 0.1}
    s["qubit_pairs"]["q1-2"]["macros"]["cz"]["duration_qubit"] = "#/runtime/len"
    s["qubit_pairs"]["broken"] = "#/missing"
    store = QuamStore.from_dicts(s, {})
    monkeypatch.setattr(routes, "_store", lambda: store)
    monkeypatch.setattr(routes, "_active_ctx", lambda: None)
    c = create_app(testing=True, instance_path=str(tmp_path / "_inst")).test_client()
    d = c.get("/cz-plan/data?op=qubit_pairs.q1-2.macros.cz").get_json()
    tags = dict(zip(d["notes"], d["notes_figure"]))
    assert len(d["notes"]) == len(d["notes_figure"]) == 3 and len(d["info"]) == len(d["info_figure"])
    assert [t for n, t in tags.items() if "unavailable" in n] == ["status"]
    assert [t for n, t in tags.items() if "coupler" in n] == ["both"]
    assert [t for n, t in tags.items() if "nominal one" in n] == ["time"]
    assert d["unavailable"] and "unavailable" in d["unavailable"][0]


def test_the_card_counts_its_staleness_and_groups_it_per_qubit_and_run(tmp_path, monkeypatch):
    from quam_state_manager.web import routes
    _app, c, _ctx = _client(tmp_path)
    pt = lambda t_us, rid, t: {"t_us": t_us, "t": t, "run_id": rid, "provenance": "run_proven", "proven": True}

    def history(rows):
        out = {"mode": "ledger", "rows": {}}
        for row in rows:
            for p in row["amplitudes"]:
                out["rows"][p] = {"effective": [pt(10 * 10**6, 5, "2026-01-01T00:00:10Z")]}
            out["rows"]["qubits.q1.f_01"] = {"effective": [pt(50 * 10**6, 7, "2026-01-01T00:00:50Z")]}
            out["rows"]["qubits.q1.z.joint_offset"] = {"effective": [pt(90 * 10**6, 9, "2026-01-01T00:01:30Z")]}
            out["rows"]["qubits.q2.anharmonicity"] = {"effective": [pt(50 * 10**6, 7, "2026-01-01T00:00:50Z")]}
        return out
    monkeypatch.setattr(routes, "_cz_plan_history", history)
    card = c.get("/cz-plan/data?op=qubit_pairs.q1-2.macros.cz").get_json()["calibration"]
    assert card["warning_count"] == len(card["warnings"]) == 3
    assert card["warning_groups"] == [
        {"qubit": "q1", "runs": [{"run_id": 9, "t": "2026-01-01T00:01:30Z", "fields": ["z.joint_offset"]},
                                 {"run_id": 7, "t": "2026-01-01T00:00:50Z", "fields": ["f_01"]}]},
        {"qubit": "q2", "runs": [{"run_id": 7, "t": "2026-01-01T00:00:50Z", "fields": ["anharmonicity"]}]}]


# ---------------------------------------------------------------- the contract
_CONTRACT = Path(__file__).parent / "fixtures" / "cz_plan_contract"


def _shape(v, depth=0):
    """Key sets of a JSON value down to a few levels (arrays: their first element)."""
    if depth > 4:
        return None
    if isinstance(v, dict):
        return {k: _shape(x, depth + 1) for k, x in v.items()}
    if isinstance(v, list):
        return [_shape(v[0], depth + 1)] if v else []
    return None


def _subset(small, big, where=""):
    """Every key the synthetic answer has is a key the frozen contract has, recursively."""
    if isinstance(small, dict) and isinstance(big, dict):
        for k, v in small.items():
            assert k in big, f"{where}.{k} is not in the contract"
            _subset(v, big[k], f"{where}.{k}")
    elif isinstance(small, list) and isinstance(big, list) and small and big:
        _subset(small[0], big[0], where + "[]")


@pytest.mark.skipif(not _CONTRACT.exists(), reason="no contract fixtures")
def test_the_routes_answer_in_the_frozen_contract_shape(tmp_path):
    storage = tmp_path / "storage"
    _q1_scan(storage)
    _app, c, _ctx = _client(tmp_path, roots=[(storage, "declared")])
    frozen = {name: json.loads((_CONTRACT / f"{name}.json").read_text(encoding="utf-8"))
              for name in ("map_rig5q", "loss_rig5q", "data_rig5q")}
    live = {"map_rig5q": c.get("/cz-plan/map").get_json(), "loss_rig5q": c.get("/cz-plan/loss").get_json(),
            "data_rig5q": c.get("/cz-plan/data?op=qubit_pairs.q1-2.macros.cz").get_json()}
    for name in frozen:
        assert set(live[name]) == set(frozen[name]), name
        # the frozen curve entry's keys are exactly what a live curve carries
        _subset(_shape(live[name]), _shape(frozen[name]), name)
    scan_keys = set(live["loss_rig5q"]["curves"]["q1"]["scan"])
    frozen_scan = next(e["scan"] for e in frozen["loss_rig5q"]["curves"].values() if e["scan"])
    assert scan_keys == set(frozen_scan)
