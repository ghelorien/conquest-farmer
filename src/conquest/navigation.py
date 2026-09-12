"""Read-only terrain routing with bounded A* and explicit portal avoidance.

Version-1003 DMap cells are access/surface/height records with a row checksum.
Scene collision tiles use the bottom-right anchor plus signed part offsets. Paths still require live position feedback for dynamic obstructions.
"""
from dataclasses import dataclass
import hashlib
import heapq
import json
from pathlib import Path
import struct

import numpy as np


@dataclass
class TerrainMap:
    map_id: int
    width: int
    height: int
    blocked: np.ndarray
    source_sha256: str
    portals: tuple
    excluded_scenes: tuple

    def walkable(self, point):
        x,y = point
        return 0<=x<self.width and 0<=y<self.height and not self.blocked[y,x]

    def path(self, start, goal, *, avoid=(), limit=250000):
        start,goal = tuple(start),tuple(goal)
        excluded = set(map(tuple,avoid))
        if not self.walkable(start) or not self.walkable(goal) or goal in excluded:
            raise ValueError('Route endpoint is blocked or outside the map')
        def heuristic(point):
            # Cardinal walking steps avoid cutting the corner of a solid tile.
            return abs(point[0]-goal[0])+abs(point[1]-goal[1])
        heap, costs, previous = [(heuristic(start),0,start)], {start:0}, {}
        visited = 0
        while heap:
            _,cost,point = heapq.heappop(heap)
            if costs.get(point)!=cost:
                continue
            if point==goal:
                result = [goal]
                while result[-1]!=start:
                    result.append(previous[result[-1]])
                return list(reversed(result))
            visited+=1
            if visited>limit:
                raise ValueError('Route search exceeded its node budget')
            x,y=point
            for neighbor in ((x+1,y),(x-1,y),(x,y+1),(x,y-1)):
                if neighbor in excluded or not self.walkable(neighbor):
                    continue
                candidate=cost+1
                if candidate<costs.get(neighbor,float('inf')):
                    costs[neighbor],previous[neighbor]=candidate,point
                    heapq.heappush(heap,(candidate+heuristic(neighbor),candidate,neighbor))
        raise ValueError('No traversable route between the endpoints')

    def straight_path(self, start, goal, *, avoid=(), limit=250000):
        """Among shortest checked routes, prefer the fewest changes of direction."""
        start,goal=tuple(start),tuple(goal)
        excluded=set(map(tuple,avoid))
        if not self.walkable(start) or not self.walkable(goal) or goal in excluded:
            raise ValueError('Route endpoint is blocked or outside the map')
        h=lambda p:abs(p[0]-goal[0])+abs(p[1]-goal[1])
        initial=(start,(0,0));costs={initial:(0,0)};previous={}
        heap=[(h(start),0,0,initial)];visited=0
        while heap:
            _,turns,negative_steps,state=heapq.heappop(heap)
            steps=-negative_steps
            if costs.get(state)!=(steps,turns):continue
            point,direction=state
            if point==goal:
                result=[point]
                while state!=initial:
                    state=previous[state];result.append(state[0])
                return list(reversed(result))
            visited+=1
            if visited>limit:
                # Preserve reachability if direction-aware search gets too big.
                return self.path(start,goal,avoid=avoid,limit=limit)
            for delta in ((1,0),(-1,0),(0,1),(0,-1)):
                neighbor=(point[0]+delta[0],point[1]+delta[1])
                if neighbor in excluded or not self.walkable(neighbor):continue
                next_state=(neighbor,delta)
                cost=(steps+1,turns+int(direction!=(0,0) and direction!=delta))
                if cost<costs.get(next_state,(float('inf'),float('inf'))):
                    costs[next_state]=cost;previous[next_state]=state
                    heapq.heappush(heap,(cost[0]+h(neighbor),cost[1],-cost[0],next_state))
        raise ValueError('No traversable route between the endpoints')

    def travel_path(self, start, goal, *, avoid=(), limit=250000):
        """Route in eight directions, then remove terrain-visible detours."""
        start,goal=tuple(start),tuple(goal)
        excluded=set(map(tuple,avoid))
        if not self.walkable(start) or not self.walkable(goal) or goal in excluded:
            raise ValueError('Route endpoint is blocked or outside the map')
        if clear_segment(self,start,goal,avoid=excluded):return line_tiles(start,goal)
        h=lambda p:max(abs(p[0]-goal[0]),abs(p[1]-goal[1]))
        heap=[(h(start),0,start)];costs={start:0};previous={};visited=0
        while heap:
            _,cost,point=heapq.heappop(heap)
            if costs.get(point)!=cost:continue
            if point==goal:
                path=[goal]
                while path[-1]!=start:path.append(previous[path[-1]])
                path.reverse();result=[start];index=0
                while index<len(path)-1:
                    end=len(path)-1
                    while end>index+1 and not clear_segment(self,path[index],path[end],avoid=excluded):end-=1
                    result.extend(line_tiles(path[index],path[end])[1:]);index=end
                return result
            visited+=1
            if visited>limit:raise ValueError('Route search exceeded its node budget')
            for dx,dy in ((1,0),(-1,0),(0,1),(0,-1),(1,1),(1,-1),(-1,1),(-1,-1)):
                neighbor=(point[0]+dx,point[1]+dy)
                if not clear_segment(self,point,neighbor,avoid=excluded):continue
                candidate=cost+1
                if candidate<costs.get(neighbor,float('inf')):
                    costs[neighbor]=candidate;previous[neighbor]=point
                    heapq.heappush(heap,(candidate+h(neighbor),candidate,neighbor))
        raise ValueError('No traversable route between the endpoints')


