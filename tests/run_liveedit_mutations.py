"""Revert each stress fix independently, require RED, restore in finally.

Run under the test environment; logs go to a caller-selected scratch directory.
Never edits fixtures or data files.
"""
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
WEB = 'quam_state_manager/web/'
PY = [sys.executable, '-m', 'pytest', 'tests/test_liveedit_stress.py', '-q', '-p', 'no:cacheprovider']
cases = [
 ('D1', WEB+'routes.py', '_freq_sync = _pj.get("freq_sync") is True', '_freq_sync = False', PY+['-k','cold_partner']),
 ('D2', WEB+'static/bulk-edit.js', '_applyCells(batches[i - 1], tr, true, seenGlobal, grp)', '_applyCells(_cells(tr).filter(_isDirty), tr, true, seenGlobal, grp)', ['node','tests/grid_editing_selfcheck.cjs']),
 ('D3', WEB+'static/app.js', 'var first = window._editSeqSeen === undefined && (!shown || shown === d.edit_seq);', 'var first = window._editSeqSeen === undefined;', ['node','tests/drift_banner_follow_selfcheck.cjs']),
 ('D4', WEB+'routes.py', 'seen_edit_seq = (request.values.get("seen_edit_seq") or "").strip()', 'seen_edit_seq = ""', PY+['-k','unseen_applied']),
 ('a-grid', WEB+'static/bulk-edit.js', "n + ' typed, not staged'", "n + ' unapplied edits'", PY+['-k','readability']),
 ('a-tray', WEB+'templates/_sync_control.html', ' staged, not applied', ' unapplied edits', PY+['-k','readability']),
 ('b-cancel', WEB+'static/bulk-edit.js', "c.value = c.getAttribute('data-orig');\n                                    _markCellDirty(c);", "/* reverted cancel */\n                                    _markCellDirty(c);", ['node','tests/grid_editing_selfcheck.cjs']),
 ('b-modal', WEB+'static/app.js', "if (Array.prototype.some.call(document.querySelectorAll('.fsp-card'), function (c) {\n            return c.closest('.ch-overlay').style.display !== 'none';\n        })) return;", '', ['node','tests/drift_banner_follow_selfcheck.cjs']),
 ('c-clip', WEB+'static/ui-kit.css', '.ui-live-pin { overflow: hidden; }', '.ui-live-pin { overflow: visible; }', PY+['-k','readability']),
 ('c-hydrate', WEB+'static/bulk-edit.js', 'coldPins.every', 'coldPins.filter', PY+['-k','readability']),
 ('d', WEB+'static/ui-kit.css', '.ui-live-tray > .auto-sync-wrap { order: -1; }', '.ui-live-tray > .auto-sync-wrap { order: 0; }', PY+['-k','readability']),
 ('e', WEB+'templates/_auto_sync_pill.html', '{{ _as.flushes }} landed', '{{ _as.flushes }}', PY+['-k','readability']),
 ('f', WEB+'static/ui-kit.css', 'min-width: calc(var(--ui-icon-btn) * 3)', 'min-width: 0', PY+['-k','readability']),
 ('g', WEB+'static/bulk-edit.js', 'if (cmp) return { tok: tok, isCmp: true', 'if (false) return { tok: tok, isCmp: true', ['node','tests/bulk_virt_server_selfcheck.cjs']),
 ('g-pair', WEB+'static/pair-edit.js', 'if (cmp) return { tok: tok, isCmp: true', 'if (false) return { tok: tok, isCmp: true', ['node','tests/pair_virt_server_selfcheck.cjs']),
 ('c-corner', WEB+'static/style.css', '.bulk-table thead th.bulk-corner { z-index: 5; }', '', PY+['-k','readability']),
 ('f-gap', WEB+'static/app.js', '_cellTextWidth(input, g) + 8;', '_cellTextWidth(input, g) + 4;', PY+['-k','readability']),
 ('f-mid', WEB+'static/app.js', 'if (g.mid > 0) b.style.top = g.mid + "px";', '', PY+['-k','readability']),
 ('f-raf', WEB+'static/app.js', 'var m = _cellBtnMid(b, input);', 'var m = 0;', PY+['-k','readability']),
 ('h', WEB+'static/sidebar-typeahead.js', 'text.split(/[^A-Za-z0-9_]+/).concat(text.split(/[^A-Za-z0-9]+/))', 'text.split(/[^A-Za-z0-9]+/)', ['node','tests/param_typeahead_selfcheck.cjs']),
]

def main():
    out = Path(sys.argv[1]); out.mkdir(parents=True, exist_ok=True)
    for name, relative, old, new, cmd in cases:
        target = ROOT / relative
        original = target.read_bytes()
        source = original.decode('utf-8')
        # Worktrees on Windows may contain CRLF.
        normalized = source.replace('\r\n', '\n')
        assert normalized.count(old) == 1, (name, normalized.count(old))
        try:
            target.write_text(normalized.replace(old,new), encoding='utf-8', newline='\n')
            p = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, encoding='utf-8', timeout=180,
                               env=dict(os.environ, PYTHONUTF8='1'))
            (out / (name+'.log')).write_text(p.stdout+'\n'+p.stderr, encoding='utf-8')
            assert p.returncode != 0, f'{name}: mutation survived'
            print(f'{name}: RED (exit {p.returncode})', flush=True)
        finally:
            target.write_bytes(original)

if __name__ == '__main__':
    main()
