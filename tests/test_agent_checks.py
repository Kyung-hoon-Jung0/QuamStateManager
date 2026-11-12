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
        assert r["ok"] is False and "not in the file" in r["why"]

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
        assert rows["chip_name"] == "agrees" and rows["n_qubits"] == "disagrees"
        assert rows["cwd"] == "agrees" and rows["node_files"] == "agrees"
        assert rows["calibrations_folder_in_config"] == "unverified"
        assert rows["run_folders"] == "not reported" and rows["config_file"] == "not reported"

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
        ans = {"answers": [{"id": "purcell", "value": "yes", "confidence": "sure",
                            "evidence": [_ev(roots["cfg"] / "config.toml", 'folder = "D:/lab/cal"')]},
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