def line_tiles(start,end):
    dx,dy=end[0]-start[0],end[1]-start[1];length=max(abs(dx),abs(dy))
    if not length:return [tuple(start)]
    return [(start[0]+round(dx*i/length),start[1]+round(dy*i/length)) for i in range(length+1)]


def clear_segment(terrain,start,end,*,avoid=()):
    """Check every crossed cell, including both sides of diagonal corners."""
    cells=line_tiles(start,end)
    for i,p in enumerate(cells):
        if p in avoid or not terrain.walkable(p):return False
        if i:
            a=cells[i-1]
            if a[0]!=p[0] and a[1]!=p[1]:
                if any(q in avoid or not terrain.walkable(q) for q in ((a[0],p[1]),(p[0],a[1]))):return False
    return True


def travel_waypoint(terrain,path,maximum_step=12,*,avoid=(),viewport=(1036,793)):
    """Take the farthest visible checked landing; short segments remain runs."""
    source=path[0]
    for point in reversed(path[1:maximum_step*2+1]):
        if max(abs(a-b) for a,b in zip(source,point))>maximum_step:continue
        dx,dy=native_movement_delta(point[0]-source[0],point[1]-source[1],viewport=viewport)
        target=(source[0]+dx,source[1]+dy)
        if target!=tuple(source) and clear_segment(terrain,source,target,avoid=avoid):return target
    raise ValueError('No visible route landing point')


def straight_waypoints(path, maximum_step=4):
    """Compress only collinear, already checked steps; never skip corners."""
    if not 1<=maximum_step<=12:
        raise ValueError('Movement segments must be one to twelve tiles')
    if not path:
        return []
    result=[tuple(path[0])]
    index=0
    while index<len(path)-1:
        direction=(path[index+1][0]-path[index][0],path[index+1][1]-path[index][1])
        if abs(direction[0])+abs(direction[1])!=1:
            raise ValueError('Route contains a non-adjacent step')
        end=index+1
        while end<len(path)-1 and end-index<maximum_step:
            step=(path[end+1][0]-path[end][0],path[end+1][1]-path[end][1])
            if step!=direction:
                break
            end+=1
        result.append(tuple(path[end]))
        index=end
    return result


