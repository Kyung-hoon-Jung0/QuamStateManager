# S10 C7: old -> new, share the live surface matrix without instrumentation.
from tests.ledger_fixture import no_runs  # noqa: F401
SURFACES = [
    ("drawer", "/field/history?path=qubits.qA1.T1"),
    ("trends", "/topology/trends?metrics=T1"),
    ("versions", "/state/versions"),
    ("state_history", "/state-history?body=1"),
    ("agent_field_history", "/api/agent/field-history?path=qubits.qA1.T1"),
    ("trends_paths", "/topology/trends/paths?q=T1"),
    ("metric_meta", "/topology/metric-meta"),
    ("param_history_grid", "/param-history?since=all&props=T1"),
    ("param_history_expand", "/param-history/expand?qubit=qA1&prop=T1"),
    ("changes", "/param-history/changes"),
    ("changes_paths", "/param-history/param-search?q=T1"),
    ("sparklines", "/api/topology/sparklines/qA1"),
    ("history_drawer", "/api/history"),
    ("version_count", "/state/version"),
]
