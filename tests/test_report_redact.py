"""The shareable report's redaction rule (docs/277 section 3).

"Hide network addresses and local folder paths" blanks, by RULE, every value
that addresses a machine or names a folder on this PC -- in the structured
documents (the raw tree) and in any text (every other section, and the whole
finished file). Pinned here layer by layer, including what it must NOT touch:
a JSON pointer, an OPX port, a date, a time, a unit, a dotted parameter path.
"""
from __future__ import annotations

import pytest

from quam_state_manager.core.report_redact import (
    HIDDEN, Redactor, html_pass, is_network_key, key_words)


@pytest.fixture
def red():
    return Redactor.for_documents(
        {"network": {"host": "10.20.30.40", "port": 9510, "cluster_name": "Cluster_Alpha",
                     "cloud": {"token_server": "auth.example.org"}}},
        {"extras": {"data_folder": "C:/lab data/devA_runs"},
         "qdac": {"qdac_ip": "qdac.lab.example", "port": 5025},
         "z": {"opx_output": "#/ports/analog_outputs/con1/5/1", "trigger_port": "ext1"}})


class TestStructural:
    def test_every_scalar_in_a_network_block_is_hidden_and_null_stays_null(self, red):
        out = red.redact_tree({"wiring": {}, "network": {
            "host": "1.2.3.4", "port": 80, "cluster_name": "X_cluster", "extra": None,
            "nested": {"anything": "goes", "n": 3}}})
        net = out["network"]
        assert net["host"] == net["port"] == net["cluster_name"] == HIDDEN
        assert net["extra"] is None
        assert net["nested"] == {"anything": HIDDEN, "n": HIDDEN}

    def test_a_network_word_in_a_key_hides_its_string(self, red):
        out = red.redact_tree({"qdac": {"qdac_ip": "box.local", "ipAddress": "x",
                                        "server_url": "http://x", "hostName": "lab-pc"}})
        assert set(out["qdac"].values()) == {HIDDEN}

    def test_a_port_is_hidden_only_beside_a_host(self, red):
        out = red.redact_tree({"svc": {"host": "a.b", "port": 1234},
                               "z": {"trigger_port": "ext1", "port_id": 3,
                                     "opx_output": "#/ports/analog_outputs/con1/5/1"}})
        assert out["svc"]["port"] == HIDDEN
        assert out["z"] == {"trigger_port": "ext1", "port_id": 3,
                            "opx_output": "#/ports/analog_outputs/con1/5/1"}

    def test_a_path_value_is_hidden_whole_and_a_pointer_never(self, red):
        out = red.redact_tree({"extras": {"data_folder": "D:/a b/c", "p": "#/qubits/q1/xy",
                                          "rel": "#../x180/length", "self": "#./inferred_x",
                                          "cls": "quam.components.pulses.SquarePulse"}})
        e = out["extras"]
        assert e["data_folder"] == HIDDEN
        assert (e["p"], e["rel"], e["self"], e["cls"]) == (
            "#/qubits/q1/xy", "#../x180/length", "#./inferred_x",
            "quam.components.pulses.SquarePulse")

    def test_the_tree_is_a_copy(self, red):
        doc = {"network": {"host": "1.2.3.4"}}
        red.redact_tree(doc)
        assert doc == {"network": {"host": "1.2.3.4"}}

    def test_key_words_split_snake_kebab_and_camel(self):
        assert key_words("qopHostName") == ["qop", "host", "name"]
        assert key_words("IPAddress") == ["ip", "address"]
        assert key_words("trigger-port") == ["trigger", "port"]
        assert is_network_key("cluster_url") and not is_network_key("opx_output")
        assert not is_network_key("trigger_port")


class TestValuePatterns:
    @pytest.mark.parametrize("text", [
        "http://qop.example:9510/x", "10.20.30.40", "10.20.30.40:80", "chip_10.1.2.3_5q",
        "fe80::1ff:fe23:4567:890a", "qop.lab.local:9510", "localhost:8080",
        r"C:\Users\someone\data", "C:/Program Files/x/y.exe", r"\\server\share\x",
        "/home/user/data", "~/data/x",
    ])
    def test_an_address_or_a_path_is_hidden(self, red, text):
        assert red.redact_text(text) != text
        assert HIDDEN in red.redact_text(text)

    @pytest.mark.parametrize("text", [
        "#/qubits/q1/xy", "#../x180/length", "#./inferred_intermediate_frequency",
        "16:11:25 (UTC+9)", "2026-10-04 16:11:25", "2026/10/04", "con1/fem3/p2",
        "T2 Ramsey / 2·T1", "I/Q", "rad/s", "1/2", "4.8954", "-0.35",
        "ports.analog_outputs.con1.5.1", "qubits.q1.xy.operations.x180",
    ])
    def test_ordinary_report_text_is_untouched(self, red, text):
        assert red.redact_text(text) == text

    def test_a_path_with_spaces_goes_whole_and_the_sentence_stays(self, red):
        out = red.redact_text(r"see D:\work\a b\c.json now")
        assert out == f"see {HIDDEN} now"


