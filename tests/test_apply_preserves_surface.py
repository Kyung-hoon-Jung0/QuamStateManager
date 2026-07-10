"""Guard: a clean one-click "Apply to live" must NOT reset the page or blank the
open inspector/edit screen.

SUPER-CRITICAL regression: editing a pulse param then clicking "⚡ Apply to live
now" reset the whole Pulses page (doStateSync called _softRefreshLiveSurface, which
re-fetches /pulses into #table-pane) AND blanked the edit screen (closeInspector
emptied #inspector-pane) — because the one-click ⚡ routed the apply through
doStateSync('apply'). The old tray "Apply to live chip" was a plain hx-post that
swapped only #pending-tray, so the page + inspector survived.

doStateSync must skip BOTH destructive calls for a clean apply (status ok, mode
apply) — the user's own edits were just pushed to live, so the surface + inspector
already show the correct, applied values — and instead fire the gentle
`pulses-changed` refresh (the Pulses rows re-render in place without touching
#inspector-pane). The pull modes (discard/reapply) and apply-conflicts still refresh.

This is client-side behavior pytest can't run, so pin the source contract as a
tripwire — a future refactor that drops the guard fails here, loudly."""

from pathlib import Path

_APP_JS = (Path(__file__).resolve().parent.parent
           / "quam_state_manager" / "web" / "static" / "app.js").read_text(encoding="utf-8")


def _do_state_sync_body() -> str:
    i = _APP_JS.index("window.doStateSync = function")
    return _APP_JS[i:i + 6500]


class TestCleanApplyPreservesSurface:
    def test_clean_apply_flag_defined(self):
        assert ('cleanApply = (data.status === "ok" && data.mode === "apply")'
                in _do_state_sync_body())

    def test_surface_refresh_gated_on_clean_apply(self):
        # _softRefreshLiveSurface (the page reset) must stay gated: a clean apply
        # skips it UNLESS the screen provably no longer matches the working copy —
        # the replay dropped edits, or the pull absorbed other live changes
        # (pulled_other_changes). A blanket refresh on every apply is the
        # blink/freeze regression; an unconditional skip is the stale-grid one.
        b = _do_state_sync_body()
        assert "if (!cleanApply || replayFailed || data.pulled_other_changes)" in b
        assert "_softRefreshLiveSurface();" in b

    def test_inspector_close_gated_on_clean_apply(self):
        b = _do_state_sync_body()
        # closeInspector (the edit-screen blank) only runs when NOT a clean apply.
        assert "if (!cleanApply) {" in b
        assert "window.closeInspector()" in b

    def test_clean_apply_fires_gentle_pulses_refresh(self):
        # A clean apply still gently refreshes the Pulses rows (clears pending
        # markers) without blanking the inspector.
        assert 'htmx.trigger(document.body, "pulses-changed")' in _do_state_sync_body()

    def test_pulses_rows_refresh_targets_only_the_rows(self):
        # The pulses-changed refresh element re-renders ONLY the rows wrapper (into
        # itself, innerHTML, no hx-target) — so the gentle refresh never blanks
        # #inspector-pane (the edit screen). (#inspector-pane appearing ELSEWHERE in
        # the template is the row-click→load-inspector target, which is correct.)
        import re
        tpl = (Path(__file__).resolve().parent.parent / "quam_state_manager"
               / "web" / "templates" / "_pulses.html").read_text(encoding="utf-8")
        m = re.search(r'<div id="pulses-rows-wrap"[^>]*>', tpl)
        assert m, "pulses-rows-wrap element not found"
        block = m.group(0)
        assert 'hx-trigger="pulses-changed from:body"' in block
        assert 'hx-swap="innerHTML"' in block
        assert "inspector-pane" not in block  # the REFRESH element doesn't touch it
