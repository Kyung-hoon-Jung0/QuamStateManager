"""Runner+agent P0 foundation (docs/78 P0a–P0e): envmatrix, corpus, the
run_fit_audit pair shim + folder-name derivation, and the figure-gen registry.

All synthetic (tmp_path); the real-archive tier lives in the job-side exit
harness (docs/78 P0 exit criterion) — committed tests never carry customer
paths (repo scrub doctrine).
"""
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from quam_state_manager.core import fit_audit as FA
from quam_state_manager.core.autofit import corpus, envmatrix, figure_gen, sourceroot
from quam_state_manager.generator import run_fit_audit as ENG


def _git_repo(root: Path, files: dict) -> bool:
    """A tiny local git repo, or False when git is unavailable."""
    import subprocess
    root.mkdir(parents=True, exist_ok=True)
    def run(*a):
        return subprocess.run(["git", "-C", str(root), *a], capture_output=True,
                              text=True).returncode == 0
    if not run("init", "-q"):
        return False
    run("config", "user.email", "t@t"); run("config", "user.name", "t")
    for rel, content in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
    return run("add", "-A") and run("commit", "-qm", "seed")


# ---------------------------------------------------------------------------
# run_fit_audit: folder-name fallback (docs/78 §4.4)
# ---------------------------------------------------------------------------

class TestNameFromFolder:
    @pytest.mark.parametrize("folder,expected_util", [
        ("#1212_09_qubit_spectroscopy_vs_flux_041300", "qubit_spectroscopy_vs_flux"),
        ("#95_1Q_08_qubit_spectroscopy_233303", "qubit_spectroscopy"),
        ("#22_07_resonator_spectroscopy_vs_coupler_flux_011358",
         "resonator_spectroscopy_vs_coupler_flux"),
        ("#1_11_power_rabi_120000", "power_rabi"),
    ])
    def test_strips_run_id_and_timestamp(self, folder, expected_util):
        assert ENG._derive_util(ENG._name_from_folder(folder)) == expected_util

    def test_plain_name_passes_through(self):
        # a folder without the #id_/HHMMSS decorations is left alone
        assert ENG._name_from_folder("08_qubit_spectroscopy") == "08_qubit_spectroscopy"


class TestRunParams:
    """THE parameter unwrap (docs/78 §4.4). Without it the replay silently
    falls back to today's defaults instead of the run's own values."""

    def test_unwraps_model(self):
        node = {"data": {"parameters": {"model": {"num_shots": 3},
                                        "schema": {"properties": {}}}}}
        assert ENG.run_params(node) == {"num_shots": 3}

    def test_flat_layout_passes_through(self):
        node = {"data": {"parameters": {"num_shots": 7}}}
        assert ENG.run_params(node) == {"num_shots": 7}

    def test_missing_or_malformed_is_empty(self):
        assert ENG.run_params({}) == {}
        assert ENG.run_params({"data": {"parameters": ["nope"]}}) == {}
        assert ENG.run_params({"data": {"parameters": {"model": 5}}}) == {"model": 5}

    def test_every_consumer_uses_the_one_unwrap(self):
        # a private copy would drift; the figure and corpus paths must share it
        import inspect
        from quam_state_manager.generator import run_figure_gen as FG
        from quam_state_manager.core.autofit import corpus as C
        assert "RFA.run_params(" in inspect.getsource(FG.main)
        assert "run_params(node)" in inspect.getsource(C.index_run)

    def test_the_replay_runner_uses_it_too(self):
        # it used raw _deep_find, so params stayed {"model": ..., "schema": ...}
        # and every knob fell to defaults — a use_state_discrimination=True run
        # then re-processed its state-only ds_raw through convert_IQ_to_V and
        # died on KeyError 'I' (the CQT corpus, docs/127)
        import inspect
        from quam_state_manager.generator import run_autofit_replay as AR
        src = inspect.getsource(AR.main)
        assert "RFA.run_params(" in src
        assert "_deep_find(node" not in src


# ---------------------------------------------------------------------------
# run_fit_audit: pair derivation (docs/78 §4.6) — from the RUN's record, never
# guessed from the machine (q0 belongs to q0-1 AND q0-3)
# ---------------------------------------------------------------------------

def _machine(qubits=("q0", "q1", "q3"), pairs=None):
    pairs = pairs if pairs is not None else {
        "q0-1": ("q0", "q1"), "q0-3": ("q0", "q3")}
    qd = {n: SimpleNamespace(name=n) for n in qubits}
    pd = {p: SimpleNamespace(qubit_control=qd[c], qubit_target=qd[t])
          for p, (c, t) in pairs.items()}
    return SimpleNamespace(qubits=qd, qubit_pairs=pd)


