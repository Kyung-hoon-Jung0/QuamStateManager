"""Hide network addresses and local folder paths in a shared chip report
(docs/277 section 3).

The report leaves the lab. With the switch on, recognized addresses, paths
and values learned from network fields are replaced by ``[hidden]``:

* **S (structural)** on the state / wiring documents: every scalar inside a
  ``network`` block; a string under a key whose words are network vocabulary
  (``host``, ``ip``, ``url``, ...); a ``port`` only beside such a key; every
  other string leaf through V.
* **V (value patterns)** on any text: URLs with a scheme, IPv4 / IPv6
  addresses, ``host:port``, absolute filesystem paths.
* **L (literals)**: every string S blanked is also replaced wherever the
  document repeats it (a cluster name inside a chip key or a message).

``redact_html`` applies V + L to the text and attribute values of a whole
HTML document, including comments and CSS content. JSON scripts and ``xmlns``
attributes are left alone (raw JSON is redacted before it is built).
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
    "url", "uri", "endpoint", "server", "proxy", "gateway", "cluster",
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
_HOSTPORT = r"(?<![\w.\-/])(?=[\w.\-]*[a-zA-Z])[a-zA-Z0-9\-]+(?:\.[a-zA-Z0-9\-]+)*:\d{1,5}\b"
# Consume the entire timestamp before its date and hour can read as host:port.
_TIMESTAMP = (r"(?<![\w.])(?P<timestamp>\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}"
              r"(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})?|\d{8}T\d{6}(?:\.\d+)?"
              r"(?:Z|[+-]\d{4})?)(?![\w.])")
_WIN = r"(?<![A-Za-z0-9])[A-Za-z]:[\\/](?:[^\"'<>|*?\r\n\\/]*[\\/])*[^\s\"'<>|*?\\/]*"
_UNC = r"(?<!\w)(?:\\+|/{2,})[^\s\\/\"'<>]+[\\/]+(?:[^\"'<>|*?\r\n\\/]*[\\/]+)*[^\s\"'<>|*?\\/]*"
_POSIX = r"(?<![\w.~:/\\#\-])/(?:[^\s/\"'<>|]+/)+[^\s/\"'<>|]*"
_HOME = r"(?<![\w])~[\\/][^\s\"'<>]*"
_SCP = r"\b[\w.\-]+@[\w.\-]+:[^\s\"'<>]+"
_VISA = r"\bTCPIP\d*::[^\s\"'<>]+"
_MOUNT = r"\b[\w.\-]+:/(?:[^\s/\"'<>]+/)*[^\s\"'<>]+"
# A fixed suffix list distinguishes bare domains from dotted parameter paths.
_DOMAIN_SUFFIXES = (
    "com", "org", "net", "edu", "gov", "mil", "int", "io", "co", "ai",
    "app", "dev", "cloud", "info", "biz", "name", "xyz", "online", "site", "tech",
    "uk", "us", "ca", "de", "fr", "kr", "jp", "cn", "au", "eu", "ch", "nl",
    "internal", "local", "lan", "corp", "intra", "home", "localdomain", "example",
    "test", "invalid",
)
_FQDN = (r"(?<![\w.])(?:[a-zA-Z0-9-]+\.)+"
         rf"(?:{'|'.join(_DOMAIN_SUFFIXES)})"
         r"(?![\w.])(?:/[^\s\"'<>]*)?")

#: Layer V, in order (a URL is consumed before its ``s://`` could read as a
#: drive letter, an address before its digits could read as anything else).
VALUE_PATTERNS: tuple[re.Pattern, ...] = tuple(
    re.compile(p, re.I) for p in (_TIMESTAMP, _URL, _SCP, _VISA, _MOUNT, _IPV4, _IPV6,
                                 _HOSTPORT, _WIN, _UNC, _POSIX, _HOME, _FQDN))
#: The same layer as ONE alternation (leftmost match wins; at one position the
#: order above decides), so a text costs one scan, not eight.
_V_ALL = re.compile("|".join(f"(?:{p.pattern})" for p in VALUE_PATTERNS), re.I)


def _replace_value_match(m: re.Match) -> str:
    # Small four-part numeric versions are ambiguous with IPv4. Network
    # fields still hide them structurally; ordinary version text survives.
    value = m.group()
    if m.lastgroup == "timestamp":
        return value
    if re.fullmatch(r"[0-9]\.[0-9]\.[0-9]\.[0-9]", value):
        return value
    # S10 walk: a path or address that ends a sentence or closes a
    # parenthesis in prose ("(D:\data\x) are ...", "... in D:\data\x.")
    # leaves that punctuation outside the blank -- a ")" only when the match
    # did not open it ("C:\Program Files (x86)" stays whole)
    tail = ""
    while len(value) > 1:
        last = value[-1]
        if last in ".,;:" or (last == ")" and value.count(")") > value.count("(")):
            tail = last + tail
            value = value[:-1]
        else:
            break
    return HIDDEN + tail


def _value_text(s: str) -> str:
    return _V_ALL.sub(_replace_value_match, s)


def _literal_pattern(strings: Iterable[str]) -> str:
    """Share literal prefixes so case-insensitive matching stays bounded."""
    trie: dict = {}
    for value in strings:
        node = trie
        for char in value.lower():
            node = node.setdefault(char, {})
        node[None] = None

    def render(node):
        branches = []
        for char, child in node.items():
            if char is None:
                continue
            prefix = char
            while len(child) == 1 and None not in child:
                char, child = next(iter(child.items()))
                prefix += char
            branches.append(re.escape(prefix) + render(child))
        if None in node:
            branches.append("")
        if len(branches) == 1:
            return branches[0]
        return "(?:" + "|".join(branches) + ")"
    return render(trie)


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
    def for_documents(cls, *docs: Any, literals: Iterable[str] = ()) -> "Redactor":
        """A redactor whose literal set (L) holds every value S blanks in
        *docs*. Walks without building a copy."""
        r = cls(literals)
        r._seen: set = set()
        r._seen_keys: set = set()
        for d in docs:
            r._collect(d, False)
        del r._seen
        del r._seen_keys
        r.literals = frozenset(r.literals)
        r._memo.clear()
        r._tag_memo.clear()
        r._apply_literals("")
        return r

    def _collect(self, obj: Any, in_net: bool) -> None:
        """The literal-gathering half of :meth:`redact_tree`, copy-free."""
        if isinstance(obj, dict):
            for k, v in obj.items():
                if isinstance(k, str) and k not in self._seen_keys:
                    self._seen_keys.add(k)
                    if _value_text(k) != k:
                        self._remember(k)
                net = in_net or str(k).lower() == "network"
                if isinstance(v, (dict, list)):
                    self._collect(v, net)
                elif isinstance(v, str):
                    if net or is_network_key(k):
                        self._remember(v)
                    elif v not in self._seen:
                        self._seen.add(v)
                        if _value_text(v) != v:
                            self._remember(v)
        elif isinstance(obj, list):
            for v in obj:
                if isinstance(v, (dict, list)):
                    self._collect(v, in_net)
                elif isinstance(v, str):
                    if in_net:
                        self._remember(v)
                    elif v not in self._seen:
                        self._seen.add(v)
                        if _value_text(v) != v:
                            self._remember(v)
        elif isinstance(obj, str) and (in_net or _value_text(obj) != obj):
            self._remember(obj)

    @staticmethod
    def _literal_ok(s: Any) -> bool:
        if not isinstance(s, str):
            return False
        t = s.strip()
        return (len(t) >= MIN_LITERAL and t != HIDDEN
                and not re.fullmatch(r"[\d.\s+\-eE]+", t))

    def _remember(self, s: Any) -> None:
        if isinstance(self.literals, frozenset):
            return
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
        match = _V_ALL.match(s)
        if match and _replace_value_match(match) == HIDDEN:
            return HIDDEN
        out = self.redact_text(s)
        return out

    def redact_text(self, s: str) -> str:
        """Layers V and L on free text."""
        if not s or s.isspace():
            return s
        hit = self._memo.get(s)
        if hit is not None:
            return hit
        out = _value_text(self._apply_literals(s))
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
            self._lit_re = re.compile(_literal_pattern(alts), re.I)
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
                original_key = k
                k = self.redact_text(k) if isinstance(k, str) else k
                hidden_key = k
                suffix = len(out)
                while k in out:
                    k = f"{hidden_key} {suffix}"
                    suffix += 1
                if isinstance(v, (dict, list)):
                    out[k] = self.redact_tree(v, _in_network=net)
                elif v is None:
                    out[k] = None
                elif net:
                    self._remember(v)
                    out[k] = HIDDEN
                elif isinstance(v, str) and is_network_key(original_key):
                    self._remember(v)
                    out[k] = HIDDEN
                elif (host_like and _is_port_key(original_key) and not isinstance(v, bool)
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
                quote = q[0] if q[0] in "\"'" else ""
                value = q[1:-1] if quote else q
                value = redact_css(value, self) if name == "style" else self.redact_text(value)
                return f"{m.group(1)}{m.group(2)}{quote}{value}{quote}"
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
_ATTR = re.compile(r"""([^\s=/>"']+)(\s*=\s*)("[^"]*"|'[^']*'|[^\s>]+)""")
_CSS_CONTENT = re.compile(r'''url\(\s*(?:"(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*'|[^)]*)\s*\)|"(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*'|/\*.*?\*/''', re.S | re.I)


def redact_css(css: str, red=None, *, offline: bool = False) -> str:
    """Change CSS content tokens only; preserve declarations and data URIs."""
    def replace(m):
        token = m.group()
        if token.lower().startswith("url("):
            argument = token[4:-1]
            stripped = argument.strip()
            quote = stripped[0] if stripped[:1] in ("'", '"') else ""
            value = stripped[1:-1] if quote else stripped
            if value.lower().startswith("data:") or value.startswith("#"):
                return token
            if offline:
                return 'url("")'
            return token.replace(value, red.redact_text(value), 1) if red else token
        if not red:
            return token
        if token.startswith("/*"):
            return "/*" + red.redact_text(token[2:-2]) + "*/"
        return token[0] + red.redact_text(token[1:-1]) + token[-1]
    return _CSS_CONTENT.sub(replace, css)


def _html_tokens(doc: str):
    """Yield text gaps and matches without a whole-document split."""
    pos = 0
    for match in HTML_TOKENS.finditer(doc):
        if match.start() > pos:
            yield doc[pos:match.start()]
        yield match.group()
        pos = match.end()
    if pos < len(doc):
        yield doc[pos:]


def html_pass(doc: str, red: "Redactor | None", *, on_script=None, on_tag=None,
              collapse_space: bool = False, offline: bool = False, on_token=None) -> str:
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
    * comments and CSS quoted strings / URL arguments get V + L; CSS
      structure and embedded data URIs survive; *offline* drops remote URLs;
    * *on_token* can discard body children outside the section whitelist."""
    out: list[str] = []
    app = out.append
    for tok in _html_tokens(doc):
        if on_token is not None and not on_token(tok):
            continue
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
        if tok.startswith("<!--"):
            app("<!--" + red.redact_text(tok[4:-3]) + "-->" if red else tok)
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
            end = tok.index(">") + 1
            close = tok.rindex("</")
            head = on_tag(tok[:end]) if on_tag else tok[:end]
            if red:
                head = red.redact_tag(head)
            app(head + redact_css(tok[end:close], red, offline=offline) + tok[close:])
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
