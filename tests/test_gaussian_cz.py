"""docs/126 ⑦b — the Gaussian CZ macro builder.

The customer's ``add_gaussian_cz_macros.py`` as an SM feature. Three layers:

- the PURE planner (`core.gaussian_cz.plan`): subtree shapes, pointer grammar,
  the script's own guard set as honest refusals;
- the real-chip structural golden (gated on the customer folder): the plan's
  structure — key sets, classes, pointer strings — must match what the
  customer's own script wrote on their live chip (numeric values may differ:
  cz_flattop has been recalibrated since, and the macros themselves were
  hand-tuned after creation — including one deliberate class change on the
  bipolar coupler op, which is recorded here, not silently tolerated);
- the route: one change group (one undo restores everything), 409 on existing
  until overwrite=1, archives refuse, and — the docs/98-grade proof — the
  result still ``Quam.load()``s in the customer env.
"""
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from quam_state_manager.core import gaussian_cz

_REAL = Path("D:/work/Customer_Codes/PJ_10082026/quam_state")

_WIRING = {"network": {"host": "1.1.1.1", "cluster_name": "C1"}}


def _chip(coupler=True, moving="control", z_ops=True):
    state = {
        "qubits": {
            "q1": {"id": "q1", "f_01": 5e9,
                   "z": ({"operations": {"const": {"amplitude": 0.1}}}
                         if z_ops else {"dc_offset": 0.0})},
            "q2": {"id": "q2", "f_01": 5.2e9,
                   "z": {"operations": {"const": {"amplitude": 0.1}}}},
        },
        "qubit_pairs": {"q1-2": {
            "id": "q1-2",
            "qubit_control": "#/qubits/q1",
            "qubit_target": "#/qubits/q2",
            "moving_qubit": moving,
            "macros": {"cz_flattop": {
                "flux_pulse_qubit": {"amplitude": 0.31, "flat_length": 60},
                "coupler_flux_pulse": ({"amplitude": 0.12} if coupler else None),
            }},
        }},
        "active_qubit_names": ["q1", "q2"],
    }
    if coupler:
        state["qubit_pairs"]["q1-2"]["coupler"] = {
            "id": "c12", "operations": {"const": {"amplitude": 0.2}}}
    return state


