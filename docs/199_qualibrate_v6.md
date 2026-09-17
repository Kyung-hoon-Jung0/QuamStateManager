# 199 — A newer config is not an unreadable one

Date: 2026-09-18. Found while pressing the **Projects** menu, which no stress
round had touched. The page reported, in red:

> Versions: qualibrate v6 / quam v3 &nbsp; **⚠ unsupported**

on the customer's own machine.

---

## 1. What was actually true

`SUPPORTED_QUALIBRATE_VERSION = 5` and this lab runs **6**. So the badge was
literally accurate. It was also, in the ways that matter, wrong twice over.

**SM reads their v6 config exactly.** Measured against the raw TOML, field by
field:

| field | raw config.toml | SM | |
|---|---|---|---|
| `quam.state_path` | `D:\…\quam_states\260907_KRS_5Q` | same | ✓ resolves |
| `qualibrate.storage.location` | `D:\…\dataset\KH_202608_CZ` | same | ✓ resolves |
| `qualibrate.calibration_library.folder` | `D:\…\1Q_calibrations` | same | ✓ resolves |

All three matched, all three were verified to exist, the active project
resolved, and 34 projects listed. The only thing v6 added to this config is
`[qualibrate.composite.*]` spawn flags — which SM never reads.

**And the explanation was a non-explanation.** The tooltip read *"differs from
the supported v5/v3 — SM stays read-only"*, which implies a matching version
would let SM write. It would not. docs/55's No-Conflict Doctrine makes this
tree read-only for **every** version, because qualibrate's own root write is
non-atomic, unlocked and comment-destroying. So the badge blamed the version
for a property the version has nothing to do with.

The net effect: a lab is told, in red, that its perfectly-readable configuration
is unsupported, and given a reason that is not a reason.

## 2. The distinction that was missing

A config from a **later** generation is not the same thing as one this reader
cannot read. qualibrate has only ever *added* sections, and the fields SM reads
have kept their places — which is why v6 read cleanly. An **older** config is
the genuinely worrying direction, because a field this reader expects may not
exist there yet.

`list_projects` therefore answers with both facts now:

```python
"versions": {"qualibrate": 6, "quam": 3, "supported": False, "newer": True}
```

`newer` is true only for a clean forward drift — both numbers present, both at
or above the pinned pair, and not the pinned pair itself. A quam regression
alongside a qualibrate bump is not a forward drift and does not qualify.

The badge splits accordingly, and neither branch blames the version for
read-only any more:

- **newer** → a muted *"newer than v5 — read fine"*, titled *"Newer than the
  v5/v3 generation this reader was pinned to. SM read this config and resolved
  the paths it needs; it never writes these files, for any version."*
- **otherwise** → the ⚠ stays, titled *"…and is not simply newer — a field SM
  expects may be missing. It never writes these files, for any version."*

`SUPPORTED_QUALIBRATE_VERSION` is deliberately **not** bumped to 6. Claiming
support for a generation means claiming every one of its changes was checked,
and only this lab's config has been. The honest statement is the one now on
screen: newer than what the reader was pinned to, and read fine.

## 3. Measured

Real Chrome, the customer's own `~/.qualibrate`:

```
Versions: qualibrate v6 / quam v3   newer than v5 — read fine
title: "Newer than the v5/v3 generation this reader was pinned to. SM read this
        config and resolved the paths it needs; it never writes these files,
        for any version."
```

Zero console complaints.

## 4. Pins, and two that could not fail

`TestANewerConfigIsNotAnUnreadableOne` (5) in `tests/test_qualibrate_config.py`
covers the payload: the supported pair, the customer's exact shape (qualibrate
ahead / quam level), an older config, mixed directions, and absent versions.

`TestTheVersionBadgeSaysWhichKindOfDrift` (4) in `tests/test_qualibrate_routes.py`
**renders the page** per version shape.

That second class exists because the sweep caught the first attempt: the
template pins were source greps, and **both template mutations passed**.
Disabling the forward-drift branch (`{% if false %}`) leaves its words in the
file, and rewriting one branch's tooltip leaves the phrase in the other — a grep
cannot tell a live branch from a dead one. The grep pin was deleted rather than
patched. **Sweep 7/7 red** once the pins render.
