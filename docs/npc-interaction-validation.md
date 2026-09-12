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