class TestPlanner:
    def test_full_coupler_pair_builds_six_subtrees(self):
        res = gaussian_cz.plan(_chip(), "q1-2", padding_length=20,
                               qubit_filter_mhz=20, coupler_filter_mhz=50)
        assert "error" not in res
        paths = [p for p, _ in res["creates"]]
        assert paths == [
            "qubit_pairs.q1-2.macros.cz_gaussian_unipolar",
            "qubits.q1.z.operations.cz_gaussian_unipolar_pulse",
            "qubit_pairs.q1-2.coupler.operations.cz_gaussian_unipolar_coupler_pulse",
            "qubit_pairs.q1-2.macros.cz_gaussian_bipolar",
            "qubits.q1.z.operations.cz_gaussian_bipolar_pulse",
            "qubit_pairs.q1-2.coupler.operations.cz_gaussian_bipolar_coupler_pulse",
        ]
        by = dict(res["creates"])
        uni = by["qubit_pairs.q1-2.macros.cz_gaussian_unipolar"]
        assert uni["__class__"].endswith(".CZGate")
        assert uni["flux_pulse_qubit"]["__class__"] == \
            "quam_builder.common.pulses.GaussianFilteredSquarePulse"
        assert uni["flux_pulse_qubit"]["pulse_length"] == 60
        assert uni["flux_pulse_qubit"]["amplitude"] == 0.31
        assert uni["flux_pulse_qubit"]["padding_length"] == 20
        assert uni["coupler_flux_pulse"]["amplitude"] == 0.12
        assert uni["coupler_flux_pulse"]["gaussian_filter_frequency_mhz"] == 50
        bi = by["qubit_pairs.q1-2.macros.cz_gaussian_bipolar"]
        assert bi["flux_pulse_qubit"]["__class__"] == (
            "quam_builder.architecture.superconducting.components.pulses."
            "GaussianFilteredSymmetricBipolarPulse")
        # the channel op is the pointer-linked twin
        zop = by["qubits.q1.z.operations.cz_gaussian_unipolar_pulse"]
        ref = "#/qubit_pairs/q1-2/macros/cz_gaussian_unipolar/flux_pulse_qubit"
        assert zop["amplitude"] == ref + "/amplitude"
        assert zop["pulse_length"] == ref + "/pulse_length"
        assert zop["padding_length"] == ref + "/padding_length"
        assert zop["gaussian_filter_frequency_mhz"] == \
            ref + "/gaussian_filter_frequency_mhz"
        assert zop["id"] is None and zop["length"] == "#./inferred_length"
        cop = by["qubit_pairs.q1-2.coupler.operations"
                 ".cz_gaussian_unipolar_coupler_pulse"]
        assert cop["amplitude"] == (
            "#/qubit_pairs/q1-2/macros/cz_gaussian_unipolar/"
            "coupler_flux_pulse/amplitude")

    def test_couplerless_pair_builds_four_with_null_slot(self):
        res = gaussian_cz.plan(_chip(coupler=False), "q1-2")
        assert "error" not in res
        assert len(res["creates"]) == 4
        by = dict(res["creates"])
        assert by["qubit_pairs.q1-2.macros.cz_gaussian_unipolar"][
            "coupler_flux_pulse"] is None

    def test_moving_target_routes_to_the_target_qubit(self):
        res = gaussian_cz.plan(_chip(moving="target"), "q1-2")
        assert res["sources"]["moving_qubit"] == "q2"
        assert any(p.startswith("qubits.q2.z.") for p, _ in res["creates"])

    def test_refusals_name_what_is_missing(self):
        state = _chip()
        del state["qubit_pairs"]["q1-2"]["macros"]["cz_flattop"]
        assert "cz_flattop" in gaussian_cz.plan(state, "q1-2")["error"]

        state = _chip()
        state["qubit_pairs"]["q1-2"]["moving_qubit"] = None
        assert "moving_qubit" in gaussian_cz.plan(state, "q1-2")["error"]

        # a QDAC-biased z (no operations) cannot host OPX flux pulses
        res = gaussian_cz.plan(_chip(z_ops=False), "q1-2")
        assert "QDAC" in res["error"]

        state = _chip()
        state["qubit_pairs"]["q1-2"]["macros"]["cz_flattop"][
            "flux_pulse_qubit"]["amplitude"] = "not-a-number"
        assert "numeric" in gaussian_cz.plan(state, "q1-2")["error"]

        assert "not found" in gaussian_cz.plan(_chip(), "nope")["error"]

    def test_existing_macros_are_listed(self):
        state = _chip()
        state["qubit_pairs"]["q1-2"]["macros"]["cz_gaussian_unipolar"] = {
            "__class__": "x"}
        res = gaussian_cz.plan(state, "q1-2")
        assert "qubit_pairs.q1-2.macros.cz_gaussian_unipolar" in res["existing"]

    def test_eligible_pairs_lists_flattop_carriers_only(self):
        state = _chip()
        state["qubit_pairs"]["q3-4"] = {"id": "q3-4", "macros": {}}
        rows = gaussian_cz.eligible_pairs(state)
        assert [r["pair_id"] for r in rows] == ["q1-2"]
        assert rows[0]["has_coupler"] is True


