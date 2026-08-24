"""docs/137 — the combined QDAC + LF-FEM generator SM emits for the lab.

The lab's own `build_quam_qdac.py:93` picks between a QDAC class and a
flux-tunable one per qubit id. A bias-tee qubit is both, so it has no branch —
and the bias assignment after the line loop is unconditional on `z`, which
means on a qubit that DID get a flux line the `FluxLine` is overwritten
silently. These pins cover the two files SM emits to fix that, plus one live
end-to-end build that is the only real evidence here.

Every static pin below was mutation-checked when written.
"""

from __future__ import annotations

import ast
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from quam_state_manager.core import config_generator as cg
from quam_state_manager.core import qdac_lf_recipe as R
from quam_state_manager.core import script_emitter as SE

_ROOT = Path(__file__).resolve().parent.parent
_ENV_NAME = "CQT_20Q"          # resolves quam_config to PJ_10082026 (docs/136 §19)
_PJ = Path(r"D:\work\Customer_Codes\PJ_10082026\qualibration_graphs"
           r"\superconducting\quam_config")


# --------------------------------------------------------------------------
# fixtures
# --------------------------------------------------------------------------

def _spec(*, tee=("q1",), qdac_only=(), pairs=()) -> dict:
    """A spec with the requested bias shapes. q1..q3 by default."""
    qubits = ["q1", "q2", "q3"]
    lines = []
    for q in qubits:
        lines.append({"element": q, "line": "resonator", "group": "feedline1"})
        lines.append({"element": q, "line": "drive"})
        if q not in qdac_only:
            lines.append({"element": q, "line": "flux"})
    for c, t in pairs:
        lines.append({"element": f"{c}-{t}", "line": "coupler"})
    qd: dict = {}
    for i, q in enumerate(qdac_only):
        qd[q] = {"channel": 10 + i, "dc_offset": 0.0, "trigger_port": "ext1"}
    for i, q in enumerate(tee):
        qd[q] = {"channel": 20 + i, "dc_offset": 0.0, "trigger_port": "ext2",
                 "bias_tee": True}
    spec: dict = {
        "network": {"host": "1.2.3.4", "cluster_name": "C"},
        "instruments": {"controllers": [{"con": 1, "fems": [
            {"slot": 1, "fem": "mw"}, {"slot": 5, "fem": "lf"}]}]},
        "qubits": qubits,
        "qubit_pairs": [list(p) for p in pairs],
        "lines": lines,
    }
    if qd:
        spec["qdac"] = {"communication_type": "Ethernet", "ip_address": "1.2.3.5",
                        "port": 5025, "usb_device": None, "lib": "@py",
                        "qubits": qd}
    return spec


def _alloc(spec: dict) -> dict:
    """A plausible allocation for every line the spec declares."""
    out: dict = {}
    port = {"rr": 1, "xy": 1, "z": 1, "c": 1, "qt": 1}
    for line in spec["lines"]:
        el, kind = line["element"], line["line"]
        key = {"resonator": "rr", "drive": "xy", "flux": "z", "coupler": "c"}[kind]
        slot = 1 if key in ("rr", "xy") else 5
        chans = [{"con": 1, "slot": slot, "port": port[key]}]
        if key == "rr":
            chans.append({"con": 1, "slot": slot, "port": port[key],
                          "io_type": "input"})
        out.setdefault(el, {})[key] = chans
        port[key] += 1
    for q in ((spec.get("qdac") or {}).get("qubits") or {}):
        out.setdefault(q, {})["qt"] = [{"con": 1, "slot": 4, "port": port["qt"]}]
        port["qt"] += 1
    return out


def _emit(spec):
    return R.emit_files(spec, _alloc(spec), "chip", "2026-08-24")


# --------------------------------------------------------------------------


class TestWhenItFires:
    def test_only_a_bias_tee_asks_for_it(self):
        assert R.wanted(_spec(tee=("q1",))) is True
        assert R.wanted(_spec(tee=(), qdac_only=("q1",))) is False
        assert R.wanted(_spec(tee=())) is False

    def test_a_plain_chip_gets_no_files(self):
        assert _emit(_spec(tee=(), qdac_only=("q1",))) == {}

    def test_a_tee_chip_gets_both(self):
        assert set(_emit(_spec())) == {R.BUILDER_FILENAME, R.GENERATOR_FILENAME}

    def test_both_are_valid_python(self):
        for name, src in _emit(_spec(pairs=(("q1", "q2"),))).items():
            ast.parse(src)          # a recipe that will not parse is not a recipe


