"""Hide network addresses and local folder paths in a shared chip report
(docs/277 section 3).

The report leaves the lab. With the switch on, everything that addresses a
machine or names a folder on this PC is replaced by ``[hidden]``, by RULE --
never by a list of one chip's keys:

* **S (structural)** on the state / wiring documents: every scalar inside a
  ``network`` block; a string under a key whose words are network vocabulary
  (``host``, ``ip``, ``url``, ...); a ``port`` only beside such a key; every
  other string leaf through V.
* **V (value patterns)** on any text: URLs with a scheme, IPv4 / IPv6
  addresses, ``host:port``, absolute filesystem paths.
* **L (literals)**: every string S blanked is also replaced wherever the
  document repeats it (a cluster name inside a chip key or a message).

``redact_html`` applies V + L to the text and attribute values of a whole
HTML document; ``<style>`` bodies, JSON scripts and ``xmlns`` attributes are
left alone (the raw-tree JSON is redacted structurally before it is built).
"""

from __future__ import annotations

import html as _html
import re
from functools import lru_cache
from typing import Any, Iterable

HIDDEN = "[hidden]"

#: Words that make a key a network field (S2). A key is split into words on
#: ``_ - .`` and camelCase; one of these words is enough.
NET_WORDS = frozenset({
    "host", "hostname", "ip", "ipaddr", "ipv4", "ipv6", "addr", "address",
    "url", "uri", "endpoint", "server", "proxy", "gateway",
})

#: A key named after a port is network only beside a host-like key (S2).
PORT_WORDS = frozenset({"port"})

#: Strings shorter than this are not remembered as literals (L): replacing a
#: two-letter value everywhere would wreck the document.
MIN_LITERAL = 4

_WORD_RE = re.compile(r"[A-Z]+(?=[A-Z][a-z]|\d|\b|_)|[A-Z]?[a-z]+|[A-Z]+|\d+")

_URL = r"\b[a-zA-Z][a-zA-Z0-9+.\-]{1,15}://[^\s\"'<>]+"
_OCT = r"(?:25[0-5]|2[0-4]\d|1\d\d|[1-9]?\d)"
_IPV4 = rf"(?<![0-9.]){_OCT}(?:\.{_OCT}){{3}}(?::\d{{1,5}})?(?![0-9.])"
_H4 = r"[0-9a-fA-F]{1,4}"
_IPV6 = (rf"(?<![\w:])(?:(?:{_H4}:){{7}}{_H4}"
         rf"|(?=[0-9a-fA-F:]*::)(?:{_H4})?(?:::?{_H4}){{2,7}}::?|"
         rf"(?=[0-9a-fA-F:]*::)(?:{_H4})?(?:::?{_H4}){{2,7}})(?![\w:])")
_HOSTPORT = r"(?<![\w.\-/])(?:localhost|(?=[\w.\-]*[a-zA-Z])[a-zA-Z0-9\-]+(?:\.[a-zA-Z0-9\-]+)+):\d{1,5}\b"
_WIN = r"(?<![A-Za-z0-9])[A-Za-z]:[\\/](?:[^\"'<>|*?\r\n\\/]*[\\/])*[^\s\"'<>|*?\\/]*"
_UNC = r"(?<![\w\\])\\\\[^\s\\/\"'<>]+[\\/](?:[^\"'<>|*?\r\n\\/]*[\\/])*[^\s\"'<>|*?\\/]*"
_POSIX = r"(?<![\w.~:/\\#\-])/(?:[^\s/\"'<>|]+/)+[^\s/\"'<>|]*"
_HOME = r"(?<![\w])~[\\/][^\s\"'<>]*"

#: Layer V, in order (a URL is consumed before its ``s://`` could read as a
#: drive letter, an address before its digits could read as anything else).
VALUE_PATTERNS: tuple[re.Pattern, ...] = tuple(
    re.compile(p) for p in (_URL, _IPV4, _IPV6, _HOSTPORT, _WIN, _UNC, _POSIX, _HOME))
#: The same layer as ONE alternation (leftmost match wins; at one position the
#: order above decides), so a text costs one scan, not eight.
_V_ALL = re.compile("|".join(f"(?:{p.pattern})" for p in VALUE_PATTERNS))


def key_words(key: Any) -> list[str]:
    """``qopHostName`` -> ``['qop', 'host', 'name']``; ``ip_address`` ->
    ``['ip', 'address']``."""
    out: list[str] = []
    for part in re.split(r"[_\-.\s]+", str(key)):
        out.extend(w.lower() for w in _WORD_RE.findall(part))
    return out


