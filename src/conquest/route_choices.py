"""Presentation of saved routes; archived experiments remain loadable by ID."""

# Keep comparison definitions for historical runs and the optimization engine.
ARCHIVED_BANDIT_ROUTES = frozenset({
    'bandit-original', 'bandit-east', 'bandit-south', 'bandit-southeast',
    'bandit-far-east', 'bandit-east-ridge', 'bandit-eastern-corridor',
    'bandit-southern-fields', 'bandit-northern-fields',
    'bandit-central-eastern-fields', 'bandit-combined-fields',
    'bandit-wide-circuit', 'bandit-region-rotation',
})

BUILTIN_LABELS = {
    'pheasant': 'Pheasants', 'turtledove': 'Turtledoves', 'robin': 'Robins',
    'apparition': 'Apparitions', 'poltergeist': 'Poltergeists',
    'wingedsnake': 'Winged Snakes', 'bandit': 'Bandits',
    'firespirit': 'Fire Spirits',
}


def route_label(route):
    name = BUILTIN_LABELS.get(route.id, route.name)
    low, high = route.recommended_levels
    return f'{name} ({low}–{high})'


def saved_route_choices(routes):
    return sorted(
        (route for route in routes if route.id not in ARCHIVED_BANDIT_ROUTES),
        key=lambda route: (*route.recommended_levels, route_label(route).casefold(), route.id),
    )
