"""One reading of a request's JSON body (v1.3.0 B1).

Routes used to write ``request.get_json(silent=True) or {}`` and then call
``.get()`` on the result -- a body of ``"s"`` or ``12`` reached ``.get`` and the
route answered 500. ``json_object()`` returns the body only when it is a JSON
object; anything else is a 400 with a plain message, before the route reads it.
``json_text()`` reads one text field of it the same way."""
from __future__ import annotations

from typing import Any, Callable
import json

from flask import jsonify, request
from werkzeug.exceptions import BadRequest


class BadBody(BadRequest):
    """400 for a request body of the wrong shape: ``{"ok": false, "error": ...}``
    (the body was JSON, so the caller reads JSON)."""

    def __init__(self, message: str):
        super().__init__(description=message)
        self.response = jsonify(ok=False, error=message)
        self.response.status_code = 400


def json_object(fallback: Callable[[], Any] | Any = None) -> Any:
    """The request's JSON body when it is an object.

    No body, a body that is not JSON, or JSON ``null`` -> *fallback* (called
    when callable, e.g. ``request.form.to_dict``), else ``{}``. Any other JSON
    value -- a list, a string, a number -- raises :class:`BadBody` (400)."""
    data = request.get_json(silent=True)
    if data is None:
        if fallback is None:
            return {}
        return fallback() if callable(fallback) else fallback
    if isinstance(data, dict):
        return data
    raise BadBody("The request body must be a JSON object ({...}), "
                  f"not a {type(data).__name__}.")


def json_text(data: Any, key: str, default: str = "") -> str:
    """``data[key]`` as stripped text; missing or empty -> *default*.

    Same reading as the ``(data.get(key) or "").strip()`` it replaces, except
    that a value that is not text (a list, an object, a number) is a 400
    naming the field instead of a 500."""
    v = data.get(key) if hasattr(data, "get") else None
    if not v:
        return default
    if isinstance(v, str):
        return v.strip()
    raise BadBody(f"'{key}' must be text, not a {type(v).__name__}.")


def provenance(data: Any, *, form: bool = False) -> dict | None:
    """Optional plot origin: four bounded strings, never arbitrary metadata."""
    if "provenance" not in data:
        return None
    value = data["provenance"]
    if form:
        try:
            value = json.loads(value)
        except (ValueError, TypeError, RecursionError):
            raise BadBody("'provenance' must be a JSON object.") from None
    if not isinstance(value, dict):
        raise BadBody("'provenance' must be a JSON object.")
    if set(value) - {"src", "run_uid", "request_hash", "text"}:
        raise BadBody("Unknown provenance field.")
    if any(not isinstance(v, str) or len(v) > 300 for v in value.values()):
        raise BadBody("Provenance fields must be text of at most 300 characters.")
    try:
        for v in value.values():
            v.encode("utf-8")
    except UnicodeEncodeError:
        raise BadBody("Provenance fields must be valid Unicode text.") from None
    return dict(value)


def map_request(data: dict) -> dict:
    """Validate map selectors and options before touching arrays or running a model."""
    import math
    allowed = {"fig", "panel", "source", "smooth", "contour", "fit", "which", "var", "selection", "x_dim", "y_dim", "component"}
    if set(data) - allowed:
        raise BadBody("Unknown map request field.")
    for key in ("fig", "source", "which", "var", "x_dim", "y_dim", "component"):
        if key in data and (not isinstance(data[key], str) or len(data[key]) > 300):
            raise BadBody(f"'{key}' must be bounded text.")
    if data.get("source", "replot") not in {"replot", "recipe", "ndview"}:
        raise BadBody("Unknown map source.")
    panel = data.get("panel", 0)
    if type(panel) is not int or panel < 0:
        raise BadBody("'panel' must be a nonnegative integer.")
    fields = {"smooth": {"kernel", "sigma_x", "sigma_y", "override_cap"},
              "contour": {"on", "mode", "level", "band"}, "fit": {"model"}}
    for key, keys in fields.items():
        options = data.get(key, {})
        if not isinstance(options, dict) or set(options) - keys:
            raise BadBody(f"Invalid '{key}' options.")
        for name, value in options.items():
            if name in {"override_cap", "on"}:
                if type(value) is not bool:
                    raise BadBody(f"'{name}' must be boolean.")
            elif name in {"sigma_x", "sigma_y", "level", "band"}:
                if type(value) not in (float, int) or not math.isfinite(value):
                    raise BadBody(f"'{name}' must be finite numeric data.")
                if name != "level" and value < 0 or name.startswith("sigma") and value > 8:
                    raise BadBody(f"'{name}' is outside its range.")
            elif not isinstance(value, str):
                raise BadBody(f"'{name}' must be text.")
    if data.get("smooth", {}).get("kernel", "gaussian") not in {"gaussian", "box", "savgol", "median"}:
        raise BadBody("Unknown smoothing kernel.")
    if data.get("contour", {}).get("mode", "auto") not in {"auto", "target", "custom"}:
        raise BadBody("Unknown contour mode.")
    if "selection" in data and (not isinstance(data["selection"], dict) or any(
            not isinstance(k, str) or type(v) is not int or v < 0 for k, v in data["selection"].items())):
        raise BadBody("Invalid map selection.")
    return data
