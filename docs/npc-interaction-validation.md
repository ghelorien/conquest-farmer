# NPC input timing validation

Town vendor, warehouse and Conductress opening clicks now recheck their exact
NPC observation after moving the cursor and immediately before mouse-down.
The check also waits, for at most 350 ms, for the native actor hit-test pointer
to match the intended click within one pixel. A timeout sends no mouse-down;
an expired/torn NPC read can be observed again without repeating a click.
Existing inventory/shop receipts still decide whether an opening succeeded.

`scene_pointer.py` verifies the pinned actor hit-test instructions before reading
the X/Y input fields at RVA `0x6985a8`. On September 12 the live farmer's native
coordinates matched six stable Windows client-coordinate samples at 1416×907.
No image observations or input were used for that comparison. The change has
regression coverage for renderer lag, changed NPC projection, torn observations
and cancellation. A live Blacksmith reopening with this change remains required.

The missed Blacksmith clicks are not yet attributed conclusively to this timing
gap. Separate read-only research located the model's bounds/projection chain for
Market flags and reproduced a flag's memory-reported screen origin. Model-based
click centers remain diagnostic candidates; the saved interaction points have
not been replaced or qualified by that research.

## Owned Market booth hit test

Further read-only inspection found that an owned booth (model 406) does **not**
use the flag's model ray test. Its graphics vtable dispatches to RVA `0x262160`,
which accepts five tile offsets selected by the booth orientation. Actor hit
kind 14 converts the cursor to a ground tile before this call.

For Spiritual's current booth at `(272,174)`, orientation 6 accepts the center
and the tiles `(272,173)` and `(272,175)`. The previous draw-minus-32 point
`(976,316)` resolves to `(271,173)`, outside that footprint. The new memory-derived
point `(976,348)` resolves to the booth's center. The accessor instructions,
camera, booth identity, orientation and footprint were checked in the live
client without input. Model bounding-box intersection alone would have missed
this distinction and is not used to target an owned booth.

`merchants/booth_target.py` now reads and rechecks the native tile footprint and
camera transform. Both the owned-panel qualification probe and recovery use
this target, revalidate before mouse-down, and wait for the native pointer.
Recovery requires the new `native_booth_tiles` qualification; a legacy fixed
offset receipt cannot authorize it. A successful live panel-open receipt with
matching owned/displayed booth IDs is still required. Empty-flag claim input
and merchant disconnect recovery remain unverified.

On September 12, Dutch completed the live owned-panel test through the normal
authenticated bridge and exclusive input coordinator. The custom native
`#CLOSE` control closed only the view: owned booth UID `103060`, exact stock and
silver stayed unchanged. The native tile point then reopened that same booth,
and its displayed UID matched its owned UID. Both transitions have durable
receipts. The test used no visual observations, confirmed no listings or trades,
and resumed the farmer after releasing input. Dutch's `booth_panel` capability
is now qualified; Spiritual's is still pending.

`booth_panel_probe.py` pins the custom header's close-button call and cleanup
path, requires its native hover ID before mouse-down, and journals before
submission. The probe rejects pending closes, foreign booths, changed stock,
manual Stop and missing hover. Its entry through `inspect-market-stall` has a
15-second input deadline. Closing a panel does not establish disconnect recovery
or vacant-stall claim qualification.

An older unresolved flag probe may be superseded by a later durable
`manual_stall_setup_adopted` event only when its exact owned booth still exists
at that flag, in the same client, with no pending transaction or interaction.
Reconciliation preserves the old observations and explicitly leaves original
inventory results and automatic input qualification unverified. It never
retries a claim or grants a recovery capability. Current stock is checked for
stability before recording the manual resolution.
