# Three-cycle native merchant acceptance

This temporary, machine-local mode requests three real hunt → town → merchant
delivery → native refill/update → verified hunting-resume cycles. It does not
change portable profiles, kill metrics, ordinary return thresholds, merchant
permissions, input ownership, or global rollout/parity policy. Completion does
not stop or restart farming; it only disarms the temporary early-return trigger.
Global rollout remains disabled unless approved separately in a later release.

## Authenticated app-owned control contract

Use the existing authenticated merchant bridge (`/merchants`). The app reads its
own source/recipient memory; caller-supplied inventory or qualification is never
accepted. Arm with Farming Off, no running input/manual/pause holds, a selected
native route, verified round-trip transit, enabled native banking, promoted
farmer delivery qualification, and at least one exact live Market merchant with
trading and automatic refill enabled, available capacity, qualified booth input,
and no pending transaction/manual hold:

```json
{"action":"farmer-loop-acceptance","enabled":true,"farmer_profile_id":"<farmer UUID>","request_id":"<unique stable run request>","cycles":3}
```

The response includes `run_id`, inventory UID baseline, pinned farmer and merchant
UUID/process/character identities, and qualification file/build digest. Repeating
the same request ID returns its current evidence and never resets or rearms it.
Arming sends no gameplay input and does not switch Farming On. Start the normal
native route separately using the existing Farming control.

```json
{"action":"farmer-loop-acceptance-status"}
{"action":"farmer-loop-acceptance","enabled":false,"farmer_profile_id":"<farmer UUID>"}
```

Disable is input-free and allowed between cycles. An unresolved active cycle is
retained, not silently abandoned. Farming Off, Global Stop, F11/F12, manual mouse
and session fences remain authoritative at every native input boundary.

To abandon a cycle, first stop Farming and wait for input to release. The exact
run-bound, input-free abort is allowed only before any town/input admission
(`triggered`) or after terminal bilateral delivery and completed refill service
(`awaiting_hunt`). Native journals and current ownership are checked read-only.
Any pending transaction, journey or refill blocks abort. A partially completed
town phase must be reconciled through its existing native workflow first.
The crash boundary after saving `town_started` but before the native town input
marker is also abortable when no delivery was admitted and exact stock remains.

```json
{"action":"farmer-loop-acceptance-abort","farmer_profile_id":"<farmer UUID>","run_id":"<status run_id>"}
```

Abort archives an `aborted_cycle`, counts no success, and disarms trial permission.
It does not clear native transaction or TownVisit evidence, change controls,
withdraw an item, repeat a trade, or declare an uncertain action successful.

For older interrupted cycles whose item was already banked by warehouse fallback,
first reconcile native journals and stop all input. A
separate explicit unknown/deferred override can then disarm this temporary mode:

```json
{"action":"farmer-loop-acceptance-override-preview","farmer_profile_id":"<farmer UUID>","run_id":"<run_id>"}
{"action":"farmer-loop-acceptance-override","farmer_profile_id":"<farmer UUID>","run_id":"<run_id>","operator_confirmed":true,"incident_digest":"<preview incident_digest>"}
```

The preview and apply must observe identical fresh farmer/warehouse ownership
within 30 seconds. Warehouse evidence is read only if already available; no panel
is opened. Unavailable warehouse evidence explicitly makes no ownership inference.
Override preserves the entire prior cycle and all other native journals, labels
the outcome `unknown_or_deferred`, and never counts a completed acceptance cycle.

## Native execution and proof

Only a newly carried `delivery.eligible` UID outside the activation/current-cycle
baseline can trigger. Exact native pickup history and a verified kill after the
cycle baseline must both exist; manual/recovered inventory and consumables cannot
substitute. Loose Meteors and storage-only valuables do not trigger delivery and
retain their existing bank/consolidation policy. Ordinary DragonBalls, eligible
Super/+ equipment and MeteorScrolls follow the existing delivery eligibility.

The normal route stops combat and performs native town banking/delivery; it banks
loose Meteors before departure. The durable journey is scoped to the selected
item and never withdraws an unrelated stored scroll. Its local trial gate permits
only the pinned run/cycle/item, exact merchant roster, current process/build,
qualification digest, and journaled native request/grant. Qualification, current
capacity, bilateral reconciliation and manual input checks are never bypassed.

An expired Market service visit gets at most one new 60-second window per exact
acceptance cycle, only before any delivery admission or input reservation. The
native bridge requires the active town controller's current PID/start time and
heartbeat, stopped combat, no manual Stop or ownership hold, unchanged journey,
and fresh memory proving the pinned process still carries the exact item. Source
admissions/transactions, receiver reservations, and work-window history all block
renewal, including terminal submissions from this town visit. The SQLite cycle
seals the replacement visit and deadline before the visit file changes; a crash
or lost response can recover only that same deadline. Ordinary visits and all
post-submission budgets remain unchanged. If delivery still cannot complete, the
scoped item stays carried and the acceptance route stops before warehouse input.

A cycle requires the exact admitted request's terminal, cleanup-free bilateral
receipt (`release_route`); a subsequent completed native merchant refill check;
and the existing TownVisit proof of a new verified hunting kill. The refill's
immutable `source_delivery_operation_id` survives budget pause/restart and new
native grants. The delivered item must be freshly verified listed or explicitly
queued; unknown prices remain queued, never guessed. These are not sales claims.

All transitions use a synchronous SQLite journal under the active farmer's
machine-local `.runtime/farmer-loop-acceptance.sqlite3`; event rows preserve prior
runs and aborted work. Three verified cycles set `enabled=false, phase=completed`.
Lost admission/receipt acknowledgements reconcile by exact saved request, never
historical UID alone. Changed identity, missing item, uncompleted refill, or
uncertain native work prevents a successful cycle and remains visible for review.

Forced visits are labeled `merchant_acceptance` and explicitly excluded from the
natural two-hour cycle evaluator. This test does not promote global parity, select
a route winner, or replace the 60 verified-kills/minute assessment, which includes
normal travel, shopping, loot and recovery downtime.
