# docs/183 — The inspector's inline edit sent nothing at all

2026-09-12. Found by the write-path stress round's Ctrl+Z lane (docs/179's
sibling), then reproduced independently on a copy of the customer's KRISS 5Q
chip in real headless Chrome before anything was touched.

## What a person saw

Open the qubit or pair inspector, click a value, type, press Enter.

- **zero POST requests**
- the tray stays at `data-change-count="0"`
- no toast, no error
- the field shows the old value again on the next render

Both inspectors, and both ways of reaching them — the full page and the htmx
partial you get by clicking a row, which is the path people actually use. The
same with Tab, and the same on `/pair/…`.

Measured before the fix, on `qubits.q1.anharmonicity`:

```
posts: []   tray 0 -> 0
EvalError: Evaluating a string as JavaScript violates the following Content
Security Policy directive because 'unsafe-eval' is not an allowed source of
script: script-src 'self' 'unsafe-inline'
```

## The cause, which this project had already written down twice

The form carried

```
hx-vals='js:{freq_sync: …, expect_chip: window.__chipToken || ""}'
```

htmx compiles a `js:` value with `new Function`. This app's CSP is
`script-src 'self' 'unsafe-inline'` — **no `unsafe-eval`** — so htmx throws
while evaluating it and aborts the request. The submit never becomes a request.

`_state_review.html:51` carries a comment saying exactly this, and docs/120 ②
fixed the identical class for every `hx-on::after-request` handler in the app.
It survived here because nothing was looking.

### Why no test caught it

The jsdom harnesses do not enforce CSP, and a synthetic
`form.dispatchEvent(new Event('submit'))` — which is what a harness does —
posts perfectly well. Only a real browser, with the real header, and a real
keystroke can see this one. `pulses_commit_selfcheck.cjs` and
`test_pulses_commit.py` were both green throughout.

## The half that was actually unsafe

Two things rode the dead attribute:

| | |
|---|---|
| `freq_sync` | the toolbar's f01↔RF mirror toggle — a browser preference, so the server's default ON simply always won |
| `expect_chip` | the docs/120 **chip-identity gate** |

The second never reached the server, so the inspector's chip-identity gate was
**off** — a stale tab's edit was ungated. The visible symptom (nothing is
written) is the safe direction; the invisible one was not.

## The fix

docs/120 ②'s own pattern: a data attribute names what to inject, and **one
delegated listener** injects it. Greppable, no mini-language, no eval.

```
data-inject="chip-freq"
```

```js
document.addEventListener("htmx:configRequest", function (evt) {
    var host = evt.detail.elt.closest('[data-inject~="chip-freq"]');
    if (!host) return;
    evt.detail.parameters.freq_sync = (window.freqSyncFlag ? window.freqSyncFlag() : "1");
    evt.detail.parameters.expect_chip = window.__chipToken || "";
});
```

Its own listener rather than a branch in the existing one: that handler rewrites
`evt.detail.path` and **returns early for everything else**, and two unrelated
concerns in one function is how the next one gets missed. Pinned.

## Verification

- `tests/stress_inspector_edit.cjs` — the same driver that reproduced it:
  **0/5 before, 6/6 after**, on a copy of the customer's chip, with real CDP
  key events. Every path posts and the tray increments: `/qubit/q1/edit` from
  the full page, `/qubit/q1/edit` from the clicked-row partial,
  `/pair/q1-2/edit` from the pair inspector.
- `tests/test_inspector_inline_edit.py` — 14 pins, including the structural
  guard: **no template may use an `hx-vals` `js:` value**, the CSP really
  forbids eval, and the header really is sent (without which the guard would be
  cargo cult). Plus the gate that was hiding behind the bug: a stale
  `expect_chip` is refused 409 on both doors.
- `tests/inspector_inject_selfcheck.cjs` — the injector **executed**: it fires
  from the input htmx actually reports (not the form), leaves unmarked requests
  alone, carries `freq_sync=0` when the toggle is off, and injects `""` rather
  than the string `"undefined"` for an absent token — which the server would
  otherwise compare against the real chip and refuse.

### Three things the driver got wrong first, all recorded

- **`Input.dispatchKeyEvent` with `text` on both `keyDown` and `char`** types
  every character twice.
- **Declining the `beforeunload` dialog cancels the navigation.** Two later
  phases reported "no row to click" and "no pair given" — they were still on
  the previous page. A `beforeunload` is the *browser* asking; an app `confirm`
  is the *app* asking, and only the second is worth recording.
- **An unscoped `input[name="dot_path"]` query found a leftover field from the
  previous page**, so the "pair inspector" check quietly re-tested the qubit
  one. Its POST going to `/qubit/q1/edit` is what gave it away, and the pin now
  asserts the route *and* the path prefix.

### And the pin tripped on its own comment

The sixth time in this session. The fix's note and `_state_review.html`'s older
one both *quote* the forbidden attribute in order to warn about it, and a raw
grep flags the warning as the offence. The scan strips Jinja and HTML comments
first — and a second pin makes sure the stripper has not become so eager that
deleting the scan entirely would look just as green.
