"""R3 MINOR 10b: an unanswered share is not a missing chip folder."""
from __future__ import annotations

import pytest

from quam_state_manager.core import folder_probe
from quam_state_manager.web import routes
from tests.test_v151_wrong_cause_messages import _app, _chip, _ctx


@pytest.mark.parametrize("folder,anchor", [(None, True), (False, False), (False, None)])
def test_an_unreachable_folder_is_named_in_the_control_and_panel(tmp_path, monkeypatch, folder, anchor):
    chip = _chip(tmp_path / "chip")
    app, c = _app(tmp_path)
    c.post("/load", data={"folder": str(chip)})
    chip.rename(tmp_path / "parked")
    asks = []
    def present(paths, **kwargs):
        asks.extend(str(p) for p in paths)
        return {str(p): anchor if str(p) == chip.anchor else folder for p in paths}
    monkeypatch.setattr(folder_probe, "folders_present", present)
    monkeypatch.setattr(routes, "_LIVE_UNREADABLE_AFTER_S", 0.0)
    with app.test_request_context():
        routes._sync_live_refresh(_ctx(app))
        routes._sync_live_refresh(_ctx(app))
    tray = c.get("/state/tray").get_data(as_text=True)
    # the control's own headline, not only the reason in its hover text
    assert 'sync-control-text">Live chip folder unreachable<' in tray, tray[:3000]
    assert "Can&#39;t read the live chip" not in tray and "Can't read the live chip" not in tray
    for html in (tray, c.get("/state/review").get_data(as_text=True)):
        assert "Live chip folder unreachable" in html, html[:3000]
        assert "drive/share not answering" in html
        assert "Live chip folder not found" not in html
    assert str(chip) in asks and chip.anchor in asks


def test_a_missing_folder_on_an_answering_drive_is_still_missing(tmp_path, monkeypatch):
    chip = _chip(tmp_path / "chip")
    app, c = _app(tmp_path)
    c.post("/load", data={"folder": str(chip)})
    chip.rename(tmp_path / "parked")
    monkeypatch.setattr(folder_probe, "folders_present", lambda paths, **kw:
                        {str(p): str(p) == chip.anchor for p in paths})
    html = c.get("/state/review").get_data(as_text=True)
    assert "Live chip folder not found" in html and "unreachable" not in html


def _free_drive():
    import os
    import string
    if os.name != "nt":
        return None
    for letter in reversed(string.ascii_uppercase[3:]):
        if not os.path.exists(f"{letter}:\\"):
            return f"{letter}:\\"
    return None


def test_the_real_probe_tells_an_unmapped_drive_from_a_missing_folder(tmp_path, monkeypatch):
    """No stub: the bounded probe itself answers both cases."""
    from pathlib import Path
    drive = _free_drive()
    if drive is None:
        pytest.skip("needs Windows and a free drive letter")
    chip = _chip(tmp_path / "chip")
    app, c = _app(tmp_path)
    c.post("/load", data={"folder": str(chip)})
    wc = _ctx(app)["working_copy"]
    wc.live_folder = Path(drive) / "lab" / "chip"
    html = c.get("/state/review").get_data(as_text=True)
    assert "Live chip folder unreachable" in html and "Live chip folder not found" not in html
    wc.live_folder = chip
    # an earlier caller (a history resolve) found the folder: that answer is
    # remembered for minutes, and a failed read must not trust it
    assert folder_probe.folders_present([str(chip)])[str(chip)] is True
    chip.rename(tmp_path / "parked")
    html = c.get("/state/review").get_data(as_text=True)
    assert "Live chip folder not found" in html and "unreachable" not in html
