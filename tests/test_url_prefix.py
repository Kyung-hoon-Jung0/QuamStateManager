"""docs/226 -- serving SM under a URL prefix behind a reverse proxy (implementer A).

Pins the server + CLI + config surface: prefix normalisation and the collision
rule, the tolerant PrefixMiddleware (+ opt-in ProxyFix), the byte-identical
root stack, CSRF behind a proxy (same strictness), the CSP frame-ancestors
opt-in, `_record_own_port` / `SM_BIND_PORT`, `chat_api._sm_url`, the
`SMLink` Origin, the journal's run links, the server-side literal sites, and
the `qsm serve` / desktop launcher flags.

Every app built here names its prefix EXPLICITLY (``url_prefix=""`` for root),
so the file means the same thing in both suite modes (``SM_TEST_URL_PREFIX``).
"""
from __future__ import annotations

import json
import logging
import re
import threading
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from flask import Flask, jsonify, render_template_string, request, url_for

from quam_state_manager.web import url_prefix as up
from quam_state_manager.web.app import _build_csp, create_app

ROOT = Path(__file__).resolve().parent.parent
WEB = ROOT / "quam_state_manager" / "web"

# The two CSP strings SM sent before docs/226, verbatim (NOT imported: a pin
# that reads the value it checks cannot fail).
OLD_CSP = ("default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' "
           "'unsafe-inline'; img-src 'self' data:; connect-src 'self'; object-src 'none'; "
           "base-uri 'self'; frame-src 'self' http://127.0.0.1:* http://localhost:*; "
           "frame-ancestors 'self'")
OLD_CSP_WORKBENCH = ("default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' "
                     "'unsafe-inline'; img-src 'self' data:; connect-src 'self' "
                     "http://127.0.0.1:* http://localhost:*; object-src 'none'; base-uri 'self'; "
                     "frame-src 'self' http://127.0.0.1:* http://localhost:*; "
                     "frame-ancestors 'self'")


def _app(tmp_path, *, testing=True, url_prefix="", behind_proxy=False, frame_ancestors="",
         name="inst"):
    return create_app(testing=testing, instance_path=str(tmp_path / name),
                      url_prefix=url_prefix, behind_proxy=behind_proxy,
                      frame_ancestors=frame_ancestors)


# ---------------------------------------------------------------- normalise

@pytest.mark.parametrize("raw,want", [
    (None, ""), ("", ""), ("/", ""), ("//", ""),
    ("/sm", "/sm"), ("/sm/", "/sm"), ("//a//b/", "/a/b"), ("/a/b", "/a/b"),
    ("/lab-1/sm_2.x~y", "/lab-1/sm_2.x~y"),
    ("/" + "a" * 199, "/" + "a" * 199),          # exactly 200 characters
])
def test_normalize_accepts(raw, want):
    assert up.normalize_prefix(raw) == want


@pytest.mark.parametrize("raw", [
    "sm", "sm/", "/a/../b", "/./x", "/..", "/a b", "/a%20b", "/a?b", "/a#b",
    "/\u00e9", "/a\nb", "/a\tb", "/" + "a" * 200,  # 201 characters
])
def test_normalize_rejects_with_one_line(raw):
    with pytest.raises(ValueError) as exc:
        up.normalize_prefix(raw)
    assert "\n" not in str(exc.value)


def test_collision_with_an_sm_route_is_refused(tmp_path):
    with pytest.raises(up.PrefixCollisionError, match=r"collides with SM route /qubits"):
        _app(tmp_path, url_prefix="/qubits")
    with pytest.raises(up.PrefixCollisionError):
        _app(tmp_path, url_prefix="/static/x", name="i2")   # the FIRST segment decides
    app = _app(tmp_path, url_prefix="/qubitsx", name="i3")  # a longer word is not the route
    assert app.config["SM_URL_PREFIX"] == "/qubitsx"


def test_route_first_segments_is_the_route_map(tmp_path):
    segs = up.route_first_segments(_app(tmp_path))
    assert {"qubits", "static", "api", "diff", "datasets", "journal"} <= segs
    assert "" not in segs and not any(s.startswith("<") for s in segs)


