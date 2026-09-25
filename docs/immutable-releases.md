# Immutable releases

Build a release from a clean checkout, then activate it only after the staged
copy, its local virtual environment, and its exact manifest verify:

```powershell
py scripts/release.py build C:\src\Conquest-farmer C:\releases 2026.09.19
py scripts/release.py activate C:\releases\2026.09.19
py scripts/release.py launch
```

`build` creates a copied release-local `.venv` and invokes
`pip install ".[market]"`, never an editable install, so every release carries
the market add-on (Playwright). It may fetch the declared production
dependencies; tests inject a command runner and do not use a network.

It then runs `<release venv python> -B -m playwright install chromium` with
`PLAYWRIGHT_BROWSERS_PATH=%LOCALAPPDATA%\Conquest\ms-playwright` (the managed
data root, or `build --data-root <root>`). Chromium is machine state, not
release content: it lives outside every release, so it is not in the manifest,
is shared by all releases, and never enters git (`ms-playwright/` is ignored).
A failed browser download does not fail the build; the result lists a
`WARNING` with the folder and the exact PowerShell command to run later:

```powershell
$env:PLAYWRIGHT_BROWSERS_PATH='C:\Users\<you>\AppData\Local\Conquest\ms-playwright'; & 'C:\releases\2026.09.19\.venv\Scripts\python.exe' -m playwright install chromium
```

`launch` and every worker environment set `PLAYWRIGHT_BROWSERS_PATH` to that
same `<data root>\ms-playwright`, overriding any inherited value, so an app
started from another host (for example one whose `LOCALAPPDATA` differs) finds
the same browser. If Chromium is missing, market collection reports that once
with the same command and does not retry every minute; after installing it,
use the market refresh (or start a new shop update) to try again. The manifest hashes every
release file, including all packaged profiles and the virtual environment. Its
only exclusion is `release-manifest.json` itself, which cannot self-hash. The
activation receipt in `%LOCALAPPDATA%\Conquest\active-release.json` pins that
manifest digest, so a changed manifest also fails before launch.

Builds select only `pyproject.toml`, `README.md`, `AGENTS.md`, and the `src`,
`scripts`, `profiles`, `docs`, and `data` trees. Git metadata, caches, test
trees, arbitrary working-tree files, and normal runtime-state trees are not
copied. Reparse points are rejected in selected input and in every excluded
live-state tree; local profile YAMLs are excluded (and a reparse one is rejected).
All mutable machine state stays in
`%LOCALAPPDATA%\Conquest`, where the existing migration, profile registry, and
managed application lock continue to operate. The active receipt is atomically
replaced only after verification; it retains the previous receipt. Use
`py scripts/release.py rollback` to atomically restore that verified prior
release. Releases are deliberately retained rather than deleted by this tool.
The launcher runs the release's own interpreter with `-B` and
`PYTHONDONTWRITEBYTECODE=1`, so importing it cannot add bytecode cache files to
the exact verified tree.
