"""QUAlibrate's web UI through SM, with an Open-in-QSM link per run.

A stub upstream plays the QUAlibrate endpoints the feature relies on (the
installed qualibrate 1.5.1 was measured for them; see qualibrate_proxy.py).
"""
from __future__ import annotations

import json
import shutil
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from quam_state_manager.core import activity
from quam_state_manager.web import app as app_mod
from quam_state_manager.web import qualibrate_proxy as qp
from quam_state_manager.web.app import create_app

_ROOT = Path(__file__).resolve().parent.parent
_INDEX = (b'<!doctype html><html lang="en"><head><meta charset="utf-8"/>'
          b'<script defer src="./main.js"></script></head><body><div id="root"></div></body></html>')


class Upstream:
    """Records what reached it; answers like QUAlibrate."""

    def __init__(self):
        self.seen = []
        self.routes = {}
        up = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _any(self):
                n = int(self.headers.get("Content-Length") or 0)
                body = self.rfile.read(n) if n else b""
                up.seen.append({"method": self.command, "path": self.path,
                                "headers": dict(self.headers), "body": body})
                status, headers, data = up.routes.get(self.path.split("?")[0], (404, {}, b"nope"))
                if callable(data):
                    data = data()
                self.send_response(status)
                for k, v in headers.items():
                    self.send_header(k, v)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                if self.command != "HEAD":
                    self.wfile.write(data)

            do_GET = do_POST = do_PUT = do_DELETE = do_HEAD = do_PATCH = _any

        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.port = self.srv.server_address[1]
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()

    def json(self, path, obj, status=200):
        self.routes[path] = (status, {"Content-Type": "application/json"}, json.dumps(obj).encode())

    def close(self):
        self.srv.shutdown()
        self.srv.server_close()


@pytest.fixture
def up(monkeypatch):
    u = Upstream()
    monkeypatch.setenv(qp.TARGET_ENV, f"http://127.0.0.1:{u.port}")
    monkeypatch.delenv(qp.REMOTE_ENV, raising=False)
    u.routes["/"] = (200, {"Content-Type": "text/html; charset=utf-8", "ETag": '"abc"',
                           "Set-Cookie": "qb=1", "Content-Security-Policy": "default-src *"}, _INDEX)
    yield u
    u.close()


@pytest.fixture
def app(tmp_path):
    return create_app(testing=True, instance_path=str(tmp_path / "inst"))


@pytest.fixture
def client(app):
    return app.test_client()


# ------------------------------------------------------------------ target
class TestTarget:
    def test_default_is_local_8001(self, monkeypatch):
        monkeypatch.delenv(qp.TARGET_ENV, raising=False)
        assert qp.target() == ("127.0.0.1", 8001)

    @pytest.mark.parametrize("url", [
        "http://example.com:8001", "http://127.0.0.1.example.com", "http://10.0.0.5:8001",
        "http://127.0.0.1@example.com", "http://user:pw@127.0.0.1:8001",
        "https://127.0.0.1:8001", "file:///etc/passwd", "http://:8001",
    ])
    def test_only_a_plain_local_address(self, monkeypatch, url):
        monkeypatch.setenv(qp.TARGET_ENV, url)
        monkeypatch.delenv(qp.REMOTE_ENV, raising=False)
        with pytest.raises(qp.TargetError):
            qp.target()

    def test_remote_only_when_the_operator_says_so(self, monkeypatch):
        monkeypatch.setenv(qp.TARGET_ENV, "http://10.0.0.5:8001")
        monkeypatch.setenv(qp.REMOTE_ENV, "1")
        assert qp.target() == ("10.0.0.5", 8001)

    def test_a_request_cannot_choose_the_target(self, client, up):
        client.get("/qualibrate-ui/?qb=http://example.com&url=http://example.com")
        assert up.seen and all(s["headers"]["Host"] == f"127.0.0.1:{up.port}" for s in up.seen)

    def test_refused_target_answers_400_and_sends_nothing(self, client, monkeypatch):
        monkeypatch.setenv(qp.TARGET_ENV, "http://example.com")
        monkeypatch.delenv(qp.REMOTE_ENV, raising=False)
        r = client.get("/qualibrate-ui/")
        assert r.status_code == 400 and b"not this computer" in r.data


