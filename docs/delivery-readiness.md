# Merchant delivery readiness

The authenticated `delivery-readiness` operation is read-only. Its legacy
`qualified` field covers farmer trade controls only; `qualification_scope`
states that limit explicitly. `configured_prerequisites_met` and `blockers`
also describe rollout flags, each merchant's saved credential-file presence,
trading permission and qualified controls. No credential values are read or
returned. File presence alone does not verify a successful login.

The response does not grant permission or replace live checks. Submission must
still verify both participants, exact inventories, available capacity and input
ownership. Merchant deliveries remain disabled until live qualification and
rollout checks are complete; verified warehouse storage remains the fallback.

Native refill checks retain the 900-second interval. A successful timer tick
with no eligible stock proves scheduling only, not a safe input handoff or an
end-to-end merchant delivery. Automatic booth reopening and vacant-stall claim
must be qualified independently from inventory and listing controls.
