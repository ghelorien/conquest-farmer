# Experimental background input

**Qualification failed the interrupted-drag acceptance test. Live input
experiments are disabled.** Read-only observation and native surface restoration
remain available. This is an experimental diagnostic backend, not a qualified
production input backend.
Merchant and farmer production input routing is unchanged. Only the manager
coordinates live tests; agent experiments use disposable windows.

## Current evidence

Reports live in the local, ignored `reports/merchants/background` directory.
These contain verified process creation identity, frame and input queue samples,
processed mouse/modifier/control state, native focus/geometry, and outcome checks.
Key and text event payloads are excluded from the observation reader.

| Capability | Status | Evidence and limits |
| --- | --- | --- |
| Ordinary posted background hover | Failed | Both merchants: posted and acknowledged movement, including tracking cancellation, produced no stable hover. |
| Cause of ordinary hover failure | Passed | Spiritual `b9aca631c511445081fac81b29870d8a.json` records a valid mouse position followed by the mouse-leave invalid-position sentinel in the same input queue. |
| Fixed-size unowned background surface | Passed for bounded probes | Both merchants kept advancing frames offscreen and restored their original native window state without foreground activation. Minimized-app endurance remains untested. |
| Event-ordering experiment in disposable windows | Passed | `mouse-dummy-verification.json`: real Windows message ordering; modeled frame processing is explicitly not gameplay evidence. |
| Single background hover using a character barrier | Passed for bounded probes | Spiritual `b06b263370e24a0d866ca4065c2d50e0.json`, Dutch `e09310be3489496685ce6d78c730a705.json`: one hover frame each; stock and position unchanged. |
| Sustained background hover | Passed for bounded probes | Spiritual `01333e5f1ad8458083f236a24d8efffd.json`, Dutch `8ee2cc706a164fc9ac8a136b301fe9a5.json`: 12 consecutive hover frames each using alternating adjacent points inside the inventory control. Foreground and physical cursor unchanged. |
| Isolated Ctrl keyboard table in disposable windows | Passed for bounded lab | `keyboard-dummy-verification.json`: target GetKeyState saw Ctrl; global asynchronous Ctrl and foreground remained unchanged; table restored and helper detached. This does not qualify actual game jumping. |
| Isolated Ctrl acknowledged by real client | Passed for bounded probes | Spiritual `479e72dc4acc4a44a703f3d80cb09606.json`, Dutch `32b78843908f4a5fb8e0d6d465539c66.json`: processed Ctrl became held then released; helper detached; empty queue/neutral keys across two fresh frames; stock and position unchanged. |
| Background inventory press/release | Passed for bounded probes | Spiritual `5816693c5f264a59bebfc3b25b07a049.json`, Dutch `d1db0ab90e2445b68d89eaa0182e966c.json`: exact hover/ActiveID, processed button-down and release; stock/silver/position unchanged. Not a general action or endurance qualification. |
| Inventory drag and cancellation | Passed for bounded probes | Spiritual `b915c9b970ba45d986203086fba52dd3.json`, Dutch `a2e829ac8e3447e6b3adec8f6d2fbe11.json`: exact CQITEM source UID, held movement, return to the original cell, release, unchanged stock/silver/position. |
| Interruption during a held drag | **Failed** | Spiritual `9b326d87fe3e4983b29ae221224946b4.json`: Stop caused an emergency button-up; queued events drained, but the stock read failed. Later reconciliation could not locate source ApeHat UID292976902. A neutral input queue is insufficient proof of a safe cancellation. |
| Input with Conquest minimized | Passed for bounded probes | Spiritual Ctrl `a77224a7435343f1883c57a08a6902ad.json` and Dutch drag `457b08a22cfe49c190e44b51b0c0ce22.json` record minimized=True throughout, independent foreground unchanged and foreground capture zero. Endurance/concurrent physical typing remains untested. |
| Background scroll, price entry and cancellation | Untested | Price/cancel implementation has no Enter or Confirm path. Live attempt `0ead6afcdf124654b917aa90d39b3aa6.json` stopped before input because Spiritual no longer had an inventory item. Dutch's booth was full. |
| Parasite movement, actual jumping, combat, healing and loot | Untested | Parasite is not running and has no attached farmer observer or saved local login. `parasite-initial.json` records unavailable observation. Arrival alone must not count as jumping. |
| Concurrent foreground typing and modifier use | Untested live | Recorded hover runs had a stationary physical cursor. |
| Global Stop UI, takeover, geometry changes and restart | Untested live | Diagnostic Stop was tested and failed stock reconciliation. Other deterministic guard/lifecycle tests do not substitute for live acceptance. |

