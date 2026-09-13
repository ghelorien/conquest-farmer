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

After review, the user authorized combining these fixes with the other PC's
version. A fresh fetch still showed `ca58e7b` at the target branch, so the
tested production code required no further changes. Publication targets
`codex/merchant-automation` with an ordinary fast-forward push; no force push
or rewriting of the other PC's history is permitted. The earlier base commit
remains an ancestor for comparison and rollback through a reviewed revert.
Healing, travel care, combat, merchant modules and the desktop UI were checked
again and are identical to that base. Deployment remains separate: the active
launcher and installed/running app were not changed by publication.

## Subsequent live validation on this PC

Published source `d8f4a4c7f882472ce7d4495ee93593700464ad6a` was then exercised
against the running Kilhiam client (PID 25124, creation time
134337152471239177) using a bounded read-only diagnostic. Direct access first
failed with Windows error 5. The separately elevated diagnostic opened only a
query/read memory session and disabled all game-input operations; it exited
after the observations. It did not launch the desktop app, embed the game,
start farming, use a potion, move the character or alter a launcher.

All **20 of 20** samples passed over 10.35 seconds. Each verified life,
equipment, inventory, ground observations and process identity. The slowest
complete sample took 0.016 seconds. Observed character state remained at map
1011, tile (251,332), level 43, HP 513/681, three inventory entries and 207
silver. Equipped ScarletBow UID 293092845 consistently reported sockets
255/0. The merchant item-reading path independently returned the same UID and
socket bytes with equipment quantity one. All ground samples were valid but
empty; no new nonempty drop or pickup was exercised in this run. Earlier
controlled live-drop evidence remains documented in socket-memory-mapping.md.

### Full desktop rollout is not qualified for Kilhiam

The integration deliberately retained the other PC's desktop code. Its
`DesktopApp.make_observer` and embedded control runtime still select Parasite
explicitly. The portable-profile UI from the separate local branch was not
part of the two approved fixes. Therefore launching this combined desktop
against Kilhiam would not constitute a supported character match.

The changed readers pass live validation, but **the full combined app has not
passed end-to-end validation on this PC**. Combat, hotkey healing, delivery,
merchant trades and booth operations were not exercised. That requires either
testing with the supported characters on the other PC or a separately reviewed
integration of portable character support. Do not replace the Kilhiam launcher
on the basis of the reader checks alone.

Detailed local evidence: `%LOCALAPPDATA%\Conquest\diagnostics\live-combined-readers.json`.
