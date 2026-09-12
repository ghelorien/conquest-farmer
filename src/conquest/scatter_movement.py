"""Choose a long, terrain-checked jump into a fresh selected monster group."""
import time
from conquest.viewport import clear_scene,size_for


def clear_jump(terrain,source,destination):
    """Check every crossed tile, including both sides of diagonal corners."""
    dx,dy=destination[0]-source[0],destination[1]-source[1]
    steps=max(abs(dx),abs(dy))*4
    previous=source
    if not terrain.walkable(source) or not steps:return False
    for i in range(1,steps+1):
        point=(round(source[0]+dx*i/steps),round(source[1]+dy*i/steps))
        if not terrain.walkable(point):return False
        if point[0]!=previous[0] and point[1]!=previous[1]:
            if not terrain.walkable((point[0],previous[1])) or not terrain.walkable((previous[0],point[1])):return False
        previous=point
    return True


def remember_scatter(supervisor,targets):
    supervisor.last_scatter_group=(time.monotonic(),{
        (t.entity_id,t.object_address):t.current_hp for t in targets
        if getattr(t,'entity_id',None) and getattr(t,'object_address',None)
        and type(t.current_hp) is int and t.current_hp>0})


def wounded_group_in_range(supervisor,targets,position,radius):
    stamp,previous=getattr(supervisor,'last_scatter_group',(-float('inf'),{}))
    if not 0<=time.monotonic()-stamp<=3:return False
    # Only fresh, currently aimable identities count. A missing/out-of-range
    # group never pins the farmer to an old location.
    wounded={(t.entity_id,t.object_address) for t in targets
        if getattr(t,'entity_id',None) and getattr(t,'object_address',None)
        and t.world_position is not None and type(t.current_hp) is int
        and 0<t.current_hp<previous.get((t.entity_id,t.object_address),0)
        and max(abs(a-b) for a,b in zip(position,t.world_position))<=radius}
    return len(wounded)>=2


def scatter_landing(supervisor, targets, position, boundary, radius,minimum_count=1,*,anchor=(518,396)):
    terrain=supervisor.recovery.terrain
    viewport=size_for(getattr(supervisor,'observer',None))
    observed=getattr(supervisor,'scatter_scene_targets',()) or targets
    x,y=position;left,top,right,bottom=boundary
    # Use the same hunt bounds as choose_target. Otherwise a dense group just
    # outside the boundary attracts repeated jumps but can never be attacked.
    live=[t.world_position for t in observed if t.world_position is not None
          and type(t.current_hp) is int and t.current_hp>0
          and left<=t.world_position[0]<=right and top<=t.world_position[1]<=bottom]
    if not live:return None
    recent=getattr(supervisor,'scatter_landings',[])
    now=time.monotonic();recent=[(p,at) for p,at in recent if now-at<8]
    blocked={p for (world,p),until in getattr(supervisor,'movement_obstructions',{}).items()
             if world==terrain.map_id and until>now}
    candidates=[]
    for dx in range(-12,13):
        for dy in range(-12,13):
            distance=max(abs(dx),abs(dy))
            if not 8<=distance<=12:continue
            point=(x+dx,y+dy)
            if not(left<=point[0]<=right and top<=point[1]<=bottom):continue
            if point in live:continue  # Ctrl-clicking an actor can attack instead of jumping.
            px,py=anchor[0]+(dx-dy)*32,anchor[1]+(dx+dy)*16
            if not clear_scene((px,py),viewport):continue
            if any(abs(px-t.x)<=24 and -40<=py-t.y<=10 for t in observed
                   if hasattr(t,'x') and hasattr(t,'y')):continue
            steps=max(abs(dx),abs(dy))*4
            if any((round(x+dx*i/steps),round(y+dy*i/steps)) in blocked for i in range(1,steps+1)):continue
            count=sum(max(abs(p[0]-point[0]),abs(p[1]-point[1]))<=radius for p in live)
            if count<minimum_count or not clear_jump(terrain,position,point):continue
            # Keep dense ordinary groups; do not deliberately land beside bosses.
            from conquest.routes import boss_name
            if any(boss_name(m.name) and max(abs(m.position[0]-point[0]),abs(m.position[1]-point[1]))<=2
                   for m in getattr(supervisor,'escape_monsters',())):continue
            repeated=sum(max(abs(p[0]-point[0]),abs(p[1]-point[1]))<=2 for p,_ in recent)
            centrality=-sum(max(abs(p[0]-point[0]),abs(p[1]-point[1])) for p in live
                            if max(abs(p[0]-point[0]),abs(p[1]-point[1]))<=radius)
            candidates.append(((count,-repeated,centrality,distance),point))
    if not candidates:return None
    destination=max(candidates,key=lambda row:row[0])[1]
    supervisor.scatter_landings=recent+[(position,now)]
    return destination
