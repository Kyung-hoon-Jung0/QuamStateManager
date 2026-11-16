# 314 Slot gate classes

A slot gate class is a gate class SM writes as text: a `QubitPairMacro` subclass that plays one pulse per chosen
slot (control, target, coupler), optionally plays constant spectator levels, and may carry sequence steps. SM makes
it from the New CZ gate dialog (`web/static/cz-gate.js`, `newCreate`), keeps it as a class draft
(docs/312_pulse_class_drafts.md), saves it with the one save door (docs/313_quam_config_save.md), and then plans,
checks and creates gates of it through the CZ gate doors. Files: `core/slot_gate_template.py` (text, rules),
`core/slot_gate_plan.py` (plan pieces), `core/gate_check.py` (the spectator check), `web/cz_gate_routes.py` (doors).
A gate class of this kind is never a row of `cz_gate_build.GATE_CLASSES`. Its descriptor, the row the gate planner needs
for a class (slots, fields, lines), is built per class by `slot_desc` (slot_gate_plan.py:73).

## 1. What a slot class is

- **Slots and presets.** `SLOTS` (slot_gate_template.py:23) names the three slots; `PRESETS` (slot_gate_template.py:24) maps each preset
  name to the slot set it ticks. The dialog route renders `_cz_gate_dialog.html`; its template data carries
  `slot_presets` (rows `{key, label, slots}`, `_slot_presets`, cz_gate_routes.py:215) and `slot_names` (`cz_gate_dialog`,
  cz_gate_routes.py:938-959). A spec is `SlotSpec` (slot_gate_template.py:108): class name, slots, a spectators flag and
  optional `before` / `after` sequence steps (`STEP_KINDS`, slot_gate_template.py:78; checked by `sequence_steps_problem`, slot_gate_template.py:179). The
  make door takes no steps (section 7); it keeps the steps of an existing older draft (section 2).
- **Slot fields.** Each slot gives the macro one field `<slot>_flux_pulse` (`_region`, slot_gate_template.py:419; `slot_field`,
  slot_gate_plan.py:69). A class with spectators adds `spectator_qubits`, `spectator_levels`, `spectator_lengths`
  (`_region`, slot_gate_template.py:423; `SPECTATOR_FIELDS`, slot_gate_plan.py:28). The apply body plays each slot on its own channel
  (`_channel`, slot_gate_template.py:400; the play loop, slot_gate_template.py:497).
- **Template region marks.** The header is `HEADER_MARK` plus ", version N." (`_render`, slot_gate_template.py:540). The generated body
  sits between `REGION_BEGIN` and `REGION_END` (slot_gate_template.py:21, slot_gate_template.py:22); the comment lines inside carry the spec: `# slots` and
  `# spectators`, plus `# before` and `# after` only when the class has sequence steps (`_region`, slot_gate_template.py:407-410; read back
  by `parse`, slot_gate_template.py:570-576).
- **Text only.** Module docstring, slot_gate_template.py:1: SM never imports, compiles or runs a class file. `parse`
  (slot_gate_template.py:552) reads the spec from the comment lines with `ast.literal_eval` (slot_gate_template.py:570) and accepts the text only when
  `_render` of that spec equals it byte for byte (slot_gate_template.py:579). The lab environment alone runs the class, in the save
  door's check (`check_slot`, core/quam_config_save.py:252; every declared slot must be played once, pinned by
  tests/test_slot_gate_save.py `test_worker_slot_check_needs_each_declared_slot_played_once`).
- **Save.** A draft whose first line is the slot header is a gate class, not a pulse: `_slot_header`
  (quam_config_save.py:85). The parser is the one judge of a readable draft (quam_config_save.py:212); an unreadable one is refused
  before any check with `SLOT_UNREADABLE` (quam_config_save.py:75, raised quam_config_save.py:214). No amplitude or proportional rule applies and
  `proportional` is set to None (quam_config_save.py:269-271). The class is not added to the pulse class modules of the probe, the
  lab-side import check of the class modules (docstring, quam_config_save.py:185-187).

## 2. Template versions

- **Current and readable.** `TEMPLATE_VERSION` is the render SM writes (slot_gate_template.py:17, `render`, slot_gate_template.py:520).
  `READ_VERSIONS` (slot_gate_template.py:19) lists every version `parse` reads; the header version must be in it (slot_gate_template.py:559-562) and
  `Parsed.version` says which (slot_gate_template.py:581). `render_v1` (slot_gate_template.py:525) is the frozen older render, used only to recognise an older
  text, never written. The only difference of the older render is what control and target accept (`_region`, slot_gate_template.py:410
  and slot_gate_template.py:448-449; `V1_SLOT_KINDS`, slot_gate_template.py:74; `older_render_refuses_pulse`, slot_gate_template.py:530: true only for version 1 with a control or
  target slot). A coupler-only class of the older render gets no note (tests/test_cz_gate_slot_routes.py
  `test_a_gate_on_an_older_coupler_only_lab_file_says_nothing`).
