# Classic Conquer Archer bot

Windows Python diagnostics and a supervised Archer farming prototype using
read-only process memory, normal input, YAML configuration, SQLite statistics,
and a small local dashboard.

**Snapshot: September 11, 2026.** The Windows client wrapper and foreground
farmer use read-only memory for gameplay decisions. The current implementation
includes saved leveling routes, jump/Scatter combat, healing and revival,
restocking and equipment checks, verified loot, Discord notifications, warehouse
banking and the Market Meteor route. Background/minimized input remains
unqualified. Client-specific memory layouts still need validation on another PC.

Recent fixes keep healing and revival active during recoverable movement stalls,
improve Phoenix/Market path recovery, accept all encoded Scatter ranks while
retaining skill/range validation, and surface the actual farming startup error.
This is still an evolving prototype: safe travel and the 40–50 verified kills/minute
target are not guaranteed. See the [current handoff](docs/snapshot-2026-09-11.md).

## Install and test

On Windows with 64-bit Python 3.12 or newer, from the repository root:

```powershell
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e '.[dev]'
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m conquest --help
```

Tests use fake observations and input backends plus Windows integration checks;
they do not qualify live gameplay. Validation results for this publication are
recorded in the current handoff.

See [other-PC setup](docs/other-pc-setup.md) for process discovery, worker startup,
profile calibration, diagnostics and the dashboard. This work-in-progress snapshot
includes a [development handoff](docs/continue-development.md) for the next PC.
The game must be installed separately on each PC.

## Available commands

The native client launcher/wrapper opens with
`.\.venv\Scripts\pythonw.exe scripts/start_desktop_app.py`. It provides client
selection, automatic embedding after launch, release/reconnect, and foreground
farming controls. See [wrapper status and verification](docs/client-wrapper.md)
for the tested lifecycle and remaining live-client limitations.

| Command | Purpose |
| --- | --- |
| `diagnose` | Identify the executable, fingerprint, window and read access |
| `calibrate`, `observe` | Discover and record candidate memory values without input |
| `sample-player` | Resolve character name, maximum HP and position from memory |
| `sample-health` | Decode candidate current HP alongside maximum HP; never authorizes input |
| `sample-inventory` | Read item IDs, quantities and equipped arrows from memory |
| `sample-entities` | Read candidate monster IDs and coordinates from the scene collection |
| `record-route` | Record map-coordinate waypoints from memory |
| `dashboard` | Persistent On/Off switch, exact monster ID selection, and live memory candidates |
| `worker` | Start a bounded localhost worker pinned to one process session |
| `farm-trial` | Historical supervised loop; blocked by the memory-only gate |
| `probe-input` | Explicit diagnostic of a calibrated window-directed click |

Successful candidate diagnostics return exit code 3 and never authorize farming.
Executable fingerprints are enforced, pointer chains are resolved afresh, and
invalid or stale observations cannot silently become healthy state. No memory
writes, injection, packet changes, client modification, bypasses or drivers are
implemented.

## Integration status

[Memory observation status](docs/memory-observation-status.md) records what is
validated and what remains uncertain. Offset `0x3e0` is **maximum HP**, not current
HP. Entity collection membership alone does not establish that a monster is alive.
Both bundled farming profiles select `memory_only`; old profiles without this
setting also default to memory-only. The dashboard explicitly shows unavailable
qualified HP until a validated current-HP reader exists. The controls dashboard
can display the separately labeled, unqualified HP candidate.

## Local controls

```powershell
.\.venv\Scripts\python.exe -m conquest dashboard --database reports/live-grind/trial.sqlite3 --worker-info .runtime/memory-worker.json --health-profile profiles/classic-1074-health-candidate.yaml --port 8765
```

Open `http://127.0.0.1:8765/`. Add individual monster IDs or select them from
the observed scene. IDs and input mode are saved locally; On/Off survives browser
refreshes and focus changes, but a service restart begins Off. Changing IDs or
turning Off invalidates pending dispatches. No selected IDs means no attacks.

The production attack dispatcher is not connected. Background input, current HP
changes, and monster life state remain unqualified, so On currently reports
blocked or waiting rather than performing attacks. Focus independence of the
control setting does not establish that the client accepts background attacks.

The memory launcher checks for a compatible running worker before starting one.
An existing elevated worker needs no additional UAC approval. A new worker may
still require Windows approval after its one-hour lifetime, F12 stop, or exit.

`scripts/start_input_probe_worker.py` starts a bounded four-hour diagnostic
worker. It sends no input on startup. A revision-7 input worker supports one
`background-click` request, using the freshly decoded HP candidate instead of
the old maximum-HP guard. `scripts/probe_background_worker.py` records player
state before and after one calibrated click and never retries. The existing
read-only worker rejects this operation. A successful message queue result
does not qualify attacks; inspect the actual panel/game effect first.

The latest [background input comparison](docs/background-input-status.md)
confirmed that the calibrated point opens Status with foreground SendInput,
but the same point with background window messages moved the character instead.
Background attacks remain disabled.

Historical visual code is retained behind `legacy_visual` for regression tests.
It is not the active configuration. Its earlier behavior, calibration history
and original diagnostic instructions are archived in
[historical diagnostics](docs/historical-diagnostics.md); they do not override
the current memory-only gate.

The historical loop prioritizes F1 potion use below 40%, bounds input sequences,
and uses F11 to pause and F12 to stop. Controls require calibration on each client.
Automatic resurrection/return, loot, equipment/skill progression, and route
transitions still need live qualification.

Local tokens, reports, databases, logs, memory dumps, virtual environments and
`profiles/*.local.yaml` are ignored by Git. Never publish game installation files
or copy a worker connection file between computers.
