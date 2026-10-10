"""v1.3.0 B1 P2: a malformed request is a 4xx that says what was wrong, never a 500.

Found by a sweep that sent every route of the url map missing / wrong-typed /
huge / NUL-carrying parameters: a JSON body that is not an object reached
``.get()``; a list where text was expected reached ``.strip()``; a NUL byte or
an over-long path raised ValueError where the code caught only OSError; two
integer query args were parsed with a bare ``int()``; and a relative journal
folder was created in the server's working directory."""
import json
import os
from pathlib import Path

import pytest

from quam_state_manager.web.app import create_app

_H = {"Origin": "http://localhost"}
_BAD_PATHS = pytest.mark.parametrize("bad", ["a\x00b", "C:/x/" + "A" * 40000], ids=["nul", "long"])


def _app(tmp_path):
    a = create_app(testing=True, instance_path=str(tmp_path / "inst"))
    a.config["PROPAGATE_EXCEPTIONS"] = False     # a crash answers 500, as in production
    return a


@pytest.fixture
def client(tmp_path):
    return _app(tmp_path).test_client()


@pytest.fixture
def chip_client(tmp_path):
    from tests.test_web import _make_state, _make_wiring
    d = tmp_path / "chip"
    d.mkdir()
    (d / "state.json").write_text(json.dumps(_make_state(), indent=2), encoding="utf-8")
    (d / "wiring.json").write_text(json.dumps(_make_wiring(), indent=2), encoding="utf-8")
    c = _app(tmp_path).test_client()
    assert c.post("/load", data={"folder": str(d)}).status_code in (200, 302)
    return c


def _post_raw(client, url, raw):
    return client.post(url, data=raw, content_type="application/json", headers=_H)


# routes whose body was read with ``get_json(silent=True) or {}`` / ``or request.form.to_dict()``
OBJECT_ROUTES = ["/api/agent/session/stop", "/api/agent/setup/connect", "/api/pulse/synth",
                 "/generate/load", "/scheduler/presets", "/disk/settings", "/disk/reclaim",
                 "/journal/adopt", "/journal/claim", "/config/preview"]
# ... and the ones that first ask for an open chip
CHIP_OBJECT_ROUTES = ["/api/agent/note", "/api/agent/plans", "/api/agent/chat/send",
                      "/type-fix/apply"]
NOT_OBJECTS = pytest.mark.parametrize("raw", ['"s"', "12", "[1, 2]"], ids=["str", "num", "list"])


class TestABodyThatIsNotAnObject:
    @pytest.mark.parametrize("url", OBJECT_ROUTES)
    @NOT_OBJECTS
    def test_is_a_400_naming_the_shape(self, client, url, raw):
        r = _post_raw(client, url, raw)
        assert r.status_code == 400, (url, raw, r.status_code, r.data[:200])
        assert "must be a JSON object" in r.get_json()["error"]

    @pytest.mark.parametrize("url", CHIP_OBJECT_ROUTES)
    @NOT_OBJECTS
    def test_with_a_chip_open_too(self, chip_client, url, raw):
        r = _post_raw(chip_client, url, raw)
        assert r.status_code == 400, (url, raw, r.status_code, r.data[:200])
        assert "must be a JSON object" in r.get_json()["error"]

    def test_an_absent_body_still_reads_as_empty(self, client):
        # control: the helper changed nothing for a request without a JSON body
        r = client.post("/journal/adopt", data={"day": "not-a-day"}, headers=_H)
        assert r.status_code != 500


