// Single-global CodeMirror 6 bundle for the pulse class code pane.
// Python language, history, search, lint gutter (error lines) and a
// protected range (the template region the form owns) held by a changeFilter.
// Colors come from CSS custom properties so the page's light/dark tokens apply.
import { EditorState, Compartment, StateField, StateEffect, RangeSetBuilder, Annotation } from "@codemirror/state";
import {
  EditorView, Decoration, keymap, lineNumbers, highlightActiveLine, highlightActiveLineGutter,
  drawSelection, highlightSpecialChars,
} from "@codemirror/view";
import { defaultKeymap, history, historyKeymap, indentWithTab, undo, redo } from "@codemirror/commands";
import { bracketMatching, indentOnInput, syntaxHighlighting, HighlightStyle, indentUnit } from "@codemirror/language";
import { search, searchKeymap, highlightSelectionMatches, openSearchPanel } from "@codemirror/search";
import { lintGutter, lintKeymap, setDiagnostics } from "@codemirror/lint";
import { python } from "@codemirror/lang-python";
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
    ".cm-sm-protected": { backgroundColor: v("--cm-protected", dark ? "#1a2233" : "#f3f5f9") },
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
    { tag: [t.keyword, t.controlKeyword, t.definitionKeyword, t.moduleKeyword, t.operatorKeyword], color: v("--cm-keyword", dark ? "#d7a0ff" : "#7a2fb3") },
    { tag: [t.string, t.special(t.string)], color: v("--cm-string", dark ? "#9fd39b" : "#1d7a3a") },
    { tag: t.number, color: v("--cm-number", dark ? "#f0b878" : "#a14d00") },
    { tag: [t.bool, t.null], color: v("--cm-keyword", dark ? "#d7a0ff" : "#7a2fb3") },
    { tag: t.comment, color: v("--cm-comment", dark ? "#7a8394" : "#6b7383"), fontStyle: "italic" },
    { tag: [t.definition(t.variableName), t.function(t.definition(t.variableName)), t.className], color: v("--cm-key", dark ? "#8fbcff" : "#0b5cad") },
    { tag: t.invalid, color: v("--cm-invalid", "#d33") },
  ]);
}

// The protected range: {from, to} or null, mapped through every change.
const setProtect = StateEffect.define();
const fromSm = Annotation.define();
const protectField = StateField.define({
  create: () => null,
  update(value, tr) {
    for (const e of tr.effects) if (e.is(setProtect)) return e.value;
    if (value && tr.docChanged) {
      return { from: tr.changes.mapPos(value.from, -1), to: tr.changes.mapPos(value.to, 1) };
    }
    return value;
  },
});
const protectDecos = StateField.define({
  create: () => Decoration.none,
  update(_d, tr) {
    const r = tr.state.field(protectField);
    if (!r) return Decoration.none;
    const b = new RangeSetBuilder();
    const doc = tr.state.doc;
    const end = Math.min(r.to, doc.length);
    for (let pos = Math.min(r.from, doc.length); pos <= end;) {
      const line = doc.lineAt(pos);
      b.add(line.from, line.from, Decoration.line({ class: "cm-sm-protected" }));
      if (line.to >= end) break;
      pos = line.to + 1;
    }
    return b.finish();
  },
  provide: (f) => EditorView.decorations.from(f),
});
const protectFilter = EditorState.changeFilter.of((tr) => {
  const r = tr.startState.field(protectField, false);
  if (!r) return true;
  return [r.from, r.to];
});

function create(el, opts) {
  opts = opts || {};
  const themeC = new Compartment();
  const exts = [
    lineNumbers(), highlightActiveLineGutter(), highlightSpecialChars(), history(),
    drawSelection(), indentOnInput(), bracketMatching(), highlightActiveLine(),
    highlightSelectionMatches(), search({ top: true }), lintGutter(),
    python(), indentUnit.of("    "),
    keymap.of([...defaultKeymap, ...searchKeymap, ...historyKeymap, ...lintKeymap, indentWithTab]),
    themeC.of([makeTheme(!!opts.dark), syntaxHighlighting(makeHighlight(!!opts.dark))]),
    protectField, protectDecos, protectFilter,
  ];
  if (typeof opts.onChange === "function") {
    exts.push(EditorView.updateListener.of((u) => {
      if (u.docChanged && !u.transactions.some((tr) => tr.annotation(fromSm))) opts.onChange(u.state.doc.toString(), u);
    }));
  }
  const view = new EditorView({ state: EditorState.create({ doc: opts.doc || "", extensions: exts }), parent: el });
  return {
    view,
    getDoc: () => view.state.doc.toString(),
    // SM's own load (a file read, a form rewrite): never filtered, never reported as a user edit
    setDoc: (text) => view.dispatch({ changes: { from: 0, to: view.state.doc.length, insert: text },
      filter: false, annotations: fromSm.of(true) }),
    setProtected: (range) => view.dispatch({ effects: setProtect.of(range && range.to > range.from ? { from: range.from, to: range.to } : null) }),
    getProtected: () => view.state.field(protectField),
    setDark: (dark) => view.dispatch({ effects: themeC.reconfigure([makeTheme(!!dark), syntaxHighlighting(makeHighlight(!!dark))]) }),
    // list: [{line (1-based), message, severity}]
    setErrors: (list) => {
      const doc = view.state.doc;
      const diags = (list || []).filter((d) => d && d.line >= 1 && d.line <= doc.lines).map((d) => {
        const ln = doc.line(d.line);
        return { from: ln.from, to: ln.to, severity: d.severity || "error", message: String(d.message || "") };
      });
      view.dispatch(setDiagnostics(view.state, diags));
    },
    openSearch: () => openSearchPanel(view),
    focus: () => view.focus(),
    destroy: () => view.destroy(),
  };
}

window.SmCodePython = {
  version: "6",
  create,
  commands: { undo, redo },
};
