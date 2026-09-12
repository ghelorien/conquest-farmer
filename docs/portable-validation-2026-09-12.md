# Portable UI validation — September 12, 2026

Branch: `codex/portable-character-ui`.
Behavior baseline integrated: `a431fb6` (initial baseline `b38b4cd`).
Integration merge: `801237c`.

| Check | Result |
| --- | --- |
| Full integrated regression suite (`python -m pytest -q --tb=short`) | **2,031 passed**, 134.42 seconds |
| Initial portable registry/migration/attachment tests | 46 passed; expanded in the final suite |
| Intermediate affected regression subset | 218 passed |
| Hidden native UI (`python scripts/validate_portable_ui.py`) | Passed; synthetic profiles, no game discovery/input |
| Python compilation | Passed |
| `git diff --check` | Passed |
| GitHub repository visibility | Verified private |

The migration test initially reproduced a Windows rename failure because SQLite
connection context managers commit but do not close handles. Migration now
explicitly closes both backup and rewrite connections before activating the
staged directory; the interrupted-migration and preservation tests pass.

Geometry tests cover all combinations of 1366×768, 1920×1080 and 3840×2160 with
100%, 125%, 150%, 175% and 200% scaling, plus negative origins and saved-monitor
removal. These validate calculation and fallback behavior, not physical displays.

No running game was reparented, resized, clicked, logged in, traded or repriced
during this phase. The active checkout, launcher and its state were not replaced.
The actual second-PC rollback is still unreproduced here. Live native hosting on
both PCs, actual mixed-DPI rendering, and simultaneous independent farmer sessions
on separate PCs remain **untested**. They are release gates listed in
[the rollout guide](portable-character-profiles.md#validation-and-release-gate).

Current engine limits remain explicit: the America client build is qualified;
additional servers and unavailable skills are blocked. In particular, this UI
does not add a combat fallback for a fresh archer whose required skill observations
are unavailable. That behavior remains owned by the behavior workstream.
