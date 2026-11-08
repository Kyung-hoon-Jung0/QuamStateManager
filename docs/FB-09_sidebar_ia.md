# Sidebar information architecture (FB-09)

The final user decisions of 2026-11-08 supersede the original proposal and docs/69.
This change affects display names, navigation grouping and active marks. Existing
routes, page tokens, editing, working-copy and apply contracts remain unchanged.

The main group follows workflow order:

| Parent | Default destination | Other children |
| --- | --- | --- |
| Configuration | Generate config (`/generate`) | Modify config (`/regenerate`), Config Viewer |
| Instrument Wiring | `/instrument` (standalone) | Floating-panel button retained |
| Chip Status | Overview | Existing sections and CZ gate plan |
| Chip Components | Qubits | Existing conditional component pages |
| State Edit | Live state edit (table view) | Live state edit (Json tree view), Pulses, Z-line distortion |
| Datasets | Runs | Collections, Trends |
| Experiment logs | Calibration log | State logs (`/state-history`), Param logs (`/param-history`) |

Compare, Diagnostics, OPX monitor, Agent and the experimental tools follow.
Projects and State Load stay above this group. Conditional entries retain their
existing availability rules. Decision U-7 merges all three log pages under Experiment logs and removes the
separate History parent. Existing saved orders ignore the removed parent key.

Parent links carry `data-nav-parent` so the destination child owns the active
mark on full renders and pane-only navigation. The active child's submenu opens
on navigation even if previously collapsed. The parent link and its disclosure
button remain separate controls. OPX monitor opens its existing Live child.

All main parents share `data-nav-group="main"`, including Configuration. The
pointer drag implementation is unchanged. Parents move with their child DOM
nodes; Alt+Arrow and Reset menu order still use the existing core.

`quam_sidebar_order_v2` replaces v1. On the first read without v2, the migration
maps old parent keys to the new parent keys and retains saved groups. The normal
known-item slot algorithm preserves known items' relative order, ignores missing
entries and leaves new entries in their default slots. The old key is retired,
so Reset cannot resurrect the legacy order. Former creation-group ordering has
no remaining sibling order to preserve. Existing page keys remain on the child
entries; new parents have their own keys.

The command palette and header search share the updated page list, including
parent names and Runs. Help and the landing tour describe the new grouping.
Modify config replaces the previous feature name in page headings, links,
tooltips, wizard leave prompts and the emitted recipe heading.

Validation uses synthetic Flask renders, the shipped JS under jsdom, mutation
checks, and real Chrome on a chip copy. Evidence stays outside the repository.
Chrome checks cover default-child navigation, active-child visibility, existing
entries, pointer reorder persistence, and expanded/collapsed layouts at 1024,
1440 and 1920 pixels. Conditional entries absent from that chip are covered by
the template-preservation pin. No chip edits are needed for this UI change;
peek, working-copy and copied-live values are checked for agreement.
