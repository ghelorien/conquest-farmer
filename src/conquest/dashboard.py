"""Small read-only, localhost progress dashboard over the bot's SQLite log."""
import json
import sqlite3
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


def status(database):
    result = {"state": "Waiting for run", "attacks": 0, "heals": 0, "kills": 0,
              "pickups": 0, "reloads": 0, "character_level": None,
              "deaths": 0, "revivals": 0,
              "elapsed": 0, "health": None, "health_valid": False,
              "health_note": "Waiting for health monitor", "ammo": None, "potions": None, "events": []}
    database = Path(database).resolve()
    if not database.is_file():
        return result
    try:
        with sqlite3.connect(database.as_uri() + "?mode=ro", uri=True, timeout=.2) as db:
            first = db.execute("SELECT rowid,time FROM events WHERE event='trial_started' ORDER BY rowid DESC LIMIT 1").fetchone()
            if not first:
                return result
            rows = db.execute("SELECT time,event,payload FROM events WHERE rowid>=? ORDER BY rowid", (first[0],)).fetchall()
        result["state"] = "Running"
        ended = None
        health_time = None
        for timestamp, name, encoded in rows:
            data = json.loads(encoded)
            if name == "trial_started":
                result["monster"] = data.get("monster", "Pheasant")
            if name == "attack_attempt":
                result["attacks"] += 1
                result["state"] = "Attacking"
            if name == "healing_outcome" and data.get("outcome") == "verified":
                result["heals"] += 1
            if name == "healing_attempt":
                result["state"] = "Healing"
            if name == "movement_attempt":
                result["state"] = "Patrolling"
            if name == "death_detected":
                result["deaths"] = data["deaths"]
                result["state"] = "Waiting to revive"
            if name == "revive_calibration_required":
                result["state"] = "Waiting for Revive-button calibration"
            if name == "revival_verified":
                result["revivals"] = data["total"]
            if name == "recovery_state":
                result["state"] = data["state"].replace("_", " ").capitalize()
            if name == "recovery_complete":
                result["state"] = "Patrolling"
            if name == "reload_attempt":
                result["state"] = "Reloading arrows"
            if name == "reload_outcome" and data.get("outcome") == "verified":
                result["reloads"] += 1
            if name == "pickup_attempt":
                result["state"] = "Picking up Stancher"
            if name == "pickup_outcome" and data.get("outcome") == "verified":
                result["pickups"] = data["total"]
            if name == "paused":
                result["state"] = "Paused"
            if name == "resumed":
                result["state"] = "Running"
            if "health_ratio" in data:
                result["health"] = round(100 * data["health_ratio"], 1)
                health_time = timestamp
            if name == "kill_verified":
                result["kills"] = data["total"]
            for key in ("ammo", "potions", "position", "occupied_slots", "character_level"):
                if key in data:
                    result[key] = data[key]
            if name == "trial_stopped":
                result["state"] = "Stopped: " + data["reason"].replace("_", " ")
                ended = timestamp
                result["kills"] = data.get("confirmed_kills")
            if name not in ("observation", "health_observation"):
                result["events"].append({"time": timestamp, "event": name.replace("_", " "), "data": data})
        result["elapsed"] = round((ended or time.time()) - first[1])
        if ended is None and result["state"] != "Paused" and time.time() - rows[-1][0] > 10:
            result["state"] = "No recent updates"
        result["health_valid"] = health_time is not None and 0 <= time.time() - health_time <= 1
        if not result["health_valid"]:
            result["health"] = None
            result["health_note"] = "No fresh health observation"
        else:
            result["health_note"] = "Live · potion below 40%"
        result["events"] = result["events"][-12:][::-1]
    except (sqlite3.Error, ValueError, KeyError) as error:
        result["state"] = "Waiting for log: " + str(error)
    return result


