"""docs/2xx -- Pulses adapt to a pulse class SM has never seen, with no SM change.

User queue item 1 (2026-09-25): when a lab adds a CZ pulse whose waveform class
SM does not know, SM itself had to be modified. The pins here hold the four
seams that make it adaptive instead:

1. DISCOVERY -- the schema probe's pulse roster also carries the lab's own
   Pulse subclasses (found by the subclass closure over what the chip's own
   classes -- and any module the user named -- imported; never a blind
   package walk), and the manifest records the out-of-env source files it read.
2. FRESHNESS -- a cached manifest is served only while those files still have
   the stats the probe saw (an edit to an editable lab package never moves the
   env signature, so without this a class the lab ADDED stays invisible).
3. THE WAVEFORM -- drawn by the class itself (``calculate_waveform()`` in the
   selected env), cached in RAM under the canonical field values, validated on
   every read against the same source stats. A cross-check pins that for a
   class SM DOES mirror, the lab route and SM's own synth agree sample for
   sample, so the route is drawing the same thing generate_config() does.
4. THE PAGE -- the inspector types a lab class's fields from its own schema,
   says where every curve came from, the row thumbnail prefers the class's own
   drawing at the CURRENT values over a possibly older generated config, and
   the create form draws a class that has no pulse yet.

The subprocess tests run the REAL scripts under this interpreter (the cqt env
carries quam); they skip where quam is absent.
"""

from __future__ import annotations

import json
import os
import random
import subprocess
import sys
import time
from pathlib import Path

import pytest

from quam_state_manager.core import lab_waveform, pulse_catalog, state_env_schema
from quam_state_manager.web.app import create_app

GEN = Path(__file__).resolve().parents[1] / "quam_state_manager" / "generator"

try:  # the subprocess tests need a quam the scripts can import
    import quam.components.pulses  # noqa: F401
    HAVE_QUAM = True
except Exception:  # noqa: BLE001
    HAVE_QUAM = False

needs_quam = pytest.mark.skipif(not HAVE_QUAM, reason="quam not importable")

LAB_MOD = "smlab_scratch.cz_pulses"
LAB_CLASS = LAB_MOD + ".WobbleCZPulse"
NEW_MOD = "smlab_scratch.more_pulses"
NEW_CLASS = NEW_MOD + ".RampCZPulse"

WOBBLE_SRC = '''"""A synthetic lab CZ pulse class SM has never seen."""
import numpy as np
from quam.components.pulses import Pulse
from quam.core import quam_dataclass


@quam_dataclass
class WobbleCZPulse(Pulse):
    amplitude: float
    flat_length: int
    ramp: int = 8
    ripple: float = 0.1
    cycles: float = 2.0
    length: int = "#./inferred_length"

    @property
    def inferred_length(self) -> int:
        n = 2 * self.ramp + self.flat_length
        return int(-(-n // 4) * 4)

    def waveform_function(self):
        n = self.inferred_length
        t = np.arange(n)
        env = np.ones(n)
        r = self.ramp
        if r:
            ramp = 0.5 * (1 - np.cos(np.pi * np.arange(r) / r))
            env[:r] = ramp
            env[n - r:] = ramp[::-1]
        return self.amplitude * env * (
            1 + self.ripple * np.cos(2 * np.pi * self.cycles * t / n))
'''

RAMP_SRC = '''import numpy as np
from quam.components.pulses import Pulse
from quam.core import quam_dataclass


@quam_dataclass
class RampCZPulse(Pulse):
    amplitude: float
    length: int = 16

    def waveform_function(self):
        return self.amplitude * np.linspace(0.0, 1.0, self.length)
'''


