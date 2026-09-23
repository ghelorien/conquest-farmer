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
