# CodeMirror 6 bundles (vendored)

Two pre-built, minified IIFEs. All bundled packages are MIT; their licenses
are in `LICENSE`. Neither is a core script.

- `cm6.min.js` exposes `window.SmCodeMirror` for the Json Tree "Edit as JSON"
  editor. Loaded only as the `cm6` page bundle on the Json Tree page
  (`page_bundles['explorer']` in `base.html`).
- `cm6-python.min.js` is the same editor with the Python language, for the
  pulse class editor. Loaded only in the `pulseclass` page bundle. Built from
  `tools/cm6/entry-python.js`; its packages and versions are the ones in
  `tools/cm6/package-lock.json`.

## Versions (exact, from `tools/cm6/package-lock.json`)

| Package | Version |
|---|---|
| @codemirror/state | 6.4.1 |
| @codemirror/view | 6.28.4 |
| @codemirror/commands | 6.6.0 |
| @codemirror/language | 6.10.2 |
| @codemirror/search | 6.5.6 |
| @codemirror/lint | 6.8.1 |
| @codemirror/lang-json | 6.0.1 |
| @lezer/common | 1.2.1 |
| @lezer/highlight | 1.2.0 |
| @lezer/lr | 1.4.1 |
| @lezer/json | 1.0.2 |
| crelt | 1.0.6 |
| style-mod | 4.1.2 |
| w3c-keyname | 2.2.8 |
| esbuild (build only) | 0.21.5 |

Transitive versions are pinned through `overrides` in `package.json`.

## Contents

JSON language, search with find/replace panel, fold gutter, history
(undo/redo), bracket matching, line numbers, lint gutter + `jsonParseLinter`,
and a light/dark theme whose colors read CSS custom properties
(`--cm-bg`, `--cm-fg`, `--cm-key`, `--cm-string`, `--cm-number`, ...) with
built-in fallbacks.

API: `SmCodeMirror.create(el, {doc, dark, onChange, readOnly, lint, extensions})`
returns `{view, getDoc, setDoc, setDark, setReadOnly, setDiagnostics,
openSearch, focus, destroy}`; `EditorView`, `EditorState`, `Compartment`,
`keymap`, and the full `state` / `view` namespaces are exposed for later stages.

## Size

`cm6.min.js`:

- raw: 374,429 bytes
- gzip -9: 121,353 bytes

## Rebuild

```
cd tools/cm6
npm ci
node build.mjs          # writes dist/cm6.min.js and dist/cm6-python.min.js
copy dist\cm6.min.js ..\..\quam_state_manager\web\static\vendor\cm6\
copy dist\cm6-python.min.js ..\..\quam_state_manager\web\static\vendor\cm6\
```

Do not commit `node_modules` or `dist`.
