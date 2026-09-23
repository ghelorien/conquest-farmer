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

The 1078 runtime now retains one exact-process read-only observer for each
memory-identified Spiritual/Dutch client and polls the manual ownership reader
even outside a global Manual handoff. The attached observer provides a verified
native HWND to the guarded Manual handoff Client view, while its current inventory
and booth snapshot feeds the merchant display. Background host restoration skips
read-only clients; it cannot change a manually operated window. Open
trade/request modals remain observation evidence. A read gap clears the displayed stock until a fresh read succeeds;
process replacement requires a new memory-identified attachment. Manual handoff
still uses its independent frozen reader registry and baseline.
The exact-build fence refreshes while the app runs and stays active once 1078
is observed, including across process identity changes, until the app restarts.

The 1078 runtime attachment fence remains. Binding an observation-only merchant
to automation is explicitly denied, and `MerchantDriver` independently rejects
1078 before loading any old qualification. Market safety may report a read-only
merchant's memory location, but this observer cannot initiate recovery or change
saved controls. Hosting leaves the merchant input surface blocked. No refill
timer, trading, listing, login, travel, focus or other input is enabled by this
foundation.

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

`merchant-listing-preflight-1078` accepts the same character/profile selection
and authentication. It brackets passive live GUI reads with identical merchant
ownership snapshots. A missing/closed owned booth, open trade/request or merchant
outside Market produces an explicit blocker instead of guessed controls. When
available, it reports the owned booth panel and live window-owned inventory and
booth tables (column/row/clip geometry), stripped of memory addresses. Relevant
windows, table geometry and the typed owned-booth model are rechecked.

A currently rendered price dialog contributes its observed bounds and any
passive amount/OK/Cancel label-hash matches under the existing pointer. It never
moves the pointer. These matches do not qualify handlers or click targets. The
1078 selected-item/amount-buffer semantics and input behavior remain explicit
blockers; no 1074 modal button offsets are reused. All input/readiness flags stay
false, and the command writes no qualifications or saved state.

`merchant-refill-preview-1078` uses the same authenticated read-only 1078
observation for one configured merchant. It reads the saved comparable price
catalog in SQLite query-only mode and brackets planning with unchanged merchant
ownership, journal and quote reads. Every carried item appears in a queue:
verified prior booth listings retain their exact saved total price, other items
use the last comparable historical quote (including the specified +1-to-+2
equipment fallback), and items without reliable prices remain deferred. Known
totals are sorted highest first, with only the first free booth slots shown as
potential next listings. The preview reports blockers and never submits a
listing, grants input, changes controls or writes runtime state. A missing or
unreadable price catalog defers non-restoration inventory.

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
were run, and it does not claim live validation of the persistent observer or
host integration.
