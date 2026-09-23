# 1078 owned-booth target qualification

`merchant-booth-target-preflight-1078` is an authenticated, read-only merchant
observation. It requires the exact 1078 executable fingerprint, configured
merchant character/UID and unchanged process identity. It reuses the native
ownership reader, then finds exactly one nearby Market scene actor whose UID,
name, model, position and scene membership identify the merchant's own booth.
It brackets the scene read with fresh ownership observations.

The endpoint deliberately returns `input_qualified: false` and no authorized
target point.
For read-only qualification it examines at most 17 aligned pointer fields of
the verified booth actor (`+0x2d0..+0x350`), and only three fixed vtable slots
(`0x20`, `0x28`, `0x40`) when those pointers resolve inside the exact client
module. These method observations use 24-byte prefixes; the projection also
checks fixed 1078 instruction sequences, native camera constants and fields,
the booth footprint, and a screen-to-tile round trip. Actor pointer fields,
scene membership and ownership are reread before returning. The native
hit-test compares the kind on a secondary actor subobject at `actor + 0x10`,
so its `+0x2b0` field is read at `actor + 0x2c0`. This was verified read-only
on both Spiritual and Dutch. The result is only a projection candidate:
neither a live booth-open click outcome nor production input has been
qualified. Merchant refill remains disabled for this 1078 build.

Before any supervised booth click, a separate reviewed implementation must
prove the live click opens only this booth's owned native model and still pass
the existing Stop, mouse, modal, process, scene and input-lease guards. Listing
items from an already-open booth is a separate input qualification. This
preflight neither issues nor authorizes a click.

## Price dialog, read-only qualification

Both exact-build merchant processes produced byte-identical loaded booth-render
code while their owned booths and inventories stayed stable. The render function
at RVA `0x75b90` binds selected item UID at booth-model `+0x50` and a 12-byte
`##Amount` buffer at `+0x54`. Its **OK** handler parses a price in
`1..999,999,999`, calls the native listing callback with that UID and price,
then clears and closes the dialog. **Cancel** only clears and closes it. The
read-only listing preflight now pins the complete loaded function hash and
vtable slot before describing those field/button semantics.

This is not listing-input authorization. Button hit geometry, a submitted
callback's transport outcome, and server acceptance remain unverified. A
closed popup alone is not a receipt. The next supervised proof must compare
the exact UID in inventory and the booth, with verified price and durable
before/after observations; no automatic Confirm is enabled yet. The bounded
loaded-code diagnostic is `scripts/probe_booth_modal_code_1078.py`.
