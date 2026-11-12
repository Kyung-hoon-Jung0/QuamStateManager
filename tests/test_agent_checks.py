"""Confirm / Investigate by agent: SM checks every citation and decides every
row (agents ver180, decisions D-11..D-17, D-23). Fault injection included:
a missing file, a wrong quote, a wrong state value, a path outside the roots,
a verdict with no evidence -- none of them may ever read as agreement."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from quam_state_manager.core import agent_checks as ac


@pytest.fixture()
def roots(tmp_path):
    cfg = tmp_path / "qcfg"
    cfg.mkdir()
    (cfg / "config.toml").write_text('[qualibrate.calibration_library]\nfolder   =   "D:/lab/cal"\n', encoding="utf-8")
    cal = tmp_path / "cal"
    cal.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.txt").write_text("the secret line\n", encoding="utf-8")
    return {"cfg": cfg, "cal": cal, "outside": outside, "list": [str(cal), str(cfg)]}


def _ev(ref, quote, line=2, **kw):
    return {"kind": "file", "ref": str(ref), "line": line, "quote": quote, **kw}


class TestEvidence:
    def test_present_quote_passes_whitespace_normalised(self, roots):
        r = ac.check_evidence(_ev(roots["cfg"] / "config.toml", 'folder = "D:/lab/cal"'), roots=roots["list"])
        assert r["ok"] is True

    def test_absent_quote_fails(self, roots):
        r = ac.check_evidence(_ev(roots["cfg"] / "config.toml", 'folder = "E:/other"'), roots=roots["list"])
        assert r["ok"] is False and "not at the cited line" in r["why"]

    def test_missing_file_fails(self, roots):
        r = ac.check_evidence(_ev(roots["cfg"] / "nope.toml", "x"), roots=roots["list"])
        assert r["ok"] is False and "does not exist" in r["why"]

    def test_outside_roots_fails_and_never_echoes(self, roots):
        r = ac.check_evidence(_ev(roots["outside"] / "secret.txt", "the secret line"), roots=roots["list"])
        assert r["ok"] is False and r["quote"] is None and r["ref"] is None
        assert "secret" not in json.dumps(r)

    def test_state_key_match_and_mismatch(self):
        look = {"qubits.q1.f_01": 5.1e9}.__getitem__
        ok = ac.check_evidence({"kind": "state_key", "ref": "qubits.q1.f_01", "quote": "5100000000.0"}, roots=[], state_lookup=look)
        bad = ac.check_evidence({"kind": "state_key", "ref": "qubits.q1.f_01", "quote": "4.9e9"}, roots=[], state_lookup=look)
        gone = ac.check_evidence({"kind": "state_key", "ref": "qubits.q9.f_01", "quote": "1"}, roots=[], state_lookup=look)
        assert ok["ok"] and not bad["ok"] and not gone["ok"]

    def test_caps(self):
        items = [{"for": "a", "quote": str(i)} for i in range(5)] + [{"for": f"k{i}"} for i in range(30)]
        kept, dropped = ac.cap_evidence(items)
        assert len(kept) == ac.MAX_EVIDENCE and sum(1 for k in kept if k["for"] == "a") == ac.MAX_EVIDENCE_PER_VALUE
        assert dropped == len(items) - ac.MAX_EVIDENCE


class TestParse:
    def test_last_fenced_block_wins(self):
        t = 'first\n```json\n{"a": 1}\n```\nthen\n```json\n{"a": 2}\n```\n'
        assert ac.parse_answer(t)[0] == {"a": 2}

    def test_non_json(self):
        obj, why = ac.parse_answer("I could not do that.")
        assert obj is None and "expected form" in why

    @pytest.mark.parametrize("text", [
        # claude: the final Text event of a -p stream-json turn
        'Here is what I see.\n\n```json\n{"seen": {"chip_name": "c1"}}\n```',
        # codex: the agent_message of exec --json, no language tag on the fence
        '```\n{"seen": {"chip_name": "c1"}}\n```',
    ])
    def test_both_backends_shapes(self, text):
        assert ac.parse_answer(text)[0]["seen"]["chip_name"] == "c1"


class TestCompare:
    def _sm(self, roots):
        return {"chip_name": "c1", "n_qubits": 5, "cwd": str(roots["cal"]), "config_file": str(roots["cfg"] / "config.toml"),
                "calibrations_folder_in_config": "D:/lab/cal", "node_files": ["01_a", "02_b"], "run_folders": ["#1_x"]}

    def test_agrees_disagrees_not_reported_unverified(self, roots):
        ans = {"verdict": "ok", "seen": {"chip_name": "c1", "n_qubits": 4, "cwd": str(roots["cal"]) + "\\",
                                         "calibrations_folder_in_config": "D:/lab/cal", "node_files": ["01_a.py"]},
               "evidence": [_ev(roots["cfg"] / "config.toml", 'folder = "E:/nope"', **{"for": "calibrations_folder_in_config"})]}
        rows = {r["key"]: r["agent"] for r in ac.compare_confirm(ans, self._sm(roots), roots=roots["list"])["rows"]}
        # review r2 P2-4: values the agent read through SM itself are an echo, not agreement
        assert rows["chip_name"] == "echo" and rows["n_qubits"] == "disagrees"
        assert rows["cwd"] == "echo" and rows["node_files"] == "agrees"
        assert rows["calibrations_folder_in_config"] == "unverified"
        assert rows["run_folders"] == "not reported" and rows["config_file"] == "not reported"

    def test_run_folders_compare_by_name_whatever_the_path_form(self, roots):
        # measured with the real CLI: it reports "date/run" relative paths, SM holds names
        ans = {"seen": {"run_folders": ["2026-08-31/#1_x", "D:\data\2026-09-06\#9_y"]}}
        sm = {**self._sm(roots), "run_folders": ["#1_x", "#9_y"]}
        rows = {r["key"]: r["agent"] for r in ac.compare_confirm(ans, sm, roots=roots["list"])["rows"]}
        assert rows["run_folders"] == "agrees"

    def test_verdict_ok_without_evidence_is_never_a_check(self, roots):
        ans = {"verdict": "ok", "seen": {"calibrations_folder_in_config": "D:/lab/cal"}}
        rows = {r["key"]: r["agent"] for r in ac.compare_confirm(ans, self._sm(roots), roots=roots["list"])["rows"]}
        assert rows["calibrations_folder_in_config"] == "unverified"
        res = ac.compare_confirm(ans, self._sm(roots), roots=roots["list"])
        assert "verdict" not in res and all("verdict" not in r for r in res["rows"])

    def test_verified_quote_agrees(self, roots):
        ans = {"seen": {"calibrations_folder_in_config": "D:/lab/cal"},
               "evidence": [_ev(roots["cfg"] / "config.toml", 'folder = "D:/lab/cal"', **{"for": "calibrations_folder_in_config"})]}
        rows = {r["key"]: r["agent"] for r in ac.compare_confirm(ans, self._sm(roots), roots=roots["list"])["rows"]}
        assert rows["calibrations_folder_in_config"] == "agrees"


QS = [{"id": "purcell", "kind": "choice", "options": ["yes", "no", "unknown"]},
      {"id": "notes", "kind": "text"}]


class TestInvestigate:
    def test_schema_and_asks(self, roots):
        rd = roots["cal"] / "readme.md"
        rd.write_text("x\nA Purcell filter sits on the readout line\n", encoding="utf-8")
        ans = {"answers": [{"id": "purcell", "value": "yes", "confidence": "sure",
                            "evidence": [_ev(rd, "A Purcell filter sits on the readout line")]},
                           {"id": "invented", "value": "x"},
                           {"id": "notes", "value": {"nested": 1}, "confidence": "sure"}],
               "asks": [{"id": "notes"}, {"id": "brand_new", "question": "What is the fridge?"}]}
        v = ac.vet_investigate(ans, QS, roots=roots["list"])
        by = {a["id"]: a for a in v["answers"]}
        assert by["purcell"]["value"] == "yes" and by["purcell"]["status"] == "proposed"
        assert v["dropped_ids"] == 1 and v["dropped_values"] == 1
        assert "brand_new" not in v["asks"] and v["also_asked"] == ["What is the fridge?"]
        assert v["asks"][0] == "notes"

    @pytest.mark.parametrize("ev", [
        lambda r: _ev(r["cfg"] / "missing.toml", "x"),
        lambda r: _ev(r["cfg"] / "config.toml", "not there"),
        lambda r: _ev(r["outside"] / "secret.txt", "the secret line"),
        lambda r: None,
    ])
    def test_failed_or_missing_citation_is_unsure_and_no_guess(self, roots, ev):
        e = ev(roots)
        ans = {"answers": [{"id": "purcell", "value": "yes", "confidence": "sure", "evidence": [e] if e else []}]}
        v = ac.vet_investigate(ans, QS, roots=roots["list"])
        a = v["answers"][0]
        assert a["confidence"] == "unsure" and a["value"] is None and "purcell" in v["asks"]
        assert "secret" not in json.dumps(v)

    def test_value_outside_options_is_dropped(self, roots):
        ans = {"answers": [{"id": "purcell", "value": "maybe", "confidence": "sure",
                            "evidence": [_ev(roots["cfg"] / "config.toml", 'folder = "D:/lab/cal"')]}]}
        v = ac.vet_investigate(ans, QS, roots=roots["list"])
        assert v["dropped_values"] == 1 and v["answers"][0]["value"] is None


# ---- the routes, with a fake agent answer (fault injection, D-23)
from tests.test_calibrations_resolver import pc, _cfg, _dir  # noqa: E402,F401


def _fake(monkeypatch, text, **kw):
    from quam_state_manager.web import setup_api
    seen = {}

    def fake(backend, prompt, timeout_s, model=None):
        seen["prompt"], seen["backend"] = prompt, backend
        return {"ask_id": "a1", "backend": backend, "elapsed_s": 1.0, "done": True, "answer": text,
                "failed": False, "error": None, "tools": ["mcp__sm__sm_status"], **kw}
    monkeypatch.setattr(setup_api, "_ask_and_wait", fake)
    return seen


@pytest.mark.parametrize("backend", ["claude", "codex"])
def test_confirm_route_compares_and_checks(pc, monkeypatch, backend):
    cfg_text = (pc._cdir / "projects" / "p1" / "config.toml").read_text(encoding="utf-8")
    line = [ln for ln in cfg_text.splitlines() if "folder" in ln][0]
    answer = {"verdict": "ok", "seen": {"chip_name": "WRONG", "calibrations_folder_in_config": str(pc._projcal),
                                        "storage_in_config": "E:/elsewhere"},
              "evidence": [{"for": "calibrations_folder_in_config", "kind": "file",
                            "ref": str(pc._cdir / "projects" / "p1" / "config.toml"), "line": 2, "quote": line},
                           {"for": "storage_in_config", "kind": "file", "ref": str(pc._cdir / "nope.toml"), "quote": "x"}]}
    seen = _fake(monkeypatch, "ok\n```json\n" + json.dumps(answer) + "\n```")
    r = pc.post("/api/agent/setup/confirm", json={"backend": backend})
    assert r.status_code == 200, r.get_json()
    b = r.get_json()
    rows = {x["key"]: x["agent"] for x in b["rows"]}
    assert rows["calibrations_folder_in_config"] == "agrees"
    assert rows["chip_name"] == "disagrees"
    assert rows["storage_in_config"] == "unverified"
    assert b["current"] is True and seen["backend"] == backend
    assert "READ-ONLY" in seen["prompt"]


def test_confirm_non_json_and_stale_after_switch(pc, monkeypatch):
    _fake(monkeypatch, "I looked around and all is fine.")
    b = pc.post("/api/agent/setup/confirm", json={"backend": "claude"}).get_json()
    assert b["parse_error"] and b["raw"].startswith("I looked")
    assert pc.get("/api/agent/setup/asks").get_json()["confirm"]["current"] is True
    # p2 points at the SAME folder: only the project differs, and that alone makes it stale
    (pc._cdir / "projects" / "p2").mkdir()
    (pc._cdir / "projects" / "p2" / "config.toml").write_bytes((pc._cdir / "projects" / "p1" / "config.toml").read_bytes())
    with pc._app.app_context():
        from quam_state_manager.web import routes as r
        r._active_ctx()["qualibrate_project"] = "p2"
    assert Path(pc.get("/api/agent/setup").get_json()["paths"]["calibrations"]["folder"]) == pc._projcal
    assert pc.get("/api/agent/setup/asks").get_json()["confirm"]["current"] is False


def test_one_ask_at_a_time(pc, monkeypatch):
    _fake(monkeypatch, "{}")
    pc._app.config["setup_ask_busy"] = {"kind": "investigate"}
    r = pc.post("/api/agent/setup/confirm", json={"backend": "claude"})
    assert r.status_code == 409 and r.get_json()["busy"] is True
    pc._app.config["setup_ask_busy"] = {}


def test_confirm_writes_nothing(pc, monkeypatch):
    _fake(monkeypatch, "```json\n{\"seen\": {}}\n```")
    before = {p: p.stat().st_mtime_ns for p in pc._cdir.rglob("*")}
    from quam_state_manager.core import scheduler
    with pc._app.app_context():
        from quam_state_manager.web import routes as r
        s0 = scheduler.load_settings(r._sched_inst())
        live = Path(r._active_ctx()["path"])
        st0 = (live / "state.json").read_bytes()
    pc.post("/api/agent/setup/confirm", json={"backend": "claude"})
    with pc._app.app_context():
        from quam_state_manager.web import routes as r
        assert scheduler.load_settings(r._sched_inst()) == s0
    assert before == {p: p.stat().st_mtime_ns for p in pc._cdir.rglob("*")}
    assert (live / "state.json").read_bytes() == st0


# ---- the context block round-trips the new lines (F-12, D-18..D-21)
def test_block_round_trip():
    from quam_state_manager.core import agent_setup as st
    facts = {"n_qubits": 2, "n_pairs": 1, "bias_modes": {"opx": 2}}
    answers = {"tunable": "unknown", "coupler": "none", "flux_source": "both (bias tee)", "purcell": "yes",
               "squid": "unknown", "data_read": "unknown", "never_touch": ["q1", "qubits.q2.xy"], "notes": "a\nb"}
    ev = {"purcell": [{"kind": "file", "ref": "D:/lab/cal/readme.md", "line": 4}],
          "flux_source": [{"kind": "state_key", "ref": "qubits.q1.z"}]}
    block = st.context_block(facts, answers, chip="c", data_folder="D:/data", evidence=ev)
    assert "(source: readme.md:4)" in block and "(source: qubits.q1.z)" in block
    back = st._answers_from_block(block)
    assert back == answers


def test_every_choice_has_not_sure_and_flux_is_detected():
    from quam_state_manager.core import agent_setup as st
    qs = st.questions({"bias_modes": {"qdac": 3}, "qubit_ids": ["q1"], "top_level": ["qubits"]})
    for q in qs:
        if q["kind"] == "choice":
            assert "unknown" in q["options"], q["id"]
    fq = next(q for q in qs if q["id"] == "flux_source")
    assert fq["detected"] == "QDAC" and fq["detected_counts"] == {"qdac": 3}
    assert st.detected_flux_source({"opx": 1, "qdac": 2}) == "mixed"
    assert st.detected_flux_source({"none": 5}) == "none (fixed-frequency)"
    assert st.detected_flux_source({}) == "unknown"
    nt = next(q for q in qs if q["id"] == "never_touch")
    assert "q1" in nt["tokens"] and "qubits" in nt["tokens"]


def test_never_touch_hit():
    from quam_state_manager.core import agent_setup as st
    assert st.never_touch_hit("qubits.q3.xy.operations.x180.amplitude", ["q3"]) == "q3"
    assert st.never_touch_hit("qubits.q30.xy", ["q3"]) is None
    assert st.never_touch_hit("qubits.q2.xy.RF", ["qubits.q2.xy"]) == "qubits.q2.xy"
    assert st.never_touch_hit("qubits.q2.xyz", ["qubits.q2.xy"]) is None


def _write_ctx(pc, answers, evidence=None):
    return pc.post("/api/agent/setup/context", json={"answers": answers, "evidence": evidence or {}, "apply": True,
                                                     "targets": ["claude"]})


def test_context_write_keeps_evidence_and_never_touch(pc):
    r = _write_ctx(pc, {"purcell": "yes", "never_touch": "q1, qubits.q2"},
                   {"purcell": [{"kind": "file", "ref": "x/readme.md", "line": 2}], "squid": [{"ref": "unaccepted"}]})
    assert r.status_code == 200
    from quam_state_manager.core import agent_setup as st
    rec = st.load_record(pc._app.instance_path)["context"]
    assert rec["never_touch"] == ["q1", "qubits.q2"]
    assert rec["answers_evidence"] == {"purcell": [{"kind": "file", "ref": "x/readme.md", "line": 2}]}
    text = (pc._projcal / "CLAUDE.local.md").read_text(encoding="utf-8")
    assert "- Never touch: q1, qubits.q2" in text and "(source: readme.md:2)" in text
    assert st.saved_answers(pc._app.instance_path, str(pc._projcal))["never_touch"] == ["q1", "qubits.q2"]


def test_never_touch_refuses_agents_only(pc):
    _write_ctx(pc, {"never_touch": ["q1"]})
    agent = {"X-SM-Agent": "claude"}
    r = pc.post("/field/edit", data={"dot_path": "qubits.q1.f_01", "value": "5e9"}, headers=agent)
    assert r.status_code == 403 and r.get_json()["refused"] == "never_touch"
    r = pc.post("/field/edit-batch", json={"updates": [{"dot_path": "qubits.q1.f_01", "value": 5e9}]}, headers=agent)
    assert r.status_code == 403 and r.get_json()["rule"] == "q1"
    # a person's door is untouched
    r = pc.post("/field/edit", data={"dot_path": "qubits.q1.f_01", "value": "5e9"})
    assert not (r.status_code == 403 and (r.get_json(silent=True) or {}).get("refused") == "never_touch")


def test_investigate_route_proposes_and_saves_nothing(pc, monkeypatch):
    readme = pc._projcal / "readme.md"
    readme.write_text("line one\nThis device has a Purcell filter on every readout line.\n", encoding="utf-8")
    ans = {"answers": [{"id": "purcell", "value": "yes", "confidence": "sure",
                        "evidence": [{"kind": "file", "ref": str(readme), "line": 2,
                                      "quote": "This device has a  Purcell filter on every readout line."}]},
                       {"id": "squid", "value": "symmetric", "confidence": "sure", "evidence": []},
                       {"id": "made_up", "value": "x"}],
           "asks": [{"id": "squid"}, {"id": "not_a_question"}]}
    seen = _fake(monkeypatch, "```json\n" + json.dumps(ans) + "\n```")
    from quam_state_manager.core import agent_setup as st
    rec0 = st.load_record(pc._app.instance_path)
    b = pc.post("/api/agent/setup/investigate", json={"backend": "codex"}).get_json()
    by = {a["id"]: a for a in b["answers"]}
    assert by["purcell"]["confidence"] == "sure" and by["purcell"]["status"] == "proposed"
    assert by["squid"]["value"] is None and by["squid"]["confidence"] == "unsure"
    assert b["dropped_ids"] == 1 and "not_a_question" not in b["asks"] and b["asks"][0] == "squid"
    assert "flux_source" in seen["prompt"] and "never_touch" in seen["prompt"]
    assert st.load_record(pc._app.instance_path) == rec0
    assert not (pc._projcal / "CLAUDE.local.md").exists()


def test_agent_investigate_selfcheck():
    """The REAL agent-setup.js under jsdom (tests/agent_investigate_selfcheck.cjs)."""
    import shutil
    import subprocess
    node = shutil.which("node")
    if node is None:
        pytest.skip("node not available")
    root = Path(__file__).resolve().parent.parent
    try:
        subprocess.run([node, "-e", "require('jsdom')"], check=True, capture_output=True, timeout=30)
    except Exception:
        pytest.skip("jsdom not installed")
    r = subprocess.run([node, str(root / "tests" / "agent_investigate_selfcheck.cjs")], capture_output=True, text=True,
                       encoding="utf-8", timeout=180, cwd=str(root))
    if r.returncode == 2:
        pytest.skip("jsdom not installed")
    assert r.returncode == 0, r.stdout + r.stderr
    assert r.stdout.count("ok - ") >= 20, r.stdout


# ---- review r1 fixes
class TestReviewR1:
    def test_a_short_quote_proves_nothing(self, roots):
        r = ac.check_evidence(_ev(roots["cfg"] / "config.toml", "="), roots=roots["list"])
        assert r["ok"] is False and "too short" in r["why"]

    def test_the_quote_must_be_at_the_cited_line(self, roots):
        f = roots["cfg"] / "config.toml"
        assert ac.check_evidence(_ev(f, 'folder = "D:/lab/cal"', line=99), roots=roots["list"])["ok"] is False
        assert ac.check_evidence(_ev(f, 'folder = "D:/lab/cal"', line=None), roots=roots["list"])["ok"] is False
        assert ac.check_evidence(_ev(f, 'folder = "D:/lab/cal"', line=1), roots=roots["list"])["ok"] is True  # +-1

    def test_a_real_line_number_elsewhere_fails(self, roots):
        f = roots["cfg"] / "long.toml"
        f.write_text('a = 1\nb = 2\nc = 3\nd = 4\nfolder = "D:/lab/cal"\n', encoding="utf-8")
        assert ac.check_evidence(_ev(f, 'folder = "D:/lab/cal"', line=1), roots=roots["list"])["ok"] is False
        assert ac.check_evidence(_ev(f, 'folder = "D:/lab/cal"', line=5), roots=roots["list"])["ok"] is True

    def test_confirm_quote_must_carry_the_reported_value(self, roots):
        # the quote is real and at its line, but names another folder than the agent reports
        ans = {"seen": {"calibrations_folder_in_config": "D:/lab/other"},
               "evidence": [_ev(roots["cfg"] / "config.toml", 'folder = "D:/lab/cal"', **{"for": "calibrations_folder_in_config"})]}
        rows = {r["key"]: r["agent"] for r in ac.compare_confirm(ans, {"calibrations_folder_in_config": "D:/lab/other"},
                                                                  roots=roots["list"])["rows"]}
        assert rows["calibrations_folder_in_config"] == "unverified"

    def test_never_touch_ignores_case(self):
        from quam_state_manager.core import agent_setup as st
        assert st.never_touch_hit("qubits.q1.xy", ["Q1"]) == "Q1"
        assert st.never_touch_hit("Qubits.Q2.xy.RF", ["qubits.q2.xy"]) == "qubits.q2.xy"


def test_never_touch_follows_a_pointer_alias(pc):
    """review r1 P1-2: the write lands at the resolved path -- an alias must not bypass the rule."""
    import json as _j
    with pc._app.app_context():
        from quam_state_manager.web import routes as r
        chip = Path(r._active_ctx()["path"])
    st_ = _j.loads((chip / "state.json").read_text(encoding="utf-8"))
    q1 = q2 = sorted(st_["qubits"])[0]
    field = next(k for k, v in st_["qubits"][q2].items() if isinstance(v, (int, float)) and not isinstance(v, bool))
    st_["qubits"][q1]["alias_probe"] = f"#/qubits/{q2}/{field}"
    with pc._app.app_context():
        from quam_state_manager.web import routes as r
        store = r._active_ctx()["store"]
        for d in {id(store.state): store.state, id(store.merged): store.merged}.values():
            d["qubits"][q1]["alias_probe"] = f"#/qubits/{q2}/{field}"
    alias, target = f"qubits.{q1}.alias_probe", f"qubits.{q2}.{field}"
    with pc._app.app_context():
        from quam_state_manager.web import routes as r
        assert r._resolve_edit_path(r._active_ctx()["store"], alias) == target
    _write_ctx(pc, {"never_touch": [target]})     # a dotted path rule; the alias path is outside it
    assert pc.post("/field/edit", data={"dot_path": f"qubits.{q1}.alias_probe", "value": "1"},
                   headers={"X-SM-Agent": "claude"}).status_code == 403
    agent = {"X-SM-Agent": "claude"}
    r1 = pc.post("/field/edit", data={"dot_path": alias, "value": "1"}, headers=agent)
    assert r1.status_code == 403 and r1.get_json()["refused"] == "never_touch", r1.get_json()
    r2 = pc.post("/field/edit-batch", json={"updates": [{"dot_path": alias, "value": 1}]}, headers=agent)
    assert r2.status_code == 403 and r2.get_json()["refused"] == "never_touch"


def test_a_missing_folder_created_later_is_seen(tmp_path):
    from quam_state_manager.core import calibrations
    from tests.test_calibrations_resolver import _cfg
    later = tmp_path / "later"
    cdir = _cfg(tmp_path, own_folder=later)
    inst = tmp_path / "inst"
    inst.mkdir()
    assert calibrations.resolve(inst, "p1", cfg_dir=cdir)["folder"] is None
    later.mkdir()
    assert Path(calibrations.resolve(inst, "p1", cfg_dir=cdir)["folder"]) == later


# ---- review r2 fixes
class TestReviewR2:
    def test_a_whole_short_line_is_not_enough(self, roots):
        f = roots["cal"] / "short.py"
        f.write_text("x = {\r\nx = 1\r\n}\r\nTrue\r\n", encoding="utf-8")
        for q, ln in (("}", 3), ("True", 4), ("x = 1", 2)):
            assert ac.check_evidence(_ev(f, q, line=ln), roots=roots["list"])["ok"] is False, q

    def test_an_investigate_quote_must_be_about_the_question(self, roots):
        f = roots["cal"] / "readme.md"
        f.write_text("line\nthe fridge is cold and quiet today\npurcell filter: yes on every line\n", encoding="utf-8")
        off = {"answers": [{"id": "purcell", "value": "yes", "confidence": "sure",
                            "evidence": [_ev(f, "the fridge is cold and quiet today", line=2)]}]}
        on = {"answers": [{"id": "purcell", "value": "yes", "confidence": "sure",
                           "evidence": [_ev(f, "purcell filter: yes on every line", line=3)]}]}
        assert ac.vet_investigate(off, QS, roots=roots["list"])["answers"][0]["confidence"] == "unsure"
        assert ac.vet_investigate(on, QS, roots=roots["list"])["answers"][0]["confidence"] == "sure"

    def test_a_huge_file_is_not_read(self, roots, monkeypatch):
        f = roots["cfg"] / "config.toml"
        monkeypatch.setattr(ac, "MAX_FILE_BYTES", 10)
        r = ac.check_evidence(_ev(f, 'folder = "D:/lab/cal"', line=2), roots=roots["list"])
        assert r["ok"] is False and "too large" in r["why"]

    def test_echo_rows_and_list_share(self, roots):
        sm = {"chip_name": "c1", "n_qubits": 5, "cwd": str(roots["cal"]), "node_files": ["a", "b", "c", "d", "e"]}
        one = {"seen": {"chip_name": "c1", "n_qubits": 5, "cwd": str(roots["cal"]), "node_files": ["a", "x", "y", "z"]}}
        rows = {r["key"]: r for r in ac.compare_confirm(one, sm, roots=roots["list"])["rows"]}
        assert rows["chip_name"]["agent"] == "echo" and rows["n_qubits"]["agent"] == "echo" and rows["cwd"]["agent"] == "echo"
        assert rows["node_files"]["agent"] == "disagrees" and (rows["node_files"]["matched"], rows["node_files"]["of"]) == (1, 4)
        most = {"seen": {"node_files": ["a.py", "b", "c", "d", "q"]}}
        assert {r["key"]: r["agent"] for r in ac.compare_confirm(most, sm, roots=roots["list"])["rows"]}["node_files"] == "agrees"


def test_never_touch_is_per_project_and_chip(tmp_path):
    from quam_state_manager.core import agent_setup as st
    inst = tmp_path / "i"
    st.save_never_touch(inst, "A", "chip1", ["q1"])
    assert st.never_touch_list(inst, "B", "chip1") == []          # another project's rule never applies
    assert st.never_touch_list(inst, None, "chip1") == []
    st.save_never_touch(inst, "B", "chip1", [])                    # writing B never clears A
    assert st.never_touch_list(inst, "A", "chip1") == ["q1"]


def test_never_touch_on_create_and_delete(pc):
    _write_ctx(pc, {"never_touch": ["qubits"]})
    agent = {"X-SM-Agent": "claude"}
    with pc._app.app_context():
        from quam_state_manager.web import routes as r
        q = sorted(r._active_ctx()["store"].state["qubits"])[0]
    r1 = pc.post("/field/delete", data={"dot_path": f"qubits.{q}.T1"}, headers=agent)
    assert r1.status_code == 403 and r1.get_json()["refused"] == "never_touch"
    r2 = pc.post("/field/create", data={"dot_path": f"qubits.{q}", "key": "probe", "value": "1"}, headers=agent)
    assert r2.status_code == 403 and r2.get_json()["refused"] == "never_touch"
    r3 = pc.post("/field/delete", data={"dot_path": f"qubits.{q}.T1"})
    assert not (r3.status_code == 403 and (r3.get_json(silent=True) or {}).get("refused") == "never_touch")


def test_config_row_follows_override_and_missing(pc, tmp_path):
    mine = tmp_path / "mine2"
    mine.mkdir()
    pc.post("/api/agent/setup/calibrations", json={"folder": str(mine)})
    chk = {c["key"]: c for c in pc.get("/api/agent/setup").get_json()["paths"]["checks"]}
    assert chk["config"]["ok"] is None and "override" in chk["config"]["detail"]
    pc.post("/api/agent/setup/calibrations", json={"clear": True})
    (pc._cdir / "projects" / "p1" / "config.toml").write_text("", encoding="utf-8")
    chk = {c["key"]: c for c in pc.get("/api/agent/setup").get_json()["paths"]["checks"]}
    assert chk["config"]["ok"] is False
    assert chk["data"]["label"] == "Run folders in the data folder"
