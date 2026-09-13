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

The expired 60-second visit must not be reset in place. Warehouse fallback,
verified departure/reentry, and subsequent controlled transfers are outstanding.
Review also identified missing per-step layout and overlapping-panel guards in
the existing warehouse deposit drag. Source now checks physical geometry,
topmost hovered grids, manual input and the destination at the actual input
boundaries. Live deposit qualification remains outstanding.

A separate controlled retrieval command binds two selected test items to their
immutable, fully verified deposit receipts. It compares complete item ownership,
including sockets and binding, and records possible input before pressing.
An ambiguous result remains locked for read-only reconciliation. It has no
unattended retrieval trigger and cannot renew a merchant-service budget.
Unresolved retrievals block reload, Farming On, route startup and new deliveries;
manual Stop and read-only observations remain available. The associated source
and readiness tests passed independent review before deployment.

## Unattended qualification remains outstanding

No new two-hour uninterrupted run has started. Farmer intent remains Off during
these controlled checks. Merchant operation/refill permissions are temporarily
paused; their open booths and connected Market clients are preserved.

Required remaining evidence includes verified lower-value delivery and refill
to both recipients, exact storage fallback, relevant geometry faults, and a
natural native hunt/town/delivery/refill/return cycle within the two-hour run.
The rate must include all downtime and reach at least 40 verified kills/minute,
with 50 as the stretch target. Assisted checks and reloads cannot count toward
that uninterrupted result.
