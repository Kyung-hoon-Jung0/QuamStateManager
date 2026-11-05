import json
import subprocess
from pathlib import Path
from tests.test_autosync_merge import env, _arm, _state
from tests.test_autosync_lost_applied import _apply_name, _outside_save_and_pull


def test_one_server_notice_reaches_response_and_peer_poll(env):
    _arm(env, pull=True, push=True)
    _apply_name(env)
    response = _outside_save_and_pull(env, _state())
    notice = json.loads(response.headers["HX-Trigger"])["autoSyncRestored"]
    assert notice["message"] == "An outside save reverted 1 value you applied; Auto-Sync recovered it."
    assert env["client"].get("/state/drift").get_json()["auto_restore_notice"] == notice


def test_all_windows_display_server_words():
    run = subprocess.run(["node", str(Path(__file__).with_name("leftover_minors_notice_selfcheck.cjs"))],
                         capture_output=True, text=True, encoding="utf-8", timeout=30)
    assert run.returncode == 0, run.stdout + run.stderr
