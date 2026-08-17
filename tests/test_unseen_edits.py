"""An Apply may not write edits the presser never saw (docs/120 item 22).

Two State Manager windows on one machine share ONE server context, so they
share one change log — and a tray only refreshes on its own actions. Reproduced
in real Chrome against the customer's 20-qubit chip: tab B, opened first and
then left alone, showed ``data-change-count="0"`` and "● Synced" while tab A
typed an edit; tab B's **Apply to live now** answered
``{"replay":{"applied":1}}`` and put tab A's value on the instrument.

The covenant asks for one explicit press per direct live write. That press was
made against a screen showing nothing to write, so it cannot have meant it.

The consent record is the tray's own ``data-change-count`` — what the presser
was looking at. The gate compares it with what the server holds and refuses
only when the server holds MORE. Deliberately count-based, not seq-based: a
sequence number runs ahead for harmless reasons (a save, another window merely
reloading) and a refusal on the one button that must stay trustworthy is only
acceptable when the change SET really is bigger than the screen said.
"""

from __future__ import annotations

import json

import pytest

from quam_state_manager.web.app import create_app


def _state() -> dict:
    return {
        "qubits": {
            "q1": {"id": "q1", "f_01": 6.1e9, "T1": 1.2e-5},
            "q2": {"id": "q2", "f_01": 6.3e9, "T1": 1.4e-5},
        },
        "active_qubit_names": ["q1", "q2"],
    }


@pytest.fixture
def app_client(tmp_path):
    folder = tmp_path / "quam_state"
    folder.mkdir()
    (folder / "state.json").write_text(json.dumps(_state()), encoding="utf-8")
    (folder / "wiring.json").write_text(
        json.dumps({"network": {"host": "1.2.3.4"}}), encoding="utf-8")
    app = create_app(testing=True, instance_path=str(tmp_path / "_i"))
    c = app.test_client()
    c.post("/load", data={"folder": str(folder)})
    c._app = app
    c._folder = folder
    return c


def _live(client) -> dict:
    return json.loads((client._folder / "state.json").read_text(encoding="utf-8"))


def _edit(client, path, value):
    return client.post("/field/edit", data={"dot_path": path, "value": value})


class TestTheOrdinaryPathIsUntouched:
    """A guard that fires on the normal path is worse than the hole it closes."""

    def test_apply_with_a_matching_view_goes_through(self, app_client):
        _edit(app_client, "qubits.q1.f_01", "6.2e9")
        r = app_client.post("/state/sync", data={"mode": "apply", "seen_changes": "1"})
        assert r.status_code == 200, r.get_data(as_text=True)
        assert (r.get_json() or {}).get("status") == "ok"
        assert _live(app_client)["qubits"]["q1"]["f_01"] == 6.2e9

    def test_several_of_my_own_edits_go_through(self, app_client):
        _edit(app_client, "qubits.q1.f_01", "6.2e9")
        _edit(app_client, "qubits.q2.f_01", "6.4e9")
        _edit(app_client, "qubits.q1.T1", "1.3e-5")
        r = app_client.post("/state/sync", data={"mode": "apply", "seen_changes": "3"})
        assert r.status_code == 200, r.get_data(as_text=True)
        live = _live(app_client)
        assert live["qubits"]["q1"]["f_01"] == 6.2e9
        assert live["qubits"]["q2"]["f_01"] == 6.4e9

    def test_a_client_that_sends_nothing_behaves_exactly_as_before(self, app_client):
        """Absent parameter ⇒ no gate. No caller can be refused by opting out
        of a mechanism it does not know about."""
        _edit(app_client, "qubits.q1.f_01", "6.2e9")
        r = app_client.post("/state/sync", data={"mode": "apply"})
        assert r.status_code == 200
        assert _live(app_client)["qubits"]["q1"]["f_01"] == 6.2e9

    def test_a_view_ahead_of_the_server_is_not_a_refusal(self, app_client):
        """Only MORE-on-the-server is the harm; a stale-high count is not."""
        _edit(app_client, "qubits.q1.f_01", "6.2e9")
        r = app_client.post("/state/sync", data={"mode": "apply", "seen_changes": "9"})
        assert r.status_code == 200

    def test_garbage_is_ignored_rather_than_500(self, app_client):
        _edit(app_client, "qubits.q1.f_01", "6.2e9")
        r = app_client.post("/state/sync", data={"mode": "apply",
                                                 "seen_changes": "not-a-number"})
        assert r.status_code == 200