class TestDerivePairs:
    def test_not_pair_shaped(self):
        m = _machine()
        assert ENG._derive_pairs(m, ["q0"], {}, []) == (None, None, None)

    def test_old_shape_cube_is_qubit_names(self):
        # cube holds the measured QUBIT name; the pair list is the run's own
        m = _machine()
        names, measured, sel = ENG._derive_pairs(
            m, ["q0"], {"qubit_pairs": ["q0-1"]}, [])
        assert names == ["q0-1"]
        assert [q.name for q in measured] == ["q0"]
        assert sel == ["q0"]

    def test_old_shape_ambiguous_membership_never_guessed(self):
        # q0 is in two pairs — the run's record (q0-3) wins, no machine guess
        m = _machine()
        names, _, _ = ENG._derive_pairs(m, ["q0"], {"qubit_pairs": ["q0-3"]}, [])
        assert names == ["q0-3"]

    def test_old_shape_unalignable_is_refused(self):
        m = _machine()
        with pytest.raises(ValueError, match="cannot align"):
            ENG._derive_pairs(m, ["q0"], {"qubit_pairs": ["q0-1", "q0-3"]}, [])

    def test_new_shape_cube_is_pair_names(self):
        m = _machine()
        names, measured, sel = ENG._derive_pairs(m, ["q0-1", "q0-3"], {}, [])
        assert names == ["q0-1", "q0-3"] == sel
        assert [q.name for q in measured] == ["q0", "q0"]   # control by default

    def test_new_shape_measure_qubit_preference(self):
        m = _machine()
        _, measured, _ = ENG._derive_pairs(
            m, ["q0-1"], {"measure_qubit": "target"}, [])
        assert [q.name for q in measured] == ["q1"]

    def test_figure_target_accepts_every_slot_vocabulary(self):
        # the two coupler families report DIFFERENT names for the same slot:
        # the resonator node keys fit_results by PAIR name, the qubit-spec node
        # by MEASURED QUBIT name (measured on real runs) — a figure request
        # carrying either must resolve to the same panel (docs/78 D-14)
        from quam_state_manager.generator import run_figure_gen as FG
        q = SimpleNamespace(name="q3")
        assert FG._aliases("q0-3", "q3", q) == {"q0-3", "q3"}
        assert FG._aliases("q0-1", "q0-1", SimpleNamespace(name="q0")) == \
            {"q0-1", "q0"}
        assert FG._aliases("q0-1", "q0-1", None) == {"q0-1"}

    def test_pairs_ride_namespace(self):
        shim = ENG._Node([], ENG._Params({}, {}), ["q0-1"])
        assert shim.namespace["qubit_pairs"].get_names() == ["q0-1"]
        bare = ENG._Node([], ENG._Params({}, {}))
        assert "qubit_pairs" not in bare.namespace


# ---------------------------------------------------------------------------
# FAMILIES registry (docs/78 P0c) + unit threading
# ---------------------------------------------------------------------------

class TestFamiliesRegistry:
    def test_nine_families_with_full_shape(self):
        assert len(FA.FAMILIES) == 9
        for fam, spec in FA.FAMILIES.items():
            for key in ("util", "value_field", "value_tol", "label"):
                assert key in spec, f"{fam} missing {key}"

    def test_figspec_matches_families(self):
        # the judge-figure registry and the replay registry must cover the
        # exact same families — a figure without a verifier (or vice versa)
        # is a silent capability gap
        assert set(figure_gen.FIGSPEC) == set(FA.FAMILIES)
        for fam, spec in figure_gen.FIGSPEC.items():
            assert spec["arg2"] in ("qubits", "pairs")
            assert spec["fn"].startswith("plot_")

    def test_codify_unit_threading(self):
        fresh = {"success": True, "idle_offset": 0.052, "deterministic": True}
        v, detail = FA._codify(True, 0.040, fresh, "idle_offset", 5e-3, "V")
        assert v == "drift"
        assert "V" in detail and "Hz" not in detail
        # dimensionless: no fabricated Hz either
        fresh = {"success": True, "opt_amp": 0.30, "deterministic": True}
        v, detail = FA._codify(True, 0.28, fresh, "opt_amp", 5e-3, "")
        assert v == "drift" and "Hz" not in detail

    def test_codify_default_unit_still_infers_hz(self):
        fresh = {"success": True, "frequency": 5.0e9 + 2e6, "deterministic": True}
        v, detail = FA._codify(True, 5.0e9, fresh, "frequency", 1e6)
        assert v == "drift" and "Hz" in detail

    @pytest.mark.parametrize("stored_val,fresh_extra", [
        (None, {"frequency": 5.0e9}),          # archive lacks the field
        (5.0e9, {}),                            # fresh fitter lacks the field
        (None, {}),                             # neither side has it
    ])
    def test_codify_missing_value_is_unverifiable_not_agrees(self, stored_val,
                                                             fresh_extra):
        # a cross-generation rename (archive stored `sweet_spot_frequency`,
        # today's fitter reports `resonator_frequency`) must NOT read as the
        # strongest verdict on evidence we do not have
        fresh = {"success": True, "deterministic": True, **fresh_extra}
        v, detail = FA._codify(True, stored_val, fresh, "frequency", 1e6)
        assert v == "unverifiable"
        assert "frequency" in detail

    def test_codify_still_agrees_when_both_numbers_match(self):
        fresh = {"success": True, "frequency": 5.0e9, "deterministic": True}
        assert FA._codify(True, 5.0e9, fresh, "frequency", 1e6)[0] == "agrees"


