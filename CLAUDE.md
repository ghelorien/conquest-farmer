# Claude Code handoff: conquest-farmer

Gameplay and merchant behaviour rules live in AGENTS.md. They are the spec;
follow them. @AGENTS.md

## What this is

Windows-only Python bot for the Classic Conquer client (ImConquer.exe): an
archer farmer plus two merchants (Spiritual, Dutch). It reads game memory at
per-build addresses and refuses to run on an unrecognised client build.
The only live client build is **1078**. Build 1074 is dead. Future game
updates will obsolete 1078 too, so layouts stay keyed per build.

Working branch: `codex/reliable-1078-cycle` (main is not the working base).
`r38-baseline` tags `1c8a1ff`, the last live-verified 1078 cycle before cleanup.

## Ground rules on this machine

- The bot moves real items and silver. Never start, stop or restart live
  input, trades, deliveries or travel without the user's explicit go-ahead
  in the session. Reading logs, reports and databases is fine.
- Edit in a separate git worktree from the checkout the bot runs from.
  Restarting the bot on new code is a deliberate, user-approved step.
- One agent on the branch at a time. The user also works with Codex; check
  `git status` and `git log` for unexpected changes before committing.
- Never repoint a 1074-only code path at 1078. That would switch on paths
  that were never live-verified on 1078. Delete or fail closed instead.
- One commit per logical change. Run the full suite before and after each.

## Cleanup status (plan steps 1-2)

Done (commits after d937e88):
- `4aeeec6` ruff format of src/tests/scripts (no behaviour change)
- `44c0795` removed unreferenced modules: controller, background_mouse_lab,
  item_definitions, merchants/delivery_recovery, merchants/sales_recovery
  (+ their tests)
- `dd41160` 1078 now has its own profiles/classic-1078-entities-candidate.yaml;
  it previously loaded the 1074 entities file and patched six fields.

Held: `merchants/delivery_offer_probe.py` is unimported but kept until the
user confirms it is dead.

Test baseline (Linux sandbox, so Windows-only tests could not run): 4150 pass
after the commits above; ~1133 fail only on Windows imports (ctypes.windll,
msvcrt, win32api, pywintypes). **First job here: run the full suite on
Windows and record the real baseline.**

## Remaining: strip 1074 support (awaiting user decision)

A. Named scope, mechanical:
- profiles/classic-1074-{entities,health,inventory,player}-candidate.yaml
- memory_build_layout: CLIENT_SHA256_1074, its READ_LAYOUTS entry,
  _OLD_SLOTS, legacy branch of entity_reader_layout
- 1074 table entries in scene_pointer, scatter_selection, loot_ownership
- merchants/runtime.py make_observer: drop the 1074 EmbeddedObserver branch
- desktop_app 1074 fallbacks; cli.py default --entity-profile;
  README dashboard example still passes the 1074 health profile

B. Hidden layer: `memory_life.CLIENT_SHA256` IS the 1074 hash under a generic
name (103 refs, 24 src/script files, 20 test files).
- Gates that already reject 1078 (dead on the live client): memory_life,
  background_farmer_observation, memory_shop and memory_warehouse defaults,
  town_corner; merchants/ booth_target, client_launch, connect_market,
  flag_target, memory (default constructor), qualification,
  request_identity, trade_controls, rollout, diagnostic_worker;
  scripts/start_merchant_diagnostics.py, scripts/inspect_merchants.py
- Dual-build sets: delivery_promotion, delivery_qualification (drop 1074 member)
- Records stamped with the 1074 hash: qualification, background_observation

C. Keep: profiles/valuable-items.json (loaded on any build; hash is
provenance) and the town-*/player-neighborhood observation YAMLs (historical,
not loaded by src).

Proposed approach, pending the user's yes: do A, then B one module per
commit, reading each module first and deleting 1074-only functions/branches
(whole modules only where nothing 1078-live remains).