# ---------------------------------------------------------------- the middleware (mini app)

def _mini(prefix="", behind_proxy=False):
    app = Flask("mini")

    @app.route("/qubits")
    def qubits():
        return "q"

    @app.route("/", defaults={"p": ""})
    @app.route("/<path:p>")
    def probe(p):
        return jsonify(root=request.script_root, path=request.path, host=request.host,
                       scheme=request.scheme, built=url_for("qubits"))

    up.install(app, prefix, behind_proxy=behind_proxy)
    return app


def _get(app, path, **headers):
    return app.test_client().get(path, headers=headers).get_json()


def test_config_prefix_strips_a_non_stripped_path():
    d = _get(_mini("/sm"), "/sm/x/y")
    assert (d["root"], d["path"], d["built"]) == ("/sm", "/x/y", "/sm/qubits")


def test_config_prefix_accepts_a_stripped_path():
    d = _get(_mini("/sm"), "/x/y")                   # stripping proxy / loopback client
    assert (d["root"], d["path"]) == ("/sm", "/x/y")


def test_the_bare_prefix_is_the_index_and_the_boundary_is_a_segment():
    app = _mini("/sm")
    assert _get(app, "/sm")["path"] == "/"
    assert _get(app, "/sm/")["path"] == "/"
    assert _get(app, "/smx/y")["path"] == "/smx/y"     # not under /sm
    assert _get(app, "/sm/sm/x")["path"] == "/sm/x"    # stripped exactly ONCE


def test_header_prefix_behind_a_proxy():
    app = _mini("", behind_proxy=True)
    d = _get(app, "/sm/x", **{"X-Forwarded-Prefix": "/sm/", "X-Forwarded-Host": "lab.example",
                              "X-Forwarded-Proto": "https"})
    assert (d["root"], d["path"], d["host"], d["scheme"]) == ("/sm", "/x", "lab.example", "https")
    d = _get(app, "/x", **{"X-Forwarded-Prefix": "/sm"})     # a stripping proxy
    assert (d["root"], d["path"]) == ("/sm", "/x")
    d = _get(app, "/x")                                       # no header: root
    assert (d["root"], d["path"]) == ("", "/x")


def test_config_wins_over_a_differing_header_and_warns_once(caplog):
    app = _mini("/sm", behind_proxy=True)
    with caplog.at_level(logging.WARNING, logger="quam_state_manager.web.url_prefix"):
        for _ in range(3):
            d = _get(app, "/sm/x", **{"X-Forwarded-Prefix": "/other"})
            assert (d["root"], d["path"]) == ("/sm", "/x")
    warns = [r for r in caplog.records if "differs from --url-prefix" in r.getMessage()]
    assert len(warns) == 1


def test_an_invalid_header_prefix_is_ignored_with_one_warning(caplog):
    app = _mini("", behind_proxy=True)
    with caplog.at_level(logging.WARNING, logger="quam_state_manager.web.url_prefix"):
        for _ in range(2):
            d = _get(app, "/x", **{"X-Forwarded-Prefix": "/a%20b"})
            assert (d["root"], d["path"]) == ("", "/x")
    assert len([r for r in caplog.records if "invalid X-Forwarded-Prefix" in r.getMessage()]) == 1


def test_a_colliding_header_prefix_is_ignored():
    app = _mini("", behind_proxy=True)
    d = _get(app, "/qubits/x", **{"X-Forwarded-Prefix": "/qubits"})
    assert (d["root"], d["path"]) == ("", "/qubits/x")


def test_forwarded_headers_are_not_trusted_without_behind_proxy():
    d = _get(_mini("/sm"), "/sm/x", **{"X-Forwarded-Prefix": "/evil",
                                        "X-Forwarded-Host": "evil.example"})
    assert (d["root"], d["host"]) == ("/sm", "localhost")


