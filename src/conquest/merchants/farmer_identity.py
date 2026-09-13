"""Bind delivery preferences to the farmer that owns each UI or route."""
from conquest.discord_notify import read_json
from conquest.character_context import farmer_name, state_path


def ui_character(ui):
    app=getattr(ui,'app',None)
    return (getattr(app,'transfer_character',None)
            or getattr(getattr(app,'observer',None),'character',None) or farmer_name())


def route_character(loop):
    return (getattr(loop,'character',None)
            or read_json(state_path('reports/desktop-farming/app-state.json')).get('character') or farmer_name())
