"""QUAlibrate's web UI served through SM, with an "Open in QSM" link on each run.

The QUAlibrate frontend loads every asset and calls every API by a RELATIVE
url and routes by the url hash, so it runs unchanged under a sub-path. SM
forwards ``/qualibrate-ui/<path>`` to the QUAlibrate server and inserts one
script tag into the root document; that script adds the links and sends the
app's WebSockets straight to QUAlibrate (waitress cannot upgrade a socket).

Nothing of QUAlibrate is modified: no package file, no config file.
Measured against QUAlibrate 1.5.1 and 2.0.0 (both work; see qb-proxy.js for
the two page layouts).

Security rules (each is pinned by tests/test_qualibrate_proxy.py):
- the target is chosen on the server only (its environment, or an address saved
  from SM's own UI after it answered as QUAlibrate), never by a request;
- the target is http on a loopback host unless the operator opts in;
- only listed request/response headers cross; SM's cookies never leave;
- the run folder is resolved server side and must sit inside the storage
  location QUAlibrate reports.
"""
from __future__ import annotations

import html as _html
import http.client
import ipaddress
import json
import logging
import os
import re
import secrets
import threading
from pathlib import Path, PurePosixPath, PureWindowsPath
from urllib.parse import quote, urlsplit

from flask import Blueprint, Response, redirect, render_template, request, url_for

logger = logging.getLogger(__name__)

qbproxy_bp = Blueprint("qbproxy", __name__)

PREFIX = "/qualibrate-ui/"
TARGET_ENV = "QUALIBRATE_URL"
REMOTE_ENV = "SM_QUALIBRATE_PROXY_REMOTE"
DEFAULT_TARGET = "http://127.0.0.1:8001"
SETTINGS_FILE = "qualibrate_proxy.json"
MAX_BODY = 32 * 1024 * 1024
CHUNK = 64 * 1024
CONNECT_TIMEOUT_S = 3.0
READ_TIMEOUT_S = 120.0

_REQ_HEADERS = ("Accept", "Accept-Language", "Content-Type", "Authorization",
                "Cache-Control", "If-None-Match", "If-Modified-Since", "Range")
_RESP_HEADERS = ("Content-Type", "Cache-Control", "ETag", "Last-Modified",
                 "Content-Disposition", "Content-Range", "Accept-Ranges")
#: D-19: the secret only the proxied page knows. It is written into the root
#: document (which no other origin can read: that answer never carries CORS)
#: and the page's own fetch/XHR calls send it back. A request with the opaque
#: origin "null" -- which ANY sandboxed or data: page on the web also sends --
#: is served only with it, except a GET of a static asset (fonts load with CORS
#: and cannot carry a header).
TOKEN_HEADER = "X-QSM-Proxy-Token"
_TOKEN = secrets.token_urlsafe(32)
_STATIC_EXT = re.compile(r"\.(?:js|css|woff2?|ttf|otf|eot|svg|png|jpe?g|gif|ico|webp|map)$", re.I)


def null_origin_allowed(req) -> bool:
    """May a request with ``Origin: null`` reach the proxy? (D-19)"""
    if not req.path.startswith(PREFIX):
        return False
    if secrets.compare_digest(req.headers.get(TOKEN_HEADER, ""), _TOKEN):
        return True
    return (req.method in ("GET", "HEAD") and _STATIC_EXT.search(req.path) is not None
            and "/api/" not in req.path and "/execution/" not in req.path)


#: D-17: QUAlibrate answers must never hold every SM worker thread.
MAX_INFLIGHT = 6
_SLOTS = threading.BoundedSemaphore(MAX_INFLIGHT)
_METHODS = ["GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"]


class TargetError(ValueError):
    """The configured QUAlibrate address cannot be used."""


def _is_loopback(host: str) -> bool:
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _settings_path() -> Path | None:
    """``instance/qualibrate_proxy.json`` (Q-1), or None outside an app."""
    try:
        from flask import current_app
        return Path(current_app.instance_path) / SETTINGS_FILE
    except RuntimeError:
        return None


