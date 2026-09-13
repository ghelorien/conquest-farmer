# Full portable UI and farmer/merchant integration

This merge combines both histories, not only the socket fixes:

- Farmer/merchant branch through `e9ea6e7` (behavior through `ca58e7b`).
- Entire portable-character branch through `588ef2a`.

It restores profile management, dynamic character tabs, per-character state,
credentials and settings adapters, portable installation/hosting diagnostics,
beginner routes and the later local farmer fixes alongside the other PC's
delivery guards, panel cleanup, activity reporting and durable kill totals.
The socket and ground-identity corrections and their evidence are retained.
Uncommitted runtime shop catalogs and local credentials are not source changes
and were not copied between worktrees.

Five textual conflicts were resolved explicitly. The resulting code retains
the latest booth stability/position checks and adds profile verification after
them; retains input-before-press guards and transient observation handling;
supports both healing and travel-panel actions; and keeps the two-phase
merchant diagnostic preflight with profile-selected merchants and local paths.
Pending healing-panel cleanup runs before periodic general panel cleanup.

Additional integration review connected the newer panel cleanup and delivery
qualification helpers to the selected farmer instead of literal Parasite.
Farmer transfer preferences now use the active character state directory.
Full Market snapshots verify the farmer context for farmer trades as well as
town preflight, without resolving the farmer as a merchant. Legacy execution
without a portable context still uses Parasite and legacy paths.

## Checks

- Initial merged suite: 2,302 passed.
- Targeted integration checks after adapter corrections: 218 passed.
- Final suite: 2,310 passed and one Windows Tk resource-loading failure in
  `test_sidebar`. The installed theme file was present; the failed test passed
  immediately in isolation (1 passed) without a source change.
- Hidden portable UI smoke check passed with the merged source and its local
  launch runtime: dynamic profiles, duplicate-server names, reserved-name
  isolation and paused startup. No gameplay input is sent by this check.
- Desktop startup imports and whitespace checks passed.

The portable profile manager currently supports adding/editing profiles,
import/export, templates, local credentials, trusted deliveries and enabling or
disabling profiles on a PC. Disabling retains its records; this merge does not
add destructive profile deletion.

The previously documented Parasite-only desktop limitation is superseded by
this full integration. Socket-only ground pickup, background input, and remote
PC control remain unqualified/unsupported as documented previously. Full live
merchant trade/repricing cycles require attached merchant accounts; this PC
currently has only Kilhiam.
