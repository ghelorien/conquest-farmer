# Farmer tab integration baseline

Status: source integration completed against farmer commit
`117cc08d9ed8d418ae5b483f49196b143322a2a0`, retaining the newer farmer fixes.
The unified UI reuses the existing DesktopApp sidebar and client pane inside
the Farmer tab; it does not substitute an older farmer implementation.
All 43 DesktopApp methods are retained. Of the 59 files in the farmer update,
52 remain identical; the seven combined files are shared UI/input helpers,
documentation/policy, and the amount-entry viewport regression test.

The shared amount-entry helper derives the live size for farmer callers,
checks an explicit calibrated size for merchant callers, and honors the current
root HWND for focus checks. Its tests cover mismatched calibration and resize
during entry, including release of a held modifier after interruption.
The newer reattach ordering, dynamic combat/dialogue dimensions, two-pack arrow
policy, recovery and farmer Discord targets are retained.

Source parity evidence is recorded locally in
`reports/github-farmer-integration-parity.json`; final full-suite output is in
`reports/github-integration-pytest.txt`: **1,727 tests passed** on September 12,
2026, including the updated farmer regressions and merchant integration checks.
No launcher shortcut, local credentials,
farmer route selection or running game session was replaced during publication.
Live combat/recovery acceptance was not repeated by this source integration.

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
