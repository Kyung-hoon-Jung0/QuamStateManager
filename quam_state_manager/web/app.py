"""Flask application factory for the QUAM State Manager web dashboard.

Handles ``sys._MEIPASS``-aware resource paths for PyInstaller bundles,
and attaches shared state (Workspace, context registry) to the app context.

The app uses a **multi-context model**: ``app.config["contexts"]`` is a dict
mapping context names to context dicts, each containing a ``type`` key
(e.g. ``"quam"``) and type-specific objects (store, engine, index, etc.).
``app.config["active_context"]`` holds the name of the currently active
context.  This design allows future data types (HDF5, datasets) to plug in
alongside QUAM without restructuring the session model.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import sys
from datetime import timedelta
from pathlib import Path

from flask import Flask, current_app, request
from markupsafe import Markup

from quam_state_manager.core.history import (
    HistoryManager,
    migrate_legacy_histories,
    migrate_legacy_histories_v2,
)
from quam_state_manager.core.scanner import Workspace


# ----------------------------------------------------------------------
# Phase 4 §1 — XSS-safe JSON for inline <script> bodies.
# ----------------------------------------------------------------------

def _script_json_filter(value) -> Markup:
    """Render *value* as JSON safe to embed inside ``<script>...</script>``.

    HTML5's tokeniser terminates a script element on the literal byte
    sequences ``</``, ``<!--``, and ``]]>`` regardless of the
    ``type`` attribute. ``json.dumps()`` does NOT escape any of those
    sequences, so a string value containing one (e.g. a qubit name
    ``"q</script><script>alert(1)//"`` in a researcher-shared
    state.json) breaks out of the script context and the next bytes
    are parsed as HTML — XSS.

    The fix escapes every ``<``, ``>``, ``&`` in the JSON output using
    ``\\u00XX`` escapes that are parse-equivalent (``JSON.parse`` returns
    the original character) but invisible to the HTML tokeniser. See
    OWASP "Output Encoding for JavaScript Contexts".

    Accepts either an already-serialised JSON string (legacy callers
    that pre-`json.dumps`'d the value) or a raw object.
    """
    if not isinstance(value, str):
        value = json.dumps(value, separators=(",", ":"), ensure_ascii=False)
    # Six-character "<" / ">" / "&" escapes are valid
    # JSON string escapes, so ``JSON.parse`` recovers the original
    # characters in the browser; meanwhile the HTML tokeniser sees a
    # literal backslash-u sequence and never closes the <script> body.
    safe = (
        value.replace("<", "\\u003c")
             .replace(">", "\\u003e")
             .replace("&", "\\u0026")
    )
    return Markup(safe)


# ----------------------------------------------------------------------
# Phase 4 §3 — CSRF origin check + security headers.
# ----------------------------------------------------------------------

_SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})


def _csrf_origin_check():
    """Reject cross-origin mutations.

    Same-origin requests from our own pages always carry an ``Origin``
    or ``Referer`` header pointing back at our ``Host``. A malicious
    page elsewhere submitting a form-encoded POST to our localhost
    server gets ``Origin: http://evil.example`` — we drop those with
    403 before any route logic runs.

    Bypassed in ``TESTING`` mode so the Flask test client (which does
    not set Referer by default) keeps working.
    """
    if request.method in _SAFE_METHODS:
        return None
    if current_app.config.get("TESTING"):
        return None
    expected = f"http://{request.host}"
    expected_https = f"https://{request.host}"
    origin = request.headers.get("Origin")
    if origin:
        if origin not in (expected, expected_https):
            return ("CSRF: cross-origin mutation rejected", 403)
        return None
    referer = request.headers.get("Referer", "")
    if referer:
        if not (referer.startswith(expected + "/") or referer == expected
                or referer.startswith(expected_https + "/") or referer == expected_https):
            return ("CSRF: cross-origin referer rejected", 403)
        return None
    # Neither header present: fail closed for state-changing requests
    # (no legitimate browser flow lands here for our routes).
    return ("CSRF: missing Origin/Referer header", 403)


_CSP = (
    "default-src 'self'; "
    "script-src 'self' 'unsafe-inline'; "  # many inline <script> blocks
    "style-src 'self' 'unsafe-inline'; "   # inline style attributes
    "img-src 'self' data:; "
    "connect-src 'self'; "
    "object-src 'none'; "
    "base-uri 'self'; "
    # /workbench (the Qualibrate co-display shell) embeds two iframes: the
    # State Manager's own pages (same-origin) and Qualibrate, which runs on a
    # localhost port that MOVES (8001/8002/…). frame-src must therefore allow
    # 'self' + any localhost port. This widens only what WE may embed; it is a
    # localhost-only tool so framing local ports is benign.
    "frame-src 'self' http://127.0.0.1:* http://localhost:*; "
    # Was 'none'. Relaxed to 'self' so /workbench can embed the State Manager's
    # OWN pages in its right pane. External sites still cannot frame us.
    "frame-ancestors 'self'"
)


def _add_security_headers(resp):
    """Defense-in-depth response headers (Phase 4 §3, extended in Phase 5 §3.1).

    Phase 5 §3.1 extension: stamp ``Cache-Control: no-store`` on every
    HTMX partial response. Without this, the browser caches partials
    like ``/qubits`` and a Back-after-edit can render a stale qubit
    table — the working copy has the user's new value but the cached
    HTML still shows the old one. ``HX-Request: true`` is the canonical
    HTMX header, so we use it to scope the no-store rule to HTMX
    fetches and leave static assets alone.
    """
    resp.headers.setdefault("X-Content-Type-Options", "nosniff")
    resp.headers.setdefault("Referrer-Policy", "same-origin")
    resp.headers.setdefault("Content-Security-Policy", _CSP)
    if request.headers.get("HX-Request") == "true":
        # Don't cache HTMX partials. Set, not setdefault — routes that
        # explicitly opt into caching would have to update after this
        # hook, which currently nobody needs to.
        resp.headers["Cache-Control"] = "no-store"
    return resp


def _resource_path(relative: str) -> str:
    """Resolve a path that works both in dev and in a PyInstaller bundle.

    In dev mode, paths are relative to this file (quam_state_manager/web/).
    In a PyInstaller bundle, data is stored under
    ``sys._MEIPASS / quam_state_manager / web / <relative>``.
    """
    if hasattr(sys, "_MEIPASS"):
        return os.path.join(sys._MEIPASS, "quam_state_manager", "web", relative)
    return os.path.join(os.path.dirname(__file__), relative)


def _purge_test_leftovers(instance_path: str) -> None:
    """Drop legacy test-tmp leftovers from the user's instance/ folder.

    Cleans up three forms of pollution that older un-isolated test fixtures
    could leave behind:

    1. ``instance/history/pytest-NN/`` and ``instance/history/Temp/`` — entire
       chip-history dirs that pytest's tmp_path machinery leaked.
    2. Paths under the OS tempdir (``$TEMP``) inside ``workspace_roots.json``
       — they refer to vanished pytest tmp dirs.
    3. Paths under ``$TEMP`` inside ``last_session.json`` (both
       ``last_quam_state_path`` and ``recent_quam_state_paths``).

    Idempotent and defensive: only paths under the system tempdir are
    affected, never a real research data path.
    """
    inst = Path(instance_path)

    # 1. History dirs
    hist_root = inst / "history"
    if hist_root.exists():
        pat = re.compile(r"^pytest-\d+$")
        for d in hist_root.iterdir():
            if d.is_dir() and (pat.match(d.name) or d.name == "Temp"):
                shutil.rmtree(d, ignore_errors=True)

    import json as _json
    import tempfile as _tempfile
    tmp_root = Path(_tempfile.gettempdir()).resolve()

    def _is_under_tempdir(p: str) -> bool:
        try:
            rp = Path(p).resolve()
            return tmp_root in rp.parents or rp == tmp_root
        except Exception:
            return False

    # 2. workspace_roots.json
    wrf = inst / "workspace_roots.json"
    if wrf.exists():
        try:
            roots = _json.loads(wrf.read_text(encoding="utf-8"))
            clean = [r for r in roots if not _is_under_tempdir(r)]
            if clean != roots:
                wrf.write_text(_json.dumps(clean), encoding="utf-8")
        except Exception:
            pass

    # 3. last_session.json
    sf = inst / "last_session.json"
    if sf.exists():
        try:
            data = _json.loads(sf.read_text(encoding="utf-8"))
            changed = False
            last = data.get("last_quam_state_path")
            if last and _is_under_tempdir(last):
                data["last_quam_state_path"] = None
                changed = True
            recents = data.get("recent_quam_state_paths", [])
            clean_recents = [p for p in recents if not _is_under_tempdir(p)]
            if clean_recents != recents:
                data["recent_quam_state_paths"] = clean_recents
                changed = True
            if changed:
                sf.write_text(_json.dumps(data, indent=2), encoding="utf-8")
        except Exception:
            pass


def create_app(*, testing: bool = False, instance_path: str | None = None) -> Flask:
    """Create and configure the Flask application.

    Args:
        testing: If True, enables testing mode (in-memory, no file dialogs).
        instance_path: Override Flask's instance_path. Test fixtures should
            pass a tmp directory here so test runs don't pollute the user's
            real ``instance/``. If ``testing=True`` is set *without* an
            explicit ``instance_path``, an OS tmp dir is auto-allocated as
            a defensive isolation default.
    """
    template_dir = _resource_path("templates")
    static_dir = _resource_path("static")

    if testing and instance_path is None:
        # Defensive: never let testing=True share state with the user's real
        # instance/ folder. Auto-allocate a one-shot tmp instance dir.
        import tempfile
        instance_path = tempfile.mkdtemp(prefix="quam_test_instance_")

    flask_kwargs: dict = {
        "template_folder": template_dir,
        "static_folder": static_dir,
    }
    if instance_path is not None:
        flask_kwargs["instance_path"] = str(Path(instance_path).resolve())
    else:
        flask_kwargs["instance_relative_config"] = True

    app = Flask(__name__, **flask_kwargs)
    os.makedirs(app.instance_path, exist_ok=True)
    app.config["TESTING"] = testing
    app.config["SECRET_KEY"] = os.urandom(24).hex()

    # Phase 4 §1 — register the XSS-safe JSON filter as `script_json`,
    # used by every template that embeds a JSON payload inside a
    # <script> body. Replaces the older `| safe` filter on those sites.
    app.jinja_env.filters["script_json"] = _script_json_filter

    # `qty` — physical-unit display filter (single source of truth in
    # core/units.py). Converts raw stored SI values to fixed human units per
    # field (T1->µs, f_01->GHz, …). mode 'num' (default) emits the scaled
    # number only; 'full' emits "<num> <unit>"; 'unit' the label only.
    from quam_state_manager.core import units as _units
    app.jinja_env.filters["qty"] = _units.qty_filter
    # `unit_hint` — the SI base unit a field is *stored* in (s / Hz / ns / V),
    # for "you are editing raw <unit>" hints on inspector edit inputs. '' when
    # the field has no known unit.
    app.jinja_env.filters["unit_hint"] = _units.stored_unit_label
    # `groupdigits` — LOSSLESS full-digit + thousands-comma (no GHz scaling, no
    # precision-losing e-notation). The canonical editable/displayed form for raw
    # numbers across Bulk Edit, the inspector and the diff/review surfaces.
    app.jinja_env.filters["groupdigits"] = _units.group_digits

    def _format_ts_filter(ts) -> str:
        """Render a ``YYYYMMDD_HHMMSS`` snapshot timestamp as
        ``YYYY-MM-DD HH:MM:SS UTC``. Robust: falls back to the raw string when it
        doesn't match (never the old slice-based '::' garbage), and includes the
        date so snapshots taken days apart are distinguishable."""
        import re
        if not isinstance(ts, str):
            return str(ts)
        m = re.match(r"^(\d{4})(\d{2})(\d{2})[_\- ]?(\d{2})(\d{2})(\d{2})", ts)
        if not m:
            return ts
        y, mo, d, h, mi, s = m.groups()
        return f"{y}-{mo}-{d} {h}:{mi}:{s} UTC"
    app.jinja_env.filters["format_ts"] = _format_ts_filter

    def _ts_local_filter(ts):
        """Render a snapshot/ISO timestamp as a CLIENT-LOCALIZABLE span (feedback C2:
        users are worldwide; UTC isn't friendly). The body is the UTC fallback (graceful
        with JS off); ``data-utc`` carries a strict ISO-8601 Z instant that app.js's
        applyLocalTimes() converts to each user's local time. Use at DISPLAY sites;
        ATTRIBUTE sites (hx-confirm/title) keep ``format_ts`` plain text — a span there
        would corrupt the attribute."""
        from markupsafe import Markup, escape
        import re
        if not isinstance(ts, str):
            ts = str(ts)
        m = re.match(r"^(\d{4})(\d{2})(\d{2})[_\- ]?(\d{2})(\d{2})(\d{2})", ts)
        if m:
            y, mo, d, h, mi, s = m.groups()
            iso = f"{y}-{mo}-{d}T{h}:{mi}:{s}Z"
            fallback = f"{y}-{mo}-{d} {h}:{mi}:{s} UTC"
        else:
            iso_m = re.match(r"^(\d{4}-\d{2}-\d{2})[T ](\d{2}:\d{2}:\d{2})", ts)
            if iso_m:
                iso = f"{iso_m.group(1)}T{iso_m.group(2)}Z"
                fallback = f"{iso_m.group(1)} {iso_m.group(2)} UTC"
            else:
                return Markup(f'<span class="ts-local">{escape(ts)}</span>')
        return Markup(
            f'<span class="ts-local" data-utc="{escape(iso)}">{escape(fallback)}</span>')
    app.jinja_env.filters["ts_local"] = _ts_local_filter

    # Long-cache static assets (they're fingerprinted by asset_url below, so a
    # stale copy can't linger past an edit). HTMX partials stay no-store via
    # _add_security_headers; this only affects /static/*.
    app.config["SEND_FILE_MAX_AGE_DEFAULT"] = timedelta(days=365)

    # `asset_url(filename)` — like url_for('static', ...) but appends ?v=<mtime>
    # so editing a JS/CSS file changes its URL and busts the year-long cache,
    # with no build/hashing step. Falls back to the plain URL if stat fails.
    def _asset_url(filename: str) -> str:
        from flask import url_for
        try:
            mtime = int(os.path.getmtime(os.path.join(static_dir, filename)))
            return url_for("static", filename=filename, v=mtime)
        except OSError:
            return url_for("static", filename=filename)

    app.jinja_env.globals["asset_url"] = _asset_url

    # Diagnostics-list grouping: map a Finding category → display domain, and the
    # ordered domain list. Single source of truth in core.diagnostics, so every
    # surface that includes _diagnostics_list.html groups identically.
    from quam_state_manager.core import diagnostics as _diagnostics
    app.jinja_env.filters["diag_domain"] = _diagnostics.domain_of
    app.jinja_env.globals["diag_domains"] = _diagnostics.DIAG_DOMAINS

    # Phase 4 §3 — register CSRF origin check + defense-in-depth
    # response headers. Both are wired at the app level (not the
    # blueprint) so every route, including any future blueprints, is
    # covered.
    app.before_request(_csrf_origin_check)
    app.after_request(_add_security_headers)

    # One-time housekeeping: remove ``pytest-*`` / ``Temp`` history dirs leaked
    # by older un-isolated test runs. Cheap and idempotent.
    _purge_test_leftovers(app.instance_path)

    # One-time legacy-history migrations: both gated by their own flag
    # files so each runs at most once on this instance.
    #   v1: per-experiment-keyed → chip-named (uses meta source_path)
    #   v2: fingerprint-based correction (re-routes snapshots whose v1
    #       attribution was poisoned by the backfill source_path bug)
    try:
        migrate_legacy_histories(app.instance_path)
    except Exception:
        import logging
        logging.getLogger(__name__).warning(
            "Legacy v1 migration failed", exc_info=True,
        )
    try:
        migrate_legacy_histories_v2(app.instance_path)
    except Exception:
        import logging
        logging.getLogger(__name__).warning(
            "Legacy v2 migration failed", exc_info=True,
        )

    app.config["workspace"] = Workspace()
    app.config["history_manager"] = HistoryManager(app.instance_path)
    app.config["contexts"] = {}
    app.config["active_context"] = None

    from quam_state_manager.web.routes import bp
    app.register_blueprint(bp)

    return app