def _saved_url() -> str | None:
    p = _settings_path()
    if p is None:
        return None
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    url = data.get("url") if isinstance(data, dict) else None
    return url.strip() if isinstance(url, str) and url.strip() else None


def target_url() -> tuple[str, str]:
    """(address, source): the environment wins, then the address saved from
    SM's own UI, then QUAlibrate's default (D-20)."""
    env = (os.environ.get(TARGET_ENV) or "").strip()
    if env:
        return env, "environment"
    saved = _saved_url()
    if saved:
        return saved, "saved"
    return DEFAULT_TARGET, "default"


def parse_target(raw: str, name: str = TARGET_ENV) -> tuple[str, int]:
    """(host, port) of an http:// loopback address; TargetError otherwise."""
    raw = (raw or "").strip()
    parts = urlsplit(raw)
    if parts.scheme != "http":
        raise TargetError(f"{name} must be an http:// address, got {raw!r}.")
    host = (parts.hostname or "").lower()
    if not host or parts.username or parts.password:
        raise TargetError(f"{name} has no usable host: {raw!r}.")
    try:
        port = parts.port or 80
    except ValueError:
        raise TargetError(f"{name} has a bad port: {raw!r}.") from None
    if not _is_loopback(host) and os.environ.get(REMOTE_ENV) != "1":
        raise TargetError(f"{name} points at {host}, which is not this computer. "
                          f"Set {REMOTE_ENV}=1 to allow a QUAlibrate on another host.")
    return host, port


def target() -> tuple[str, int]:
    """(host, port) of the QUAlibrate server, chosen by the server only (D-2)."""
    raw, source = target_url()
    name = TARGET_ENV if source == "environment" else "The QUAlibrate address"
    return parse_target(raw, name)


def _netloc(host: str, port: int) -> str:
    h = f"[{host}]" if ":" in host else host
    return f"{h}:{port}"


def _status(message: str, code: int):
    return render_template("_status.html", message=message, level="warning"), code


def _upstream_path(sub: str) -> str:
    # The path is re-quoted from its decoded form, so CR/LF or a raw space can
    # never reach the request line; the host is fixed by the connection.
    path = "/" + quote(sub, safe="/:@!$&'()*+,;=-._~")
    qs = request.query_string.decode("latin-1")
    if qs:
        if any(c in qs for c in "\r\n \x00"):
            raise TargetError("bad query string")
        path += "?" + qs
    return path


def _inject(html: bytes, host: str, port: int) -> bytes:
    """Insert the one script tag right after <head>; no <head> -> unchanged."""
    m = re.search(rb"<head(\s[^>]*)?>", html, flags=re.IGNORECASE)
    if not m:
        return html
    ws = _html.escape(f"ws://{_netloc(host, port)}/", quote=True)
    tag = (f'<script src="/static/qb-proxy.js" data-ws="{ws}" data-token="{_TOKEN}" '
           f'data-prefix="{PREFIX}"></script>'
           '<link rel="stylesheet" href="/static/qb-proxy.css">').encode()
    return html[:m.end()] + tag + html[m.end():]


def page_csp(host: str, port: int) -> str:
    ws = f"ws://{_netloc(host, port)}"
    return ("default-src 'self'; "
            "script-src 'self' 'unsafe-inline'; "
            "style-src 'self' 'unsafe-inline'; "
            "img-src 'self' data: blob:; "
            "font-src 'self' data:; "
            f"connect-src 'self' {ws}; "
            "frame-src 'self' data:; "
            "worker-src 'self' blob:; "
            "object-src 'none'; "
            "base-uri 'self'; "
            "frame-ancestors 'self'; "
            # D-16: an opaque origin, so QUAlibrate's page cannot act as SM
            "sandbox allow-scripts allow-forms allow-popups "
            "allow-popups-to-escape-sandbox allow-downloads allow-modals")


def _cors(resp):
    """The sandboxed page (D-16) has the opaque origin "null"; only that origin
    may read proxy answers cross-origin. SM's other routes never say this."""
    if request.headers.get("Origin") == "null":
        resp.headers["Access-Control-Allow-Origin"] = "null"
        resp.headers["Access-Control-Allow-Credentials"] = "true"
        resp.headers["Vary"] = "Origin"
    return resp


