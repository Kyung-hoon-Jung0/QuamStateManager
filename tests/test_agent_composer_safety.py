"""The composer must not spend the customer's money by accident, and must not
throw away the answer it is holding.

Four defects from the multi-user browser stress round (2026-09-11), each
reproduced twice in real headless Chrome against a copy of the customer's
20-qubit chip. They share one path — the one control a newcomer actually
touches — and the customer's standing complaint is "고객이 어떻게 쓸지 몰라해".

    1. `observer` hid Start and Cancel and left Send live. A non-`/run` line
       goes to POST /api/agent/chat/start, which spawns the person's own
       logged-in claude/codex. The tooltip promises "this window shows but
       never starts, stops or approves".
    2. `/help`, `/ru …` and `/RUN …` were each handed to the model — one real
       CLI session per line — and `/runn …` was silently accepted AS `/run`.
    3. The textarea was DISABLED for the 15–29 ms flight, and a disabled
       textarea receives no key events: at 45 ms/char the next line lost its
       leading `/`, which turns a deterministic /run into a model-bound
       message.
    4. A bad target ships `known` (50 names on that chip) and a bad node ships
       `available` (40 names) in the SAME response as the error — and the
       client read `error` and dropped both.
"""

from __future__ import annotations

from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent
_JS = _ROOT / "quam_state_manager" / "web" / "static" / "agent.js"


def _strip_js_comments(src: str) -> str:
    """Comments out; string literals kept.

    This is the fifth pin in this project to trip on its own explanation: the
    code below is commented with the very strings these pins look for, because
    the defect each one fixes is DESCRIBED there. A raw substring scan
    therefore passes or fails for the wrong reason. Strings stay, because the
    messages a person reads are part of what is pinned.
    """
    out: list[str] = []
    i, n = 0, len(src)
    while i < n:
        c = src[i]
        if c == '"' or c == "'":
            q, j = c, i + 1
            while j < n and src[j] != q:
                j += 2 if src[j] == "\\" else 1
            out.append(src[i:j + 1])
            i = j + 1
        elif src.startswith("//", i):
            j = src.find("\n", i)
            i = n if j < 0 else j
        elif src.startswith("/*", i):
            j = src.find("*/", i + 2)
            i = n if j < 0 else j + 2
        else:
            out.append(c)
            i += 1
    return "".join(out)


def _submit_block() -> str:
    js = _JS.read_text(encoding="utf-8")
    i = js.index("  function submit(ev) {")
    return _strip_js_comments(js[i:js.index("\n  function preset(", i)])


class TestObserverHoldsTheControlThatCostsMoney:
    def test_submit_is_guarded_like_every_other_door(self):
        blk = _submit_block()
        assert "if (S.observer) {" in blk, "the one control that spends money was unguarded"
        # …and it refuses BEFORE anything is posted
        assert blk.index("if (S.observer) {") < blk.index("api(")

    def test_every_door_carries_the_same_guard(self):
        """If a new door is added without it, this is what says so."""
        js = _JS.read_text(encoding="utf-8")
        doors = ["function startPlan(", "function cancelPlan(", "function setPlanMode(",
                 "function arm(", "function disarm(", "function endSession(",
                 "function submit("]
        code = _strip_js_comments(js)
        for d in doors:
            i = code.index(d)
            assert "S.observer" in code[i:i + 900], d

    def test_it_says_why_rather_than_doing_nothing_quietly(self):
        blk = _submit_block()
        guard = blk[blk.index("if (S.observer) {"):]
        guard = guard[:guard.index("return false;")]
        assert "toast(" in guard and "observer" in guard.lower()


