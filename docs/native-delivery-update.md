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

The new production orchestration still needs its own successful live receipt.
The authenticated `delivery-test` operation exercises that path with a maximum
of five items while ordinary automatic rollout remains disabled. It still
requires qualified controls, merchant trading permission, the farmer transfer
setting, exact live participants and exclusive input ownership.

Automatic delivery policy and reconnect qualification remain gated. This update
does not enable them or distribute machine-local credentials, control profiles,
transaction journals or live memory evidence. Warehouse fallback is retained.
