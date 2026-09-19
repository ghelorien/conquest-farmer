"""Pure presentation helpers for the manual-visitor operator surface.

Authority stays in ``ManualRuntime``.  These helpers deliberately accept and
return JSON data only; they never infer a visitor, target, binding or recovery
operation from presentation text.
"""
from copy import deepcopy
import json
import math
import time


def displayed(row):
    """Detach exactly the JSON view shown to the operator."""
    return deepcopy(row) if row is not None else None


def exact_binding_text(row):
    binding=(row or {}).get('approval_binding')
    return json.dumps(binding,sort_keys=True,indent=2) if binding is not None else ''


def decision_seconds(row, *, now=None):
    deadline=(row or {}).get('deadline')
    if type(deadline) not in (int,float):return None
    now=time.time() if now is None else now
    return max(0,math.ceil(deadline-now))


def visitor_text(visitor):
    if not isinstance(visitor,dict):return 'visitor identity unavailable'
    return (f"{visitor.get('visitor_name','?')} · {visitor.get('visitor_server','?')} · "
            f"UID {visitor.get('visitor_uid','?')}")


def status_text(target, row, *, farmer_status=None, intent=None, now=None):
    """Compact status that keeps the fence and saved intent visibly distinct."""
    intent=intent or {}
    if target=='Farmer':
        saved='On' if intent.get('enabled') else 'Off'
        if intent.get('paused'):saved+=' (paused)'
        intent_line=f'Saved farming intent: {saved}; manual approval does not change it.'
    else:
        intent_line=(f"Saved permissions: trading/repricing {'On' if intent.get('enabled') else 'Off'}, "
                     f"refill {'On' if intent.get('refill_enabled') else 'Off'}; manual approval does not change them.")
    if row is None:
        observation=(farmer_status or {}).get('observation') or {}
        blocker=observation.get('reason') or observation.get('decline_blocker')
        suffix=f'\nObservation blocker: {blocker}' if blocker else ''
        return f'No manual visitor session.\n{intent_line}{suffix}'
    retracted=(row.get('terminal') or {}).get('disposition')=='manual_admission_retracted_bot_owned'
    phase='local manual admission retracted; game request still visible' if retracted else str(row.get('phase') or 'unknown').replace('_',' ')
    scope='released' if retracted else row.get('fence_scope') or 'target'
    lines=[f"{phase.title()} · {visitor_text(row.get('visitor'))}",
           f"Input fence: {scope} · session {row.get('id','?')}", intent_line]
    if row.get('phase')=='approval_pending' and row.get('request_state')=='pending':
        seconds=decision_seconds(row,now=now)
        lines.append(('Approval decision expires now' if seconds==0 else
                      f'Approve or reject within {seconds} second(s)') if seconds is not None else
                     'Approval deadline unavailable')
        binding=row.get('approval_binding') or {}
        lines.append(f"Exact request: {binding.get('request_id','unavailable')}")
    if row.get('rebaseline'):
        lines.append('Post-override rebaseline: two fresh matching closed-window observations are required.')
    if row.get('reason'):lines.append('Blocker: '+str(row['reason']))
    if target=='Farmer':
        observation=(farmer_status or {}).get('observation') or {}
        maps=observation.get('qualified_full_snapshot_maps')
        if maps:lines.append('Qualified full-session maps: '+', '.join(map(str,maps)))
        for key,label in (('reason','Observation blocker'),('decline_blocker','Decline blocker')):
            if observation.get(key):lines.append(label+': '+str(observation[key]))
    return '\n'.join(lines)


def action_state(row, *, now=None):
    pending=bool(row and row.get('phase')=='approval_pending'
                 and row.get('request_state')=='pending' and row.get('approval_binding'))
    seconds=decision_seconds(row,now=now) if pending else None
    if seconds==0:pending=False
    attention=bool(row and (row.get('phase')=='needs_attention' or row.get('rebaseline')))
    return {'approve':pending,'reject':pending,'override':attention}
