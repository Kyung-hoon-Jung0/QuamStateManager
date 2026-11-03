// Single-global CodeMirror 6 bundle for the Json Tree editor (D-21).
// Colors come from CSS custom properties so the page's light/dark tokens apply.
import { EditorState, Compartment } from "@codemirror/state";
import * as stateNs from "@codemirror/state";
import {
  EditorView, keymap, lineNumbers, highlightActiveLine, highlightActiveLineGutter,
  drawSelection, highlightSpecialChars,
} from "@codemirror/view";
import * as viewNs from "@codemirror/view";
import { defaultKeymap, history, historyKeymap, indentWithTab, undo, redo } from "@codemirror/commands";
import {
  foldGutter, foldKeymap, bracketMatching, indentOnInput, syntaxHighlighting,
  HighlightStyle, foldAll, unfoldAll,
} from "@codemirror/language";
import { search, searchKeymap, highlightSelectionMatches, openSearchPanel } from "@codemirror/search";
import { linter, lintGutter, lintKeymap, setDiagnostics, forEachDiagnostic } from "@codemirror/lint";
import { json, jsonParseLinter } from "@codemirror/lang-json";
import { tags as t } from "@lezer/highlight";

const v = (name, fb) => `var(${name}, ${fb})`;

function makeTheme(dark) {
  return EditorView.theme({
    "&": { color: v("--cm-fg", dark ? "#d7dce5" : "#1f2430"), backgroundColor: v("--cm-bg", dark ? "#13171f" : "#ffffff"), fontSize: "0.85rem", height: "100%" },
    ".cm-content": { fontFamily: v("--cm-font", "ui-monospace, Consolas, monospace"), caretColor: v("--cm-caret", dark ? "#9cc4ff" : "#0172ad") },
    ".cm-cursor, .cm-dropCursor": { borderLeftColor: v("--cm-caret", dark ? "#9cc4ff" : "#0172ad") },
    "&.cm-focused .cm-selectionBackground, .cm-selectionBackground, ::selection": { backgroundColor: v("--cm-selection", dark ? "#2b3d5a" : "#cfe2ff") },
    ".cm-gutters": { backgroundColor: v("--cm-gutter-bg", dark ? "#181d27" : "#f4f6f9"), color: v("--cm-gutter-fg", dark ? "#7a8394" : "#8a93a3"), border: "none", borderRight: "1px solid " + v("--cm-border", dark ? "#2a3140" : "#dde2ea") },
    ".cm-activeLine": { backgroundColor: v("--cm-active-line", dark ? "#1b2230" : "#f2f6fc") },
    ".cm-activeLineGutter": { backgroundColor: v("--cm-active-line", dark ? "#1b2230" : "#eaf0f8") },
    "&.cm-focused .cm-matchingBracket": { backgroundColor: v("--cm-bracket", dark ? "#33507a" : "#d6e6ff"), outline: "none" },
    ".cm-searchMatch": { backgroundColor: v("--cm-match", dark ? "#5a4a1c" : "#fff1b8") },
    ".cm-searchMatch.cm-searchMatch-selected": { backgroundColor: v("--cm-match-sel", dark ? "#806a24" : "#ffd75e") },
    ".cm-panels": { backgroundColor: v("--cm-gutter-bg", dark ? "#181d27" : "#f4f6f9"), color: v("--cm-fg", dark ? "#d7dce5" : "#1f2430") },
    ".cm-panels input, .cm-panels button": { fontSize: "0.8rem" },
    ".cm-tooltip": { backgroundColor: v("--cm-bg", dark ? "#13171f" : "#ffffff"), border: "1px solid " + v("--cm-border", dark ? "#2a3140" : "#dde2ea") },
  }, { dark });
}

function makeHighlight(dark) {
  return HighlightStyle.define([
    { tag: t.propertyName, color: v("--cm-key", dark ? "#8fbcff" : "#0b5cad") },
    { tag: t.string, color: v("--cm-string", dark ? "#9fd39b" : "#1d7a3a") },
    { tag: t.number, color: v("--cm-number", dark ? "#f0b878" : "#a14d00") },
    { tag: [t.bool, t.null], color: v("--cm-keyword", dark ? "#d7a0ff" : "#7a2fb3") },
    { tag: t.invalid, color: v("--cm-invalid", "#d33") },
  ]);
}

function create(el, opts) {
  opts = opts || {};
  const themeC = new Compartment();
  const roC = new Compartment();
  const exts = [
    lineNumbers(), highlightActiveLineGutter(), highlightSpecialChars(), history(),
    foldGutter(), drawSelection(), indentOnInput(), bracketMatching(), highlightActiveLine(),
    highlightSelectionMatches(), search({ top: true }), lintGutter(),
    json(),
    keymap.of([...defaultKeymap, ...searchKeymap, ...historyKeymap, ...foldKeymap, ...lintKeymap, indentWithTab]),
    themeC.of([makeTheme(!!opts.dark), syntaxHighlighting(makeHighlight(!!opts.dark))]),
    roC.of([EditorState.readOnly.of(!!opts.readOnly), EditorView.editable.of(!opts.readOnly)]),
  ];
  if (opts.lint) exts.push(linter(jsonParseLinter()));
  if (typeof opts.onChange === "function") {
    exts.push(EditorView.updateListener.of((u) => { if (u.docChanged) opts.onChange(u.state.doc.toString(), u); }));
  }
  if (opts.extensions) exts.push(opts.extensions);
  const view = new EditorView({ state: EditorState.create({ doc: opts.doc || "", extensions: exts }), parent: el });
  return {
    view,
    getDoc: () => view.state.doc.toString(),
    setDoc: (text) => view.dispatch({ changes: { from: 0, to: view.state.doc.length, insert: text } }),
    setDark: (dark) => view.dispatch({ effects: themeC.reconfigure([makeTheme(!!dark), syntaxHighlighting(makeHighlight(!!dark))]) }),
    setReadOnly: (ro) => view.dispatch({ effects: roC.reconfigure([EditorState.readOnly.of(!!ro), EditorView.editable.of(!ro)]) }),
    setDiagnostics: (list) => view.dispatch(setDiagnostics(view.state, list || [])),
    openSearch: () => openSearchPanel(view),
    focus: () => view.focus(),
    destroy: () => view.destroy(),
  };
}

window.SmCodeMirror = {
  version: "6",
  create,
  EditorView, EditorState, Compartment, keymap,
  state: stateNs, view: viewNs,
  commands: { undo, redo },
  language: { foldAll, unfoldAll },
  search: { openSearchPanel },
  lint: { linter, lintGutter, setDiagnostics, forEachDiagnostic, jsonParseLinter },
};