def _open(method: str, path: str, body: bytes | None, headers: dict):
    host, port = target()
    conn = http.client.HTTPConnection(host, port, timeout=CONNECT_TIMEOUT_S)
    conn.connect()
    conn.sock.settimeout(READ_TIMEOUT_S)
    conn.request(method, path, body=body, headers=headers)
    return conn, conn.getresponse()


@qbproxy_bp.route("/qualibrate-ui")
def proxy_root_redirect():
    # relative urls in QUAlibrate's page need the trailing slash
    return redirect(PREFIX, code=308)


@qbproxy_bp.route(PREFIX, defaults={"sub": ""}, methods=_METHODS)
@qbproxy_bp.route(PREFIX + "<path:sub>", methods=_METHODS)
def proxy(sub: str):
    if request.method == "OPTIONS" and request.headers.get("Origin") == "null":
        r = Response(status=204)
        r.headers["Access-Control-Allow-Methods"] = ", ".join(_METHODS)
        # echo the asked names (only header NAMES; what is forwarded is still
        # the D-4 allowlist); QUAlibrate's own fetch sends odd ones
        asked = request.headers.get("Access-Control-Request-Headers", "")
        names = [h.strip() for h in asked.split(",") if re.fullmatch(r"[A-Za-z0-9-]{1,64}", h.strip())]
        r.headers["Access-Control-Allow-Headers"] = ", ".join(names[:20])
        r.headers["Access-Control-Max-Age"] = "600"
        return _cors(r)
    if request.headers.get("Origin") == "null" and not null_origin_allowed(request):
        return _status("Refused: this request did not come from QUAlibrate's page in SM.", 403)
    try:
        host, port = target()
        path = _upstream_path(sub)
    except TargetError as e:
        return _status(str(e), 400)
    length = request.content_length or 0
    if length > MAX_BODY:
        return _status("Request body too large for the QUAlibrate proxy.", 413)
    body = request.get_data(cache=False) if request.method not in ("GET", "HEAD") else None
    if body is not None and len(body) > MAX_BODY:
        return _status("Request body too large for the QUAlibrate proxy.", 413)
    headers = {k: v for k in _REQ_HEADERS if (v := request.headers.get(k)) is not None}
    headers["Host"] = _netloc(host, port)
    headers["Accept-Encoding"] = "identity"
    if not _SLOTS.acquire(timeout=10):
        return _status("QUAlibrate is busy answering other requests; try again.", 503)
    try:
        conn, resp = _open(request.method, path, body, headers)
    except (OSError, http.client.HTTPException) as e:
        _SLOTS.release()
        return _status(f"QUAlibrate is not answering at http://{_netloc(host, port)} ({e}). "
                       f"Start it, or set its address in the Sync-Qualibrate bar.", 502)

    out = {k: v for k in _RESP_HEADERS if (v := resp.getheader(k)) is not None}
    loc = resp.getheader("Location")
    if loc:
        p = urlsplit(loc)
        if not p.netloc or p.netloc.lower() == _netloc(host, port):
            if p.path.startswith("/"):
                rel = p.path[1:] + (("?" + p.query) if p.query else "")
                out["Location"] = PREFIX + rel
            elif not p.netloc:
                out["Location"] = loc
    ctype = (resp.getheader("Content-Type") or "").lower()
    is_root = sub in ("", "index.html")
    encoded = (resp.getheader("Content-Encoding") or "identity").lower() != "identity"
    if encoded:
        out["Content-Encoding"] = resp.getheader("Content-Encoding")
    if (is_root and ctype.startswith("text/html") and request.method == "GET"
            and resp.status == 200 and not encoded):
        try:
            data = resp.read(8 * 1024 * 1024)
        finally:
            conn.close()
            _SLOTS.release()
        for k in ("ETag", "Last-Modified", "Accept-Ranges"):
            out.pop(k, None)                      # the page is no longer QUAlibrate's bytes
        r = Response(_inject(data, host, port), status=resp.status, headers=out)
        r.headers["Content-Security-Policy"] = page_csp(host, port)
        r.headers["Cache-Control"] = "no-store"
        return r                                  # never CORS: it carries the token

    done = []

    def cleanup():
        # runs once, whether the body was read, cut off, or never started
        if not done:
            done.append(1)
            conn.close()
            _SLOTS.release()

    def stream():
        try:
            while True:
                chunk = resp.read(CHUNK)
                if not chunk:
                    break
                yield chunk
        finally:
            cleanup()

    r = Response(stream(), status=resp.status, headers=out, direct_passthrough=True)
    r.call_on_close(cleanup)
    # every answer, not only HTML: an SVG or any document opened at this path
    # must render sandboxed too, never with SM's origin
    r.headers["Content-Security-Policy"] = page_csp(host, port)
    return _cors(r)


