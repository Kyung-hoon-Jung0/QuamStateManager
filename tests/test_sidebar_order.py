"""Sidebar order behavior and stable markup identities."""
import re
import shutil
import subprocess
from html.parser import HTMLParser
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


class NavParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.stack = []
        self.keys = []
        self.groups = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == 'ul':
            self.stack.append(attrs.get('data-nav-group'))
            if self.stack[-1]:
                self.groups.append(self.stack[-1])
        if tag == 'li' and self.stack and self.stack[-1]:
            self.keys.append(attrs.get('data-nav-key'))

    def handle_endtag(self, tag):
        if tag == 'ul' and self.stack:
            self.stack.pop()


def test_markup_identities_and_loading():
    base = (ROOT / 'quam_state_manager/web/templates/base.html').read_text(encoding='utf-8')
    nav = base[base.index('<nav class="sidebar-nav">'):base.index('            <hr>\n', base.index('<nav class="sidebar-nav">'))]
    # Loading/empty notes are not menu entries.
    nav = re.sub(r'<li><span.*?</li>', '', nav, flags=re.S)
    parser = NavParser()
    parser.feed(nav)
    assert len(parser.keys) >= 35
    assert all(parser.keys)
    assert len(parser.keys) == len(set(parser.keys))
    assert len(parser.groups) == len(set(parser.groups))
    assert base.index("asset_url('sidebar-order.js')") < base.index('</head>')
    assert base.index("asset_url('sidebar-order.js')") < base.index('<main')
    assert 'id="reset-menu-order">Reset menu order</button>' in base
    fragment = (ROOT / 'quam_state_manager/web/templates/_qualibrate_subnav.html').read_text(encoding='utf-8')
    assert 'data-nav-key="project:{{ p.name }}"' in fragment


def test_sidebar_order_selfcheck():
    node = shutil.which('node')
    assert node, 'Node is required for the sidebar order selfcheck'
    result = subprocess.run([node, str(ROOT / 'tests/sidebar_order_selfcheck.cjs')], cwd=ROOT,
                            capture_output=True, text=True, encoding='utf-8', timeout=180)
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.count('ok - ') >= 12