@lru_cache(maxsize=65536)
def _key_class(key: str) -> tuple[bool, bool]:
    """(is a network key, is a port key) -- a chip repeats the same few
    hundred key names hundreds of thousands of times."""
    words = key_words(key)
    return (any(w in NET_WORDS for w in words), any(w in PORT_WORDS for w in words))


def is_network_key(key: Any) -> bool:
    return _key_class(str(key))[0]


def _is_port_key(key: Any) -> bool:
    return _key_class(str(key))[1]


class Redactor:
    """One redaction pass over one chip.

    Build it with :meth:`for_documents` so the literal set (L) holds every
    value the structural pass (S) blanked in that chip's two documents."""

    #: A text memo larger than this is dropped and rebuilt (memory bound).
    MEMO_MAX = 400_000

    def __init__(self, literals: Iterable[str] = ()) -> None:
        self.literals: set[str] = {s for s in literals if self._literal_ok(s)}
        self._lit_re: re.Pattern | None = None
        # text -> redacted text. A report repeats the same cell texts (ids,
        # units, '-') tens of thousands of times; each is matched once.
        self._memo: dict[str, str] = {}
        self._tag_memo: dict[str, str] = {}

    # ---------------------------------------------------------- construction
    @classmethod
    def for_documents(cls, *docs: Any) -> "Redactor":
        """A redactor whose literal set (L) holds every value S blanks in
        *docs*. Walks without building a copy."""
        r = cls()
        r._seen: set = set()
        for d in docs:
            r._collect(d, False)
        del r._seen
        return r

    def _collect(self, obj: Any, in_net: bool) -> None:
        """The literal-gathering half of :meth:`redact_tree`, copy-free."""
        if isinstance(obj, dict):
            for k, v in obj.items():
                net = in_net or str(k).lower() == "network"
                if isinstance(v, (dict, list)):
                    self._collect(v, net)
                elif isinstance(v, str):
                    if net or is_network_key(k):
                        self._remember(v)
                    elif v not in self._seen:
                        self._seen.add(v)
                        if _V_ALL.search(v):
                            self.redact_value(v)
        elif isinstance(obj, list):
            for v in obj:
                if isinstance(v, (dict, list)):
                    self._collect(v, in_net)
                elif isinstance(v, str):
                    if in_net:
                        self._remember(v)
                    elif v not in self._seen:
                        self._seen.add(v)
                        if _V_ALL.search(v):
                            self.redact_value(v)

    @staticmethod
    def _literal_ok(s: Any) -> bool:
        if not isinstance(s, str):
            return False
        t = s.strip()
        return (len(t) >= MIN_LITERAL and t != HIDDEN
                and not re.fullmatch(r"[\d.\s+\-eE]+", t))

    def _remember(self, s: Any) -> None:
        if self._literal_ok(s) and s.strip() not in self.literals:
            self.literals.add(s.strip())
            self._lit_re = None
            self._memo.clear()              # a new literal changes the answers
            self._tag_memo.clear()

    # ---------------------------------------------------------------- layer V
    def redact_value(self, s: str) -> str:
        """Layer V on one stored value: a value that STARTS with an address or
        a path is blanked whole; otherwise each matched span is replaced."""
        if not isinstance(s, str) or not s:
            return s
        if _V_ALL.match(s):
            self._remember(s)
            return HIDDEN
        out = self.redact_text(s)
        if out != s:
            self._remember(s)
        return out

    def redact_text(self, s: str) -> str:
        """Layers V and L on free text."""
        if not s or s.isspace():
            return s
        hit = self._memo.get(s)
        if hit is not None:
            return hit
        out = self._apply_literals(_V_ALL.sub(HIDDEN, s))
        if len(self._memo) > self.MEMO_MAX:
            self._memo.clear()
        self._memo[s] = out
        return out

    def _apply_literals(self, s: str) -> str:
        if not self.literals or not s:
            return s
        if self._lit_re is None:
            alts = sorted({x for lit in self.literals
                           for x in (lit, _html.escape(lit, quote=True),
                                     _html.escape(lit, quote=False))},
                          key=len, reverse=True)
            self._lit_re = re.compile("|".join(re.escape(a) for a in alts))
        return self._lit_re.sub(HIDDEN, s)

    # ---------------------------------------------------------------- layer S
    def redact_tree(self, obj: Any, *, _in_network: bool = False) -> Any:
        """A redacted COPY of a JSON document (S1 + S2 + S3)."""
        if isinstance(obj, dict):
            host_like = any(is_network_key(k) for k, v in obj.items()
                            if isinstance(v, (str, int, float)) and not isinstance(v, bool))
            out: dict = {}
            for k, v in obj.items():
                net = _in_network or str(k).lower() == "network"
                if isinstance(v, (dict, list)):
                    out[k] = self.redact_tree(v, _in_network=net)
                elif v is None:
                    out[k] = None
                elif net:
                    self._remember(v)
                    out[k] = HIDDEN
                elif isinstance(v, str) and is_network_key(k):
                    self._remember(v)
                    out[k] = HIDDEN
                elif (host_like and _is_port_key(k) and not isinstance(v, bool)
                      and isinstance(v, (str, int, float))):
                    out[k] = HIDDEN
                elif isinstance(v, str):
                    out[k] = self.redact_value(v)
                else:
                    out[k] = v
            return out
        if isinstance(obj, list):
            return [self.redact_tree(v, _in_network=_in_network) for v in obj]
        if _in_network and obj is not None:
            self._remember(obj)
            return HIDDEN
        if isinstance(obj, str):
            return self.redact_value(obj)
        return obj

    # ------------------------------------------------------------- whole HTML
    def redact_html(self, doc: str) -> str:
        """V + L over every text node and attribute value of *doc*."""
        return html_pass(doc, self)

    def redact_tag(self, tag: str) -> str:
        """V + L over one tag's attribute values (``xmlns`` excepted).
        Memoized: a report repeats the same few hundred tags endlessly."""
        hit = self._tag_memo.get(tag)
        if hit is not None:
            return hit
        if "=" not in tag:
            out = tag
        else:
            def one(m: re.Match) -> str:
                name = m.group(1).lower()
                if name == "xmlns" or name.startswith("xmlns:"):
                    return m.group(0)
                q = m.group(3)
                return f"{m.group(1)}{m.group(2)}{q[0]}{self.redact_text(q[1:-1])}{q[0]}"
            out = _ATTR.sub(one, tag)
        if len(self._tag_memo) > self.MEMO_MAX:
            self._tag_memo.clear()
        self._tag_memo[tag] = out
        return out


