# docs/182 — The words a person typed, in the box that completes for you

2026-09-11, customer, on-site, twice in one message:

> 우리 검색어 창에 지금 타이핑을 하면 저절로 뜨는데... 이거! 제발 data tag랑
> note에 사용자가 기재한 단어들도 넣어달라고 함!!!! 다만, 검색 pop up할때 뜨는건
> run 번호: tag 이름 (혹은 note) 이렇게 뜨도록. note는 내용이 다 담기게 하는게
> 아니고 그냥 note (검색어 ...) 그냥 이렇게 compact하게.
>
> 그리고 dataset page에서는 별도로 Datasets 제목 아래, experiments, sort 버튼
> 사이에 버튼 따로 tags라고 만들어서 tag도 제발 넣어달라고 함!!!

## ① The vocabulary

The grammar could always **find** them — `tag:flagged` and `note:todo` have been
in the search help for a long time. What was missing is that **you had to
already know the word**. Every other vocabulary the box completes from is
machine-generated (node names, parameter keys); the tags and notes are the only
words in the archive a *person* chose, which makes them exactly the ones worth
being reminded of.

### Read from the file, never through a store

`core/tag_vocab.py` reads `quashboard_tags.json` **directly**. That file is the
source of truth — `DatasetStore._load_tags` reads exactly it — and costs one
`open` per data folder. Going through a store instead would let a **keystroke**
trigger the cold run scan docs/170 spent a whole round bounding (31.6 s at the
customer's share latency). A vocabulary must never be able to do that, and
`test_it_never_builds_a_dataset_store` pins it.

The version is the tag files' size+mtime, **not** the workspace version: a tag is
typed without the archive changing at all, and a note edited between two polls
has to reach the box. `?v=` then makes the re-check one comparison.

### The rows, in the customer's format

```
#12: flagged            tag · 2 runs
#11: flagged            tag · 2 runs
#12: note (fridge)      note
```

A note's **content** never enters a row — only the word that matched, in
parentheses. That is also the only honest thing to show: the row is telling you
*where* the word you typed occurs. Accepting inserts the grammar's own scope
(`tag:flagged`, `note:fridge`), and the meta says how many runs that token will
actually find, so the row and the result cannot disagree.

Three rules carried over from the earlier search rounds:

- **A one-letter stem offers nothing** — it would match half the archive.
- **A word the tokenizer would mangle is not offered at all.** The grammar
  strips quotes with no escape, so `tag:say "hi"` would search for something
  else; such a tag is skipped and *counted out loud*, the way
  `core/param_vocab` reports its own `omitted` (docs/175: a suggestion that
  finds nothing is worse than no suggestion).
- **The person's own words get a reserved slice of the panel.** The widget caps
  at `MAX_ROWS`, so without one they would be the rows sliced off exactly when
  the parameter list was full — i.e. on a rich archive.

Notes are tokenised **Unicode-aware**: this customer writes them in Korean, and
a `\w`-based tokeniser without `re.UNICODE` would silently drop every one. Real
browser, real note: `#15: note (냉각기)`.

## ② The Tags button

The chips already existed and already worked — `toggleTagFilter` in
dataset-virtual.js is not mode-aware at all. They were rendered
`{% if is_collections %}`, so on the Datasets page the whole row was absent and
a tag was something you could only type.

Now they render on both, as a collapsible banner between **Experiments** and
**Sort**, exactly where it was asked for. The collapsed class lives on `<body>`
— the same pattern the Experiments banner uses, and for the same reason: the
datasets page is swapped by htmx, and a state held on the swapped node would be
rebuilt and lost on every render. The **default** differs by page and a stored
choice beats both: open on Collections, where the tags are the subject of the
page; collapsed on Datasets, where they are one filter among several. The page
says which it is through `#tag-filter-grid[data-collections]`, so there is no
second source of truth.

docs/141 §4t's rule still holds underneath: **no tags on the chip, no row at
all** — never an empty "All" that stays lit whatever you pick.

## Verification

- `tests/test_tag_vocab.py` — 19 pins: the tokeniser (Korean, one-letter words,
  the word cap), the builder (merging across folders, newest-run-first,
  per-entry tolerance for a hand-edited file, an unreadable file, an empty tag
  name), the version moving when a tag is typed, and the route — including the
  one that matters most, that it never builds a `DatasetStore`.
- `tests/tag_typeahead_selfcheck.cjs` — 18 assertions against the real shipped
  `sidebar-typeahead.js`: the row format, that a note's other words never reach
  a row, what accepting inserts, the cap being said, and the unquotable value.
- `tests/stress_tagsearch.cjs` — **13/13 in real headless Chrome, zero console
  errors**: the Tags button present, labelled, and measurably *between* the
  Experiments and Sort toggles; collapsed on a first visit; one click showing
  the chip's real tags; and the popup rendering `#13: flagged`,
  `#12: note (fridge)` and `#15: note (냉각기)` without sideways scroll.

### A driver lesson, twice

`Input.dispatchKeyEvent` with `text` on **both** `keyDown` and `char` types each
character twice — the box read `냉냉각각`, which matches nothing, and the popup
correctly never opened. And a *default* is a claim about a first visit: the
driver's own earlier run had clicked the Tags toggle, which persists, so the
second run measured the first run's click and called it the default. It clears
the key before asserting now (the browser-profile-hygiene rule, one more time).
