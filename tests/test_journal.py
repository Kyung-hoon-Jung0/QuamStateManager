"""docs/172: the calibration journal -- plain .md files, a whitelist renderer.

The agent writes this text and SM renders it in the app. Two properties are
pinned hard: nothing the agent (or a run's output) puts in the file can
become markup, and the two SM-specific tokens (a run number, a dotted state
path) become the links that make the journal worth opening in SM at all.
"""

from __future__ import annotations

import json

import pytest

from quam_state_manager.core import journal


class TestStorage:
    def test_default_root_is_under_the_instance(self, tmp_path):
        assert journal.root(tmp_path) == tmp_path / "journal"

    def test_a_chosen_folder_wins_and_blank_restores_the_default(self, tmp_path):
        vault = tmp_path / "ObsidianVault"
        assert journal.set_root(tmp_path, str(vault)) == vault
        assert vault.is_dir()
        assert journal.root(tmp_path) == vault
        journal.set_root(tmp_path, "")
        assert journal.root(tmp_path) == tmp_path / "journal"

    def test_append_creates_one_file_per_chip_per_day_with_a_title(self, tmp_path):
        rec = journal.append(tmp_path, "PJ 20Q", "set q1 amplitude", kind="agent",
                             reason="Rabi was left-biased", run_id=12, paths=["qubits.q1.xy.operations.x180.amplitude"])
        text = journal.read(tmp_path, "PJ 20Q")
        assert text.startswith("# PJ 20Q — ")
        assert "`agent` set q1 amplitude · run #12 · `qubits.q1.xy.operations.x180.amplitude`" in text
        assert "  - because: Rabi was left-biased" in text
        assert rec["file"].endswith(".md") and "PJ_20Q" in rec["file"], "chip names are made filesystem-safe"
        assert journal.list_days(tmp_path, "PJ 20Q") == [rec["ts"][:10]]
        assert journal.list_chips(tmp_path) == ["PJ_20Q"]

    def test_a_second_append_does_not_repeat_the_title(self, tmp_path):
        journal.append(tmp_path, "c", "one", kind="hook")
        journal.append(tmp_path, "c", "two", kind="hook")
        text = journal.read(tmp_path, "c")
        assert text.count("# c — ") == 1
        assert text.index("one") < text.index("two")

    def test_multiline_text_is_indented_under_its_bullet(self, tmp_path):
        journal.append(tmp_path, "c", "first line\nsecond line", kind="human")
        assert "\n  second line\n" in journal.read(tmp_path, "c")

    def test_unknown_kind_falls_back_to_agent(self, tmp_path):
        assert journal.append(tmp_path, "c", "x", kind="robot")["kind"] == "agent"

    def test_author_kinds_are_kept_and_stamped_on_the_entry(self, tmp_path):
        """docs/173 S8: a line names its author, and the renderer stamps it so the
        page can style by_claude / by_codex / unknown apart."""
        for k in ("by_claude", "by_codex", "unknown"):
            assert journal.append(tmp_path, "c", f"{k} did it", kind=k)["kind"] == k
        out = journal.render(journal.read(tmp_path, "c"))
        assert 'data-author="by_claude"' in out and 'data-author="by_codex"' in out and 'data-author="unknown"' in out

    def test_agent_says_defaults_on(self, tmp_path):
        """docs/173 S8: the user's decision -- ON by default, but LABELLED."""
        assert journal.settings(tmp_path)["agent_says"] is True
        assert journal.set_agent_says(tmp_path, False) is False
        assert journal.settings(tmp_path)["agent_says"] is False and journal.settings(tmp_path)["claude_says"] is False