class TestAFieldOfTheWrongType:
    @pytest.mark.parametrize("url,body,field", [
        ("/generate/load", {"path": ["x"]}, "path"),
        ("/generate/preview-config", {"path": {"a": 1}}, "path"),
        ("/scheduler/presets", {"name": [1]}, "name"),
        ("/scheduler/queue/add", {"file": [1]}, "file"),
        ("/scheduler/register-storage", {"folder": [1]}, "folder"),
        ("/journal/adopt", {"day": ["2026-10-01"]}, "day"),
        ("/api/pulse/synth", {"qclass": ["x"]}, "qclass"),
        ("/api/agent/journal/root", {"root": ["x"]}, "root"),
    ])
    def test_is_a_400_naming_the_field(self, client, url, body, field):
        r = client.post(url, json=body, headers=_H)
        assert r.status_code == 400, (url, r.status_code, r.data[:300])
        assert f"'{field}' must be text" in r.get_json()["error"]

    def test_a_chip_route_too(self, chip_client):
        r = chip_client.post("/type-fix/apply", json={"sig": ["x"]}, headers=_H)
        assert r.status_code == 400, r.data[:200]
        assert "'sig' must be text" in r.get_json()["error"]

    def test_reclaim_names_must_be_names(self, client):
        r = client.post("/disk/reclaim", json={"names": [{"a": 1}]}, headers=_H)
        assert r.status_code == 400
        assert "list of folder names" in r.get_json()["error"]


class TestAPathTheOsCannotName:
    @_BAD_PATHS
    def test_open_folder(self, client, bad):
        r = client.post("/open-folder", data={"folder": bad}, headers=_H)
        assert r.status_code == 400, r.data[:200]

    @_BAD_PATHS
    def test_workspace_remove(self, client, bad):
        r = client.post("/workspace/remove", data={"folder": bad}, headers=_H)
        assert r.status_code == 400, r.data[:200]

    @pytest.mark.parametrize("url", ["/qualibrate/locate", "/qualibrate/use-location"])
    @_BAD_PATHS
    def test_qualibrate_locate(self, client, url, bad):
        r = client.post(url, data={"path": bad, "folder": bad, "location": bad}, headers=_H)
        assert r.status_code < 500, r.data[:200]

    def test_a_diagnose_figure_name_with_a_nul(self, client):
        r = client.get("/dataset/x/autofit-diagnose-fig/%00x.png")
        assert r.status_code == 404

    def test_a_diagnose_figure_cannot_leave_its_own_folder(self, client):
        inst = Path(client.application.instance_path)
        sib = inst / "autofit" / "diagnose" / "xy"
        sib.mkdir(parents=True)
        (sib / "f.png").write_bytes(b"\x89PNG")
        (inst / "autofit" / "diagnose" / "x").mkdir()
        # a prefix match ("...\\x" is a prefix of "...\\xy") used to let this through
        r = client.get("/dataset/x/autofit-diagnose-fig/..%5Cxy%5Cf.png")
        assert r.status_code == 404


class TestIntegerQueryArgs:
    @pytest.mark.parametrize("url", ["/api/agent/events?n=abc", "/api/agent/events?n=1e400",
                                     "/api/agent/chat/events?after=abc",
                                     "/api/agent/chat/events?after=..%2F..%2Fx",
                                     "/api/agent/chat/events?limit=abc"])
    def test_an_unparseable_number_takes_the_default(self, client, url):
        r = client.get(url)
        assert r.status_code == 200, (url, r.status_code, r.data[:200])


class TestARelativeJournalFolder:
    @pytest.mark.parametrize("url", ["/api/agent/setup/journal", "/api/agent/journal/root"])
    def test_is_refused_and_nothing_is_made_in_the_cwd(self, client, tmp_path, monkeypatch, url):
        cwd = tmp_path / "cwd"
        cwd.mkdir()
        monkeypatch.chdir(cwd)
        r = client.post(url, json={"root": "rel_journal"}, headers=_H)
        assert r.status_code == 400, r.data[:200]
        assert "absolute path" in r.get_json()["error"]
        assert os.listdir(cwd) == []

    def test_an_absolute_folder_still_works(self, client, tmp_path):
        target = tmp_path / "vault"
        r = client.post("/api/agent/setup/journal", json={"root": str(target)}, headers=_H)
        assert r.status_code == 200, r.data[:200]
        assert target.is_dir()
