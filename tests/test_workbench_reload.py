"""Customer, 2026-09-11: "qualibrate sync를 누르고 그 다음에 주소나 포트 바꿔서
reload 버튼 눌러도 아무런 반응이 없음."

MEASURED in real Chrome, and the cause is the opposite of how it looks. The
button worked all along: `setUrl` wrote localStorage and the iframe's `src`
really did change (8001 → 8002, measured). What never happened was any
REACTION —

    port changed to a dead address, polled every 200 ms for 11 s:
    fb_show = false at 207 · 2203 · 4206 · 6204 · 8210 · 10205 ms

— because the panel that exists to say "Qualibrate didn't load in the frame"
was cancelled by the very failure it exists to report:

    loadTimer = setTimeout(show fallback, 9000);
    qb.addEventListener("load", () => clearTimeout(loadTimer));

**An iframe fires `load` on a FAILED navigation.** Measured at 2044 ms on a
dead address: Chrome's error page is a load. So the timer was always cancelled
at ~2 s and a wrong address produced silence for ever.

After: the press is acknowledged at once, and a dead address says so in
**308 ms** ("Nothing answered at http://127.0.0.1:59997"); a live framable one
clears in 310 ms and stays quiet.
"""

from __future__ import annotations

from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
_WB = _ROOT / "quam_state_manager" / "web" / "templates" / "workbench.html"


def _script() -> str:
    s = _WB.read_text(encoding="utf-8")
    return s[s.index("function loadQb(url)"):s.index("// ── Split init")]


class TestTheReloadButtonAlwaysReacts:
    def test_the_press_is_acknowledged_before_anything_can_fail(self):
        """The whole report is "아무런 반응이 없음". Whatever happens next, the
        press itself has to land visibly."""
        blk = _script()
        i_ack = blk.index('fbShow("Connecting to "')
        assert i_ack < blk.index("qb.src = url"), (
            "the acknowledgement must be up before the navigation starts")

    def test_the_iframe_load_event_is_not_trusted_on_its_own(self):
        """It fires on a failed navigation — measured at 2044 ms on a dead
        address — so using it to cancel the failure panel is what caused the
        silence."""
        s = _WB.read_text(encoding="utf-8")
        assert 'qb.addEventListener("load", function () { clearTimeout(loadTimer); });' not in s, (
            "the bare cancel is back; a failed navigation would silence the panel again")
        handler = s[s.index('qb.addEventListener("load"'):]
        handler = handler[:handler.index("// ── Split init")]
        # it may only clear after the address has been shown to answer
        assert "fetch(" in handler
        assert handler.index("fetch(") < handler.index("clearTimeout(loadTimer)")

    def test_the_address_is_probed_from_the_parent(self):
        """Only the parent can tell "nothing is listening" from "something
        answered"; the frame cannot, and its load event lies."""
        blk = _script()
        assert 'fetch(url, { mode: "no-cors", cache: "no-store" })' in blk
        assert "alive === false" in blk

    def test_the_two_failures_are_told_apart(self):
        """They need different actions from the person: start Qualibrate, or
        open it in a tab because it refuses to be framed."""
        blk = _script()
        assert 'fbShow("Nothing answered at "' in blk
        assert "X-Frame-Options" in blk and "framing header" in blk
        # the framing message is only reached when the address DID answer
        i = blk.index("framing header")
        window = blk[max(0, i - 400):i]
        assert "if (alive)" in window

    def test_a_later_press_wins(self):
        """Two presses in a row must not leave the first one's verdict on
        screen."""
        blk = _script()
        assert "loadSeq" in blk
        assert blk.count("seq !== loadSeq") >= 3

    def test_the_wait_is_shorter_than_it_was(self):
        blk = _script()
        assert "FRAME_WAIT_MS" in _WB.read_text(encoding="utf-8")
        wait = _WB.read_text(encoding="utf-8")
        i = wait.index("var FRAME_WAIT_MS = ")
        val = int(wait[i:].split("=")[1].split(";")[0].strip())
        assert val <= 6000, val

    def test_the_fallback_still_offers_the_way_out(self):
        s = _WB.read_text(encoding="utf-8")
        assert 'id="wb-fallback-link"' in s
        assert "Open Qualibrate in a new tab" in s