def test_root_installs_nothing(tmp_path):
    app = _app(tmp_path)
    assert app.wsgi_app.__func__ is Flask.wsgi_app and app.wsgi_app.__self__ is app
    assert app.config["APPLICATION_ROOT"] == "/"
    assert (app.config["SM_URL_PREFIX"], app.config["SM_BEHIND_PROXY"]) == ("", False)
    mini = Flask("m")
    up.install(mini, "", behind_proxy=False)
    assert mini.wsgi_app.__func__ is Flask.wsgi_app


def test_the_stack_order_is_proxyfix_outside_the_prefix_middleware(tmp_path):
    from werkzeug.middleware.proxy_fix import ProxyFix
    app = _app(tmp_path, url_prefix="/sm", behind_proxy=True)
    assert isinstance(app.wsgi_app, ProxyFix)
    assert isinstance(app.wsgi_app.app, up.PrefixMiddleware)
    assert (app.wsgi_app.x_for, app.wsgi_app.x_proto, app.wsgi_app.x_host,
            app.wsgi_app.x_port, app.wsgi_app.x_prefix) == (1, 1, 1, 1, 1)
    only = _app(tmp_path, url_prefix="/sm", name="i2")
    assert isinstance(only.wsgi_app, up.PrefixMiddleware)


# ---------------------------------------------------------------- the real app under /sm

def test_real_app_answers_both_spellings_and_redirects_under_the_prefix(tmp_path):
    app = _app(tmp_path, url_prefix="/sm")
    c = app.test_client()
    assert c.get("/").status_code == 200 and c.get("/sm/").status_code == 200
    assert c.get("/sm").status_code == 200
    r = c.get("/sm/diff/versions")                     # a legacy entry -> _hub_redirect
    assert r.status_code == 302 and r.headers["Location"] == "/sm/diff"
    with app.test_request_context("/"):
        assert url_for("main.qubits") == "/sm/qubits"


def test_hub_and_pane_redirects_are_rooted(tmp_path):
    from quam_state_manager.web import routes
    for prefix in ("", "/sm"):
        app = _app(tmp_path, url_prefix=prefix, name=f"i{len(prefix)}")
        with app.test_request_context("/x", headers={"HX-Request": "true"}):
            assert routes._hub_redirect("/diff").headers["HX-Redirect"] == prefix + "/diff"
            loc = json.loads(routes._pane_redirect("/diff?a=1").headers["HX-Location"])
            assert loc["path"] == prefix + "/diff?a=1"
            assert routes._rooted("/trends/series?x=1") == prefix + "/trends/series?x=1"
        with app.test_request_context("/x"):
            assert routes._hub_redirect("/diff").headers["Location"] == prefix + "/diff"


def test_the_template_root_global(tmp_path):
    for prefix in ("", "/sm"):
        app = _app(tmp_path, url_prefix=prefix, name=f"i{len(prefix)}")
        with app.test_request_context("/"):
            assert render_template_string("[{{ root }}]") == f"[{prefix}]"
            assert render_template_string("{{ root|tojson }}") == json.dumps(prefix)
        with app.app_context():                  # a background pre-render
            assert render_template_string("[{{ root }}]") == f"[{prefix}]"


def test_create_app_reads_the_env_only_for_none(tmp_path, monkeypatch):
    monkeypatch.setenv("SM_URL_PREFIX", "/lab/")
    monkeypatch.setenv("SM_BEHIND_PROXY", "yes")
    monkeypatch.setenv("SM_FRAME_ANCESTORS", "https://lab.example")
    app = create_app(testing=True, instance_path=str(tmp_path / "a"))
    assert (app.config["SM_URL_PREFIX"], app.config["SM_BEHIND_PROXY"],
            app.config["SM_FRAME_ANCESTORS"]) == ("/lab", True, "https://lab.example")
    explicit = _app(tmp_path, name="b")                     # "" / False / "" given: root
    assert explicit.config["SM_URL_PREFIX"] == "" and explicit.wsgi_app.__func__ is Flask.wsgi_app
    monkeypatch.setenv("SM_BEHIND_PROXY", "maybe")
    with pytest.raises(ValueError, match="SM_BEHIND_PROXY"):
        create_app(testing=True, instance_path=str(tmp_path / "c"))
    # a string handed in verbatim is parsed, never truth-tested ("0" is off)
    off = create_app(testing=True, instance_path=str(tmp_path / "d"), url_prefix="",
                     behind_proxy="0", frame_ancestors="")
    assert off.config["SM_BEHIND_PROXY"] is False and off.wsgi_app.__func__ is Flask.wsgi_app


