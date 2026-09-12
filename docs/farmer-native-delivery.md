# Native farmer delivery qualification and rollout

The native adapter is implemented in `merchants/farmer_trade.py`. It has not
been qualified against live merchant clients and is not enabled in the farming
route. Market storage calls are wired in source but remain gated. Keep `profiles/merchant-deliveries.json` disabled until the live checks
in `farmer-merchant-rollout.md` are complete.

The adapter uses normal foreground clicks and drags, coordinated with merchant
input. It resolves the recipient's character UID in the farmer's scene, checks
the name and tile against the recipient's own memory, verifies trade targeting
mode, and checks the two participants before placing any item. Each drag checks
the exact inventory UID, slot, item attributes, current table geometry and the
hovered inventory window. Full offers and zero currency are checked again before
confirmation. Manual control, F11, Global Stop, merchant pause and the handoff
deadline block further input; read-only reconciliation remains available.

Live qualification must create the local file
`.runtime/merchants/farmer-delivery-qualified.json`. Do not fill it with guessed
values or promote unit-test fixtures. It uses the existing MerchantDriver
qualification fields (pinned client hash, Parasite/America, physical and GUI
sizes, capability `farmer_delivery`, and evidence), plus:

- `recipient`: qualified vtable RVA, UID/name/world-position/draw-position
  offsets, bounded name capacity, and the proven `inline_utf8` name format.
- `target_mode`: the qualified module RVA and u32 value proving that a click
  will submit a trade request.
- `controls`: live GUI/table definitions for `start_trade`, `open_inventory`,
  `inventory_item`, `trade_drop`, and `confirm_trade`. Buttons require their
  verified ImGui hover label. Item geometry follows the existing qualified
  inventory table format.

If the pinned client uses a different representation, extend and qualify the
reader before enabling it. The file is deliberately absent until evidence exists.

The native Trade HUD and targeting field now have a read-only implementation in
`merchants/trade_controls.py`. The pinned Trade button handler writes mode 19 at
RVA `0x699290`; the reader verifies the handler and controller-getter instructions
before interpreting this field. These instructions and idle mode 16 were checked
in all three live clients on September 12. This is code/observation evidence,
not a verified click-to-trade transition.

For `start_trade` and `open_inventory`, a future live-qualified control may use
`mode: native_items_trade`, `window: ##Control`, its verified window `size`, and
`label: Trade` or `label: Items`. Position follows the current six-column HUD
table rather than a saved window offset. Trade is the lower button in Items'
verified two-row, 40-pixel column. Existing viewport, window bounds and hover-ID
checks remain mandatory. The observed Trade positions were derived successfully
for Spiritual, Dutch and Parasite without clicking or changing focus. No input
capability or rollout flag was enabled by this observation.

The authenticated bridge accepts `delivery-start` with character, a stable
request ID and 1–20 carried UIDs. It returns immediately; `delivery-status`
reports the native worker and durable source receipt. Source transactions live
in `reports/banking/merchant-deliveries.sqlite3`, separately from the merchant
journal. A restart or repeated request can only reconcile an existing operation;
it cannot re-enter native input. Source submission is journaled before the
receiver is told the complete batch is ready. Both inventories must reconcile
before the stock hold is released. A lost release acknowledgement preserves the
verified receipt and retries only that acknowledgement. Pending deliveries block
safe reload. Non-manual failures use the merchant attention/notification path.

Next qualification requires both merchant clients and encrypted local logins,
a low-value transfer first, then transfers to both characters, interrupted
offers, stopped/expired input, changed geometry and reconnect reconciliation.
`merchants/delivery_route.py` now calls this protocol before warehouse storage
on existing Meteor consolidation and overflow visits to Market. It compares
fresh capacity and terrain-checked travel distances, rechecks after movement,
splits batches and persists its request before submission. Uncertain submissions
block further storage input; restart recovery only reconciles existing source
operations. A verified delivered MeteorScroll satisfies the consolidation route's
storage receipt, while all remaining valuables follow the existing warehouse
fallback and checked return. Star Dragonballs cannot enter delivery batches.

The dedicated delivery window holds ordinary merchant listing/refill input until
the receiver's exact reservation is established and reconciled. Grants remain
limited to fifteen seconds; input is revoked on release and manual changes
prevent automatic farmer refocusing. Capacity checks update the persistent
fifteen-minute schedule.

`merchants/delivery_journey.py` now starts an optional Market delivery trip
after required shopping and Meteor consolidation, while the origin warehouse
is open. A detailed memory preflight requires eligible unbound carried items,
a ready merchant and a saved verified round trip. The trip withdraws only the
fare shortfall while preserving the transport reserve. Fares are journaled
before submission and checked against inventory and silver on arrival. Pending
journeys resume before hunting after a restart; uncertain payments never repeat.
Remaining items receive exact warehouse receipts before the checked return,
and the origin balance is reread for normal silver banking. Uncertain trades
also prevent automatic panel cleanup and safe reload.

After both trade receipts reconcile, the original grant can enter a refill-only
phase for its remaining time. This does not extend the fifteen-second deadline,
consume pending repricing requests, authorize trade acceptance, or authorize
merchant login/travel. The separate refill permission still applies, and F11,
manual control changes, expiry and Global Stop prevent further input.

Input leases carry the operation purpose and check permissions before any
focus or surface-preparation callback. A delivery window admits only its
reserved trade; its refill phase admits only the thread executing authorized
inventory refill. Previously planned listings, repricing, calibration and
unrelated trades cannot borrow that window. Duplicate window requests do not
restart timers or revert refill to trading. A failed scheduler write retains
the release key so the caller can revoke the window without granting input.
These safeguards have automated regression coverage but remain source-only
until the next safe app replacement.

When a fallback deposit fills the Market warehouse's last slot, the enabled
delivery flow can continue only after all carried valuables are stored and a
fresh, identified, qualified merchant still has reachable inventory space.
Stale capacity, an occupied trade, an unreachable merchant, disabled rollout or
any unstored valuable preserves the stop. Generic merchant space can never
excuse an unstored star Dragonball. This capacity check runs only at a full
warehouse; normal hunting and ordinary deposits do not trigger it.

All input, both merchant transfers and reconnect/booth return still need live
qualification. Keep rollout and hunting-handoff gates disabled until that work
passes; unit tests do not qualify a live transaction.
