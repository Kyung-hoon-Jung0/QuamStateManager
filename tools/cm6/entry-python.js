// Single-global CodeMirror 6 bundle for the pulse class code pane.
// Python language, history, search, lint gutter (error lines) and a
// protected range (the template region the form owns) held by a changeFilter.
// Colours come from ui-kit tokens so the page's light/dark themes apply.
import { EditorState, Compartment, StateField, StateEffect, RangeSetBuilder, Annotation, Transaction } from "@codemirror/state";
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

// Every colour is a ui-kit token (ui-kit.css :root, --ui-code-*), so the page's
// theme applies and a theme switch recolours the editor without a rebuild.
const v = (name) => `var(${name})`;

function makeTheme(dark) {
  return EditorView.theme({
    "&": { color: v("--ui-text"), backgroundColor: v("--ui-surface-0"), fontSize: v("--ui-fs-sm"), height: "100%" },
    ".cm-content": { fontFamily: v("--ui-font-mono"), caretColor: v("--ui-accent") },
    ".cm-cursor, .cm-dropCursor": { borderLeftColor: v("--ui-accent") },
    "&.cm-focused .cm-selectionBackground, .cm-selectionBackground, ::selection": { backgroundColor: v("--ui-code-selection") },
    ".cm-gutters": { backgroundColor: v("--ui-surface-2"), color: v("--ui-text-3"), border: "none", borderRight: "1px solid " + v("--ui-border") },
    ".cm-activeLine": { backgroundColor: v("--ui-code-active-line") },
    ".cm-activeLineGutter": { backgroundColor: v("--ui-code-active-line") },
    ".cm-sm-protected": { backgroundColor: v("--ui-code-protected") },
    "&.cm-focused .cm-matchingBracket": { backgroundColor: v("--ui-code-selection"), outline: "none" },
    ".cm-searchMatch": { backgroundColor: v("--ui-warn-bg") },
    ".cm-searchMatch.cm-searchMatch-selected": { backgroundColor: v("--ui-warn-border") },
    ".cm-panels": { backgroundColor: v("--ui-surface-2"), color: v("--ui-text") },
    ".cm-panels input, .cm-panels button": { fontSize: v("--ui-fs-xs") },
    ".cm-tooltip": { backgroundColor: v("--ui-surface-1"), color: v("--ui-text"), border: "1px solid " + v("--ui-border") },
  }, { dark });
}

function makeHighlight() {
  return HighlightStyle.define([
    { tag: [t.keyword, t.controlKeyword, t.definitionKeyword, t.moduleKeyword, t.operatorKeyword], color: v("--ui-code-keyword") },
    { tag: [t.string, t.special(t.string)], color: v("--ui-code-string") },
    { tag: t.number, color: v("--ui-code-number") },
    { tag: [t.bool, t.null], color: v("--ui-code-keyword") },
    { tag: t.comment, color: v("--ui-code-comment"), fontStyle: "italic" },
    { tag: [t.definition(t.variableName), t.function(t.definition(t.variableName)), t.className], color: v("--ui-code-name") },
    { tag: t.invalid, color: v("--ui-danger-text") },
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
// The span a change may not touch: the marker lines plus the line breaks that
// bound them, so typing at the end of the END marker line (or deleting the
// break before BEGIN / after END) cannot join text onto a marker. A change
// that only borders this span (end of the line before, start of the line
// after) passes.
const NL = String.fromCharCode(10);
function guardSpan(doc, r) {
  let from = Math.max(0, Math.min(r.from, doc.length));
  let to = Math.max(from, Math.min(r.to, doc.length));
  if (from > 0 && doc.sliceString(from - 1, from) === NL) from -= 1;
  if (to < doc.length && doc.sliceString(to, to + 1) === NL) to += 1;
  return { from, to };
}
const protectFilter = EditorState.changeFilter.of((tr) => {
  const r = tr.startState.field(protectField, false);
  if (!r) return true;
  const g = guardSpan(tr.startState.doc, r);
  return [g.from, g.to];
});
// A marker at the very start or end of the document has no line break to
// shield it: an insertion right at that edge would still glue onto the
// marker, so such a transaction is dropped.
const protectEdges = EditorState.transactionFilter.of((tr) => {
  if (!tr.docChanged) return tr;
  const r = tr.startState.field(protectField, false);
  if (!r) return tr;
  const doc = tr.startState.doc;
  const g = guardSpan(doc, r);
  let bad = false;
  tr.changes.iterChanges((fromA, toA, _fb, _tb, ins) => {
    if (fromA !== toA || ins.length === 0) return;
    if ((fromA === g.to && g.to === doc.length && r.to >= doc.length) || (fromA === g.from && g.from === 0 && r.from <= 0)) bad = true;
  });
  return bad ? [] : tr;
});

function create(el, opts) {
  opts = opts || {};
  const themeC = new Compartment();
  const historyC = new Compartment();
  // The guard (filters) sits apart from the range: unlocking drops the guard
  // while the range keeps following every change, so a later lock is exact.
  const guardC = new Compartment();
  const exts = [
    lineNumbers(), highlightActiveLineGutter(), highlightSpecialChars(), historyC.of(history()),
    drawSelection(), indentOnInput(), bracketMatching(), highlightActiveLine(),
    highlightSelectionMatches(), search({ top: true }), lintGutter(),
    python(), indentUnit.of("    "), EditorView.lineWrapping,
    keymap.of([...defaultKeymap, ...searchKeymap, ...historyKeymap, ...lintKeymap, indentWithTab]),
    themeC.of([makeTheme(!!opts.dark), syntaxHighlighting(makeHighlight())]),
    protectField, protectDecos, guardC.of([protectFilter, protectEdges]),
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
    // SM's own load (a file read, a form rewrite): never filtered, never
    // reported as a user edit, and never undoable. The undo history starts
    // fresh, so Ctrl+Z can only take back what the user typed after the load.
    // The old protected range does not map onto a new text (it would stretch
    // over the whole document): it is cleared, and the caller sets the new one.
    setDoc: (text) => {
      view.dispatch({ changes: { from: 0, to: view.state.doc.length, insert: text },
        effects: setProtect.of(null),
        filter: false, annotations: [fromSm.of(true), Transaction.addToHistory.of(false)] });
      view.dispatch({ effects: historyC.reconfigure([]) });
      view.dispatch({ effects: historyC.reconfigure(history()) });
    },
    setProtected: (range) => view.dispatch({ effects: setProtect.of(range && range.to > range.from ? { from: range.from, to: range.to } : null) }),
    getProtected: () => view.state.field(protectField),
    setGuard: (on) => view.dispatch({ effects: guardC.reconfigure(on ? [protectFilter, protectEdges] : []) }),
    setDark: (dark) => view.dispatch({ effects: themeC.reconfigure([makeTheme(!!dark), syntaxHighlighting(makeHighlight())]) }),
    isDark: () => view.state.facet(EditorView.darkTheme),
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