def native_movement_delta(dx,dy,*,viewport=(1036,793)):
    """Use the longest visible landing point outside HUD and chat controls."""
    length=max(abs(dx),abs(dy))
    for distance in range(min(12,round(length)),0,-1):
        x,y=round(dx*distance/length),round(dy*distance/length)
        from conquest.viewport import clear_scene
        px,py=viewport[0]//2+(x-y)*32,viewport[1]//2+(x+y)*16
        if clear_scene((px,py),viewport):
            return x,y
    return 0,0


def native_waypoint(path, maximum_step=12,*,viewport=(1036,793)):
    target=straight_waypoints(path,maximum_step)[1]
    source=path[0]
    if max(abs(target[0]-source[0]),abs(target[1]-source[1]))<3:
        # A one-tile click can hit the player's sprite instead of the ground.
        # Run to a nearby checked point using the client's walking pathfinder;
        # keep it below jump distance so this never jumps across a corner.
        target=tuple(path[min(4,len(path)-1)])
    dx,dy=native_movement_delta(target[0]-source[0],target[1]-source[1],viewport=viewport)
    if dx==dy==0:
        raise ValueError('No visible route landing point')
    return source[0]+dx,source[1]+dy


def visible_cardinal_step(terrain,source,goal,anchor,*,avoid=(),allow_detour=False,bounds=(80,140,956,667)):
    """Find a clear straight detour when the shortest path is behind the HUD."""
    from conquest.scene_input import clear_route_point
    remaining=lambda p:abs(p[0]-goal[0])+abs(p[1]-goal[1])
    candidates=[]
    for dx,dy in ((1,0),(-1,0),(0,1),(0,-1)):
        for distance in range(1,13):
            point=(source[0]+dx*distance,source[1]+dy*distance)
            if point in avoid or not terrain.walkable(point):break
            px=anchor[0]+(dx-dy)*distance*32
            py=anchor[1]+(dx+dy)*distance*16
            if clear_route_point((px,py),bounds) and (allow_detour or remaining(point)<remaining(source)):
                candidates.append(point)
    return min(candidates,key=remaining) if candidates else None


def client_file(root, relative):
    root=Path(root).resolve()
    result=(root/relative.replace('\\','/')).resolve()
    if not result.is_relative_to(root):
        raise ValueError('Map resource leaves the client directory')
    return result


