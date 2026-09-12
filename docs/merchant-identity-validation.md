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