class TestTheOtherWindowsEditIsRefused:
    def test_apply_refuses_and_writes_nothing(self, app_client):
        """The reproduction, as a pin."""
        _edit(app_client, "qubits.q1.f_01", "6.2e9")     # the OTHER window
        before = _live(app_client)["qubits"]["q1"]["f_01"]
        r = app_client.post("/state/sync",
                            data={"mode": "apply", "seen_changes": "0"})
        assert r.status_code == 409
        body = r.get_json() or {}
        assert body["status"] == "unseen_changes"
        assert body["have"] == 1 and body["seen"] == 0
        assert _live(app_client)["qubits"]["q1"]["f_01"] == before, "nothing written"

    def test_it_names_the_paths_rather_than_only_counting(self, app_client):
        _edit(app_client, "qubits.q1.f_01", "6.2e9")
        _edit(app_client, "qubits.q2.T1", "1.9e-5")
        r = app_client.post("/state/sync", data={"mode": "apply", "seen_changes": "0"})
        paths = (r.get_json() or {}).get("paths") or []
        assert "qubits.q1.f_01" in paths and "qubits.q2.T1" in paths

    def test_only_the_unseen_tail_is_named(self, app_client):
        """The user saw the first one; the report is about the rest."""
        _edit(app_client, "qubits.q1.f_01", "6.2e9")
        _edit(app_client, "qubits.q2.T1", "1.9e-5")
        r = app_client.post("/state/sync", data={"mode": "apply", "seen_changes": "1"})
        assert r.status_code == 409
        body = r.get_json() or {}
        assert body["paths"] == ["qubits.q2.T1"]

    def test_the_second_door_is_gated_too(self, app_client):
        """`/state/apply-to-live` writes live as well — one hole is no fix."""
        _edit(app_client, "qubits.q1.f_01", "6.2e9")
        before = _live(app_client)["qubits"]["q1"]["f_01"]
        r = app_client.post("/state/apply-to-live", data={"seen_changes": "0"})
        assert r.status_code == 409
        assert _live(app_client)["qubits"]["q1"]["f_01"] == before

    def test_acknowledging_it_applies_everything(self, app_client):
        """Never a dead end: the user is told what would go, and one click
        accepts it."""
        _edit(app_client, "qubits.q1.f_01", "6.2e9")
        r = app_client.post("/state/sync", data={"mode": "apply",
                                                 "seen_changes": "0",
                                                 "ack_unseen": "1"})
        assert r.status_code == 200, r.get_data(as_text=True)
        assert _live(app_client)["qubits"]["q1"]["f_01"] == 6.2e9

    def test_force_does_not_double_as_the_acknowledgement(self, app_client):
        """`force=1` answers the STALENESS question — a different question,
        asked against a different screen. One token never collapses two gates
        (docs/41), or resolving a staleness conflict would silently consent to
        another window's edits too."""
        _edit(app_client, "qubits.q1.f_01", "6.2e9")
        r = app_client.post("/state/sync", data={"mode": "apply",
                                                 "seen_changes": "0", "force": "1"})
        assert r.status_code == 409
        assert (r.get_json() or {}).get("status") == "unseen_changes"

    def test_a_pull_is_never_gated(self, app_client):
        """Only the LIVE WRITE is the consent question. Pulling touches the
        working copy alone and must stay one click."""
        _edit(app_client, "qubits.q1.f_01", "6.2e9")
        r = app_client.post("/state/sync", data={"mode": "reapply",
                                                 "seen_changes": "0"})
        assert r.status_code == 200, r.get_data(as_text=True)


class TestTheTopBarPublishesItsRealHeight:
    """docs/120 item 23 — `--topbar-height` declared 48px while the rendered
    bar (a wrapping <nav>) measured 201px @1600, 229 @1280, 254 @1024. Every
    `calc(100vh - var(--topbar-height))` panel was therefore sized 150-200px
    taller than the space it had, on every page and worst on the narrow windows
    a laptop actually uses.

    A stylesheet cannot say "however tall that turns out to be", so app.js
    measures the bar and publishes it. These pins guard the two ways that goes
    wrong silently.
    """

    def _js(self):
        from pathlib import Path
        import quam_state_manager
        return (Path(quam_state_manager.__file__).parent
                / "web" / "static" / "app.js").read_text(encoding="utf-8")

    def test_the_publisher_exists_and_is_wired_to_resize(self):
        js = self._js()
        assert "window.TopbarHeight" in js
        assert "ResizeObserver" in js.split("window.TopbarHeight")[1][:2000]
        assert "addEventListener('resize'" in js.split("window.TopbarHeight")[1][:2000]

    def test_a_hidden_bar_publishes_zero(self):
        """`html.topbar-hidden` zeroes the variable in CSS, and an inline style
        on <html> BEATS a stylesheet rule — so a hidden bar must publish 0 here
        or the dead strip comes back and the CSS rule can never win again."""
        js = self._js().split("window.TopbarHeight")[1][:2000]
        assert "topbar-hidden" in js and "return 0" in js

    def test_it_does_not_touch_document_body_at_load(self):
        """app.js loads in <head>; `document.body` is null there. The htmx
        listener binds to `document` (events bubble) for that reason."""
        js = self._js().split("window.TopbarHeight")[1][:2500]
        assert "document.body.addEventListener" not in js

    def test_the_css_fallback_is_labelled_as_one(self):
        from pathlib import Path
        import quam_state_manager
        css = (Path(quam_state_manager.__file__).parent
               / "web" / "static" / "style.css").read_text(encoding="utf-8")
        i = css.find("--topbar-height:")
        assert i != -1
        assert "fallback" in css[i:i + 200].lower(), \
            "the literal must not read as the authority any more"


