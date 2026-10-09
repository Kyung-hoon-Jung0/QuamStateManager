"""Optional pytest plugin capturing every raised C0 tripwire, including caught ones.

Set PYTEST_ADDOPTS='-p tools.hub_fallback_capture' and HUB_FALLBACK_REPORT to
an output directory. Sequential pytest processes merge their reports there.
"""

from json import dumps as _dumps, loads as _loads
import os
from pathlib import Path


_current = None
_hits = {}
_failures = {}


def pytest_configure(config):
    from quam_state_manager.web import routes
    original = routes._hub_fallback_reached

    def capture(surface, reason):
        try:
            return original(surface, reason)
        except RuntimeError as exc:
            if str(exc).startswith("hub fallback reached:") and _current:
                _hits.setdefault(_current, set()).add(f"{surface} ({reason})")
            raise

    routes._hub_fallback_reached = capture


def pytest_runtest_setup(item):
    global _current
    _current = item.nodeid


def _save(name, additions):
    directory = os.environ.get("HUB_FALLBACK_REPORT")
    if not directory:
        return
    path = Path(directory) / name
    path.parent.mkdir(parents=True, exist_ok=True)
    existing = _loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    for key, values in additions.items():
        existing[key] = sorted(set(existing.get(key, [])) | set(values))
    path.write_text(_dumps(dict(sorted(existing.items())), indent=2) + "\n", encoding="utf-8")


def pytest_runtest_logreport(report):
    if report.failed:
        # Store failure IDs and phases; raw diagnostics remain in the suite logs.
        _failures.setdefault(report.nodeid, set()).add(report.when)
    _save("tripwire_tests.json", _hits)
    _save("failed_tests.json", _failures)


def pytest_sessionfinish(session, exitstatus):
    _save("tripwire_tests.json", _hits)
    _save("failed_tests.json", _failures)
