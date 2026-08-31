# docs/149 — the config picker gets the explorer, files become pickable, and the dialog learns the keyboard (2026-09-01, user)

Four asks in one report, all on the first-run landing's QUAlibrate config
picker: *"분명히 첫 메인화면 폴더 explorer 기능 넣지 않았나?"* (the Browse
Folders dialog existed for state/dataset pickers but the config picker only
had a bare text input), *toml 파일도 선택 가능하게* (folder OR file), *키보드
로도 exploring*, and *"Check가 너무 길잖아"* (button balance).

## Browse on the config picker, in a `config` mode

`_qualibrate_locate.html` gains a **Browse…** button opening the SAME shared
explorer (`openFolderBrowser`) with a new `kind="config"`; Select auto-submits
the form, so a pick runs Check immediately. Server (`/browse?kind=config`):

- **dot-directories are SHOWN** — the default filter hid `.qualibrate`, the
  single folder this picker exists to find;
- **`files`**: the folder's `*.toml` files as selectable rows (the
  `QUALIBRATE_CONFIG_FILE` env var's dir-or-file semantics — the server side
  already accepted a file, `_normalize_config_input` drops to its parent);
- **`config_dirs`** marks children holding a `config.toml` (`is-config`
  highlight, same pattern as `dataset_dirs`), **`has_config`** badges the
  current folder.

A **file row is a pick, not a place**: clicking fills the selected path
(highlighted row) without navigating; Select/Enter confirms it into the
target input. And `/browse` with an existing FILE path now lists its parent
**without** the "was not found" missing note (that note was a lie about a
file that exists — the old `test_file_path_lands_at_parent_with_marker` pin
was amended intent-preserved).

## Keyboard navigation (every mode, not just config)

Arrows move a highlight over the rows (`.. (up)` included), Enter descends
into a folder / confirms a highlighted file (or, with no highlight, selects
the current folder), ArrowRight descends, ArrowLeft/Backspace go up,
Home/End jump, and **type-ahead** jumps to the first matching row —
dot-prefix-insensitive, so typing `qual` finds `.qualibrate`. The readonly
selected-path input (where `showModal` lands focus) is list-transparent;
the new-folder input keeps its own keys. Escape stays native.

**The bug real Chrome caught and jsdom hid**: the first cut registered the
keydown listener in a load-time IIFE — but app.js evaluates in `<head>`,
before base.html's `<dialog>` exists, so the registration silently bound
nothing. Every key was dead in the real browser while the harness (which
builds the dialog markup BEFORE evaluating app.js) passed everything. The
binding is now lazy (`_kbBind()` from `openFolderBrowser`), and the harness
gained a **late-dialog world** (`makeWorld({lateDialog: true})`) that
reproduces the real load order — G14 pins it, and goes red if the lazy call
is removed. Same standing-rule family as the docs/78 CSS-global and docs/125
realm lessons: a harness that pre-builds what production builds late will
vouch for code that never ran.

## Button balance

Pico gives `[type=submit]` `width:100%` — Check rendered as a full-width bar
next to the auto-width Scan button. `.locate-form .btn-sm` now pins
`width:auto` for all three (Browse / Check / Scan). CDP-measured: 60px /
177px buttons on an 830px form, no more bar.

Verified end-to-end in real Chrome against the real home: Browse → navigate
to the user profile → `.qualibrate` visible AND `is-config`-marked →
type-ahead `qual` → Enter → `config.toml` file row + badge → keyboard pick →
input filled, dialog closed, auto-Check rendered "✓ … 32 projects, active:
CQT_20Q … Use this folder". Pinned by `TestConfigKind` in
`test_browse_route.py`, G12/G13/G14 in `folder_browser_selfcheck.cjs`.
Mutations red ×4 (listener registration killed, file rows dropped, dot-dirs
re-hidden, lazy-bind call removed).
