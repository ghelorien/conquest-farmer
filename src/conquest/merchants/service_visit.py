"""One durable merchant-service budget for an entire required Market visit."""
from pathlib import Path
import time
import uuid
from conquest.character_context import state_path, farmer_name, current
from conquest.discord_notify import read_json, write_json

MARKET_SECONDS=60


def farmer_id():
    context=current()
    return context.profile.id if context else farmer_name()


def parent_visit():
    from conquest.town_visit import TownVisit
    required=TownVisit().active_id()
    if required:return required
    candidates=[]
    for name in ('merchant-journey','meteor-consolidation','overflow'):
        row=read_json(state_path(f'reports/banking/{name}.json'))
        if row.get('started_at') and row.get('phase') not in (None,'completed','complete','failed'):
            candidates.append((row['started_at'],name))
    return ':'.join(map(str,max(candidates))) if candidates else None


def validate_grant(ui,body):
    """The bridge verifies its own durable visit and current client memory."""
    row=read_json(MarketVisit().path)
    if (row.get('phase')!='active' or row.get('visit_id')!=body.get('visit_id')
            or row.get('farmer_profile_id')!=farmer_id()
            or body.get('expires_at')!=row.get('deadline')
            or not time.time()<row['deadline']<=row['started_at']+MARKET_SECONDS):
        raise ValueError('Market grant must use the original current visit deadline')
    from conquest.merchants.memory import MerchantMemory
    observer=ui.app.observer
    if observer is None or observer.character!=farmer_name():
        raise ValueError('Market visit farmer is not attached')
    with observer.lock:source=MerchantMemory(observer).read()
    if (source.get('map_id')!=1036 or source.get('hp',0)<=0
            or not 0<=time.time()-source.get('timestamp',0)<=2):
        raise ValueError('Market grant needs a fresh living farmer in Market')
    return row


class MarketVisit:
    def __init__(self,path=None,*,clock=time.time):
        self.path=Path(path or state_path('reports/banking/merchant-service-visit.json'))
        self.clock=clock

    def begin(self,*,parent=None,profile=None):
        now=self.clock();profile=profile or farmer_id();old=read_json(self.path)
        if old.get('phase')=='active':
            if old.get('farmer_profile_id')!=profile:
                raise ValueError('Market visit belongs to another farmer profile')
            if parent is not None and old.get('parent_visit_id') not in (None,parent):
                raise ValueError('Previous Market visit needs departure reconciliation')
            return old
        row={'version':1,'visit_id':uuid.uuid4().hex,'farmer_profile_id':profile,
             'parent_visit_id':parent,'phase':'active','started_at':now,
             'deadline':now+MARKET_SECONDS,'attempts':[]}
        from conquest.town_visit import TownVisit
        required=TownVisit().active_id()
        if required and required==parent:row['town_visit_id']=required
        write_json(self.path,row)
        return row

    def departed(self,map_id):
        if map_id==1036:return
        row=read_json(self.path)
        if row.get('phase')=='active':
            row.update(phase='departed',departed_at=self.clock(),arrival_map=map_id)
            write_json(self.path,row)

    def attempt(self,merchant,position,outcome):
        row=read_json(self.path)
        if row.get('phase')!='active':raise ValueError('No active Market visit')
        row.setdefault('attempts',[]).append({'merchant':merchant,'position':list(position),
                                            'outcome':outcome,'at':self.clock()})
        write_json(self.path,row)
