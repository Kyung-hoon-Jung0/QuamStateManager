"""Serving SM under a URL prefix, behind a reverse proxy (docs/226).

ONE variable -- the mount prefix, ``''`` at root -- and every consumer is the
identity at ``''``. With no prefix configured and ``--behind-proxy`` off,
:func:`install` installs NOTHING: ``app.wsgi_app`` stays Flask's own bound
method, so a root-mounted user runs the exact WSGI stack they ran before.

What lives here:

- :func:`normalize_prefix` -- the one parser for ``--url-prefix`` /
  ``SM_URL_PREFIX`` / ``X-Forwarded-Prefix``. No percent-encoding is accepted
  on purpose: the value is concatenated verbatim into ``data-root``, JS strings
  and HTML attributes.
- :class:`PrefixMiddleware` -- sets ``SCRIPT_NAME`` to the prefix and strips it
  from ``PATH_INFO`` when present (tolerant: a stripping proxy, a non-stripping
  proxy and a loopback client that never heard of the prefix all land on the
  same route).
- :func:`validate_frame_ancestors` -- the CSP ``frame-ancestors`` opt-in, with a
  header-injection guard.
- :func:`route_first_segments` -- the collision rule's vocabulary.
"""

from __future__ import annotations

import logging
import re
import threading

logger = logging.getLogger("quam_state_manager.web.url_prefix")

#: The longest prefix accepted (normalised form).
MAX_PREFIX_LEN = 200

_SEGMENT = re.compile(r"[A-Za-z0-9._~-]+")

_TRUE = frozenset({"1", "true", "t", "yes", "y", "on"})
_FALSE = frozenset({"", "0", "false", "f", "no", "n", "off"})


class PrefixCollisionError(ValueError):
    """The configured prefix's first segment is one of SM's own routes."""

    def __init__(self, prefix: str, segment: str):
        self.prefix = prefix
        self.segment = segment
        super().__init__(f"--url-prefix {prefix} collides with SM route /{segment}; "
                         "choose another prefix")


def normalize_prefix(raw) -> str:
    """``raw`` -> the canonical prefix (``''`` = root). Raises ``ValueError``
    with a one-line reason for anything that is not a plain path.

    ``None``/``''``/``'/'`` -> ``''``; must start with ``/``; ``//+`` collapses;
    a trailing ``/`` is dropped; every segment is ``[A-Za-z0-9._~-]+`` and never
    ``.`` or ``..``; at most :data:`MAX_PREFIX_LEN` characters.
    """
    if raw is None:
        return ""
    if not isinstance(raw, str):
        raise ValueError(f"must be text, got {type(raw).__name__}")
    if raw in ("", "/"):
        return ""
    if not raw.startswith("/"):
        raise ValueError(f"{raw!r} must start with '/'")
    collapsed = re.sub(r"/{2,}", "/", raw).rstrip("/")
    if not collapsed:
        return ""
    for seg in collapsed[1:].split("/"):
        if seg in (".", ".."):
            raise ValueError(f"{raw!r}: '.' and '..' segments are not allowed")
        if not _SEGMENT.fullmatch(seg):
            raise ValueError(
                f"{raw!r}: only letters, digits and . _ ~ - are allowed in a "
                "segment (no spaces, %, ?, # or non-ASCII)")
    if len(collapsed) > MAX_PREFIX_LEN:
        raise ValueError(f"longer than {MAX_PREFIX_LEN} characters")
    return collapsed


def parse_bool(raw, *, name: str = "SM_BEHIND_PROXY") -> bool:
    """``1/true/yes/on`` (any case) -> True; ``''/0/false/no/off`` -> False;
    anything else is a ``ValueError`` (a typo must not silently mean "off")."""
    if isinstance(raw, bool):
        return raw
    if raw is None:
        return False
    v = str(raw).strip().lower()
    if v in _TRUE:
        return True
    if v in _FALSE:
        return False
    raise ValueError(f"{name}={raw!r} is not a yes/no value (use 1/0, true/false, yes/no, on/off)")


