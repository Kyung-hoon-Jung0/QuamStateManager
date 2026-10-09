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