def read_terrain(client_root, map_id):
    from conquest.character_context import installation_path
    client_root=installation_path(client_root)
    records=json.loads(client_file(client_root,'ini/GameMap.json').read_text(encoding='utf-8'))
    matches=[r for r in records if r['DocumentId']==map_id]
    if len(matches)!=1:
        raise ValueError('Map ID must resolve to one installed map')
    data=client_file(client_root,matches[0]['FileName']).read_bytes()
    if len(data)<276 or struct.unpack_from('<I',data)[0] not in (1003,1004):
        raise ValueError('Only DMap versions 1003 and 1004 are supported')
    width,height=struct.unpack_from('<II',data,268)
    if not 1<=width<=2048 or not 1<=height<=2048:
        raise ValueError('Invalid map dimensions')
    end=276+height*(width*6+4)
    if end+4>len(data):
        raise ValueError('Truncated terrain grid')
    cells=np.ndarray((height,width),dtype=np.dtype([('access','<u2'),('surface','<u2'),('elevation','<i2')]),
                     buffer=data,offset=276,strides=(width*6+4,6))
    blocked=(cells['access']!=0).copy()
    offset=end
    def take(size):
        nonlocal offset
        if size<0 or offset+size>len(data):
            raise ValueError('Truncated map object data')
        value=data[offset:offset+size]
        offset+=size
        return value
    def number():
        return struct.unpack('<I',take(4))[0]
    count=number()
    if count>4096:
        raise ValueError('Invalid portal count')
    portals=[]
    for _ in range(count):
        x,y,portal_id=struct.unpack('<III',take(12))
        if not 0<=x<width or not 0<=y<height:
            raise ValueError('Portal lies outside the map')
        portals.append((x,y,portal_id))
        blocked[max(0,y-1):min(height,y+2),max(0,x-1):min(width,x+2)]=True
    count=number()
    if count>100000:
        raise ValueError('Invalid map object count')
    scenes=[]
    object_sizes={4:416,8:260,10:72,15:276,19:96,24:424,27:272}
    for _ in range(count):
        kind=number()
        if kind==1:
            name=take(260).split(b'\0',1)[0].decode('ascii')
            x,y=struct.unpack('<II',take(8))
            scene=client_file(client_root,name).read_bytes()
            if len(scene)<4:
                raise ValueError('Truncated terrain scene')
            parts=struct.unpack_from('<I',scene)[0]
            if parts>4096:
                raise ValueError('Invalid terrain scene part count')
            cursor=4
            for _ in range(parts):
                if cursor+356>len(scene):
                    raise ValueError('Truncated terrain scene part')
                sw,sh=struct.unpack_from('<II',scene,cursor+332)
                dx,dy=struct.unpack_from('<ii',scene,cursor+344)
                if sw>2048 or sh>2048 or max(abs(dx),abs(dy))>2048:
                    raise ValueError('Invalid terrain scene bounds')
                cursor+=356
                end_cells=cursor+sw*sh*12
                if end_cells>len(scene):
                    raise ValueError('Truncated terrain scene cells')
                # ScenePart.Size extends backwards from its bottom-right TileOffset.
                # Format: Tiled2Dmap SceneFile / DmapFileRender.SceneDrawOrder.
                left,top=x+dx-sw+1,y+dy-sh+1
                if left<0 or top<0 or left+sw>width or top+sh>height:
                    raise ValueError('Terrain scene cells leave the map')
                access=np.ndarray((sh,sw),dtype='<u4',buffer=scene,offset=cursor,
                                  strides=(sw*12,12))
                blocked[top:top+sh,left:left+sw]=access!=0
                cursor=end_cells
        elif kind in object_sizes:
            take(object_sizes[kind])
        else:
            raise ValueError(f'Unsupported map object type {kind}')
    # Scene overlays cannot reopen guarded portal tiles.
    for x,y,_ in portals:
        blocked[max(0,y-1):min(height,y+2),max(0,x-1):min(width,x+2)]=True
    # Additional decorative layers do not change the base collision grid.
    return TerrainMap(map_id,width,height,blocked,hashlib.sha256(data).hexdigest(),tuple(portals),tuple(scenes))


def path_boundary(path, map_size, padding=12):
    """A bounded travel area containing the entire terrain-validated path."""
    if not path or padding < 0:
        raise ValueError('A nonempty path and nonnegative padding are required')
    width,height=map_size
    if any(not (0<=x<width and 0<=y<height) for x,y in path):
        raise ValueError('Travel path leaves the map')
    xs,ys=zip(*path)
    return (max(0,min(xs)-padding),max(0,min(ys)-padding),
            min(width-1,max(xs)+padding),min(height-1,max(ys)+padding))


def plan_hunting_return(terrain, position, anchor, hunting_boundary):
    """Recover a same-map excursion by following a checked path to the saved spot."""
    left,top,right,bottom=hunting_boundary
    if not (left<=anchor[0]<=right and top<=anchor[1]<=bottom):
        raise ValueError('Saved hunting spot is outside the hunting boundary')
    path=terrain.travel_path(tuple(position),tuple(anchor))
    points=[];index=0
    while index<len(path)-1:
        target=travel_waypoint(terrain,path[index:]);points.append(target)
        # Replan shortened projected landings that are not exactly on the path.
        if target not in path[index:]:
            path=path[:index+1]+terrain.travel_path(target,tuple(anchor));index+=1
        else:index=path.index(target,index+1)
    return tuple(points),path_boundary(path,(terrain.width,terrain.height))