# ---------------------------------------------------------------- CSRF (the real check)

def _post(app, path, base="http://localhost", **headers):
    return app.test_client().post(path, base_url=base, headers=headers).status_code


def test_csrf_root_same_origin_passes_and_cross_origin_is_403(tmp_path):
    app = _app(tmp_path, testing=False)
    # POST on a GET-only route: 405 means the CSRF gate let it through
    assert _post(app, "/qubits", Origin="http://localhost") == 405
    assert _post(app, "/qubits", Origin="http://evil.example") == 403
    assert _post(app, "/qubits") == 403                       # neither header


def test_csrf_host_preserving_proxy(tmp_path):                # (a)
    app = _app(tmp_path, testing=False, url_prefix="/sm")
    assert _post(app, "/sm/qubits", base="http://lab.example", Origin="https://lab.example") == 405
    assert _post(app, "/sm/qubits", base="http://lab.example",
                 Referer="https://lab.example/sm/qubits") == 405
    assert _post(app, "/sm/qubits", base="http://lab.example", Origin="https://evil.example") == 403


def test_csrf_host_rewriting_proxy_with_x_forwarded_host(tmp_path):   # (b)
    app = _app(tmp_path, testing=False, url_prefix="/sm", behind_proxy=True)
    h = {"Origin": "https://lab.example", "X-Forwarded-Host": "lab.example",
         "X-Forwarded-Proto": "https"}
    assert _post(app, "/sm/qubits", base="http://127.0.0.1:5331", **h) == 405
    # traefik also sends the public port; werkzeug drops the scheme's default
    h["X-Forwarded-Port"] = "443"
    assert _post(app, "/sm/qubits", base="http://127.0.0.1:5331", **h) == 405


def test_csrf_host_rewriting_proxy_without_x_forwarded_host_is_403(tmp_path):   # (c)
    app = _app(tmp_path, testing=False, url_prefix="/sm", behind_proxy=True)
    assert _post(app, "/sm/qubits", base="http://127.0.0.1:5331",
                 Origin="https://lab.example") == 403


def test_csrf_x_forwarded_host_is_not_trusted_without_behind_proxy(tmp_path):   # (d)
    app = _app(tmp_path, testing=False, url_prefix="/sm")
    assert _post(app, "/sm/qubits", base="http://127.0.0.1:5331", Origin="https://lab.example",
                 **{"X-Forwarded-Host": "lab.example"}) == 403


# ---------------------------------------------------------------- CSP

def test_csp_default_is_byte_identical():
    assert _build_csp("") == (OLD_CSP, OLD_CSP_WORKBENCH)


def test_csp_header_at_root_is_the_old_string(tmp_path):
    c = _app(tmp_path).test_client()
    assert c.get("/").headers["Content-Security-Policy"] == OLD_CSP
    assert c.get("/workbench").headers["Content-Security-Policy"] == OLD_CSP_WORKBENCH


def test_csp_frame_ancestors_opt_in(tmp_path):
    page, wb = _build_csp("https://lab.example")
    assert page.endswith("frame-ancestors 'self' https://lab.example")
    assert "connect-src 'self' http://127.0.0.1:* http://localhost:*; " in wb
    assert _build_csp("'none'")[0].endswith("frame-ancestors 'none'")
    app = _app(tmp_path, url_prefix="/sm", frame_ancestors="https://lab.example lab2.example:8443")
    hdr = app.test_client().get("/sm/").headers["Content-Security-Policy"]
    assert hdr.endswith("frame-ancestors 'self' https://lab.example lab2.example:8443")
    assert "X-Frame-Options" not in app.test_client().get("/sm/").headers