@pytest.mark.skipif(not _REAL.exists(), reason="customer chip absent")
class TestRealChipStructuralGolden:
    def test_structure_matches_the_customers_own_script_output(self):
        from quam_state_manager.core.loader import QuamStore
        from quam_state_manager.core.pointer_path import _walk
        st = QuamStore(str(_REAL))
        m = st.merged
        res = gaussian_cz.plan(m, "q19-20", padding_length=20,
                               qubit_filter_mhz=200, coupler_filter_mhz=50)
        assert "error" not in res, res
        assert res["sources"]["moving_qubit"] == "q19"

        # The real chip HAS these (the customer ran the script) — compare
        # structurally: identical key sets everywhere; every pointer string
        # byte-equal; classes byte-equal EXCEPT the bipolar variant's COUPLER
        # pulse (macro + linked op), which the customer hand-changed to the
        # Square class after creation (a physics choice — the coupler needs
        # no net-zero shape; the handed script builds Symmetric for both, so
        # the builder follows the script and this deviation is RECORDED).
        allowed_class_dev = {
            ("qubit_pairs.q19-20.coupler.operations."
             "cz_gaussian_bipolar_coupler_pulse.__class__"),
            ("qubit_pairs.q19-20.macros.cz_gaussian_bipolar."
             "coupler_flux_pulse.__class__"),
        }
        for path, planned in res["creates"]:
            found, real = _walk(m, path.split("."))
            assert found, path

            def compare(a, b, at):
                if isinstance(a, dict):
                    assert isinstance(b, dict), at
                    if at.rsplit(".", 1)[-1] in ("fidelity", "extras"):
                        # calibration-populated containers — the script
                        # creates them empty; RB results land there later
                        return
                    assert set(a) == set(b), (at, sorted(set(a) ^ set(b)))
                    for k in a:
                        compare(a[k], b[k], at + "." + k)
                    return
                if isinstance(a, str):
                    if at in allowed_class_dev:
                        return   # documented post-creation hand edit
                    assert a == b, (at, a, b)
                elif a is None:
                    assert b is None, (at, b)
                # numbers may differ: cz_flattop was recalibrated since, and
                # the macros were hand-tuned after creation
            compare(planned, real, path)


class TestRoute:
    @pytest.fixture
    def env(self, tmp_path):
        from quam_state_manager.web.app import create_app
        (tmp_path / "state.json").write_text(json.dumps(_chip()),
                                             encoding="utf-8")
        (tmp_path / "wiring.json").write_text(json.dumps(_WIRING),
                                              encoding="utf-8")
        app = create_app(testing=True, instance_path=str(tmp_path / "_i"))
        c = app.test_client()
        c.post("/load", data={"folder": str(tmp_path)})
        return {"app": app, "client": c, "tmp": tmp_path}

    def _store(self, env):
        return next(iter(env["app"].config["contexts"].values()))["store"]

    def test_form_lists_the_eligible_pair(self, env):
        html = env["client"].get("/pulse/gaussian-cz").get_data(as_text=True)
        assert 'value="q1-2"' in html and "coupler" in html

    def test_create_undo_and_overwrite(self, env):
        c = env["client"]
        r = c.post("/api/pulse/gaussian-cz", data={"pair_id": "q1-2"})
        assert r.status_code == 200, r.data
        st = self._store(env)
        assert "cz_gaussian_unipolar" in st.state["qubit_pairs"]["q1-2"]["macros"]
        assert "cz_gaussian_bipolar_pulse" in \
            st.state["qubits"]["q1"]["z"]["operations"]
        assert "cz_gaussian_unipolar_coupler_pulse" in \
            st.state["qubit_pairs"]["q1-2"]["coupler"]["operations"]
        # ONE undo removes the whole set (one group)
        r2 = c.post("/undo")
        assert r2.status_code == 200
        assert "cz_gaussian_unipolar" not in \
            st.state["qubit_pairs"]["q1-2"]["macros"]
        assert "cz_gaussian_unipolar_pulse" not in \
            st.state["qubits"]["q1"]["z"]["operations"]

        # create again, then 409 on a repeat until overwrite=1
        assert c.post("/api/pulse/gaussian-cz",
                      data={"pair_id": "q1-2"}).status_code == 200
        r3 = c.post("/api/pulse/gaussian-cz", data={"pair_id": "q1-2"})
        assert r3.status_code == 409
        assert "Replace existing" in r3.get_data(as_text=True)
        r4 = c.post("/api/pulse/gaussian-cz",
                    data={"pair_id": "q1-2", "overwrite": "1",
                          "qubit_filter_mhz": "77"})
        assert r4.status_code == 200
        assert st.state["qubit_pairs"]["q1-2"]["macros"][
            "cz_gaussian_unipolar"]["flux_pulse_qubit"][
            "gaussian_filter_frequency_mhz"] == 77

    def test_archive_refuses(self, env, tmp_path):
        ctx = next(iter(env["app"].config["contexts"].values()))
        ctx["origin"] = "dataset_archive"
        r = env["client"].post("/api/pulse/gaussian-cz",
                               data={"pair_id": "q1-2"})
        assert r.status_code == 409
        assert "archive" in r.get_data(as_text=True)

    def test_planner_error_is_a_400_not_a_500(self, env):
        r = env["client"].post("/api/pulse/gaussian-cz",
                               data={"pair_id": "ghost"})
        assert r.status_code == 400
        assert "not found" in r.get_data(as_text=True)


