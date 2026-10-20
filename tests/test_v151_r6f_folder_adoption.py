"""One root registration failure cannot hide the complete folder answer."""
from types import SimpleNamespace
import pytest
from flask import Flask
from quam_state_manager.core.loader import QuamStore
from quam_state_manager.web import routes


@pytest.mark.parametrize("failure", [OSError, ValueError])
def test_failed_root_stays_dangling_while_other_roots_publish(tmp_path, monkeypatch, caplog, failure):
    roots = {raw: tmp_path / raw for raw in ("existing", "blocked", "ready")}
    for path in roots.values():
        path.mkdir()
    declared = ["existing", "blocked", "ready", "absent"]
    ctx = {"type": "quam", "origin": "live", "qualibrate_project": "scope",
           "store": QuamStore.from_dicts({"extras": {"data_folder": declared}}, {}),
           "extras_data_roots": [], "extras_data_dangling": ["old"]}
    attempts, saved, recorded = [], [], []
    def add_root(root, **kwargs):
        attempts.append(root)
        if root == str(roots["blocked"]):
            raise failure("registration probe detail")
    ws = SimpleNamespace(root_folders=[roots["existing"]], add_root=add_root)
    monkeypatch.setattr(routes, "_ws", lambda: ws)
    monkeypatch.setattr(routes, "_extras_native", lambda raw: str(roots.get(raw, tmp_path / raw)))
    monkeypatch.setattr(routes, "_save_workspace_roots", lambda: saved.append(True))
    monkeypatch.setattr(routes, "_record_project_roots", lambda scope, roots, **kw: recorded.append(roots))
    app = Flask(__name__)
    app.config["dataset_store"] = object()
    with app.app_context(), caplog.at_level("WARNING"):
        routes._adopt_extras_data_folders_locked(ctx)
    assert attempts == [str(roots["blocked"]), str(roots["ready"])], attempts
    assert ctx["extras_data_declared"] == declared
    assert ctx["extras_data_roots"] == [str(roots["existing"]), str(roots["ready"])]
    assert set(ctx["extras_data_dangling"]) == {"blocked", "absent"}
    assert saved == [True] and "dataset_store" not in app.config
    assert recorded == [[str(roots["existing"]), str(roots["ready"])]]
    assert "registration probe detail" in caplog.text and str(roots["blocked"]) in caplog.text
