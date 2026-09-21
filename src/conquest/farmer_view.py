"""Persisted extra vertical space for the farmer's owned game window."""
from conquest.discord_notify import read_json
from conquest.character_context import state_path


def apply(app):
    factor = float(read_json(state_path('.runtime/farmer-view.json')).get('height_scale', 1.0))
    if not 1 <= factor <= 1.15: factor = 1.0
    app.host.api.height_scale = factor
    app.host.resize(app.pane.winfo_width(), app.pane.winfo_height())
