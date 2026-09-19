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