@pytest.mark.parametrize("raw", [
    "https://a.example; script-src *", "https://a.example,https://b.example",
    '"https://a.example"', "'none' https://a.example", "javascript:alert(1)",
    "https://a.example\nX-Evil: 1", "https://a.example/path",
])
def test_csp_frame_ancestors_rejects(raw):
    with pytest.raises(ValueError):
        up.validate_frame_ancestors(raw)


def test_frame_ancestors_normalises():
    assert up.validate_frame_ancestors("  'self'  https://a.example  https://a.example ") == "https://a.example"
    assert up.validate_frame_ancestors("") == ""


# ---------------------------------------------------------------- port registry / agent link

def _registry_port(inst: Path):
    rows = [json.loads(p.read_text(encoding="utf-8")) for p in (inst / "instances").glob("*.json")]
    return [r.get("port") for r in rows]


def test_record_own_port_prefers_the_bound_port(tmp_path):
    app = _app(tmp_path, testing=False, url_prefix="/sm", behind_proxy=True)
    app.config["SM_BIND_PORT"] = 5331
    app.test_client().get("/sm/", base_url="http://127.0.0.1:5331",
                          headers={"X-Forwarded-Host": "lab.example:8443"})
    assert _registry_port(tmp_path / "inst") == [5331]


def test_record_own_port_falls_back_to_the_host(tmp_path):
    app = _app(tmp_path, testing=False)
    assert app.config["SM_BIND_PORT"] is None
    app.test_client().get("/", base_url="http://127.0.0.1:5332")
    assert _registry_port(tmp_path / "inst") == [5332]


def test_sm_url_three_modes(tmp_path):
    from quam_state_manager.web.chat_api import _sm_url
    root = _app(tmp_path, name="r")
    root.config["SM_BIND_PORT"] = 5331
    with root.test_request_context("/", base_url="http://127.0.0.1:5331"):
        assert _sm_url() == "http://127.0.0.1:5331"           # identical to before
    pre = _app(tmp_path, url_prefix="/sm", name="p")
    pre.config["SM_BIND_PORT"] = 5331
    with pre.test_request_context("/", base_url="http://lab.example/sm"):
        assert _sm_url() == "http://127.0.0.1:5331/sm"        # beside SM, never via the proxy
    prox = _app(tmp_path, behind_proxy=True, name="x")
    prox.config.update(SM_BIND_PORT=5331, SM_BIND_HOST="0.0.0.0")
    with prox.test_request_context("/", base_url="https://lab.example"):
        assert _sm_url() == "http://127.0.0.1:5331"
    unbound = _app(tmp_path, url_prefix="/sm", name="u")      # a WSGI host that never said
    with unbound.test_request_context("/", base_url="http://127.0.0.1:5332/sm"):
        assert _sm_url() == "http://127.0.0.1:5332/sm"


def test_smlink_origin_is_the_netloc():
    from quam_state_manager.core.agent_link import SMLink
    assert SMLink("http://127.0.0.1:5050/sm/")._headers()["Origin"] == "http://127.0.0.1:5050"
    assert SMLink("http://127.0.0.1:5050")._headers()["Origin"] == "http://127.0.0.1:5050"


def test_smlink_through_a_prefixed_live_server(tmp_path):
    """The real client, the real CSRF check, a real socket: a prefixed base
    reaches SM (tolerant strip) and its POST is not refused as cross-origin."""
    from werkzeug.serving import make_server
    from quam_state_manager.core.agent_link import SMLink
    app = _app(tmp_path, testing=False, url_prefix="/sm")
    srv = make_server("127.0.0.1", 0, app, threaded=True)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    try:
        base = f"http://127.0.0.1:{srv.server_port}"
        for b in (base + "/sm", base):
            code, _body = SMLink(b, timeout=10).post_form("/qubits", {})
            assert code == 405, (b, code)          # through CSRF; GET-only route
        code, _ = SMLink(base + "/sm", timeout=10).get("/api/agent/chip")
        assert code != 403
    finally:
        srv.shutdown()
        t.join(timeout=5)


# ---------------------------------------------------------------- journal links