def _wobble(amplitude, flat_length, ramp=8, ripple=0.1, cycles=2.0):
    """An INDEPENDENT numpy re-statement of the fixture class's formula -- the
    cross-check that the subprocess really ran the class, not a stand-in."""
    import numpy as np
    n = 2 * ramp + flat_length
    n = int(-(-n // 4) * 4)
    t = np.arange(n)
    env = np.ones(n)
    if ramp:
        r = 0.5 * (1 - np.cos(np.pi * np.arange(ramp) / ramp))
        env[:ramp] = r
        env[n - ramp:] = r[::-1]
    return amplitude * env * (1 + ripple * np.cos(2 * np.pi * cycles * t / n))


@pytest.fixture
def labpkg(tmp_path, monkeypatch):
    """A lab package on PYTHONPATH (never a customer folder)."""
    root = tmp_path / "labpkg"
    pkg = root / "smlab_scratch"
    pkg.mkdir(parents=True)
    (pkg / "__init__.py").write_text("", encoding="utf-8")
    (pkg / "cz_pulses.py").write_text(WOBBLE_SRC, encoding="utf-8")
    (pkg / "more_pulses.py").write_text(RAMP_SRC, encoding="utf-8")
    monkeypatch.setenv("PYTHONPATH", str(root))
    lab_waveform.MEMO.clear()
    with lab_waveform._SLOT_LOCK:
        lab_waveform._SLOT_FILES.clear()
    yield pkg
    lab_waveform.MEMO.clear()


def _probe(tmp_path, classes, modules=()):
    inp = tmp_path / "c.json"
    out = tmp_path / "r.json"
    inp.write_text(json.dumps({"classes": list(classes), "pulse_roster": True,
                               "pulse_modules": list(modules)}), encoding="utf-8")
    subprocess.run([sys.executable, "-B", str(GEN / "probe_state_schema.py"),
                    "--classes", str(inp), "--out", str(out)],
                   capture_output=True, text=True, timeout=300, env=os.environ.copy())
    return json.loads(out.read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# 1. discovery
# ---------------------------------------------------------------------------

@needs_quam
class TestTheProbeFindsTheLabsOwnClasses:
    def test_a_chip_class_reaches_the_roster_with_its_schema(self, tmp_path, labpkg):
        res = _probe(tmp_path, [LAB_CLASS])
        assert res["status"] == "ok", res.get("error")
        rec = res["pulse_roster"].get("WobbleCZPulse")
        assert rec is not None, "the subclass closure missed an imported lab class"
        assert rec["lab"] is True
        assert rec["canonical"] == LAB_CLASS
        f = rec["fields"]
        assert f["amplitude"]["has_default"] is False       # required
        assert f["flat_length"]["type"]["base"] == "int"
        assert f["ramp"]["has_default"] is True and f["ramp"]["default"] == 8
        # quam's own classes keep their non-lab records
        assert not res["pulse_roster"]["SquarePulse"].get("lab")

    def test_a_module_nobody_imports_is_not_walked_blind(self, tmp_path, labpkg):
        res = _probe(tmp_path, [LAB_CLASS])
        assert "RampCZPulse" not in res["pulse_roster"]

    def test_naming_the_module_reaches_it(self, tmp_path, labpkg):
        res = _probe(tmp_path, [LAB_CLASS], modules=[NEW_MOD, "no_such_pkg.x"])
        assert res["pulse_roster"]["RampCZPulse"]["lab"] is True
        assert res["pulse_modules"][NEW_MOD] == "ok"
        assert res["pulse_modules"]["no_such_pkg.x"].startswith("error:")

    def test_the_manifest_names_the_lab_files_it_read(self, tmp_path, labpkg):
        res = _probe(tmp_path, [LAB_CLASS])
        src = {os.path.normcase(k): v for k, v in res["sources"].items()}
        f = os.path.normcase(str(labpkg / "cz_pulses.py"))
        assert f in src
        st = os.stat(labpkg / "cz_pulses.py")
        assert src[f] == [st.st_mtime_ns, st.st_size]
        # nothing from inside the interpreter's own install
        assert not any(os.path.normcase(sys.prefix) in k for k in src)


# ---------------------------------------------------------------------------
# 2. freshness of the cached manifest
# ---------------------------------------------------------------------------

class TestTheCachedManifestIsValidatedOnRead:
    def _seed(self, inst, py, sources, modules=None):
        ver = state_env_schema._env_versions
        entry = {"format": state_env_schema.SCHEMA_FORMAT, "versions": {"quam": "x"},
                 "signature": "sig", "classes": {"a.B": {"importable": True}},
                 "pulse_roster": {}, "pulse_modules": modules or {},
                 "sources": sources}
        state_env_schema._save_cache(inst, {py: entry})
        return ver

    def _store(self):
        class S:
            state = {"x": {"__class__": "a.B"}}
            import threading
            _lock = threading.RLock()
        return S()

    def test_an_edit_to_a_lab_file_is_a_miss(self, tmp_path, monkeypatch):
        inst = tmp_path / "inst"
        inst.mkdir()
        lab = tmp_path / "lab.py"
        lab.write_text("x = 1\n", encoding="utf-8")
        st = os.stat(lab)
        py = str(tmp_path / "python.exe")
        monkeypatch.setattr(state_env_schema, "_env_signature", lambda p: "sig")
        self._seed(inst, py, {str(lab): [st.st_mtime_ns, st.st_size]})
        m = state_env_schema.manifest_for_store(self._store(), py, inst, cached_only=True)
        assert m is not None and m["sources"]
        lab.write_text("x = 1\nclass New: pass\n", encoding="utf-8")
        assert state_env_schema.manifest_for_store(
            self._store(), py, inst, cached_only=True) is None
        # and a deleted lab file is a miss too, never an answer from nothing
        lab.unlink()
        assert not state_env_schema.sources_fresh({str(lab): [1, 1]})

    def test_a_newly_named_module_is_a_miss(self, tmp_path, monkeypatch):
        inst = tmp_path / "inst"
        inst.mkdir()
        py = str(tmp_path / "python.exe")
        monkeypatch.setattr(state_env_schema, "_env_signature", lambda p: "sig")
        self._seed(inst, py, {}, modules={"lab.a": "ok"})
        state_env_schema.save_pulse_modules(inst, ["lab.a"])
        assert state_env_schema.manifest_for_store(
            self._store(), py, inst, cached_only=True) is not None
        state_env_schema.save_pulse_modules(inst, ["lab.a", "lab.b"])
        assert state_env_schema.manifest_for_store(
            self._store(), py, inst, cached_only=True) is None

    def test_a_removed_module_is_a_miss(self, tmp_path, monkeypatch):
        # the entry imported lab.a AND lab.b; the user removed lab.b -- a cold
        # probe would no longer offer lab.b's classes, so neither may the cache
        inst = tmp_path / "inst"
        inst.mkdir()
        py = str(tmp_path / "python.exe")
        monkeypatch.setattr(state_env_schema, "_env_signature", lambda p: "sig")
        self._seed(inst, py, {}, modules={"lab.a": "ok", "lab.b": "ok"})
        state_env_schema.save_pulse_modules(inst, ["lab.a", "lab.b"])
        assert state_env_schema.manifest_for_store(
            self._store(), py, inst, cached_only=True) is not None
        state_env_schema.save_pulse_modules(inst, ["lab.a"])
        assert state_env_schema.manifest_for_store(
            self._store(), py, inst, cached_only=True) is None

    def test_the_probe_path_reprobes_instead_of_serving_it(self, tmp_path, monkeypatch):
        inst = tmp_path / "inst"
        inst.mkdir()
        lab = tmp_path / "lab.py"
        lab.write_text("x = 1\n", encoding="utf-8")
        st = os.stat(lab)
        py = tmp_path / "python.exe"
        py.write_text("", encoding="utf-8")
        monkeypatch.setattr(state_env_schema, "_env_versions", lambda p: {"quam": "x"})
        self._seed(inst, str(py), {str(lab): [st.st_mtime_ns, st.st_size]})
        runs = []

        def fake(argv, work_dir, timeout, outcome, **kw):
            runs.append(argv)
            outcome.update(ok=True, result={"classes": {"a.B": {"importable": True}},
                                            "pulse_roster": {"New": {}}, "sources": {}})
            return outcome
        monkeypatch.setattr(state_env_schema, "_run_script_outcome", fake)
        r1 = state_env_schema.probe_state_schema(str(py), ["a.B"], inst)
        assert r1["cached"] is True and not runs
        lab.write_text("x = 2 # edited\n", encoding="utf-8")
        r2 = state_env_schema.probe_state_schema(str(py), ["a.B"], inst)
        assert r2["cached"] is False and len(runs) == 1
        assert "New" in r2["pulse_roster"]

    def test_module_names_are_validated_before_they_are_imported(self, tmp_path):
        assert state_env_schema.valid_module_name("my_lab.cz_pulses")
        for bad in ("", "1abc", "a..b", "os; rm", "a.b/c", "x" * 201):
            assert not state_env_schema.valid_module_name(bad)
        saved = state_env_schema.save_pulse_modules(tmp_path, ["ok.mod", "bad mod", "ok.mod"])
        assert saved == ["ok.mod"]
        assert state_env_schema.load_pulse_modules(tmp_path) == ["ok.mod"]


# ---------------------------------------------------------------------------
# 3. the waveform, drawn by the class itself
# ---------------------------------------------------------------------------

@needs_quam
class TestTheClassDrawsItsOwnWaveform:
    def test_it_is_the_classes_formula(self, labpkg):
        rec = lab_waveform.draw(sys.executable, [(LAB_CLASS, {
            "amplitude": 0.2, "flat_length": 40, "length": "#./inferred_length",
            "id": "cz"})])[0]
        assert rec["ok"], rec
        want = _wobble(0.2, 40)
        assert rec["length"] == len(want) == 56
        assert max(abs(a - b) for a, b in zip(rec["i"], want)) < 1e-12
        assert rec["q"] is None and rec["iq"] is False

    def test_it_agrees_with_SMs_own_synth_for_a_class_SM_mirrors(self, labpkg):
        """The executable cross-check that `calculate_waveform()` standalone is
        the SAME curve SM's golden-pinned mirror draws -- for a class both know."""
        from quam_state_manager.core import waveform_synth
        params = {"amplitude": 0.3, "length": 40, "alpha": -0.05,
                  "anharmonicity": 2.0e8, "axis_angle": 0.3, "detuning": 0.0}
        rec = lab_waveform.draw(sys.executable, [(
            "quam_builder.architecture.superconducting.components.pulses.DragCosinePulse",
            params)])[0]
        assert rec["ok"], rec
        syn = waveform_synth.synthesize("DragCosinePulse", params)
        assert syn["ok"]
        assert len(rec["i"]) == len(syn["i"]) == 40
        assert max(abs(a - b) for a, b in zip(rec["i"], syn["i"])) < 1e-12
        assert max(abs(a - b) for a, b in zip(rec["q"], syn["q"])) < 1e-12

    def test_a_second_ask_is_RAM(self, labpkg, monkeypatch):
        p = {"amplitude": 0.1, "flat_length": 20}
        first = lab_waveform.draw(sys.executable, [(LAB_CLASS, p)])[0]
        assert first["ok"] and first["cached"] is False
        calls = []
        real = lab_waveform._run
        monkeypatch.setattr(lab_waveform, "_run",
                            lambda *a, **k: calls.append(a) or real(*a, **k))
        again = lab_waveform.draw(sys.executable, [(LAB_CLASS, dict(p))])[0]
        assert again["cached"] is True and not calls
        assert again["i"] == first["i"]

    def test_editing_the_lab_module_is_a_miss_not_a_stale_curve(self, labpkg):
        p = {"amplitude": 0.1, "flat_length": 20}
        a = lab_waveform.draw(sys.executable, [(LAB_CLASS, p)])[0]
        src = (labpkg / "cz_pulses.py").read_text(encoding="utf-8")
        # the lab doubles its ripple default -- same fields, a different curve
        (labpkg / "cz_pulses.py").write_text(
            src.replace("ripple: float = 0.1", "ripple: float = 0.2"), encoding="utf-8")
        b = lab_waveform.draw(sys.executable, [(LAB_CLASS, p)])[0]
        assert b["cached"] is False
        assert max(abs(x - y) for x, y in zip(b["i"], _wobble(0.1, 20, ripple=0.2))) < 1e-12
        assert b["i"] != a["i"]

    def test_the_classes_own_refusal_is_the_answer(self, labpkg):
        rec = lab_waveform.draw(sys.executable, [(LAB_CLASS, {"flat_length": 20})])[0]
        assert rec["ok"] is False
        assert "missing required field: amplitude" in rec["error"]

    def test_one_subprocess_for_many_pulses(self, labpkg, monkeypatch):
        calls = []
        real = lab_waveform._run
        monkeypatch.setattr(lab_waveform, "_run",
                            lambda *a, **k: calls.append(a) or real(*a, **k))
        recs = lab_waveform.draw(sys.executable, [
            (LAB_CLASS, {"amplitude": 0.1, "flat_length": 8}),
            (LAB_CLASS, {"amplitude": 0.2, "flat_length": 8}),
            ("quam.components.pulses.SquarePulse", {"amplitude": 0.1, "length": 12}),
        ])
        assert len(calls) == 1
        assert [r["ok"] for r in recs] == [True, True, True]
        assert recs[2]["kind"] == "constant" and len(recs[2]["i"]) == 12


class TestTheWaveformCacheContract:
    """Without a subprocess: the memo is keyed on CONTENT and validated on read."""

    @pytest.fixture
    def fake_run(self, tmp_path, monkeypatch):
        lab = tmp_path / "lab.py"
        lab.write_text("v = 1\n", encoding="utf-8")
        py = tmp_path / "python.exe"
        py.write_text("", encoding="utf-8")
        calls = []

        def run(python_path, items):
            calls.append(items)
            st = os.stat(lab)
            ver = int(lab.read_text(encoding="utf-8").split("=")[1])
            out = []
            for it in items:
                a = float(it["params"].get("amplitude", 0))
                out.append({"ok": True, "i": [a * ver] * 4, "q": None, "iq": False,
                            "kind": "arbitrary", "length": 4, "canonical": it["qclass"],
                            "dropped": [], "warnings": [], "error": None})
            return {"ok": True, "error": None, "items": out,
                    "sources": {str(lab): [st.st_mtime_ns, st.st_size]}}
        monkeypatch.setattr(lab_waveform, "_run", run)
        monkeypatch.setattr(lab_waveform, "_env_sig", lambda p: "sig")
        lab_waveform.MEMO.clear()
        with lab_waveform._SLOT_LOCK:
            lab_waveform._SLOT_FILES.clear()
        yield str(py), lab, calls
        lab_waveform.MEMO.clear()

    def test_no_env_is_named_never_guessed(self):
        rec = lab_waveform.draw(None, [("a.B", {})])[0]
        assert rec["ok"] is False and rec["reason"] == "no-env"

    def test_a_list_render_never_spawns(self, fake_run):
        py, _, calls = fake_run
        rec = lab_waveform.draw(py, [("a.B", {"amplitude": 1})], spawn=False)[0]
        assert rec["reason"] == "not-drawn" and not calls

    def test_a_failure_to_run_is_not_cached(self, fake_run, monkeypatch):
        py, _, calls = fake_run
        monkeypatch.setattr(lab_waveform, "_run",
                            lambda p, items: {"ok": False, "error": "timed out",
                                              "items": [], "sources": {}})
        rec = lab_waveform.draw(py, [("a.B", {"amplitude": 1})])[0]
        assert rec["reason"] == "run-failed"
        assert lab_waveform.cached(py, "a.B", {"amplitude": 1}) is None

    def test_randomized_events_never_serve_a_stale_curve(self, fake_run):
        """A random sequence of field edits, lab-file edits, env changes and
        reads: every served drawing equals a cold recompute at that moment."""
        py, lab, _ = fake_run
        rng = random.Random(20260926)
        ver = 1
        for step in range(300):
            ev = rng.random()
            if ev < 0.15:
                ver += 1
                # a same-size edit is still a different mtime; force the
                # mtime forward so a fast filesystem cannot hide it
                lab.write_text(f"v = {ver}\n", encoding="utf-8")
                t = time.time_ns() + step * 1_000_000
                os.utime(lab, ns=(t, t))
            amp = rng.choice([0.1, 0.2, 0.3])
            spawn = rng.random() < 0.7
            rec = lab_waveform.draw(py, [("a.B", {"amplitude": amp})], spawn=spawn)[0]
            if rec.get("ok"):
                assert rec["i"] == [amp * ver] * 4, (step, rec, ver)
            else:
                assert rec["reason"] == "not-drawn"
                assert not spawn

    def test_the_payload_draws_like_a_synth_payload(self):
        from quam_state_manager.core.waveform_synth import sparkline_svg
        rec = {"ok": True, "i": [0, 1, 0], "q": None, "iq": False,
               "kind": "arbitrary", "length": 3, "warnings": ["detached"]}
        p = lab_waveform.payload_for_plot(rec)
        assert p["ok"] and p["x_ns"] == [0, 1, 2] and p["warnings"] == ["detached"]
        assert sparkline_svg(p)
        assert lab_waveform.payload_for_plot({"ok": False, "error": "x"})["ok"] is False


# ---------------------------------------------------------------------------
# 4. catalog + the page
# ---------------------------------------------------------------------------

LAB_FIELDS = {
    "amplitude": {"type": {"base": "float"}, "has_default": False, "default": None},
    "flat_length": {"type": {"base": "int"}, "has_default": False, "default": None},
    "ramp": {"type": {"base": "int"}, "has_default": True, "default": 8},
    "length": {"type": {"base": "int"}, "has_default": True,
               "default": "#./inferred_length", "default_is_reference": True},
    "id": {"type": {"base": "str"}, "has_default": True, "default": None},
}
ROSTER = {
    "SquarePulse": {"homes": ["quam.components.pulses"],
                    "canonical": "quam.components.pulses.SquarePulse", "fields": {}},
    "WobbleCZPulse": {"homes": [LAB_MOD], "canonical": LAB_CLASS, "lab": True,
                      "readout": False, "fields": LAB_FIELDS},
}


@pytest.fixture
def overlay():
    pulse_catalog.apply_env_overlay(ROSTER)
    yield
    pulse_catalog.apply_env_overlay(None)
    pulse_catalog.apply_chip_classes(None)


class TestTheCatalogOffersTheLabsClass:
    def test_a_lab_roster_class_is_creatable_in_its_own_group(self, overlay):
        spec = pulse_catalog.env_creatable_specs().get("WobbleCZPulse")
        assert spec is not None and spec.group == pulse_catalog.LAB_GROUP
        assert spec.qclass == LAB_CLASS
        assert spec.length_mode == "inferred"
        amp = spec.param("amplitude")
        assert amp.kind == "float" and amp.required
        assert spec.param("ramp").required is False
        assert spec.param("flat_length").kind == "int"

    def test_the_schema_is_found_for_exactly_this_class(self, overlay):
        assert pulse_catalog.adaptive_spec_for(LAB_CLASS).key == "WobbleCZPulse"
        # the same leaf at ANOTHER home is not this class
        assert pulse_catalog.adaptive_spec_for("other_lab.pulses.WobbleCZPulse") is None
        assert pulse_catalog.adaptive_spec_for(None) is None

    def test_the_chip_inventory_answers_when_the_roster_cannot(self):
        pulse_catalog.apply_env_overlay(None)
        pulse_catalog.apply_chip_classes({"mylab.g.SNZ": {
            "importable": True, "canonical": "mylab.g.SNZ",
            "bases": ["quam.components.pulses.Pulse"],
            "fields": {"amplitude": {"type": {"base": "float"}, "has_default": False}}}})
        try:
            spec = pulse_catalog.adaptive_spec_for("mylab.g.SNZ")
            assert spec is not None and spec.param("amplitude").kind == "float"
        finally:
            pulse_catalog.apply_chip_classes(None)


PULSE = "qubits.q1.z.operations.cz_wobble"


def _chip(folder: Path, fields=None) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    body = {"__class__": LAB_CLASS, "amplitude": 0.2, "flat_length": 40,
            "length": "#./inferred_length"}
    body.update(fields or {})
    (folder / "state.json").write_text(json.dumps({
        "qubits": {"q1": {"id": "q1", "f_01": 6.1e9,
                          "xy": {"RF_frequency": 6.1e9, "operations": {}},
                          "z": {"joint_offset": 0.0, "operations": {"cz_wobble": body}}}},
        "active_qubit_names": ["q1"],
    }), encoding="utf-8")
    (folder / "wiring.json").write_text(json.dumps(
        {"network": {"host": "1.2.3.4"}, "wiring": {"qubits": {}}}), encoding="utf-8")
    return folder


@pytest.fixture
def page(tmp_path, monkeypatch, overlay):
    _chip(tmp_path / "quam_state")
    inst = tmp_path / "_i"
    app = create_app(testing=True, instance_path=str(inst))
    c = app.test_client()
    c.post("/load", data={"folder": str(tmp_path / "quam_state")})
    pulse_catalog.apply_env_overlay(ROSTER)
    # the env is selected AFTER the load, so no background probe/generate runs
    py = tmp_path / "python.exe"
    py.write_text("", encoding="utf-8")
    (inst / "config_generator.json").write_text(
        json.dumps({"selected_env_python": str(py)}), encoding="utf-8")
    calls = []

    def run(python_path, items):
        calls.append(items)
        out = []
        for it in items:
            p = it["params"]
            if "amplitude" not in p:
                out.append({"ok": False, "error": "missing required field: amplitude"})
                continue
            a = p["amplitude"]
            assert isinstance(a, float), ("fields must arrive typed", a)
            n = 2 * p.get("ramp", 8) + int(p["flat_length"])
            out.append({"ok": True, "i": [a] * n, "q": None, "iq": False,
                        "kind": "arbitrary", "length": n, "canonical": it["qclass"],
                        "dropped": [], "warnings": ["drawn detached"], "error": None})
        return {"ok": True, "error": None, "items": out, "sources": {}}
    monkeypatch.setattr(lab_waveform, "_run", run)
    monkeypatch.setattr(lab_waveform, "_env_sig", lambda p: "sig")
    lab_waveform.MEMO.clear()
    with lab_waveform._SLOT_LOCK:
        lab_waveform._SLOT_FILES.clear()
    yield app, c, calls
    lab_waveform.MEMO.clear()


class TestThePage:
    def test_the_detail_asks_the_class_and_types_its_fields(self, page):
        _, c, calls = page
        html = c.get(f"/pulse/detail?path={PULSE}").data.decode()
        # typed from the class's own schema, not raw
        assert 'data-param="amplitude" data-kind="float"' in html
        assert 'data-param="flat_length" data-kind="int"' in html
        assert "typed from the class&#39;s own" in html
        assert "Left at the class default" in html and "ramp=8" in html
        data = json.loads(html.split('id="pulse-detail-data" type="application/json">')[1]
                          .split("</script>")[0])
        assert data["pulses"][0]["needs_lab"] is True
        assert not calls                           # the render never spawned

    def test_the_lab_route_draws_and_the_next_render_is_from_RAM(self, page):
        _, c, calls = page
        j = c.post("/api/pulse/lab-waveform", json={"paths": [PULSE]}).get_json()
        r = j["results"][0]
        assert r["ok"] and r["plot"]["ok"] and r["warnings"] == ["drawn detached"]
        assert len(calls) == 1
        html = c.get(f"/pulse/detail?path={PULSE}").data.decode()
        assert "drawn by the class&#39;s own code" in html
        assert "it said: drawn detached" in html
        data = json.loads(html.split('id="pulse-detail-data" type="application/json">')[1]
                          .split("</script>")[0])
        assert data["pulses"][0]["plot_source"] == "lab"
        assert data["pulses"][0]["needs_lab"] is False
        assert len(calls) == 1

    def test_an_edit_is_a_new_key_never_the_old_curve(self, page):
        _, c, calls = page
        c.post("/api/pulse/lab-waveform", json={"paths": [PULSE]})
        r = c.post("/pulse/edit", data={"path": PULSE, "dot_path": PULSE + ".amplitude",
                                        "mode": "value", "value": "0.35"})
        assert r.status_code == 200
        html = c.get(f"/pulse/detail?path={PULSE}").data.decode()
        data = json.loads(html.split('id="pulse-detail-data" type="application/json">')[1]
                          .split("</script>")[0])
        # the 0.2 drawing is NOT served for the 0.35 pulse
        assert data["pulses"][0]["plot_source"] != "lab"
        assert data["pulses"][0]["needs_lab"] is True
        j = c.post("/api/pulse/lab-waveform", json={"paths": [PULSE]}).get_json()
        assert j["results"][0]["plot"]["traces"][0]["y"][0] == 0.35

    def test_uncommitted_values_are_typed_by_the_schema(self, page):
        _, c, calls = page
        j = c.post("/api/pulse/lab-waveform",
                   json={"paths": [PULSE], "params": {"amplitude": "0.5"}}).get_json()
        assert j["results"][0]["ok"], j
        assert calls[-1][0]["params"]["amplitude"] == 0.5

    def test_the_create_form_draws_a_class_with_no_pulse_yet(self, page):
        _, c, calls = page
        j = c.post("/api/pulse/lab-waveform", json={
            "qclass": LAB_CLASS, "params": {"amplitude": "0.3", "flat_length": "24"}}).get_json()
        r = j["results"][0]
        assert r["ok"], r
        assert calls[-1][0]["params"] == {"amplitude": 0.3, "flat_length": 24}
        bad = c.post("/api/pulse/lab-waveform", json={
            "qclass": LAB_CLASS, "params": {"flat_length": "24"}}).get_json()["results"][0]
        assert bad["ok"] is False and bad["error"] == "amplitude: required"

    def test_the_create_form_lists_the_lab_class(self, page):
        _, c, _ = page
        html = c.get("/pulse/new").data.decode()
        cat = json.loads(html.split('id="pulse-catalog-data" type="application/json">')[1]
                         .split("</script>")[0])
        assert "WobbleCZPulse" in cat
        assert cat["WobbleCZPulse"]["group"] == pulse_catalog.LAB_GROUP
        assert cat["WobbleCZPulse"]["env_only"] is True
        assert "Your own pulse classes live in another module?" in html

    def test_the_row_thumbnail_prefers_the_class_at_the_current_values(self, page):
        app, c, calls = page
        ctx = app.config["contexts"][app.config["active_context"]]
        # a generated config carrying an OLDER shape of this pulse
        ctx["store"].generated_config = {
            "elements": {"q1.z": {"operations": {"cz_wobble": "p"}}},
            "pulses": {"p": {"length": 3, "operation": "control",
                             "waveforms": {"single": "w"}}},
            "waveforms": {"w": {"type": "arbitrary", "samples": [0.0, 0.9, 0.0]}}}
        ctx["store"].generated_config_meta = {"at": "t0"}
        html = c.get("/pulses").data.decode()
        assert "pulse-spark-lab" in html and "Waveform from the generated config" in html
        c.post("/api/pulse/lab-waveform", json={"paths": [PULSE]})
        html = c.get("/pulses").data.decode()
        assert "at the current field values" in html
        assert "Waveform from the generated config" not in html

    def test_naming_a_module(self, page, monkeypatch):
        app, c, _ = page
        kicked = []
        from quam_state_manager.web import routes
        monkeypatch.setattr(routes, "_kick_env_reprobe", lambda *a: kicked.append(a))
        r = c.post("/pulse/class-modules", data={"action": "add", "module": "my_lab.cz"})
        html = r.data.decode()
        assert "my_lab.cz" in html and kicked
        assert state_env_schema.load_pulse_modules(app.instance_path) == ["my_lab.cz"]
        r = c.post("/pulse/class-modules", data={"action": "add", "module": "not a module"})
        assert "is not a Python module name" in r.data.decode()
        assert state_env_schema.load_pulse_modules(app.instance_path) == ["my_lab.cz"]
        c.post("/pulse/class-modules", data={"action": "remove", "module": "my_lab.cz"})
        assert state_env_schema.load_pulse_modules(app.instance_path) == []


class TestTheAttachedSchemaIsValidatedOnEveryRead:
    """verifier round (docs/2xx): the manifest the STORE holds -- what the
    detail and edit forms type fields from -- is checked on every surface
    that reads it, and a module named mid-probe is never left unread."""

    @pytest.fixture
    def held(self, page, monkeypatch, tmp_path):
        app, c, calls = page
        from quam_state_manager.web import routes
        src = tmp_path / "lab_src.py"
        src.write_text("x = 1\n", encoding="utf-8")
        st = os.stat(src)
        manifest = {"sources": {str(src): [st.st_mtime_ns, st.st_size]},
                    "pulse_modules": {}}
        monkeypatch.setattr(routes, "_live_env_manifest", lambda store: manifest)
        kicked = []
        monkeypatch.setattr(routes, "_kick_env_reprobe", lambda *a: kicked.append(a))
        routes._lab_reprobe_tried.clear()
        yield app, c, manifest, src, kicked
        routes._lab_reprobe_tried.clear()

    def test_a_fresh_schema_says_typed(self, held):
        _, c, _, _, kicked = held
        html = c.get(f"/pulse/detail?path={PULSE}").data.decode()
        assert "typed from the class&#39;s own" in html
        assert "data-schema-stale" not in html and not kicked

    def test_a_lab_edit_makes_the_detail_say_so_and_re_read(self, held):
        _, c, _, src, kicked = held
        src.write_text("x = 1\nskew = 0.0\n", encoding="utf-8")   # size moves
        html = c.get(f"/pulse/detail?path={PULSE}").data.decode()
        assert 'data-schema-stale="code"' in html
        assert "typed from the class&#39;s own" not in html      # no stale claim
        assert len(kicked) == 1                                   # the detail kicked it
        c.get(f"/pulse/detail?path={PULSE}")
        assert len(kicked) == 1                                   # once per state

    def test_the_list_page_kicks_the_re_read_too(self, held):
        _, c, _, src, kicked = held
        src.write_text("x = 22\n", encoding="utf-8")
        c.get("/pulses")
        assert len(kicked) == 1

    def test_a_named_module_the_schema_never_imported_is_stale(self, held):
        app, c, manifest, _, kicked = held
        state_env_schema.save_pulse_modules(app.instance_path, ["my_lab.third"])
        j = c.get("/pulse/schema-status").get_json()
        assert j["stale"] and j["reason"] == "modules" and kicked
        manifest["pulse_modules"] = {"my_lab.third": "ok"}
        j = c.get("/pulse/schema-status").get_json()
        assert j["stale"] is False


def test_a_module_named_while_a_probe_runs_is_read_by_a_rerun(page, monkeypatch):
    """The verifier's race: the running probe read the module set when it
    started, so the module named during it must earn a second probe."""
    import threading
    from quam_state_manager.web import routes
    app, c, _ = page
    inst = app.instance_path
    store = app.config["contexts"][app.config["active_context"]]["store"]
    started, release = threading.Event(), threading.Event()
    reads = []

    def fake_probe(python_path, classes, instance_path=None, **kw):
        mods = state_env_schema.load_pulse_modules(instance_path)
        reads.append(mods)
        started.set()
        if len(reads) == 1:
            release.wait(10)
        return {"ok": True, "pulse_modules": {m: "ok" for m in mods}}
    monkeypatch.setattr(state_env_schema, "probe_state_schema", fake_probe)
    attached = []
    monkeypatch.setattr(routes, "_attach_probe_result",
                        lambda s, i, f, p, res: attached.append(res["pulse_modules"]))
    state_env_schema.save_pulse_modules(inst, ["lab.more"])
    routes._kick_env_reprobe(store, inst, "folder")
    assert started.wait(10)
    state_env_schema.save_pulse_modules(inst, ["lab.more", "lab.third"])
    routes._kick_env_reprobe(store, inst, "folder")      # finds the key in flight
    release.set()
    deadline = time.time() + 10
    while time.time() < deadline and len(attached) < 2:
        time.sleep(0.05)
    assert reads == [["lab.more"], ["lab.more", "lab.third"]]
    assert attached[-1] == {"lab.more": "ok", "lab.third": "ok"}
    deadline = time.time() + 5
    while time.time() < deadline and routes._schema_warm_inflight:
        time.sleep(0.05)
    assert not routes._schema_rerun_pending


class TestTheCreateDrawNeverImportsAClientPath:
    def test_an_unread_class_is_refused_with_a_reason(self, page):
        _, c, calls = page
        for q in ("this.s", "smlab_scratch.third_pulses.TriCZPulse",
                  "evil_mod.SquarePulse"):          # a leaf-name catalog match too
            r = c.post("/api/pulse/lab-waveform",
                       json={"qclass": q, "params": {"amplitude": 0.5}}).get_json()
            res = r["results"][0]
            assert res["ok"] is False and res["reason"] == "unknown-class", (q, res)
        assert not calls                            # nothing was spawned

    def test_a_roster_class_is_still_drawn(self, page):
        _, c, calls = page
        r = c.post("/api/pulse/lab-waveform", json={
            "qclass": LAB_CLASS, "params": {"amplitude": "0.3", "flat_length": "24"}}).get_json()
        assert r["results"][0]["ok"] and len(calls) == 1
