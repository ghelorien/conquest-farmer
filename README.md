# Classic Conquer Archer bot

Windows Python diagnostics and a supervised Archer farming prototype using
read-only process memory, normal input, YAML configuration, SQLite statistics,
and a small local dashboard.

**Current status:** the default is memory-only observation. Farming is blocked
until current HP, monster alive state, ground loot and revival state are
validated in memory. No screen capture or visual fallback starts in this mode.
The historical foreground loop achieved verified Pheasant and Turtledove kills,
but a complete memory-only farmer and the supervised 30-minute acceptance run
are unfinished. Background and minimized input remain unqualified.

## Install and test

On Windows with 64-bit Python 3.12 or newer, from the repository root:

```powershell
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e '.[dev]'
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m conquest --help
```

The latest local run passed **185 tests**. Tests use fake observations and input
backends plus Windows integration checks; they do not qualify live gameplay.

See [other-PC setup](docs/other-pc-setup.md) for process discovery, worker startup,
profile calibration, diagnostics and the dashboard. This work-in-progress snapshot
includes a [development handoff](docs/continue-development.md) for the next PC.
The game must be installed separately on each PC.

## Available commands

| Command | Purpose |
| --- | --- |
| `diagnose` | Identify the executable, fingerprint, window and read access |
| `calibrate`, `observe` | Discover and record candidate memory values without input |
| `sample-player` | Resolve character name, maximum HP and position from memory |
| `sample-inventory` | Read item IDs, quantities and equipped arrows from memory |
| `sample-entities` | Read candidate monster IDs and coordinates from the scene collection |
| `record-route` | Record map-coordinate waypoints from memory |
| `dashboard` | Show local statistics and available live memory observations |
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
HP until a validated current-HP reader exists.

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