_SAMPLE = ("# Day\n- **10:00:01** `by_claude` ran #2711 see [q](/qubits) and "
           "[e](https://ex.org/a#9)\n\n> quote #4\n\n[d](/sm/datasets?q=1)\n")
_SAMPLE_ROOT = "\n".join([
    "<h1>Day</h1>",
    "<ul>",
    '<li class="jr-entry" data-time="10:00:01" data-author="by_claude"><strong>10:00:01</strong> '
    '<code>by_claude</code> ran <a class="jr-run" href="/dataset/by-run/2711" '
    'hx-get="/dataset/by-run/2711" hx-target="#table-pane" hx-push-url="true">#2711</a> see '
    '<a href="/qubits" rel="noopener">q</a> and <a href="https://ex.org/a#9" rel="noopener">e</a></li>',
    "</ul>",
    '<blockquote>quote <a class="jr-run" href="/dataset/by-run/4" hx-get="/dataset/by-run/4" '
    'hx-target="#table-pane" hx-push-url="true">#4</a></blockquote>',
    '<p><a href="/sm/datasets?q=1" rel="noopener">d</a></p>',
])


def test_journal_render_is_identical_at_root():
    from quam_state_manager.core import journal
    assert journal.render(_SAMPLE) == _SAMPLE_ROOT
    assert journal.render(_SAMPLE, root="") == _SAMPLE_ROOT


def test_journal_render_roots_in_app_links_once():
    from quam_state_manager.core import journal
    out = journal.render(_SAMPLE, root="/sm")
    assert 'href="/sm/dataset/by-run/2711" hx-get="/sm/dataset/by-run/2711"' in out
    assert 'href="/sm/qubits"' in out and 'href="https://ex.org/a#9"' in out
    assert 'href="/sm/datasets?q=1"' in out and "/sm/sm/" not in out


def test_journal_raw_page_carries_the_prefix(tmp_path):
    from quam_state_manager.core import journal
    app = _app(tmp_path, url_prefix="/sm")
    journal.append(app.instance_path, "chipX", "looked", run_id=42)
    body = app.test_client().get("/sm/journal/raw?chip=chipX").get_data(as_text=True)
    assert 'href="/sm/dataset/by-run/42"' in body


# ---------------------------------------------------------------- server literal sites

_LITERAL_PATTERNS = [
    r"\bredirect\(\s*f?[\"']/",
    r"HX-(Redirect|Location)[\"']\]\s*=\s*f?[\"']/",
    r"_url\s*=\s*\(?\s*f?[\"']/[a-z]",
    r"\burl\s*=\s*\(?\s*f?[\"']/[a-z]",
    r"[\"'](url|href|redirect|location|next|path)[\"']\s*:\s*f?[\"']/[a-z]",
    r"(href|hx-get|hx-post|src|action)=\\?\"/[a-z]",
]


def test_no_root_absolute_url_literal_is_emitted_by_the_server():
    hits = []
    for p in sorted(WEB.glob("*.py")) + [ROOT / "quam_state_manager" / "core" / "journal.py"]:
        for i, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1):
            if line.lstrip().startswith("#"):
                continue
            for pat in _LITERAL_PATTERNS:
                if re.search(pat, line):
                    hits.append(f"{p.name}:{i}: {line.strip()}")
    assert hits == [], "\n".join(hits)


def test_the_literal_lint_catches_what_it_claims():
    samples = ['return redirect("/diff")', 'resp.headers["HX-Redirect"] = "/qubits"',
               'action_url=(f"/state-history/x"', 'hub_url="/compare-hub"',
               'url="/trends/param-diff?"', '{"url": "/x"}',
               "f'<a href=\"/dataset/by-run/1\"'"]
    for s in samples:
        assert any(re.search(p, s) for p in _LITERAL_PATTERNS), s
    for ok in ['redirect(url_for("main.home"))', 'action_url=_rooted(f"/state-history/x")',
               'url_for("static", filename=f)']:
        assert not any(re.search(p, ok) for p in _LITERAL_PATTERNS), ok


