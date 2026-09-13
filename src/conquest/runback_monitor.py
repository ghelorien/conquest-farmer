"""Per-trip progress and survival measurements from fresh memory observations."""
from conquest.character_context import state_path
import json
import math
import time
from pathlib import Path

OUTPUT=Path(state_path('reports/runbacks'))

class RunbackMonitor:
    def __init__(self, destination, map_id, kind, notify=lambda *args:None, *,
                 clock=time.monotonic, wall=time.time, output=None):
        self.clock,self.wall,self.output,self.notify=clock,wall,Path(output or OUTPUT),notify
        self.destination=tuple(destination);self.map_id=map_id;self.kind=kind
        self.started=clock();self.started_at=wall();self.previous=None
        self.last_move=self.started;self.last_damage=-float('inf');self.last_publish=-float('inf')
        self.distance=self.active_seconds=self.paused_seconds=self.damage=0
        self.stalls=self.recoveries=self.deaths=0;self.was_stalled=False;self.was_dead=False
        self.minimum_hp=1.;self.urgent=False;self.finished=False;self.position=None
        self.paused=True;self.last_sample=None;self.io_error=None

    def observe(self, life, *, paused=False):
        if self.finished or not life or not all(k in life for k in ('map_id','position','current_hp','max_hp')):return
        stamp=life.get('timestamp')
        if stamp is not None and stamp==self.last_sample:return
        self.last_sample=stamp;now=self.clock()
        if life['map_id']!=self.map_id:
            self.finish('map_changed');return
        point=tuple(life['position']);hp=life['current_hp'];maximum=life['max_hp']
        dead=bool(life.get('dead_candidate') or hp<=0)
        if dead and not self.was_dead:self.deaths+=1
        self.was_dead=dead;self.minimum_hp=min(self.minimum_hp,hp/max(1,maximum))
        if self.previous:
            last,old_hp,then=self.previous;elapsed=max(0,now-then)
            # Observation gaps and user/focus pauses do not create fake stalls.
            interrupted=paused or self.paused or elapsed>2
            if interrupted:self.paused_seconds+=elapsed;self.last_move=now
            else:self.active_seconds+=elapsed
            if point!=last:
                if not interrupted:self.distance+=max(abs(a-b) for a,b in zip(point,last))
                self.last_move=now
            if hp<old_hp:
                self.damage+=old_hp-hp;self.last_damage=now
        else:self.last_move=now
        self.previous=(point,hp,now);self.position=point;self.paused=paused
        stationary=now-self.last_move
        self.urgent=not(paused or dead) and now-self.last_damage<=2 and stationary>=.5
        stalled=not(paused or dead) and stationary>=1
        if stalled and not self.was_stalled:self.stalls+=1
        self.was_stalled=stalled
        if now-self.last_publish>=2:self.publish('runback_progress')

    def observe_health(self, health):
        data=health.get('embedded_controls',{});life=data.get('life')
        fresh=0<=self.wall()-data.get('observed_at',0)<=1
        window=health.get('window',{})
        if fresh:
            self.observe(life,paused=bool(data.get('manual_mouse') or window.get('minimized')
                or window.get('foreground')!=window.get('root_hwnd')))

    def recovery(self):
        self.recoveries+=1

    def snapshot(self, result='travelling'):
        elapsed=max(0,self.clock()-self.started)
        return {'kind':self.kind,'map_id':self.map_id,'destination':self.destination,
                'position':self.position,'started_at':self.started_at,'updated_at':self.wall(),
                'result':result,'elapsed_seconds':round(elapsed,1),
                'active_seconds':round(self.active_seconds,1),'paused_seconds':round(self.paused_seconds,1),
                'tiles_travelled':self.distance,'tiles_per_second':round(self.distance/max(.01,self.active_seconds),2),
                'stalls':self.stalls,'recoveries':self.recoveries,'hp_lost':self.damage,
                'minimum_hp_percent':round(100*self.minimum_hp,1),'deaths':self.deaths,
                'urgent':self.urgent,'paused':self.paused,'source':'read_only_memory'}

    def publish(self, event, result='travelling'):
        row=self.snapshot(result);self.last_publish=self.clock()
        try:
            self.output.mkdir(parents=True,exist_ok=True)
            path=self.output/(self.kind+'.json');temporary=path.with_suffix('.tmp')
            temporary.write_text(json.dumps(row),encoding='utf-8');temporary.replace(path)
            if event=='runback_finished':
                with (self.output/'history.jsonl').open('a',encoding='utf-8') as out:
                    out.write(json.dumps(row)+'\n')
        except OSError as error:self.io_error=type(error).__name__
        # Telemetry must never interrupt healing or movement.
        try:self.notify(event,row)
        except OSError:pass

    def finish(self, result):
        if self.finished:return
        self.finished=True;self.urgent=False;self.publish('runback_finished',result)


def escape_step(terrain, source, destination, anchor, monsters, *, avoid=(),viewport=(1036,793)):
    """Pick a clear visible escape using only current living-monster positions."""
    threats=[tuple(m['position']) for m in monsters if m.get('alive') is not False
             and m.get('current_hp',1)!=0 and m.get('position')]
    if not threats:return None
    nearest=lambda p:min(max(abs(a-b) for a,b in zip(p,m)) for m in threats)
    danger=lambda p:sum(max(0,5-max(abs(a-b) for a,b in zip(p,m))) for m in threats)
    candidates=[]
    for dx,dy in ((1,0),(-1,0),(0,1),(0,-1)):
        for step in range(1,13):
            point=(source[0]+dx*step,source[1]+dy*step)
            if point in avoid or not terrain.walkable(point):break
            if step not in (4,8,9,10,11,12):continue
            x=anchor[0]+(dx-dy)*step*32;y=anchor[1]+(dx+dy)*step*16
            from conquest.viewport import clear_scene
            if not clear_scene((x,y),viewport):continue
            if danger(point)>=danger(source) and nearest(point)<=nearest(source):continue
            candidates.append((danger(point),-min(12,nearest(point)),math.dist(point,destination),-step,point))
    return min(candidates)[-1] if candidates else None