PAGE = """<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Archer · Live progress</title><style>
body{margin:0;background:#11151b;color:#ebeff5;font:15px system-ui;padding:28px;max-width:850px;margin:auto}h1{font-size:26px;margin:0 0 6px}.sub{color:#9caabd;margin-bottom:24px}.status{color:#82d9aa;font-weight:600;margin-bottom:18px}.grid{display:grid;grid-template-columns:repeat(3,1fr);gap:12px}.card{background:#1c232d;border:1px solid #303c4c;border-radius:12px;padding:18px}.label{color:#a6b3c4;font-size:13px}.value{font-size:28px;font-weight:650;margin-top:5px}.bar{height:9px;background:#323d4b;border-radius:9px;overflow:hidden;margin-top:14px}.bar span{display:block;background:#76d39d;height:100%;transition:width .3s}h2{font-size:16px;margin-top:28px}.event{padding:9px 0;border-bottom:1px solid #283342;color:#c7d1df}time{color:#7c8ea4;font-size:12px;margin-right:10px}.note{color:#8d9eb3;font-size:12px;margin-top:24px}@media(max-width:500px){body{padding:16px}.grid{grid-template-columns:repeat(2,1fr)}}
</style><h1>Archer farming</h1><div class="sub">Parasite · <span id="stage">Live session</span></div><div class="status" id="state">Waiting for run</div>
<div class="grid"><div class="card"><div class="label">Health</div><div class="value" id="health">—</div><div class="bar"><span id="bar"></span></div><div class="label" id="health-note" style="margin-top:10px">Connecting...</div></div><div class="card"><div class="label">Arrows equipped</div><div class="value" id="ammo">—</div></div><div class="card"><div class="label">Potions</div><div class="value" id="potions">—</div></div><div class="card"><div class="label">Verified kills</div><div class="value" id="kills">0</div></div><div class="card"><div class="label">Verified heals</div><div class="value" id="heals">0</div></div><div class="card"><div class="label">Session time</div><div class="value" id="elapsed">0:00</div></div></div>
<div class="note" id="milestones"></div><h2>Level milestones</h2><div id="progression"></div><h2>Recent activity</h2><div id="events"></div><div class="note">F11 pauses · F12 stops · Foreground play requires game focus.<br>F1 potion below 40% health. Health tracking continues while farming is paused; keep the game visible. Inventory and arrows use read-only memory; health uses bar fill, without OCR.</div>
<script>const $=id=>document.getElementById(id);async function refresh(){try{const d=await(await fetch('/status',{cache:'no-store'})).json();$('state').textContent=d.state;$('stage').textContent=(d.monster??'')+' stage';$('progression').replaceChildren(...[...(d.completed_upgrades??[]).map(t=>'Done: '+t),...(d.due_reviews??[]).map(r=>'Review due: '+r.title),...(d.next_reviews??[]).map(r=>'Level '+r.level+': '+r.title)].map(t=>{let n=document.createElement('div');n.className='event';n.textContent=t;return n}));$('milestones').textContent='Level '+(d.character_level??'—')+' · Verified pickups: '+d.pickups+' · Arrow reloads: '+d.reloads;for(const k of ['ammo','potions','kills','heals'])$(k).textContent=d[k]??'—';$('health').textContent=d.health==null?'—':d.health+'%';$('bar').style.width=(d.health??0)+'%';$('bar').style.background=d.health<40?'#ef7777':'#76d39d';$('health-note').textContent=d.health_note;$('elapsed').textContent=Math.floor(d.elapsed/60)+':'+String(d.elapsed%60).padStart(2,'0');$('events').replaceChildren(...d.events.map(e=>{let n=document.createElement('div');n.className='event';let t=document.createElement('time');t.textContent=new Date(e.time*1000).toLocaleTimeString();n.append(t,document.createTextNode(e.event+(e.data.reason?' · '+e.data.reason.replaceAll('_',' '):'')));return n}));}catch{$('state').textContent='Dashboard disconnected'}}refresh();setInterval(refresh,250)</script>"""


def serve_dashboard(database, port=8765, *, monitor=None):
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path not in ("/", "/status"):
                self.send_error(404)
                return
            if self.path == "/":
                content = PAGE.encode()
            else:
                data = status(database)
                if monitor is not None:
                    data.update(monitor.snapshot())
                content = json.dumps(data).encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8" if self.path == "/" else "application/json")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(content)))
            self.end_headers()
            self.wfile.write(content)
        def log_message(self, *args):
            pass
    with ThreadingHTTPServer(("127.0.0.1", port), Handler) as server:
        if monitor is not None:
            monitor.start()
        try:
            server.serve_forever()
        finally:
            if monitor is not None:
                monitor.close()
