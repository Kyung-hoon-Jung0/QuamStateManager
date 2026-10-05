# 288 — Agent setup reads as four cards; Connect is one decision

**Date:** 2026-10-05 · **Trigger:** customer-facing feedback, verified by walking every
step in headless Chrome and reading each screenshot.

## What was wrong (measured on the page, not assumed)

- Agent Setup was nine accordions full of raw file paths. Two different steps were both
  numbered "2.", and Connect's first screen was a raw JSON diff.
- The Agent page's strip spoke in mechanisms: "terminal: not registered as an MCP
  server", "MCP ✓ hooks ✓ allow ✓", "4 setup steps". The user did not know what to press.
- An empty Agent feed gave no hint of what to type.
- The Connect confirm left the tile's own "Connect" beside the confirm's "Connect …",
  so two Connect buttons were on screen. A short tile also stretched to the height of
  its neighbour.
- On a connected tile, the "Always use this window and chip" box always showed unchecked,
  whatever the registration said. It is read only by Connect, so on that tile it
  claimed a state SM never read.

## What it is now

- **Four numbered cards plus a fold:**
  1. *Connect a terminal agent* (optional).
  2. *Where runs and notes live*: calibrations folder, the hardware switch, the journal folder.
  3. *Tell the agent about this device*.
  4. *Check it works*.
  5. A *Safety limits for <chip>* fold.

  A progress bar reads "N of 4 ready". The cards use the left navy accent and compact type.
- **One tile per CLI:** Connected / Not connected / Not installed. The tile says in
  words what is missing and offers ONE button.
  - Connect asks the server what it would write and says it in a sentence (*add SM to its
    list of tools*, *report the runs it starts to SM*, …). The exact line diff sits behind
    *Show exact changes*.
  - While the confirm is open, the tile's own Connect button and the pin box are hidden.
  - The pin box appears only before connecting.
- **The hardware switch** reads *Dry run — the agent's runs are simulated, nothing touches
  the OPX* / *Live — the agent's runs use the OPX*.
- **A failed Test** says the CLI did not answer and quotes the CLI's own words. A login hint
  appears only when the CLI itself says it is not logged in; no other cause is guessed.
- **The Agent page strip** reads: *terminal: not connected*, *SM tools ✓ · run reports ✓ ·
  no prompts ✓*, *Finish setup (N left) →*. The mechanism stays in each word's tooltip.
- **An empty feed** shows three questions to try. *observer* is now *view only*.
- **The journal tag** reads *Not saved yet* while the path shown is only a suggestion.

## Pins

- Rewritten because the old wording or accordion structure no longer exists (each rewrite
  says so in place):
  - `tests/agent_setup_selfcheck.cjs`;
  - `tests/agent_panel_selfcheck.cjs` W3/W6/W12;
  - `tests/test_agent_setup.py::test_the_three_words_and_their_order`.
- New, and mutation-checked (each went red under its mutation):
  - the confirm marks its tile *asking*, and Cancel gives the Connect back;
  - a connected tile shows no pin box;
  - `test_the_confirm_is_the_one_connect_on_screen`, the CSS rule.
- The test fixture's chip name was a lab's chip; it is now `chipA`.

## Not changed (noted)

- The diagnostics banner overlays the top of every page until the pointer moves (docs/251
  behaviour).
- The topbar Agent pill opens the Calibration log, not the Agent page.
- `tests/stress_*.cjs` are manual CDP rigs from earlier rounds and still use the old setup
  DOM. They are not part of the suite.