The character barrier is U+0001. The inspected renderer queues it, its input
trickling separates movement frames, and the text filter rejects it before text
callbacks. This is not claimed to be entirely free of internal side effects:
one game message handler updates an integrity accumulator on every message.
Current probes require idle keys/buttons, no active control, no price dialog,
matching executable code, and the inspected in-game state/handler registry.

## Running diagnostics

Only `observe` and `park-observe` can run. Other named modes are retained as
reviewable code but rejected before runtime access; there is no live override.
Use the authenticated localhost bridge from the running Conquest app. Do not
print its local token. `background-probe` accepts only named bounded experiments,
with `character` set to a connected merchant. `background-probe-stop` cancels;
`background-probe-restore` retries retained native window restoration.

Diagnostics require stopped farmer input, paused merchant operations, no pending
transactions, and exclusive input ownership. They do not obtain foreground focus
or change production qualification files. Frozen frames, identity/geometry
changes, manual target activation and Global Stop abort the probe.

Failed restoration retains the exact saved surface state and prevents ordinary
layout/embedding from overwriting it. Embed, Release, Close and Restart defer
until cancellation and restoration complete. An unresolved restoration keeps
Conquest and its bridge alive for recovery.

## Decision

The current native backend failed qualification. The interrupted-drag report's
last sample has 49 queued events, including movement to the displaced source
point and a final invalid mouse-leave position. Emergency cleanup posted only
button-up, whose coordinates do not change ImGui's processed pointer. A release
after those pending events can therefore occur outside the intended control.
This is a plausible drop mechanism, not proof of the item's eventual recipient.

The inspected renderer at `0xd2cbc` checks active dragging; `0xd2d4e` takes an
outside-UI path when WantCaptureMouse is false, accepts delivered CQITEM data,
reads processed mouse coordinates and calls protected handler `0xd45a0`.
The button-up handler only queues button state, so its LPARAM cannot repair an
invalid processed mouse position. The protected handler's final network action
has not been established.

`apehat-reconciliation.json` records the exact UID absent from inventory, booth,
known equipment slots and the nearby ground snapshot. Journal queries found no
listing or sale record for that UID. Recovery has not been verified.

The manager and mouse agent reviewed alternative cancellation sequences:

- Returning to the source with a barrier is a candidate only after fresh exact
  UID/slot/geometry checks, drained pending events, and acknowledged source hover
  and held state. A pending leave can still invalidate release. Frozen rendering
  and manual takeover prevent guaranteed bounded acknowledgment.
- Escape is not a proven item-drag cancel. Its captured global handler closes
  a popup/window; the drag-clear path depends on delivery/expiry.
- WM_CANCELMODE routes to an ImGui no-op. Cancelling native capture does not
  establish semantic cancellation of the dragged item.
- Smaller acknowledged bursts reduce backlog and latency, but cannot retract
  movement already queued or eliminate immediate mouse-leave generation.

Unproven cleanup must not be tested by risking another valuable item. This result does not prove
that every possible native backend is impossible; it does mean this one must
remain disabled. Production routing, broader automation and VMs remain deferred.

488 focused background/merchant/desktop/window-hosting tests passed. The running
app was reloaded and verified to reject live experimental modes before acquiring
input. Both merchants remain connected with no input owner.