#: One HTML token: a comment, a whole script / style / pre / textarea element,
#: a tag, or (between them, from ``split``) a text run.
HTML_TOKENS = re.compile(
    r"(<!--.*?-->|<script\b[^>]*>.*?</script\s*>|<style\b[^>]*>.*?</style\s*>"
    r"|<pre\b[^>]*>.*?</pre\s*>|<textarea\b[^>]*>.*?</textarea\s*>|<[^>]+>)",
    re.S | re.I)
_ATTR = re.compile(r"""([^\s=/>"']+)(\s*=\s*)("[^"]*"|'[^']*')""")


def html_pass(doc: str, red: "Redactor | None", *, on_script=None, on_tag=None,
              collapse_space: bool = False) -> str:
    """ONE walk over an HTML document's tokens.

    * text runs and ``<pre>`` / ``<textarea>`` bodies get layers V + L (when
      *red* is given); with *collapse_space* a whitespace-only run between
      tags becomes one newline or space (it renders the same);
    * a tag's attribute values get V + L (``xmlns`` never: a namespace is not
      an address); *on_tag(tag) -> str* may rewrite it further;
    * a ``<script>`` keeps its body (the raw-tree JSON is redacted
      STRUCTURALLY before it is serialized; code is not content) -- only its
      opening tag is redacted; *on_script(tok) -> str | None* may drop it
      (``None``) or replace it;
    * ``<style>`` and comments pass through."""
    out: list[str] = []
    app = out.append
    for tok in HTML_TOKENS.split(doc):
        if not tok:
            continue
        c0 = tok[0]
        if c0 != "<":
            if tok.isspace():
                if not collapse_space:
                    app(tok)
                elif out and out[-1] in ("\n", " "):
                    # runs left beside a dropped script: one is enough
                    if "\n" in tok:
                        out[-1] = "\n"
                else:
                    app("\n" if "\n" in tok else " ")
            else:
                app(red.redact_text(tok) if red is not None else tok)
            continue
        if tok[1:2] == "/" or tok.startswith("<!"):
            app(tok)                         # an end tag / comment / doctype
            continue
        low = tok[:9].lower()
        if low.startswith("<script"):
            if on_script is not None:
                tok = on_script(tok)
                if tok is None:
                    continue
            if red is not None:
                end = tok.index(">") + 1
                tok = red.redact_tag(tok[:end]) + tok[end:]
            app(tok)
        elif low.startswith("<style"):
            app(tok)
        elif low.startswith(("<pre", "<textarea")):
            end = tok.index(">") + 1
            close = tok.rindex("</")
            head, body, tail = tok[:end], tok[end:close], tok[close:]
            if on_tag is not None:
                head = on_tag(head)
            if red is not None:
                head, body = red.redact_tag(head), red.redact_text(body)
            app(head + body + tail)
        else:
            if on_tag is not None:
                tok = on_tag(tok)
            app(red.redact_tag(tok) if red is not None else tok)
    return "".join(out)
