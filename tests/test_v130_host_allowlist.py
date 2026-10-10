"""v1.3.0 B1 P1: the server answers only to hosts it was meant to answer to.

A DNS-rebound page reaches 127.0.0.1 under ITS OWN domain; its Origin then
names that same domain, so the origin check passes. The Host header is what
it cannot fake -- these pins hold that check in front of every route."""
import pytest

from quam_state_manager.web import app as app_mod
from quam_state_manager.web.app import (ALLOWED_HOSTS_ENV, allow_bind_host, create_app,
                                        host_name, is_loopback_host)


@pytest.fixture
def app(tmp_path):
    return create_app(testing=True, instance_path=str(tmp_path / "inst"))


def _get(app, host, path="/help"):
    return app.test_client().get(path, headers={"Host": host})


class TestHostAllowlist:
    @pytest.mark.parametrize("host", ["evil.example", "evil.example:5050", "10.1.2.3:5050",
                                      "localhost.evil.example", "127.0.0.1.evil.example"])
    def test_a_foreign_host_is_refused_before_any_route(self, app, host):
        r = _get(app, host)
        assert r.status_code == 403
        assert b"is not served here" in r.data
        assert r.headers["Content-Type"].startswith("text/plain")

    @pytest.mark.parametrize("host", ["localhost", "localhost:5050", "127.0.0.1:5050",
                                      "127.0.0.2:5050", "[::1]:5050", "LOCALHOST:5050"])
    def test_loopback_names_are_served(self, app, host):
        r = _get(app, host)
        assert r.status_code != 403, (host, r.data[:200])

    def test_a_rebound_post_passes_the_origin_check_but_not_the_host_check(self, tmp_path):
        a = create_app(testing=False, instance_path=str(tmp_path / "inst"))
        host = "rebind.example:5050"
        r = a.test_client().post("/load", data={"folder": str(tmp_path)},
                                 headers={"Host": host, "Origin": "http://" + host})
        assert r.status_code == 403
        assert b"is not served here" in r.data           # the host check, not CSRF
        # control: the same POST from a loopback origin reaches the route
        r2 = a.test_client().post("/load", data={"folder": str(tmp_path / "nothing")},
                                  headers={"Host": "127.0.0.1:5050",
                                           "Origin": "http://127.0.0.1:5050"})
        assert r2.status_code != 403

    def test_the_operator_can_list_extra_hosts(self, tmp_path, monkeypatch):
        monkeypatch.setenv(ALLOWED_HOSTS_ENV, "lab-pc, Lab-PC.example:8080")
        a = create_app(testing=True, instance_path=str(tmp_path / "inst"))
        assert _get(a, "lab-pc:5050").status_code != 403
        assert _get(a, "lab-pc.example").status_code != 403
        assert _get(a, "other-pc:5050").status_code == 403

    def test_the_bind_host_is_served(self, app):
        allow_bind_host(app, "lab-pc.example")
        assert _get(app, "lab-pc.example:5050").status_code != 403
        assert _get(app, "other.example:5050").status_code == 403

    def test_a_wildcard_bind_serves_this_pcs_own_names(self, app, monkeypatch):
        monkeypatch.setattr(app_mod, "_machine_names", lambda: {"lab-pc", "192.168.7.20"})
        allow_bind_host(app, "0.0.0.0")
        assert _get(app, "lab-pc:5050").status_code != 403
        assert _get(app, "192.168.7.20:5050").status_code != 403
        assert _get(app, "evil.example:5050").status_code == 403

    def test_a_loopback_bind_adds_nothing(self, app):
        allow_bind_host(app, "127.0.0.1")
        assert app.config["SM_ALLOWED_HOSTS"] == set()


class TestHostName:
    @pytest.mark.parametrize("raw,want", [
        ("localhost:5050", "localhost"), ("[::1]:5050", "::1"), ("::1", "::1"),
        ("Example.COM.", "example.com"), ("", ""), (None, "")])
    def test_host_name(self, raw, want):
        assert host_name(raw) == want

    @pytest.mark.parametrize("name,want", [
        ("localhost", True), ("127.0.0.1", True), ("127.9.9.9", True), ("::1", True),
        ("10.0.0.1", False), ("localhost.example", False), ("", False)])
    def test_is_loopback_host(self, name, want):
        assert is_loopback_host(name) is want


def test_qsm_serve_answers_to_the_host_it_binds(tmp_path, monkeypatch):
    from quam_state_manager import cli
    a = create_app(testing=True, instance_path=str(tmp_path / "inst"))
    assert _get(a, "lab-pc.example:5999").status_code == 403
    monkeypatch.setattr(a, "run", lambda **kw: None)
    cli._run_app(a, host="lab-pc.example", port=5999, debug=True)
    assert _get(a, "lab-pc.example:5999").status_code != 403
