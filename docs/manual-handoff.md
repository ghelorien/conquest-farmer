# Operator manual handoff

Use **Manual handoff** in the Farmer pane or Merchant Overview before doing a multi-character game action. After Start, wait for **Ready** before touching any client. It fences attached Farmer and merchant automation but never sends game input or changes saved Farming, trading, refill, banking, or delivery controls.

The session waits for active automation input and fresh closed-window native-memory baselines. After a request/trade is observed, or after **End manual handoff**, it completes only after each participant has five seconds of matching closed-window ownership and the mouse is idle. A process-identity change remains an attention hold; do not treat End as a resolution.

Authenticated localhost bridge commands are `manual-handoff-start`, `manual-status`, and `manual-handoff-end` with the exact session id returned by status. Baselines and observation gaps are durable in the merchant journal; handoff changes never create sales receipts.
