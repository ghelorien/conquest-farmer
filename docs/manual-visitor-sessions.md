# Manual visitor/session domain (Wave 1)

`conquest.merchants.manual_sessions` provides durable domain state only. It does
not send gameplay input, observe the process itself, change automated delivery
trust, or add sales receipts. The runtime/UI/sales integration is separate work.
All supplied game observations must come from qualified read-only memory.

## Storage and isolation

`ManualSessionStore(path=None, on_settlement=None)` defaults to
`state_path('reports/merchants/journal.sqlite3')`, the same machine-local database
as `merchants.Journal`. Portable deployments resolve this under their
`machine-state` directory. Legacy deployments use their existing local reports
directory. Do not export/copy this database as portable preferences.

The visitor key is exactly `(target_profile_id, visitor_name, visitor_server,
visitor_uid)`, with case-sensitive equality and a positive observed UID. Names
are not normalized or matched approximately. No permissions are imported from
or written to `profiles.json` or `trusted_sources`. Permission does not authorize
automated accepting, offering, confirming, repricing, travel, or logging in.

Schema version 1 adds these tables alongside the existing journal tables:

| Table | Purpose and identity |
| --- | --- |
| `manual_schema` | Domain schema version |
| `visitor_permissions` | Exact four-column key, allowed flag and update time |
| `manual_sessions` | Session UUID, target, immutable identity context, current phase, current request, stabilization and terminal receipt |
| `manual_requests` | Request UUID, session, fingerprint, immutable approval binding and before image, deadline, mutable request state |
| `manual_declines` | Immutable timeout/rejection intent, unique per request |
| `manual_decline_claims` | Immutable input-boundary claim, unique per decline request |
| `manual_evidence` | Append-only full snapshots, observation/recording times, evidence and ownership digests, reader errors |
| `manual_audit` | Append-only event payloads linked by SHA-256 digest |

A partial unique index permits one nonterminal session per target profile.
`BEGIN IMMEDIATE`, WAL and `synchronous=FULL` serialize decision races. SQL
triggers prohibit changes/deletion of audit, evidence, declines and claims,
changes to request bindings, deletion of sessions/requests, and updates to
terminal sessions. Session identity fields are written once by the API.
The hash chain detects accidental corruption; it is not a signature against a
privileged attacker who can replace the database or remove its triggers.

## Required memory evidence

Use the existing merchant memory snapshot shape. Required fields are:

- `identity`: full game-process identity, including positive `pid`, positive
  `creation_time_100ns`, and exact executable `path`; additional identity fields
  are retained. A PID alone is insufficient.
- `character`, positive `character_uid`, `server`, finite `timestamp`.
- `inventory` and `booth`: complete lists with positive `uid`, `type_id`, and
  `quantity`; integer `plus`, `gem1`, `gem2`; explicit boolean `bound`. Booth
  items also need positive native silver `price`. UIDs must be unique across
  both lists. Item order, names and inventory slots do not affect ownership.
- Nonnegative integer `silver`, bag integer `capacity` (0–40), explicit
  boolean `booth_open`, and nonnegative integer `own_booth_uid`.
- Explicit `request` and `trade`, each either `None` or a nonempty dictionary.
  A request needs exact `participant`, positive `participant_uid`, and the
  matching native `message`. Its complete dictionary is fingerprinted.

Snapshots must be no more than two seconds old and cannot be future-dated.
The caller maps the verified native character to the selected target profile;
this module does not resolve profile names or infer a profile ID.

`canonical_ownership(snapshot)` and `ownership_digest(snapshot)` require both
windows to be explicitly absent. They cover process and character identity,
exact inventory/booth ownership and binding/quantity, booth prices/state,
silver and capacity. Timestamp, HP, position, map and GUI geometry do not affect
equality; the full original snapshot remains immutable evidence.

## Public workflow

1. `begin_request(target_profile_id, snapshot, session_id=None,
   timeout_seconds=30, now=None)` creates `approval_pending`. A still-visible
   repeated request returns its original session/binding/deadline. A second
   identical request requires an intervening observed absence and gets a new
   request UUID. Pass the existing `session_id` to attach that request before
   calling ordinary `observe`; a new binding resets any stabilization window.
2. Display `approval_binding` unchanged. It includes session/request IDs, target
   profile, exact visitor, process and character identity, request fingerprint,
   full displayed evidence digest, and canonical ownership digest.
