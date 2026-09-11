# Set up another Windows PC

This is a work-in-progress source snapshot for the connected GitHub account
`ghelorien`. The memory-only integration is unfinished; see
[the development handoff](continue-development.md).

This is a Windows Python application. GitHub stores the source and profiles;
the game, worker and dashboard run locally on each PC. It does not host a running
game session or synchronize the two desktops.

## Install

Install 64-bit Python 3.12 or newer and Git. Clone the private repository once
its URL is available, then open PowerShell in the cloned directory:

```powershell
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e '.[dev]'
.\.venv\Scripts\python.exe -m pytest -q
```

Python's package installer rejects versions below 3.12. All commands here run
from the repository root. Install the game separately; its files are not included.

## Discover this PC's client

Start the game and log in manually, then run:

```powershell
.\.venv\Scripts\python.exe -m conquest diagnose --output reports/client-preflight.json
```

The report includes the current PID, window handle, executable architecture and
SHA-256. If several clients are open, select the intended one with `--pid`.
The supplied candidate layouts accept only SHA-256
`c2b53437ef68d687a1ef0f70c74bcf2df6027bf82b558e93330c839eb5e1c396`.
A different build needs fresh calibration; do not change the hash merely to
make the profile load. Client version labels alone do not qualify a build.

If read access fails because the game runs elevated, open PowerShell using
**Run as administrator** and repeat the diagnostic. Leave Windows UAC enabled.
Use the report's current PID and window handle when starting the worker:

```powershell
.\.venv\Scripts\python.exe -m conquest worker --pid <PID> --hwnd <HWND> --expected-sha256 c2b53437ef68d687a1ef0f70c74bcf2df6027bf82b558e93330c839eb5e1c396 --info .runtime/worker-live.json --lifetime 3600
```

Replace both placeholders with the numbers from your report. Keep that terminal
open. The worker is bounded to this process session, listens only on localhost,
and expires after one hour. Start a fresh worker after restarting the client.
Never copy `.runtime/worker-live.json` between PCs: it contains a local token
and refers to a process that exists only on the originating PC.

## Observe before enabling actions

For memory calibration, add `--read-only` to the worker command. This disables
all click, key and drag endpoints before their guards are read. The worker's
health response reports `read_only: true`; observations and shutdown remain
available. `scripts/start_memory_worker.py` discovers the current client, checks
its fingerprint and read access, and starts this mode for one hour using
`.runtime/memory-worker.json`. Run it with the repository's virtual-environment
Python from an administrator PowerShell if the ordinary read check is denied.
The launcher never reuses a stored PID or window handle and does not enable
farming or qualify any candidate field.

The worker also supports bounded `read-block` diagnostics (1 to 65,536 bytes,
base64 response, process identity checked before and after reading). Candidate
scans accept `max_mib` from 1 to 4096 and `max_candidates` from 1 to 10,000,
with a fixed 20-second limit. Defaults remain 512 MiB and 2,000 candidates.
Always inspect scan coverage and truncation; a successful scan need not cover
all eligible regions. Keep captured client bytes under ignored `reports/`.

In another PowerShell window at the repository root:

For the current HP candidate (read-only worker, no farming authorization):

```powershell
.\.venv\Scripts\python.exe -m conquest sample-health --worker-info .runtime/memory-worker.json --profile profiles/classic-1074-health-candidate.yaml --character Parasite --output reports/health-candidate.json
```

The result remains unqualified even when it matches a full-health display.
Damage, healing, death and actual client restart checks are still required.

```powershell
.\.venv\Scripts\python.exe -m conquest sample-player --worker-info .runtime/worker-live.json --profile profiles/classic-1074-player-candidate.yaml --output reports/player.json
.\.venv\Scripts\python.exe -m conquest sample-inventory --worker-info .runtime/worker-live.json --player-profile profiles/classic-1074-player-candidate.yaml --inventory-profile profiles/classic-1074-inventory-candidate.yaml --output reports/inventory.json
.\.venv\Scripts\python.exe -m conquest sample-entities --worker-info .runtime/worker-live.json --profile profiles/classic-1074-entities-candidate.yaml --output reports/entities.json
```

Exit code 3 means the diagnostic returned candidate observations, not permission
to farm. A scene change can invalidate an entity sample; retry once the scene
settles. Reports never establish current HP from maximum HP or entity membership.

Copy a farming profile to `profiles/character.local.yaml`, an ignored file, and
set its character name and route to match the actual character and area. The
checked-in profiles describe one calibration PC. Window size, controls, map
coordinates and any future input projection must be verified on the second PC.
Do not copy absolute heap addresses, PID values or window handles from old reports.

```powershell
.\.venv\Scripts\python.exe -m conquest dashboard --database reports/live-grind/trial.sqlite3 --profile profiles/character.local.yaml --worker-info .runtime/worker-live.json --port 8765
```

Open http://127.0.0.1:8765/ locally. It remains usable when the worker is absent,
showing unavailable observations. Memory-only mode currently reports current HP
as unavailable and blocks farming before capture or input. The legacy visual
implementation is retained for historical tests, but is not selected by supplied
profiles. See [memory observation status](memory-observation-status.md).

## Keep computers in sync

Use `git pull --ff-only` for source updates. Keep character-specific settings in
ignored `*.local.yaml` profiles. Do not commit `.runtime`, `.venv`, reports,
SQLite databases, memory dumps, logs, passwords or game installation files.
Run the tests after updating, and rerun diagnostics after a client update.