- **How an older draft is shown.** `slot_drafts(..., versions=True)` lists every draft the parser reads with its
  version (slot_gate_plan.py:432); `SlotClass.version` keeps it (cz_gate_routes.py:171) and the class entry carries
  `older_render: sc.version != sgt.TEMPLATE_VERSION` (cz_gate_routes.py:204). When a gate is planned, `older_render_note` (cz_gate_routes.py:521)
  reads the saved lab file: if it is a version-1 render with a control or target slot, the plan answer carries a note
  (`res["notes"]`, cz_gate_routes.py:424-428): `OLDER_RENDER_NOTE` (cz_gate_routes.py:513) while the draft is still the older render, and
  `OLDER_RENDER_SAVE_NOTE` (cz_gate_routes.py:516) once the draft was made again and only the save is left (cz_gate_routes.py:535). An absent or
  unreadable lab file gives no note (cz_gate_routes.py:527-533).
- **Make again.** The make door accepts the name of an existing slot draft (`_name_check`, cz_gate_routes.py:1287):
  - no gate of this chip uses the class (`_gates_using`, cz_gate_routes.py:1255): the draft is replaced (cz_gate_routes.py:1302-1303), an older render
    becomes the current render;
  - gates use it and the new text would equal the stored draft: nothing is written and the door
    answers ok (`UNCHANGED`, cz_gate_routes.py:1284; cz_gate_routes.py:1307-1309 and cz_gate_routes.py:1357-1358);
  - gates use it, the stored draft is an older render, and the posted spec equals the stored spec once its steps are
    left out (`_older_match`, cz_gate_routes.py:1265): the stored spec, steps included, is written again in the current render
    (cz_gate_routes.py:1313). Such a remake only widens what control and target accept (comment, cz_gate_routes.py:1304-1306), so gates keep working;
  - after the steps refusal (section 5), anything else while gates use it is refused with `NAME_IN_USE` (cz_gate_routes.py:1236, returned cz_gate_routes.py:1312).
  After a write the draft watch (`pulse_class_watch`) records the bytes SM wrote as settled content (`adopt`,
  pulse_class_watch.py:118; the call, cz_gate_routes.py:1364), so they are not taken for an outside edit and the save door sees the same
  bytes.
- **Sha pins of the older render.** tests/test_slot_gate_template.py pins the rendered text of every slot set and
  spectator choice per version: `NO_STEP_SHA256` (current, tests/test_slot_gate_template.py:497) and `NO_STEP_SHA256_V1` (older, tests/test_slot_gate_template.py:514), checked by
  `test_no_step_render_is_unchanged` (tests/test_slot_gate_template.py:569); the older render with steps is pinned by `STEP_SHA256_V1` (tests/test_slot_gate_template.py:1541) and
  `test_stepped_version_1_render_is_unchanged` (tests/test_slot_gate_template.py:1570). A change of the current render fails the first pin and needs
  a new version, never an edit of an old pin.

## 3. What a slot accepts

- **One table.** `SLOT_KINDS` (slot_gate_template.py:67) says in words what each slot accepts; `SLOT_PROBLEM` (slot_gate_template.py:63)
  is the one refusal sentence, formatted with slot, kinds and value. The generated class and the pure check
  `slot_value_problem` (slot_gate_template.py:386) read the same table (pinned by `test_kinds_text_is_one_table` and
  `test_generated_slot_value_rule_matches_the_pure_check`, tests/test_slot_gate_template.py).
- **Kinds per slot.** A string (an operation name) passes on every slot (`slot_value_problem`, slot_gate_template.py:393). The coupler
  slot also takes an inline `Pulse` (slot_gate_template.py:395; generated check, slot_gate_template.py:447). Control and target take a `Pulse` only when it is
  an operation of that slot's own z channel.
