"""D-7: machine-wide persisted setting, with a mutation of read-back."""
import json
import os
import subprocess
from pathlib import Path
from quam_state_manager.web.app import create_app
from quam_state_manager.web import routes


def test_tree_setting(tmp_path, monkeypatch):
    app = create_app(testing=True, instance_path=str(tmp_path / "instance"))
    client = app.test_client()
    assert client.get("/settings/tree-edit").json["commit_on_leave"] is True
    assert client.post("/settings/tree-edit", data={"commit_on_leave": "0"}).json["ok"]
    path = Path(app.instance_path) / "tree_edit.json"
    assert json.loads(path.read_text())["commit_on_leave"] is False
    if os.environ.get("TREE_SETTING_MUTATION"):
        monkeypatch.setattr(routes, "_tree_commit_on_leave", lambda: True)
    assert client.get("/settings/tree-edit").json["commit_on_leave"] is False
    assert "window.__treeCommitOnLeave = false;" in client.get("/ui-kit").text
    assert client.post("/settings/tree-edit", data={"commit_on_leave": "bad"}).status_code == 400
    assert client.get("/settings/tree-edit").json["commit_on_leave"] is False
    assert client.post("/settings/tree-edit", data={"commit_on_leave": "1"}).json["commit_on_leave"] is True
    path.write_text("bad json")
    assert client.get("/settings/tree-edit").json["commit_on_leave"] is True


def test_setting_mutation():
    import sys
    env = dict(os.environ, TREE_SETTING_MUTATION="1", PYTHONUTF8="1")
    result = subprocess.run([sys.executable, "-m", "pytest", __file__+"::test_tree_setting", "-q", "-p", "no:cacheprovider"],
                            env=env, capture_output=True, text=True, encoding="utf-8", timeout=90)
    assert result.returncode == 1 and "AssertionError" in result.stdout, result.stdout + result.stderr
