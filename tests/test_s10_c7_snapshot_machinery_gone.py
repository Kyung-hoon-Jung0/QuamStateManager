"""S10 C7: old -> new, pin the removal of callerless snapshot machinery."""
import ast
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PKG = ROOT / "quam_state_manager"


def test_callerless_snapshot_machinery_stays_absent():
    # Split retired identifiers so this pin leaves no references to them.
    names = [
        "Chip" + "TrendsTable", "_snapshot_" + "provenance_map",
        "_snapshot_" + "provenance_build", "_Writer" + "Check",
        "_trend_run_" + "folder_resolver", "value_" + "writer",
        "vh-" + "fallback", "add_indexed_" + "listener",
        "_fire_" + "indexed", "_indexed_" + "listeners",
        "_indexed_" + "lock", "_indexed_" + "pending",
        "_hub_fallback_" + "reached", "_hub_fallback_" + "counts",
        "_HUB_FALLBACK_" + "REACHED", "_HUB_FALLBACK_" + "WARNED",
        "_HUB_FALLBACK_" + "LOCK", "HUB_FALLBACK_" + "TRIPWIRE",
        "fallback_" + "reached",
        "run_" + "uncertain_chip", "history_" + "compare",
        "/api/history/" + "compare", "_history_" + "compare.html",
        "history-detail-" + "area", "topo-trends-" + "snaps",
        "_capturer" + "Line", "_UID_" + "DEFERRED",
        "_SNAPSHOT_" + "WHY", "index_" + "updating",
        "History index " + "updating",
        "snapshots_" + "cached", "leaf_index_updating_" + "dir",
        "_resolved_key_" + "memo",
    ]
    hits = []
    for p in PKG.rglob("*"):
        if p.suffix not in {".py", ".html", ".js", ".css"} or "vendor" in p.parts or p.name.startswith("plotly"):
            continue
        text = p.read_text(encoding="utf-8")
        hits.extend((p.relative_to(PKG).as_posix(), n) for n in names
                    if re.search(rf"(?<![\w]){re.escape(n)}(?![\w])", text))
    assert hits == [], hits
    js = (PKG / "web/static/chip-status.js").read_text(encoding="utf-8")
    assert not re.search(r"\be\." + "writer" + r"\b|\bctx\." + "snaps" + r"\b|\b_" + "snaps" + r"\b", js)
    assert not (PKG / "core" / ("value_" + "writer.py")).exists()
    for module, retired in {
        "chip_trends_ram": {
            "table", "token", "is_open", "forget", "stats", "close_all", "has_conn",
            "_" + "Base", "_" + "Delta", "_" + "Ident", "_Index" + "Conn",
            "_" + "Marks", "_Read" + "Txn", "_" + "add", "_conn_" + "for",
            "_delta_" + "read", "_family_" + "rows", "_family_rows_" + "by_row",
            "_family_rows_" + "marked", "_family_rows_term_" + "marked",
            "_" + "heads", "_" + "unpack", "_verify_" + "on", "_warm_" + "full",
        },
        "param_history_ram": {"leaf_" + "search", "warm_path_" + "rank", "_path_rank_" + "memo"},
    }.items():
        tree = ast.parse((PKG / "core" / f"{module}.py").read_text(encoding="utf-8"))
        defined = {n.name for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.ClassDef))}
        assert defined.isdisjoint(retired), defined & retired
        assigned = {n.id for statement in tree.body for n in ast.walk(statement)
                    if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Store)
                    and isinstance(statement, (ast.Assign, ast.AnnAssign))}
        retired_slots = ({"COU" + "NTS", "WARM_FU" + "LL_TABLE", "_CO" + "NNS", "_CONN" + "_LOCK", "_CP" + "_SIG",
                          "_DELT" + "A_MAX", "_MAX_" + "CONNS", "_MAX_" + "DEPTH", "_M" + "EMO", "_NO" + "_ID",
                          "_PATH" + "S_SIG", "_SIG" + "_MOD"} if module == "chip_trends_ram" else
                         {"_NAT_" + "ORDER", "_PATH_R" + "ANK_LOCK", "_PATH_R" + "ANK_MEMO", "_WAR" + "MING"})
        assert assigned.isdisjoint(retired_slots), assigned & retired_slots
    from quam_state_manager.web.app import create_app
    from tempfile import TemporaryDirectory
    with TemporaryDirectory() as instance:
        app = create_app(testing=True, instance_path=instance)
        assert not {"kind_" + "for", "snapshot_" + "source"} & app.jinja_env.globals.keys()
        assert app.test_client().get("/api/history/" + "compare").status_code == 404