- **Own flux line, decided by identity.** `slot_value_problem` accepts a control or target `Pulse` only when it is
  one of the operations of the slot's own channel, compared with `is` (slot_gate_template.py:395); the generated class does the same
  against `pair.qubit_control.z.operations` / `pair.qubit_target.z.operations` (`_channel`, slot_gate_template.py:400; check, slot_gate_template.py:451-452).
  A stored reference to another line's operation is therefore refused; the lab test plays a control reference to the
  target line's operation and expects the slot sentence (tests/test_slot_gate_save.py
  `test_lab_gate_with_slot_pointers_plays_on_a_built_chip`). The plan builds each pulse on the slot's own line: the
  descriptor's slot rows carry line `control` / `target` / `coupler` (`slot_desc`, slot_gate_plan.py:95) and
  `cz_gate_build._plan_known` creates the operation on that line (cz_gate_build.py:1130-1217).
- **Older render.** Version 1 refused any `Pulse` on control and target (`V1_SLOT_KINDS` comment, slot_gate_template.py:72-73; generated
  guard, slot_gate_template.py:448-449).
- **Refusal sentences by name** (slot_gate_template.py): `SLOT_PROBLEM` (slot_gate_template.py:63), `KEYS_PROBLEM` (slot_gate_template.py:51),
  `OWN_PROBLEM` (slot_gate_template.py:52), `LENGTH_PROBLEM` (slot_gate_template.py:53), `UNIT_PROBLEM` (slot_gate_template.py:55), `UNIT_VALUE_PROBLEM` (slot_gate_template.py:56), `LEVEL_PROBLEM`
  (slot_gate_template.py:58), `DUPLICATE_PROBLEM` (slot_gate_template.py:60), `TYPE_PROBLEM` (slot_gate_template.py:61), `DURATION_PROBLEM` (slot_gate_template.py:62). Step sentences: `STEPS_PROBLEM`
  (slot_gate_template.py:87) to `XY_MISSING_PROBLEM` (slot_gate_template.py:98). Name sentences of the make door: `NAME_NOT_TEXT` to `NAME_IN_USE`
  (cz_gate_routes.py:1231-1236). `validate` (slot_gate_template.py:139) adds "Select at least one slot." and the duplicate / subset
  sentence (slot_gate_template.py:146, slot_gate_template.py:148). tests/test_slot_gate_template.py `test_spectator_cap_sentence_and_constant` pins the
  wording of `LENGTH_PROBLEM` and `DURATION_PROBLEM` and that both are ASCII.

## 4. Spectators

- **Rows.** Each row is `{qubit, length, level, unit?}` (`plan_spectators` docstring, slot_gate_plan.py:253). The
  plan stores them in the three macro dicts keyed by the real qubit name: `qubits` holds `#/qubits/<name>`,
  `levels` and `lengths` the numbers (slot_gate_plan.py:314-316, slot_gate_plan.py:356-358). Lengths are stored as whole numbers (`_whole`, slot_gate_plan.py:158).
  The route plan row also carries `reachable` and `unit_created` (slot_gate_plan.py:352-355).
- **Own qubits and duplicates.** A spectator row naming the pair's control or target qubit, or an alias of it, is
  refused with `OWN_PROBLEM`, judged by landed identity (`_identity_problem`, slot_gate_plan.py:200, called by
  `rows_problem`, slot_gate_plan.py:225; the generated class checks it too, slot_gate_template.py:461). Two rows naming one qubit are
  refused with `DUPLICATE_PROBLEM` (slot_gate_plan.py:212, slot_gate_plan.py:311; generated check, slot_gate_template.py:464).
- **The unit operation.** A spectator plays `SPECTATOR_UNIT_OP` (slot_gate_template.py:49) scaled by
  `level / unit` on the qubit's z line (generated play, slot_gate_template.py:502-508). The unit operation belongs to the qubit, not to
  the gate: an existing one is reused read only (`_plan_spectators`, slot_gate_plan.py:318-322); a missing one is
  created with the given unit amplitude as a pulse of `UNIT_CLASS` (slot_gate_plan.py:31) and the length
  `LENGTH_FLOOR_ANY_QOP_NS` (16 ns, which plays on every QOP; slot_gate_plan.py:347-351). Two names of one qubit are one qubit (`real_qubit`, slot_gate_plan.py:169; `qubit_identity`, slot_gate_plan.py:189; a duplicate is
  refused, slot_gate_plan.py:310-311). A unit that differs from the stored one, or from one another gate of the request plans, is
  refused (`UNIT_GIVEN_PROBLEM` slot_gate_plan.py:33, `UNIT_PLANNED_PROBLEM` slot_gate_plan.py:36); equality is exact, with no tolerance
  (`_same_number`, slot_gate_plan.py:163). A stored unit operation with no readable amplitude is refused with
  `UNIT_NO_AMPLITUDE_PROBLEM` (slot_gate_plan.py:40, returned slot_gate_plan.py:242).