class TestTheBuilderDiverges:
    """The four divergences from the lab's `_add_transmons_with_qdac`."""

    def _src(self):
        return _emit(_spec())[R.BUILDER_FILENAME]

    def test_the_class_pick_is_three_way(self):
        src = self._src()
        assert "QdacBiasedFluxTunableTransmon" in src
        assert "QdacBiasedFixedFrequencyTransmon" in src
        assert "machine.qubit_type" in src

    def test_the_bias_field_is_chosen_by_mode(self):
        """D4 — the original's `transmon.z = QdacBiasLine(...)` is the line that
        silently overwrites a bias-tee qubit's FluxLine."""
        src = self._src()
        assert 'bias_attr = "z" if mode == "qdac" else "qdac_bias"' in src
        assert "setattr(transmon, bias_attr," in src
        assert "transmon.z = QdacBiasLine(" not in src

    def test_the_trigger_goes_on_the_bias_line(self):
        """A tee's `z` declares neither opx_trigger_out nor trigger_port, so
        writing them there is dropped at save() without raising."""
        src = self._src()
        assert "def _attach_bias_trigger(transmon, bias_attr," in src
        assert "bias = getattr(transmon, bias_attr)" in src
        assert "bias.opx_trigger_out = Channel(" in src

    def test_the_two_guards_stopped_being_complements(self):
        """`:108` and `:120` in the original are exact complements, which is
        why a qubit needing both is unreachable from either side."""
        src = self._src()
        assert 'if mode == "qdac":' in src        # flux guard, narrowed
        assert 'if mode == "opx":' in src         # qt guard, narrowed

    def test_it_reuses_the_labs_own_helpers(self):
        src = self._src()
        assert "from quam_config.build_quam_qdac import (" in src
        for name in ("_add_pulses", "_mark_trigger_ports_shareable",
                     "_set_default_grid_location", "_validate_trigger_cabling"):
            assert name in src, name

    def test_it_adds_the_checks_the_original_lacks(self):
        # Asserted on the emitted SOURCE, so a message split across two string
        # literals is matched by a half that is actually contiguous there.
        src = self._src()
        assert "are declared BOTH QDAC-only and " in src        # disjointness
        assert "which are not " in src and "QDAC-biased" in src  # stray ids
        assert "one physical channel biases one qubit" in src    # channel reuse
        assert "def _validate_combined(" in src


class TestTheGeneratorPinsEverything:
    """An unpinned line moves every later coupler. No error, no symptom."""

    def _src(self, **kw):
        return _emit(_spec(**kw))[R.GENERATOR_FILENAME]

    def test_every_flux_and_coupler_line_is_pinned(self):
        src = self._src(pairs=(("q1", "q2"), ("q2", "q3")))
        i = src.index("FLUX_PINS = ")
        block = src[i:src.index("\n\n", i)]
        for el in ("q1", "q2", "q3", "q1-q2", "q2-q3"):
            assert f"'{el}'" in block, (el, block)

    def test_a_missing_pin_raises_rather_than_allocating(self):
        """Checked STRUCTURALLY, not by its message. Asserting the wording
        alone passes even with the guard deleted — the message is still in the
        source, just unreachable (caught by mutation when this was written)."""
        src = self._src()
        tree = ast.parse(src)
        guards = [
            node for node in ast.walk(tree)
            if isinstance(node, ast.If)
            and isinstance(node.test, ast.Compare)
            and isinstance(node.test.ops[0], ast.Is)
            and isinstance(node.test.comparators[0], ast.Constant)
            and node.test.comparators[0].value is None
            and any(isinstance(b, ast.Raise) for b in node.body)
        ]
        # one for the flux/coupler pin, one for the feedline pin
        assert len(guards) >= 2, ast.dump(tree)[:400]
        assert "has no FLUX_PINS entry" in src
        assert "moves every later coupler" in src

    def test_the_wirer_gets_integer_indices(self):
        """`qubits = list(range(1, 21))` — the wirer indexes by NUMBER. Passing
        "q1" is accepted and then allocates nothing recognisable."""
        src = self._src()
        assert "def _idx(qubit_id):" in src
        assert 'int(str(qubit_id).lstrip("qQ"))' in src
        assert "qubits=[_idx(_q)]" in src
        assert "qubits=[_q]" not in src

    def test_readout_is_one_call_per_feedline(self):
        """Multiplexed: every qubit on a feedline shares ONE in/out pair, so
        adding them one at a time would ask for one port each."""
        src = self._src()
        assert "for _grp, _members, _pin in FEEDLINES:" in src
        assert "qubits=[_idx(_m) for _m in _members]" in src

    def test_a_qdac_only_qubit_gets_no_flux_line(self):
        src = self._src(tee=("q1",), qdac_only=("q3",))
        assert "if _q in QDAC_ONLY_CHANNELS:" in src
        assert "        continue" in src

    def test_the_qt_line_never_reaches_the_wirer(self):
        """create_wiring has a line-type whitelist and raises on 'qt'."""
        src = self._src()
        assert "deliberately NOT added to the" in src
        assert 'add_wiring_spec' not in src