# ---------------------------------------------------------------------------
# envmatrix (docs/78 P0a, D-13)
# ---------------------------------------------------------------------------

def _state_dir(run: Path, classes, top=("qubits",)):
    sd = run / "quam_state"
    sd.mkdir(parents=True, exist_ok=True)
    state = {k: {} for k in top}
    state["__class__"] = classes[0]
    state.setdefault("qubits", {})["q1"] = {"__class__": classes[-1]}
    (sd / "state.json").write_text(json.dumps(state), encoding="utf-8")
    (sd / "wiring.json").write_text(json.dumps({"wiring": {}}), encoding="utf-8")
    return run


class TestEnvmatrixFingerprint:
    def test_differs_by_class_inventory(self, tmp_path):
        a = _state_dir(tmp_path / "a", ["quam.Q", "quam.Transmon"])
        b = _state_dir(tmp_path / "b", ["quam.Q", "quam.TransmonV2"])
        fa, fb = (envmatrix.generation_fingerprint(x) for x in (a, b))
        assert fa and fb and fa != fb

    def test_stable_for_same_inventory(self, tmp_path):
        a = _state_dir(tmp_path / "a", ["quam.Q", "quam.T"])
        b = _state_dir(tmp_path / "b", ["quam.Q", "quam.T"])
        assert envmatrix.generation_fingerprint(a) == \
            envmatrix.generation_fingerprint(b)

    def test_unreadable_is_none(self, tmp_path):
        assert envmatrix.generation_fingerprint(tmp_path / "nope") is None


class TestEnvmatrixClassify:
    @pytest.mark.parametrize("stage,exc,detail,expected", [
        ("load", "AttributeError", "Did you mean: 'duration_qubit'?",
         "generation_mismatch"),
        ("load", "ValueError", "Optional attributes: ['grid_location']",
         "generation_mismatch"),
        ("load", "TypeError", "got an unexpected keyword argument 'x'",
         "generation_mismatch"),
        # a NEWER state under an OLDER quam — measured on real 2026-07 runs
        # against a quam-0.5 env (docs/78 D-13)
        ("load", "AttributeError",
         'Attribute isolation is not a valid attr of Quam.twpas["twpaA"]',
         "generation_mismatch"),
        ("load", "FileNotFoundError", "no state.json", "state_unreadable"),
        ("load", "RuntimeError", "something else entirely", "error"),
        ("import", "ModuleNotFoundError", "No module named 'quam_config'",
         "no_quam_config"),
        ("import", "ModuleNotFoundError", "No module named 'quam'", "no_quam"),
        ("spawn", "TimeoutExpired", "probe exceeded", "error"),
    ])
    def test_buckets(self, stage, exc, detail, expected):
        assert envmatrix.classify(stage, exc, detail) == expected

    def test_import_bucket_uses_the_missing_module_not_the_traceback(self):
        # the traceback ALWAYS renders `from quam_config import Quam`, so text
        # sniffing labels a missing `quam` as no_quam_config and the no_quam
        # bucket becomes unreachable
        tb = ('Traceback (most recent call last):\n'
              '  File "run_quam_load_probe.py", line 52, in main\n'
              '    from quam_config import Quam\n'
              "ModuleNotFoundError: No module named 'quam'")
        assert envmatrix.classify("import", "ModuleNotFoundError", tb,
                                  "quam") == "no_quam"
        assert envmatrix.classify("import", "ModuleNotFoundError", tb,
                                  "quam_config") == "no_quam_config"
        # A DOTTED miss inside an installed package = the TREE is newer than the
        # env (measured: the live tree's quam_config imports a quam-0.6-only
        # submodule, so a quam-0.5 env cannot serve that tree at all). Calling
        # that "no quam" would send the user to reinstall what they already have.
        assert envmatrix.classify(
            "import", "ModuleNotFoundError",
            "quam_config/x.py: No module named 'quam.components._waveform_tools'",
            "quam.components._waveform_tools") == "tree_incompatible"
        assert "pinned revision" in envmatrix.explain(
            {"classification": "tree_incompatible", "lib_versions": {"quam": "0.5.0a3"}})

    def test_cacheable_buckets_are_all_real_buckets(self):
        # _CACHEABLE and classify()'s vocabulary are two hand-kept lists; a typo
        # in one silently disables (or wrongly enables) caching
        produced = {
            envmatrix.classify("import", "ModuleNotFoundError", "x", "quam"),
            envmatrix.classify("import", "ModuleNotFoundError", "x", "quam_config"),
            envmatrix.classify("import", "ModuleNotFoundError", "x", "quam.sub"),
            envmatrix.classify("load", "AttributeError", "did you mean"),
            envmatrix.classify("load", "FileNotFoundError", "x"),
            envmatrix.classify("spawn", "OSError", "x"),
            "ok",
        }
        assert set(envmatrix._CACHEABLE) <= produced
        # and every bucket must have a human sentence
        for c in produced:
            assert envmatrix.explain({"classification": c, "lib_versions": {}})

    def test_explain_never_leaks_a_traceback(self):
        for c in ("ok", "generation_mismatch", "no_quam", "no_quam_config",
                  "state_unreadable", "error"):
            msg = envmatrix.explain({"classification": c,
                                     "lib_versions": {"quam": "0.6.0"}})
            assert "Traceback" not in msg and msg