# ------------------------------------------------------------------ the proxy
class TestProxy:
    def test_root_without_slash_redirects(self, client):
        r = client.get("/qualibrate-ui")
        assert r.status_code == 308 and r.headers["Location"].endswith("/qualibrate-ui/")

    def test_root_gets_one_tag_right_after_head(self, client, up):
        r = client.get("/qualibrate-ui/")
        html = r.data.decode()
        assert r.status_code == 200
        head = html.index("<head>") + len("<head>")
        assert html[head:].startswith('<script src="/static/qb-proxy.js" '
                                      f'data-ws="ws://127.0.0.1:{up.port}/" data-token="{qp._TOKEN}" '
                                      'data-prefix="/qualibrate-ui/">')
        assert html.count("qb-proxy.js") == 1
        assert "ETag" not in r.headers
        assert not any("qb=1" in c for c in r.headers.getlist("Set-Cookie"))

    def test_page_csp_is_sandboxed_with_the_ws_origin(self, client, up):
        csp = client.get("/qualibrate-ui/").headers["Content-Security-Policy"]
        assert "sandbox allow-scripts" in csp and "allow-same-origin" not in csp
        assert f"connect-src 'self' ws://127.0.0.1:{up.port};" in csp
        assert "frame-ancestors 'self'" in csp and "default-src *" not in csp

    def test_no_head_passes_through_unchanged(self, client, up):
        up.routes["/"] = (200, {"Content-Type": "text/html"}, b"<html><body>x</body></html>")
        assert client.get("/qualibrate-ui/").data == b"<html><body>x</body></html>"

    def test_only_the_root_is_rewritten(self, client, up):
        up.routes["/other.html"] = (200, {"Content-Type": "text/html"}, _INDEX)
        assert client.get("/qualibrate-ui/other.html").data == _INDEX

    def test_encoded_body_is_not_rewritten(self, client, up):
        up.routes["/"] = (200, {"Content-Type": "text/html", "Content-Encoding": "gzip"}, _INDEX)
        r = client.get("/qualibrate-ui/")
        assert r.data == _INDEX and r.headers["Content-Encoding"] == "gzip"

    def test_sm_cookies_and_origin_never_leave(self, client, up):
        client.set_cookie("sm_person_1", "secret")
        client.get("/qualibrate-ui/api/x", headers={
            "Origin": "null", qp.TOKEN_HEADER: qp._TOKEN,
            "Referer": "http://127.0.0.1/x", "X-Forwarded-For": "1.2.3.4",
            "Authorization": "Basic abc", "Accept": "application/json"})
        h = up.seen[-1]["headers"]
        assert "Cookie" not in h and "Origin" not in h and "Referer" not in h
        assert qp.TOKEN_HEADER not in h
        assert "X-Forwarded-For" not in h
        assert h["Authorization"] == "Basic abc" and h["Accept"] == "application/json"
        assert h["Accept-Encoding"] == "identity"

    def test_upstream_cookies_and_policies_are_dropped(self, client, up):
        up.routes["/api/x"] = (200, {"Content-Type": "application/json", "Set-Cookie": "qb=1",
                                     "Access-Control-Allow-Origin": "*", "X-Other": "1"}, b"{}")
        r = client.get("/qualibrate-ui/api/x")
        assert "qb=1" not in str(r.headers) and "X-Other" not in r.headers
        assert r.headers.get("Access-Control-Allow-Origin") is None

    def test_post_body_and_path_reach_upstream(self, client, up):
        up.json("/execution/submit/node", True)
        r = client.post("/qualibrate-ui/execution/submit/node?name=a%20b", data=b'{"x":1}',
                        headers={"Content-Type": "application/json"})
        assert r.status_code == 200
        s = up.seen[-1]
        assert s["method"] == "POST" and s["path"] == "/execution/submit/node?name=a%20b"
        assert s["body"] == b'{"x":1}'

    def test_crlf_never_reaches_the_request_line(self, client, up):
        # the router refuses a newline in the path before the proxy runs;
        # the quoting in _upstream_path is the second line of defence
        r = client.get("/qualibrate-ui/a%0d%0aX-Evil:%201")
        assert r.status_code == 404 and not up.seen
        client.get("/qualibrate-ui/a b%3Cx")
        assert up.seen[-1]["path"] == "/a%20b%3Cx"
        assert client.get("/qualibrate-ui/x?a=%0d").status_code in (200, 404)
        assert all("\r" not in s["path"] and "\n" not in s["path"] for s in up.seen)

    def test_location_points_back_into_the_prefix(self, client, up):
        up.routes["/go"] = (307, {"Location": f"http://127.0.0.1:{up.port}/api/x?a=1"}, b"")
        up.routes["/away"] = (302, {"Location": "http://example.com/x"}, b"")
        assert client.get("/qualibrate-ui/go").headers["Location"].endswith("/qualibrate-ui/api/x?a=1")
        assert "Location" not in client.get("/qualibrate-ui/away").headers

    def test_oversize_body_is_refused(self, client, up, monkeypatch):
        monkeypatch.setattr(qp, "MAX_BODY", 10)
        r = client.post("/qualibrate-ui/x", data=b"x" * 11)
        assert r.status_code == 413 and not up.seen

    def test_declared_oversize_is_refused_before_reading(self, client, up, monkeypatch):
        monkeypatch.setattr(qp, "MAX_BODY", 10)
        read = []
        from flask import Request
        monkeypatch.setattr(Request, "get_data", lambda self, *a, **k: read.append(1) or b"")
        r = client.post("/qualibrate-ui/x", data=b"x" * 11)
        assert r.status_code == 413 and not read

    def test_every_answer_is_sandboxed(self, client, up):
        up.routes["/assets/a.svg"] = (200, {"Content-Type": "image/svg+xml"}, b"<svg/>")
        up.json("/api/x", {})
        for u in ("/qualibrate-ui/assets/a.svg", "/qualibrate-ui/api/x"):
            assert "sandbox allow-scripts" in client.get(u).headers["Content-Security-Policy"]

    def test_the_ws_attribute_is_escaped(self, monkeypatch):
        monkeypatch.setattr(qp, "_netloc", lambda h, p: 'h"x:1')
        out = qp._inject(b"<html><head></head></html>", "h", 1).decode()
        assert 'data-ws="ws://h&quot;x:1/"' in out

    def test_upstream_down_says_where(self, client, monkeypatch):
        monkeypatch.setenv(qp.TARGET_ENV, "http://127.0.0.1:1")
        r = client.get("/qualibrate-ui/")
        assert r.status_code == 502 and b"127.0.0.1:1" in r.data

    def test_slots_come_back(self, client, up):
        up.json("/api/x", {})
        for _ in range(qp.MAX_INFLIGHT * 3):
            client.get("/qualibrate-ui/api/x").close()
            client.head("/qualibrate-ui/api/x").close()
        assert qp._SLOTS._value == qp.MAX_INFLIGHT

    def test_cors_only_for_the_sandboxed_page_and_only_here(self, client, up):
        up.json("/api/x", {})
        assert client.get("/qualibrate-ui/api/x", headers={"Origin": "null", qp.TOKEN_HEADER: qp._TOKEN}).headers[
            "Access-Control-Allow-Origin"] == "null"
        r = client.options("/qualibrate-ui/api/x", headers={
            "Origin": "null", "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "content-type, access-control-allow-origin, bad name"})
        assert r.status_code == 204 and r.headers["Access-Control-Allow-Origin"] == "null"
        assert r.headers["Access-Control-Allow-Headers"] == "content-type, access-control-allow-origin"
        assert "Access-Control-Allow-Origin" not in client.get("/workbench", headers={"Origin": "null"}).headers


class TestOnlyTheProxiedPage:
    """D-19: Origin "null" is what ANY sandboxed or data: page on the web sends.
    Only the page SM served (which holds the token) may use the proxy."""

    def test_a_foreign_null_origin_post_is_refused_and_never_sent(self, client, up):
        up.json("/execution/submit/node", True)
        for h in ({"Origin": "null"}, {"Origin": "null", qp.TOKEN_HEADER: "guess"},
                  {"Origin": "null", qp.TOKEN_HEADER: ""}):
            r = client.post("/qualibrate-ui/execution/submit/node", data=b"{}",
                            headers={**h, "Content-Type": "application/json"})
            assert r.status_code == 403
        assert not up.seen

    def test_the_proxied_page_post_still_works(self, client, up):
        up.json("/execution/submit/node", True)
        r = client.post("/qualibrate-ui/execution/submit/node", data=b"{}", headers={
            "Origin": "null", qp.TOKEN_HEADER: qp._TOKEN, "Content-Type": "application/json"})
        assert r.status_code == 200 and up.seen

    @pytest.mark.parametrize("path", ["/qualibrate-ui/api/x", "/qualibrate-ui/execution/get_nodes",
                                      "/qualibrate-ui/api/x.js", "/qualibrate-ui/execution/a.png"])
    def test_a_foreign_page_cannot_read_qualibrate_data(self, client, up, path):
        up.json(path.replace("/qualibrate-ui", ""), {"secret": 1})
        r = client.get(path, headers={"Origin": "null"})
        assert r.status_code == 403 and "Access-Control-Allow-Origin" not in r.headers

    def test_static_assets_load_without_the_token(self, client, up):
        up.routes["/assets/f.woff2"] = (200, {"Content-Type": "font/woff2"}, b"F")
        r = client.get("/qualibrate-ui/assets/f.woff2", headers={"Origin": "null"})
        assert r.status_code == 200 and r.headers["Access-Control-Allow-Origin"] == "null"
        assert client.post("/qualibrate-ui/assets/f.woff2", headers={"Origin": "null"}).status_code == 403

    def test_the_page_carrying_the_token_is_never_readable_cross_origin(self, client, up):
        for h in ({}, {"Origin": "null"}, {"Origin": "null", qp.TOKEN_HEADER: qp._TOKEN}):
            r = client.get("/qualibrate-ui/", headers=h)
            if r.status_code == 200:
                assert "Access-Control-Allow-Origin" not in r.headers
        assert f'data-token="{qp._TOKEN}"' in client.get("/qualibrate-ui/").data.decode()

    def test_the_preflight_names_nothing_but_header_names(self, client):
        r = client.options("/qualibrate-ui/execution/submit/node", headers={
            "Origin": "null", "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "x-qsm-proxy-token, content-type"})
        assert r.status_code == 204 and qp._TOKEN not in str(r.headers)

    def test_the_token_opens_nothing_outside_the_proxy(self, app):
        for p in ("/field/edit", "/state/apply-to-live", "/qualibrate-uiX/a.js"):
            with app.test_request_context(p, method="POST", headers={qp.TOKEN_HEADER: qp._TOKEN}):
                from flask import request
                assert not qp.null_origin_allowed(request)

    def test_run_link_is_a_read_only_redirect(self, client):
        for m in ("post", "put", "delete"):
            assert getattr(client, m)("/qualibrate-run/1").status_code == 405


class TestCsrf:
    """The sandboxed page (opaque origin "null") may write to the proxy only."""

    def _check(self, app, path, origin):
        app.config["TESTING"] = False
        with app.test_request_context(path, method="POST", headers={"Origin": origin, "Host": "127.0.0.1:5000"}):
            return app_mod._csrf_origin_check()

    def _check_tok(self, app, path, token):
        app.config["TESTING"] = False
        with app.test_request_context(path, method="POST", headers={
                "Origin": "null", "Host": "127.0.0.1:5000", qp.TOKEN_HEADER: token}):
            return app_mod._csrf_origin_check()

    def test_null_origin_may_post_to_the_proxy_with_the_token(self, app):
        assert self._check_tok(app, "/qualibrate-ui/execution/submit/node", qp._TOKEN) is None

    def test_null_origin_without_the_token_is_refused_even_on_the_proxy(self, app):
        assert self._check(app, "/qualibrate-ui/execution/submit/node", "null") is not None
        assert self._check_tok(app, "/qualibrate-ui/execution/submit/node", "nope") is not None

    @pytest.mark.parametrize("path", ["/field/edit", "/state/apply-to-live", "/qualibrate-uiX/x",
                                      "/qualibrate-run/1", "/workbench"])
    def test_null_origin_may_not_post_anywhere_else(self, app, path):
        assert self._check(app, path, "null") is not None

    def test_foreign_origin_may_not_post_to_the_proxy(self, app):
        assert self._check(app, "/qualibrate-ui/x", "http://evil.example") is not None


def test_proxy_polls_do_not_count_as_a_user_waiting():
    assert not activity.is_foreground("/qualibrate-ui/api/x")
    assert activity.is_foreground("/qualibrate-run/3")


# ------------------------------------------------------------------ run link
@pytest.fixture
def storage(tmp_path):
    loc = tmp_path / "store"
    run = loc / "2026-11-04" / "#25_ramsey_181449"
    run.mkdir(parents=True)
    (run / "node.json").write_text("{}")
    return loc, run


def _qb(up, loc, rel="2026-11-04\\#25_ramsey_181449", active="p1", projects=None):
    up.json("/api/data_file/25/", rel)
    up.json("/api/project/active", active)
    up.json("/api/projects/", projects if projects is not None else [
        {"name": "other", "config": {"qualibrate": {"storage": {"location": "/nowhere"}}}},
        {"name": "p1", "config": {"qualibrate": {"storage": {"location": str(loc)}}}}])


class TestOpenRun:
    def test_active_projects_folder_opens_through_by_path(self, client, up, storage):
        loc, run = storage
        _qb(up, loc)
        r = client.get("/qualibrate-run/25")
        assert r.status_code == 302
        from urllib.parse import parse_qs, urlsplit
        u = urlsplit(r.headers["Location"])
        assert u.path == "/dataset/by-path" and Path(parse_qs(u.query)["path"][0]) == run.resolve()

    @pytest.mark.parametrize("rel", ["..\\..\\etc", "../2026-11-04/#25_ramsey_181449", "C:\\x\\#25_a_1",
                                     "/abs/#25_a_1", "\\\\host\\share\\#25_a_1", "", 5])
    def test_unusable_folder_is_refused(self, client, up, storage, rel):
        loc, _ = storage
        _qb(up, loc, rel=rel)
        r = client.get("/qualibrate-run/25")
        assert r.status_code == 404

    def test_folder_must_carry_the_run_number(self, client, up, storage):
        loc, run = storage
        _qb(up, loc)
        up.json("/api/data_file/26/", "2026-11-04\\#25_ramsey_181449")
        assert client.get("/qualibrate-run/26").status_code == 404

    def test_folder_must_exist(self, client, up, storage):
        loc, run = storage
        shutil.rmtree(run)
        _qb(up, loc)
        r = client.get("/qualibrate-run/25")
        assert r.status_code == 404 and b"not found" in r.data

    def test_escaping_link_inside_the_location_is_refused(self, client, up, storage, tmp_path):
        loc, run = storage
        outside = tmp_path / "out" / "#25_x_1"
        outside.mkdir(parents=True)
        _qb(up, loc, rel="2026-11-04/lnk")
        try:
            subprocess.run(["cmd", "/c", "mklink", "/J", str(loc / "2026-11-04" / "lnk"), str(outside)],
                           check=True, capture_output=True)
        except (OSError, subprocess.CalledProcessError):
            try:
                (loc / "2026-11-04" / "lnk").symlink_to(outside, target_is_directory=True)
            except OSError:
                pytest.skip("no junction or symlink on this machine")
        assert client.get("/qualibrate-run/25").status_code == 404

    def test_without_a_location_a_single_loaded_folder_is_used(self, client, up, storage, monkeypatch):
        loc, run = storage
        _qb(up, loc, projects=[])
        import quam_state_manager.web.routes as routes
        monkeypatch.setattr(routes, "_active_dataset_stores", lambda **k: [{"path": str(loc)}])
        r = client.get("/qualibrate-run/25")
        assert r.status_code == 302 and "by-path" in r.headers["Location"]

    def test_two_loaded_folders_with_the_run_ask_instead_of_guessing(self, client, up, storage, monkeypatch, tmp_path):
        loc, run = storage
        loc2 = tmp_path / "store2"
        shutil.copytree(loc, loc2)
        _qb(up, loc, projects=[])
        import quam_state_manager.web.routes as routes
        monkeypatch.setattr(routes, "_active_dataset_stores",
                            lambda **k: [{"path": str(loc)}, {"path": str(loc2)}])
        r = client.get("/qualibrate-run/25")
        assert r.status_code == 409 and b"more than one" in r.data

    def test_no_active_project_and_several_projects_is_not_a_guess(self, client, up, storage, monkeypatch):
        loc, run = storage
        # two projects, none active; the first would even hold the run
        _qb(up, loc, active=None, projects=[
            {"name": "p1", "config": {"qualibrate": {"storage": {"location": str(loc)}}}},
            {"name": "p2", "config": {"qualibrate": {"storage": {"location": "/nowhere"}}}}])
        import quam_state_manager.web.routes as routes
        monkeypatch.setattr(routes, "_active_dataset_stores", lambda **k: [])
        assert client.get("/qualibrate-run/25").status_code == 404
        _qb(up, loc, active=None, projects=[
            {"name": "p1", "config": {"qualibrate": {"storage": {"location": str(loc)}}}}])
        assert client.get("/qualibrate-run/25").status_code == 302

    @pytest.mark.parametrize("rel", ["..", "a/../b", r"a\..\b", "C:x", r"C:\x", "/x", r"\\h\s"])
    def test_relative_parts_refuses_climbing_or_anchored(self, rel):
        assert qp._relative_parts(rel) is None

    def test_relative_parts_splits_both_separators(self):
        assert qp._relative_parts(r"2026-11-04\#25_a_1") == ["2026-11-04", "#25_a_1"]
        assert qp._relative_parts("./2026-11-04/#25_a_1") == ["2026-11-04", "#25_a_1"]

    def test_unknown_run_and_down_server(self, client, up, storage, monkeypatch):
        loc, _ = storage
        _qb(up, loc)
        assert client.get("/qualibrate-run/99").status_code == 404
        monkeypatch.setenv(qp.TARGET_ENV, "http://127.0.0.1:1")
        assert client.get("/qualibrate-run/25").status_code == 502


# ------------------------------------------------------------------ pages
def test_workbench_offers_the_proxy_and_trusts_only_its_own_frame(client):
    html = client.get("/workbench").data.decode()
    assert 'id="wb-viasm"' in html and 'setUrl("/qualibrate-ui/")' in html
    assert "e.source !== qb.contentWindow" in html
    assert "/^\\/qualibrate-run\\/\\d{1,9}$/" in html


def test_the_script_selfcheck(tmp_path):
    node = shutil.which("node")
    if not node:
        pytest.skip("node not installed")
    r = subprocess.run([node, str(_ROOT / "tests" / "qb_proxy_selfcheck.cjs")],
                       capture_output=True, text=True, timeout=120)
    if "jsdom not installed" in r.stdout:
        pytest.skip("jsdom not installed")
    assert r.returncode == 0, r.stdout + r.stderr
    assert "assertions" in r.stdout