class TestPairIdSpelling:
    """A pair is `q1-q2` in a spec and `q1-2` in an allocation — the target
    drops its leading `q`. Looking it up verbatim misses every coupler, and
    the miss is SILENT: no pin, no connectivity line, a chip with no qubit
    pairs at all, and a generator that still reports success. Measured on a
    two-pair chip before this was fixed."""

    def test_the_short_allocation_key_is_found(self):
        assert R._resolve_alloc({"q1-2": {"c": [{"con": 1, "slot": 5, "port": 4}]}},
                                "q1-q2", "c") == (1, 5, 4)

    def test_the_long_one_is_too(self):
        assert R._resolve_alloc({"q1-q2": {"c": [{"con": 1, "slot": 5, "port": 4}]}},
                                "q1-2", "c") == (1, 5, 4)

    def test_a_qubit_key_is_unaffected(self):
        assert R._resolve_alloc({"q1": {"z": [{"con": 1, "slot": 5, "port": 1}]}},
                                "q1", "z") == (1, 5, 1)

    def test_a_genuinely_absent_line_is_still_absent(self):
        assert R._resolve_alloc({"q9-9": {"c": []}}, "q1-q2", "c") is None

    def test_a_coupler_reaches_the_emitted_pins(self):
        spec = _spec(pairs=(("q1", "q2"),))
        alloc = _alloc(spec)
        alloc["q1-2"] = alloc.pop("q1-q2")        # as the real allocator keys it
        src = R.emit_files(spec, alloc, "c", "d")[R.GENERATOR_FILENAME]
        i = src.index("FLUX_PINS = ")
        assert "'q1-q2': ['coupler'" in src[i:src.index("\n\n", i)]

    def test_a_gap_raises_instead_of_dropping_the_pair(self):
        """The coupler loop iterates FLUX_PINS, so a coupler missing from it is
        simply never built. Silence is the defect being fixed here."""
        src = _emit(_spec(pairs=(("q1", "q2"),)))[R.GENERATOR_FILENAME]
        assert "PAIR_COUPLERS = " in src
        assert "_missing_pairs = [" in src
        assert "no qubit pairs and no complaint" in src
        tree = ast.parse(src)
        assert any(
            isinstance(n, ast.If) and any(isinstance(b, ast.Raise) for b in n.body)
            and "missing_pairs" in ast.dump(n.test)
            for n in ast.walk(tree)), "the guard must RAISE, not warn"


class TestTheCabling:
    def test_a_spec_pin_beats_the_allocation(self):
        spec = _spec()
        spec["qdac"]["qubits"]["q1"]["trigger_pin"] = {"con": 2, "slot": 7, "port": 4}
        src = _emit(spec)[R.GENERATOR_FILENAME]
        i = src.index("QDAC_TRIGGER_CABLING = {")
        assert "[2, 7, 4]" in src[i:i + 300], src[i:i + 300]

    def test_the_allocation_supplies_the_rest(self):
        src = _emit(_spec())[R.GENERATOR_FILENAME]
        i = src.index("QDAC_TRIGGER_CABLING = {")
        assert "'ext2'" in src[i:i + 300]

    def test_qubits_on_one_ext_share_one_cable(self):
        """One OPX digital output feeds one ext input and arms every channel on
        it — 11 qubits on 4 cables is the real bench, not a collision."""
        spec = _spec(tee=("q1", "q2"))
        src = _emit(spec)[R.GENERATOR_FILENAME]
        i = src.index("QDAC_TRIGGER_CABLING = {")
        block = src[i:src.index("QDAC_QUBIT_TRIGGER_PORTS", i)]
        assert block.count("'ext2'") == 1, block

    def test_a_qubit_with_no_cable_is_named_not_dropped(self):
        spec = _spec()
        spec["qdac"]["qubits"]["q1"]["trigger_port"] = None
        alloc = _alloc(spec)
        alloc["q1"].pop("qt", None)
        src = R.emit_files(spec, alloc, "c", "d")[R.GENERATOR_FILENAME]
        assert "no cable was resolved" in src