# ------------------------------------------------------------------ run link

def _api_json(path: str):
    host, port = target()
    conn, resp = _open("GET", path, None, {"Host": _netloc(host, port),
                                            "Accept": "application/json"})
    try:
        if resp.status != 200:
            raise LookupError(f"{path} answered {resp.status}")
        return json.loads(resp.read(4 * 1024 * 1024).decode("utf-8"))
    finally:
        conn.close()


def _relative_parts(rel: str) -> list[str] | None:
    """The run folder's path relative to the storage location, as parts.

    Refuses anything absolute or climbing out (``..``) in either spelling."""
    if not isinstance(rel, str) or not rel.strip():
        return None
    for flavour in (PureWindowsPath, PurePosixPath):
        p = flavour(rel)
        if p.is_absolute() or p.anchor or ".." in p.parts:
            return None
    parts = [x for x in re.split(r"[\\/]+", rel) if x not in ("", ".")]
    return parts or None


def _run_folder_ok(folder: Path, root: Path, run_id: int) -> bool:
    try:
        f, r = folder.resolve(), root.resolve()
    except OSError:
        return False
    if f != r and r not in f.parents:
        return False
    return f.is_dir() and re.match(rf"#{run_id}_", f.name) is not None


def _active_location() -> Path | None:
    try:
        active = _api_json("/api/project/active")
        projects = _api_json("/api/projects/")
    except (OSError, LookupError, ValueError, http.client.HTTPException):
        return None
    if not isinstance(projects, list):
        return None
    named = [p for p in projects if isinstance(p, dict)]
    if isinstance(active, str):
        named = [p for p in named if p.get("name") == active]
    elif len(named) != 1:
        return None
    for p in named:
        loc = (((p.get("config") or {}).get("qualibrate") or {}).get("storage") or {}).get("location")
        if isinstance(loc, str) and loc.strip():
            return Path(loc)
    return None


@qbproxy_bp.route("/qualibrate-run/<int:run_id>")
def open_run(run_id: int):
    """Resolve a QUAlibrate run id to its folder, then open it in SM."""
    try:
        host, port = target()
        rel = _api_json(f"/api/data_file/{run_id}/")
    except TargetError as e:
        return _status(str(e), 400)
    except (OSError, http.client.HTTPException) as e:
        return _status(f"QUAlibrate is not answering at http://{_netloc(host, port)} ({e}).", 502)
    except (LookupError, ValueError):
        return _status(f"QUAlibrate has no folder for run #{run_id}.", 404)
    parts = _relative_parts(rel)
    if parts is None:
        return _status(f"QUAlibrate gave run #{run_id} an unusable folder ({str(rel)[:200]!r}).", 404)

    def to_by_path(folder: Path):
        return redirect(url_for("main.dataset_by_path", path=str(folder.resolve())))

    loc = _active_location()
    if loc is not None:
        folder = loc.joinpath(*parts)
        if _run_folder_ok(folder, loc, run_id):
            return to_by_path(folder)
    from quam_state_manager.web.routes import _active_dataset_stores
    hits = []
    for entry in _active_dataset_stores(fast=True, rescan=False):
        root = Path(entry["path"])
        folder = root.joinpath(*parts)
        if _run_folder_ok(folder, root, run_id):
            hits.append(folder.resolve())
    hits = sorted(set(hits))
    if len(hits) == 1:
        return to_by_path(hits[0])
    shown = "/".join(parts)
    if hits:
        return _status(f"Run #{run_id} ({shown}) exists in more than one loaded data folder: "
                       + "; ".join(str(h) for h in hits)
                       + ". Open QUAlibrate's active project folder in SM to choose.", 409)
    where = f" under {loc}" if loc is not None else ""
    return _status(f"Run #{run_id} ({shown}) was not found{where} or in any loaded data "
                   f"folder. Add the data folder that holds it in Datasets, then retry.", 404)


