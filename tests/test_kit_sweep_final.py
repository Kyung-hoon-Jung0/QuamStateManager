"""Kit sweep, final pass: controls that still drew their own look now use the kit parts.

Each test reads the shipped template or stylesheet and finds the one element or rule it pins, so reverting the fix
line turns it red.
"""
from html.parser import HTMLParser
from pathlib import Path
import re

WEB = Path(__file__).resolve().parents[1] / "quam_state_manager" / "web"
TEMPLATES = WEB / "templates"
KIT_CSS = (WEB / "static" / "ui-kit.css").read_text(encoding="utf-8")
FINAL_BLOCK = KIT_CSS[KIT_CSS.index("/* Kit sweep wave 2: final */"):]


class _Tags(HTMLParser):
    """Collects (tag, attrs, parent chain) for every start tag of a template, Jinja left as text."""

    def __init__(self):
        super().__init__()
        self.stack, self.found = [], []

    def handle_starttag(self, tag, attrs):
        entry = (tag, dict(attrs), list(self.stack))
        self.found.append(entry)
        if tag not in ("input", "link", "meta", "br", "img"):
            self.stack.append(entry)

    def handle_endtag(self, tag):
        for i in range(len(self.stack) - 1, -1, -1):
            if self.stack[i][0] == tag:
                del self.stack[i:]
                break


def _tags(name):
    parser = _Tags()
    parser.feed((TEMPLATES / name).read_text(encoding="utf-8"))
    return parser.found


def _classes(attrs):
    return set((attrs.get("class") or "").split())


def _one(tags, pred, what):
    hits = [t for t in tags if pred(t)]
    assert len(hits) == 1, f"{what}: expected one element, found {len(hits)}"
    return hits[0]


def _rule(selector):
    match = re.search(re.escape(selector) + r"\s*\{([^}]*)\}", FINAL_BLOCK)
    assert match, f"no rule for {selector} in the final kit block"
    return match.group(1)


def test_workbench_through_sm_toggle_is_the_kit_check():
    tags = _tags("workbench.html")
    tag, attrs, parents = _one(tags, lambda t: t[1].get("id") == "wb-viasm", "#wb-viasm")
    assert attrs.get("type") == "checkbox"
    assert "ui-check-box" in _classes(attrs)
    assert parents and parents[-1][0] == "label" and "ui-check" in _classes(parents[-1][1])
    # The standalone page loads no shared stylesheet, so it draws the kit box in its own style block.
    style = (TEMPLATES / "workbench.html").read_text(encoding="utf-8")
    box = re.search(r"#wb-bar input\.ui-check-box\s*\{([^}]*)\}", style)
    assert box and re.search(r"(?<![-\w])appearance:\s*none", box.group(1)), "the box must drop the native look"
    assert re.search(r"#wb-bar input\.ui-check-box:checked::after\s*\{[^}]*opacity:\s*1", style)


def test_bookmark_note_is_a_kit_textarea_as_wide_as_the_tag():
    tags = _tags("base.html")
    _, note, _ = _one(tags, lambda t: t[0] == "textarea" and "archive-note" in _classes(t[1]), "note")
    _, tag_in, _ = _one(tags, lambda t: t[0] == "input" and "archive-in" in _classes(t[1]), "tag")
    assert "ui-textarea" in _classes(note)
    assert {"ui-input", "ui-input--block"} <= _classes(tag_in)


def test_chip_report_download_is_a_kit_menu():
    tags = _tags("_wiring.html")
    _, details, _ = _one(tags, lambda t: t[0] == "details" and "topo-report-export" in _classes(t[1]), "report")
    assert "ui-menu" in _classes(details)
    _, summary, parents = _one(tags, lambda t: t[0] == "summary" and t[2] and t[2][-1][1] is details, "trigger")
    assert "ui-menu-trigger" in _classes(summary)
    _, menu, _ = _one(tags, lambda t: "topo-report-menu" in _classes(t[1]), "menu")
    assert "ui-menu-list" in _classes(menu) and menu.get("role") == "menu"
    options = [a for t, a, p in tags if t == "a" and p and p[-1][1] is menu]
    assert len(options) == 3 and all("ui-option" in _classes(a) for a in options)
    assert "display: none" in _rule(".topo-report-export > .ui-menu-trigger::after")
    assert "display: none" in _rule(".topo-report-export > .ui-menu-trigger::-webkit-details-marker")


def test_agent_transcript_keeps_a_readable_height_when_narrow():
    narrow = FINAL_BLOCK[FINAL_BLOCK.index("@media (max-width: 640px)"):]
    body = re.search(r"#table-pane:has\(> \.agent-home\) \.ag-cards\s*\{([^}]*)\}", narrow)
    assert body, "the narrow agent pane must size its transcript"
    assert re.search(r"(?<![-\w])height:\s*\d+vh", body.group(1))
    assert re.search(r"#table-pane:has\(> \.agent-home\)\s*\{[^}]*overflow-y:\s*auto", narrow)


def _z_of(css, selector):
    """The z-index value of the rule whose selector list starts with ``selector`` (var() resolved from :root)."""
    m = re.search(re.escape(selector) + r"\s*\{[^}]*?z-index:\s*([^;}\s]+)", css)
    assert m, "no z-index rule for " + selector
    value = m.group(1)
    var = re.fullmatch(r"var\((--[\w-]+)\)", value)
    if var:
        token = re.search(re.escape(var.group(1)) + r":\s*(\d+)", css)
        assert token, "token not defined: " + var.group(1)
        value = token.group(1)
    return int(value)


def test_the_in_page_confirm_opens_above_the_sync_tray_that_asks_it():
    """Revert this session asks from the sync tray overlay; the confirm must not open underneath it."""
    style = (WEB / "static" / "style.css").read_text(encoding="utf-8")
    tray = _z_of(style, ".state-review-overlay")
    confirm = _z_of(KIT_CSS, "#sm-confirm-overlay")
    backdrop = _z_of(style, ".kb-sheet-backdrop")
    assert confirm > tray, (confirm, tray)
    assert confirm > backdrop, "the id rule must lift the confirm above the shared sheet backdrop"