# ---------------------------------------------------------------- CLI + desktop launcher

def _cli(args, tmp_path, env=None):
    from typer.testing import CliRunner
    import quam_state_manager.cli as cli
    import quam_state_manager.web.app as webapp
    seen: dict = {}
    real = webapp.create_app

    def fake_create_app(**kw):
        seen["kw"] = kw
        return real(testing=True, instance_path=str(tmp_path / "cli"), **kw)

    env = {"SM_URL_PREFIX": None, "SM_BEHIND_PROXY": None, "SM_FRAME_ANCESTORS": None, **(env or {})}
    with patch.object(webapp, "create_app", fake_create_app), \
            patch.object(cli, "_run_app", lambda app, **kw: seen.update(app=app, run=kw)):
        res = CliRunner().invoke(cli.app, args, env=env)
    return res, seen


def test_serve_bad_prefix_exits_2_with_one_reason(tmp_path):
    res, seen = _cli(["serve", "--url-prefix", "sm"], tmp_path)
    assert res.exit_code == 2 and "must start with '/'" in res.output
    assert "Traceback" not in res.output and "kw" not in seen
    res, _ = _cli(["serve", "--url-prefix", "/qubits"], tmp_path)
    flat = " ".join(res.output.replace("│", " ").split())      # Rich wraps its panel
    assert res.exit_code == 2 and "--url-prefix: /qubits collides with SM route /qubits" in flat
    assert "open  http" not in res.output, "announced a server that never started"
    res, _ = _cli(["serve", "--frame-ancestors", "a.example; x"], tmp_path)
    assert res.exit_code == 2 and "--frame-ancestors" in res.output


def test_serve_flags_env_and_banner(tmp_path):
    res, seen = _cli(["serve", "--port", "5331"], tmp_path)
    assert res.exit_code == 0, res.output
    assert "open  http://127.0.0.1:5331   (Ctrl+C" in res.output          # root banner unchanged
    assert seen["kw"] == {"url_prefix": "", "behind_proxy": False, "frame_ancestors": ""}
    res, seen = _cli(["serve", "--port", "5331"], tmp_path,
                     env={"SM_URL_PREFIX": "/lab/", "SM_BEHIND_PROXY": "1"})
    assert "open  http://127.0.0.1:5331/lab   (Ctrl+C" in res.output
    assert seen["kw"]["url_prefix"] == "/lab" and seen["kw"]["behind_proxy"] is True
    res, seen = _cli(["serve", "--url-prefix", "/x"], tmp_path, env={"SM_URL_PREFIX": "/y"})
    assert seen["kw"]["url_prefix"] == "/x"                                # CLI > env
    res, seen = _cli(["browser", "--no-open", "--url-prefix", "/sm", "--port", "5332"], tmp_path)
    assert "opening  http://127.0.0.1:5332/sm" in res.output
    assert isinstance(seen["app"].wsgi_app, up.PrefixMiddleware)


def test_run_app_stamps_the_bound_port():
    from quam_state_manager.cli import _run_app
    fake = SimpleNamespace(config={}, run=MagicMock())
    _run_app(fake, host="0.0.0.0", port=5331, debug=True)
    assert fake.config == {"SM_BIND_PORT": 5331, "SM_BIND_HOST": "0.0.0.0"}


def test_waitress_keeps_forwarded_headers_only_behind_a_proxy():
    """waitress 3.x scrubs X-Forwarded-Host/Proto/Port by default; behind a
    proxy they must reach ProxyFix, and at root nothing about the call changes."""
    import waitress
    from quam_state_manager.cli import _SERVE_THREADS, _run_app
    calls = []
    with patch.object(waitress, "serve", lambda app, **kw: calls.append(kw)):
        _run_app(SimpleNamespace(config={}), host="127.0.0.1", port=5331, debug=False)
        _run_app(SimpleNamespace(config={"SM_BEHIND_PROXY": True}), host="0.0.0.0",
                 port=5332, debug=False)
    assert calls[0] == {"host": "127.0.0.1", "port": 5331, "threads": _SERVE_THREADS}
    assert calls[1] == {"host": "0.0.0.0", "port": 5332, "threads": _SERVE_THREADS,
                        "clear_untrusted_proxy_headers": False}


