# Merchant capacity and full-cycle recovery — 2026-09-13

The live Bandit trial exposed shared capacity: inventory plus listed booth stock
cannot exceed forty owned slots. Spiritual had 8 inventory items + 32 listings;
Dutch had 7 + 32. The old inventory-only check wrongly offered more items to
Spiritual. Planning, incoming trade validation, reservations, readiness and
warehouse fallback now use the shared capacity helper. Urgent DragonBalls and
+2 equipment take priority when only part of a batch fits. Star DragonBalls
remain storage-only. After every delivery the route rereads both merchants;
full recipients are skipped and residual valuables use warehouse storage.

## Live evidence

- The first automatic Dutch delivery transferred five exact item UIDs.
- The interrupted three-item Spiritual batch was reconciled as partially
  aborted, not falsely marked successful: Meteor 293796399 reached Spiritual;
  +1 StrangeBlade 293796758 and DragonBall 293797302 remained with Parasite.
- The empty, unaccepted Spiritual trade was closed using fingerprint-pinned
  native control geometry and a memory-verified hover. No currency or offered
  items were present. Both inventories remained unchanged during cancellation.
- With the fix loaded, automatic delivery `route-delivery:8e8785d0de344b11a2d0ffa5729beb7e`
  transferred DragonBall 293797302 to Dutch in approximately 7.25 seconds.
  Exact source and receiver inventories reconciled. Both merchants then held
  forty items across inventory and shop.
- Warehouse interaction from (197,179) opened an adjacent shop and failed.
  After two confirmed panel-open failures, the new bounded closer approach
  jumped eleven tiles to (186,176) in 1.4 seconds, without a stall or HP loss.
  +1 StrangeBlade 293796758 was then verified in the Market warehouse.
- The saved Market return journey completed, transport funding remained at
  200 silver after banking, and verified Bandit kills resumed.
- Merchant refill checks ran and reported booth_full. The stocked urgent bank
  visit correctly made no unnecessary supply purchases. A subsequent necessary
  restock/refill is still part of the open full-cycle validation.

This was an assisted recovery, not a clean unattended qualification. The trial
also recorded an earlier death and missing Meteor. Its debugging and travel
 downtime must remain in elapsed performance measurements. Do not use a fresh
app's short-term kill rate to describe the entire trial.

## Validation

155 focused capacity, delivery, journey, refill, focus and recovery tests passed.
An additional 182 merchant integration, bridge, probe, readiness and banking
tests passed. Tests cover shared capacity, a last-slot DragonBall delivery to
Dutch followed by warehouse fallback, refusal to cancel accepted/nonempty
trades, rejection of ambiguous partial ownership, and a bounded closer bank
approach that never repeats a deposit or currency transfer.

Runtime journals, credentials, diagnostic memory snapshots and tokens remain
local and are not included in this report or the source publication.

## Follow-up: deferred handoffs and town refill opportunities

Live hunting handoffs correctly deferred twice when no quiet spot was verified
within twelve seconds. They granted no merchant input and resumed hunting,
while retaining the next fifteen-minute deadline. Spiritual subsequently had
one free slot following a sale; refill remained pending until a safe opportunity.

A temporary "Waiting for a safe farmer handoff" no longer hides a connected,
qualified merchant's available delivery capacity. Transaction failures and
manual merchant pause still block readiness, and planning itself grants no input.
After urgent valuables are verified in storage, an already-required town visit
now offers the same bounded refill window as a regular restock visit. It does
not purchase unneeded supplies or perform merchant work before storage succeeds.

The handoff policy path is isolated in unit tests, preventing tests from using
live merchant configuration. The updated route/handoff/refill suites passed
163 tests in the live source and 165 in the publication checkout. The changed
runtime was loaded through a safe reload; actual refill posting remains under
live observation rather than being inferred from these tests.
