# 1078 owned-booth target qualification

`merchant-booth-target-preflight-1078` is an authenticated, read-only merchant
observation. It requires the exact 1078 executable fingerprint, configured
merchant character/UID and unchanged process identity. It reuses the native
ownership reader, then finds exactly one nearby Market scene actor whose UID,
name, model, position and scene membership identify the merchant's own booth.
It brackets the scene read with fresh ownership observations.

The endpoint deliberately returns `input_qualified: false` and no target point.
The 1074 booth graphics vtable, tile footprint, camera and hit-test instructions
are not carried over. Its bounded graphics-method evidence is for identifying
the 1078 native hit-test path in a supervised read-only observation. Until its
exact method, footprint, projection and screen-to-tile inverse have been
proved on both Spiritual and Dutch, booth opening and merchant refill input
remain disabled.

Before any supervised booth click, a separate reviewed implementation must
pin those exact 1078 instructions and camera fields, reject a changed scene or
process, prove that the computed client point resolves to this booth's native
footprint, and still pass the existing Stop, mouse, modal and input-lease guards.
This preflight neither issues nor authorizes that click.