class TestLiterals:
    def test_a_blanked_value_is_hidden_wherever_it_is_repeated(self, red):
        assert "Cluster_Alpha" in red.literals and "qdac.lab.example" in red.literals
        assert red.redact_text("cluster Cluster_Alpha is down") == f"cluster {HIDDEN} is down"

    def test_short_or_numeric_values_are_never_literals(self):
        r = Redactor.for_documents({"network": {"host": "ab", "port": 9510, "x": "1234"}})
        assert r.literals == set()


class TestHtml:
    def test_text_attributes_and_css_but_not_namespaces_or_json(self, red):
        doc = ('<p title="D:\\x\\y">see 10.20.30.40 and Cluster_Alpha</p>'
               '<svg xmlns="http://www.w3.org/2000/svg"><text>/mnt/d/x</text></svg>'
               '<script type="application/json">{"a":"10.20.30.40"}</script>'
               '<style>a::after{content:"10.20.30.40"}</style>')
        out = red.redact_html(doc)
        assert out.startswith(f'<p title="{HIDDEN}">see {HIDDEN} and {HIDDEN}</p>')
        assert 'xmlns="http://www.w3.org/2000/svg"' in out
        assert f"<text>{HIDDEN}</text>" in out
        assert '{"a":"10.20.30.40"}' in out             # structurally redacted upstream
        assert 'content:"[hidden]"' in out              # CSS strings are rendered content

    def test_an_html_escaped_literal_is_caught(self):
        r = Redactor.for_documents({"network": {"cluster_name": "A&B Lab"}})
        assert HIDDEN in r.redact_html("<p>A&amp;B Lab</p>")

    def test_pre_bodies_are_content(self, red):
        assert HIDDEN in red.redact_html("<pre>/home/user/x\n</pre>")

    def test_whitespace_between_tags_collapses_only_when_asked(self):
        doc = "<td>\n        <b>x</b>\n    </td>"
        assert html_pass(doc, None) == doc
        assert html_pass(doc, None, collapse_space=True) == "<td>\n<b>x</b>\n</td>"


@pytest.mark.parametrize("secret", [
    r"\\nas\s\f", repr(r"\\nas\s\f"), "//host.example/share/file",
    "TCPIP::host.example.internal::5025::SOCKET", "git@github.com:group/repo.git@abc",
    "nfs:/export/share/q", "host-a:9510", "wiki.example.org/setup",
    "Primary device is host.example.internal", "host.example.private", "cluster_a queue",
])
def test_review_hidden_strings(secret):
    red = Redactor.for_documents({"extras": {"cluster_name": "CLUSTER_A"}})
    out = red.redact_text(secret)
    if secret.startswith("Primary device is "):
        assert out == "Primary device is [hidden]"
    elif secret == "cluster_a queue":
        assert out == "[hidden] queue"
    elif secret.startswith("'"):
        assert out == "'[hidden]'"
    else:
        assert out == HIDDEN


@pytest.mark.parametrize("ordinary", [
    "3.4.0.1", "quam_config.two_flux_gate.CZGateTwoFlux",
    "quam.components.pulses.SquarePulse", "#/qubits/qA1/xy", "cal.h5", "con1/2/1",
])
def test_review_surviving_strings(ordinary):
    assert Redactor.for_documents({}).redact_text(ordinary) == ordinary


def test_review_keys_cluster_and_frozen_literals():
    doc = {"host.example.internal": {r"E:\folder\cal.h5": 3},
           "extras": {"cluster_name": "CLUSTER_A"}, "opx": {"cluster": "CLUSTER_B"}}
    red = Redactor.for_documents(doc)
    before = red.literals
    out = red.redact_tree(doc)
    assert "host.example.internal" not in str(out) and "folder" not in str(out)
    assert out["extras"]["cluster_name"] == out["opx"]["cluster"] == HIDDEN
    red.redact_value("http://new.example.org/path")
    assert isinstance(red.literals, frozenset) and red.literals == before


def test_review_literals_precede_patterns():
    red = Redactor.for_documents({"network": {"cluster": r"D:\a\folder with spaces"}})
    assert red.redact_text(r"Source D:\a\folder with spaces") == "Source [hidden]"


def test_review_css_and_comments_preserve_structure():
    css = 'a.x{opacity:0.5;color:#fff;content:"host.example.internal";background:url(https://host.example.org/x)}'
    data = 'b{background:url("data:image/svg+xml;base64,AAAA")} '
    out = html_pass('<style>' + css + data + '</style><!-- host.example.internal -->', Redactor())
    assert out == '<style>a.x{opacity:0.5;color:#fff;content:"[hidden]";background:url([hidden])}' + data + '</style><!-- [hidden] -->'


def test_review_oserror_representation():
    exc = FileNotFoundError(2, "x", r"\\nas\s\f")
    assert "nas" not in Redactor().redact_text(str(exc))


def test_review_tokenizer_streams(monkeypatch):
    import quam_state_manager.core.report_redact as module
    class Tokens:
        def finditer(self, doc):
            return original.finditer(doc)
        def split(self, doc):
            raise AssertionError("whole-document split holds the GIL")
    original = module.HTML_TOKENS
    monkeypatch.setattr(module, "HTML_TOKENS", Tokens())
    assert module.html_pass('<p>x</p>', None) == '<p>x</p>'
