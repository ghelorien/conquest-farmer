# Portable character profiles and native client hosting

Published integration branch: `codex/merchant-automation`. The full portable
history through `588ef2a` is combined with behavior through `d168881`, including
merchant client recovery and native booth setup. Combat, farming, pricing,
delivery and recovery policy remain in those engines. See
[full integration evidence](full-portable-integration.md) for tests and live limits.
This integration does not enable background input or remote-PC control.

## Open the combined build

Install the game separately on each PC. From this branch's repository root:

```powershell
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e '.[dev]'
.\.venv\Scripts\pythonw.exe scripts/start_desktop_app.py
```

On a fresh PC, the character editor opens first. Add the exact character name,
server and role; select the profile to use on this PC. Use **Account login** and
**Discord destination** to store secrets with Windows DPAPI, and **Game
installation** to locate this PC's `ImConquer.exe`. The app discovers the
installation from a verified running executable when possible. It does not
request elevation merely because Embed was clicked.

Overview contains **Manage characters / import or export settings (restart)**.
Stop farming, merchant management and refilling and reconcile pending work
before opening the editor. Other saved farmer tabs show their preferences and
provide a selection/restart action. Each process uses one selected farmer;
merchant input remains coordinated with that farmer. Other PCs have separate
registries and runtime state.

New profiles start paused, including merchant refill. Existing migrated merchant
pause/refill intent is preserved. User labels can change without renaming storage.
Adding a farmer never authorizes it to trade. Trusted delivery sources require an
explicit name, server and verified character UID; owned merchants are derived
from the configured merchant roster, including merchants disabled on this PC.

## Settings and observations

`{}` means the existing engine's automatic defaults. Templates and explicit
overrides support healing threshold/cooldown, healing enablement, jump/Scatter,
single-target preference, kiting, range and recovery preference. An unavailable
setting is shown as unavailable and cannot bypass engine checks. Requested attack
range is capped by the engine's observed range. `pickup_enabled` is reserved in
the preference schema but currently unavailable through the engine adapter.

The character tab separately displays fresh engine-provided level, class ID,
equipment and available learned Scatter information. An absent skill is not
invented, and other learned skills are marked unavailable when the engine has no
presentation API for them. The current combat engine may block a fresh archer
without its required bow/Scatter observations; implementing additional combat
behavior remains the behavior workstream's responsibility.

Export/import uses an explicit preference allowlist. No credentials, webhook,
observed level/class/skills/equipment, trusted identities, account/profile IDs,
PID/HWND, transaction, qualification or monitor geometry is exported. Import
creates a new paused profile with a new account reference. Custom route copies
are stored per character; packaged route definitions remain shared read-only
defaults. Route selections and custom definitions survive legacy migration.

## State layout

The normal application root is `%LOCALAPPDATA%\Conquest`:

| Location | Contents |
| --- | --- |
| `profiles.json` | Stable profile/account IDs, expected identity, role, templates and overrides |
| `machine.json` | Selected profile and PC-local installation paths |
| `accounts/<account-id>/account.dpapi` | Encrypted login |
| `characters/<profile-id>/.runtime` | Controls, qualifications and recovery state |
| `characters/<profile-id>/reports` | Character logs, farmer journals and notification cursors |
| `characters/<profile-id>/routes` | Character-specific route copies |
| `machine-state/reports/merchants/journal.sqlite3` | Merchant transactions, sales, events and cursors keyed by profile ID |
| `machine-state/.runtime/merchants` | Shared market, shops notifier and app state |
| `locks/input.lock`, `app.lock` | Input and application ownership |

`--data-root` is an explicit test/deployment override, not a portable preference.
Use the same local root for all normal launches on one PC. Separate data roots
must not be used to run competing foreground controllers on one desktop.

## Migration and rollback

The launcher offers migration when it finds a legacy shop journal and no local
destination. For an explicit legacy checkout:

```powershell
.\.venv\Scripts\pythonw.exe scripts/start_desktop_app.py --migrate-from 'C:\path\to\old\Conquest'
```

Close the old app and workers first. Migration stages a complete copy beside the
destination, uses SQLite's backup API, remaps merchant record keys to stable IDs,
closes database handles, then atomically activates the destination. A completion
marker makes repeating the import idempotent. Existing destinations are never
overwritten. Failure leaves the original intact and the destination inactive.

