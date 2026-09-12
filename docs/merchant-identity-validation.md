# Merchant identity validation, September 12

The pinned c2b53437 client uses self actor +0x68 for character identity.
Runtime code at RVA 0x8dc8 calls the self getter 0x181b30 and compares that
field with an incoming identity. RVA 0x97bc independently compares it with
another actor's identity. The reader checks both instruction sequences live.

The previous merchant reader incorrectly reused actor +0x3258, which belongs
to the owned booth. It is zero without a booth and must remain separate from
the character UID. Booth model +0x4c is still compared with that owned-booth
field, and both are rechecked after observation. No ownership guard was removed.

Live self snapshots identify Spiritual as 1173711 and Dutch as 1173856.
Both clients independently observed the other at Market (211,196), matching
its self UID, exact name and position. The remote entity layout observed in
both directions is vtable RVA 0x5c5e20, UID +0x78, inline name +0xa4,
tile position +0xe8 and signed integer draw position +0xf8. The last field
was incorrectly parsed as floats in the delivery adapter; it now requires
an explicitly qualified i32 format. Cross-client evidence does not authorize
trade controls or establish the farmer's live recipient qualification.

Local evidence files (not client-memory dumps):

- reports/merchants/character-identity-validation.json
- reports/merchants/dutch-peer-identity.json
- reports/merchants/spiritual-peer-identity.json

Both saved merchant logins succeeded and their expected names were verified
through memory. The user manually took both to Market. Automatic Conductress
travel and booth restoration remain unqualified; do not credit manual arrival
as an automatic recovery test. Merchants are held paused in Market.

Travel-only snapshots now avoid inventory and booth scans, use the named
living actor, recheck silver, map, position, server and active trade/dialog
flags, and cannot be used as delivery/capacity snapshots. Route waypoints use
the live camera anchor and viewport instead of an assumed centered view.

Validation: 1,939 full-suite tests passed after the self-identity correction;
33 targeted tests passed after cross-client diagnostics and integer-coordinate
targeting changes. Live booth setup, lower-value deliveries to both merchants,
interrupted trade qualification, automatic return routes and the enabled-refill
performance comparison remain outstanding. Merchant delivery stays disabled.

## Market stall qualification in progress

Dutch completed memory-verified movement from (223,185) to (228,181), and
from (225,206) to (228,205), with stock and silver unchanged. This qualifies
movement for the tested 1888 x 665 embedded viewport, not a complete reconnect
route. Transient movement observations are retried without repeating input.

One left-click probe at flag 101509 produced no booth or dialog. Read-only
reconciliation verified unchanged full item attributes and silver, and no
remaining interaction. This is not evidence that the flag is vacant or that
the click hit its control. The user confirmed that only unattended flags can
be claimed, using a left click.

Diagnostic flag clicks require a separate `stall_occupancy` qualification,
with the same live vacancy reader as automatic booth setup. Availability and
the qualification are checked again before pressing. Unknown, occupied and
newly occupied stalls fail without a click.

The user opened Dutch's flag 101485 at (262,206) and positioned Spiritual
beside the empty flag 101486. Dutch's self-owned booth UID 103060 matched a
separate scene entity named Dutch, model 406, at (265,206), in both clients.
All 29 Dutch-scene booth records and all 27 Spiritual-scene records matched
flags three tiles west of them. Neither client had a booth at the adjacent
empty flag's corresponding tile. This qualifies nearby scene occupancy for
the pinned build; it does not yet qualify the claim click. The reader limits
vacancy decisions to flags within eight tiles and requires two unchanged
scene observations and a stationary living merchant. A ShopFlag name alone
remains insufficient. Scene reads are batched and retain membership, identity
and expiry checks.

Spiritual also completed checked Market movement at the current embedded
viewport. A later click at memory-verified vacant flag 101465 produced no
booth response, so claim controls remain unqualified. A passive, client-only
manual-click comparison is in progress to establish the correct target.

The existing third client was subsequently attached to the Farmer tab, without
launching a fourth client or changing Farming Off. Its saved encrypted login
succeeded in one submission. Fresh memory verified Parasite UID 1173490, alive
on map 1011 at (193,266). This proves account login, not town/Conductress travel
or merchant delivery. The eight carried inventory items currently include no
eligible delivery sample. Local evidence is recorded in
`reports/merchants/farmer-reconnect-validation.json`.

Login now retains exclusive input ownership across focus, dialog dismissal,
both credential fields and submission. The attachment bridge accepts an exact
PID plus creation time, rejects assigned merchant clients, verifies a connected
client's character, and preserves manual control. Fifty targeted reconnect and
attachment tests passed; the preceding full suite passed 1,964 tests before
these final attachment/input-ownership changes.
