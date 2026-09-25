"""datasets-r2-20: a run whose files are broken SAYS so.

A run with a truncated data.json claimed "No figures saved for this run." and
"No fit results available for this run." while its PNG sat in the folder, and
a run whose data.json names a figure file that was deleted showed a broken
image (a 404) with no text. The detail now names the unreadable file, lists
the images that are on disk, and renders "figure file missing" in place of the
image (docs/114: honesty at the moment something goes wrong).
"""
from __future__ import annotations

import json

from quam_state_manager.core.dataset import DatasetStore
from quam_state_manager.web import routes
from tests.test_multifolder_datasets import _app_with_folders, _seed_run

_PNG = (b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
        b"\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\rIDATx\x9cc\xf8\x0f"
        b"\x00\x00\x01\x01\x00\x05\x18\xd8N\x00\x00\x00\x00IEND\xaeB`\x82")


def _with_figure(run, *, truncate=False, delete_png=False):
    data = {"fit_results": {"q1": {"T1": 8e-6}},
            "figures": {"amplitude": "./figures.amplitude.png"}}
    text = json.dumps(data)
    (run / "data.json").write_text(text[: len(text) // 2] if truncate else text,
                                   encoding="utf-8")
    if not delete_png:
        (run / "figures.amplitude.png").write_bytes(_PNG)


def _detail(tmp_path, *, truncate=False, delete_png=False):
    f = tmp_path / "data"
    run = _seed_run(f, 51, qubits=["q1"])
    _with_figure(run, truncate=truncate, delete_png=delete_png)
    app, c = _app_with_folders(tmp_path, [f])
    with app.app_context():
        key = routes._folder_key(f)
    return c.get(f"/dataset/{key}:51", headers={"HX-Request": "true"}).get_data(as_text=True)


class TestStoreHealth:
    def test_a_healthy_run_reports_nothing(self, tmp_path):
        f = tmp_path / "data"
        _with_figure(_seed_run(f, 51, qubits=["q1"]))
        h = DatasetStore(f).run_file_health(51)
        assert h == {"unreadable": [], "files_on_disk": [], "missing_figures": []}

    def test_a_truncated_data_json_is_named_and_the_images_listed(self, tmp_path):
        f = tmp_path / "data"
        _with_figure(_seed_run(f, 51, qubits=["q1"]), truncate=True)
        h = DatasetStore(f).run_file_health(51)
        assert h["unreadable"] == ["data.json"]
        assert h["files_on_disk"] == ["figures.amplitude.png"]

    def test_a_deleted_figure_file_is_named(self, tmp_path):
        f = tmp_path / "data"
        _with_figure(_seed_run(f, 51, qubits=["q1"]), delete_png=True)
        h = DatasetStore(f).run_file_health(51)
        assert h["missing_figures"] == ["figures.amplitude"]
        assert h["unreadable"] == []


class TestDetailSaysIt:
    def test_unreadable_data_json_is_not_no_figures(self, tmp_path):
        html = _detail(tmp_path, truncate=True)
        assert "No figures saved for this run." not in html
        assert "No fit results available for this run." not in html
        assert "No fit results.<" not in html               # the Figures tab's own row too
        assert "data.json could not be read" in html
        assert "figures.amplitude.png" in html          # what IS in the folder
        assert "ds-file-health-note" in html            # the one-line banner

    def test_a_missing_figure_is_text_not_a_broken_image(self, tmp_path):
        html = _detail(tmp_path, delete_png=True)
        assert "figure file missing" in html
        assert "/fig/figures.amplitude" not in html

    def test_a_healthy_run_is_unchanged(self, tmp_path):
        html = _detail(tmp_path)
        assert "/fig/figures.amplitude" in html
        assert "could not be read" not in html
        assert "ds-file-health-note" not in html
        assert '<p class="muted figure-missing">' not in html
        # the late-deletion fallback rides on the image itself
        assert "this.onerror=null" in html
