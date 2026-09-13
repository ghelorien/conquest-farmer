# Live rollout evidence — 13 September 2026

This is an incomplete qualification record. Passing automated tests does not
establish an uninterrupted farming and merchant cycle.

## Notification replay corrected

The farmer notifier replayed historical merchant events when it first adopted
that event stream. Release `645586c` establishes the current journal high-water
mark on first adoption, preserves an existing cursor, and suppresses repeated
identical merchant failures until verified recovery. Historical journals and
pickup receipts were retained.

An independent live audit found both notification queues empty, no delivery
errors, and the farmer's merchant cursor equal to the journal high-water mark.
After the old replay cluster, the only three delivered messages were expected
15-minute status summaries, approximately 900 seconds apart. The deployed
notifier policy matched publication commit `d017384`. No additional queue
suppression was needed. A lost webhook acknowledgement can still cause an
ambiguous retry; no such active failure was observed in this check.

## UI and source deployment

Complete source releases use a hash-checked manifest and an explicit legacy
data root. Credentials, profiles and runtime journals were retained in their
existing locations. Parent and child import checks confirmed the staged source.

UI-only inspection covered Farmer controls, routes, pickup history, monster
groups, both merchant tabs, and normal/maximized host layout. A real Farmer
window overlap covered the shared header; the unified host now constrains its
owned client to the Farmer pane. Standalone height settings remain separate.
The corrected header layout was observed live at a 1416 × 907 client size.
Live monitor/DPI changes and panel movement during transactions remain to be
qualified; they are not implied by these host-layout observations.

## Controlled transfer result

The first lower-value Dutch trial was rejected before any trade input because
admission compared serialized inventory metadata instead of the seven ownership
fields. Neither selected test item moved, no trade window opened, and input
ownership was released. The rejection and original expired visit were retained.

Commit `d017384` compares UID, type, plus, both sockets, quantity and binding
canonically, while retaining rich metadata as transaction evidence. The related
188 tests passed; an independent 50-test review also passed. The complete source
release was safely loaded while all three characters remained in Market.

The expired 60-second visit must not be reset in place. Its actual departure
and reentry are now verified; subsequent controlled transfers remain outstanding.
Review also identified missing per-step layout and overlapping-panel guards in
the existing warehouse deposit drag. Source now checks physical geometry,
topmost hovered grids, manual input and the destination at the actual input
boundaries.

Release `b43adb8` completed a controlled Market warehouse fallback: all eight
carried valuables moved into the warehouse with exact rich inventory and
warehouse conservation. This included the +2 halberd, the mace with two open
sockets, the MeteorScroll, and five other +1 items. Only five potions and the
5,000-arrow reserve remained in the bag; equipped gear and ammunition stayed
with the farmer. Warehouse and Inventory panels closed after verification.
The immutable completion evidence is retained locally with SHA-256
`f7620711edc564650c88426f38d2199df134aff98d3741b4909cda9702083394`.

The saved Phoenix/Market round-trip check initially stopped at its
focus/preflight boundary before input. After the focus fix, the native saved
Market-to-Phoenix and Phoenix-to-Market routes completed. A read-only recovery
reconciled the first arrival after the operator helper queried a Market-only
endpoint in Phoenix. No fare or travel input was replayed. The return used the
verified 100-silver fare and left 200 silver with the farmer. Inventory,
equipment and equipped ammunition were conserved. The old service visit is
recorded as departed; no new service budget was created. This assisted check
does not qualify as an uninterrupted route cycle.

The Show/focus path now uses the existing native activation recovery, verifies
the exact hosted client and final foreground ownership, and checks input
ownership again before keyboard focus. It refreshes the current Farmer pane
geometry after a tab switch. The 39 focused tests and 67 related window,
handoff, and trial-focus tests passed. The subsequent controlled transit used
this focus recovery successfully. A further fix compares actual Farming intent
instead of changing observation telemetry when verifying an idle focus request;
manual control and coordinator checks remain enforced.

A separate controlled retrieval command binds two selected test items to their
immutable, fully verified deposit receipts. It compares complete item ownership,
including sockets and binding, and records possible input before pressing.
An ambiguous result remains locked for read-only reconciliation. It has no
unattended retrieval trigger and cannot renew a merchant-service budget.
Unresolved retrievals block reload, Farming On, route startup and new deliveries;
manual Stop and read-only observations remain available. The associated source
and readiness tests passed independent review before deployment.

The controlled retrieval produced exact, separate outcomes: the selected +1
CloudCap moved from the warehouse into the farmer's inventory, while the +1
LeatherArmor remained in the warehouse with a terminal `no_transfer` receipt
and no attempted input. Neither operation may be replayed. The farmer remains
in Market with the CloudCap; the other seven previously banked valuables remain
stored. A diagnostic fix now preserves the original exception stage, type,
reason and timestamp when reconciliation proves no input occurred or leaves an
ambiguous result blocked. It does not add retry authority. The existing
LeatherArmor failure predates that fix, so its original cause is unavailable.

## Merchant inventory refill restored

Both merchants' refill permissions had been paused for controlled qualification.
Their independent native refill switches are now enabled again; general merchant
operations remain paused. Spiritual posted four items and Dutch posted eleven.
Every new listing receipt matched the current booth's exact item identity and
price, and each merchant listed in descending verified value order.

Both booths reached 32 of 32 slots, with four items remaining in each inventory.
Both refill checks completed with no pending transaction or current error. The
next check is persisted 900 seconds after completion; remaining stock waits for
capacity and qualified prices. These are native held-inventory refills, not proof
of a new farmer delivery or a complete hunting cycle.

A related source fix allows explicitly enabled merchant operations to decline
a qualified unrelated request before an old shop-return incident restricts the
remaining cycle to held-stock refill. Paused operations, refill-only grants and
Global Stop do not gain decline authority. Owned transactions and reservations
are checked again immediately before input. Live decline qualification remains
outstanding: the observed unrelated request disappeared externally, without an
automation decline.

## Unattended qualification remains outstanding

No new two-hour uninterrupted run has started. Farmer intent remains Off during
these controlled checks. General merchant operations remain paused, while both
independent refill schedules are enabled. Their open booths and connected
Market clients are preserved.

Required remaining evidence includes verified lower-value delivery and refill
to both recipients, relevant geometry faults, and a
natural native hunt/town/delivery/refill/return cycle within the two-hour run.
The rate must include all downtime and reach at least 40 verified kills/minute,
with 50 as the stretch target. Assisted checks and reloads cannot count toward
that uninterrupted result.
