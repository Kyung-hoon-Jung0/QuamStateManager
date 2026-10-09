"""Run isolated folder-offer mutations; always restore the original bytes."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import subprocess

ROOT = Path(__file__).resolve().parents[1]
ROUTES = "quam_state_manager/web/routes.py"
VALUE = "quam_state_manager/core/value_history.py"
APP = "quam_state_manager/web/static/app.js"
JS = "quam_state_manager/web/static/hub-folders.js"
TEMPLATES = "quam_state_manager/web/templates/"
TEST = "tests/test_hub_link_folder.py"
MUTATIONS = []


def add(name, file, before, after, pin):
    MUTATIONS.append((name, file, before, after, pin))


add("rank", ROUTES, '(-row["matches"], row["fs_key"])', '(row["matches"], row["fs_key"])', "test_candidate_bucketing_and_ranking")
add("deepest", ROUTES, 'rows[max(holders, key=len)]', 'rows[min(holders, key=len)]', "test_deepest_root_owns_evidence_and_prefix_boundaries")
add("boundary", ROUTES, 'path_key.startswith(base + os.sep)', 'path_key.startswith(base)', "test_deepest_root_owns_evidence_and_prefix_boundaries")
add("unreadable", ROUTES, '("unreadable", alignment.get("unknown") or [])', '("other", alignment.get("unknown") or [])', "test_deepest_root_owns_evidence_and_prefix_boundaries")
add("renamed", ROUTES, '("other", alignment.get("renamed") or [])', '("unreadable", alignment.get("renamed") or [])', "test_deepest_root_owns_evidence_and_prefix_boundaries")
add("inside", ROUTES, '"inside": _hub_root_contains(_hub_root_key(r), _hub_root_key(ctx["path"]))', '"inside": False', "test_deepest_root_owns_evidence_and_prefix_boundaries")
add("registered", ROUTES, 'if k not in registered\n', 'if True\n', "test_candidates_exclude_decisions_registered_and_empty_roots")
add("different", ROUTES, '_hub_root_decision(chip_dir.name, row["path"], roots, decisions) != "different"', 'True', "test_candidates_exclude_decisions_registered_and_empty_roots")
add("label", ROUTES, 'return decisions.get(f"{chip_key}::{label}") if label and labels[label] == 1 else None', 'return None', "test_candidates_exclude_decisions_registered_and_empty_roots")
add("holds_runs", ROUTES, 'and hub_sync._holds_runs(Path(row["path"]))', 'and True', "test_candidates_exclude_decisions_registered_and_empty_roots")
add("unique_label", ROUTES, 'label and labels[label] == 1 else None', 'label else None', "test_unique_label_is_not_evidence_for_two_roots")
add("explicit_override", ROUTES, 'if explicit is not None:\n        return explicit', 'if explicit == "same":\n        return explicit', "test_unlink_keeps_events_and_shows_no_folder_even_after_label_link")
add("zero_post", ROUTES, 'row["fs_key"] == fs_key and row["matches"] >= 1', 'row["fs_key"] == fs_key and row["matches"] >= 0', "test_link_refuses_stale_or_unproven_requests[zero]")
add("zero_button", TEMPLATES + "_hub_link_folder.html", 'row.matches >= 1', 'row.matches >= 0', "test_candidate_bucketing_and_ranking")
add("workspace", ROUTES, 'fs_key not in roots or not root', 'False or not root', "test_link_refuses_stale_or_unproven_requests[outside]")
add("root_spelling", ROUTES, 'or _hub_root_key(root) != fs_key', 'or False', "test_link_refuses_stale_or_unproven_requests[mismatched_root]")
add("chip_switch", ROUTES, 'and expected != chip_dir.name:', 'and False:', "test_link_refuses_stale_or_unproven_requests[switch]")
add("scan_ready", ROUTES, 'if request.method == "POST":\n            return "The folder check is still running.", 409', 'if False:\n            return "The folder check is still running.", 409', "test_link_refuses_stale_or_unproven_requests[running]")
add("switch_after_scan", ROUTES, '_ctx_now, _dir_now, error = _hub_link_context()\n        if error:', '_ctx_now, _dir_now, error = _hub_link_context()\n        if False:', "test_scan_chip_switch_is_rechecked_before_saving")
add("live_type", ROUTES, 'if not ctx or ctx.get("type") != "quam":\n        return None, None, ("No live chip is open.", 409)', 'if not ctx:\n        return None, None, ("No live chip is open.", 409)', "test_live_context_required")
add("archive_doors", ROUTES, 'if (ctx.get("origin") or "live") != "live":\n        return None, None, ("read-only", 409)', 'if False:\n        return None, None, ("read-only", 409)', "test_archive_link_doors_are_read_only")
add("wait_budget", ROUTES, 'Path(ctx["path"]), ws, wait_s=0.12)', 'Path(ctx["path"]), ws, wait_s=0.0)', "test_pending_fragment_keeps_chip_and_refetches")
add("self_refetch", TEMPLATES + "_hub_link_folder.html", 'hx-trigger="every 1s"', 'hx-trigger="load"', "test_pending_fragment_keeps_chip_and_refetches")
add("hint", TEMPLATES + "_hub_link_folder.html", "this chip's state folder is inside it", 'State folder', "test_state_folder_hint_is_rendered")
add("written_key", ROUTES, 'f"root:{fs_key}", "same")', 'f"root:{root}", "same")', "test_link_writes_the_read_key_and_registers_then_shows_run_rows")
add("link_sync", ROUTES, '_hub_sync_open(ctx)\n        return "Folder linked."', 'pass\n        return "Folder linked."', "test_link_writes_the_read_key_and_registers_then_shows_run_rows")
add("unlink_decision", ROUTES, 'f"root:{fs_key}", "different")', 'f"root:{fs_key}", "same")', "test_unlink_keeps_events_and_shows_no_folder_even_after_label_link")
add("unlink_sync", ROUTES, '_hub_sync_open(ctx)\n    return "Folder unlinked."', 'pass\n    return "Folder unlinked."', "test_unlink_keeps_events_and_shows_no_folder_even_after_label_link")
add("unlink_source", ROUTES, '== fs_key and "decided_same" in r.get("sources", [])', '== fs_key and True', "test_unlink_refuses_a_declared_root")
add("decide_sync", ROUTES, 'if chip_dir is not None and chip_dir.name == chip_key:\n            _hub_sync_open(ctx)', 'if chip_dir is not None and chip_dir.name == chip_key:\n            pass', "test_decide_same_reregisters_only_the_open_chip")
add("decide_open_chip", ROUTES, 'if chip_dir is not None and chip_dir.name == chip_key:', 'if chip_dir is not None:', "test_decide_same_reregisters_only_the_open_chip")
add("link_event", ROUTES, 'return "Folder linked.", 200, {"HX-Trigger": "hubLinked"}', 'return "Folder linked.", 200, {"HX-Trigger": "other"}', "test_link_writes_the_read_key_and_registers_then_shows_run_rows")
add("unlink_event", ROUTES, 'return "Folder unlinked.", 200, {"HX-Trigger": "hubLinked"}', 'return "Folder unlinked.", 200, {"HX-Trigger": "other"}', "test_unlink_keeps_events_and_shows_no_folder_even_after_label_link")
add("note", VALUE, 'and not ledger.get("has_runs") and origin == "live"):', 'and False):', "test_no_roots_offer_on_html_surfaces")
runs_gate = '''elif not st.get("roots") and state in ("ready", "degraded") and ledger.get("has_runs"):
        out.append({"level": "info", "code": "no_folder",
                    "text": "No data folder is linked to this chip now; newer runs may be missing."})
    elif (not st.get("roots") and state in ("ready", "degraded")
          and not ledger.get("has_runs") and origin == "live"):'''
add("note_runs", VALUE, runs_gate, runs_gate.replace('and ledger.get("has_runs"):', 'and False:').replace('and not ledger.get("has_runs")', 'and True'), "test_note_condition")
add("note_roots", VALUE, 'elif (not st.get("roots") and state in ("ready", "degraded")', 'elif (state in ("ready", "degraded")', "test_note_condition")
add("note_state", VALUE, 'elif (not st.get("roots") and state in ("ready", "degraded")', 'elif (not st.get("roots") and state != "idle"', "test_note_condition")
add("note_degraded", VALUE, 'elif (not st.get("roots") and state in ("ready", "degraded")', 'elif (not st.get("roots") and state == "ready"', "test_note_condition")
add("note_archive", VALUE, 'and not ledger.get("has_runs") and origin == "live"):', 'and not ledger.get("has_runs")):', "test_archive_history_has_no_offer")
add("value_archive", ROUTES, 'newest=newest,\n                                   origin=ctx.get("origin") or "live")', 'newest=newest)', "test_archive_history_has_no_offer[/field/history?path=qubits.qA1.T1]")
add("table_archive", "quam_state_manager/web/hub_status.py", 'answer["ledger"], origin=ctx.get("origin") or "live")', 'answer["ledger"])', "test_archive_history_has_no_offer[/topology/trends]")
add("versions_archive", ROUTES, '{"has_runs": res.get("has_runs")},\n                          origin=ctx.get("origin") or "live")', '{"has_runs": res.get("has_runs")})', "test_archive_history_has_no_offer[/state/versions]")
add("offer_button", TEMPLATES + "_hub_notes.html", "{% if note.code == 'no_folder_linked' %}", "{% if false %}", "test_no_roots_offer_on_html_surfaces")
for surface, file in (("drawer", "_field_history_ledger.html"), ("column", "_column_history_ledger.html"),
                      ("versions", "_state_versions.html"), ("state_history", "_state_history_body.html"),
                      ("trends", "_topo_trends.html"), ("grid", "_param_history.html"),
                      ("changes", "_param_history_changes.html"), ("cell", "_param_history_drawer.html")):
    add("notes_" + surface, TEMPLATES + file, "{% include '_hub_notes.html' %}", "", "test_no_roots_offer_on_html_surfaces[" + surface + "]")
add("meta_json", ROUTES, '**({"link": {"offer": True, "url": "/hub/link-folder"}}', '**({"link": {"offer": False, "url": "/hub/link-folder"}}', "test_json_surfaces_offer_a_read_url[meta]")
add("agent_json", "quam_state_manager/web/agent_api.py", '**({"link": {"offer": True, "url": "/hub/link-folder"}}', '**({"link": {"offer": False, "url": "/hub/link-folder"}}', "test_json_surfaces_offer_a_read_url[agent]")
add("agent_write_door", "quam_state_manager/web/agent_api.py", 'def field_history():', '@agent_bp.route("/hub/link-folder", methods=["POST"])\ndef field_history():', "test_json_surfaces_offer_a_read_url[agent]")
add("focus_trap", JS, 'release = window.trapFocus(overlay.querySelector(\'.ch-card\'), close);', 'release = function () {};', "test_modal_focus_and_refresh_selfcheck")
add("nested_focus", APP, 'if (folderModal && !folderModal.contains(container)) return;', 'if (false) return;', "test_modal_focus_and_refresh_selfcheck")
add("modal_errors", JS, "overlay.querySelector('#hub-folder-body').textContent = event.detail.xhr.responseText;", 'return;', "test_modal_focus_and_refresh_selfcheck")
add("accessible", JS, 'aria-modal="true"', '', "test_modal_focus_and_refresh_selfcheck")
add("surface_refresh", JS, "document.addEventListener('hubLinked', function () {", "document.addEventListener('other', function () {", "test_modal_focus_and_refresh_selfcheck")
add("refresh_swap", JS, "? 'outerHTML' : 'innerHTML'", "? 'innerHTML' : 'innerHTML'", "test_modal_focus_and_refresh_selfcheck")
add("drawer_refresh", APP, 'document.addEventListener("hubLinked", function () {\n        if (panel', 'document.addEventListener("other", function () {\n        if (panel', "test_modal_focus_and_refresh_selfcheck")
add("column_refresh", APP, 'document.addEventListener("hubLinked", function () {\n        if (overlay', 'document.addEventListener("other", function () {\n        if (overlay', "test_modal_focus_and_refresh_selfcheck")
add("versions_refresh", APP, "document.addEventListener('hubLinked', function () {\n        var p = panel()", "document.addEventListener('other', function () {\n        var p = panel()", "test_modal_focus_and_refresh_selfcheck")
add("history_refresh", TEMPLATES + "_state_history.html", ', hubLinked from:body', '', "test_modal_focus_and_refresh_selfcheck")
add("script_loaded", TEMPLATES + "base.html", '<script src="{{ asset_url(\'hub-folders.js\') }}"></script>', '', "test_modal_focus_and_refresh_selfcheck")
add("decide_key", ROUTES, 'data_folder = "root:" + _hub_root_key(data_folder[5:])', 'data_folder = data_folder', "test_decide_root_uses_the_shared_key")
add("current_evidence", ROUTES, 'row["fs_key"] == fs_key and row["matches"] >= 1', 'row["fs_key"] == fs_key and row["matches"] >= 0', "test_link_checks_the_scan_again_at_press_time")
add("link_building", ROUTES, '_hub_sync_open(ctx)\n        return "Folder linked."', 'pass\n        return "Folder linked."', "test_link_builds_before_run_rows_are_read")
add("events_kept", ROUTES, '_hub_sync_open(ctx)\n    return "Folder unlinked."', '_hub_sync_open(ctx)\n    from quam_state_manager.core.hub_store import HubStore\n    with HubStore(chip_dir) as ledger:\n        ledger.conn.execute("UPDATE events SET actor=\'changed\'")\n        ledger.conn.commit()\n    return "Folder unlinked."', "test_unlink_keeps_events_and_shows_no_folder_even_after_label_link")
add("retained_note", VALUE, '"code": "no_folder",', '"code": "other",', "test_unlink_keeps_events_and_shows_no_folder_even_after_label_link")
add("empty_text", TEMPLATES + "_hub_link_folder.html", 'No Datasets folder holds runs of this chip.', 'No folder is available.', "test_candidates_exclude_decisions_registered_and_empty_roots")
add("modal_layer", "quam_state_manager/web/static/style.css", '#hub-folder-modal { z-index: 9700; }', '#hub-folder-modal { z-index: 9550; }', "test_modal_focus_and_refresh_selfcheck")
add("modal_scroll", "quam_state_manager/web/static/style.css", '#hub-folder-body { overflow: auto;', '#hub-folder-body { overflow: hidden;', "test_modal_focus_and_refresh_selfcheck")
add("read_key", ROUTES, 'explicit = decisions.get(f"{chip_key}::root:{_hub_root_key(root)}")', 'explicit = decisions.get(f"{chip_key}::folder:{_hub_root_key(root)}")', "test_link_writes_the_read_key_and_registers_then_shows_run_rows")


def pytest_run(pin=None):
    env = {**os.environ, "PYTHONUTF8": "1", "NODE_PATH": "D:/work/statemanager/node_modules"}
    command = ["conda", "run", "-n", os.environ["HUB_TEST_ENV"], "python", "-m", "pytest", TEST + ("::" + pin if pin else ""),
               "-q", "-p", "no:cacheprovider", "--timeout=900", "--timeout-method=thread"]
    for attempt in range(3):
        result = subprocess.run(command, cwd=ROOT, env=env, capture_output=True, text=True, encoding="utf-8", errors="replace")
        output = result.stdout + result.stderr
        if "__conda_tmp" not in output or "being used by another process" not in output:
            return result.returncode, output
    return result.returncode, output


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--only", nargs="*")
    args = parser.parse_args()
    selected = [m for m in MUTATIONS if args.only is None or m[0] in args.only]
    code, output = pytest_run()
    assert code == 0, output
    results = []
    for name, file, before, after, pin in selected:
        path = ROOT / file
        original = path.read_bytes()
        source = original.decode("utf-8").replace("\r\n", "\n")
        assert source.count(before) == 1, (name, source.count(before))
        try:
            path.write_text(source.replace(before, after), encoding="utf-8")
            code, output = pytest_run(pin)
            red = code == 1 and "AssertionError" in output and not re.search(r"^ERROR tests", output, re.M)
            summary = re.findall(r"\d+ failed[^\r\n]*", output)
            results.append({"mutation": name, "file": file, "pin": pin,
                            "assertion_failure": red, "summary": summary[-1] if summary else "no assertion summary"})
            print(name + ": " + ("RED" if red else "FAILED CHECK"), flush=True)
            if not red:
                print(output[-6000:], flush=True)
        finally:
            path.write_bytes(original)
    code, output = pytest_run()
    target = ROOT / "docs" / "s10_c2_mutations.json"
    if args.only is not None and target.exists():
        previous = json.loads(target.read_text(encoding="utf-8"))
        by_name = {r["mutation"]: r for r in previous["mutations"]}
        by_name.update({r["mutation"]: r for r in results})
        results = list(by_name.values())
    report = {"mutations": results, "restored_green": code == 0,
              "green_summary": re.findall(r"\d+ passed[^\r\n]*", output)[-1:]}
    target.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    assert code == 0, output
    assert all(r["assertion_failure"] for r in results), "A mutation did not fail its pin."
    print(str(len(results)) + " mutations RED; restored pins GREEN", flush=True)


if __name__ == "__main__":
    main()