class TestRendererWhitelist:
    def test_html_never_passes_through(self):
        out = journal.render("hello <script>alert(1)</script> <img src=x onerror=y> & done")
        assert "<script" not in out and "<img" not in out
        assert "&lt;script&gt;" in out and "&amp; done" in out

    def test_html_inside_code_and_headings_and_tables_is_escaped_too(self):
        out = journal.render("# <b>t</b>\n\n`<i>x</i>`\n\n| a | b |\n|---|---|\n| <u>1</u> | 2 |\n")
        assert "<b>" not in out and "<i>" not in out and "<u>" not in out
        assert "<h1>" in out and "<table>" in out and "<td>" in out

    def test_only_http_and_relative_links_are_links(self):
        out = journal.render("[ok](https://example.org/x) [rel](/datasets) [bad](javascript:alert(1))")
        assert 'href="https://example.org/x"' in out and 'href="/datasets"' in out
        assert "javascript:" not in out.replace("&#39;", "'").split("href=")[-1] or 'href="javascript' not in out

    def test_a_run_number_becomes_a_run_link(self):
        out = journal.render("ran power_rabi → run #2711 looks clean")
        assert 'href="/dataset/by-run/2711"' in out and 'class="jr-run"' in out
        assert 'hx-target="#table-pane"' in out

    def test_a_backticked_dot_path_becomes_a_path_token(self):
        out = journal.render("changed `qubits.q1.xy.operations.x180.amplitude` and `python -m x`")
        assert 'class="jr-path" data-path="qubits.q1.xy.operations.x180.amplitude"' in out
        assert "<code>python -m x</code>" in out, "a command is plain code, not a path"

    def test_a_hash_inside_a_word_or_url_is_not_a_run(self):
        out = journal.render("issue/#12 and C#7 stay put; #99 links")
        assert out.count('class="jr-run"') == 1 and 'by-run/99' in out

    def test_a_hash_inside_a_link_href_stays_in_the_href(self):
        """review R9: a #N in a link's URL was turned into a NESTED <a> inside the
        href. The #run linker must never fire inside an anchor _LINK already built."""
        out = journal.render("see [z](/a?x=#123) and separately #456")
        assert 'href="/a?x=#123"' in out and 'href="/a?x=<a' not in out, out
        assert out.count("<a ") == out.count("</a>"), "every anchor is closed exactly once"
        assert out.count('class="jr-run"') == 1 and 'by-run/456' in out, "a bare #N outside a link still links"

    def test_the_entry_bullets_carry_their_time(self):
        out = journal.render("- **02:13:44** `hook` ran `05_power_rabi`\n  - because: q1 rabi\n- plain bullet\n")
        assert '<li class="jr-entry" data-time="02:13:44"' in out and 'data-author="hook"' in out  # docs/173 S8: the author is stamped on the entry
        assert out.count("<li") == 3 and "<ul>" in out
        assert out.count("<ul>") == 2, "the because-line is a nested list"

    def test_fenced_code_blockquote_rule_and_paragraphs(self):
        out = journal.render("para one\nstill one\n\n```py\nx = 1 < 2\n```\n\n> quoted\n\n---\n")
        assert "<p>para one still one</p>" in out
        assert '<pre><code class="lang-py">x = 1 &lt; 2</code></pre>' in out
        assert "<blockquote>quoted</blockquote>" in out and "<hr>" in out

    def test_bold_italic_and_wikilinks(self):
        out = journal.render("**bold** *it* [[Chip Notes|notes]] [[Plain]]")
        assert "<strong>bold</strong>" in out and "<em>it</em>" in out
        assert '<span class="jr-wiki">notes</span>' in out and '<span class="jr-wiki">Plain</span>' in out

    def test_round_trip_of_a_real_entry(self, tmp_path):
        journal.append(tmp_path, "c", "set q1 amp 0.31->0.29", reason="run #12 <b>left-biased</b>", run_id=12,
                       paths=["qubits.q1.xy.operations.x180.amplitude"])
        out = journal.render(journal.read(tmp_path, "c"))
        assert 'by-run/12' in out and 'data-path="qubits.q1.xy.operations.x180.amplitude"' in out
        assert "<b>" not in out and "&lt;b&gt;left-biased&lt;/b&gt;" in out
