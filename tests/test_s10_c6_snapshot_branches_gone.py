"""S10 C6: the Versions panel, State History page and History drawer read the
change ledger only; their snapshot-only branches are deleted.

One static pin: every name the deletion removed is absent from the shipped
package, so a revert of any one branch (route arm, template, macro, client
filter) turns this RED.
"""
import re
from pathlib import Path

PKG = Path(__file__).resolve().parents[1] / "quam_state_manager"

# name -> where it lived (for the failure message)
DELETED = {
    # routes.py: the Versions panel's snapshot list and its changes-only filter
    "hidden_unchanged": "Versions panel changes-only filter count",
    "changes_only": "Versions panel changes-only filter mode",
    "visible_total": "Versions panel filtered total",
    "first_ts": "zero-diff baseline rule (panel, page, drawer)",
    "_HISTORY_PANEL_PER_PAGE": "snapshot History drawer page size",
    '_hub_fallback_reached("versions"': "Versions tripwire call",
    '_hub_fallback_reached("state_history"': "State History tripwire call",
    '_hub_fallback_reached("history_drawer"': "History drawer tripwire call",
    '_hub_fallback_reached("history_count"': "History (N) tripwire call",
    '_hub_fallback_reached("version_count"': "version chip tripwire call",
    # templates
    "_history_panel.html": "snapshot History drawer template",
    "_snapshot_zero_label": "shared zero-diff macro template",
    "zero_diff_label": "shared zero-diff macro",
    "data-changes": "panel filter attribute",
    "sv-hidden-note": "panel filter note",
    "ledger_mode": "panel snapshot/ledger switch",
    # app.js / style.css
    "setChanges": "StateVersions filter toggle",
    "_changesMode": "StateVersions filter mode",
    "quam_versions_changes": "stored filter choice",
    "selectHistoryEntry": "snapshot drawer compare tick",
    "compareSelectedSnapshots": "snapshot drawer Compare Selected",
    "history-compare-cb": "snapshot drawer compare checkbox",
    "history-page-size": "snapshot drawer page-size select",
}


def _shipped_text():
    for p in PKG.rglob("*"):
        if p.suffix in (".py", ".html", ".js", ".css") and "vendor" not in p.parts:
            yield p, p.read_text(encoding="utf-8", errors="replace")


def test_the_deleted_snapshot_branch_names_are_absent():
    for name in ("_history_panel.html", "_snapshot_zero_label.html"):
        assert not (PKG / "web" / "templates" / name).exists(), name
    shipped = list(_shipped_text())
    assert any(p.name == "app.js" for p, _ in shipped) and len(shipped) > 50, "nothing scanned"
    hits = [f"{name} ({where}) in {p.relative_to(PKG)}"
            for p, text in shipped
            for name, where in DELETED.items() if name in text]
    assert hits == [], hits
    # one comparison rule: the N-way Compare no longer reaches Differ's N-way diff
    # itself (hub_versions.compare_n does, under compare_equal)
    routes = (PKG / "web" / "routes.py").read_text(encoding="utf-8")
    assert re.search(r"\bdiff_n\b", routes) is None
