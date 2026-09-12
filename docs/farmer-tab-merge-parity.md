# Farmer tab integration baseline

Status: awaiting the source/location of the combined sellers/farmer UI.
The local farmer contains fixes newer than GitHub main 1b4d916. Do not replace
it wholesale with that older revision. Source hashes are recorded in
reports/integration/farmer-source-baseline.json. This is an inventory, not
proof of feature parity with a combined UI that has not yet been obtained.

## Controls and displays that must remain available

- Client selection and refresh; launch, embed, release, show/focus, retry
  reconnect, safe reload, and expandable client diagnostics.
- Farming On/Off, F10 start, F11 pause/resume, F12 stop, manual mouse priority.
- Current activity and failure/recovery state, verified kills and rates,
  current level, next-level estimate, runback progress.
- Saved-route selector and Save copy, level-bracket presets, Resume leveling,
  session hold and expandable route details.
- Nearby monster group toggles and automatic matched IDs; pickup history
  with timestamp, item quality/enhancement, quantity and persistence.

## Backend behavior and data that the tab must retain

- Memory-only observations; exact process/HWND identity and live dimensions.
- Existing elevated worker reuse, reversible embedding, reattach ordering,
  keyboard focus, reconnect and safe-spot reload with resume intent.
- Native combat, jump/scatter, Fly, healing, ammunition, death/revival and
  return travel; stale observations must not terminate life recovery.
- Route progression/holds, city transport, path recovery and efficiency
  telemetry; existing controller ownership and manual-stop semantics.
- Current loot eligibility, owned-loot checks, pickup receipts/history and
  Discord notifications; no currency or Elite-only pickup. Ground socket-only
  eligibility is not implemented and must not be presented as existing.
- Empty-supply/full-inventory restocking, five selected potions, at most one
  equipped and one spare arrow pack, eligible equipment and arrow upgrades.
- Valuable protection, urgent Dragonball/+2 banking, silver reserve, warehouse
  overflow, ten-Meteor packing, Market storage and verified city return.
- All saved profiles, route selections, journals, pickup history and runtime
  settings remain associated with their existing farmer instance. Credentials
  and webhook secrets must not be embedded in the UI or copied into GitHub.

## Integration verification once the UI is available

Map each control to the current implementation instead of substituting an
older farmer backend. Verify tab-switch behavior so seller/farmer workers do
not compete for input or change On/Off intent. Verify embedded window bounds,
keyboard/mouse priority, safe reload/reconnect, history persistence and route
selection. Keep every seller feature supplied by the combined UI. Run the
relevant regressions and a controlled live verification before replacing the
user's active launcher. Record any unavailable feature explicitly.
