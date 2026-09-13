# Full portable UI and farmer/merchant integration

This merge combines both histories, not only the socket fixes:

- Farmer/merchant branch through `d168881` (including merchant recovery and booth setup).
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

- Final complete suite after integrating `d168881`: **2,360 passed**.
- Targeted launch, login identity and stall approach checks: 48 passed, including
  the selected merchant's installation and exact-process login reattachment.
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

## Latest behavior integration

Commit `d168881` arrived during validation and was merged as well. Recovery keeps
its fingerprint-checked installed-client launch and scoped input ownership,
while resolving the selected merchant's local installation. Login discovery keeps
profile attachment diagnostics and skips in-world identity reads only for a
previously pinned login process. The vacant-stall approach resolves terrain from
the merchant installation; diagnostic workers use local state paths.

## Local live check

The combined app attached to Kilhiam with Farming Off. Eight consecutive bridge
checks reported the selected profile, available memory observations and stopped
control. Equipment reads retained ScarletBow's one empty socket (255/0).
A request for a different profile was rejected by the authenticated bridge.
No merchant account is present on this PC, so merchant gameplay cycles and the
other PC's display/embedding checks remain untested locally.
