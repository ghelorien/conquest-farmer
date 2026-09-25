"""Trial templates as the desktop app runs them against the 1078 client.

Profile templates name no memory profiles. For the attached exact build the
desktop app selects the 1078 player and inventory profiles
(desktop_app.run_embedded_farm's automation_ready_build override); trial-loop
tests use that same selection.
"""

from pathlib import Path

import yaml

EXACT_1078_PROFILES = {
    "player_profile": "profiles/classic-1078-player-candidate.yaml",
    "inventory_profile": "profiles/classic-1078-inventory-candidate.yaml",
}


def trial_template(name):
    config = yaml.safe_load(Path("profiles", name).read_text(encoding="utf-8"))
    config.update(EXACT_1078_PROFILES)
    return config
