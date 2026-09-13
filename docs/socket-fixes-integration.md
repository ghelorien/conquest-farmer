# Socket/ground fixes integrated with the other PC's update

Integration base: `ca58e7b00748706764a171a9e88682e1e177996c` from
`origin/codex/merchant-automation`, fetched September 13, 2026. The default
`main` branch is older and was not used as the integration base.

Review branch: `codex/integrate-socket-ground-fixes`.

Only these two local commits were cherry-picked, without conflicts:

- `6f4c488` -> `ddb98eb`: corrected full-item socket offsets and ground UID
  mapping, with focused regressions and qualification evidence.
- `588ef2a` -> `c2de525`: documented the controlled socketed-bow drop and safe
  retrieval, including the remaining limit on ground socket identification.

The portable UI branch was not merged. Its other farming, UI, route, profile
and runtime changes were not transferred. Relative to the integration base,
the only changed production files are `equipment.py` and `memory_ground.py`.
The other PC's desktop UI, native farmer and merchant modules are unchanged.
No credentials, runtime catalogs, saved routes, live journals or launcher
configuration were copied into the integration branch.

## Validation

- Full integrated test suite: **2,209 passed**, 109.49 seconds.
- Desktop entry-point import check passed using this worktree's source.
- Diff whitespace check passed.
- Replayed the recorded real client's ScarletBow item bytes through the
  integrated equipment reader: UID 293092845, socket bytes 255/0.
- Replayed the captured ground registry and two actor snapshots through the
  integrated ground reader: ground UID 2068190997, type 500069, tile (54,139),
  plus zero; advancing rendering state did not invalidate item identity.
- The GitHub repository's visibility was confirmed private.

The earlier live app bridge was no longer present, so this is not a new live
combined-version qualification. The replay uses previously captured evidence;
it does not exercise the other PC's newly changed delivery/combat behavior.
No running app was launched, restarted or replaced for this integration.

Socket values on full item records are corrected, but sockets on unknown
ground drops remain unqualified. Before activating merchant pricing, refresh
owned inventory and booth observations: old cached observations may contain
socket classifications from the former offsets. Existing pricing rules and
history were not rewritten.

Publication remains separate from integration. This branch was prepared
locally for review; no remote branch was updated, no merge was performed and
the active launcher remains unchanged. Re-fetch the target before publication
and reassess any later changes from the other PC.