- **Created once per request.** `_plan_all` keeps one `planned_units` map for the request so a qubit gets one create
  (cz_gate_routes.py:356, cz_gate_routes.py:399-400); a refused gate removes the units it added (cz_gate_routes.py:416-418). `assign_units` (cz_gate_routes.py:439) then
  gives each new unit to exactly one written gate: the first allowed gate, in request order, whose rows use it; the
  rows' `unit_created` follow. The check loop recomputes this until it is stable (`check_answer`, cz_gate_routes.py:1023-1041), and
  the create door applies it with the verdicts' allowed set (cz_gate_routes.py:1161). Pins: tests/test_cz_gate_slot_routes.py
  `test_one_unit_operation_per_qubit_for_the_whole_request`, `test_assign_units_gives_a_unit_to_the_first_written_gate_only`,
  `test_a_shared_unit_is_written_with_the_gates_that_are_written`.
- **`not_calibrated`.** Every planned slot row carries `not_calibrated` set to `cgb.NOT_CALIBRATED`
  (cz_gate_routes.py:500-501; text at cz_gate_build.py:1264). The dialog shows it once, after a check
  (cz-gate.js:624). Pin: `test_every_planned_slot_row_carries_the_not_calibrated_line`.
- **Amp scale limit.** The rule is `|level / unit|` strictly below `AMP_SCALE_LIMIT` (slot_gate_template.py:35,
  `level_scale_problem`, slot_gate_template.py:286); both numbers must be finite, the unit nonzero, bool refused (slot_gate_template.py:289-295). A negative
  unit counts by magnitude; `reachable_level` (slot_gate_template.py:305) is the excluded boundary in V. Its source is the comment at
  slot_gate_template.py:30-34: the open range confirmed for `amp()`, and a documentation-source line about the fixed-point overflow that
  is not published. The limit has one owner: the plan and the check never restate it, they call
  `spectator_rows_problem` (slot_gate_plan.py:7-11; pinned by `test_limit_has_one_owner_and_no_bare_ratio_limit` and
  `test_scale_limit_assigned_in_one_file_and_no_bare_two_in_the_plan`). A level of 0 is accepted on purpose
  (slot_gate_plan.py:268-270).
- **Length rules and caps.** A length must be finite, a whole multiple of `LENGTH_STEP_NS` (slot_gate_template.py:42), at least
  `MIN_LENGTH_NS` (slot_gate_template.py:40) and at most `MAX_SPECTATOR_NS` (slot_gate_template.py:45, whose comment quotes its documentation source);
  `spectator_length_problem` (slot_gate_template.py:310) adds the shared `length_rule`, and `MIN_LENGTH_NS` / `LENGTH_STEP_NS` are pinned
  equal to that rule (`test_min_length_matches_shared_rule`, `test_length_step_matches_shared_rule`).
  The length floor has one owner, `spec_constraints.length_floor_ns(state, from_qua)` (spec_constraints.py:155),
  after docs Guides/timing_in_qua.md:14-16: a chip that declares a QOP before 3.3 gets 16 ns; QOP 3.3 or later
  gets 4 ns for a length set in the configuration and 8 ns for one set from QUA; a chip with no version gets the
  3.3 floor plus the note `LENGTH_FLOOR_NOTE` when a value under 16 ns is accepted. The CZ gate builder's
  `chip_length_rule` (cz_gate_build.py:394, over `length_rule`, cz_gate_build.py:368) and the gate check's
  `length` result (`_length_check`, gate_check.py:498) use the chip's floor. The builder puts the note in the
  plan answer's `notes` (`_floor_notes`, cz_gate_build.py:408), which the plan and check doors pass on
  (cz_gate_routes.py:770, cz_gate_routes.py:1073) and the dialog shows beside the plan and after the write, so a
  known-class gate created without a lab check still names the QOP it needs. Exception: on the slot path an
  older-render note is appended to the plan's `notes` (cz_gate_routes.py:428). Every caller of the floor reads its
  note, or is listed with the place that shows it (`FLOOR_ONLY`, tests/test_gate_length_floor.py). The slot
  template keeps `MIN_LENGTH_NS` at `LENGTH_FLOOR_ANY_QOP_NS` (16 ns): its generated class is chip-independent
  text that sets spectator lengths and waits from QUA, and a lower floor there is a new render version. The slot
  duration (clock cycles) is capped by `DURATION_PROBLEM`, checked in the generated apply (slot_gate_template.py:490-493). A wait step is
  capped by `MAX_WAIT_NS` (slot_gate_template.py:47, `WAIT_PROBLEM`, slot_gate_template.py:92). The generated class checks the same length, unit and level
  rules before it plays anything (slot_gate_template.py:454-489).