class TestASlashLineNeverReachesTheModel:
    """The composer advertises `/run <node> <targets>` in its own placeholder,
    so the grammar has to answer for itself."""

    def test_the_client_refuses_an_unknown_command(self):
        blk = _submit_block()
        assert 'text.charAt(0) === "/"' in blk
        assert 'cmd !== "/run"' in blk
        # the refusal happens before either POST
        assert blk.index('cmd !== "/run"') < blk.index("/api/agent/chat/start")

    def test_a_wrong_case_command_is_told_what_is_wrong(self):
        blk = _submit_block()
        assert 'cmd.toLowerCase() === "/run"' in blk, (
            "/RUN must be told it is the case, not just 'no such command'")

    @pytest.mark.parametrize("line", ["/runn 05_rabi q1", "/RUN 05_rabi q1",
                                      "/help", "/ru x", "/run2 a b"])
    def test_the_server_refuses_it_too(self, line):
        """Defence in depth: the bridge and the API reach this without the
        browser. `startswith('/run')` ran `/runn` AS `/run`."""
        from quam_state_manager.core import agent_plans
        got = agent_plans.parse_run_line(line)
        assert got and got.get("error"), (line, got)
        assert "no " + line.split()[0] + " command" in got["error"]

    @pytest.mark.parametrize("line", ["plain text", "", "   ", "run 05 q1"])
    def test_a_line_that_is_not_a_command_still_goes_to_the_agent(self, line):
        from quam_state_manager.core import agent_plans
        assert agent_plans.parse_run_line(line) is None, line

    def test_a_real_run_line_is_untouched(self):
        from quam_state_manager.core import agent_plans
        got = agent_plans.parse_run_line("/run 05_power_rabi q1,q2 num_shots=200")
        assert got == {"node": "05_power_rabi", "targets": ["q1", "q2"],
                       "params": {"num_shots": 200}}


class TestTypingIsNeverSwallowed:
    def test_the_box_is_not_disabled_while_a_line_is_in_flight(self):
        """A disabled textarea receives NO key events, and the flight is
        15–29 ms — so the next line lost its leading `/` and became a
        model-bound message."""
        blk = _submit_block()
        assert "ta.disabled = true" not in blk, "the box must stay live"
        assert "S.sending" in blk, "a flag stops the double send instead"

    def test_the_double_send_is_still_stopped(self):
        blk = _submit_block()
        assert "if (S.sending) return false;" in blk
        assert "S.sending = true;" in blk
        assert "S.sending = false;" in blk

    def test_only_what_was_sent_is_removed_never_the_whole_box(self):
        """Anything typed ahead during the flight is the person's NEXT line."""
        blk = _submit_block()
        assert "sentText" in blk
        assert "ta.value = v.slice(sentText.length)" in blk
        # the old unconditional clear is gone
        assert "if (ok) { ta.value = \"\"; grow(ta); }" not in blk


class TestTheAnswerTheServerAlreadySentIsShown:
    def _err_block(self) -> str:
        js = _JS.read_text(encoding="utf-8")
        i = js.index("  function errText(r, fallback) {")
        return _strip_js_comments(js[i:js.index("\n  function setUnreachable(", i)])

    def test_the_names_ride_along_with_the_error(self):
        blk = self._err_block()
        assert "b.known" in blk and "b.available" in blk

    def test_a_long_list_is_capped_and_says_how_many_more(self):
        blk = self._err_block()
        assert "slice(0, 12)" in blk and "more" in blk

    def test_the_server_still_sends_them(self):
        """The client half is only worth anything while the server keeps
        putting the answer in the response."""
        api = (_ROOT / "quam_state_manager" / "web" / "agent_api.py").read_text(encoding="utf-8")
        assert "known=sorted(known" in api
        assert "available=sorted(" in api

    def test_the_message_is_not_a_python_repr(self):
        from quam_state_manager.web.agent_api import _unknown_targets_msg
        one = _unknown_targets_msg(["q0"])
        many = _unknown_targets_msg(["q0", "Q1"])
        for m in (one, many):
            assert "[" not in m and "'" not in m, m
        assert "q0" in one and "Q1" in many
        api = (_ROOT / "quam_state_manager" / "web" / "agent_api.py").read_text(encoding="utf-8")
        assert 'f"unknown targets {bad}"' not in api
