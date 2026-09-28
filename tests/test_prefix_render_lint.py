"""The rendered-output leak lint (docs/226, spec §5.4).

The one detector that does not care WHY a root-absolute URL got out: a template
the rewrite missed, a macro imported without context, a server literal, a JSON
field, a header. It renders every page of the synthetic chip (the same sweep
the root golden uses, ``tests/tools/render_pages.py``) with SM mounted under
``/sm`` and scans what actually left the server:

* every URL attribute (``href``/``src``/``action``/``hx-*``, a ``data-*`` that
  names an app route) must be under ``/sm``;
* every quoted ``'/<route>'`` literal in inline JS/JSON must be under ``/sm``
  (templates prefix at the source -- spec §3.2 rule 2 -- so this can be strict);
* every ``Location`` / ``HX-Redirect`` / ``HX-Location`` / ``HX-Push-Url``;
* no ``/sm/sm/`` anywhere;
* every full document carries ``data-root="/sm"``; an htmx document's
  ``<body>`` carries ``hx-ext="sm-root"``.

And at root (no prefix configured): ``data-root=""``, no ``hx-ext``, no ``/sm``.

At a commit whose ``create_app`` has no ``url_prefix`` (91c8aae), the mount is
rendered the way such an SM behaves behind a stripping proxy and the first pin
fails naming what leaked -- that is the harness catching the reported bug.
"""
from __future__ import annotations

import re
from collections import Counter

import pytest

from tests.tools import render_pages as rp

MOUNT = "/sm"


def _summary(found: dict, limit: int = 25) -> str:
    kinds = Counter(k for hits in found.values() for k, _ in hits)
    lines = [f"{sum(kinds.values())} leaks on {len(found)} pages; by kind {dict(kinds)}"]
    shown = 0
    for page, hits in sorted(found.items()):
        for kind, snip in hits:
            lines.append(f"  {page}  [{kind}]  {snip}")
            shown += 1
            if shown >= limit:
                return "\n".join(lines + ["  ..."])
    return "\n".join(lines)


@pytest.fixture(scope="module")
def mounted(tmp_path_factory):
    base = tmp_path_factory.mktemp("prefix_lint")
    r = rp.render_subprocess(base / "out", work=base / "work", prefix=MOUNT, strip=False)
    if r["rc"] != 0 and "no url_prefix kwarg" in r["stderr"]:
        r = rp.render_subprocess(base / "out", work=base / "work", prefix=MOUNT,
                                 legacy_mount=True, strip=False)
    assert r["rc"] == 0, r["stderr"][-3000:]
    return r


@pytest.fixture(scope="module")
def at_root(tmp_path_factory):
    base = tmp_path_factory.mktemp("prefix_lint_root")
    r = rp.render_subprocess(base / "out", work=base / "work", prefix="", strip=False)
    assert r["rc"] == 0, r["stderr"][-3000:]
    return r


def test_the_app_is_really_mounted(mounted):
    """create_app(url_prefix="/sm") exists and the sweep ran through it."""
    idx = mounted["index"]
    if idx["mode"] != "native":
        pytest.fail("create_app() has no url_prefix kwarg at this commit; rendered what "
                    "it emits behind a stripping proxy instead:\n"
                    + _summary(rp.scan_dir(mounted["out"])))
    assert idx["load_status"] in (200, 302), idx["load_status"]
    assert idx["workspace_add_status"] == 200


def test_no_root_absolute_url_leaves_the_server(mounted):
    found = rp.scan_dir(mounted["out"])
    assert not found, _summary(found)


def test_every_page_answers_under_the_mount_as_it_does_at_root(mounted, at_root):
    """A page that renders at root renders under /sm with the same status."""
    a, b = at_root["index"]["pages"], mounted["index"]["pages"]
    assert set(a) == set(b)
    diff = {k: (a[k]["status"], b[k]["status"]) for k in a
            if a[k]["status"] != b[k]["status"] or bool(a[k]["error"]) != bool(b[k]["error"])}
    assert not diff, diff


