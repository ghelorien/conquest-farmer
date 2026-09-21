# Immutable releases

Build a release from a clean checkout, then activate it only after the staged
copy, its local virtual environment, and its exact manifest verify:

```powershell
py scripts/release.py build C:\src\Conquest-farmer C:\releases 2026.09.19
py scripts/release.py activate C:\releases\2026.09.19
py scripts/release.py launch
```

`build` creates a copied release-local `.venv` and invokes `pip install .`, never
an editable install. It may fetch the declared production dependencies; tests
inject a command runner and do not use a network. The manifest hashes every
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
