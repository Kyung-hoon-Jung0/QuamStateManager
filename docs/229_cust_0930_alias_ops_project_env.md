# docs/229: an aliased operation 500'd Chip Status; a project opens only with a chosen env

2026-09-30, customer report on a 21-qubit chip whose qubits are named
`qA1 … qD5` (four chains A–D) and whose readout operation is an alias.
Branch `fix/cust-0930`, based on `origin/main` `e258f3c6`.

## 1. Chip Status and the Qubits chip map were broken: an operation alias

**Symptom.** `/topology` (Chip Status) answered 500. The Qubits page
rendered its table, but its chip map (which fetches `/api/topology`) did not.
The user's server log showed the same traceback for both:

```
File ".../core/query.py", line 725, in get_topology
    "readout_amplitude": ro.get("amplitude"),
AttributeError: 'str' object has no attribute 'get'
```

**Cause.** The qubit naming was not the cause. `chain` is derived as
`name[1]` when `name[0] == "q"`, which gives A–D for this chip. The cause is
that a QUAM operations map stores aliases as sibling pointers:

```
"resonator": {"operations": {
    "readout": "#./readout_square",        (qB1-qB3: "#./readout_drachma")
    "readout_square": {...}, "readout_drachma": {...}}}
```

Eleven readers in `core/query.py` used `_get_nested(ch, "operations", name) or {}`
and then called `.get()` on the result. They are in `get_qubit`,
`get_wiring_map` and `get_topology`, and they read `readout`,
`x180_DragCosine`, `x90_DragCosine` and `saturation`. Every one of them
raised on an alias.

**Fix.** A new helper, `_op_dict(channel, name)`, replaces all eleven. It
works as follows:

- It follows `#./` siblings, relative to the operations dict (QUAM's own
  meaning), for up to 8 hops.
- It returns `{}` for anything else: a dangling alias, a cycle, an absolute
  pointer, or a value that is not a dict.

The result is that the table shows the value of the pulse QUAM actually uses,
or a blank. It never crashes and never guesses. On the customer chip the
values were checked against `state.json`:

| Qubit | Alias | Readout amplitude shown |
|---|---|---|
| qA1 | `readout_square` | 0.0154 |
| qA2 | `readout_square` | 0.0177 |
| qB1 | `readout_drachma` | 0.0526 |

**Pins.** `tests/test_query.py::TestOperationAliases`:

- an alias, including an alias on the named `x180_DragCosine` key;
- a dangling alias, which gives a blank;
- an alias cycle, which gives a blank;
- `get_qubit` and `get_wiring_map`.

All three are red against `origin/main`'s `query.py`, which is the mutation
check.

## 2. The landing's environment list was off screen

The per-project env picker added in w9/labwarm (docs/228) opened hidden,
after the project cards. With about 20 to 34 projects that is below the fold,
so "Choose…" appeared to do nothing.

It now sits **above the cards**. It is shown from the start for the
last-used project (else QUAlibrate's active project, else the first
project), and its list scrolls within 15rem. A card's Choose… / Change…
retargets it. It has no close button any more, because it is the landing's
env list rather than a popup.

## 3. A project opens only with an env chosen for it

Before this change, `/qualibrate/open` selected the project's remembered env,
or else the *suggested* one (the env used most recently by any project). A
project could therefore open, and a lab could work, in an env nobody picked
for it.

**Now.** `/qualibrate/open` requires one of two things:

- `python` in the form. The landing picker's pick rides along; it is saved
  for the project with `how=changed`.
- A remembered env for the project that still exists on disk.

Otherwise nothing is activated and nothing is selected. What the user sees
depends on where they pressed Open:

- **The landing card's Open**, or "Open QUAlibrate's current project" (both
  carry `from=landing`). The server answers `200` with `HX-Reswap: none` and
  `HX-Trigger: sm-env-required {project, message}`. `landing-env.js` then
  shows the picker for that project with a red note ("Choose the Python
  environment for X first … The project opens as soon as you pick one."). The
  pick saves the env and re-submits that project's Open form.
- **The sidebar Projects submenu, the Config Manager page, or a plain form
  post.** The server redirects (`HX-Redirect` or 302) to
  `/?landing=1&choose_env=X`, which boots the same required picker.
- **A remembered env whose interpreter has vanished.** This is refused too,
  and the note says the env no longer exists.

A suggested env is still offered: its row is marked, and its button reads
**"Use this (suggested)"**, never "In use". It still has to be clicked.

**Unchanged: State Load (`/load`) needs no env.** Viewing a state is not
running a project. The existing rule that a State Load of a *synced*
project's folder selects that project's env is also unchanged.

**Tests.**

- `tests/test_project_env.py::TestOpening` has six new or rewritten pins:
  - never-synced does not open;
  - the landing is answered in place;
  - another surface is sent to the landing picker;
  - a picked env rides along and opens;
  - a vanished env does not open;
  - a State Load needs no env.

  The old pin `test_a_never_synced_project_opens_with_the_suggestion_unconfirmed`
  encoded exactly the behaviour the customer asked to remove, and was
  replaced. With the gate mutated out, 4 of these pins go red.
- `tests/landing_env_selfcheck.cjs` has 26 assertions. They cover:
  - the boot-on-screen picker;
  - the required flow (`sm-env-required` and `?choose_env=`);
  - re-submission of the Open form after the pick;
  - the "(suggested)" label;
  - Escape, which now drops only the pending open.
- Tests about project scope and listing (`test_project_scope`,
  `test_qualibrate_routes`, and one in `test_ram_liveedit`) opt into a new
  conftest fixture, `any_project_env_chosen`. In that fixture every project
  counts as synced and selecting its env touches nothing. Those tests are
  about scope, not envs; the gate itself is pinned only in `test_project_env`.

**Real Chrome** (headless over CDP, against a copy of the customer chip,
real `~/.qualibrate` with 34 projects). The journey:

1. The landing shows the picker above the cards on the first screen, with 13
   envs listed.
2. Opening KRISS_CZ with no env stays on the landing and shows the red note.
3. "Use this" on the KRISS_CZ env opens the project on `/qubits`.
4. Back on the landing, the card shows `env KRISS_CZ ✓` and the picker marks
   it "In use".
5. After a reload the page is intact, with no JS errors.

Screenshots were looked at: the landing, the required note, Chip Status and
the Qubits chip map on the customer chip.
