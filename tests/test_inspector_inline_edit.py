"""docs/183 — the inspector's inline edit sent nothing at all.

Found by the write-path stress round's Ctrl+Z lane and reproduced independently
on a copy of the customer's KRISS 5Q chip in real headless Chrome: click a value
in the qubit or pair inspector, type, press Enter — **zero POST requests**, the
tray stays at 0, no toast, no error, and the field shows the old value again on
the next render. Both inspectors, and both ways of reaching them (full page and
the htmx partial a person actually clicks).

Root cause is a rule this project had already written down twice. The form
carried::

    hx-vals='js:{freq_sync: …, expect_chip: window.__chipToken || ""}'

htmx compiles a ``js:`` value with ``new Function``; this app's CSP is
``script-src 'self' 'unsafe-inline'`` with **no** ``unsafe-eval``; htmx throws
while evaluating and aborts the request, so the submit never becomes one.
``_state_review.html`` carries a comment saying exactly this, and docs/120 ②
fixed the identical class for every ``hx-on::after-request``.

Two things rode that dead attribute, and only one of them was cosmetic:

* ``freq_sync`` — the toolbar's f01↔RF mirror toggle, a browser preference;
* ``expect_chip`` — the docs/120 **chip-identity gate**. With the attribute dead
  it never reached the server, so a stale tab's edit was ungated. That is the
  half that was actually unsafe, and it is why this file pins the gate as well
  as the sending.

Why nothing caught it: the jsdom harnesses do not enforce CSP, and a synthetic
``form.dispatchEvent(new Event("submit"))`` — which is what a harness does —
posts perfectly well. Only a real browser with the real header can see it.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from quam_state_manager.web.app import create_app

_ROOT = Path(__file__).resolve().parent.parent
_TEMPLATES = _ROOT / "quam_state_manager" / "web" / "templates"
_STATIC = _ROOT / "quam_state_manager" / "web" / "static"

def _strip_comments(text: str) -> str:
    """Jinja and HTML comments removed — what the browser actually receives."""
    text = re.sub(r"\{#.*?#\}", " ", text, flags=re.S)
    return re.sub(r"<!--.*?-->", " ", text, flags=re.S)


_WIRING = {"network": {"host": "1.1.1.1", "cluster_name": "C1"}}


def _chip(folder: Path):
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "state.json").write_text(json.dumps({
        "qubits": {"q1": {"id": "q1", "f_01": 5.0e9, "anharmonicity": 2.0e8},
                   "q2": {"id": "q2", "f_01": 5.2e9, "anharmonicity": 2.1e8}},
        "qubit_pairs": {"q1-2": {"id": "q1-2", "detuning": 1.0e6}},
        "active_qubit_names": ["q1", "q2"],
    }), encoding="utf-8")
    (folder / "wiring.json").write_text(json.dumps(_WIRING), encoding="utf-8")


@pytest.fixture
def env(tmp_path):
    live = tmp_path / "chip"
    _chip(live)
    app = create_app(testing=True, instance_path=str(tmp_path / "_inst"))
    c = app.test_client()
    assert c.post("/load", data={"folder": str(live)}).status_code in (200, 302)
    return {"app": app, "client": c, "live": live}


class TestNoTemplateAsksForEval:
    """The structural guard. This is the third time the CSP has killed a
    behaviour silently (docs/120 ②, then this), so the rule gets a test rather
    than another comment."""

    # The attribute this whole round is about. Built from pieces so this
    # FILE does not contain the literal it is banning — a scanner that trips
    # on the test that defines it is the same comment trap, one level up.
    _BANNED = r"hx-vals\s*=\s*(['\"])" + "js:"

    def test_no_template_uses_hx_vals_js(self):
        """COMMENTS FIRST. Both the fix's own note and `_state_review.html`'s
        older one *quote* the forbidden attribute in order to warn about it, and
        a raw grep flags the warning as the offence — this project has tripped a
        pin on its own comments repeatedly. Strip Jinja and HTML comments, then
        look at what the browser would actually receive."""
        offenders = []
        for p in sorted(_TEMPLATES.rglob("*.html")):
            live = _strip_comments(p.read_text(encoding="utf-8"))
            for m in re.finditer(self._BANNED, live):
                line = live[:m.start()].count("\n") + 1
                offenders.append(f"{p.name}:~{line}")
        assert offenders == [], (
            "an hx-vals js: value is compiled with new Function, which this "
            "app's CSP forbids — htmx aborts the whole request and the press "
            "does nothing: " + ", ".join(offenders))

    def test_the_comment_stripping_does_not_hide_a_real_one(self):
        """…and the stripper must not be so eager that it eats live markup: a
        real attribute after a comment is still found, on the same line and on
        the next one. Without this, deleting the scan entirely and returning []
        would look exactly as green."""
        warn = "{# never use hx-vals=" + chr(39) + "js:{...}" + chr(39) + " #}"
        real = "<form hx-vals=" + chr(39) + "js:{a: 1}" + chr(39) + "></form>"
        assert re.search(self._BANNED, _strip_comments(warn)) is None
        assert re.search(self._BANNED, _strip_comments(warn + real))
        assert re.search(self._BANNED, _strip_comments(warn + "\n" + real))

    def test_the_csp_really_forbids_eval(self):
        """The pin above only means something while this is true."""
        csp = (_ROOT / "quam_state_manager" / "web" / "app.py").read_text(encoding="utf-8")
        i = csp.index("script-src")
        line = csp[i:csp.index("\n", i)]
        assert "unsafe-eval" not in line, line

    def test_and_the_header_is_actually_sent(self, env):
        """A CSP that is not on the response would make all of this moot in the
        other direction — the attribute would work and the guard would be
        cargo cult. It is sent."""
        r = env["client"].get("/qubits")
        csp = r.headers.get("Content-Security-Policy", "")
        assert "script-src" in csp and "unsafe-eval" not in csp, csp


class TestTheFormsCarryTheMarker:
    def test_both_inspectors_declare_what_to_inject(self, env):
        c = env["client"]
        for url in ("/qubit/q1", "/pair/q1-2"):
            html = c.get(url, headers={"HX-Request": "true"}).data.decode()
            assert "form class=\"inline-edit" in html, url
            assert 'data-inject="chip-freq"' in html, url

    def test_every_editable_form_carries_it_not_just_the_first(self, env):
        html = env["client"].get("/qubit/q1",
                                 headers={"HX-Request": "true"}).data.decode()
        forms = html.count('<form class="inline-edit')
        marked = html.count('data-inject="chip-freq"')
        assert forms > 1, forms
        assert marked == forms, (marked, forms)


class TestTheInjector:
    """One delegated listener, the docs/120 ② pattern."""

    def test_it_exists_and_is_its_own_listener(self):
        js = (_STATIC / "app.js").read_text(encoding="utf-8")
        i = js.index('data-inject~="chip-freq"')
        blk = js[max(0, i - 900):i + 500]
        assert "htmx:configRequest" in blk
        assert "parameters" in blk
        assert "freq_sync" in blk and "expect_chip" in blk

    def test_it_does_not_live_inside_the_pulses_handler(self):
        """That handler rewrites `evt.detail.path` and returns early for
        everything else; two unrelated concerns in one function is how the next
        one gets missed."""
        js = (_STATIC / "app.js").read_text(encoding="utf-8")
        inject = js.index('data-inject~="chip-freq"')
        pulses = js.index("var isPulsesReq")
        assert inject < pulses, "the injector must not sit behind the pulses early-return"


class TestTheGateItWasHiding:
    """`expect_chip` rode the dead attribute, so the inspector's chip-identity
    gate never reached the server. These pin that it is live — the half that
    was actually unsafe."""

    def _token(self, env) -> str:
        html = env["client"].get("/qubits").data.decode()
        m = re.search(r"__chipToken\s*=\s*['\"]([^'\"]*)['\"]", html)
        return m.group(1) if m else ""

    def test_a_matching_token_is_accepted(self, env):
        tok = self._token(env)
        assert tok, "the page publishes no chip token to send back"
        r = env["client"].post("/qubit/q1/edit", data={
            "dot_path": "qubits.q1.anharmonicity", "value": "2.05e8",
            "expect_chip": tok})
        assert r.status_code == 200, r.data[:200]

    def test_a_stale_token_is_refused(self, env):
        r = env["client"].post("/qubit/q1/edit", data={
            "dot_path": "qubits.q1.anharmonicity", "value": "2.05e8",
            "expect_chip": "some-other-chip"})
        assert r.status_code == 409, r.data[:200]

    def test_the_pair_door_is_gated_too(self, env):
        r = env["client"].post("/pair/q1-2/edit", data={
            "dot_path": "qubit_pairs.q1-2.detuning", "value": "2e6",
            "expect_chip": "some-other-chip"})
        assert r.status_code == 409, r.data[:200]


class TestTheEditStillWorks:
    """The route itself was never broken — only the request never arrived.
    These keep it that way."""

    def test_an_edit_reaches_the_tray(self, env):
        c = env["client"]
        assert 'data-change-count="0"' in c.get("/state/tray").data.decode()
        r = c.post("/qubit/q1/edit", data={
            "dot_path": "qubits.q1.anharmonicity", "value": "2.21e8"})
        assert r.status_code == 200
        assert 'data-change-count="1"' in c.get("/state/tray").data.decode()

    def test_freq_sync_off_is_honoured(self, env):
        """The other value that rode the dead attribute. Default ON, and an
        explicit "0" must still turn it off — otherwise the toolbar toggle was
        silently ignored along with everything else."""
        c = env["client"]
        c.post("/qubit/q1/edit", data={"dot_path": "qubits.q1.f_01",
                                       "value": "5.5e9", "freq_sync": "0"})
        tray = c.get("/state/tray").data.decode()
        assert "qubits.q1.f_01" in tray
        assert "RF_frequency" not in tray, "freq_sync=0 still mirrored"


_SELFCHECK = _ROOT / "tests" / "inspector_inject_selfcheck.cjs"


@pytest.mark.skipif(__import__("shutil").which("node") is None, reason="node not on PATH")
def test_inspector_inject_selfcheck_passes():
    """The injector EXECUTED, not grepped.

    The source pins above cannot tell you whether the listener fires, whether it
    finds the form from the element htmx actually reports (the input, not the
    form — which is why it uses ``closest``), or whether it leaves every other
    request alone. This does, against the real shipped ``app.js``.

    It also pins the one detail that would turn an ungated edit into a broken
    one: an absent chip token must inject ``""`` and never the string
    ``"undefined"``, which the server would compare against the real token and
    refuse with a 409.
    """
    import subprocess
    r = subprocess.run(
        ["node", str(_SELFCHECK)],
        capture_output=True, text=True, encoding="utf-8", cwd=str(_ROOT), timeout=180,
    )
    if r.returncode == 2:
        pytest.skip("jsdom not installed (run `npm install jsdom`)")
    assert r.returncode == 0, (r.stdout + r.stderr)
    assert "inspector_inject_selfcheck ok" in r.stdout, (r.stdout + r.stderr)