- **List-path and dotted-name refusals.** A qubit or line path part with a dot is refused (`DOT_PROBLEM`,
  slot_gate_plan.py:46; check slot_gate_plan.py:307-308), because the create path is a dotted string. A new unit operation on a line
  reached through a list is refused (`LIST_PROBLEM`, slot_gate_plan.py:48; `_through_list`, slot_gate_plan.py:52; check slot_gate_plan.py:344-345), because the create
  door steps through dicts only. An existing unit operation on such a line is read and needs no create
  (`test_a_unit_that_exists_on_a_line_inside_a_list_is_read_and_needs_no_create`).
- **Other row refusals.** `rows_problem` (slot_gate_plan.py:217) adds to the template sentence: a qubit that is not on the chip or has
  no flux line gets that reason instead of `UNIT_PROBLEM` (slot_gate_plan.py:234-240), and a length no float can hold is
  refused (slot_gate_plan.py:230-232). `ROW_PROBLEM` (slot_gate_plan.py:43) and `UNREADABLE_PROBLEM` (slot_gate_plan.py:44) cover rows of another shape; data never
  raises (slot_gate_plan.py:244-245, slot_gate_plan.py:278-279).
- **What the dialog shows.** The spectator panel lists the pair's candidates with length, level and unit cells
  (`fillSpect`, cz-gate.js:533). `_spectator_offer` (cz_gate_routes.py:276) says offered with `method: "unit"` when the
  class has spectators, else the descriptor sentence of `spectator_refusal` (cz_gate_build.py:1281). `_unit_candidates`
  (cz_gate_routes.py:228) adds the stored `unit` and `reachable` per candidate. A blank unit is passed on as absent (`_slot_rows`,
  cz_gate_routes.py:290).

## 5. The routes

All doors answer through functions shared with the agent's routes (comment, cz_gate_routes.py:982-984).

