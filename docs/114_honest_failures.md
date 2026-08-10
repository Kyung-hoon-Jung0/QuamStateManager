# 114 — Failures that explain themselves (docs/110 #15 + #16)

*2026-08-11. docs/104 #15/#16, user-approved in the docs/110 campaign. The
theme is honesty at the moment something goes wrong — the first-hour user
meets these before they meet any feature.*

## The four

**1. A non-chip folder gets a persistent explanation, not a 6-second toast.**
The first thing a new user does is pick the PARENT of the chip folder. The
answer used to be a corner toast that vanished before it was read. Now
`POST /load`'s failure renders `_load_failed.html` INTO the load target: it
names the folder, quotes the error, says what a loadable folder contains
(`state.json` + usually `wiring.json`), and — the part that actually
rescues the user — lists any immediate subfolder that **really holds a
state.json** as a one-click load button. The old hint only fired for
folders literally named `quam_state`; a lab whose folder is called
anything else got nothing.

**2. A dangling pointer says so.** The inspector rendered an unresolvable
pointer as raw text with the tooltip *"Resolves to: `<the pointer
itself>`"* — a sentence that is simply false, and reads like data
corruption. When the resolved value IS the raw pointer (the resolver's
honest "I could not follow this"), the badge is tinted and says **DANGLING
pointer — its target does not exist in this state**. A pointer that
resolves is untouched.

**3. Write permission is checked at OPEN, not discovered at apply.** A
read-only mount (a lab share, a mounted archive) used to be found out at
apply time — after twenty minutes of edits — through a vanishing toast.
Activation now probes `os.access(folder, W_OK)` and the tray carries a 🔒
whose tooltip says edits still save to the working copy but Apply will
fail until the folder is writable. The apply-failure message also names
permission explicitly when the OS reports `EACCES`/`EPERM`. Deliberately a
HINT, not a block: `os.access` is optimistic under NTFS ACLs, and refusing
to open a chip on its evidence would be worse than the problem.

**4. The value-history clock is discoverable.** 🕘 sat at `opacity: 0`
until row hover — an invisible affordance is an undiscoverable feature. It
now rests at `.3` and brightens on hover/focus. `tabindex="-1"` is
deliberately kept: Tab is reserved for hopping between EDIT cells
(docs/64's pinned contract).

Pinned by `tests/test_honest_failures.py` (7: the candidate offer incl. a
non-`quam_state` name and the no-state.json folder NOT offered, the
missing-folder path, dangling vs resolving pointers, the read-only tray
lock + its absence on a writable chip, and the clock's resting opacity).
