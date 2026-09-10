"""`pushUrl` is not an htmx 2 ajax option, and four call sites believed it was.

This is a SOURCE-level pin, deliberately and with its limits stated: the
option is silently ignored by htmx, so there is no runtime signal to assert
on -- the whole defect is that nothing happens and nothing complains. The one
site that IS reachable from a harness (the Trends point click) is pinned
behaviourally in ``tests/trends_provenance_selfcheck.cjs``; this file covers
the rest, which live inside IIFE-private functions reached only through a
full palette or drawer interaction.

Evidence for the claim, re-checkable at any time: the bundled
``web/static/htmx.min.js`` contains the strings "pushUrl" and "pushURL" zero
times. htmx 2's ``ajax`` forwards a fixed set of context keys (handler,
headers, values, targetOverride, swapOverride, select, source, event) and
drops the rest.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

_STATIC = Path(__file__).resolve().parent.parent / "quam_state_manager" / "web" / "static"
_SHIPPED = ("app.js", "chip-status.js")


def _code_lines(name: str) -> list[tuple[int, str]]:
    """Source lines with whole-line ``//`` comments dropped.

    A comment naming the dead option is exactly what we want to KEEP in the
    codebase -- it is how the next reader learns why the option is absent --
    so a pin that greps the raw text would forbid its own explanation.
    """
    out = []
    for n, line in enumerate(_STATIC.joinpath(name).read_text(encoding="utf-8")
                             .splitlines(), 1):
        if line.lstrip().startswith(("//", "*", "/*")):
            continue
        out.append((n, line))
    return out


class TestHtmxHasNoSuchOption:
    def test_the_bundled_htmx_really_does_not_know_it(self):
        """The premise, measured rather than remembered."""
        htmx = _STATIC.joinpath("htmx.min.js").read_text(encoding="utf-8")
        assert "pushUrl" not in htmx
        assert "pushURL" not in htmx
        # ...while the keys it DOES forward are present, so this is not a
        # vacuous "the file contains no strings" assertion.
        assert "targetOverride" in htmx and "swapOverride" in htmx

    @pytest.mark.parametrize("name", _SHIPPED)
    def test_no_shipped_call_still_passes_it(self, name):
        offenders = [(n, ln.strip()) for n, ln in _code_lines(name)
                     if re.search(r"\bpushU[rR][lL]\b", ln)]
        assert not offenders, (
            "%s still passes a dead pushUrl option: %s" % (name, offenders))


class TestTheOneSiteWhereItMattered:
    """Two of the four sites were noise; one was a real loss of function.

    The command palette's own comment said "Page navigation via HTMX so
    push-url works" -- and it did not, so the pane changed while the address
    bar did not, and Back left the app instead of returning to the page the
    user came from. That one has to actually push.
    """

    def test_the_palette_pushes_the_history_entry_itself(self):
        """The BEHAVIOUR is pinned next door.

        Review round: the first replacement for the dead option pushed from
        the ajax PROMISE, which settles the same way whether a swap happened
        or not -- so a failed pick moved the address bar to a page that never
        loaded. That class of defect is not visible to a source grep at all,
        so it is pinned by driving the real palette in
        ``tests/palette_nav_selfcheck.cjs``. What is left here is the one
        thing a grep is good for: that the dead option has not come back.
        """
        src = _STATIC.joinpath("app.js").read_text(encoding="utf-8")
        i = src.index("_pushRecent(entry);")
        block = src[i:i + 5200]   # the branch grew a comment; keep the window past it
        assert "history.pushState" in block,             "the palette's navigation branch must add the history entry itself"
        assert "htmx:afterSwap" in block,             "it must hang the entry off the SWAP, not off the ajax promise"
        assert "syncSidebarNavActive" in block,             "a raw pushState fires no htmx event -- the sidebar needs telling"

    def test_the_two_noisy_sites_pass_a_source_instead(self):
        """Where there was no history entry to add, the fix is `source`.

        htmx reads the SOURCE element's hx-sync; with no source every request
        shares body's single timeout-0 queue, so one stalled load wedges every
        later click. That is the "Datasets frozen" dead-click class, and it is
        what these two sites were actually missing.
        """
        src = _STATIC.joinpath("app.js").read_text(encoding="utf-8")
        i = src.index("window.htmx.ajax('GET', '/param-history',")
        assert "source: '#param-history-root'" in src[i - 300:i + 300], \
            "the param-history self-reload must name a source element"