- **Dialog read** `GET /pulse/cz-gate/dialog` (cz_gate_routes.py:938): the answer is HTML, not JSON; no chip gives the `_status.html`
  partial with 400 (cz_gate_routes.py:942-943). Template data keys: `pairs`, `selected`, `classes`,
  `fit`, `env`, `sync`, `slot_presets`, `slot_names` (cz_gate_routes.py:955-957). A slot class entry has `class`, `label`, `layout`
  `"slot"`, `available`, `why`, `older_render`, `slots`, `models`, `slot_spec` (`slots`, `spectators`, `before`, `after`) and
  `spectators` (`_slot_entry`, cz_gate_routes.py:201). Each pair entry also carries `lines` (`{control, target, coupler}`:
  true when that line can hold a pulse, `_pulse_lines`, cz_gate_routes.py:113) and `xy_operations` (per role, the operation
  names of that qubit's xy channel, or null; `slot_gate_plan.xy_operations`, slot_gate_plan.py:385). The dialog offers no
  optional pulse on a line the pair lacks, neither ticks nor enables that slot in the new-class panel, and picks the first
  preset that fits the pair (`lineOpen`, `slotOffered`, `fittingPreset`, static/cz-gate.js:727, :732, :1086). Reading writes nothing (`test_the_dialog_read_has_no_side_effects`).
- **Make** `POST /api/pulse/cz-gate/slot-class` (cz_gate_routes.py:1320). Request keys: `class_name`, `slots`, `spectators`,
  `before`, `after` (a missing or null list is no steps). The steps are checked only by `validate`, which calls
  `sequence_steps_problem` (cz_gate_routes.py:1346); there is no second copy of the rule in the route or in the browser.
  Answer: `{ok, cls, qclass}`; a refusal is `{ok: false, error, problems}` and, when the sentence names one step, `step:
  {where, index}` (index from 0; `_step_at`, cz_gate_routes.py:1240, which asks the same check of growing prefixes). The
  dialog shows that sentence beside the row it names (static/cz-gate.js:1256). Status 400 for a non-text name or a
  `validate` problem; a refused body writes nothing. 409 for a taken name (`_name_check`, cz_gate_routes.py:1354: class table,
  pulse class, other draft, lab-made file, in use). A class in use with other posted steps is another class body, so it
  answers `NAME_IN_USE`; an older render in use is made again only when the body posts no steps or exactly its stored steps
  (`_older_match`, cz_gate_routes.py:1265). A name not in use replaces its draft with the posted steps. 400 also for a body
  that is not a JSON object (`BadBody`) and when the draft cannot be written (`DraftError`, `ValueError`); a store problem
  answers through `_store_or_error`. It writes the draft only; saving is the class save door (docs/313_quam_config_save.md).
- **Steps in the dialog.** The new-class panel has two ordered lists, Before and After (`stepLists`, `stepRow`,
  static/cz-gate.js:1016, :962): kind align / wait / xy pulse, a wait length in ns, an xy role and an operation picked from
  `xy_operations`; Add, Up, Down, Remove. With no xy operation on either qubit the xy kind says so and cannot be added.
  Non-empty lists are posted (`stepsBody`, static/cz-gate.js:938).
- **Steps of a gate.** Plan: an xy step whose operation the role's qubit lacks refuses the pair with
  `XY_MISSING_PROBLEM` (`xy_steps_problem`, slot_gate_plan.py:406; cz_gate_routes.py:395), and a class with steps adds the
  note of `steps_sentence` (slot_gate_plan.py:420; cz_gate_routes.py:425-428, after the build's own notes). Timeline and
  check: the steps are statements of the lab-built QUA program, placed by `static_timeline._place`
  (core/static_timeline.py:182) with wait and xy pulse lengths from the config, or answered "cannot draw: <statement>"
  (core/static_timeline.py:191-241); the check reads that timeline (gate_check.py:427-437). Spectators play alongside the
  gate pulses and are not changed by steps.
- **Plan** `POST /api/pulse/cz-gate/plan` (cz_gate_routes.py:962, `plan_answer`, cz_gate_routes.py:986). Body (`_body`, cz_gate_routes.py:320): `pairs`, `gate_name`,
  `gate_class` (the `qclass` of the class), `pulses`, `copy_from`, `model_gate`, `macro_fields`, `spectators`
  (`{pair: [rows]}`; a bare list is the one asked pair, `_spectators`, cz_gate_routes.py:304; otherwise `SPECTATORS_ONE_PAIR`, cz_gate_routes.py:85).
  No pair gives 400 `Pick a pair.` (cz_gate_routes.py:993). Answer: `ok`, `plan_sig`, `pairs` (`_summary`, cz_gate_routes.py:741, with `spectators` and
  `spectator_ops` when present). A refused pair has `ok: false` and `refusals`. A class is found by its `qclass`
  among the readable drafts (`_request_slot`, cz_gate_routes.py:190); spectator rows for a class without spectators are refused with
  the descriptor sentence (`_plan_slot_rows`, cz_gate_routes.py:485-487).
- **Check** `POST /api/pulse/cz-gate/check` (cz_gate_routes.py:968, `check_answer`, cz_gate_routes.py:1002). Same body. Answer: `ok`, `status`, `sync`,
  `why_not_checked`, `leftover`, `plan_sig`, `check_token`, `pairs` (each with `checks`, `unchecked`, `verdict`
  `{allowed, state, reason, headline}`, `paths`, `outcome`; cz_gate_routes.py:1071-1082). `_verdict` (cz_gate_routes.py:1099) uses layout `"slot"`; a
  slot gate is never a `known` layout in `may_create` (core/cz_gate_create.py:79), so with SM-made class files it
  is allowed only when `load_config` and `ops` pass (core/cz_gate_create.py:88-90), and without a run it is refused (core/cz_gate_create.py:94-98;
  `test_a_slot_class_is_never_known_to_may_create`). A moved class file refuses with `class_files_moved` (cz_gate_routes.py:1108-1110).
- **Create** `POST /api/pulse/cz-gate/create` (cz_gate_routes.py:975, `create_answer`, cz_gate_routes.py:1118). Body: `check_token`, `plan_sig`.
  Refusals: 400 no chip (cz_gate_routes.py:1125-1126); 400 when the body is not a JSON object (cz_gate_routes.py:1133-1134); 409 read-only archive (cz_gate_routes.py:1127-1128), the agent edit lock's refusal (cz_gate_routes.py:1129-1131), unknown, other-actor or used token (cz_gate_routes.py:1135-1144),
  another chip (cz_gate_routes.py:1145-1147), changed inputs (cz_gate_routes.py:1148-1150), chip moved (cz_gate_routes.py:1155-1157, `stale: true`), or a gate whose
  creates no longer match its check (cz_gate_routes.py:1169-1171); 400 when no pair may be created (cz_gate_routes.py:1172-1175); 400 or 500 after a rolled back
  write (cz_gate_routes.py:1202-1204); 500 `partial` when the rollback failed (cz_gate_routes.py:1198-1200). Success: `created`, `skipped`, `count`,
  `group` (cz_gate_routes.py:1220-1222). All creates of all written gates are one change group (cz_gate_routes.py:1176).
- **Notes.** The table classes' answers are unchanged when a slot draft exists
  (`test_a_table_class_answers_the_same_with_a_slot_draft_present`). A gate of an SM-made class whose gate class has
  no extras slot gets `EXTRAS_SLOT_REFUSAL` (cz_gate_routes.py:508, added cz_gate_routes.py:1106-1107).

## 6. What the check reads

The check `spectator_scale` (`CHECKS`, `OWN_CHECK`, gate_check.py:102-104) is SM's own reading of the gate's spectator
rows (module docstring, gate_check.py:22-27). Three facts:

- **The chip as the write would leave it.** `check_gates` builds `own_merged = _with_creates(store.merged, creates)`
  (gate_check.py:883). `_with_creates` (gate_check.py:844) returns a copy of the merged state with every create set, copying only the dicts on
  each create's path; a create the create door would refuse (a path through a non-dict or a list, a missing parent,
  an empty path part, an existing key) is left out. Why: a unit operation the gate creates in the same write must be
  read by the check, and a create the door refuses must not make a level pass (docstring, gate_check.py:845-850; pins:
  `test_unit_created_in_the_same_write_is_read_by_the_check`, `test_the_overlay_leaves_out_every_create_the_door_refuses`,
  `test_the_overlay_and_the_door_leave_the_same_chip`).
- **Same rule as the plan.** `_spectator_scale_check` (gate_check.py:352) calls `macro_spectator_problem` (slot_gate_plan.py:363)
  on the stored macro, which reads every qubit through `real_qubit` and the unit amplitudes of the merged state, then
  `rows_problem`. The failure message is that sentence; a pass reads "Every spectator level is within the range its
  unit operation reaches." (gate_check.py:367); a gate without spectator rows gets a `note` with `NO_SPECTATOR_ROWS` (gate_check.py:349, gate_check.py:364)
  and no entry in the stored count (docstring gate_check.py:22-27). A read failure is `skipped`, never a pass (gate_check.py:362).
- **Judged without the lab.** The check does not need the lab's answer: it runs when there is no environment
  (`check_gates`, gate_check.py:884-886 passes `after_merged`), when the lab code did not run, and when the chip as it is does not
  load (`_one_gate`, gate_check.py:370-374; pins `test_the_own_check_is_judged_when_the_lab_code_did_not_run`,
  `test_the_own_check_is_judged_when_the_chip_as_it_is_does_not_load`,
  `test_check_gates_without_an_environment_still_judges_the_write`). A plain-words label exists for it (gate_check.py:233).
- **Make active and Re-check compare previews for every gate on the chip**, a known-class gate as well as a slot gate.
  The preview is SM's own drawing of each pulse the lab's code played, read from the chip (`core/gate_preview.py`
  `chip_previews`); a pulse SM cannot draw is named with its reason in the preview message (`gate_check.SKIPPED`). A
  differing pulse now refuses a known-class gate too (stricter than before); one whose pulses match is still made
  active (pins in `tests/test_make_active_preview.py`).

## 7. Known limits (as the code states them)

- Steps are checked for shape at the make door and for xy operations at the plan; the browser holds no step rule. A
  stepped class in use cannot be made again with other steps (`NAME_IN_USE`; pin
  `test_a_stepped_current_render_in_use_is_still_refused`).
- A new unit operation cannot be added on a flux line inside a list, and no name with a dot can be written (section 4).
- A unit amplitude is compared exactly; `0.1 + 0.2` is not the stored `0.3` (slot_gate_plan.py:163-166).
- A qubit entry that is an alias is followed to the real qubit; if the chain cannot be followed the written name is
  kept (`real_qubit`, slot_gate_plan.py:169-187).
- A slot class cannot be combined with a gate class that has no extras slot (`EXTRAS_SLOT_REFUSAL`, section 5).
- The older render refuses a stored control or target pulse until the class is made again and saved
  (`OLDER_RENDER_NOTE`, section 2).
- The scale limit's source is a confirmed open range plus an unpublished documentation line (slot_gate_template.py:30-34).

## 8. Tests

| File | Pins |
|---|---|
| `tests/test_slot_gate_template.py` | Render, parse and round trip; presets; the sha pins of both renders; slot value rule (pure and generated); spectator rows, scale, length and duration caps; steps and their statements; older-render parse and version-1 refusal |
| `tests/test_slot_gate_plan.py` | `slot_desc` against the rendered fields; unit reuse, single create, exact-equality, dot and list refusals; alias qubits; the check overlay (`_with_creates`) and the `spectator_scale` check, including without the lab |
| `tests/test_slot_gate_save.py` | The save door for slot drafts (check route, no proportional rule, unreadable draft refused, header-only recognition); the real lab worker plays every declared slot; slot pointers on a built chip |
| `tests/test_cz_gate_slot_routes.py` | Dialog read and presets; make door (names, in-use rule, older render remake, unchanged text writes nothing); plan, check and create with units shared across gates; `not_calibrated`; older-render notes |

## 9. Sequence steps on screen and at the door

- **xy steps are gate lanes.** The xy lines of the pair's own two qubits play only the class's steps, so
  `timeline_data` puts them in the gate group with `step` True and the label "<element> (step)"; they are
  never counted, folded or named as spectators (`quam_state_manager/core/gate_timeline_view.py:150`).
- **Wait and align use the drawn clock of each line.** The one placement function records a separate
  wait span or align line for every covered output line, at its program clock plus the port delay
  already used by its pulses (`quam_state_manager/core/static_timeline.py:195`, mark builder at
  `quam_state_manager/core/static_timeline.py:202`). A wait touches the preceding pulse's end and the
  following pulse's start on that line; different clocks never become one earliest-to-latest block.
  Elements that never play have no output lane; a covered output line whose delay is unknown makes
  the timeline say "cannot draw: <statement>", with no guessed zero and no marks.
  `timeline_data` passes them through (`quam_state_manager/core/gate_timeline_view.py:172`). Both views draw them with one
  function, `stepMarks` (`quam_state_manager/web/static/gate-timeline.js:686`): a wait is a shaded span, an align a dashed
  line. Both axis ranges use `spanOf`, including every mark's endpoints. `fitMarks` fits labels on two rows,
  hides labels that do not fit and runs again on zoom; the Sequence steps disclosure retains every full
  description in text and its title (`quam_state_manager/web/static/gate-timeline.js:711`, disclosure at
  `quam_state_manager/web/static/gate-timeline.js:537`). The note says pulses and marks include the line's
  port delay, before filters (`quam_state_manager/core/static_timeline.py:172`).
- **The make door checks xy steps against the posted pair.** When the body names `pair`, an xy step whose
  operation that pair's qubit lacks is refused at its row (`step: {where, index}`), nothing is written
  (`quam_state_manager/web/cz_gate_routes.py:1264`, called at `quam_state_manager/web/cz_gate_routes.py:1386`). An unknown pair is a 400.
  The dialog posts the picked pair (`quam_state_manager/web/static/cz-gate.js:1272`).
- **The class page reads an SM slot class as SM-made**, with its slots and steps, through the slot template
  parser (`quam_state_manager/core/pulse_class_watch.py:88`). Before, every slot class read as "changed by hand" because the
  page used the single-pulse template's region markers; the steps in the file were never lost.
- **Step rows** are one `ui-item` each, with the fields and the move buttons in two `ui-actions` groups
  (`quam_state_manager/web/static/cz-gate.js:962`). Each row's buttons read Up, Down, Remove in DOM order.
  Wait typing updates the model without replacing fields or buttons on blur, so the first command click
  receives the typed value (`quam_state_manager/web/static/cz-gate.js:1070`).
- **Short plots** put lane titles above the lanes under the existing 200 px plot rule, including step lanes.
  The dialog shows the chart section before measuring it (`quam_state_manager/web/static/cz-gate.js:1660`);
  a resize remeasures the layout (`quam_state_manager/web/static/gate-timeline.js:1383`). Unit inputs use
  the kit focus outline inside their box (`quam_state_manager/web/static/ui-kit.css:1042`).
- **Default preset**: the preset whose slots are exactly the pair's open lines, else the first that fits, else
  the first (`quam_state_manager/web/static/cz-gate.js:1106`).
- **Not saved to quam_config yet**: the refusal carries its class (`ClassRefusal`, `quam_state_manager/core/quam_config_save.py:359`);
  the plan passes `save_class` (`quam_state_manager/web/cz_gate_routes.py:424`) and the dialog shows a kit notice inside the
  dialog body that says to save on the class page and offers "Open the class page"
  (`quam_state_manager/web/static/cz-gate.js:1481`).