# 'self' | 'none' | https?://host(:port)? | host(:port)?   (host may carry '*')
_FA_TOKEN = re.compile(r"'self'|'none'|https?://[A-Za-z0-9.*-]+(:\d+)?|[A-Za-z0-9.*-]+(:\d+)?")
_FA_FORBIDDEN = re.compile(r"[;,\"`\r\n\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def validate_frame_ancestors(raw) -> str:
    """The extra CSP ``frame-ancestors`` sources, validated and normalised.

    Returns the tokens joined by one space (``''`` when there are none;
    ``'self'`` is dropped because the policy always carries it). ``'none'`` is
    only valid alone. Any ``;``, ``,``, quote or control character raises --
    this string goes into a response header verbatim.
    """
    if raw is None:
        return ""
    if not isinstance(raw, str):
        raise ValueError(f"must be text, got {type(raw).__name__}")
    if _FA_FORBIDDEN.search(raw):
        raise ValueError("frame-ancestors may not contain ; , quotes or control characters")
    tokens = raw.split()
    out: list[str] = []
    for tok in tokens:
        if not _FA_TOKEN.fullmatch(tok):
            raise ValueError(f"{tok!r} is not a frame-ancestors source "
                             "(use 'self', 'none', https://host[:port] or host[:port])")
        if tok == "'self'" or tok in out:
            continue
        out.append(tok)
    if "'none'" in out and len(out) > 1:
        raise ValueError("'none' must be the only frame-ancestors source")
    return " ".join(out)


def route_first_segments(app) -> frozenset:
    """The first path segment of every rule the app routes (``static`` included,
    the bare ``/`` excluded)."""
    segs = set()
    for rule in app.url_map.iter_rules():
        parts = rule.rule.split("/")
        if len(parts) > 1 and parts[1] and not parts[1].startswith("<"):
            segs.add(parts[1])
    return frozenset(segs)


def check_collision(prefix: str, segments) -> None:
    """Raise :class:`PrefixCollisionError` when ``prefix``'s first segment is a
    route of SM's own -- the tolerant strip and the client's idempotency rule
    both read "starts with the prefix" as "already prefixed"."""
    if not prefix:
        return
    first = prefix.split("/")[1]
    if first in segments:
        raise PrefixCollisionError(prefix, first)


class PrefixMiddleware:
    """Mount the app under a prefix. See the module docstring.

    ``behind_proxy``: ProxyFix (installed OUTSIDE this) has already copied
    ``X-Forwarded-Prefix`` into ``SCRIPT_NAME``, raw; it is normalised here. A
    configured prefix always wins over the header (warned once per mismatch
    kind); an invalid or colliding header is ignored (warned once).
    """

    def __init__(self, app, prefix: str, *, behind_proxy: bool, route_segments):
        self.app = app
        self.prefix = prefix or ""
        self.behind_proxy = bool(behind_proxy)
        self.route_segments = frozenset(route_segments or ())
        self._warned: set[str] = set()
        self._lock = threading.Lock()

    def _warn_once(self, key: str, msg: str, *args) -> None:
        with self._lock:
            if key in self._warned:
                return
            self._warned.add(key)
        logger.warning(msg, *args)

    def __call__(self, environ, start_response):
        cfg = self.prefix
        hdr = ""
        if self.behind_proxy:
            raw = environ.get("SCRIPT_NAME", "") or ""
            try:
                hdr = normalize_prefix(raw)
            except ValueError:
                self._warn_once("invalid", "invalid X-Forwarded-Prefix %r ignored", raw)
                hdr = ""
            if hdr and hdr.split("/")[1] in self.route_segments:
                self._warn_once("collide", "X-Forwarded-Prefix %r collides with an SM route; ignored", hdr)
                hdr = ""
            if cfg and hdr and hdr != cfg:
                self._warn_once(
                    "differs",
                    "X-Forwarded-Prefix %r differs from --url-prefix %r; using the configured one",
                    hdr, cfg)
        prefix = cfg or hdr
        path = environ.get("PATH_INFO", "") or "/"
        if prefix and (path == prefix or path.startswith(prefix + "/")):
            path = path[len(prefix):] or "/"
        environ["PATH_INFO"] = path
        environ["SCRIPT_NAME"] = prefix
        return self.app(environ, start_response)


def install(app, prefix: str, *, behind_proxy: bool) -> None:
    """Wrap ``app.wsgi_app`` -- ONLY when a prefix is configured or the proxy
    headers are trusted. Call after every blueprint is registered (the
    collision vocabulary is the full route map).

    Order: ProxyFix (outer, runs first) -> PrefixMiddleware -> Flask.
    """
    if not (prefix or behind_proxy):
        return                      # root: the WSGI stack is untouched
    segments = route_first_segments(app)
    check_collision(prefix, segments)
    wsgi = PrefixMiddleware(app.wsgi_app, prefix, behind_proxy=behind_proxy,
                            route_segments=segments)
    if behind_proxy:
        from werkzeug.middleware.proxy_fix import ProxyFix
        wsgi = ProxyFix(wsgi, x_for=1, x_proto=1, x_host=1, x_port=1, x_prefix=1)
    app.wsgi_app = wsgi
