# Plot value provenance

Plot-derived values use the existing staging doors. This adds metadata only;
the chip gate, type conversion, compensation, atomic rollback, mutator lock,
and live apply covenant retain their existing decisions.

`/field/edit` accepts an optional `provenance` form field containing JSON.
`/field/edit-batch` accepts an optional top-level object with the same name.
A batch row may override that object so different plot targets retain their
own formulas. Every origin is validated before any batch mutation or type
assignment. A present value must be an object with only `src`, `run_uid`,
`request_hash`, and `text`; every supplied value must be a string of at most
300 characters. Null, invalid JSON, unknown keys, and wrong types return 400.
Missing provenance preserves the existing request and record shapes.

The metadata becomes `ChangeEntry.origin`, defaulting to None. The pending
tray's dataclass serializer preserves it, including on restart. Replay tags
gain a fourth element only when an origin is present; older two- and
three-element tags retain their shapes. Undo journal entries and redo frames
copy the origin. Journal inverse and forward staging also preserve it.

The Review row displays escaped origin text beneath the path. A homogeneous
plot apply records the origin's `src` and `run_uid` and puts `request_hash`
and `text` in the hub event's existing `ref` field. A mixed apply keeps its
door source and stores origins by path in `ref.origins`, using the latest
edit's origin for a repeated path. Ordinary edits clear earlier attribution
at that path. Saved units retain their origins until the later apply.

The ledger projects `ref` into an optional `sm_events.ref` column, added by
the existing schema migration. Value history selects the matching path's
origin for mixed applies. Its shared presentation includes `ref.text` and
describes the linked run as the derivation source. SM does not write an
external Calibration log here; the event reference is available to its
readers. The staging endpoint inventory is unchanged.
