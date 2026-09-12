# Dialogue dimensions audit

The active viewport is 1420x1009. GUI positions and sizes come from read-only
client memory, and normal input rechecks the physical viewport and focus.
No screenshots, OCR, memory writes or injected client calls were used.

All NPC choice windows share dialog_geometry: Phoenix and Twin City
Conductresses, every saved MillionaireLee exchange page, Mark.Controller,
and service dismissal. Width comes from the completed renderer table. The
renderer still defines two columns and 22-pixel rows; resolution changes
do not scale those row strides. Exact dialogue text, option IDs, NPC identity,
prices, item batches and post-transaction receipts remain required.

The selected row, rather than the complete option table, must be visible.
Scrolling is chosen from its live row rectangle, window bounds and current
viewport, with support for either direction. A visible early choice does not
trigger scrolling merely because later choices are below the window. Hidden
choices are not clicked, and horizontal clipping cannot authorize vertical
scrolling or input. Twin City travel now prepares its selected destination
through this same path before its existing guarded fare submission.

The recognized interrupted-server error popup now derives its full-width OK
button from memory without requiring the old 409x84 outer window. Its exact
message and stable DC/button geometry remain mandatory; wrapped text can
move the button vertically. Unknown errors are not dismissed automatically.

Other audited surfaces already obtain live origins and current viewport:
shops, equipment/inventory slots, warehouse deposit/withdrawal and amount
controls, login fields, revival and Fly/Scatter popups. Their fixed widget
strides and content-shape checks are renderer qualifications, not old screen
coordinates, and remain intact. Shops, warehouse transfers, exchange and
return transport completed at the new viewport in the preceding live banking
validation. The nonresizable, auto-sized Login form is code-qualified (Begin
flags 0x16f in the pinned renderer), and reconnect at the new viewport was
verified previously. No unnecessary disconnect or purchases were introduced
to exercise these unaffected controls.

Focused tests cover all saved NPC dialogue records at 1020x754, 1420x1009
and 1920x1080 viewports with moved/resized windows, selected-row clipping,
both scroll directions, manual Stop, changed records, malformed geometry,
resized/wrapped error popups and no repeat fare after an uncertain outcome.
87 focused tests passed before the complete regression run.

Complete regression: 1252 tests passed in 110.75 seconds. Live loading and
resumed combat are recorded in reports/performance/all-dialogue-dimensions-loaded.json.
