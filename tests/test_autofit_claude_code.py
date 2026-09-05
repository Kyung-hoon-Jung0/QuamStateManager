"""The autofit judge on the user's own Claude Code login (docs/169).

Customer: their lab has Claude Code on a subscription and wants the judge to
use THAT, not an API key. Proved on this machine before a line was written:
`claude -p --output-format json --json-schema` returns a schema-shaped verdict
under the OAuth login, and with `--allowedTools Read` and a PNG path in the
prompt the model reads the figure (it reported a test image's title number and
"dip"). The real Auditor was then driven end to end: `reject/noisy` on a
low-SNR resonator trace with a physics reason, `signature=clear` on the same
figure.

None of that can be a unit test -- it needs a login. What CAN be pinned is the
contract around the subprocess, and that is where every defect found on the way
lived:

  * `--bare` must NOT be passed: it skips the keychain and the same call
    answers "Not logged in".
  * the schema must carry the PARSER'S OWN enums -- with a free-text verdict
    the model answered "defer", a sensible word the parser rejects, and the
    whole call became an abstain.
  * a figure is a FILE the model reads, in order, and the file is gone after.
  * the CLI's `is_error` becomes a raised reason in the CLI's own words.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from quam_state_manager.core.autofit import auditor
from quam_state_manager.web.app import create_app

PNG = (b"\x89PNG\r\n\x1a\n" + b"\x00" * 32)          # bytes are all the provider needs


class _Run:
    """A fake subprocess.run that records the command and answers as the CLI."""

    def __init__(self, envelope: dict):
        self.calls: list[dict] = []
        self.envelope = envelope

    def __call__(self, cmd, input=None, **kw):
        # capture what existed ON DISK at call time -- the provider must have
        # written the figures before calling, and must remove them after
        files = []
        for a in cmd:
            if isinstance(a, str) and a.endswith(".png") and os.path.exists(a):
                files.append(a)
        on_disk = [p for p in _paths_in(input or "") if os.path.exists(p)]
        bytes_on_disk = [Path(p).read_bytes() for p in on_disk]
        self.calls.append({"cmd": list(cmd), "stdin": input or "", "kw": kw,
                           "figures_on_disk": on_disk, "figure_bytes": bytes_on_disk})

        class R:
            returncode = 0
            stdout = json.dumps(self.envelope)
            stderr = ""
        return R()


def _paths_in(text: str) -> list[str]:
    return [t for t in text.replace("\n", " ").split(" ")
            if t.endswith(".png")]


def _settings(**over):
    s = dict(auditor._DEFAULTS, provider="claude_code", claude_bin="claude")
    s.update(over)
    return s


class TestTheCommandLine:
    def test_never_bare_and_always_structured(self, monkeypatch):
        run = _Run({"is_error": False, "structured_output":
                    {"verdict": "accept", "failure_mode": None, "reason": "ok"}})
        monkeypatch.setattr(auditor.subprocess, "run", run) \
            if hasattr(auditor, "subprocess") else None
        monkeypatch.setattr("subprocess.run", run)
        text = auditor._call_claude_code(_settings(), {"context": {"a": 1}})
        cmd = run.calls[0]["cmd"]
        assert "--bare" not in cmd, "bare skips the keychain: 'Not logged in'"
        assert "-p" in cmd and "--output-format" in cmd and "json" in cmd
        assert "--json-schema" in cmd
        assert json.loads(text)["verdict"] == "accept"

    def test_the_schema_carries_the_parsers_enums(self, monkeypatch):
        run = _Run({"is_error": False, "structured_output": {"verdict": "accept", "reason": "x"}})
        monkeypatch.setattr("subprocess.run", run)
        auditor._call_claude_code(_settings(), {"context": {}})
        cmd = run.calls[0]["cmd"]
        schema = json.loads(cmd[cmd.index("--json-schema") + 1])
        assert schema["properties"]["verdict"]["enum"] == list(auditor.VERDICTS)
        assert set(auditor.FAILURE_MODES) <= set(
            x for x in schema["properties"]["failure_mode"]["enum"] if x)

    def test_each_ask_gets_its_own_vocabulary(self):
        assert json.loads(auditor._cc_schema("signature"))["properties"]["signature"]["enum"] \
            == list(auditor.SIGNATURES)
        assert json.loads(auditor._cc_schema("compare"))["properties"]["comparison"]["enum"] \
            == list(auditor.COMPARISONS)
        assert json.loads(auditor._cc_schema("triage"))["properties"]["state"]["enum"] \
            == list(auditor.TRIAGE_STATES)

    def test_the_ask_is_read_off_the_bundle(self):
        assert auditor._cc_ask_of({"context": {}, "system": auditor._SIGNATURE_SYSTEM}) == "signature"
        assert auditor._cc_ask_of({"context": {}, "system": auditor._TRIAGE_SYSTEM}) == "triage"
        assert auditor._cc_ask_of({"context": {}, "system": auditor._COMPARE_SYSTEM}) == "compare"
        assert auditor._cc_ask_of({"context": {"ask": "judge"}}) == "judge"

    def test_model_and_system_prompt_are_forwarded(self, monkeypatch):
        run = _Run({"is_error": False, "structured_output": {"signature": "clear", "reason": "x"}})
        monkeypatch.setattr("subprocess.run", run)
        auditor._call_claude_code(_settings(model="claude-haiku-4-5-20251001"),
                                  {"context": {}, "system": auditor._SIGNATURE_SYSTEM})
        cmd = run.calls[0]["cmd"]
        assert cmd[cmd.index("--model") + 1] == "claude-haiku-4-5-20251001"
        assert cmd[cmd.index("--system-prompt") + 1] == auditor._SIGNATURE_SYSTEM


class TestFiguresTravelAsFiles:
    def test_written_before_named_in_order_and_removed_after(self, monkeypatch):
        run = _Run({"is_error": False, "structured_output": {"comparison": "same", "reason": "x"}})
        monkeypatch.setattr("subprocess.run", run)
        import base64
        two = [base64.b64encode(PNG + b"1").decode(), base64.b64encode(PNG + b"2").decode()]
        auditor._call_claude_code(_settings(), {"context": {}, "images_b64": two,
                                                "system": auditor._COMPARE_SYSTEM})
        call = run.calls[0]
        named = _paths_in(call["stdin"])
        assert len(named) == 2, call["stdin"]
        assert named[0].endswith("figure_1.png") and named[1].endswith("figure_2.png"), \
            "the comparison ask is order-dependent"
        assert call["figures_on_disk"] == named, "the files must exist when the CLI runs"
        assert call["figure_bytes"] == [PNG + b"1", PNG + b"2"],             "figure_1 must BE the first image -- a name in order over swapped bytes is worse than no order"
        assert not any(os.path.exists(p) for p in named), "and be gone after"
        cmd = call["cmd"]
        assert cmd[cmd.index("--allowedTools") + 1] == "Read", "Read is the ONLY tool"

    def test_no_figure_means_no_tool(self, monkeypatch):
        run = _Run({"is_error": False, "structured_output": {"verdict": "accept", "reason": "x"}})
        monkeypatch.setattr("subprocess.run", run)
        auditor._call_claude_code(_settings(), {"context": {}})
        cmd = run.calls[0]["cmd"]
        assert cmd[cmd.index("--allowedTools") + 1] == ""


class TestFailuresSpeak:
    def test_not_logged_in_is_raised_in_the_clis_words(self, monkeypatch):
        run = _Run({"is_error": True, "result": "Not logged in \u00b7 Please run /login"})
        monkeypatch.setattr("subprocess.run", run)
        with pytest.raises(OSError, match="Not logged in"):
            auditor._call_claude_code(_settings(), {"context": {}})

    def test_and_the_auditor_turns_that_into_an_abstain_not_a_crash(self, monkeypatch):
        run = _Run({"is_error": True, "result": "Not logged in"})
        monkeypatch.setattr("subprocess.run", run)
        v = auditor.Auditor(_settings()).audit({"context": {}})
        assert v.verdict == "abstain"
        assert "Not logged in" in (v.reason or "")

    def test_a_timeout_is_an_abstain_not_a_crash(self, monkeypatch):
        import subprocess as sp

        def slow(cmd, input=None, **kw):
            raise sp.TimeoutExpired(cmd, kw.get("timeout"))
        monkeypatch.setattr("subprocess.run", slow)
        with pytest.raises(OSError, match="did not answer"):
            auditor._call_claude_code(_settings(), {"context": {}})
        v = auditor.Auditor(_settings()).audit({"context": {}})
        assert v.verdict == "abstain" and "did not answer" in (v.reason or "")

    def test_the_cli_gets_a_floor_on_the_api_shaped_timeout(self, monkeypatch):
        run = _Run({"is_error": False, "structured_output": {"verdict": "accept", "reason": "x"}})
        monkeypatch.setattr("subprocess.run", run)
        auditor._call_claude_code(_settings(timeout_s=60), {"context": {}})
        assert run.calls[0]["kw"]["timeout"] == 120, "60 s cuts the default model off mid-read"
        auditor._call_claude_code(_settings(timeout_s=300), {"context": {}})
        assert run.calls[1]["kw"]["timeout"] == 300, "a floor, never a cap"

    def test_a_missing_binary_disables_the_provider(self, monkeypatch):
        monkeypatch.setattr("shutil.which", lambda exe: None)
        a = auditor.Auditor(_settings(claude_bin="claude-nope"))
        assert a.enabled is False
        ok, why = auditor.claude_code_available(_settings(claude_bin="claude-nope"))
        assert ok is False and "claude-nope" in why


class TestTheDoor:
    @pytest.fixture
    def client(self, tmp_path):
        app = create_app(testing=True, instance_path=str(tmp_path / "_inst"))
        return app.test_client()

    _H = {"Origin": "http://localhost"}          # the browser sends one; the CSRF guard wants it

    def test_get_never_echoes_the_key(self, client, tmp_path):
        auditor.save_settings(str(tmp_path / "_inst"), {"provider": "anthropic", "api_key": "sk-secret"})
        d = client.get("/autofit/ai").get_json()
        assert d["has_api_key"] is True
        assert "sk-secret" not in json.dumps(d)

    def test_post_persists_and_keeps_a_saved_key(self, client, tmp_path):
        r = client.post("/autofit/ai", json={"provider": "anthropic", "api_key": "sk-1"}, headers=self._H)
        assert r.status_code == 200, r.get_data(as_text=True)
        r = client.post("/autofit/ai", json={"provider": "claude_code", "model": "m"}, headers=self._H)
        assert r.get_json()["provider"] == "claude_code"
        s = auditor.load_settings(str(tmp_path / "_inst"))
        assert s["api_key"] == "sk-1", "changing the provider must not wipe a saved key"
        assert s["model"] == "m"

    def test_clear_removes_the_key(self, client, tmp_path):
        client.post("/autofit/ai", json={"provider": "anthropic", "api_key": "sk-1"}, headers=self._H)
        client.post("/autofit/ai", json={"api_key": "CLEAR"}, headers=self._H)
        assert auditor.load_settings(str(tmp_path / "_inst"))["api_key"] == ""

    def test_an_unknown_provider_is_refused(self, client):
        r = client.post("/autofit/ai", json={"provider": "gemini"}, headers=self._H)
        assert r.status_code == 400

    def test_probe_reports_the_providers_own_reason(self, client, tmp_path, monkeypatch):
        client.post("/autofit/ai", json={"provider": "claude_code"}, headers=self._H)
        monkeypatch.setattr("shutil.which", lambda exe: "C:/claude.exe")
        run = _Run({"is_error": True, "result": "Not logged in \u00b7 Please run /login"})
        monkeypatch.setattr("subprocess.run", run)
        r = client.post("/autofit/ai/probe", headers=self._H)
        assert r.status_code == 502
        assert "Not logged in" in r.get_json()["error"]

    def test_probe_succeeds_through_the_real_dispatch(self, client, monkeypatch):
        client.post("/autofit/ai", json={"provider": "claude_code"}, headers=self._H)
        monkeypatch.setattr("shutil.which", lambda exe: "C:/claude.exe")
        run = _Run({"is_error": False, "structured_output": {"verdict": "accept", "reason": "probe ok"}})
        monkeypatch.setattr("subprocess.run", run)
        r = client.post("/autofit/ai/probe", headers=self._H)
        assert r.status_code == 200, r.get_data(as_text=True)
        assert r.get_json()["provider"] == "claude_code"

    def test_the_page_carries_the_block_and_the_readiness_chip_reads_it(self, client, tmp_path):
        client.post("/autofit/ai", json={"provider": "claude_code"}, headers=self._H)
        html = client.get("/autofit").get_data(as_text=True)
        assert 'id="autofit-ai"' in html
        assert 'value="claude_code"' in html
        assert "LLM audit: claude_code" in html


@pytest.mark.skipif(shutil.which("node") is None, reason="node not on PATH")
def test_the_settings_block_drives_the_real_js():
    """docs/169: the three handlers against the real autofit.js under jsdom --
    the first cut read fetchJSON's answer with the wrong shape and only a real
    browser noticed; this runs the real code so pytest notices."""
    root = Path(__file__).resolve().parent.parent
    r = subprocess.run(["node", str(root / "tests" / "autofit_ai_selfcheck.cjs")],
                       capture_output=True, text=True, encoding="utf-8",
                       cwd=str(root), timeout=120)
    if r.returncode == 2:
        pytest.skip("jsdom not installed (run `npm install jsdom`)")
    assert r.returncode == 0, (r.stdout + r.stderr)
    assert "all checks passed" in r.stdout, (r.stdout + r.stderr)