@pytest.mark.skipif(not _REAL.exists(), reason="customer chip absent")
class TestQuamLoadRoundTrip:
    def test_created_macros_still_quam_load(self, tmp_path):
        """docs/98-grade proof on the REAL chip: create the macro set for a
        pair that lacks it, save, and the working copy still Quam.load()s in
        the customer env (this suite's own env carries quam_config)."""
        probe = subprocess.run(
            [sys.executable, "-c", "import quam_config, quam_builder"],
            capture_output=True, timeout=120)
        if probe.returncode != 0:
            pytest.skip("customer QM stack not importable in this env")

        from quam_state_manager.web.app import create_app
        live = tmp_path / "quam_state"
        live.mkdir()
        for f in ("state.json", "wiring.json"):
            shutil.copy2(_REAL / f, live / f)
        app = create_app(testing=True, instance_path=str(tmp_path / "_i"))
        c = app.test_client()
        c.post("/load", data={"folder": str(live)})

        ctx = next(iter(app.config["contexts"].values()))
        store = ctx["store"]
        # a pair with cz_flattop but WITHOUT the gaussian macros
        pid = next(
            (r["pair_id"] for r in gaussian_cz.eligible_pairs(store.merged)
             if not r["existing"] and (store.merged["qubit_pairs"][r["pair_id"]]
                                       .get("moving_qubit") in ("control",
                                                                "target"))),
            None)
        if pid is None:
            pytest.skip("no gaussian-less cz_flattop pair on the real chip")
        r = c.post("/api/pulse/gaussian-cz", data={"pair_id": pid})
        if r.status_code == 400:
            pytest.skip("real-chip pair refused: " + r.get_data(as_text=True))
        assert r.status_code == 200, r.data
        assert c.post("/save").status_code == 200

        wc_dir = ctx["working_copy"].working_folder
        code = (
            "import os, sys\n"
            f"os.environ['QUAM_STATE_PATH'] = r'{wc_dir}'\n"
            "from quam_config import Quam\n"
            f"m = Quam.load(r'{wc_dir}')\n"
            f"pair = m.qubit_pairs['{pid}']\n"
            "g = pair.macros['cz_gaussian_unipolar']\n"
            "assert g.flux_pulse_qubit.amplitude == "
            "pair.macros['cz_flattop'].flux_pulse_qubit.amplitude\n"
            "print('LOADED-OK', len(m.qubit_pairs))\n"
        )
        run = subprocess.run([sys.executable, "-c", code],
                             capture_output=True, text=True, encoding="utf-8",
                             timeout=300)
        assert run.returncode == 0, (run.stdout or "") + (run.stderr or "")
        assert "LOADED-OK" in run.stdout


class TestBarePulseRecognized:
    """docs/126 follow-up (found while verifying the CQT XEB run #2560): the
    bare ``quam.components.pulses.Pulse`` is a DIGITAL-MARKER-ONLY pulse —
    quam's own waveform is None — and real chips carry 11 of them as the QDAC
    trigger pulses (docs/119). SM used to brand them "Unrecognized pulse
    class"; they are now recognized with an honest no-analog answer."""

    def test_resolves_and_is_not_creatable(self):
        from quam_state_manager.core.pulse_catalog import resolve_qclass
        spec, how = resolve_qclass("quam.components.pulses.Pulse")
        assert spec is not None and spec.key == "Pulse" and how == "exact"
        assert not spec.creatable
        assert spec.label == "Digital marker only"

    def test_synth_answers_digital_only_never_unrecognized(self):
        from quam_state_manager.core import waveform_synth as ws
        p = ws.synthesize("quam.components.pulses.Pulse",
                          {"length": 100, "digital_marker": "ON"})
        assert p["ok"] is False and p.get("digital_only") is True
        assert "digital marker only" in p["error"]
        assert "unrecognized" not in p["error"].lower()
