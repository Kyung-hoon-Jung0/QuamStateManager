"""Shared ledger setup for surfaces that previously read snapshot fallbacks."""
from pathlib import Path


def declare_root(client, root):
    """Declare a synthetic data root and project it before a surface read."""
    from quam_state_manager.web import routes
    app = client.application
    with app.app_context():
        ctx = routes._active_ctx()
        ctx["extras_data_roots"] = [str(Path(root).resolve())]
        app.config["HUB_SYNC_ON_OPEN"] = True
        routes._hub_sync_open(ctx)
        return Path(ctx["hub_chip_dir"])


# S10 C4: snapshot field reader -> real ledger routes, retain folder isolation.
def observed_view(hm, live, dot_path):
    """Project recorded folders and read the requested folder's ledger drawer."""
    from quam_state_manager.core import hub
    from quam_state_manager.web import routes
    from quam_state_manager.web.app import create_app
    old = hub._PROJECTOR.inline
    hub.set_inline(True)
    try:
        app = create_app(testing=True, instance_path=str(hm._root.parent))
        client = app.test_client()
        folders = dict.fromkeys(m.source_path for m in reversed(hm.list_snapshots(live))
                               if m.source_path and Path(m.source_path).is_dir()
                               and m.trigger != "experiment")
        for folder in [*folders, str(live)]:
            assert client.post("/load", data={"folder": folder}).status_code in (200, 302)
        with app.test_request_context():
            ans = routes._value_history(routes._active_ctx(), {"value": dot_path})
            assert ans["mode"] == "ledger", ans
            return routes._vh_drawer_view(ans, "value", dot_path)
    finally:
        hub.set_inline(old)