def test_root_render_carries_no_prefix_machinery(at_root):
    found = rp.scan_dir(at_root["out"])        # prefix '' -> only the document rules apply
    assert not found, _summary(found)
    leaks = {}
    rx = re.compile(r"""(["'`])""" + re.escape(MOUNT) + r"""(?=[/?#"'`])""")
    for key, meta in at_root["index"]["pages"].items():
        text = (at_root["out"] / "pages" / meta["file"]).read_text(encoding="utf-8")
        if 'hx-ext="sm-root"' in text or rx.search(text):
            leaks[key] = True
    assert not leaks, sorted(leaks)[:25]


# ---------------------------------------------------------------------------
# the scanner itself -- a lint that cannot fail proves nothing
# ---------------------------------------------------------------------------
_SEGS = {"qubits", "diff", "static", "api", "datasets"}


@pytest.mark.parametrize("text,kind", [
    ('<a href="/qubits">', "attr"),
    ('<div hx-get="/diff?a=1">', "attr"),
    ('<form action="/api/x">', "attr"),
    ('<div data-calc-window-url="/qubits">', "data-attr"),
    ("fetch('/datasets/poll')", "literal"),
    ('{"url": "/qubits"}', "literal"),
    ("htmx.ajax('GET', `/diff`, {})", "literal"),
    ("background: url(/static/x.png)", "css"),
    ('<a href="/sm/sm/qubits">', "double"),
])
def test_scanner_flags(text, kind):
    hits = rp.scan_page(text, {}, MOUNT, _SEGS)
    assert kind in {k for k, _ in hits}, hits


@pytest.mark.parametrize("text", [
    '<a href="/sm/qubits">', '<a href="/sm">', '<a href="/sm?x=1">',
    '<a href="//cdn.example/x">', '<a href="https://h/qubits">', '<a href="#top">',
    "fetch('/sm/datasets/poll')", '{"url": "/sm/qubits"}',
    '<div data-path="/not/an/app/route">', "var p = '#/qubits/q1';",
    '<a href="{{ url }}">',
])
def test_scanner_passes(text):
    assert rp.scan_page(text, {}, MOUNT, _SEGS) == []


def test_scanner_headers_and_documents():
    assert rp.scan_page("", {"Location": "/diff"}, MOUNT, _SEGS)
    assert rp.scan_page("", {"HX-Redirect": "/qubits"}, MOUNT, _SEGS)
    assert rp.scan_page("", {"HX-Location": '{"path": "/diff"}'}, MOUNT, _SEGS)
    assert rp.scan_page("", {"Location": "/sm/diff"}, MOUNT, _SEGS) == []
    doc = '<!DOCTYPE html>\n<html lang="en" data-root="/sm">\n<script src="/sm/static/htmx.min.js"></script><body hx-ext="sm-root">'
    assert rp.scan_page(doc, {}, MOUNT, _SEGS) == []
    assert rp.scan_page(doc.replace(' hx-ext="sm-root"', ""), {}, MOUNT, _SEGS)
    assert rp.scan_page(doc.replace('data-root="/sm"', 'data-root=""'), {}, MOUNT, _SEGS)
    # the same document is clean at root only with data-root="" and no hx-ext
    root_doc = doc.replace('data-root="/sm"', 'data-root=""').replace(' hx-ext="sm-root"', "")
    assert rp.scan_page(root_doc, {}, "", _SEGS) == []
    assert rp.scan_page(doc.replace('data-root="/sm"', 'data-root=""'), {}, "", _SEGS)
    # a redirect body (werkzeug's <html lang=en>) is not a document the browser shows
    assert rp.scan_page("<!doctype html>\n<html lang=en>\n<title>Redirecting</title>",
                        {}, MOUNT, _SEGS, status=302) == []
    # "<html>" inside JS prose is not the document element
    assert rp.scan_page('<html data-root="/sm"><script>// class on <html></script>',
                        {}, MOUNT, _SEGS) == []