class TestABuildFailureSaysWhatToDo:
    """docs/120 item 26 — the wizard printed the allocator's own exception text
    (`NotEnoughChannelsException: …`) straight through. That names the library's
    class, not the user's problem, and the problem is one the wizard can say how
    to fix."""

    def test_a_known_failure_gets_an_action(self):
        from quam_state_manager.core.config_generator import explain_build_error
        out = explain_build_error(
            "NotEnoughChannelsException: no free channel for q7:xy")
        assert "step" in out.lower(), out
        assert "re-allocate" in out.lower(), out

    def test_the_original_text_is_never_hidden(self):
        """A message that swallows the original makes the failure unreportable."""
        from quam_state_manager.core.config_generator import explain_build_error
        raw = "NotEnoughChannelsException: no free channel for q7:xy"
        assert raw in explain_build_error(raw)

    def test_an_unknown_failure_is_passed_through_verbatim(self):
        """These mappings are a convenience, not a claim to understand every
        case — anything unrecognised must arrive exactly as it was raised."""
        from quam_state_manager.core.config_generator import explain_build_error
        raw = "SomeNovelError: the sky fell in"
        assert explain_build_error(raw) == raw

    def test_empty_stays_empty(self):
        from quam_state_manager.core.config_generator import explain_build_error
        assert explain_build_error(None) == ""
        assert explain_build_error("") == ""


class TestHxOnWithoutEval:
    """docs/120 item 27 — the app sets its own CSP and deliberately omits
    'unsafe-eval'. htmx compiles every ``hx-on::…`` with
    ``new Function("event", body)``, so under our own policy that compile threw
    and EVERY ``hx-on::after-request`` in this codebase silently never ran. One
    CSP violation per page in the console was the only sign, and one of the
    seven had already been reported by hand as "the Auto-Sync popup does not
    close after Save".

    Verifying it needed care, and two probes lied before one held up: a
    `new Function` evaluated from a debugger-injected script returns a value
    quite happily, which proves nothing about what the page's own htmx can do.
    What settled it: attach an ``hx-on::after-request`` that writes into the
    DOM, fire the request, read the DOM back — it stayed unwritten while a CSP
    `new Function` violation appeared, and after the change the same probe on a
    ``data-after-request`` handler writes and the violation count goes 1 -> 0.
    """

    def _templates(self):
        from pathlib import Path
        import quam_state_manager
        root = Path(quam_state_manager.__file__).parent / "web" / "templates"
        return {p.name: p.read_text(encoding="utf-8") for p in root.glob("*.html")}

    def _app_js(self):
        from pathlib import Path
        import quam_state_manager
        return (Path(quam_state_manager.__file__).parent / "web" / "static"
                / "app.js").read_text(encoding="utf-8")

    def test_no_template_uses_hx_on(self):
        """The attribute cannot work under this app's CSP, so its presence is
        always a dead handler — not a style preference."""
        import re
        offenders = []
        for name, src in self._templates().items():
            # Strip Jinja comments first — the templates EXPLAIN why the
            # attribute is not used, and prose about a rule must not trip it.
            body = re.sub(r"\{#.*?#\}", "", src, flags=re.S)
            if re.search(r"hx-on::[\w-]+\s*=", body):
                offenders.append(name)
        assert offenders == [], offenders

    def test_the_csp_still_forbids_eval(self):
        """If someone adds 'unsafe-eval' the test above stops meaning anything,
        so pin the reason as well as the rule."""
        from quam_state_manager.web.app import _CSP
        assert "unsafe-eval" not in _CSP

    def test_every_named_action_exists_in_the_dispatcher(self):
        """A typo'd name is the one way this arrangement can fail silently."""
        import re
        used = set()
        for s in self._templates().values():
            used.update(re.findall(r'data-after-request="([^"]+)"', s))
        assert used, "no template uses the mechanism — did they get reverted?"
        js = self._app_js()
        block = js[js.find("__afterRequestActions") - 4000:]
        for name in sorted(used):
            assert (name + ":") in block, f"{name} has no handler in app.js"

    def test_the_dispatcher_is_bound_to_document(self):
        js = self._app_js()
        assert "document.addEventListener('htmx:afterRequest'" in js