class TestItRefusesToHalfBuild:
    def test_both_files_gate_on_the_class(self):
        for src in _emit(_spec()).values():
            assert "QdacBiasedFluxTunableTransmon" in src
            assert "raise ImportError(" in src
            assert "Stopping before anything is written" in src

    def test_the_generator_loads_back_what_it_wrote(self):
        """If the Union was not widened the build still succeeds and save()
        overwrites state.json — the failure only shows in the NEXT process,
        after the good file is gone."""
        src = _emit(_spec())[R.GENERATOR_FILENAME]
        assert "_reloaded = Quam.load()" in src
        assert "widen the Union" in src
        assert "lost its OPX flux line" in src
        assert "lost its QDAC DC bias" in src


class TestTheBundle:
    def test_a_tee_chip_bundle_carries_them(self):
        b = SE.emit_bundle(_spec(), _alloc(_spec()), {}, "chip")
        assert R.BUILDER_FILENAME in b and R.GENERATOR_FILENAME in b

    def test_a_plain_bundle_is_the_same_four_files(self):
        b = SE.emit_bundle(_spec(tee=()), {}, {}, "chip")
        assert sorted(b) == ["01_make_wiring.py", "02_build_machine.py",
                             "03_generate_config.py", "README.md"]

    def test_the_readme_hands_over_the_snippet(self):
        r = SE.emit_bundle(_spec(), _alloc(_spec()), {}, "chip")["README.md"]
        assert "class QdacBiasedFluxTunableTransmon(FluxTunableTransmon):" in r
        assert "qdac_bias: QdacBiasLine = None" in r
        assert "SM will not write into your tree" in r

    def test_a_plain_readme_says_nothing_about_it(self):
        r = SE.emit_bundle(_spec(tee=()), {}, {}, "chip")["README.md"]
        assert "bias tee" not in r.lower()


# --------------------------------------------------------------------------
# the live end-to-end build — the only real evidence in this file
# --------------------------------------------------------------------------

def _env_python():
    for env in cg.discover_envs():
        if env.get("name") == _ENV_NAME:
            python = env.get("python")
            if cg.probe_env(python).get("usable"):
                return python
    return None


def _patched_quam_config(dest: Path) -> Path:
    """A COPY of the lab's quam_config with the snippet applied. Their own tree
    is never written to."""
    pkg = dest / "quam_config"
    shutil.copytree(_PJ, pkg,
                    ignore=shutil.ignore_patterns("__pycache__", "*.pyc", ".git"))
    body = R.SNIPPET.split("# ---- and widen")[0].split("---\n", 1)[-1]
    qc = pkg / "qdac_components.py"
    qc.write_text(qc.read_text(encoding="utf-8") + "\n\n" + body, encoding="utf-8")
    mq = pkg / "my_quam.py"
    src = mq.read_text(encoding="utf-8")
    src = src.replace(
        "from quam_config.qdac_components import "
        "QdacBiasedFixedFrequencyTransmon, QdacInstrument",
        "from quam_config.qdac_components import ("
        "QdacBiasedFixedFrequencyTransmon,\n"
        "                                         "
        "QdacBiasedFluxTunableTransmon, QdacInstrument)")
    src = src.replace(
        "qubits: Dict[str, Union[FluxTunableTransmon, "
        "QdacBiasedFixedFrequencyTransmon]] = field(",
        "qubits: Dict[str, Union[FluxTunableTransmon, "
        "QdacBiasedFixedFrequencyTransmon,\n"
        "                        QdacBiasedFluxTunableTransmon]] = field(")
    mq.write_text(src, encoding="utf-8")
    return pkg


@pytest.mark.skipif(_env_python() is None,
                    reason=f"conda env {_ENV_NAME!r} not found or not QM-usable")
@pytest.mark.skipif(not _PJ.is_dir(), reason="the lab's quam_config is not on this machine")
@pytest.mark.parametrize("pairs", [(), (("q1", "q2"), ("q2", "q3"))],
                         ids=["no-pairs", "tunable-couplers"])