# ------------------------------------------------------------ the address (Q-1)

def probe(host: str, port: int) -> tuple[str | None, str | None]:
    """(version, None) when QUAlibrate answers ``/execution/meta`` at
    host:port, else (None, reason). 1.5.1 and 2.0 both answer
    ``{"version": "..."}`` there."""
    try:
        conn = http.client.HTTPConnection(host, port, timeout=CONNECT_TIMEOUT_S)
        try:
            conn.request("GET", "/execution/meta", headers={"Host": _netloc(host, port),
                                                             "Accept": "application/json"})
            resp = conn.getresponse()
            body = resp.read(64 * 1024)
        finally:
            conn.close()
    except (ConnectionRefusedError, TimeoutError):
        return None, f"nothing answered at http://{_netloc(host, port)}"
    except (OSError, http.client.HTTPException) as e:
        return None, f"nothing answered at http://{_netloc(host, port)} ({e})"
    if resp.status != 200:
        return None, f"http://{_netloc(host, port)}/execution/meta answered {resp.status}; is it QUAlibrate?"
    try:
        data = json.loads(body.decode("utf-8"))
    except ValueError:
        data = None
    v = data.get("version") if isinstance(data, dict) else None
    if not isinstance(v, str) or not v:
        return None, f"http://{_netloc(host, port)} answered, but not as QUAlibrate does"
    return v[:40], None


def _target_status() -> dict:
    raw, source = target_url()
    out = {"url": raw, "source": source, "env": TARGET_ENV, "ok": False,
           "version": None, "reason": None}
    try:
        host, port = target()
    except TargetError as e:
        out["reason"] = str(e)
        return out
    version, why = probe(host, port)
    out.update(ok=version is not None, version=version, reason=why)
    return out


@qbproxy_bp.route("/qualibrate-ui-target", methods=["GET"])
def proxy_target_get():
    """Which QUAlibrate SM serves at /qualibrate-ui/, where that address came
    from, and whether it answers (the workbench's self-check, D-21)."""
    from flask import jsonify
    r = jsonify(_target_status())
    r.headers["Cache-Control"] = "no-store"
    return r


@qbproxy_bp.route("/qualibrate-ui-target", methods=["POST"])
def proxy_target_set():
    """Save the QUAlibrate address chosen in SM's UI (D-20).

    Origin-checked like every SM write; the address must be loopback (D-3) and
    must answer as QUAlibrate before it is saved. ``{"url": ""}`` forgets it."""
    from flask import jsonify
    from quam_state_manager.web.request_body import json_object, json_text
    data = json_object()
    url = json_text(data, "url")
    if (os.environ.get(TARGET_ENV) or "").strip():
        st = _target_status()
        st["reason"] = (f"SM uses {TARGET_ENV}={st['url']} from its environment; "
                        f"change or unset it there and restart SM.")
        return jsonify(st), 409
    path = _settings_path()
    if not url:
        try:
            path.unlink()
        except FileNotFoundError:
            pass
        except OSError as e:
            return jsonify(ok=False, reason=f"Could not forget the address: {e}"), 500
        return jsonify(_target_status())
    try:
        host, port = parse_target(url, "The QUAlibrate address")
    except TargetError as e:
        return jsonify(ok=False, url=url, reason=str(e)), 400
    version, why = probe(host, port)
    if version is None:
        return jsonify(ok=False, url=url, reason=f"Not saved: {why}."), 502
    from quam_state_manager.core.safe_io import atomic_write_json
    canonical = f"http://{_netloc(host, port)}"
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_json(path, {"url": canonical})
    except Exception as e:  # noqa: BLE001 (answer with the reason, never a 500 page)
        return jsonify(ok=False, url=url, reason=f"Could not save the address: {e}"), 500
    return jsonify(_target_status())
