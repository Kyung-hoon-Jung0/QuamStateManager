"""Queue item 6: the Datasets run detail keeps the reader's place EXACTLY
across run switches.

The anchor module (web/static/ds-scroll-anchor.js) and its app.js wiring are
driven under jsdom with a fake layout by `ds_scroll_anchor_selfcheck.cjs`
(landmark identity, walk-up, the late-load pin, reader detection, a 400-switch
randomized sequence against a cold recompute, and the capture-phase wiring).
The real-Chrome proof is tests/browser/journeys/ds_scroll_keep.cjs.
"""
import shutil
import subprocess
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent


@pytest.mark.skipif(shutil.which("node") is None, reason="node not on PATH")
def test_ds_scroll_anchor_selfcheck_passes():
    r = subprocess.run(
        ["node", str(_ROOT / "tests" / "ds_scroll_anchor_selfcheck.cjs")],
        capture_output=True, text=True, encoding="utf-8", cwd=str(_ROOT), timeout=180,
    )
    if r.returncode == 2:
        pytest.skip("jsdom not installed (run `npm install`)")
    assert r.returncode == 0, (r.stdout + r.stderr)
    assert "FAIL" not in r.stdout, r.stdout
    assert "(0 assertions)" not in r.stdout, r.stdout


# ── Verifier P2: the Figures tab painted the clamped offset first, then
# jumped (20-24 of 24 switches in real Chrome), because an <img> has no box
# until it loads. The PNG header's size is written as the img's width/height
# attributes, so the first painted frame can already hold the place.

def _png(w: int, h: int) -> bytes:
    import struct
    import zlib
    ihdr = struct.pack(">IIBBBBB", w, h, 8, 6, 0, 0, 0)
    chunk = b"IHDR" + ihdr
    return (b"\x89PNG\r\n\x1a\n" + struct.pack(">I", len(ihdr)) + chunk
            + struct.pack(">I", zlib.crc32(chunk) & 0xFFFFFFFF))


class TestFigureBoxReserved:
    def test_png_size_reads_the_ihdr(self, tmp_path):
        from quam_state_manager.core.dataset import png_size
        p = tmp_path / "a.png"
        p.write_bytes(_png(1200, 750))
        assert png_size(p) == (1200, 750)

    def test_non_png_and_truncated_give_none(self, tmp_path):
        from quam_state_manager.core.dataset import png_size
        (tmp_path / "j.png").write_bytes(b"\xff\xd8\xff\xe0" + b"\0" * 40)   # a JPEG named .png
        (tmp_path / "t.png").write_bytes(_png(10, 20)[:20])
        (tmp_path / "z.png").write_bytes(_png(0, 20))
        assert png_size(tmp_path / "j.png") is None
        assert png_size(tmp_path / "t.png") is None
        assert png_size(tmp_path / "z.png") is None
        assert png_size(tmp_path / "missing.png") is None
        bad = bytearray(_png(10, 20)); bad[12:16] = b"tEXt"                 # signature, but no IHDR first
        (tmp_path / "n.png").write_bytes(bytes(bad))
        assert png_size(tmp_path / "n.png") is None

    def test_the_detail_sizes_the_img_box(self, tmp_path):
        import json
        from quam_state_manager.web import routes
        from tests.test_multifolder_datasets import _app_with_folders, _seed_run
        f = tmp_path / "data"
        run = _seed_run(f, 51, qubits=["q1"])
        (run / "data.json").write_text(json.dumps(
            {"figures": {"amp": "./figures.amp.png", "raw": "./figures.raw.png"}}), encoding="utf-8")
        (run / "figures.amp.png").write_bytes(_png(1500, 900))
        (run / "figures.raw.png").write_bytes(b"not a png at all, but served")
        app, c = _app_with_folders(tmp_path, [f])
        with app.app_context():
            key = routes._folder_key(f)
        html = c.get(f"/dataset/{key}:51", headers={"HX-Request": "true"}).get_data(as_text=True)
        import re
        imgs = {m.group(1): m.group(0) for m in
                re.finditer(r'<img src="/dataset/[^"]+/fig/([^"]+)"[^>]*>', html)}
        assert set(imgs) == {"figures.amp", "figures.raw"}, list(imgs)
        assert 'width="1500" height="900"' in imgs["figures.amp"], imgs["figures.amp"]
        assert "width=" not in imgs["figures.raw"] and "height=" not in imgs["figures.raw"]

    def test_the_css_lets_the_attributes_set_the_ratio(self):
        css = (_ROOT / "quam_state_manager" / "web" / "static" / "style.css").read_text(encoding="utf-8")
        import re
        m = re.search(r"^\.figure-card img \{([^}]*)\}", css, re.M)
        assert m and "width: 100%" in m.group(1) and "height: auto" in m.group(1), m and m.group(1)