def test_the_emitted_generator_builds_a_loadable_bias_tee_chip(tmp_path, pairs):
    """Emit it, RUN it, and load what it wrote in a FRESH process.

    A build that reports ok is not evidence — that is exactly what the
    root-class CRITICAL did (docs/136 §12). The assertions that matter run in a
    separate interpreter, against the files on disk.
    """
    python = _env_python()

    spec = _spec(tee=("q1",), pairs=pairs)
    if pairs:
        spec["pair_gate"] = "cz_tunable"
    assert cg.validate_spec(spec) == []

    outcome = cg.run_generator(python, mode="allocate", spec=spec,
                               out_dir=tmp_path / "_alloc", timeout=420)
    allocation = (outcome.get("result") or {}).get("allocation") or {}
    assert outcome.get("ok") and allocation, outcome.get("error")

    files = R.emit_files(spec, allocation, "teechip", "2026-08-24")
    pkg = _patched_quam_config(tmp_path / "env")
    for name, source in files.items():
        (pkg / name).write_text(source, encoding="utf-8")

    out = tmp_path / "chip"
    env = dict(os.environ)
    env["PYTHONUTF8"] = "1"
    env["PYTHONPATH"] = str(tmp_path / "env")
    env["QUAM_STATE_PATH"] = str(out)
    r = subprocess.run([python, str(pkg / R.GENERATOR_FILENAME), str(out)],
                       capture_output=True, text=True, encoding="utf-8",
                       env=env, timeout=900)
    assert r.returncode == 0, (r.stdout or "") + (r.stderr or "")[-2500:]
    assert "through a bias tee, reloaded clean" in (r.stdout or "")

    state = json.loads((out / "state.json").read_text(encoding="utf-8"))
    wiring = json.loads((out / "wiring.json").read_text(encoding="utf-8"))
    q1 = state["qubits"]["q1"]

    # the pulse line SURVIVED — this is the `:131` silent-overwrite defect
    assert str(q1["__class__"]).endswith("QdacBiasedFluxTunableTransmon")
    assert str(q1["z"]["__class__"]).endswith("FluxLine"), q1["z"]["__class__"]
    assert q1["z"].get("opx_output")
    assert "const" in (q1["z"].get("operations") or {})
    # …and the DC bias landed on the sibling, with its trigger
    bias = q1.get("qdac_bias") or {}
    assert str(bias.get("__class__")).endswith("QdacBiasLine")
    assert bias.get("channel") == 20 and bias.get("trigger_port") == "ext2"
    assert bias.get("opx_trigger_out")
    # both wiring entries, in one qubit dict
    wq = (wiring.get("wiring") or wiring)["qubits"]["q1"]
    assert wq.get("z", {}).get("opx_output") and wq.get("qt", {}).get("digital_output")
    # an ordinary qubit on the same chip is untouched
    assert str(state["qubits"]["q2"]["__class__"]).endswith("FluxTunableTransmon")

    # The couplers are the highest-consequence path: they are cabled by
    # allocation ORDER in the lab's own generator, so a wrong pin is silent and
    # physical. Every emitted pin must be the port that actually landed.
    if pairs:
        emitted = {k: v for k, v in R._flux_pins(spec, allocation).items()
                   if v[0] == "coupler"}
        assert len(emitted) == len(pairs), emitted
        built = (wiring.get("wiring") or wiring).get("qubit_pairs") or {}
        assert len(built) == len(pairs), sorted(built)
        for element, (_kind, con, slot, port) in emitted.items():
            key = next(k for k in R._alloc_keys(element) if k in built)
            got = (built[key].get("c") or {}).get("opx_output")
            assert got == f"#/ports/analog_outputs/con{con}/{slot}/{port}", (
                element, got)
        assert sorted(state.get("qubit_pairs") or {}) == sorted(built)

    probe = tmp_path / "_load.py"
    probe.write_text(
        "import os\n"
        f"os.environ['QUAM_STATE_PATH'] = r'{out}'\n"
        "from quam_config import Quam\n"
        f"m = Quam.load(r'{out}')\n"
        "q = m.qubits['q1']\n"
        "assert type(q).__name__ == 'QdacBiasedFluxTunableTransmon'\n"
        "assert type(q.z).__name__ == 'FluxLine'\n"
        "assert type(q.qdac_bias).__name__ == 'QdacBiasLine'\n"
        "assert q.qdac_bias.opx_trigger_out is not None\n"
        "cfg = m.generate_config()\n"
        "assert 'q1_qdac_trigger' in cfg['elements'], sorted(cfg['elements'])\n"
        "print('LOADED', len(m.qubits))\n", encoding="utf-8")
    r2 = subprocess.run([python, str(probe)], capture_output=True, text=True,
                        encoding="utf-8", env=env, timeout=900)
    assert r2.returncode == 0, (r2.stdout or "") + (r2.stderr or "")[-2500:]
    assert "LOADED" in (r2.stdout or "")
