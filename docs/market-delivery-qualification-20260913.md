# Market delivery qualification — September 13, 2026

The Market approach previously tried to reach within two tiles of a merchant's
occupied booth. A live Dutch request succeeded from four tiles away while the
approach continued attempting unnecessary movement. Delivery now skips movement
within the existing twelve-tile request bound, otherwise follows checked terrain
to the first tile within that bound. Native recipient identity, projection,
request and exact inventory checks still run before trade input.

Parasite's +1 DarkHammer was transferred to Dutch. The saved receipt verified its
exact UID leaving the farmer and arriving in the merchant inventory, with zero
currency change on both sides. Dutch's independent refill then listed that same
item at the saved comparable price of 100,000 silver. The first acceptance attempt
sent no input because focus failed; both unchanged inventories and the original
incoming request were reconciled before re-embedding and retrying acceptance.

The periodic merchant UI poll now uses existing dead-client resize handling so a
closed HWND does not overwrite reconnect intent with Pause. Spiritual's saved
login and Market recovery were exercised; initial recovery required this fix and
bounded memory-only scouting toward its saved stall area. Vacancy and unchanged
stock are checked before a stall claim. All 32 saved listings were restored.

The local Parasite policy is enabled for required town deliveries after recorded
farming parity and live deliveries to both receivers. Both merchants have saved
credentials and qualified controls. Warehouse fallback remains active. Hunting
handoffs remain disabled pending live qualification. A complete natural cycle
with the newly enabled merchant policy still needs observation; this report does
not claim that cycle has passed. Machine-specific policy, credentials, journals
and qualification files are not published as portable defaults.

Validation: 113 focused tests passed in the publication checkout, covering
Market approaches, recovery, focus, delivery transactions and the return journey.
The fixes were loaded by safe reload while Parasite was idle in Market; Farming
Off was preserved.