3. `allow_and_activate(binding, live_snapshot, operator=..., now=None)` checks
   that exact binding against freshly read live evidence, then writes visitor
   permission and `manual_active` atomically. `activate_allowed(...)` instead
   requires a previously saved exact permission. Both are idempotent while the
   same approved request is still live. The returned phase must be checked:
   approval at/after the deadline records a timeout decline rather than an
   activation. Binding errors raise `ManualSessionError`/`BindingMismatch`
   without granting permission or returning input authority.
4. `reject(binding, operator=..., now=None)` or `expire(session_id, now=None)`
   durably records one decline intent. Repeats and concurrent decisions cannot
   create multiple intents or overwrite an already-approved request.
5. Immediately at the independently qualified native input boundary,
   `claim_decline(session_id, live_snapshot, now=None)` revalidates the request
   and returns one durable claim. Only a newly returned claim permits the
   caller's one decline attempt. Later calls return `None`. Do not cache or
   replay returned claims across restart. A crash between claim and click is
   uncertain; SQLite cannot make external game input exactly-once. Reconcile
   through observations and report unresolved claims; never automatically
   retry that input.
6. `observe(session_id, snapshot, now=None)` or `resume(...)` performs read-only
   reconciliation. An approved matching open trade remains `manual_active`.
   First complete absent-window evidence becomes `settlement_observed`. Two
   matching digests with strictly increasing observation times at least five
   seconds apart produce a terminal receipt. Ownership changes restart the
   five-second window. Duplicate/out-of-order observations cannot advance it.

`completed` means an approved manual interval has settled, not a proven trade.
`request_withdrawn` means an unapproved request disappeared with unchanged
ownership. `declined_verified` additionally requires a durable decline input
claim and unchanged ownership. Terminal receipts explicitly set
`sales_receipt=False`. Any process rollover, character mismatch, missing or
stale evidence, unapproved ownership change, or unapproved/mismatched open trade
becomes `needs_attention`; later good observations do not silently release it.
`observe_target(target_profile_id, snapshot)` also quarantines an already-open
unapproved trade when no session exists. If the visitor or ownership is
unreadable but the target/process identity is known, it records `needs_attention`
with `visitor=None`; this record cannot become a permission key. An unreadable
target identity before any session exists raises an error; the integration must
hold input on that error. Booth stock is separate from bag capacity, so combined
bag and booth count may legitimately exceed `capacity`.

`operator_override(session_id, confirmation_reference=..., operator=...,
reason=..., now=None)` records the explicit `operator_overridden` disposition.
It is idempotent for that same confirmation reference and never represents a
successful transfer. It does not synthesize a fresh sales baseline.

Read APIs: `get`, `active`, `permissions`, `allowed`, `audit`, `evidence`,
`verify_audit`, and `overlaps(target_profile_id, start, end)`. `revoke(visitor,
operator=...)` removes future manual permission and records the operator;
it sends no input to an already-active manual session. Returned records include
`holds_automation`, `request_state`, `approval_binding` and `expires_at`.

Profile-management UI must use `ProfileRegistry.list_visitors(profile_id)` and
`ProfileRegistry.revoke_visitors(profile_id, exact_rows, operator=...)`. These
APIs keep manual visitor permission separate from automated `trusted_sources`,
require the affected profile's transaction journals to be idle, and revoke the
selected exact keys atomically. A role change is refused while any allowed
manual visitor key remains attached. Cosmetic labels, templates and ordinary
preference overrides do not consult transaction history.

## Atomic settlement integration

Construct the store with the exact `Journal.path`. Supply
`on_settlement(db, session_view, snapshot, receipt)` to update sales exclusion
events, reset the sales baseline and record newly observed stock using the
provided SQLite connection. The hook executes inside the same transaction as
the final evidence, terminal session transition and audit receipt. Hook failure
rolls all those writes back; later observation can retry. Do not open another
connection, commit/rollback, send input, or perform network I/O from this hook.
The hook runs once for evidence-settled terminals, not for operator overrides.

`SCHEMA` and the store's `db()` context manager are available to integrations
needing schema setup or same-database queries. `overlaps` exposes conservative
manual intervals, including pending approval and unresolved sessions, for sales
exclusion. Session completion itself does not count items or silver as sales.
The runtime must gate all normal merchant work while `holds_automation` is
true, preserve Global Stop/input priority, and route new requests through
`begin_request` before ordinary observation. There are no runtime timers or
gameplay side effects in this module.

