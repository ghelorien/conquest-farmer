# 1078 merchant observation foundation

`merchants.runtime.make_observer` selects by executable SHA-256. The existing
1074 observer is unchanged. The exact 1078 build instead receives
`ReadOnlyMerchantObserver`, which opens only a query/read memory session and
checks the selected character and full process identity. It does not construct
Operations, a bridge, or a controller. Unknown builds are rejected.

For Spiritual and Dutch, explicit read-only callers can obtain the existing
versioned `MerchantMemory` closed-modal Market snapshot through `observer.read()`.
It includes carried inventory, owned booth items and prices, character/process
identity, the qualified combined ownership capacity, and remaining owned slots.
It refuses an open request/trade, a foreign displayed booth, overlapping item
UIDs, stock exceeding capacity, or stock changes during the read. Item attributes
and booth prices are re-read, not just deque headers. Capacity here is the
combined inventory/booth ownership limit; it is not evidence of a newly qualified
booth input control or a listing slot limit.

The 1078 runtime attachment fence remains. Binding an observation-only merchant
to automation is explicitly denied, and `MerchantDriver` independently rejects
1078 before loading any old qualification. Market safety may report a read-only
merchant's memory location, but this observer cannot initiate recovery or change
saved controls. No refill timer, trading, listing, login, travel, focus or other
input is enabled by this foundation.

## Authenticated observation command

The existing app-owned `/merchants` bridge accepts
`{"action":"merchant-observe-1078","character":"Spiritual"}` (or Dutch).
Its existing profile-ID normalization also accepts `profile_id` instead of
`character`. The existing bridge token authentication remains mandatory.

This command independently opens short-lived read-only process sessions inside
the elevated app. It resolves exact-build actor names, pinned character UIDs and
server memory without window/title selection, rejects ambiguous matches or
unreadable candidates, and rechecks the selected process/profile and process
inventory before returning. A configured UID must match; an unbound profile
reports `profile_uid_verified: false` without binding or writing that profile.
Non-1078 executables are skipped rather than interpreted using 1078 offsets.

The compact result includes identity, map/position/HP, inventory, booth items and
prices, combined ownership capacity, booth owner/open state and closed-modal
flags. It deliberately reports `input_qualified: false` and
`refill_input_ready: false`, even when every observation succeeds. Open requests
or trades are observation evidence only. No coordinates, raw memory addresses,
controls, credentials or input surface are returned. It neither attaches an
automation observer nor changes saved permissions, journals or manual handoffs.
Read gaps and a four-second overall deadline require a fresh read-only retry.

## Qualification still required

Before enabling any listing/refill input, obtain fresh exact-1078 evidence for
both configured merchants: exclusive safe farmer handoff and manual Stop;
native foreground ownership and embedded geometry; owned-booth owner/control
identity; inventory selection and drag/drop targeting; editable price entry and
confirmation; exact item UID/attributes leaving inventory and entering the owned
booth at the submitted price; combined capacity preservation; and uncertain
submission reconciliation without replay. Qualify any removal/repricing path
separately. Old 1074 input evidence must never satisfy these requirements.

This change adds source foundations only. No tests or live qualification/input
were run, and it does not claim live validation of the new observer integration.
