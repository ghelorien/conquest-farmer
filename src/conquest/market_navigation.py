"""Bounded alternate landings for Market and Phoenix travel corridors."""
from conquest.navigation import clear_segment
from conquest.viewport import clear_scene,DEFAULT_SIZE


def recovery_landing(terrain, source, goal, anchor, *, failed=(), used=(),viewport=DEFAULT_SIZE):
    """Find a visible 8–12 tile sidestep with a checked onward path.

    A failed click is evidence about its landing, not proof that every tile
    towards it is blocked. Keep these observations separate from map collision.
    """
    if terrain.map_id not in (1036,1011):
        return None
    excluded=set(map(tuple,failed)) | set(map(tuple,used))
    def direction(point):
        dx,dy=point[0]-source[0],point[1]-source[1]
        length=max(abs(dx),abs(dy))
        return (round(dx/length,3),round(dy/length,3)) if length else (0,0)
    used_directions={direction(p) for p in used}
    candidates=[]
    for distance in (12,10,8):
        for dx,dy in ((1,0),(-1,0),(0,1),(0,-1),(1,1),(1,-1),(-1,1),(-1,-1)):
            point=(source[0]+dx*distance,source[1]+dy*distance)
            if point in excluded or direction(point) in used_directions or not clear_segment(terrain,source,point):continue
            x=anchor[0]+(dx-dy)*distance*32
            y=anchor[1]+(dx+dy)*distance*16
            if not clear_scene((x,y),viewport):continue
            candidates.append((max(abs(a-b) for a,b in zip(point,goal)), -distance, point))
    for _,_,point in sorted(candidates):
        try:terrain.travel_path(point,goal)
        except ValueError:continue
        return point
    return None
