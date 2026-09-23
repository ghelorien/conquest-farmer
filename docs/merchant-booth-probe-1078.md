# One-item 1078 booth probe (source only; not live qualified)

The authenticated merchant bridge exposes `merchant-booth-probe-1078` and
`merchant-booth-probe-status-1078`. This is an explicit diagnostic, never a
scheduler action. It does not qualify `MerchantDriver`, alter the 1078
observation-only runtime fence, or enable refill, trading or recovery.

The first fixture is Dutch's manually observed BreastPlate UID 295498033,
type 130624, +1, unsocketed, unbound, quantity one. The exact fingerprint is
checked every time. This is not a general low-price classifier or a price quote.
The booth must be owned, open, empty and in Market. The selected item must be
visible in its native inventory grid. The operation never scrolls, opens a
booth, removes a listing or chooses an alternative item.

Required start body fields are `action`, `character` (or the bridge's existing
`profile_id` alias), `request_id`, `item_uid`, `expected_identity`,
`expected_character_uid` and `expected_own_booth_uid`. Obtain process and
ownership fields from a fresh `merchant-listing-preflight-1078` response.
`request_id` must match `booth1078-[A-Za-z0-9_-]{8,80}`. The profile must have
its character UID configured. A start returns immediately after durable intent
and worker creation; it does not imply input or cancellation succeeded.

The status body contains only `action`, `character` and `request_id`.
Poll status after a lost HTTP response. Repeating the identical start returns
its existing receipt; a changed request with the same ID is rejected. No
request resumes or replays any input. Prepared receipts after a crash and
uncertain receipts require operator reconciliation before any new probe.

The farmer must already be safely yielded under the normal InputCoordinator
policy, and Dutch must already be foreground in its standalone native window.
This deliberately limited first probe performs no embed, focus or restore
operation. Global Stop, manual handoff, visitor sessions, mouse priority,
grant revision/expiry, process identity and native layout remain authoritative.
The probe does not change saved farmer or merchant controls. A private,
thread-bound capability permits only this exact journaled probe through the
1078 surface block; the block itself is never cleared.

The worker journals before the drag gesture and its press/release boundaries,
before Amount/Cancel pointer and press boundaries, and before each of the six
sentinel digit pairs. It verifies the exact selected UID after the drag. Amount
and Cancel coordinates are only candidates derived from live modal layout;
fresh native window/label hover hashes must match before either press. A
freshly empty amount field receives only the Unicode digits `123456`; no
Enter, paste or shortcut is sent. The verified model buffer must be `123,456`
before Cancel. No Confirm coordinate or submission operation exists here.

Success requires the modal to disappear, its selected UID to clear, and two
fresh exact inventory/booth/silver/ownership observations to equal the original
baseline. The receipt records diagnostic success with `listing_submitted`,
`input_qualified` and `refill_input_ready` all false. Any uncertainty after the
first possible input leaves a durable pending transaction and attention state;
no automatic cleanup or retry gesture is sent. Existing shop alerts observe
the uncertain pending transaction. A successful probe is not an item sale.

Validation for this source change is syntax and diff review only, by request.
There has been no live run, deployment, or unit/regression test execution.
