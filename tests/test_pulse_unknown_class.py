"""docs/189 — a pulse whose CLASS the lab wrote still plots.

Customer, on-site 2026-09-16: *"pulses 메뉴에서 snz 는 plotting이 안돼. 왜 그래?"*

The KRISS_CZ chip's CZ flux pulse is `quam_config.two_flux_gate.SNZTwoFluxPulse`
— a class that lab wrote. `core/waveform_synth.py` mirrors quam's own classes,
so it answered ``unrecognized pulse class '...'`` and the page drew nothing.
Four such classes cover 30 pulse objects on that one chip.

The remedy is deliberately NOT to transcribe the lab's algorithm into SM: a copy
goes stale the day the lab edits its module, and a wrong waveform is worse than
no waveform. The generated config IS the lab's own ``generate_config()`` output,
so it cannot disagree with what the instrument will play — and the page says
that is where the curve came from, rather than passing it off as the synth.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from quam_state_manager.web.app import create_app


LAB_CLASS = "mylab.two_flux_gate.SNZTwoFluxPulse"
SNZ_PATH = "qubits.q1.z.operations.cz_snz"
KNOWN_PATH = "qubits.q1.xy.operations.x180_DragCosine"


def _chip(folder: Path) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "state.json").write_text(json.dumps({
        "qubits": {
            "q1": {
                "id": "q1", "f_01": 6.1e9,
                "xy": {"RF_frequency": 6.1e9, "operations": {
                    # a class SM DOES know — the control for every pin here
                    "x180_DragCosine": {
                        "__class__": ("quam_builder.architecture.superconducting"
                                      ".components.pulses.DragCosinePulse"),
                        "amplitude": 0.3, "length": 40, "alpha": -0.05,
                        "anharmonicity": 2.0e8, "axis_angle": 0.0,
                        "detuning": 0.0, "digital_marker": None,
                    },
                }},
                "z": {"joint_offset": 0.0, "independent_offset": 0.0,
                      "flux_point": "joint", "operations": {
                          # the lab's own class: the fields are real SNZ knobs,
                          # and nothing in SM's catalog describes them
                          "cz_snz": {
                              "__class__": LAB_CLASS,
                              "amplitude": 0.33, "flat_length": 78,
                              "t_phi": 0, "b_over_a": 0.25,
                              "neg_offset_v": -9.5e-05, "padding": 4,
                              "axis_angle": None, "length": 88,
                          },
                      }},
            },
        },
        "active_qubit_names": ["q1"],
    }), encoding="utf-8")
    (folder / "wiring.json").write_text(json.dumps(
        {"network": {"host": "1.2.3.4"}, "wiring": {"qubits": {}}}),
        encoding="utf-8")
    return folder


#: The lab's own generate_config() output, in the shape QM emits: an element
#: carries operation -> pulse name, a pulse carries waveform names, a waveform
#: carries the samples. These are the ONLY samples SM can honestly show for a
#: class it does not model.
TRUTH_SAMPLES = [0.0, 0.33, 0.33, 0.08, -0.08, -0.33, -0.33, 0.0]
#: The config ALSO carries the known pulse, with deliberately different
#: numbers. Without it the "a known class is untouched" pin is vacuous: the
#: lookup would answer `not-found` for that element and the page would look
#: identical whether or not the fallback fired (the mutation sweep found
#: exactly that — docs/141 §4af: a GREEN means the FIXTURE cannot reach the
#: state). ``0.777`` appears nowhere the synth could produce it.
MARKER = 0.777
CONFIG = {
    "elements": {
        "q1.z": {"operations": {"cz_snz": "cz_snz_pulse"}},
        "q1.xy": {"operations": {"x180_DragCosine": "x180_pulse"}},
    },
    "pulses": {
        "cz_snz_pulse": {"length": len(TRUTH_SAMPLES), "operation": "control",
                         "waveforms": {"single": "cz_snz_wf"}},
        "x180_pulse": {"length": 4, "operation": "control",
                       "waveforms": {"I": "x180_i", "Q": "x180_q"}},
    },
    "waveforms": {
        "cz_snz_wf": {"type": "arbitrary", "samples": TRUTH_SAMPLES},
        "x180_i": {"type": "arbitrary", "samples": [MARKER] * 4},
        "x180_q": {"type": "arbitrary", "samples": [0.0] * 4},
    },
}


def _client(tmp_path, *, with_config: bool):
    _chip(tmp_path / "quam_state")
    app = create_app(testing=True, instance_path=str(tmp_path / "_i"))
    c = app.test_client()
    c.post("/load", data={"folder": str(tmp_path / "quam_state")})
    ctx = app.config["contexts"][app.config["active_context"]]
    store = ctx["store"]
    if with_config:
        store.generated_config = CONFIG
        store.generated_config_meta = {"at": "2026-09-16T01:52:34+00:00",
                                       "versions": {}, "warnings": []}
    return app, c, store


@pytest.fixture
def plain(tmp_path):
    return _client(tmp_path, with_config=False)


@pytest.fixture
def configured(tmp_path):
    return _client(tmp_path, with_config=True)


class TestTheReasonIsMachineReadable:
    def test_synth_says_WHICH_kind_of_failure_it_is(self, plain):
        """"this class is not in the catalog" and "this class is, but a
        parameter is wrong" have different remedies, and the only thing that
        told them apart was the wording of an English sentence."""
        _, c, _ = plain
        r = c.post("/api/pulse/synth", json={"path": SNZ_PATH, "params": {}})
        j = r.get_json()
        assert j["ok"] is False
        assert j["reason"] == "unknown_class"
        assert j["qclass"] == LAB_CLASS
        assert LAB_CLASS in (j["error"] or "")

    def test_a_class_SM_knows_carries_no_reason(self, plain):
        _, c, _ = plain
        j = c.post("/api/pulse/synth",
                   json={"path": KNOWN_PATH, "params": {}}).get_json()
        assert j["ok"] is True
        assert j.get("reason") is None


class TestItPlotsFromTheLabsOwnConfig:
    def test_the_waveform_is_the_config_s_and_is_labelled_as_such(self, configured):
        _, c, _ = configured
        body = c.get("/pulse/detail",
                     query_string={"path": SNZ_PATH}).get_data(as_text=True)
        assert "waveform from the generated config" in body
        assert "lab" in body                       # whose code drew it
        # the samples on screen ARE the config's
        assert "0.33" in body
        # and the page no longer claims the preview is unavailable
        assert "the synthesized preview is unavailable" not in body

    def test_the_error_line_is_answered_not_left_behind(self, configured):
        """A reason that has been acted on must not keep shouting: the plot is
        there, so the bar carries provenance, not a complaint."""
        _, c, _ = configured
        body = c.get("/pulse/detail",
                     query_string={"path": SNZ_PATH}).get_data(as_text=True)
        assert "unrecognized pulse class" not in body

    def test_a_class_SM_knows_is_untouched_by_any_of_this(self, configured):
        """The fallback must be invisible to every pulse that already worked.

        The config carries this pulse too, under a marker amplitude the synth
        could never produce — so a fallback that fired here would be VISIBLE,
        not merely unobserved."""
        _, c, _ = configured
        body = c.get("/pulse/detail",
                     query_string={"path": KNOWN_PATH}).get_data(as_text=True)
        assert "preview (synthesized)" in body
        assert "generated config" not in body
        assert str(MARKER) not in body            # the synth drew it, not the config


class TestNoConfigIsNotADeadEnd:
    def test_it_names_the_class_and_offers_the_one_action(self, plain):
        _, c, _ = plain
        body = c.get("/pulse/detail",
                     query_string={"path": SNZ_PATH}).get_data(as_text=True)
        assert "unrecognized pulse class" in body
        assert LAB_CLASS in body
        # the door that fixes it, not just a diagnosis
        assert "Generate now" in body
        assert "regenerateThenVerify" in body
        assert "preview (synthesized)" in body     # nothing was invented

    def test_the_route_and_the_page_ask_the_same_lookup(self, configured):
        """ONE ground-truth lookup, two callers — the /api route (which adds
        staleness and a synth-vs-truth comparison) and the detail render."""
        _, c, store = configured
        j = c.get("/api/pulse/ground-truth",
                  query_string={"path": SNZ_PATH}).get_json()
        assert j["ok"] is True
        ys = j["plot"]["traces"][0]["y"]
        assert ys == pytest.approx(TRUTH_SAMPLES)
        # the page renders the same numbers
        body = c.get("/pulse/detail",
                     query_string={"path": SNZ_PATH}).get_data(as_text=True)
        assert str(TRUTH_SAMPLES[1]) in body

    def test_no_comparison_is_claimed_when_there_is_nothing_to_compare(self, configured):
        """SM cannot synthesize this class, so there is no second opinion to
        set against the config — the route must not invent one."""
        _, c, _ = configured
        j = c.get("/api/pulse/ground-truth",
                  query_string={"path": SNZ_PATH}).get_json()
        assert j["comparison"] is None

# ---------------------------------------------------------------------------
# docs/189 -- and it is ready BEFORE anyone clicks
# ---------------------------------------------------------------------------
class TestTheConfigIsWarmedInAdvance:
    """The customer's own question: *"이거 미리할수는 없나?"*

    Generating the config costs a ~13 s subprocess in the lab's env, and the
    result is one cached dict. Paying it once while the user is still looking
    at the chip beats paying it the moment they click the pulse they wanted --
    but ONLY on a chip that needs it, and never in a loop.
    """

    def test_a_chip_with_a_lab_class_asks_for_one(self, tmp_path, monkeypatch):
        from quam_state_manager.web import routes as R
        _, _, store = _client(tmp_path, with_config=False)
        assert R._chip_needs_generated_config(store) is True

    def test_a_chip_of_quam_classes_only_pays_nothing(self, tmp_path):
        """The difference between a warm that helps one lab and a warm that
        taxes every other user."""
        from quam_state_manager.web import routes as R
        folder = tmp_path / "plainchip"
        folder.mkdir()
        import json as _json
        (folder / "state.json").write_text(_json.dumps({
            "qubits": {"q1": {"id": "q1", "f_01": 6.1e9, "xy": {
                "RF_frequency": 6.1e9, "operations": {"x180_DragCosine": {
                    "__class__": ("quam_builder.architecture.superconducting"
                                  ".components.pulses.DragCosinePulse"),
                    "amplitude": 0.3, "length": 40, "alpha": -0.05,
                    "anharmonicity": 2.0e8, "axis_angle": 0.0,
                    "detuning": 0.0, "digital_marker": None}}}}},
            "active_qubit_names": ["q1"],
        }), encoding="utf-8")
        (folder / "wiring.json").write_text(_json.dumps(
            {"network": {"host": "1.2.3.4"}, "wiring": {"qubits": {}}}),
            encoding="utf-8")
        app = create_app(testing=True, instance_path=str(tmp_path / "_i2"))
        c = app.test_client()
        c.post("/load", data={"folder": str(folder)})
        store = app.config["contexts"][app.config["active_context"]]["store"]
        assert R._chip_needs_generated_config(store) is False

    def test_it_refuses_to_run_without_an_env_the_user_picked(self, tmp_path):
        """SM never chooses the environment; a missing one is said, not
        guessed."""
        from quam_state_manager.web import routes as R
        app, _, _ = _client(tmp_path, with_config=False)
        ctx = app.config["contexts"][app.config["active_context"]]
        with app.app_context():
            assert R._warm_generated_config_async(
                ctx, app.instance_path) == "no-env"

    def test_a_fresh_config_is_not_regenerated(self, tmp_path):
        from quam_state_manager.web import routes as R
        app, _, store = _client(tmp_path, with_config=True)
        ctx = app.config["contexts"][app.config["active_context"]]
        # the cached config's basis IS the current state -> provably fresh
        store.generated_config_meta["basis_hash"] = R._config_state_hash(store)
        with app.app_context():
            assert R._warm_generated_config_async(
                ctx, app.instance_path) == "already-fresh"

    def test_a_failed_env_is_not_retried_at_the_same_state(self, tmp_path,
                                                           monkeypatch):
        """A broken env must cost one subprocess, not one per page view -- and
        the latch is keyed on the chip's CONTENT, so a fix re-arms it."""
        from quam_state_manager.web import routes as R
        from quam_state_manager.core import config_generator
        app, _, store = _client(tmp_path, with_config=False)
        ctx = app.config["contexts"][app.config["active_context"]]
        monkeypatch.setattr(config_generator, "get_selected_env",
                            lambda _inst: "python")
        calls = []

        def _boom(python_path, folder):
            calls.append(folder)
            return {"ok": False, "error": "env exploded"}

        monkeypatch.setattr(config_generator, "run_config_preview", _boom)
        with app.app_context():
            assert R._warm_generated_config_async(ctx, app.instance_path) == "started"
            for _ in range(80):                      # let the daemon finish
                if calls:
                    break
                time.sleep(0.05)
            for _ in range(80):
                with R._cfg_warm_lock:
                    done = not R._cfg_warm_inflight
                if done:
                    break
                time.sleep(0.05)
            assert len(calls) == 1
            assert R._warm_generated_config_async(
                ctx, app.instance_path) == "failed-before"
            assert len(calls) == 1                   # nothing ran a second time

    def test_two_activations_never_spawn_two_subprocesses(self, tmp_path,
                                                          monkeypatch):
        """Opening the same chip twice in quick succession -- a reload, a
        second window, the LRU handing the context back -- must cost ONE ~13 s
        subprocess, not one each. The mutation sweep found this unguarded."""
        import threading as _th
        from quam_state_manager.web import routes as R
        from quam_state_manager.core import config_generator
        app, _, _ = _client(tmp_path, with_config=False)
        ctx = app.config["contexts"][app.config["active_context"]]
        monkeypatch.setattr(config_generator, "get_selected_env",
                            lambda _inst: "python")
        started, release, calls = _th.Event(), _th.Event(), []

        def _slow(python_path, folder):
            calls.append(folder)
            started.set()
            release.wait(10)               # stand in for the real 13 s
            return {"ok": False, "error": "done"}

        monkeypatch.setattr(config_generator, "run_config_preview", _slow)
        with app.app_context():
            assert R._warm_generated_config_async(ctx, app.instance_path) == "started"
            assert started.wait(10)        # the first one is genuinely in flight
            # the second caller must be told so, and must NOT run
            assert R._warm_generated_config_async(ctx, app.instance_path) == "running"
            release.set()
            for _ in range(100):
                with R._cfg_warm_lock:
                    if not R._cfg_warm_inflight:
                        break
                time.sleep(0.05)
        assert len(calls) == 1

    def test_re_opening_a_cached_chip_still_warms(self, tmp_path, monkeypatch):
        """`_activate_quam` returns EARLY for a chip already in the LRU, and
        re-opening one is the commonest way to reach it. A warm placed only on
        the cold-build path would almost never run -- measured: it did not, on
        a real server, and this pin is that measurement."""
        from quam_state_manager.web import routes as R
        calls = []
        monkeypatch.setattr(R, "_maybe_warm_generated_config",
                            lambda ctx, inst: calls.append(ctx.get("path")))
        _chip(tmp_path / "quam_state")
        app = create_app(testing=True, instance_path=str(tmp_path / "_i3"))
        c = app.test_client()
        folder = str(tmp_path / "quam_state")
        c.post("/load", data={"folder": folder})     # cold build
        assert len(calls) == 1
        c.post("/load", data={"folder": folder})     # served from the LRU
        assert len(calls) == 2, "the cached activation path skipped the warm"

    def test_the_staleness_hash_is_memoized_on_the_stores_own_counters(
            self, tmp_path):
        """33 ms of canonical-JSON hashing per render of a lab-class pulse.
        The memo may never outlive a change to the thing it hashes."""
        from quam_state_manager.web import routes as R
        _, _, store = _client(tmp_path, with_config=False)
        first = R._config_state_hash(store)
        assert R._config_state_hash(store) is first        # same object: memoized
        store.mutation_seq += 1
        assert R._config_state_hash(store) is not first    # recomputed