class TestEnvmatrixCache:
    @pytest.fixture(autouse=True)
    def _clean_mem(self):
        envmatrix._MEM_CACHE.clear()
        yield
        envmatrix._MEM_CACHE.clear()

    def _wire(self, monkeypatch, outcome, counter):
        def spawn(env, run, sr, timeout):
            counter.append(1)
            return dict(outcome)
        monkeypatch.setattr(envmatrix, "_spawn_probe", spawn)
        monkeypatch.setattr(envmatrix, "_env_version_sig",
                            lambda env, ip: "quam=0.6.0|py=3.11")

    def test_deterministic_outcome_cached(self, tmp_path, monkeypatch):
        run = _state_dir(tmp_path / "r", ["quam.Q"])
        calls = []
        self._wire(monkeypatch, {"ok": False, "stage": "load",
                                 "exc_type": "AttributeError",
                                 "detail": "Did you mean: 'duration_qubit'?"},
                   calls)
        e1 = envmatrix.probe_load("py.exe", run, instance_path=str(tmp_path))
        e2 = envmatrix.probe_load("py.exe", run, instance_path=str(tmp_path))
        assert e1["classification"] == "generation_mismatch"
        assert not e1["cached"] and e2["cached"]
        assert len(calls) == 1

    def test_two_envs_with_equal_versions_never_share_a_verdict(self, tmp_path,
                                                                monkeypatch):
        # quam_config is NOT a probed package, so two interpreters can report
        # identical versions and still differ on what the probe imports — the
        # interpreter must be in the key (fit_audit's own doctrine)
        run = _state_dir(tmp_path / "r", ["quam.Q"])
        outcomes = {"a.exe": {"ok": True},
                    "b.exe": {"ok": False, "stage": "import",
                              "exc_type": "ModuleNotFoundError",
                              "missing_module": "quam_config",
                              "detail": "No module named 'quam_config'"}}
        seen = []

        def spawn(env, r, sr, t):
            seen.append(env)
            return dict(outcomes[env])

        monkeypatch.setattr(envmatrix, "_spawn_probe", spawn)
        monkeypatch.setattr(envmatrix, "_env_version_sig",
                            lambda env, ip: "quam=0.6.0|py=3.11")   # IDENTICAL
        a = envmatrix.probe_load("a.exe", run, instance_path=str(tmp_path))
        b = envmatrix.probe_load("b.exe", run, instance_path=str(tmp_path))
        assert a["ok"] is True
        assert b["ok"] is False and b["classification"] == "no_quam_config"
        assert seen == ["a.exe", "b.exe"]      # b was really probed, not cached

    def test_transient_error_not_cached(self, tmp_path, monkeypatch):
        run = _state_dir(tmp_path / "r", ["quam.Q"])
        calls = []
        self._wire(monkeypatch, {"ok": False, "stage": "spawn",
                                 "exc_type": "OSError", "detail": "boom"}, calls)
        envmatrix.probe_load("py.exe", run, instance_path=str(tmp_path))
        envmatrix.probe_load("py.exe", run, instance_path=str(tmp_path))
        assert len(calls) == 2     # retried, never served stale

    def test_disk_cache_round_trip(self, tmp_path, monkeypatch):
        run = _state_dir(tmp_path / "r", ["quam.Q"])
        calls = []
        self._wire(monkeypatch, {"ok": True}, calls)
        envmatrix.probe_load("py.exe", run, instance_path=str(tmp_path))
        envmatrix._MEM_CACHE.clear()          # simulate a fresh process
        e = envmatrix.probe_load("py.exe", run, instance_path=str(tmp_path))
        assert e["cached"] and e["ok"] and len(calls) == 1

    def test_choose_env_first_ok_wins(self, tmp_path, monkeypatch):
        run = _state_dir(tmp_path / "r", ["quam.Q"])
        outcomes = {"old.exe": {"ok": True},
                    "new.exe": {"ok": False, "stage": "load",
                                "exc_type": "AttributeError",
                                "detail": "Did you mean: 'x'?"}}
        monkeypatch.setattr(envmatrix, "_spawn_probe",
                            lambda env, r, sr, t: dict(outcomes[env]))
        monkeypatch.setattr(envmatrix, "_env_version_sig",
                            lambda env, ip: f"sig-{env}")
        ch = envmatrix.choose_env(run, ["new.exe", "old.exe"])
        assert ch["env"] == "old.exe"
        assert [p["classification"] for p in ch["probes"]] == \
            ["generation_mismatch", "ok"]

    def test_choose_context_falls_back_to_a_pinned_root(self, tmp_path, monkeypatch):
        # docs/78 D-13 amendment: compatibility is (env x tree-rev x generation).
        # The live root refuses (its quam_config moved to a newer quam); a
        # pinned revision of the SAME tree serves the run.
        run = _state_dir(tmp_path / "r", ["quam.Q"])

        def spawn(env, r, sr, t):
            if sr == "/live":
                return {"ok": False, "stage": "import",
                        "exc_type": "ModuleNotFoundError",
                        "missing_module": "quam.components._waveform_tools",
                        "detail": "quam_config/x.py: No module named "
                                  "'quam.components._waveform_tools'"}
            return {"ok": True}

        monkeypatch.setattr(envmatrix, "_spawn_probe", spawn)
        monkeypatch.setattr(envmatrix, "_env_version_sig", lambda e, i: "sig")
        monkeypatch.setattr(envmatrix, "_source_sig", lambda sr: str(sr))
        ctx = envmatrix.choose_context(
            run, ["py.exe"],
            [{"path": "/live", "kind": "live", "rev": "aaa"},
             {"path": "/pin", "kind": "pinned", "rev": "bbb"}])
        assert ctx["env"] == "py.exe" and ctx["source_root"] == "/pin"
        assert ctx["root_kind"] == "pinned" and ctx["root_rev"] == "bbb"
        assert [p["classification"] for p in ctx["probes"]] == \
            ["tree_incompatible", "ok"]

    def test_no_env_fits_is_honest(self, tmp_path, monkeypatch):
        run = _state_dir(tmp_path / "r", ["quam.Q"])
        monkeypatch.setattr(envmatrix, "_spawn_probe",
                            lambda env, r, sr, t: {"ok": False, "stage": "load",
                                                   "exc_type": "AttributeError",
                                                   "detail": "Did you mean: 'x'?"})
        monkeypatch.setattr(envmatrix, "_env_version_sig",
                            lambda env, ip: f"sig-{env}")
        ch = envmatrix.choose_env(run, ["a.exe", "b.exe"])
        assert ch["env"] is None and len(ch["probes"]) == 2