def test_waitress_live_forwarded_host_passes_csrf(tmp_path):
    """The real launcher (`_run_app`), the real waitress, the real stack: a
    host-rewriting proxy's POST (X-Forwarded-Host + the public Origin) is not a
    CSRF 403. Only the port is swapped for an OS-chosen one."""
    import http.client
    import waitress
    from waitress.server import create_server
    from quam_state_manager.cli import _run_app
    app = _app(tmp_path, testing=False, url_prefix="/sm", behind_proxy=True)
    holder, ready = {}, threading.Event()

    def serve(wsgi, **kw):
        holder["srv"] = srv = create_server(wsgi, **{**kw, "port": 0})
        ready.set()
        srv.run()

    with patch.object(waitress, "serve", serve):
        t = threading.Thread(target=_run_app, args=(app,),
                             kwargs=dict(host="127.0.0.1", port=5331, debug=False), daemon=True)
        t.start()
        assert ready.wait(20)
    srv = holder["srv"]
    try:
        c = http.client.HTTPConnection("127.0.0.1", srv.effective_port, timeout=10)
        c.request("POST", "/sm/qubits", headers={
            "X-Forwarded-Host": "lab.example", "X-Forwarded-Proto": "https",
            "Origin": "https://lab.example", "Content-Length": "0"})
        assert c.getresponse().status == 405
    finally:
        srv.close()


def test_desktop_window_opens_under_the_prefix(tmp_path):
    from quam_state_manager import main as m
    fake_app = SimpleNamespace(config={"SM_URL_PREFIX": "/sm"}, instance_path=str(tmp_path))
    with patch.object(m, "create_app", return_value=fake_app), \
            patch.object(m, "find_free_port", return_value=9999), \
            patch.object(m, "_start_server"), \
            patch.object(m, "_wait_for_server", return_value=True), \
            patch.object(m, "webview") as wv, patch.object(m, "_shutdown"), \
            patch.object(m.atexit, "register"):
        m.main()
    assert wv.create_window.call_args.kwargs["url"] == "http://127.0.0.1:9999/sm"


def test_desktop_start_server_stamps_the_bound_port():
    from quam_state_manager.main import _start_server
    fake = SimpleNamespace(config={}, run=MagicMock())
    _start_server(fake, 5331).join(timeout=5)
    assert fake.config["SM_BIND_PORT"] == 5331


def test_a_direct_jinja_render_of_a_root_reading_template_passes_root():
    """docs/226: `jinja_env.get_template(name).render(...)` runs NO Flask context
    processor, so a template that reads `root` prints '' unless the call passes
    `root=` itself. Found by the /sm suite: the virtual Pulses rows (RowMemo,
    `/pulses/vrows`) carried un-rooted links while `/pulse/row` carried rooted
    ones. Every such render site must name `root=` within its statement."""
    import re
    from pathlib import Path
    web = Path(__file__).resolve().parent.parent / "quam_state_manager" / "web"
    tdir = web / "templates"
    bad = []
    for py in sorted(web.glob("*.py")):
        text = py.read_text(encoding="utf-8")
        for m in re.finditer(r'get_template\("([^"]+)"\)((?:.|\n){0,400}?)(?=\n\S|\Z)', text):
            name, tail = m.group(1), m.group(2)
            tpl = tdir / name
            if not tpl.exists():
                continue
            reads_root = re.search(r"\{\{\s*root\b|\broot\s*~", tpl.read_text(encoding="utf-8")) is not None
            if not reads_root:
                continue
            # the render may be on this statement or on a later `tmpl.render(` in the tail
            renders = re.findall(r"\.render\((?:[^()]|\([^()]*\))*\)", tail)
            for call in renders:
                if "root=" not in call:
                    bad.append(f"{py.name}: get_template({name!r}) ... {call[:60]}")
    assert not bad, "direct Jinja renders of a root-reading template without root= (docs/226):\n" + "\n".join(bad)
