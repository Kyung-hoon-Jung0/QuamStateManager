"""Readable Plotly text for the common matplotlib mathtext label syntax."""
from html import escape
from html.entities import name2codepoint
import re

_SYMBOLS = {name: chr(code) for name, code in name2codepoint.items()}
_SYMBOLS.update(varepsilon="\u03f5", varphi="\u03d5", vartheta="\u03d1", varrho="\u03f1",
                cdot="\u00b7", times="\u00d7", pm="\u00b1", mu="\u03bc", pi="\u03c0",
                infty="\u221e", leq="\u2264", geq="\u2265", langle="\u27e8", rangle="\u27e9")


def readable(text):
    if not isinstance(text, str):
        return text

    def render(expression):
        expression = escape(expression, quote=False)
        expression = re.sub(r"\\(?:mathrm|mathregular|mathdefault|mathbf|mathit|text|operatorname)\b", "", expression)
        expression = re.sub(r"\\([A-Za-z]+)", lambda m: _SYMBOLS.get(m[1], m[1]), expression)
        expression = expression.replace(r"\,", " ").replace(r"\;", " ").replace(r"\!", "")
        expression = re.sub(r"([_^])(?:\{([^{}]*)\}|([^\s{}]))",
                            lambda m: "<" + ("sub" if m[1] == "_" else "sup") + ">" +
                            (m[2] if m[2] is not None else m[3]) + "</" + ("sub" if m[1] == "_" else "sup") + ">", expression)
        return expression.replace("{", "").replace("}", "")

    parts, end = [], 0
    for match in re.finditer(r"(?<!\\)\$([^$]+)(?<!\\)\$", text):
        parts.extend((escape(text[end:match.start()], quote=False), render(match[1])))
        end = match.end()
    parts.append(escape(text[end:], quote=False))
    return "".join(parts).replace("\r\n", "\n").replace("\n", "<br>")


def plotly_text(value, key=""):
    if isinstance(value, dict):
        return {k: plotly_text(v, k) for k, v in value.items()}
    if isinstance(value, list):
        if value and isinstance(value[0], (int, float, type(None))) and key not in {"text", "title", "name", "label", "ticktext"}:
            return value
        return [plotly_text(v, key) for v in value]
    return readable(value) if key in {"text", "title", "name", "label", "ticktext"} else value
