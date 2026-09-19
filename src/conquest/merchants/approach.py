"""Rank checked standing tiles; only a fresh memory probe authorizes arrival."""
import time
from conquest.viewport import clear_scene

DELIVERY_PROBE_MAX_AXIS_DELTA = 12


def within_delivery_probe_range(farmer_position, merchant_position):
    """The exact bilateral world-distance contract used by the live probe."""
    if (not isinstance(farmer_position, (list, tuple)) or len(farmer_position) != 2
            or not isinstance(merchant_position, (list, tuple)) or len(merchant_position) != 2
            or any(type(value) is not int or value < 0
                   for value in (*farmer_position, *merchant_position))):
        raise ValueError('Delivery participant positions are invalid')
    return max(abs(a-b) for a, b in zip(farmer_position, merchant_position)) \
        <= DELIVERY_PROBE_MAX_AXIS_DELTA


def positions(terrain,probe,*,used=(),deadline=None):
    source=tuple(probe['farmer_position']);target=tuple(probe['merchant_position'])
    point=probe['point'];viewport=tuple(probe['viewport'])
    used=set(map(tuple,used))|{source,target}|set(map(tuple,probe.get('occupied_tiles',[])))
    candidates=[]
    for dx in range(-DELIVERY_PROBE_MAX_AXIS_DELTA, DELIVERY_PROBE_MAX_AXIS_DELTA+1):
        for dy in range(-DELIVERY_PROBE_MAX_AXIS_DELTA, DELIVERY_PROBE_MAX_AXIS_DELTA+1):
            p=(target[0]+dx,target[1]+dy)
            if p in used or not terrain.walkable(p):continue
            sx,sy=p[0]-source[0],p[1]-source[1]
            predicted=(point[0]-(sx-sy)*32,point[1]-(sx+sy)*16)
            if not clear_scene(predicted,viewport):continue
            candidates.append((max(abs(sx),abs(sy)),p))
    ranked=[]
    for _,p in sorted(candidates)[:24]:
        if deadline is not None and time.time()>=deadline:break
        try:path=terrain.travel_path(source,p)
        except ValueError:continue
        if not path or tuple(path[0])!=source or tuple(path[-1])!=p:continue
        distance=sum(max(abs(a[0]-b[0]),abs(a[1]-b[1])) for a,b in zip(path,path[1:]))
        ranked.append((distance,p))
    return [p for _,p in sorted(ranked)]


def ingress_position(terrain,probe,*,used=(),deadline=None):
    """Choose one checked visible step toward an off-scene receiver.

    The destination comes from the fresh bilateral world positions.  The
    receiver tile and every known occupied tile remain excluded; recipient
    targeting is deferred until a later memory probe sees the exact UID.
    """
    if probe.get('reason')!='recipient_absent' or probe.get('point') is not None:
        raise ValueError('Remote ingress requires an exact absent-recipient probe')
    source=tuple(probe['farmer_position']);target=tuple(probe['merchant_position'])
    if source==target:return None
    if deadline is not None and time.time()>=deadline:return None
    try:path=terrain.travel_path(source,target)
    except ValueError:return None
    if not path or tuple(path[0])!=source or tuple(path[-1])!=target:return None
    avoid=(set(map(tuple,used))|{target}|set(map(tuple,probe.get('occupied_tiles',[]))))-{source}
    from conquest.navigation import travel_waypoint
    try:
        return travel_waypoint(terrain,path,DELIVERY_PROBE_MAX_AXIS_DELTA,
                               avoid=avoid,viewport=tuple(probe['viewport']))
    except ValueError:
        return None
