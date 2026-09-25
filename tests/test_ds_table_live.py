"""QA datasets-r2-14 + datasets-r2-16 -- the Datasets/Collections table keeps
its own rules live.

* r2-14: Collections holds only runs with >=1 tag (the server's rule). Removing
  a run's last tag or unstarring it left the row drawn until F5, and a delta
  poll added untagged arrivals; dataset-virtual.js now applies the same rule
  client-side and a tag change re-filters.
* r2-16: the header select-all box follows the VISIBLE rows (checked /
  indeterminate / clear) after a filter change, a sort click and Clear; the
  "over" compare bar names how many runs are selected.

Pinned by tests/ds_table_live_selfcheck.cjs, which executes the shipped
dataset-virtual.js, app.js's updateCompareButton and the template's compare-bar
markup under jsdom.
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent


def test_ds_table_live_selfcheck():
    node = shutil.which("node")
    if node is None or subprocess.run([node, "-e", "require('jsdom')"], capture_output=True,
                                      cwd=str(_ROOT)).returncode != 0:
        pytest.skip("jsdom not installed for node")
    res = subprocess.run([node, str(_ROOT / "tests" / "ds_table_live_selfcheck.cjs")],
                         capture_output=True, text=True, encoding="utf-8", cwd=str(_ROOT))
    assert res.returncode == 0, res.stdout + res.stderr
    for line in ("ok - collections: a run with no tag left leaves the list at once",
                 "ok - collections: a delta adds the tagged arrival, not the untagged one",
                 "ok - after the filter changes to ramsey the master box is NOT left checked",
                 "ok - the over message names the count"):
        assert line in res.stdout, res.stdout


def test_the_compare_bar_keeps_its_message_spans():
    """docs/45: keep the state machine and the .ds-compare-msg-* spans; the
    ready span keeps its #ds-compare-count (test_web pins it); the over span
    carries the count by class, never a second id."""
    tpl = (_ROOT / "quam_state_manager/web/templates/_datasets.html").read_text(encoding="utf-8")
    bar = tpl.split('id="ds-compare-bar"', 1)[1].split("</div>", 1)[0]
    for cls in ("ds-compare-msg-one", "ds-compare-msg-ready", "ds-compare-msg-over"):
        assert cls in bar
    assert 'id="ds-compare-count"' in bar
    over = bar.split('ds-compare-msg-over', 1)[1].split("</span>", 1)[0]
    assert 'class="ds-compare-n"' in over


def test_the_resize_grip_sits_above_the_sort_arrow():
    """QA datasets-r2-17: the absolutely-positioned ``th.sortable::after`` sort
    arrow paints after the 7-px ``.ds-resize-handle`` (tree order) and, with no
    z-index on the grip, hit-tests as the TH over most of it -- a press sorted
    instead of resizing. The two sister grips carry ``z-index: 1``; the
    datasets one must too. (jsdom has no hit-testing; verified in real Chrome
    by elementFromPoint across the grip.)"""
    import re
    css = (_ROOT / "quam_state_manager/web/static/style.css").read_text(encoding="utf-8")
    m = re.search(r"(?m)^\.ds-resize-handle\s*\{([^}]*)\}", css)
    assert m, "no .ds-resize-handle rule"
    z = re.search(r"z-index\s*:\s*(\d+)", m.group(1))
    assert z and int(z.group(1)) >= 1, m.group(1)


def test_the_help_button_does_not_cover_the_count_strip():
    """QA F15 (seen while verifying it): the search box's "?" is positioned on
    the wrap's right edge, and a non-empty "Showing N of M" strip is the wrap's
    last flex item -- the button sat on its text ("Showing 1?f 39", and the new
    comparison hint). The strip reserves the button's room while it shows."""
    import re
    css = (_ROOT / "quam_state_manager/web/static/style.css").read_text(encoding="utf-8")
    btn = re.search(r"(?m)^\.ds-search-help-btn\s*\{([^}]*)\}", css)
    assert btn and "position: absolute" in btn.group(1) and "right:" in btn.group(1)
    strip = re.search(r"(?m)^\.ds-search-wrap > #dataset-filter-count:not\(:empty\)\s*\{([^}]*)\}", css)
    assert strip and re.search(r"padding-right:\s*[\d.]+rem", strip.group(1))