# ---------------------------------------------------------------------------
# sourceroot — pinned analysis trees (docs/78 D-13 amendment)
# ---------------------------------------------------------------------------

class TestSourceRoot:
    def test_non_git_tree_degrades(self, tmp_path):
        (tmp_path / "plain").mkdir()
        assert sourceroot.is_git_tree(tmp_path / "plain") is False
        assert sourceroot.resolve_rev(tmp_path / "plain") is None
        assert sourceroot.materialize(tmp_path / "plain", "HEAD",
                                      tmp_path / "cache") is None
        # a non-git live root still offers itself, with no pinned fallbacks
        cands = sourceroot.candidates(tmp_path / "plain", tmp_path / "inst")
        assert [c["kind"] for c in cands] == ["live"]

    def test_materialize_is_read_only_and_idempotent(self, tmp_path):
        repo = tmp_path / "tree"
        if not _git_repo(repo, {"quam_config/__init__.py": "from .my import Q\n",
                                "calibration_utils/x/analysis.py": "V = 1\n"}):
            pytest.skip("git unavailable")
        before = sorted(p.name for p in repo.iterdir())
        cache = tmp_path / "cache"
        sha = sourceroot.resolve_rev(repo)
        p1 = sourceroot.materialize(repo, sha, cache)
        assert p1 and (Path(p1) / "quam_config" / "__init__.py").is_file()
        p2 = sourceroot.materialize(repo, sha, cache)     # idempotent
        assert p1 == p2
        # the customer tree is untouched: no new entries, no stray zip/staging
        assert sorted(p.name for p in repo.iterdir()) == before
        assert not list(cache.glob("*.zip")) and not list(cache.glob("*.staging"))

    def test_dirty_live_root_is_reported(self, tmp_path):
        repo = tmp_path / "tree"
        if not _git_repo(repo, {"quam_config/__init__.py": "x = 1\n"}):
            pytest.skip("git unavailable")
        assert sourceroot.is_dirty(repo) is False
        (repo / "quam_config" / "__init__.py").write_text("x = 2\n",
                                                          encoding="utf-8")
        assert sourceroot.is_dirty(repo) is True
        # candidates label the live root dirty AND still offer the clean pin —
        # exactly the situation that made older archives unreplayable
        cands = sourceroot.candidates(repo, tmp_path / "inst")
        assert [c["kind"] for c in cands] == ["live", "pinned"]
        assert cands[0]["dirty"] is True and cands[1]["dirty"] is False
        pinned = Path(cands[1]["path"]) / "quam_config" / "__init__.py"
        assert pinned.read_text(encoding="utf-8") == "x = 1\n"   # the committed one


