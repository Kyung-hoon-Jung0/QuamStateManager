"""v1.3.0 B1 P2: a page reloaded after SM restarted points at the real fix.

After a restart no chip is open. Reloading a deep page said "Load a chip ...
pick a state folder with State Load" (chip menus), "No dataset folder is open
-- open the chip's data folder on the Datasets page" (Calibration log) or "Add
a data folder in the Workspace panel" (Datasets / Trends -- a panel renamed
long ago). The fix is re-opening the chip that was open. Each of these now
offers one "Re-open <chip>" button (the last State Load, when its folder still
holds a state.json), which re-opens it and lands back on the same page."""
import json
from pathlib import Path

import pytest

from quam_state_manager.web.app import create_app


def _chip(root: Path) -> Path:
    from tests.test_web import _make_state, _make_wiring
    d = root / "labchip"
    d.mkdir()
    (d / "state.json").write_text(json.dumps(_make_state()), encoding="utf-8")
    (d / "wiring.json").write_text(json.dumps(_make_wiring()), encoding="utf-8")
    return d


@pytest.fixture
def restarted(tmp_path):
    """An SM whose last session had *chip* open, started again (nothing open)."""
    chip = _chip(tmp_path)
    inst = tmp_path / "inst"
    app = create_app(testing=True, instance_path=str(inst))
    # written after create_app: its startup hygiene drops a last path under the
    # OS temp dir (old test leftovers), which is where pytest's tmp_path lives
    (inst / "last_session.json").write_text(
        json.dumps({"last_quam_state_path": str(chip)}), encoding="utf-8")
    return app.test_client(), chip


def _offer(html: str):
    import re
    m = re.search(r'<form class="resume-offer".*?</form>', html, re.S)
    return m.group(0) if m else None


@pytest.mark.parametrize("page", ["/qubits", "/bulk", "/topology?view=trends", "/journal",
                                  "/datasets", "/trends"])
@pytest.mark.parametrize("hx", [False, True], ids=["reload", "menu"])
def test_every_deep_page_offers_the_chip_that_was_open(restarted, page, hx):
    c, chip = restarted
    html = c.get(page, headers={"HX-Request": "true"} if hx else {}).get_data(as_text=True)
    form = _offer(html)
    assert form, html[:400]
    assert f'value="{chip}"' in form and "Re-open labchip" in form
    assert f'name="next" value="{page}"' in form.replace("&amp;", "&")
    assert "Workspace</strong> panel" not in html
    assert "open the chip's data folder on the Datasets page" not in html


@pytest.mark.parametrize("hx", [False, True], ids=["form", "htmx"])
def test_re_open_lands_back_on_the_page(restarted, hx):
    c, chip = restarted
    r = c.post("/load", data={"folder": str(chip), "next": "/topology?view=trends"},
               headers={"HX-Request": "true"} if hx else {})
    where = r.headers.get("HX-Redirect") if hx else r.headers.get("Location")
    assert where and where.endswith("/topology?view=trends"), (r.status_code, where)
    assert "Re-open" not in c.get("/qubits").get_data(as_text=True)     # a chip is open now


@pytest.mark.parametrize("bad", ["//evil.example/x", "https://evil.example/", "/load",
                                 "\\\\evil\\x", "qubits", ""])
def test_an_unsafe_next_lands_on_the_explorer(restarted, bad):
    c, chip = restarted
    r = c.post("/load", data={"folder": str(chip), "next": bad}, headers={"HX-Request": "true"})
    assert r.headers.get("HX-Redirect") == "/explorer", (bad, r.headers.get("HX-Redirect"))


def test_without_a_last_chip_the_old_words_stay(tmp_path):
    c = create_app(testing=True, instance_path=str(tmp_path / "inst")).test_client()
    html = c.get("/qubits").get_data(as_text=True)
    assert "No chip loaded" in html and "Load a chip to view qubits." in html and not _offer(html)
    j = c.get("/journal").get_data(as_text=True)
    assert "open the chip's data folder on the Datasets page" in j and not _offer(j)


def test_a_last_chip_that_is_gone_is_not_offered(restarted):
    c, chip = restarted
    (chip / "state.json").unlink()
    assert not _offer(c.get("/qubits").get_data(as_text=True))