## Runtime and UI integration (Wave 2)

`MerchantRuntime` constructs the store on its exact `Journal.path`, loads all
durable holds before workers start, and exposes:

- `manual_status(character=None)`: one active view for a merchant or the
  logical `Farmer` target, or all active views when omitted. Views retain the
  exact `approval_binding`, `visitor`, `phase`, `reason`, `expires_at`, and add
  `deadline` and `fence_scope` (`target` or `global`). All fields are JSON data.
- `approve_manual(binding, operator='local')`: fresh locked memory recheck;
  atomic allow-and-activate. Never enters a gameplay lease or focuses a client.
- `reject_manual(binding, operator='local')`: persist intent only. The observer
  performs any independently qualified decline at its native input boundary.
- `revoke_manual(visitor, operator='local')`: revoke the exact future permission.
  Read saved permissions with `runtime.manual_sessions.permissions(target_id)`.
- `override_manual(session_id, confirmation_reference=..., operator=...,
  reason=...)`: explicit disposition followed by a durable target-only hold
  requiring two fresh equal closed-window ownership observations five seconds
  apart. No immediate baseline or resumed input is inferred from disposition.
- `manual_farmer_status()`: the farmer session, current observation availability,
  qualification/decline blocker and effective input-fence flag.

Pending/unapproved sessions hold only their target. Approved nonterminal sessions
hold every automated input path in `InputCoordinator`, including farmer
`check_input`/`input_scope`, merchant leases, refill, calibration and recovery.
The logical farmer owner resolves to the selected Farmer profile UUID. Manual
permissions never modify automated trusted sources or saved enablement intent.
Stop, later pause, physical mouse ownership and existing recovery holds remain
independent. Memory observers continue while the input fence is held.

Runtime routing gives bot reservations/existing bot-owned trades first priority,
then observes existing manual sessions, then blocks new admission behind any
unfinished bot transaction, then binds new manual requests. An exact permission
activates the bound request; an unknown visitor waits for a persisted 30-second
decision. Timeout/rejection uses the existing native journaled decline path with
the domain claim immediately before the native press. Claimed uncertain input
is never replayed. Approved sessions never enter that decline path.

`configure_manual_farmer(observer_provider, control_provider)` is installed by
the unified app. A background worker observes while Farming On or Off; native
farming and town-call boundaries also invoke the observer before gameplay work.
Modal presence uses the pinned native GUI reader. Complete farmer session
evidence uses `MerchantMemory.read(farmer_preflight=True)`, currently qualified
on maps 1002, 1011 and 1036. A visible request/trade elsewhere, or missing full
evidence, creates a durable target-only `needs_attention` hold and exposes the
reader blocker. The integration does not claim qualified full session reading
on other maps. Native farmer decline additionally requires saved `trade_request`
input qualification; absent qualification is exposed, never bypassed. Manual
approval itself needs only fresh qualified memory, not an input qualification.
Bot farmer deliveries/reservations retain priority and bilateral reconciliation.

If the very first request cannot supply domain identity/ownership evidence,
`manual_reader_hold` retains the failure as `unbound:<target-profile-id>` with
no approval binding or permission key. Explicit override uses the same API.
`manual_rebaseline` retains post-override observations; missing evidence or
identity rollover remains `needs_attention`. An explicit retry uses its
`rebaseline:<source-id>` and a new confirmation reference; a duplicate reference
does not restart stabilization. These holds survive restart and never change
Farming On/Off or merchant/refill preferences.

Evidence settlement atomically appends the domain audit/terminal receipt, a
`sales_observation_gap`, a stable `sales_baseline`, and a `manual_replans` signal.
Only genuinely added/changed merchant inventory sets `new_stock`. No manual
interval writes sale/delivery receipts or attributes stock to a visitor. Normal
sales inference excludes held intervals, reader failures and rebaseline holds.
After settlement, the merchant consumer reads fresh current capacity/ownership
and makes the normal capacity/listing evaluation due, preserving refill pause.
The farmer consumer clears stale transient combat/pathing/loot state and uses
fresh map/inventory/valuable evidence; ordinary urgent banking and supply logic
then decide what follows. Merchant settlements queue only merchant work; farmer
settlements queue only farmer work. Signals confer no gameplay authority and
remain pending while the relevant consumer is stopped.

Validation is mocked/native-boundary regression coverage. No live client was
used to qualify additional memory layouts or native input controls in this wave.