# ---------------------------------------------------------------------------
# corpus (docs/78 P0d)
# ---------------------------------------------------------------------------

def _seed_run(root: Path, run_id: int, name: str, hhmmss: str = "120000",
              params: dict | None = None, patches: int = 0,
              with_files: bool = True, date: str = "2026-08-01"):
    d = root / date / f"#{run_id}_{name}_{hhmmss}"
    d.mkdir(parents=True)
    node = {"metadata": {"name": name, "run_start": f"{date}T12:00:00+09:00"},
            "data": {"parameters": {"model": params or {},
                                    "schema": {"properties": {}}},
                     "outcomes": {"q1": "successful"}, "quam": "./quam_state"}}
    if patches:
        node["patches"] = [{"op": "replace", "path": "/quam/x",
                            "value": 1, "old": 0}] * patches
    (d / "node.json").write_text(json.dumps(node), encoding="utf-8")
    if with_files:
        (d / "ds_raw.h5").write_bytes(b"\x89HDF")
        (d / "quam_state").mkdir()
        (d / "quam_state" / "state.json").write_text(
            json.dumps({"__class__": "quam.Q", "qubits": {}}), encoding="utf-8")
        (d / "quam_state" / "wiring.json").write_text("{}", encoding="utf-8")
        (d / "figures.amplitude.png").write_bytes(b"\x89PNG")
    return d


