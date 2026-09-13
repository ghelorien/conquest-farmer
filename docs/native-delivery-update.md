# Native delivery integration update

Booth listing now checks panel dimensions before journaling a new listing.
A resize uses the existing guarded control-verification flow before proceeding;
an open price dialog still requires reconciliation. Native trade buttons and
drop cells use current memory-derived geometry and hover identities.

The delivery planner splits loot into batches of at most five items for the
15-second input windows. The farmer confirms its exact offer before requesting
receiver confirmation, releasing its input lease between participants. This
avoids a receiver holding input while waiting for the farmer. Journals and
two-account inventory reconciliation remain authoritative after interruption.

The earlier supervised 12-item transfer to Spiritual verified the underlying
controls and both final inventories. Promoting those controls now also requires
both client processes to remain identical throughout that exchange. Read-only
transaction recovery may still reconcile after a client restart; that alone
does not qualify input controls.

The production orchestration initially remained pending its own live receipt;
the Spiritual-only result below supersedes that initial qualification status.
The authenticated `delivery-test` operation exercises that path with a maximum
of five items while ordinary automatic rollout remains disabled. It still
requires qualified controls, merchant trading permission, the farmer transfer
setting, exact live participants and exclusive input ownership.

Automatic delivery policy and reconnect qualification remain gated. This update
does not enable them or distribute machine-local credentials, control profiles,
transaction journals or live memory evidence. Warehouse fallback is retained.


## Activity and Market return follow-up

The Farmer and Overview tabs now show execution state and the current activity
separately from the combat switch. Restocking, travel and transfer steps remain
visible while combat is off. Explicit Stop/Off takes priority over older route
and transfer messages; stale travel reports no longer claim confirmed movement.

Delivery input acquisition waits up to three seconds for the receiver to release
ownership, while continuing to check permission and handoff expiry. Only lease
acquisition is retried: a submitted click or transaction body is never replayed.
Trade completion and reconciliation failures report their specific activity.

The Market return route leaves northeast merchant booths through the central
aisle before approaching the exit controller. The reverse aisle and subsequent
Phoenix arrival were verified live. A Meteor transfer was reconciled in both
inventories after supervised recovery; this is not a completed unattended
production-path qualification. Automatic delivery policy remains gated.


## Clear panels before farming and travel

The farmer checks for blocking display panels at most once per second during
combat and travel, including another seller's open Booth view. It closes one
identified panel and reobserves before continuing. The native Booth close uses
pinned code, current geometry and the exact #CLOSE hover identity; it does not
shut down a merchant's shop. Merchant clients are excluded. Trade, listing and
confirmation dialogs stop cleanup for reconciliation instead. Ordinary shopping
actions do not run this periodic cleanup.


## Spiritual production transfer retest

A live explicit retest completed the full native automatic transfer path from
Parasite to Spiritual: one Meteor transferred in 5.58 seconds without manual
recovery, with both inventories and currency reconciled. This qualifies that
connected Spiritual transfer path, not Dutch transfers, reconnect or booth setup.
Automatic rollout policy is unchanged.

A mismatched request ID and reserved work window now fails before creating a
delivery intent or input driver. The explicit cancellation probe closes only the
unchanged incoming request through the native Cancel hover control. It requires
paused merchant operations, input ownership, an active deadline and unchanged
participants and stock, and verifies the result before reporting cancellation.
Private receipts, journals, credentials and promoted profiles remain local.
