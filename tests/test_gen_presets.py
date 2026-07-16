"""core/gen_presets.py + the /generate/presets routes — the Populate step's
named default-value archive (customer requirement: store default sets of
pulse/readout values and re-apply them to new chips).

Covers slugify, save/load/list/delete round-trips through the Flask routes,
the overwrite confirm flow, section/field whitelist rejections, the size
cap, corrupt-file tolerance in list (never a 500), and concurrent saves.
"""
from __future__ import annotations

import json
import threading

import pytest

from quam_state_manager.core import gen_presets
from quam_state_manager.web.app import create_app


@pytest.fixture
def app(tmp_path):
    return create_app(testing=True, instance_path=str(tmp_path / "instance"))


@pytest.fixture
def client(app):
    return app.test_client()


def _sections(**over):
    base = {
        "pulses": {
            "defaults": {"x180_length": 40e-9, "x180_amplitude": 0.1},
            "overrides": {"q3": {"drag_alpha": 0.62}},
        },
    }
    base.update(over)
    return base


class TestSlugify:
    @pytest.mark.parametrize("name,slug", [
        ("Lab defaults", "lab-defaults"),
        ("  Lab   A / 5-qubit!! ", "lab-a-5-qubit"),
        ("UPPER_case", "upper-case"),
        ("한국어 chip", "chip"),          # non-ascii drops, remainder survives
    ])
    def test_shapes(self, name, slug):
        assert gen_presets.slugify(name) == slug

    def test_empty_raises(self):
        with pytest.raises(ValueError):
            gen_presets.slugify("!!! ///")

    def test_no_traversal_constructible(self):
        assert "/" not in gen_presets.slugify("a/../../b")
        assert ".." not in gen_presets.slugify("a/../../b")


class TestModuleRoundtrip:
    def test_save_load_list_delete(self, tmp_path):
        s = gen_presets.save_preset(tmp_path, "Lab defaults", _sections())
        assert s == {"slug": "lab-defaults", "name": "Lab defaults"}
        loaded = gen_presets.load_preset(tmp_path, "lab-defaults")
        assert loaded["version"] == 1
        assert loaded["sections"]["pulses"]["defaults"]["x180_amplitude"] == 0.1
        assert loaded["created_at"]

        lst = gen_presets.list_presets(tmp_path)
        assert len(lst) == 1
        assert lst[0]["sections"]["pulses"] == {"defaults": 2, "overrides": 1}

        assert gen_presets.delete_preset(tmp_path, "lab-defaults") is True
        assert gen_presets.delete_preset(tmp_path, "lab-defaults") is False
        assert gen_presets.list_presets(tmp_path) == []

    def test_overwrite_flow_preserves_created_at(self, tmp_path):
        gen_presets.save_preset(tmp_path, "P", _sections())
        first = gen_presets.load_preset(tmp_path, "p")
        with pytest.raises(FileExistsError):
            gen_presets.save_preset(tmp_path, "P", _sections())
        gen_presets.save_preset(tmp_path, "P", _sections(), overwrite=True)
        again = gen_presets.load_preset(tmp_path, "p")
        assert again["created_at"] == first["created_at"]

    @pytest.mark.parametrize("sections,frag", [
        ({"nope": {"defaults": {}}}, "unknown section"),
        ({"pulses": {"defaults": {"LO_frequency": 5e9}}}, "unknown field"),
        ({"qubit": {"defaults": {"grid_location": "0,0"}}}, "unknown field"),
        ({"pulses": {"defaults": {"x180_length": [1, 2]}}}, "scalar"),
        ({"pulses": {"weird": {}}}, "unknown key"),
        ({}, "at least one section"),
    ])
    def test_validation_rejects(self, tmp_path, sections, frag):
        with pytest.raises(ValueError, match=frag):
            gen_presets.save_preset(tmp_path, "X", sections)

    def test_size_cap(self, tmp_path):
        big = {"pulses": {"defaults": {}, "overrides": {
            f"q{i}": {"drag_alpha": 0.1 + i} for i in range(500)
        }}}
        # 500 rows is fine …
        gen_presets.save_preset(tmp_path, "big", big)
        # … 501 trips the row cap.
        big["pulses"]["overrides"]["q_extra"] = {"drag_alpha": 9.9}
        with pytest.raises(ValueError, match="500"):
            gen_presets.save_preset(tmp_path, "bigger", big)

    def test_corrupt_file_flagged_not_fatal(self, tmp_path):
        gen_presets.save_preset(tmp_path, "Good", _sections())
        bad = tmp_path / "gen_presets" / "broken.json"
        bad.write_text("{not json", encoding="utf-8")
        lst = gen_presets.list_presets(tmp_path)
        assert {p["slug"] for p in lst} == {"good", "broken"}
        assert [p for p in lst if p["slug"] == "broken"][0]["corrupt"] is True
        assert gen_presets.load_preset(tmp_path, "broken") is None
        assert gen_presets.delete_preset(tmp_path, "broken") is True

    def test_concurrent_saves_both_survive(self, tmp_path):
        errs = []

        def save(name):
            try:
                gen_presets.save_preset(tmp_path, name, _sections())
            except Exception as exc:  # noqa: BLE001
                errs.append(exc)

        threads = [threading.Thread(target=save, args=(f"preset {i}",))
                   for i in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert not errs
        assert len(gen_presets.list_presets(tmp_path)) == 8


class TestRoutes:
    def test_list_empty(self, client):
        r = client.get("/generate/presets")
        assert r.status_code == 200
        assert r.get_json() == {"ok": True, "presets": []}

    def test_save_get_delete_roundtrip(self, client):
        r = client.post("/generate/presets", json={
            "name": "Lab defaults", "sections": _sections()})
        assert r.status_code == 200
        body = r.get_json()
        assert body["ok"] is True and body["slug"] == "lab-defaults"

        r = client.get("/generate/presets/lab-defaults")
        assert r.status_code == 200
        got = r.get_json()
        assert got["ok"] is True
        assert got["sections"]["pulses"]["overrides"]["q3"]["drag_alpha"] == 0.62

        r = client.get("/generate/presets")
        assert len(r.get_json()["presets"]) == 1

        r = client.delete("/generate/presets/lab-defaults")
        assert r.get_json()["ok"] is True
        assert client.get("/generate/presets/lab-defaults").status_code == 404

    def test_save_conflict_confirm_flow(self, client):
        client.post("/generate/presets", json={"name": "P", "sections": _sections()})
        r = client.post("/generate/presets", json={"name": "P", "sections": _sections()})
        body = r.get_json()
        assert body["ok"] is False and body["needs_confirm"] is True
        r = client.post("/generate/presets", json={
            "name": "P", "sections": _sections(), "overwrite": True})
        assert r.get_json()["ok"] is True

    def test_save_validation_400(self, client):
        r = client.post("/generate/presets", json={
            "name": "Bad", "sections": {"pulses": {"defaults": {"nope": 1}}}})
        assert r.status_code == 400
        assert "unknown field" in r.get_json()["error"]

    def test_get_missing_404(self, client):
        assert client.get("/generate/presets/nope").status_code == 404

    def test_get_bad_slug_shape_404(self, client):
        # A slug that slugify would not produce (uppercase) — never a file hit.
        assert client.get("/generate/presets/NoPe").status_code == 404

    def test_files_land_in_instance_dir(self, client, app):
        client.post("/generate/presets", json={"name": "P", "sections": _sections()})
        f = json.loads(
            (
                __import__("pathlib").Path(app.instance_path)
                / "gen_presets" / "p.json"
            ).read_text(encoding="utf-8")
        )
        assert f["name"] == "P"
