"""One memory-qualified walking edge missing from the Phoenix terrain graph.

The corner exit (its player profile, object projection offset and click) was
qualified only on the retired 1074 client. No build has a qualified exit, so
once the corner state is confirmed the recovery fails closed without input.
"""

TERRAIN_SHA256 = "433b3163a38d068e978fa6609c2d8e32f48bd27b188e15cddc3b3583ca9e9921"
SOURCE = (195, 227)
DESTINATION = (196, 228)


def recover_corner(loop, goal):
    terrain = loop.terrain
    if (
        getattr(terrain, "map_id", None) != 1011
        or getattr(terrain, "source_sha256", None) != TERRAIN_SHA256
    ):
        return False
    health = loop.living()
    life = health["embedded_controls"]["life"]
    if (
        life.get("character") != "Parasite"
        or life["map_id"] != 1011
        or tuple(life["position"]) != SOURCE
        or terrain.map_id != 1011
        or terrain.source_sha256 != TERRAIN_SHA256
        or life["dead_candidate"]
        or life.get("ghost_candidate")
        or not terrain.walkable(SOURCE)
        or not terrain.walkable(DESTINATION)
    ):
        return False
    # The edge was observed twice through memory; do not generalize it into
    # permission to cross other blocked corners or change collision tiles.
    terrain.travel_path(DESTINATION, tuple(goal))
    for _ in range(3):
        if not loop.town("clear-travel-panels").get("closed_panel"):
            break
    else:
        raise ValueError("Town panels still block the qualified corner exit")
    health = loop.living()
    life = health["embedded_controls"]["life"]
    if (
        tuple(life["position"]) != SOURCE
        or life["map_id"] != 1011
        or life["character"] != "Parasite"
        or life["dead_candidate"]
        or life.get("ghost_candidate")
        or health["embedded_controls"].get("manual_mouse")
        or health["embedded_controls"]["control"]["enabled"]
    ):
        raise ValueError("Qualified corner departure state changed")
    raise ValueError("Blacksmith corner exit is not qualified for this client build")