class TestCorpus:
    def test_index_and_family_attribution(self, tmp_path):
        _seed_run(tmp_path, 1, "08_qubit_spectroscopy",
                  params={"num_shots": 400, "frequency_step_in_mhz": 0.25,
                          "load_data_id": 7})
        _seed_run(tmp_path, 2, "07_resonator_spectroscopy_vs_coupler_flux",
                  params={"min_flux_offset_in_v": -2.5,
                          "max_flux_offset_in_v": 2.5, "num_flux_points": 101,
                          "frequency_step_in_mhz": 0.1,
                          "qubit_pairs": ["q0-1"]}, patches=2)
        _seed_run(tmp_path, 3, "99_not_a_family")
        (tmp_path / "2026-08-01" / "not_a_run").mkdir()
        idx = corpus.build_index([tmp_path], with_generation=True)
        assert len(idx["runs"]) == 3          # decorated dirs only
        assert set(idx["by_family"]) == {
            "qubit_spectroscopy", "resonator_spectroscopy_vs_coupler_flux"}
        rec = idx["runs"][idx["by_family"]["qubit_spectroscopy"][0]]
        assert rec["run_id"] == 1 and rec["has_ds_raw"] and rec["generation"]
        assert idx["runs"][1]["patches"] == 2

    def test_families_only_filter(self, tmp_path):
        _seed_run(tmp_path, 1, "99_not_a_family")
        idx = corpus.build_index([tmp_path], with_generation=False,
                                 families_only=True)
        assert idx["runs"] == []

    def test_param_ranges_exclude_plumbing(self, tmp_path):
        _seed_run(tmp_path, 1, "08_qubit_spectroscopy",
                  params={"num_shots": 3, "load_data_id": 7,
                          "reset_type": "thermal"})
        _seed_run(tmp_path, 2, "08_qubit_spectroscopy",
                  params={"num_shots": 500, "reset_type": "active"},
                  hhmmss="130000")
        rng = corpus.param_ranges(
            corpus.build_index([tmp_path], with_generation=False))
        t = rng["qubit_spectroscopy"]
        assert t["num_shots"]["min"] == 3 and t["num_shots"]["max"] == 500
        assert "load_data_id" not in t
        assert sorted(t["reset_type"]["values"]) == ["active", "thermal"]

    def test_sweep_steps_all_three_flux_vocabularies(self, tmp_path):
        _seed_run(tmp_path, 1, "06_resonator_spectroscopy_vs_flux",
                  params={"min_flux_offset_in_v": -0.5,
                          "max_flux_offset_in_v": 0.5, "num_flux_points": 101,
                          "frequency_step_in_mhz": 0.1})
        _seed_run(tmp_path, 2, "09_qubit_spectroscopy_vs_flux",
                  params={"flux_offset_span_in_v": 0.2, "num_flux_points": 21,
                          "frequency_step_in_mhz": 0.5}, hhmmss="130000")
        _seed_run(tmp_path, 3, "10_qubit_spectroscopy_vs_coupler_flux",
                  params={"min_flux": -0.5, "max_flux": 0.5,
                          "num_flux_points": 51, "frequency_step_in_mhz": 1.0,
                          "qubit_pairs": ["q0-1"]}, hhmmss="140000")
        _seed_run(tmp_path, 4, "11_power_rabi",
                  params={"amp_factor_step": 0.005}, hhmmss="150000")
        steps = corpus.sweep_steps(
            corpus.build_index([tmp_path], with_generation=False))
        assert steps["resonator_spectroscopy_vs_flux"]["flux_bias"]["median"] \
            == pytest.approx(0.01)
        assert steps["qubit_spectroscopy_vs_flux"]["flux_bias"]["median"] \
            == pytest.approx(0.01)
        assert steps["qubit_spectroscopy_vs_coupler_flux"]["flux_bias"]["median"] \
            == pytest.approx(0.02)
        assert steps["power_rabi"]["amp_prefactor"]["median"] \
            == pytest.approx(0.005)
        assert steps["resonator_spectroscopy_vs_flux"]["frequency"]["median"] \
            == pytest.approx(1e5)

    def test_nested_archive_layout_is_found(self, tmp_path):
        # real archives also nest as root/<chip>/<date>/#N (docs/68) — a
        # hard-coded 2-level walk would index this to a silent ZERO
        _seed_run(tmp_path / "chipA", 1, "08_qubit_spectroscopy")
        _seed_run(tmp_path / "chipB" / "sub", 2, "11_power_rabi")
        idx = corpus.build_index([tmp_path], with_generation=False)
        assert {r["run_id"] for r in idx["runs"]} == {1, 2}

    def test_walk_never_descends_into_a_run_folder(self, tmp_path):
        run = _seed_run(tmp_path, 1, "08_qubit_spectroscopy")
        (run / "#9_08_qubit_spectroscopy_999999").mkdir()   # decoy inside a run
        idx = corpus.build_index([tmp_path], with_generation=False)
        assert [r["run_id"] for r in idx["runs"]] == [1]

    def test_underivable_axis_reported_not_dropped(self, tmp_path):
        _seed_run(tmp_path, 1, "06_resonator_spectroscopy_vs_flux",
                  params={"frequency_step_in_mhz": 0.1})   # no flux vocab at all
        steps = corpus.sweep_steps(
            corpus.build_index([tmp_path], with_generation=False))
        assert steps["resonator_spectroscopy_vs_flux"]["flux_bias"]["n"] == 0
        assert steps["resonator_spectroscopy_vs_flux"]["flux_bias"]["median"] is None

    def test_save_load_round_trip(self, tmp_path):
        _seed_run(tmp_path, 1, "08_qubit_spectroscopy")
        idx = corpus.build_index([tmp_path], with_generation=False)
        p = tmp_path / "idx.json"
        corpus.save_index(idx, p)
        assert corpus.load_index(p)["by_family"] == idx["by_family"]
        assert corpus.load_index(tmp_path / "nope.json") is None

    def test_missing_root_tolerated(self, tmp_path):
        idx = corpus.build_index([tmp_path / "ghost"])
        assert idx["runs"] == []


# ---------------------------------------------------------------------------
# figure_gen orchestration (docs/78 P0e) — spawn monkeypatched, no env needed
# ---------------------------------------------------------------------------