Original files remain available for rollback, and a pre-remapping shop journal
backup is retained under `migration-backup`. Pending transactions, sales totals,
history, route selection and notification state are retained. Input approvals,
bridge tokens and reload-resume permissions are excluded. Legacy Parasite trust
is imported only when a consistent peer UID exists in durable delivery intent;
otherwise configure trust locally before enabling trades.

Rollback means closing the review build and reopening the old checkout with its
original state. After the new build has performed transactions, reconcile its
new receipts before reverting; the preserved old database cannot know about
later sales or deliveries. Do not run old and new controllers together.

## Hosting and diagnostics

Attachment records discovery, access, identity, attachment, memory and behavior
stages. **Copy attachment diagnostics** includes stage history, sanitized error
type/code/stack locations, identity and geometry evidence, without secrets.
Hosting and automation readiness are separate: terrain/recovery setup failure
keeps the safely hosted client visible and blocks automation. Use **Retry
automation setup** after correcting the installation. Hosting failure or uncertain
ownership restores the original window.

The owned top-level host preserves its native DPI mode; it does not force
cross-process reparenting. Restoring an owned window now also avoids an unnecessary
`SetParent` call. Microsoft documents cross-process DPI resets in
[SetParent](https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-setparent).
Native `SetWindowPlacement` retains Windows' monitor-removal recovery behavior,
and saved app geometry is clamped to current monitor work areas. See
[SetWindowPlacement](https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-setwindowplacement).

Controls are scrollable, farmer controls can collapse, and the full supported
1036×793 viewport is required for portable hosting. Smaller panes offer **Use
separate game window** / **More → Open game in separate window**. A geometry
change invalidates input access; old coordinates are never stretched. A smaller
display or higher DPI may require the separate-window option rather than embedding.

## Bridge adapter contract

The existing authenticated localhost protocol is retained. Merchant commands
accept `profile_id` in place of `character`; name-only commands must resolve
unambiguously. Conflicting ID/name values are rejected. `{"action":"profiles"}`
returns non-secret profile descriptors; merchant status includes `profile_id`
and attachment diagnostics. The selected farmer's embedded bridge accepts an
optional `profile_id` and rejects a different profile before dispatch; its health
response includes the active ID. `attach-farmer-client` similarly accepts the
selected farmer profile ID plus the existing PID/creation-time pin.

`CharacterContext` carries the verified identity expectation, resolved settings,
installation and local state/credential paths. It is passed to observers and
resolved by compatibility adapters at existing engine boundaries. Launcher
environment selection happens before importing engine modules; subprocesses
inherit the same character namespace. There is no new combat/pricing decision
engine and no global substitution of character names in behavior policy.

## Validation and release gate

Automated tests exercise registry isolation, role/trust gates, import allowlists,
DPAPI compatibility, durable journal keys, migration/restart failure handling,
input ownership, route isolation, attachment rollback and behavior-readiness
failure. Geometry tests cover 1366×768, 1920×1080 and 3840×2160 at 100%, 125%,
150%, 175% and 200%, including negative origins and removal of a saved monitor.
These are layout calculations, not claims of live hardware testing.

`python scripts/validate_portable_ui.py` constructs the hidden native UI with
empty discovery and disabled input hooks/workers. It checks dynamic tabs,
same-name profiles across servers, a merchant named Farmer, paused startup and
profile-aware status. It never attaches to or sends input to a game.

| Acceptance evidence | Status |
| --- | --- |
| Integrated farmer/merchant automated regressions | Passed; see validation record |
| Portable registry/migration/secret/geometry tests | Passed; see validation record |
| Hidden native UI startup with synthetic profiles | Passed |
| Reproduce the other PC's actual rollback and capture its stage | **Untested — requires that PC** |
| Live attach/tab switch/resize/minimize/release/reattach on both PCs | **Untested** |
| Live mixed-DPI/removed-monitor game rendering | **Untested** |
| Parasite and fresh archer on separate PCs with independent live sessions | **Untested** |

Keep the active launcher unchanged until live acceptance completes. On the other
PC, capture **Copy attachment diagnostics** after its first failure; the current
evidence does not establish that terrain initialization was its exact cause.
