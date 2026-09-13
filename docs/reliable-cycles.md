# Reliable town and merchant cycles

Required restocking and urgent storage use a durable town visit, preserved across
controller restarts. The visit finishes only when the farmer returns to its hunting
map and the existing native kill journal records a new verified kill. This does
not reset the farming session or its statistics.

Merchant approach uses the same memory-qualified recipient and usable viewport
as the final trade press. A checked standing tile is a plan; fresh memory at
arrival must still prove that the intended character is actionable. Occupied
remote-player tiles are excluded. Static booth and NPC obstruction still require
terrain checks, final projection checks, and observed movement progress.

A required Market visit has one persisted 60-second merchant-service deadline.
Approaches, corrections, both recipients, split batches and refill share it.
Restarting a controller or starting another batch cannot extend it. Routine
hunting interruptions remain limited to a 15-second work window per 900 seconds.
Expired refill windows retain their queue and verified listing count, separately
from the last completed capacity check. Unknown prices stay deferred.

Deliveries persist admission and operation identifiers before input. The source
journal, receiver reservation, refill records and route carry visit identifiers.
The bridge returns an explicit outcome and permitted next action. A missing
acknowledgement triggers read-only reconciliation of the existing operation;
the route never repeats `delivery-start`. Exact ownership, currency, binding,
sockets and item quantities remain locked when reconciliation is ambiguous.
Separately verified sales can explain only their exact stock and silver changes.

Each action-capable worker and queued UI callback captures its original input
grant. Expiration, manual control changes and Stop invalidate that grant. The
farmer resumes only after held input is released and action-capable workers
acknowledge revocation. Read-only reconciliation can finish afterward.

Host and panel layout revisions invalidate queued targets. Structural changes
need two matching observations at least 250 milliseconds apart. Ordinary camera
or target movement refreshes projections without this debounce. A moving drag
releases its held button and reconciles the attempted result.

Compatibility applies to the shared farmer engine and standalone installations.
Live qualification belongs to each specific profile and client build; another
profile does not inherit Parasite's trade qualification automatically. Existing
profile speeds, routes, credentials, supply triggers and loot protection remain
in force. Warehouse fallback remains available after ownership is resolved.

## Qualification requirements

Unit and simulated fault tests are prerequisites, not proof of unattended play.
Controlled low-value delivery/refill on Dutch and Spiritual, panel handling and
warehouse fallback must be recorded before an unattended rollout. The native
two-hour run must include a natural complete town/trade/refill/return cycle,
resumed verified kills, and at least 40 verified kills per minute across the
entire observation, including all downtime. Fifty per minute is the stretch goal.

`scripts/monitor_unified_validation.py` reads native journals and reports missing
cycle coverage, unresolved operations and observed process/session interruptions.
Its configuration must record the original app/route process and kill-session
identities. An assisted recovery, reload or repair invalidates the uninterrupted
run and requires another run after the fix. The observer never controls the game.
Per-merchant and per-profile live coverage must be reported explicitly.

Rollout status is recorded with the dated qualification artifacts. Do not infer
live approval from passing tests or the presence of this implementation.