class TestFigureGen:
    def test_unknown_family_is_refused_honestly(self, tmp_path):
        r = figure_gen.generate(tmp_path, family="not_a_family", env="py.exe")
        assert not r["ok"] and r["errors"][0]["stage"] == "family"

    def test_no_compatible_env_is_honest(self, tmp_path, monkeypatch):
        monkeypatch.setattr(
            "quam_state_manager.core.autofit.envmatrix.choose_context",
            lambda *a, **k: {"env": None, "source_root": None,
                             "root_kind": None, "root_rev": None,
                             "probes": [
                                 {"env": "a.exe", "ok": False,
                                  "classification": "generation_mismatch",
                                  "message": "incompatible",
                                  "lib_versions": {}}]})
        r = figure_gen.generate(tmp_path, family="qubit_spectroscopy",
                                envs=["a.exe"])
        assert not r["ok"] and r["errors"][0]["stage"] == "env"
        assert "incompatible" in r["errors"][0]["trace"]

    def test_chosen_root_rides_the_envelope(self, tmp_path, monkeypatch):
        monkeypatch.setattr(
            "quam_state_manager.core.autofit.envmatrix.choose_context",
            lambda *a, **k: {"env": "old.exe", "source_root": "/pin",
                             "root_kind": "pinned", "root_rev": "deadbeef",
                             "probes": []})
        monkeypatch.setattr(figure_gen, "_spawn", lambda *a, **k: {
            "figures": [{"target": "q1", "path": "p.png"}], "errors": [],
            "lib_versions": {"quam": "0.5.0a3"}, "gate_hash": "gh"})
        r = figure_gen.generate(tmp_path, family="power_rabi",
                                envs=["old.exe"], roots=["/live", "/pin"],
                                out_dir=tmp_path)
        # a verdict must name the analysis tree it came from (docs/78 D-13.3)
        assert r["ok"] and r["source_root"] == "/pin"
        assert r["root_kind"] == "pinned" and r["root_rev"] == "deadbeef"

    def test_success_envelope_carries_provenance(self, tmp_path, monkeypatch):
        canned = {"figures": [{"target": "q1", "path": "x/amplitude__q1.png"}],
                  "errors": [], "lib_versions": {"quam": "0.5.0a3"},
                  "gate_hash": "abc123"}
        monkeypatch.setattr(figure_gen, "_spawn", lambda *a, **k: dict(canned))
        r = figure_gen.generate(tmp_path, family="power_rabi", env="py.exe",
                                targets=["q1"], out_dir=tmp_path)
        assert r["ok"] and r["figures"][0]["target"] == "q1"
        assert r["lib_versions"]["quam"] == "0.5.0a3"
        assert r["gate_hash"] == "abc123" and r["env"] == "py.exe"

    def test_failure_envelopes_carry_every_documented_key(self, tmp_path,
                                                          monkeypatch):
        # a caller reading r["source_root"] / r["lib_versions"] must not
        # KeyError on the failure path
        keys = {"ok", "figures", "errors", "env", "probes", "source_root",
                "root_kind", "root_rev", "lib_versions", "gate_hash",
                "fit_source", "out_dir"}
        bad_family = figure_gen.generate(tmp_path, family="nope", env="py.exe")
        assert keys <= set(bad_family)
        monkeypatch.setattr(
            "quam_state_manager.core.autofit.envmatrix.choose_context",
            lambda *a, **k: {"env": None, "source_root": None,
                             "root_kind": None, "root_rev": None, "probes": []})
        no_env = figure_gen.generate(tmp_path, family="power_rabi",
                                     envs=["a.exe"])
        assert keys <= set(no_env)

    def test_out_dir_is_created_before_the_override_file(self, tmp_path,
                                                         monkeypatch):
        # the override JSON is written into out_dir by the SM side; only the
        # env-side script used to create it, which is too late
        captured = {}

        def spawn(env, run, spec, util, sr, targets, fit_source, override,
                  out_dir, timeout):
            captured["exists"] = Path(out_dir).is_dir()
            return {"figures": [{"target": "q1", "path": "p.png"}], "errors": []}

        monkeypatch.setattr(figure_gen, "_spawn", spawn)
        out = tmp_path / "fresh" / "nested"
        r = figure_gen.generate(tmp_path, family="power_rabi", env="py.exe",
                                targets=["q1"], override_fit={"q1": {"opt_amp": 0.9}},
                                out_dir=out)
        assert r["ok"] and captured["exists"] is True

    def test_env_side_script_exists_and_is_stdlib_at_import(self):
        # the generator-script contract: importable without QM installed
        import ast
        src = Path(ENG.__file__).with_name("run_figure_gen.py")
        tree = ast.parse(src.read_text(encoding="utf-8"))
        top_imports = set()
        for n in tree.body:
            if isinstance(n, ast.Import):
                top_imports.update(a.name.split(".")[0] for a in n.names)
            elif isinstance(n, ast.ImportFrom) and n.level == 0:
                top_imports.add((n.module or "").split(".")[0])
        assert not top_imports & {"xarray", "quam", "quam_config",
                                  "matplotlib", "numpy", "calibration_utils"}
