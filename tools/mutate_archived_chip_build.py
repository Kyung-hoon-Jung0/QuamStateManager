"""S10 walk (archived chip build) single-anchor mutations; source bytes are restored after each run.

python tools/mutate_archived_chip_build.py --report OUTPUT.json [--only NAME ...]
A mutation is killed only by an assertion failure, never a tool/collection error.
Each mutation runs the pins it targets: a ``-k`` expression over
tests/test_archived_chip_build.py, or a list of pytest node ids.
"""

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]
TEST = "tests/test_archived_chip_build.py"
SYNC = "quam_state_manager/core/hub_sync.py"
ROUTES = "quam_state_manager/web/routes.py"
TEMPLATES = "quam_state_manager/web/templates/"
#: (name, file, anchor, replacement, the pins it must turn red: a -k expression
#: over TEST, or a list of pytest node ids)
MUTATIONS = [
    ("archived_view_never_builds", ROUTES,
     "        blocked = _hub_archive_ensure(hm, target_path, Path(directory))",
     "        blocked = None",
     "shows_its_snapshots_values or reads_and_never_writes"),
    ("adopt_capture_counted_as_a_run", ROUTES,
     "    return bool(getattr(meta, \"trigger\", None) == \"experiment\"",
     "    return bool(getattr(meta, \"kind\", None) == \"exp\" or getattr(meta, \"trigger\", None) == \"experiment\"",
     "adopt_capture"),
    ("open_offer_skips_adopt_captures", ROUTES,
     "        if _is_run_snapshot(meta):\n            continue",
     "        if getattr(meta, \"kind\", None) == \"exp\" or _is_run_snapshot(meta):\n            continue",
     "adopt_capture"),
    ("open_offer_missing", ROUTES,
     "    open_path = _openable_folder_for_chip(hm, target_path)\n    if open_path:\n"
     "        out.append({\"level\": \"info\", \"code\": \"archive_open\"",
     "    open_path = _openable_folder_for_chip(hm, target_path)\n    if False:\n"
     "        out.append({\"level\": \"info\", \"code\": \"archive_open\"",
     "adopt_capture or reads_and_never_writes"),
    ("build_on_the_request_thread", ROUTES,
     "        hub_sync.build_archived(directory, *_hub_archive_sources(hm, target_path, directory))\n",
     "        hub_sync.build_archived(directory, *_hub_archive_sources(hm, target_path, directory))\n"
     "        from quam_state_manager.core import hub as _hub\n"
     "        _hub._PROJECTOR._sync_inline(_hub.Hub.for_chip(directory))\n",
     "build_runs_in_the_background or runs_build_in_the_background"),
    ("progress_not_in_snapshots", ROUTES,
     "    if seen_total:\n        return (f\"The change history is being built from this chip's",
     "    if False:\n        return (f\"The change history is being built from this chip's",
     # S10 mut: + the phase-switch pin (the observing step says its count through progress_words)
     "build_runs_in_the_background or snapshot_count_stays_while_a_slice_relists"),
    ("snapshot_total_not_counted", SYNC,
     "        self.obs_total += len(fresh)\n", "",
     "build_runs_in_the_background"),
    ("snapshot_done_not_counted", SYNC,
     "                    self.obs_done += 1\n", "",
     "build_runs_in_the_background"),
    ("wait_page_names_no_chip", TEMPLATES + "_hub_surface_wait.html",
     "  {% if archive_chip %}\n", "  {% if False %}\n",
     "build_runs_in_the_background"),
    ("cut_short_build_not_resumed", ROUTES,
     "    wanted = peek is None or peek[\"build\"] != hub_sync.ARCHIVE_DONE or unlisted\n",
     "    wanted = peek is None or peek[\"build\"] not in (\"running\", hub_sync.ARCHIVE_DONE) or unlisted\n",
     "cut_short or resume_after_a_restart"),
    ("older_build_read_as_complete", ROUTES,
     "    wanted = peek is None or peek[\"build\"] != hub_sync.ARCHIVE_DONE or unlisted\n",
     "    wanted = peek is None or peek[\"build\"] not in (\"done\", hub_sync.ARCHIVE_DONE) or unlisted\n",
     "older_kind"),
    ("live_built_ledger_read_as_it_is", ROUTES,
     "    wanted = peek is None or peek[\"build\"] != hub_sync.ARCHIVE_DONE or unlisted\n",
     "    wanted = peek is None or peek[\"build\"] in (\"running\", \"done\") or unlisted\n",
     ["tests/test_s10_c5_review.py::test_an_archived_ledger_short_of_its_captures_says_so_and_offers_to_open",
      "tests/test_archived_chip_build.py::"
      "test_a_run_the_ledger_holds_is_never_added_twice_and_a_live_open_reads_its_whole_folder"]),
    ("running_marker_never_set", SYNC,
     "                store.set_meta(ARCHIVE_BUILD, \"running\")",
     "                pass",
     "cut_short or resume_after_a_restart"),
    ("unreadable_offers_try_again", ROUTES,
     "_VH_FINAL_REASONS = frozenset({\"no_ledger\", \"snapshots_unreadable\"})",
     "_VH_FINAL_REASONS = frozenset({\"no_ledger\"})",
     "unreadable_snapshots_end"),
    ("unreadable_reads_an_empty_ledger", ROUTES,
     "    if peek is not None and peek[\"build\"] == hub_sync.ARCHIVE_DONE and not peek[\"events\"]:",
     "    if False:",
     "unreadable_snapshots_end or cannot_be_listed"),
    ("unlisted_build_retried_on_every_view", ROUTES,
     "    unlisted = empty and not peek[\"looked\"] and hub_sync.registered_for(directory) is None",
     "    unlisted = empty and not peek[\"looked\"]",
     "cannot_be_listed"),
    ("unlisted_build_never_retried", ROUTES,
     "    unlisted = empty and not peek[\"looked\"] and hub_sync.registered_for(directory) is None",
     "    unlisted = False",
     "cannot_be_listed"),
    ("no_ledger_offers_try_again", ROUTES,
     "_VH_FINAL_REASONS = frozenset({\"no_ledger\", \"snapshots_unreadable\"})",
     "_VH_FINAL_REASONS = frozenset({\"snapshots_unreadable\"})",
     "no_ledger_offers_no_try_again"),
    ("picker_shows_the_folder_name", TEMPLATES + "_param_history.html",
     "<strong class=\"archive-display\">{{ chip.display or chip.key }}</strong>",
     "<strong class=\"archive-display\">{{ chip.key }}</strong>",
     "display_name"),
    ("grid_read_failure_reads_busy", ROUTES,
     "    except LedgerUnreadable:\n"
     "        # S10 final review: a failed ledger READ says so (any other error is\n"
     "        # a real one: logged and raised, never \"the index is busy\")\n"
     "        return wait(_hub_table_failed(\"param_history_grid\"))\n",
     "    except Exception as exc:   # noqa: BLE001\n"
     "        logger.warning(\"param-history trend query failed: %s\", exc)\n"
     "        rows = []\n"
     "        index_error = \"The trend index is busy (a save or import may be running). Reload in a moment.\"\n",
     "grid_read_that_fails"),
    ("drawer_offers_no_open", ROUTES,
     "        open_path = (_openable_folder_for_chip(hm, target_path)\n"
     "                     if not is_loaded and hub_answer[\"mode\"] == \"unavailable\" else None)",
     "        open_path = None",
     "unreadable_snapshots_end"),
    ("changes_read_the_open_chip", ROUTES,
     "    hm = _history()\n    archive = _changes_archive(hm)\n",
     "    hm = _history()\n    archive = None\n",
     "shows_its_snapshots_values"),
    ("typeahead_reads_the_open_chip", ROUTES,
     "    archive = _changes_archive(_history())\n    if archive is not None:\n"
     "        # S10 walk: an archived chip's Changes suggest its own ledger's paths",
     "    archive = None\n    if archive is not None:\n"
     "        # S10 walk: an archived chip's Changes suggest its own ledger's paths",
     "shows_its_snapshots_values"),
    ("live_open_keeps_the_archive_marker", SYNC,
     "        elif not self.archive and store.meta(ARCHIVE_BUILD) is not None:",
     "        elif False:",
     "reads_and_never_writes"),
    ("missing_files_passed_over", ROUTES,
     "        if t_us is None:\n            return None\n        return {\"ts\": m.timestamp,",
     "        if t_us is None or not (base / m.timestamp / \"state.json\").is_file():\n"
     "            return None\n        return {\"ts\": m.timestamp,",
     "files_are_gone"),
    ("warming_names_draw_an_empty_grid", ROUTES,
     "    if ctx.get(\"hub_archive_warming\"):\n        out.update(mode=\"preparing\")\n        return out\n",
     "",
     "still_being_prepared"),
    ("built_note_missing", ROUTES,
     "    if peek and peek.get(\"build\"):\n        out.append(_archive_built_note(peek))\n",
     "",
     "shows_its_snapshots_values or files_are_gone"),
    # -- the run captures are the run history (S10 walk follow-up) --
    ("run_captures_left_out", ROUTES,
     "            runs.setdefault(key, []).append((not own, m.timestamp, it, m))",
     "            pass",
     "shows_its_snapshots_values or pre_s10 or all_runs"),
    ("rewritten_capture_left_out", ROUTES,
     "                # save seen a moment early, one the run rewrote since stays\n"
     "                out.append(it)\n",
     "                # save seen a moment early, one the run rewrote since stays\n"
     "                pass\n",
     "saved_again"),
    ("run_folders_not_read", SYNC,
     "        if self.archive and self.archive_setup is not None:\n            self._archive_setup()\n",
     "",
     "pre_s10"),
    ("every_run_of_the_root_read", SYNC,
     "                        rs.only, rs.listed = set(only[k]), False\n",
     "                        rs.listed = False\n",
     "pre_s10 or never_added_twice"),
    ("a_captured_run_reads_as_a_state_sm_saw", SYNC,
     "                    attach = attach_captured_run if snap.get(\"run\") else attach_observed\n",
     "                    attach = attach_observed\n",
     "shows_its_snapshots_values or all_runs"),
    ("a_captured_run_names_its_folder", SYNC,
     "                 state_ref=str(folder), n_changes=len(rows), flags=SOURCE_GONE,",
     "                 state_ref=str(folder), n_changes=len(rows), flags=0,",
     "shows_its_snapshots_values or pre_s10"),
    ("a_run_the_ledger_holds_added_again", SYNC,
     "                 (run.get(\"run_id\"), run.get(\"experiment\"), digest, chash)).fetchone():\n"
     "        return done(\"run_present\")\n",
     "                 (run.get(\"run_id\"), run.get(\"experiment\"), digest, chash)).fetchone():\n"
     "        pass\n",
     "never_added_twice"),
    ("live_open_keeps_the_run_filter", SYNC,
     "        if rs.only is not None:    # every run of a live chip's data folder: list it all again\n",
     "        if False:\n",
     "never_added_twice"),
    ("captured_run_left_out_of_the_live_folder", "quam_state_manager/core/hub_lanes.py",
     "            if not rids and str(f[\"rel_path\"] or \"\").startswith(\"snapshot:\"):\n",
     "            if False:\n",
     "reads_and_never_writes"),
    ("verify_wants_a_folder_for_a_captured_run", SYNC,
     "            if (r[\"src\"] != RUN_CAPTURE_SRC\n                    and not store.conn.execute(",
     "            if (True\n                    and not store.conn.execute(",
     "pre_s10"),
    ("note_leaves_the_folder_runs_out", ROUTES,
     "    n = int(peek.get(\"runs_in_folders\") or 0)\n    if n:\n",
     "    n = int(peek.get(\"runs_in_folders\") or 0)\n    if False:\n",
     "pre_s10"),
]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--only", nargs="*", default=None)
    args = parser.parse_args()
    results = []
    env = dict(os.environ, PYTHONUTF8="1", NODE_PATH="D:/work/statemanager/node_modules")
    env.pop("PYTEST_ADDOPTS", None)
    for name, rel, anchor, replacement, select in MUTATIONS:
        if args.only and name not in args.only:
            continue
        path = ROOT / rel
        original = path.read_bytes()
        source = original.decode("utf-8")
        a, r = anchor, replacement
        if "\r\n" in source:
            # a Windows checkout (core.autocrlf): the anchors are written with "\n"
            a, r = a.replace("\n", "\r\n"), r.replace("\n", "\r\n")
        assert source.count(a) == 1, (name, source.count(a))
        mutated = source.replace(a, r)
        targets = list(select) if isinstance(select, list) else [TEST, "-k", select]
        try:
            path.write_bytes(mutated.encode("utf-8"))
            for _attempt in range(3):
                # the interpreter running this tool runs the pin (start it from the test env)
                proc = subprocess.run([sys.executable, "-m", "pytest", *targets,
                                       "-q", "-p", "no:cacheprovider", "--timeout=900", "--timeout-method=thread"],
                                      cwd=ROOT, env=env, capture_output=True, text=True, encoding="utf-8")
                output = proc.stdout + proc.stderr
                if "__conda_tmp" not in output:
                    break
            killed = proc.returncode == 1 and "AssertionError" in output and "ERROR collecting" not in output
            failed = [ln for ln in output.splitlines() if ln.startswith(("FAILED", "ERROR"))][:8]
            results.append({"mutation": name, "file": rel, "pins": targets, "killed_by_assertion": killed,
                            "exit_code": proc.returncode, "failed": failed,
                            "summary": (output.strip().splitlines() or [""])[-1]})
            args.report.parent.mkdir(parents=True, exist_ok=True)
            args.report.write_text(json.dumps(results, indent=2) + "\n", encoding="utf-8")
            print(f"{name}: {'RED (assertion)' if killed else 'NOT KILLED'} -- {results[-1]['summary']}",
                  flush=True)
        finally:
            path.write_bytes(original)
    if not all(r["killed_by_assertion"] for r in results):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
