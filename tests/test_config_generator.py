"""Tests for quam_state_manager.core.config_generator."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from quam_state_manager.core import config_generator
from quam_state_manager.core.config_generator import (
    discover_envs,
    get_selected_env,
    probe_env,
    run_config_preview,
    run_generator,
    set_selected_env,
    validate_spec,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
SAMPLE_SPEC = REPO_ROOT / "docs" / "examples" / "sample_spec_3q.json"
SAMPLE_SPEC_MULTICHASSIS = (
    REPO_ROOT / "docs" / "examples" / "sample_spec_multichassis.json"
)


def _first_usable_qm_env():
    """Return the interpreter path of the first QM-capable conda env, or None."""
    for env in discover_envs():
        if probe_env(env["python"])["usable"]:
            return env["python"]
    return None


def _valid_spec() -> dict:
    """A minimal, structurally-valid spec built fresh for each test."""
    return {
        "network": {"host": "1.2.3.4", "cluster_name": "C", "port": None},
        "instruments": {
            "controllers": [
                {"con": 1, "fems": [{"slot": 1, "fem": "mw"}, {"slot": 5, "fem": "lf"}]}
            ],
            "opx_plus": [],
            "octaves": [],
        },
        "qubits": ["q1", "q2"],
        "qubit_pairs": [["q1", "q2"]],
        "twpas": [],
        "lines": [
            {"element": "q1", "line": "resonator", "group": "f",
             "channel": {"kind": "mw_fem", "con": 1, "slot": 1}},
            {"element": "q1", "line": "drive", "channel": None},
            {"element": "q1", "line": "flux", "channel": {"kind": "lf_fem", "con": 1}},
            {"element": "q1-q2", "line": "coupler", "channel": None},
        ],
        "populate": {},
    }


class TestValidateSpecValid:
    def test_minimal_valid_spec_has_no_errors(self):
        assert validate_spec(_valid_spec()) == []

    def test_shipped_sample_spec_validates_clean(self):
        spec = json.loads(SAMPLE_SPEC.read_text(encoding="utf-8"))
        assert validate_spec(spec) == []

    def test_opx_plus_and_octave_satisfy_instruments(self):
        spec = _valid_spec()
        spec["instruments"] = {
            "controllers": [], "opx_plus": [{"con": 1}], "octaves": [{"index": 1}]
        }
        # lines still reference con 1 channels; instruments requirement is met
        assert validate_spec(spec) == []

    def test_shipped_multichassis_sample_validates_clean(self):
        spec = json.loads(SAMPLE_SPEC_MULTICHASSIS.read_text(encoding="utf-8"))
        assert validate_spec(spec) == []

    def test_two_controllers_same_slot_number_is_valid(self):
        # The FEM-slot dedup key is (con, slot), so the SAME slot number on
        # two different controllers must NOT be flagged as a duplicate.
        spec = _valid_spec()
        spec["instruments"]["controllers"].append(
            {"con": 2, "fems": [{"slot": 1, "fem": "mw"}, {"slot": 5, "fem": "lf"}]}
        )
        assert validate_spec(spec) == []


class TestValidateSpecErrors:
    def test_non_dict_spec(self):
        assert validate_spec([]) == ["spec must be a JSON object"]

    def test_missing_network_host(self):
        spec = _valid_spec()
        spec["network"]["host"] = ""
        assert any("network.host" in e for e in validate_spec(spec))

    def test_missing_cluster_name(self):
        spec = _valid_spec()
        del spec["network"]["cluster_name"]
        assert any("cluster_name" in e for e in validate_spec(spec))

    def test_bad_port_type(self):
        spec = _valid_spec()
        spec["network"]["port"] = "5000"
        assert any("network.port" in e for e in validate_spec(spec))

    def test_no_instruments(self):
        spec = _valid_spec()
        spec["instruments"] = {"controllers": [], "opx_plus": [], "octaves": []}
        assert any("at least one controller" in e for e in validate_spec(spec))

    def test_bad_fem_type(self):
        spec = _valid_spec()
        spec["instruments"]["controllers"][0]["fems"][0]["fem"] = "xy"
        assert any("fem must be" in e for e in validate_spec(spec))

    def test_slot_out_of_range(self):
        spec = _valid_spec()
        spec["instruments"]["controllers"][0]["fems"][0]["slot"] = 9
        assert any("slot must be" in e for e in validate_spec(spec))

    def test_duplicate_slot(self):
        spec = _valid_spec()
        spec["instruments"]["controllers"][0]["fems"].append({"slot": 1, "fem": "lf"})
        assert any("same slot" in e for e in validate_spec(spec))

    def test_no_qubits(self):
        spec = _valid_spec()
        spec["qubits"] = []
        assert any("at least one qubit" in e for e in validate_spec(spec))

    def test_duplicate_qubit_ids(self):
        spec = _valid_spec()
        spec["qubits"] = ["q1", "q1"]
        assert any("unique" in e for e in validate_spec(spec))

    def test_qubit_id_name_rule(self):
        # Mirrors the wizard's validateQubitName: leading lowercase 'q', then
        # letters/digits/underscore — '-' corrupts pair-id parsing, whitespace
        # breaks element naming, and a non-'q' prefix orphans populate values
        # (quam_builder keys machine.qubits as 'q' + stripped index).
        for bad in ("q-1", "Q2", "q 3", "x1", "q"):
            spec = _valid_spec()
            spec["qubits"] = [bad]
            spec["qubit_pairs"] = []
            spec["lines"] = [{"element": bad, "line": "drive", "channel": None}]
            errs = validate_spec(spec)
            assert any("qubits: id" in e for e in errs), f"{bad!r} not rejected"

    def test_alpha_qubit_ids_valid(self):
        # qA1 / qB2-style names (grid naming preset) pass cleanly, including
        # as pair endpoints and line elements.
        spec = _valid_spec()
        spec["qubits"] = ["qA1", "qB2"]
        spec["qubit_pairs"] = [["qA1", "qB2"]]
        spec["lines"] = [
            {"element": "qA1", "line": "drive", "channel": None},
            {"element": "qB2", "line": "drive", "channel": None},
            {"element": "qA1-qB2", "line": "coupler", "channel": None},
        ]
        assert not [e for e in validate_spec(spec) if "qubits: id" in e]

    def test_pair_references_unknown_qubit(self):
        spec = _valid_spec()
        spec["qubit_pairs"] = [["q1", "q9"]]
        assert any("q9" in e for e in validate_spec(spec))

    def test_qubit_line_unknown_qubit(self):
        spec = _valid_spec()
        spec["lines"].append({"element": "q7", "line": "drive", "channel": None})
        assert any("q7" in e and "declared qubit" in e for e in validate_spec(spec))

    def test_pair_line_unknown_qubit(self):
        spec = _valid_spec()
        spec["lines"].append({"element": "q1-q9", "line": "coupler", "channel": None})
        assert any("q1-q9" in e for e in validate_spec(spec))

    def test_unknown_line_type(self):
        spec = _valid_spec()
        spec["lines"].append({"element": "q1", "line": "bogus", "channel": None})
        assert any("unknown line type" in e for e in validate_spec(spec))

    def test_twpa_line_unknown_twpa(self):
        spec = _valid_spec()
        spec["lines"].append({"element": "twpaZ", "line": "twpa_pump", "channel": None})
        assert any("not a declared TWPA" in e for e in validate_spec(spec))

    def test_bad_channel_kind(self):
        spec = _valid_spec()
        spec["lines"][0]["channel"] = {"kind": "weird"}
        assert any("kind" in e for e in validate_spec(spec))

    def test_channel_non_integer_field(self):
        spec = _valid_spec()
        spec["lines"][0]["channel"] = {"kind": "mw_fem", "slot": "one"}
        assert any("slot" in e for e in validate_spec(spec))

    def test_populate_not_object(self):
        spec = _valid_spec()
        spec["populate"] = []
        assert any("populate" in e for e in validate_spec(spec))

    def test_multiple_errors_all_reported(self):
        spec = _valid_spec()
        spec["network"]["host"] = ""
        spec["qubits"] = []
        errors = validate_spec(spec)
        assert any("network.host" in e for e in errors)
        assert any("qubit" in e for e in errors)


class TestLineTypeFlexibility:
    """Specs with subset line types should validate and (on real QM envs) build."""

    def _mw_only_spec(self):
        """MW-FEM only: resonator + drive, no flux, no pairs."""
        return {
            "network": {"host": "1.2.3.4", "cluster_name": "C", "port": None},
            "instruments": {
                "controllers": [
                    {"con": 1, "fems": [{"slot": 1, "fem": "mw"}]}
                ],
                "opx_plus": [], "octaves": [],
            },
            "qubits": ["q1", "q2"],
            "qubit_pairs": [],
            "twpas": [],
            "lines": [
                {"element": "q1", "line": "resonator", "group": "f1",
                 "channel": {"kind": "mw_fem", "con": 1, "slot": 1}},
                {"element": "q2", "line": "resonator", "group": "f1",
                 "channel": {"kind": "mw_fem", "con": 1, "slot": 1}},
                {"element": "q1", "line": "drive", "channel": None},
                {"element": "q2", "line": "drive", "channel": None},
            ],
            "populate": {},
        }

    def _lf_only_spec(self):
        """LF-FEM only: flux lines only, no resonator/drive."""
        return {
            "network": {"host": "1.2.3.4", "cluster_name": "C", "port": None},
            "instruments": {
                "controllers": [
                    {"con": 1, "fems": [{"slot": 5, "fem": "lf"}]}
                ],
                "opx_plus": [], "octaves": [],
            },
            "qubits": ["q1"],
            "qubit_pairs": [],
            "twpas": [],
            "lines": [
                {"element": "q1", "line": "flux",
                 "channel": {"kind": "lf_fem", "con": 1}},
            ],
            "populate": {},
        }

    def _mw_lf_no_flux_spec(self):
        """MW + LF, but qubit z flux disabled, coupler flux only."""
        return {
            "network": {"host": "1.2.3.4", "cluster_name": "C", "port": None},
            "instruments": {
                "controllers": [
                    {"con": 1, "fems": [
                        {"slot": 1, "fem": "mw"}, {"slot": 5, "fem": "lf"}
                    ]}
                ],
                "opx_plus": [], "octaves": [],
            },
            "qubits": ["q1", "q2"],
            "qubit_pairs": [["q1", "q2"]],
            "twpas": [],
            "lines": [
                {"element": "q1", "line": "resonator", "group": "f1",
                 "channel": {"kind": "mw_fem", "con": 1, "slot": 1}},
                {"element": "q2", "line": "resonator", "group": "f1",
                 "channel": {"kind": "mw_fem", "con": 1, "slot": 1}},
                {"element": "q1", "line": "drive", "channel": None},
                {"element": "q2", "line": "drive", "channel": None},
                {"element": "q1-q2", "line": "coupler", "channel": None},
            ],
            "populate": {},
        }

    def test_mw_only_validates_clean(self):
        assert validate_spec(self._mw_only_spec()) == []

    def test_lf_only_validates_clean(self):
        assert validate_spec(self._lf_only_spec()) == []

    def test_mw_lf_no_qubit_flux_coupler_only_is_rejected(self):
        # Fixed-frequency qubits (no flux) + a tunable coupler is NOT representable:
        # quam_builder's CZGate plays on the qubit z line, so a coupler-only CZ can't
        # be built. This combo (once assumed valid) is now blocked — fixed-frequency
        # chips use cross-resonance. See test_chip_architecture.py.
        errs = validate_spec(self._mw_lf_no_flux_spec())
        assert any("coupler lines need qubit flux" in e for e in errs), errs

    def test_mw_lf_full_validates_clean(self):
        assert validate_spec(_valid_spec()) == []

    def test_no_lines_validates_clean(self):
        """A spec with qubits but no lines is structurally valid."""
        spec = self._mw_only_spec()
        spec["lines"] = []
        assert validate_spec(spec) == []

    def test_mw_only_has_no_flux_lines(self):
        spec = self._mw_only_spec()
        assert not [ln for ln in spec["lines"] if ln["line"] == "flux"]

    def test_lf_only_has_no_resonator_or_drive(self):
        spec = self._lf_only_spec()
        assert not [ln for ln in spec["lines"] if ln["line"] in ("resonator", "drive")]

    def test_no_coupler_without_pairs(self):
        spec = self._mw_only_spec()
        assert not [ln for ln in spec["lines"] if ln["line"] == "coupler"]

    def test_opx_plus_only_validates_clean(self):
        """OPX+ has both MW and LF capabilities — all line types valid."""
        spec = {
            "network": {"host": "1.2.3.4", "cluster_name": "C", "port": None},
            "instruments": {
                "controllers": [],
                "opx_plus": [{"con": 1}],
                "octaves": [],
            },
            "qubits": ["q1", "q2"],
            "qubit_pairs": [["q1", "q2"]],
            "twpas": [],
            "lines": [
                {"element": "q1", "line": "resonator", "group": "f1",
                 "channel": {"kind": "opx", "con": 1}},
                {"element": "q2", "line": "resonator", "group": "f1",
                 "channel": {"kind": "opx", "con": 1}},
                {"element": "q1", "line": "drive", "channel": None},
                {"element": "q2", "line": "drive", "channel": None},
                {"element": "q1", "line": "flux", "channel": None},
                {"element": "q2", "line": "flux", "channel": None},
                {"element": "q1-q2", "line": "coupler", "channel": None},
            ],
            "populate": {},
        }
        assert validate_spec(spec) == []

    def test_opx_plus_mw_only_validates_clean(self):
        """OPX+ with only MW line types (no flux) — valid subset."""
        spec = {
            "network": {"host": "1.2.3.4", "cluster_name": "C", "port": None},
            "instruments": {
                "controllers": [],
                "opx_plus": [{"con": 1}],
                "octaves": [],
            },
            "qubits": ["q1"],
            "qubit_pairs": [],
            "twpas": [],
            "lines": [
                {"element": "q1", "line": "resonator", "group": "f1",
                 "channel": {"kind": "opx", "con": 1}},
                {"element": "q1", "line": "drive", "channel": None},
            ],
            "populate": {},
        }
        assert validate_spec(spec) == []


class TestEnvDiscovery:
    def test_discover_envs_parses_conda_json(self, monkeypatch):
        monkeypatch.setattr(config_generator, "find_conda_executable", lambda: "conda")
        fake_json = json.dumps({"envs": [
            "C:/ProgramData/miniconda3",
            "C:/Users/x/.conda/envs/LabA",
        ]})
        monkeypatch.setattr(
            config_generator, "_run_command",
            lambda args, timeout=60: (0, fake_json, ""),
        )
        envs = discover_envs()
        names = [e["name"] for e in envs]
        assert "miniconda3" in names
        assert "LabA" in names
        laba = next(e for e in envs if e["name"] == "LabA")
        assert laba["python"].lower().endswith(("python.exe", "python"))

    def test_discover_envs_no_conda(self, monkeypatch):
        monkeypatch.setattr(config_generator, "find_conda_executable", lambda: None)
        assert discover_envs() == []

    def test_discover_envs_conda_failure(self, monkeypatch):
        monkeypatch.setattr(config_generator, "find_conda_executable", lambda: "conda")
        monkeypatch.setattr(
            config_generator, "_run_command",
            lambda args, timeout=60: (1, "", "boom"),
        )
        assert discover_envs() == []


class TestProbeEnv:
    def test_probe_env_usable(self, monkeypatch):
        out = json.dumps({
            "python": "3.11.5", "qualang_tools": "0.22.0",
            "quam_builder": "0.2.0", "quam": "0.5.0a3", "qm": "1.2.6",
        })
        monkeypatch.setattr(
            config_generator, "_run_command",
            lambda args, timeout=60: (0, out, ""),
        )
        info = probe_env("py")
        assert info["usable"] is True
        assert info["missing"] == []
        assert info["versions"]["quam_builder"] == "0.2.0"

    def test_probe_env_missing_lib(self, monkeypatch):
        out = json.dumps({
            "python": "3.11.5", "qualang_tools": "0.22.0",
            "quam_builder": None, "quam": "0.5.0a3", "qm": None,
        })
        monkeypatch.setattr(
            config_generator, "_run_command",
            lambda args, timeout=60: (0, out, ""),
        )
        info = probe_env("py")
        assert info["usable"] is False
        assert "quam_builder" in info["missing"]

    def test_probe_env_process_failure(self, monkeypatch):
        monkeypatch.setattr(
            config_generator, "_run_command",
            lambda args, timeout=60: (-1, "", "no such interpreter"),
        )
        info = probe_env("py")
        assert info["usable"] is False
        assert info["error"]

    def test_probe_env_unparseable_output(self, monkeypatch):
        monkeypatch.setattr(
            config_generator, "_run_command",
            lambda args, timeout=60: (0, "not json", ""),
        )
        info = probe_env("py")
        assert info["usable"] is False
        assert "parse" in info["error"]


class TestSelectedEnv:
    def test_selected_env_roundtrip(self, tmp_path):
        assert get_selected_env(tmp_path) is None
        set_selected_env(tmp_path, "C:/envs/LabA/python.exe")
        assert get_selected_env(tmp_path) == "C:/envs/LabA/python.exe"

    def test_get_selected_env_missing_file(self, tmp_path):
        assert get_selected_env(tmp_path) is None

    def test_get_selected_env_corrupt_file(self, tmp_path):
        (tmp_path / "config_generator.json").write_text("{bad json", encoding="utf-8")
        assert get_selected_env(tmp_path) is None


class TestRunGenerator:
    """Subprocess-mocked tests — no QM env required."""

    def test_generator_script_exists(self):
        assert config_generator.GENERATOR_SCRIPT.exists()

    def test_bad_mode_rejected(self, tmp_path):
        outcome = run_generator("py", "frobnicate", {}, tmp_path)
        assert outcome["ok"] is False
        assert "invalid mode" in outcome["error"]

    def test_spec_file_is_written(self, tmp_path, monkeypatch):
        seen = {}

        def fake_run(args, timeout=300):
            spec_path = Path(args[args.index("--spec") + 1])
            seen["spec"] = json.loads(spec_path.read_text(encoding="utf-8"))
            (spec_path.parent / "_result.json").write_text(
                json.dumps({"status": "ok"}), encoding="utf-8"
            )
            return 0, "{}", ""

        monkeypatch.setattr(config_generator, "_run_command", fake_run)
        run_generator("py", "allocate", {"qubits": ["q1"]}, tmp_path)
        assert seen["spec"] == {"qubits": ["q1"]}

    def test_success_result_parsed(self, tmp_path, monkeypatch):
        def fake_run(args, timeout=300):
            work = Path(args[args.index("--spec") + 1]).parent
            (work / "_result.json").write_text(json.dumps({
                "status": "ok", "mode": "build", "warnings": [],
                "files": {"state": "s.json", "wiring": "w.json"},
            }), encoding="utf-8")
            return 0, '{"status": "ok"}', ""

        monkeypatch.setattr(config_generator, "_run_command", fake_run)
        outcome = run_generator("py", "build", {}, tmp_path)
        assert outcome["ok"] is True
        assert outcome["status"] == "ok"
        assert outcome["result"]["files"]["state"] == "s.json"

    def test_error_status_surfaced(self, tmp_path, monkeypatch):
        def fake_run(args, timeout=300):
            work = Path(args[args.index("--spec") + 1]).parent
            (work / "_result.json").write_text(json.dumps({
                "status": "error", "error": "ConstraintsTooStrict: nope",
            }), encoding="utf-8")
            return 1, "", ""

        monkeypatch.setattr(config_generator, "_run_command", fake_run)
        outcome = run_generator("py", "build", {}, tmp_path)
        assert outcome["ok"] is False
        assert "nope" in outcome["error"]

    def test_missing_result_file_reported(self, tmp_path, monkeypatch):
        monkeypatch.setattr(
            config_generator, "_run_command",
            lambda args, timeout=300: (-1, "", "interpreter exploded"),
        )
        outcome = run_generator("py", "allocate", {}, tmp_path)
        assert outcome["ok"] is False
        assert "_result.json" in outcome["error"]
        assert "interpreter exploded" in outcome["error"]

    def test_unparseable_result_reported(self, tmp_path, monkeypatch):
        def fake_run(args, timeout=300):
            work = Path(args[args.index("--spec") + 1]).parent
            (work / "_result.json").write_text("{not json", encoding="utf-8")
            return 0, "", ""

        monkeypatch.setattr(config_generator, "_run_command", fake_run)
        outcome = run_generator("py", "build", {}, tmp_path)
        assert outcome["ok"] is False
        assert "could not read" in outcome["error"]


class TestRunConfigPreview:
    """Subprocess-mocked tests for the config previewer — no QM env required."""

    @staticmethod
    def _state_folder(tmp_path):
        folder = tmp_path / "quam_state"
        folder.mkdir()
        (folder / "state.json").write_text("{}", encoding="utf-8")
        (folder / "wiring.json").write_text("{}", encoding="utf-8")
        return folder

    @staticmethod
    def _work_dir_of(args):
        return Path(args[args.index("--out") + 1])

    def test_previewer_script_exists(self):
        assert config_generator.CONFIG_PREVIEW_SCRIPT.exists()

    def test_missing_state_json_rejected_before_spawn(self, tmp_path, monkeypatch):
        def fake_run(args, timeout=120):  # pragma: no cover - must not run
            raise AssertionError("subprocess must not be spawned")

        monkeypatch.setattr(config_generator, "_run_command", fake_run)
        outcome = run_config_preview("py", tmp_path)
        assert outcome["ok"] is False
        assert "state.json not found" in outcome["error"]

    def test_success_result_parsed(self, tmp_path, monkeypatch):
        def fake_run(args, timeout=120):
            work = self._work_dir_of(args)
            (work / "_result.json").write_text(json.dumps({
                "status": "ok", "config": {"version": 1},
                "qubits": ["q1"], "qubit_pairs": [],
            }), encoding="utf-8")
            return 0, '{"status": "ok"}', ""

        monkeypatch.setattr(config_generator, "_run_command", fake_run)
        outcome = run_config_preview("py", self._state_folder(tmp_path))
        assert outcome["ok"] is True
        assert outcome["status"] == "ok"
        assert outcome["result"]["config"] == {"version": 1}

    def test_error_status_surfaced(self, tmp_path, monkeypatch):
        def fake_run(args, timeout=120):
            work = self._work_dir_of(args)
            (work / "_result.json").write_text(json.dumps({
                "status": "error", "error": "RuntimeError: could not load",
            }), encoding="utf-8")
            return 1, "", ""

        monkeypatch.setattr(config_generator, "_run_command", fake_run)
        outcome = run_config_preview("py", self._state_folder(tmp_path))
        assert outcome["ok"] is False
        assert "could not load" in outcome["error"]

    def test_missing_result_file_reported(self, tmp_path, monkeypatch):
        monkeypatch.setattr(
            config_generator, "_run_command",
            lambda args, timeout=120: (-1, "", "interpreter exploded"),
        )
        outcome = run_config_preview("py", self._state_folder(tmp_path))
        assert outcome["ok"] is False
        assert "_result.json" in outcome["error"]
        assert "interpreter exploded" in outcome["error"]

    def test_unparseable_result_reported(self, tmp_path, monkeypatch):
        def fake_run(args, timeout=120):
            work = self._work_dir_of(args)
            (work / "_result.json").write_text("{not json", encoding="utf-8")
            return 0, "", ""

        monkeypatch.setattr(config_generator, "_run_command", fake_run)
        outcome = run_config_preview("py", self._state_folder(tmp_path))
        assert outcome["ok"] is False
        assert "could not read" in outcome["error"]


class TestScriptSmoke:
    """The standalone scripts must start (and find _script_common) without the
    QM stack — their module top is stdlib + the sibling import only."""

    SCRIPTS = [
        config_generator.GENERATOR_SCRIPT,
        config_generator.CONFIG_PREVIEW_SCRIPT,
    ]

    @pytest.mark.parametrize("script", SCRIPTS, ids=lambda s: s.name)
    def test_help_runs_standalone(self, script):
        proc = subprocess.run(
            [sys.executable, str(script), "--help"],
            capture_output=True, text=True, timeout=30,
        )
        assert proc.returncode == 0, proc.stderr

    @pytest.mark.parametrize("script", SCRIPTS, ids=lambda s: s.name)
    def test_help_runs_under_safepath(self, script):
        # PYTHONSAFEPATH (3.11+) suppresses the script-dir sys.path entry; the
        # defensive insert in each script must keep the sibling import working.
        env = {**os.environ, "PYTHONSAFEPATH": "1"}
        proc = subprocess.run(
            [sys.executable, str(script), "--help"],
            capture_output=True, text=True, timeout=30, env=env,
        )
        assert proc.returncode == 0, proc.stderr


class TestRunGeneratorIntegration:
    """Real-subprocess test — auto-skips when no QM-capable env is available."""

    def test_real_allocate_against_qm_env(self, tmp_path):
        usable = None
        for env in discover_envs():
            if probe_env(env["python"])["usable"]:
                usable = env["python"]
                break
        if not usable:
            pytest.skip("no conda env with the QM stack available")

        spec = json.loads(SAMPLE_SPEC.read_text(encoding="utf-8"))
        outcome = run_generator(usable, "allocate", spec, tmp_path, timeout=120)
        assert outcome["ok"], outcome.get("error")
        allocation = outcome["result"]["allocation"]
        # qubits q1-q3 plus the q1-2 / q2-3 coupler pairs are all allocated
        assert {"q1", "q2", "q3"} <= set(allocation)
        assert allocation["q1"]["xy"]  # drive line was allocated

    def test_real_build_propagates_per_qubit_pulses_and_resonator_fsp(
        self, tmp_path
    ):
        """End-to-end: per-qubit DRAG α + resonator FSP land where expected.

        Runs the build mode of run_build.py inside a QM-capable conda env
        against the sample spec, then asserts the on-disk state.json carries
        the per-qubit pulse params (sample sets q1 alpha=-0.15, q3 alpha=-1.08)
        and the per-feedline-group resonator FSP (sample sets -5 dBm on the
        readout port shared by q1/q2/q3).
        """
        usable = None
        for env in discover_envs():
            if probe_env(env["python"])["usable"]:
                usable = env["python"]
                break
        if not usable:
            pytest.skip("no conda env with the QM stack available")

        spec = json.loads(SAMPLE_SPEC.read_text(encoding="utf-8"))
        out_dir = tmp_path / "quam_state"
        outcome = run_generator(usable, "build", spec, out_dir, timeout=180)
        assert outcome["ok"], outcome.get("error")

        state = json.loads((out_dir / "state.json").read_text(encoding="utf-8"))

        # Per-qubit DRAG α reaches the x180_DragCosine pulse.
        ops_q1 = state["qubits"]["q1"]["xy"]["operations"]
        ops_q3 = state["qubits"]["q3"]["xy"]["operations"]
        assert ops_q1["x180_DragCosine"]["alpha"] == pytest.approx(-0.15)
        assert ops_q3["x180_DragCosine"]["alpha"] == pytest.approx(-1.08)
        # Per-qubit x180_length: q1=40, q3=48.
        assert ops_q1["x180_DragCosine"]["length"] == 40
        assert ops_q3["x180_DragCosine"]["length"] == 48

        # Resonator full_scale_power_dbm on the shared MW-FEM readout port.
        # build_quam writes the port via the qubits' resonator.opx_output
        # pointer, so it shows up under state["ports"]["mw_outputs"][...].
        mw_outs = state["ports"]["mw_outputs"]
        fsp_values = []
        for con, slots in mw_outs.items():
            if not isinstance(slots, dict):
                continue
            for _slot, ports in slots.items():
                if not isinstance(ports, dict):
                    continue
                for _port, cfg in ports.items():
                    if not isinstance(cfg, dict):
                        continue
                    if "full_scale_power_dbm" in cfg:
                        fsp_values.append(cfg["full_scale_power_dbm"])
        assert -5 in fsp_values, f"-5 dBm readout FSP not found in {fsp_values}"
        # qubit XY FSP from the sample (q1=1 dBm) also lands on its port.
        assert 1 in fsp_values, f"+1 dBm XY FSP not found in {fsp_values}"


class TestMultiChassisIntegration:
    """Real-subprocess multi-chassis tests — auto-skip without a QM env.

    Guards the cross-controller path end-to-end: a spec split across con1
    and con2 must allocate onto BOTH controllers, build controller-aware
    port pointers, and link each readout's downconverter to an output on
    the SAME chassis (never cross-linking con1<->con2).
    """

    @staticmethod
    def _find_qubits(node):
        """Locate the qubits dict in a wiring.json of unknown nesting depth."""
        if isinstance(node, dict):
            q = node.get("qubits")
            if isinstance(q, dict):
                return q
            for value in node.values():
                found = TestMultiChassisIntegration._find_qubits(value)
                if found:
                    return found
        return None

    @staticmethod
    def _pointer_con(pointer: str):
        """con segment of '#/ports/mw_outputs/<con>/<slot>/<port>/...'."""
        parts = pointer.lstrip("#/").split("/")
        # ['ports', 'mw_outputs', '<con>', '<slot>', '<port>', '<field>']
        return parts[2] if len(parts) >= 3 else None

    def test_real_allocate_spreads_across_controllers(self, tmp_path):
        usable = _first_usable_qm_env()
        if not usable:
            pytest.skip("no conda env with the QM stack available")

        spec = json.loads(SAMPLE_SPEC_MULTICHASSIS.read_text(encoding="utf-8"))
        outcome = run_generator(usable, "allocate", spec, tmp_path, timeout=120)
        assert outcome["ok"], outcome.get("error")

        allocation = outcome["result"]["allocation"]
        cons = {
            ch.get("con")
            for lines in allocation.values()
            for chans in lines.values()
            for ch in chans
        }
        # The spec pins q1 to con1 and q2/q3 to con2 — both must be used.
        assert cons == {1, 2}, f"expected channels on con1 and con2, got {cons}"

    def test_real_build_is_controller_aware_and_links_intra_chassis(self, tmp_path):
        usable = _first_usable_qm_env()
        if not usable:
            pytest.skip("no conda env with the QM stack available")

        spec = json.loads(SAMPLE_SPEC_MULTICHASSIS.read_text(encoding="utf-8"))
        out_dir = tmp_path / "quam_state"
        outcome = run_generator(usable, "build", spec, out_dir, timeout=180)
        assert outcome["ok"], outcome.get("error")

        wiring = json.loads((out_dir / "wiring.json").read_text(encoding="utf-8"))
        state = json.loads((out_dir / "state.json").read_text(encoding="utf-8"))

        # Both chassis appear in the built port table.
        mw_inputs = state["ports"]["mw_inputs"]
        assert "con1" in mw_inputs and "con2" in mw_inputs, (
            f"built state should span con1 and con2, got {list(mw_inputs)}"
        )

        # Each readout's input and output sit on the SAME controller.
        qubits = self._find_qubits(wiring) or {}
        assert {"q1", "q2", "q3"} <= set(qubits)
        for qname, qdata in qubits.items():
            rr = (qdata or {}).get("rr") or {}
            out_ref, in_ref = rr.get("opx_output"), rr.get("opx_input")
            if not out_ref or not in_ref:
                continue
            assert self._pointer_con(in_ref) == self._pointer_con(out_ref), (
                f"{qname}: readout in/out on different chassis "
                f"({in_ref} vs {out_ref})"
            )

        # Every downconverter pointer links to an upconverter on its OWN chassis.
        linked = 0
        for in_con, slots in mw_inputs.items():
            if not isinstance(slots, dict):
                continue
            for ports in slots.values():
                if not isinstance(ports, dict):
                    continue
                for cfg in ports.values():
                    if not isinstance(cfg, dict):
                        continue
                    dc = cfg.get("downconverter_frequency")
                    if isinstance(dc, str):
                        linked += 1
                        assert self._pointer_con(dc) == in_con, (
                            f"downconverter on {in_con} cross-links to {dc}"
                        )
        assert linked >= 2, f"expected >=2 downconverter links, got {linked}"


# ---------------------------------------------------------------------------
# Phase 2.1 — parallel env probe + persistent cache (§2.1)
# ---------------------------------------------------------------------------


class TestProbeEnvsCaching:
    """``probe_envs`` should:

    1. Hit the on-disk cache for an interpreter whose mtime hasn't moved
       since the last probe, returning the cached result tagged
       ``cached=True``.
    2. Re-probe when the mtime moves (e.g. env reinstall).
    3. Run probes for fresh interpreters in parallel.
    """

    def test_cached_result_reused_for_same_mtime(self, tmp_path, monkeypatch):
        from quam_state_manager.core import config_generator as cg

        # Create a fake interpreter file so mtime is meaningful.
        fake_py = tmp_path / "envA" / "bin" / "python"
        fake_py.parent.mkdir(parents=True)
        fake_py.write_text("#!/bin/bash\necho", encoding="utf-8")

        calls = []

        def fake_probe(path):
            calls.append(path)
            return {
                "python": "3.11.0",
                "versions": {"qualang_tools": "1.0", "quam_builder": "1.0", "quam": "1.0", "qm": "1.0"},
                "usable": True, "missing": [], "error": None,
            }

        monkeypatch.setattr(cg, "probe_env", fake_probe)
        # First call probes; second hits the cache.
        first = cg.probe_envs([str(fake_py)], instance_path=tmp_path)
        second = cg.probe_envs([str(fake_py)], instance_path=tmp_path)
        assert len(calls) == 1, f"expected 1 probe, got {len(calls)}"
        assert first[str(fake_py)]["usable"] is True
        assert second[str(fake_py)].get("cached") is True

    def test_mtime_change_invalidates_cache(self, tmp_path, monkeypatch):
        import os, time as _time
        from quam_state_manager.core import config_generator as cg

        fake_py = tmp_path / "envA" / "bin" / "python"
        fake_py.parent.mkdir(parents=True)
        fake_py.write_text("#!/bin/bash\necho", encoding="utf-8")

        calls = []

        def fake_probe(path):
            calls.append(path)
            return {"python": "3.11", "versions": {}, "usable": False, "missing": [], "error": None}

        monkeypatch.setattr(cg, "probe_env", fake_probe)
        cg.probe_envs([str(fake_py)], instance_path=tmp_path)
        assert len(calls) == 1

        # Bump the interpreter's mtime to simulate a reinstall.
        new_mt = fake_py.stat().st_mtime + 100
        os.utime(fake_py, (new_mt, new_mt))
        cg.probe_envs([str(fake_py)], instance_path=tmp_path)
        assert len(calls) == 2, "mtime change must trigger a fresh probe"

    def test_parallel_fanout_uses_thread_pool(self, tmp_path, monkeypatch):
        """If 6 envs are passed and each fake probe sleeps 200 ms, total
        wall time should be well under the serial 1.2 s — the pool runs
        them concurrently."""
        import time
        from quam_state_manager.core import config_generator as cg

        paths = []
        for i in range(6):
            py = tmp_path / f"env{i}" / "bin" / "python"
            py.parent.mkdir(parents=True)
            py.write_text("#!/bin/bash", encoding="utf-8")
            paths.append(str(py))

        def slow_probe(path):
            time.sleep(0.2)
            return {"python": "3.11", "versions": {}, "usable": False, "missing": [], "error": None}

        monkeypatch.setattr(cg, "probe_env", slow_probe)
        start = time.time()
        cg.probe_envs(paths, instance_path=None, max_workers=4)
        elapsed = time.time() - start
        assert elapsed < 1.0, f"parallel probe took {elapsed:.2f}s; expected < 1.0s"

    def test_pip_install_invalidates_cache_without_binary_touch(self, tmp_path, monkeypatch):
        """The load-bearing regression: a ``pip install`` writes a dist-info dir
        under site-packages (bumping THAT dir's mtime) but never touches the
        interpreter binary. The old cache keyed only on the binary's mtime →
        served a stale usable/version verdict. The env-signature must fold in
        site-packages mtime so this re-probes."""
        import os
        from quam_state_manager.core import config_generator as cg

        env = tmp_path / "envA"
        fake_py = env / "bin" / "python"
        fake_py.parent.mkdir(parents=True)
        fake_py.write_text("#!/bin/bash\necho", encoding="utf-8")
        # A conda/venv posix layout so _site_packages_for resolves it.
        site = env / "lib" / "python3.11" / "site-packages"
        site.mkdir(parents=True)

        calls = []

        def fake_probe(path):
            calls.append(path)
            return {"python": "3.11", "versions": {}, "usable": False, "missing": [], "error": None}

        monkeypatch.setattr(cg, "probe_env", fake_probe)
        cg.probe_envs([str(fake_py)], instance_path=tmp_path)
        assert len(calls) == 1

        # Simulate `pip install foo`: a new dist-info under site-packages bumps
        # the site-packages dir mtime; the interpreter binary is left untouched.
        (site / "foo-1.0.dist-info").mkdir()
        bumped = site.stat().st_mtime + 100
        os.utime(site, (bumped, bumped))
        py_mtime_before = fake_py.stat().st_mtime

        cg.probe_envs([str(fake_py)], instance_path=tmp_path)
        assert fake_py.stat().st_mtime == py_mtime_before, "test must not touch the binary"
        assert len(calls) == 2, "a site-packages change (pip install) must re-probe"

    def test_site_packages_resolves_common_layouts(self, tmp_path):
        from quam_state_manager.core import config_generator as cg

        # posix conda/venv: <env>/bin/python -> <env>/lib/pythonX.Y/site-packages
        posix_env = tmp_path / "conda"
        (posix_env / "bin").mkdir(parents=True)
        (posix_env / "bin" / "python").write_text("x", encoding="utf-8")
        (posix_env / "lib" / "python3.12" / "site-packages").mkdir(parents=True)
        assert cg._site_packages_for(str(posix_env / "bin" / "python")) == \
            posix_env / "lib" / "python3.12" / "site-packages"

        # windows conda: <env>/python.exe -> <env>/Lib/site-packages
        win_env = tmp_path / "winconda"
        win_env.mkdir()
        (win_env / "python.exe").write_text("x", encoding="utf-8")
        (win_env / "Lib" / "site-packages").mkdir(parents=True)
        assert cg._site_packages_for(str(win_env / "python.exe")) == \
            win_env / "Lib" / "site-packages"

        # windows venv: <env>/Scripts/python.exe -> <env>/Lib/site-packages
        wenv = tmp_path / "winvenv"
        (wenv / "Scripts").mkdir(parents=True)
        (wenv / "Scripts" / "python.exe").write_text("x", encoding="utf-8")
        (wenv / "Lib" / "site-packages").mkdir(parents=True)
        assert cg._site_packages_for(str(wenv / "Scripts" / "python.exe")) == \
            wenv / "Lib" / "site-packages"

        # unresolvable → None (caller then re-probes rather than caching stale)
        assert cg._site_packages_for(str(tmp_path / "nope" / "bin" / "python")) is None
