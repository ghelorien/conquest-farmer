"""One-shot retrieval of reviewed items for controlled Market qualification.

This is deliberately separate from ordinary warehouse and Meteor withdrawal.
The authenticated town bridge supplies only a plan ID, operation ID and UID;
all authority and exact item evidence come from a fixed local plan and its
immutable deposit receipts.  Once an input boundary is journaled, the operation
can only reconcile memory and can never issue another click.
"""
from contextlib import contextmanager
from dataclasses import asdict, is_dataclass
from pathlib import Path
import hashlib
import json
import math
import sqlite3
import time

from conquest.character_context import state_path
from conquest.discord_notify import read_json


PLAN = Path(state_path('reports/banking/protected-withdrawal-qualification.json'))
JOURNAL = Path(state_path('reports/banking/protected-withdrawals.sqlite3'))
RECEIPT_ROOT = Path(state_path('reports/banking/controlled-warehouse-deposits'))
CORE = ('uid','type_id','plus','gem1','gem2','quantity','bound')
TERMINAL = ('withdrawn','no_transfer','operator_overridden')
ACTIVE = ('prepared','input_maybe_sent','reconciling','blocked')
MAX_INPUT_SECONDS = 5


def pending(path=None):
    """Read active asset locks without creating or repairing the journal."""
    path=Path(path or JOURNAL)
    if not path.exists():return []
    try:
        uri=path.resolve().as_uri()+'?mode=ro'
        db=sqlite3.connect(uri,uri=True,timeout=1)
        try:
            db.row_factory=sqlite3.Row
            rows=db.execute("SELECT operation_id,plan_id,uid,phase,state FROM operations "
                            "WHERE phase IN ('prepared','input_maybe_sent','reconciling','blocked') "
                            "ORDER BY updated_at,operation_id").fetchall()
        finally:db.close()
        result=[]
        for row in rows:
            state=json.loads(row['state'])
            if (not isinstance(state,dict) or state.get('operation_id')!=row['operation_id']
                    or state.get('plan_id')!=row['plan_id'] or state.get('uid')!=row['uid']
                    or state.get('phase')!=row['phase']):
                raise ValueError('Protected withdrawal journal row disagrees with its state')
            result.append(state)
        return result
    except Exception:
        # Callers need a durable stop signal, never a missing-state inference.
        return [{'operation_id':'unreadable','plan_id':None,'uid':None,'phase':'blocked',
                 'error':'Protected withdrawal journal is unreadable; reconcile before input'}]


def canonical_artifact_digest(value, field):
    """Digest an immutable JSON artifact without its self-digest field."""
    if not isinstance(value,dict) or not isinstance(field,str) or field not in value:
        raise ValueError('Immutable artifact lacks its self-digest')
    body={key:item for key,item in value.items() if key!=field}
    return hashlib.sha256(json.dumps(body,sort_keys=True,separators=(',',':')).encode()).hexdigest()


def _digest(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':')).encode()).hexdigest()


def _number(value, name):
    if type(value) not in (int,float) or not math.isfinite(value):
        raise ValueError(f'{name} must be a finite timestamp')
    return value


def _identifier(value, name):
    if not isinstance(value,str) or not 1<=len(value)<=128:
        raise ValueError(f'Invalid {name}')
    return value


def core(item):
    if not isinstance(item,dict) or set(item)!=set(CORE):
        raise ValueError('Protected item must contain exactly the seven ownership fields')
    result={key:item[key] for key in CORE}
    if (type(result['uid']) is not int or result['uid']<=0
            or type(result['type_id']) is not int or result['type_id']<=0
            or type(result['plus']) is not int or not 0<=result['plus']<=12
            or type(result['gem1']) is not int or result['gem1']<0
            or type(result['gem2']) is not int or result['gem2']<0
            or type(result['quantity']) is not int or result['quantity']<=0
            or type(result['bound']) is not bool):
        raise ValueError('Protected ownership fields are invalid')
    return result


def _selected_core(item):
    result=core(item)
    if not 100000<=result['type_id']<600000 or result['quantity']!=1 or result['bound'] is not False:
        raise ValueError('Reviewed withdrawal item must be one unbound equipment item')
    return result


def _core_from(value):
    if is_dataclass(value):value=asdict(value)
    return core({key:value.get(key) for key in CORE})


def _core_map(items):
    values=[_core_from(item) for item in items]
    if len({item['uid'] for item in values})!=len(values):
        raise ValueError('Duplicate item UID in protected ownership evidence')
    return {item['uid']:item for item in values}


def _ammo(value):
    if value is None:return None
    if is_dataclass(value):value=asdict(value)
    fields=('uid','type_id','amount','limit','plus')
    result={key:value.get(key) for key in fields}
    if (type(result['uid']) is not int or result['uid']<=0
            or type(result['type_id']) is not int or result['type_id']<=0
            or type(result['amount']) is not int or type(result['limit']) is not int
            or not 0<=result['amount']<=result['limit']
            or result['plus'] is not None and type(result['plus']) is not int):
        raise ValueError('Invalid equipped ammunition evidence')
    return result


def _source(value):
    required=('character','character_uid','identity','server','map_id','position','hp','silver','inventory','booth','trade','request')
    if not isinstance(value,dict) or any(name not in value for name in required):
        raise ValueError('Farmer ownership snapshot is incomplete')
    return {'character':value['character'],'character_uid':value['character_uid'],
            'identity':value['identity'],'server':value['server'],'map_id':value['map_id'],
            'position':list(value['position']),'hp':value['hp'],'silver':value['silver'],
            'inventory':_core_map(value['inventory']),'booth':_core_map(value['booth']),
            'trade':value['trade'],'request':value['request']}


def _read_artifact(reference, root, digest_name):
    if (not isinstance(reference,dict) or set(reference)!= {'path','sha256'}
            or not isinstance(reference['path'],str)
            or not isinstance(reference['sha256'],str) or len(reference['sha256'])!=64):
        raise ValueError('Invalid immutable receipt reference')
    candidate=Path(reference['path'])
    if candidate.is_absolute():
        raise ValueError('Immutable receipt references must be relative to the data root')
    root=root.resolve();path=(Path.cwd()/candidate).resolve()
    try:path.relative_to(root)
    except ValueError as error:raise ValueError('Immutable receipt path escapes the protected receipt directory') from error
    if not path.is_file() or path.stat().st_size>2_000_000:
        raise ValueError('Immutable receipt is missing or too large')
    value=json.loads(path.read_text(encoding='utf-8'))
    actual=canonical_artifact_digest(value,digest_name)
    if actual!=reference['sha256'] or value.get(digest_name)!=actual:
        raise ValueError('Immutable receipt digest changed')
    return value


def _warehouse_added(before, after, item):
    old=_core_map(before.get('items',()));new=_core_map(after.get('items',()))
    if item['uid'] in old or set(new)!=set(old)|{item['uid']}:
        return False
    return ({uid:value for uid,value in new.items() if uid!=item['uid']}==old
            and new[item['uid']]==item and before.get('capacity')==after.get('capacity'))


def _validate_deposit_receipts(plan):
    previous=None
    for sequence,entry in enumerate(plan['items'],1):
        receipt=_read_artifact(entry['deposit_receipt'],RECEIPT_ROOT,'receipt_sha256')
        required={'version','kind','deposit_operation_id','sequence','attempt','verified_at',
                  'release','farmer','market_visit','item','before','native_receipt','after',
                  'conservation','previous_receipt_sha256','receipt_sha256'}
        if set(receipt)!=required or receipt['version']!=1 or receipt['kind']!='controlled_warehouse_deposit':
            raise ValueError('Controlled deposit receipt schema changed')
        if (receipt['deposit_operation_id']!=plan['deposit_operation_id']
                or receipt['sequence']!=sequence or receipt['previous_receipt_sha256']!=previous
                or receipt['release']!=plan['release'] or receipt['farmer']!=plan['farmer']
                or core(receipt['item'])!=entry['item']
                or not isinstance(receipt['market_visit'],dict)
                or receipt['market_visit'].get('phase')!='active'
                or any(receipt['market_visit'].get(name)!=plan['market_departure']['market_visit'].get(name)
                       for name in ('visit_id','farmer_profile_id','parent_visit_id','town_visit_id'))):
            raise ValueError('Controlled deposit receipt does not belong to this plan')
        attempt=_read_artifact(receipt['attempt'],RECEIPT_ROOT,'attempt_sha256')
        attempt_required={'version','kind','deposit_operation_id','sequence','created_at','release',
                          'farmer','market_visit','item','before','previous_receipt_sha256','attempt_sha256'}
        if (set(attempt)!=attempt_required or attempt['version']!=1
                or attempt['kind']!='controlled_warehouse_deposit_attempt'
                or attempt['deposit_operation_id']!=plan['deposit_operation_id']
                or attempt['sequence']!=sequence or attempt['previous_receipt_sha256']!=previous
                or attempt['release']!=plan['release'] or attempt['farmer']!=plan['farmer']
                or attempt['market_visit']!=receipt['market_visit']
                or attempt['before']!=receipt['before'] or core(attempt['item'])!=entry['item']
                or receipt['verified_at']<attempt['created_at']):
            raise ValueError('Controlled deposit attempt does not bind the final receipt')
        before,after=receipt['before'],receipt['after'];item=entry['item']
        bsource,asource=_source(before['farmer']),_source(after['farmer'])
        expected=dict(bsource['inventory']);expected.pop(item['uid'],None)
        native=receipt['native_receipt'];conservation=receipt['conservation']
        flags=('inventory_unchanged_except_uid','silver_unchanged','equipped_ammo_unchanged',
               'warehouse_added_exact_basic','essential_uids_preserved')
        stable=('character','character_uid','identity','server','map_id','position','hp','silver','booth','trade','request')
        if (bsource['inventory'].get(item['uid'])!=item or asource['inventory']!=expected
                or any(bsource[name]!=asource[name] for name in stable)
                or bsource['map_id']!=1036 or bsource['trade'] is not None or bsource['request'] is not None
                or bsource['character']!=plan['farmer']['character']
                or bsource['character_uid']!=plan['farmer']['character_uid']
                or bsource['identity']!=plan['farmer']['process_identity']
                or bsource['server']!=plan['farmer']['server']
                or _ammo(before.get('equipped_ammo'))!=_ammo(after.get('equipped_ammo'))
                or not _warehouse_added(before['warehouse'],after['warehouse'],item)
                or native!={'stored':item['uid'],'type_id':item['type_id'],'verified_in_warehouse':True}
                or conservation.get('removed_uid')!=item['uid']
                or any(conservation.get(name) is not True for name in flags)):
            raise ValueError('Controlled deposit receipt lacks exact conservation proof')
        previous=receipt['receipt_sha256']
    return previous


def load_plan(plan_id, operation_id, uid, *, path=None, current_visit=None):
    path=Path(path or PLAN)
    if not path.is_file() or path.stat().st_size>2_000_000:
        raise ValueError('Protected withdrawal qualification plan is unavailable')
    plan=json.loads(path.read_text(encoding='utf-8'))
    required={'version','kind','plan_id','created_at','release','farmer','deposit_operation_id',
              'market_departure','market_reentry','items','plan_sha256'}
    if set(plan)!=required or plan['version']!=1 or plan['kind']!='controlled_protected_withdrawal_plan':
        raise ValueError('Protected withdrawal plan schema changed')
    if canonical_artifact_digest(plan,'plan_sha256')!=plan['plan_sha256']:
        raise ValueError('Protected withdrawal plan digest changed')
    if plan['plan_id']!=_identifier(plan_id,'protected withdrawal plan ID'):
        raise ValueError('Protected withdrawal plan ID does not match')
    _identifier(plan['deposit_operation_id'],'deposit operation ID');_number(plan['created_at'],'plan creation')
    farmer=plan['farmer']
    if (not isinstance(farmer,dict) or set(farmer)!=
            {'character','character_uid','server','process_identity','client_sha256'}
            or not isinstance(farmer['character'],str) or not farmer['character']
            or type(farmer['character_uid']) is not int or farmer['character_uid']<=0
            or farmer['server']!='America' or not isinstance(farmer['process_identity'],dict)
            or not isinstance(farmer['client_sha256'],str) or len(farmer['client_sha256'])!=64):
        raise ValueError('Protected withdrawal farmer identity is invalid')
    if not isinstance(plan['items'],list) or len(plan['items'])!=2:
        raise ValueError('Protected withdrawal plan must contain exactly two reviewed items')
    seen_uids=set();seen_ops=set()
    for sequence,entry in enumerate(plan['items'],1):
        if not isinstance(entry,dict) or set(entry)!= {'item','withdrawal_operation_id','deposit_receipt'}:
            raise ValueError('Protected withdrawal plan item schema changed')
        entry['item']=_selected_core(entry['item']);op=_identifier(entry['withdrawal_operation_id'],'withdrawal operation ID')
        if entry['item']['uid'] in seen_uids or op in seen_ops:
            raise ValueError('Protected withdrawal plan repeats an item or operation')
        seen_uids.add(entry['item']['uid']);seen_ops.add(op)
    selected=next((entry for entry in plan['items'] if entry['item']['uid']==uid),None)
    if selected is None or selected['withdrawal_operation_id']!=_identifier(operation_id,'withdrawal operation ID'):
        raise ValueError('Withdrawal request is not fixed by the trusted plan')
    departure=plan['market_departure'];reentry=plan['market_reentry']
    if (not isinstance(departure,dict) or set(departure)!= {'market_visit','departed_observation'}
            or not isinstance(reentry,dict) or set(reentry)!= {'observed_at','snapshot','equipped_ammo','warehouse'}):
        raise ValueError('Protected withdrawal transit evidence is incomplete')
    visit=departure['market_visit'];departed=departure['departed_observation']
    if (not isinstance(visit,dict) or visit.get('phase')!='departed'
            or type(visit.get('departed_at')) not in (int,float) or visit.get('arrival_map')==1036
            or not isinstance(departed,dict) or departed.get('map_id')==1036
            or departed.get('identity')!=farmer['process_identity']
            or departed.get('character_uid')!=farmer['character_uid']
            or _number(departed.get('observed_at'),'departure observation')<visit['departed_at']):
        raise ValueError('Protected withdrawal lacks verified Market departure')
    rs=_source(reentry['snapshot'])
    if (rs['map_id']!=1036 or rs['identity']!=farmer['process_identity']
            or rs['character']!=farmer['character'] or rs['character_uid']!=farmer['character_uid']
            or rs['server']!='America' or rs['hp']<=0 or rs['trade'] is not None or rs['request'] is not None
            or _number(reentry['observed_at'],'Market reentry')<=departed['observed_at']):
        raise ValueError('Protected withdrawal lacks verified Market reentry')
    _ammo(reentry['equipped_ammo']);reentry_stash=_core_map(reentry['warehouse'].get('items',()))
    if type(reentry['warehouse'].get('capacity')) is not int:
        raise ValueError('Protected withdrawal reentry warehouse is invalid')
    if any(reentry_stash.get(entry['item']['uid'])!=entry['item'] for entry in plan['items']):
        raise ValueError('Protected withdrawal items are not exact in the reentry warehouse')
    if current_visit is not None and current_visit!=visit:
        raise ValueError('Original Market visit departure evidence changed')
    _validate_deposit_receipts(plan)
    return plan,selected


class ProtectedWithdrawalJournal:
    def __init__(self,path=None,*,clock=time.time):
        self.path=Path(path or JOURNAL);self.clock=clock
        self.path.parent.mkdir(parents=True,exist_ok=True)
        with self.db() as db:
            db.executescript('''
              CREATE TABLE IF NOT EXISTS operations(
                operation_id TEXT PRIMARY KEY, plan_id TEXT NOT NULL, uid INTEGER NOT NULL,
                phase TEXT NOT NULL, state TEXT NOT NULL, updated_at REAL NOT NULL);
              CREATE TABLE IF NOT EXISTS history(
                id INTEGER PRIMARY KEY AUTOINCREMENT, operation_id TEXT NOT NULL,
                phase TEXT NOT NULL, payload TEXT NOT NULL, timestamp REAL NOT NULL);
            ''')

    @contextmanager
    def db(self):
        db=sqlite3.connect(self.path,timeout=5)
        try:
            db.row_factory=sqlite3.Row;db.execute('PRAGMA busy_timeout=5000')
            yield db;db.commit()
        finally:db.close()

    def get(self,operation_id):
        with self.db() as db:
            row=db.execute('SELECT state FROM operations WHERE operation_id=?',(operation_id,)).fetchone()
        return json.loads(row['state']) if row else None

    def for_plan(self,plan_id):
        with self.db() as db:
            rows=db.execute('SELECT state FROM operations WHERE plan_id=? ORDER BY updated_at,operation_id',(plan_id,)).fetchall()
        return [json.loads(row['state']) for row in rows]

    def begin(self,state):
        encoded=json.dumps(state,sort_keys=True);now=self.clock()
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            row=db.execute('SELECT state FROM operations WHERE operation_id=?',(state['operation_id'],)).fetchone()
            if row:
                previous=json.loads(row['state'])
                if (previous['plan_id'],previous['uid'])!=(state['plan_id'],state['uid']):
                    raise ValueError('Protected withdrawal operation ID was reused')
                return previous
            active=db.execute("SELECT operation_id FROM operations WHERE phase IN ('prepared','input_maybe_sent','reconciling','blocked')").fetchone()
            if active:raise ValueError('Another protected withdrawal needs reconciliation')
            db.execute('INSERT INTO operations VALUES(?,?,?,?,?,?)',
                       (state['operation_id'],state['plan_id'],state['uid'],state['phase'],encoded,now))
            db.execute('INSERT INTO history(operation_id,phase,payload,timestamp) VALUES(?,?,?,?)',
                       (state['operation_id'],state['phase'],json.dumps({
                           'intent':state['intent'],'intent_digest':_digest(state['intent'])},sort_keys=True),now))
        return state

    def transition(self,operation_id,expected,phase,**changes):
        legal={'prepared':{'input_maybe_sent','no_transfer','blocked'},
               'input_maybe_sent':{'reconciling','withdrawn','blocked'},
               'reconciling':{'withdrawn','blocked'},'blocked':{'withdrawn','blocked'}}
        expected=(expected,) if isinstance(expected,str) else tuple(expected)
        now=self.clock()
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            row=db.execute('SELECT state FROM operations WHERE operation_id=?',(operation_id,)).fetchone()
            if not row:raise ValueError('Protected withdrawal operation is missing')
            state=json.loads(row['state']);current=state['phase']
            if current==phase and current in TERMINAL:return state
            if current not in expected or phase not in legal.get(current,set()):
                raise ValueError(f'Illegal protected withdrawal transition {current} to {phase}')
            state.update(changes,phase=phase,updated_at=now)
            db.execute('UPDATE operations SET phase=?,state=?,updated_at=? WHERE operation_id=? AND phase=?',
                       (phase,json.dumps(state,sort_keys=True),now,operation_id,current))
            if db.execute('SELECT changes()').fetchone()[0]!=1:
                raise ValueError('Protected withdrawal changed during transition')
            db.execute('INSERT INTO history(operation_id,phase,payload,timestamp) VALUES(?,?,?,?)',
                       (operation_id,phase,json.dumps(changes,sort_keys=True),now))
        return state

    def operator_override(self, operation_id, *, operator_confirmed=False,
                          confirmation_reference=None, operator=None,
                          fresh_evidence=None, incident_digest=None):
        if operator_confirmed is not True:
            raise ValueError('Operator confirmation is required for this incident')
        if not isinstance(confirmation_reference,str) or not confirmation_reference.strip():
            raise ValueError('A non-empty incident confirmation reference is required')
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            row=db.execute('SELECT state FROM operations WHERE operation_id=?',(operation_id,)).fetchone()
            if not row:raise ValueError('Protected withdrawal operation is missing')
            state=json.loads(row['state'])
            if state['phase']=='operator_overridden':
                prior=state.get('operator_override') or {}
                if prior.get('confirmation_reference')!=confirmation_reference.strip():
                    raise ValueError('Incident was already overridden with a different confirmation')
                return state
            if state['phase'] in TERMINAL:
                raise ValueError('A completed protected withdrawal cannot be overridden')
            original=json.loads(row['state'])
            digest=_digest(original)
            if incident_digest is not None and incident_digest != digest:
                raise ValueError('Incident evidence changed; recheck before overriding')
            override={'operator_confirmed':True,'confirmation_reference':confirmation_reference.strip(),
                      'operator':operator,'confirmed_at':self.clock(),
                      'original_phase':state['phase'],'original_evidence_digest':digest,
                      'original_evidence':original,
                      'fresh_evidence':fresh_evidence or {}}
            state.update(phase='operator_overridden',operator_override=override,
                         replan_required=True,updated_at=self.clock())
            encoded=json.dumps(state,sort_keys=True)
            db.execute('UPDATE operations SET phase=?,state=?,updated_at=? WHERE operation_id=? AND phase=?',
                       ('operator_overridden',encoded,state['updated_at'],operation_id,override['original_phase']))
            if db.execute('SELECT changes()').fetchone()[0]!=1:
                raise ValueError('Protected withdrawal changed during operator override')
            db.execute('INSERT INTO history(operation_id,phase,payload,timestamp) VALUES(?,?,?,?)',
                       (operation_id,'operator_overridden',json.dumps({'operator_override':override},sort_keys=True),state['updated_at']))
            return state

    def history(self,operation_id):
        with self.db() as db:
            return [dict(row) for row in db.execute(
                'SELECT id,operation_id,phase,payload,timestamp FROM history WHERE operation_id=? ORDER BY id',(operation_id,))]


def _plain_observation(value):
    return {'source':value['source'],'equipped_ammo':value['equipped_ammo'],
            'warehouse':value['warehouse'],'npc':value['npc'],'grid':value['grid']}


def _npc_identity(npc):
    """NPC identity needed for warehouse grid input; draw animation is not identity."""
    return {key:npc[key] for key in ('map_id','entity_id','object_address','name','type_id','position')}


def _same_ownership(one,two):
    a,b=_source(one['source']),_source(two['source'])
    return (a==b and _ammo(one['equipped_ammo'])==_ammo(two['equipped_ammo'])
            and _core_map(one['warehouse']['items'])==_core_map(two['warehouse']['items'])
            and one['warehouse']['capacity']==two['warehouse']['capacity'])


def _withdrawn(before,after,item):
    a,b=_source(before['source']),_source(after['source'])
    expected_bag=dict(a['inventory']);expected_bag[item['uid']]=item
    old_stash=_core_map(before['warehouse']['items']);new_stash=_core_map(after['warehouse']['items'])
    expected_stash=dict(old_stash);expected_stash.pop(item['uid'],None)
    stable=('character','character_uid','identity','server','map_id','position','hp','silver','booth')
    return (all(a[name]==b[name] for name in stable)
            and b['inventory']==expected_bag and new_stash==expected_stash
            and _ammo(before['equipped_ammo'])==_ammo(after['equipped_ammo'])
            and before['warehouse']['capacity']==after['warehouse']['capacity']
            and b['trade'] is None and b['request'] is None)


class ProtectedWithdrawal:
    def __init__(self,town,*,plan_path=None,journal=None,clock=time.time,sleep=time.sleep):
        self.town=town;self.plan_path=plan_path;self.journal=journal or ProtectedWithdrawalJournal(clock=clock)
        self.clock,self.sleep=clock,sleep

    def _observe(self):
        from conquest.memory_warehouse import MemoryWarehouseReader
        from conquest.merchants.delivery_bridge import source_memory
        memory=source_memory(self.town.observer)
        reader=MemoryWarehouseReader(self.town.observer.adapter)
        def sample():
            source=memory.read(max_seconds=2,farmer_preflight=True)
            bag=self.town.inventory.read()
            basic={item.uid:(item.type_id,item.plus,1 if 100000<=item.type_id<600000 else item.amount)
                   for item in bag.items}
            rich={item['uid']:(item['type_id'],item['plus'],item['quantity']) for item in source['inventory']}
            if basic!=rich:raise ValueError('Rich farmer inventory disagrees with the verified inventory deque')
            warehouse=reader.read(rich_item=memory.item)
            npc=self.town.vendor(0,stable_identity_only=True);grid=reader.gui.read('Warehouse/ScrollingRegion_')
            if (grid.size!=(272.,326.) or grid.scroll!=(0.,0.)
                    or len(warehouse.items)>warehouse.capacity):
                raise ValueError('Protected warehouse grid differs from its qualified layout')
            return {'source':source,'equipped_ammo':None if bag.equipped_ammo is None else asdict(bag.equipped_ammo),
                    'warehouse':{'items':[asdict(item) for item in warehouse.items],'capacity':warehouse.capacity},
                    'npc':asdict(npc),'grid':asdict(grid)}
        before=sample();after=sample()
        if (not _same_ownership(before,after) or _npc_identity(before['npc'])!=_npc_identity(after['npc'])
                or before['grid']!=after['grid']):
            raise ValueError('Farmer or warehouse changed across the protected observation')
        return after

    def _plan(self,plan_id,operation_id,uid):
        from conquest.merchants.service_visit import MarketVisit
        current=read_json(MarketVisit().path)
        return load_plan(plan_id,operation_id,uid,path=self.plan_path,current_visit=current)

    def _expected_preflight(self,plan,selected,current):
        source=_source(current['source']);planned=_source(plan['market_reentry']['snapshot'])
        if (self.town.observer.character!=plan['farmer']['character']
                or self.town.observer.adapter.identity!=plan['farmer']['process_identity']
                or self.town.observer.adapter.expected_sha256!=plan['farmer']['client_sha256']
                or source['identity']!=plan['farmer']['process_identity']
                or source['character_uid']!=plan['farmer']['character_uid']
                or source['map_id']!=1036 or source['server']!='America' or source['hp']<=0
                or source['trade'] is not None or source['request'] is not None
                or source['booth']):
            raise ValueError('Protected withdrawal requires the exact idle farmer in Market')
        earlier=[]
        for entry in plan['items']:
            if entry is selected:break
            earlier.append(entry)
        states={row['uid']:row for row in self.journal.for_plan(plan['plan_id'])}
        for row in earlier:
            prior=states.get(row['item']['uid'],{});intent=prior.get('intent',{})
            if (prior.get('phase')!='withdrawn'
                    or prior.get('operation_id')!=row['withdrawal_operation_id']
                    or intent.get('plan_sha256')!=plan['plan_sha256']
                    or intent.get('item')!=row['item']
                    or intent.get('deposit_receipt')!=row['deposit_receipt']):
                raise ValueError('Protected withdrawals must follow the reviewed item order')
        expected_bag=dict(planned['inventory'])
        for row in earlier:expected_bag[row['item']['uid']]=row['item']
        current_warehouse=_core_map(current['warehouse']['items'])
        expected_warehouse=_core_map(plan['market_reentry']['warehouse']['items'])
        for row in earlier:expected_warehouse.pop(row['item']['uid'],None)
        planned_stable=('character','character_uid','identity','server','map_id','booth','trade','request')
        if (any(source[name]!=planned[name] for name in planned_stable)
                or source['inventory']!=expected_bag or source['silver']!=planned['silver']
                or _ammo(current['equipped_ammo'])!=_ammo(plan['market_reentry']['equipped_ammo'])
                or current_warehouse!=expected_warehouse
                or current['warehouse']['capacity']!=plan['market_reentry']['warehouse']['capacity']):
            raise ValueError('Farmer or warehouse ownership changed after verified Market reentry')
        rich=_core_map(current['warehouse']['items'])
        if rich.get(selected['item']['uid'])!=selected['item']:
            raise ValueError('Stored protected item does not match its exact deposit fingerprint')
        if len(source['inventory'])>=40:
            raise ValueError('Protected withdrawal requires a free inventory slot')

    @staticmethod
    def _point(observation,item):
        stored=next((row for row in observation['warehouse']['items'] if row['uid']==item['uid']),None)
        grid=observation['grid']
        if stored is None or not 0<=stored['slot']<48:
            raise ValueError('Protected item is outside the qualified visible warehouse grid')
        point=(round(grid['position'][0]+20+40*(stored['slot']%6)),
               round(grid['position'][1]+20+40*(stored['slot']//6)))
        if not (grid['position'][0]<point[0]<grid['position'][0]+grid['size'][0]
                and grid['position'][1]<point[1]<grid['position'][1]+grid['size'][1]):
            raise ValueError('Protected item point is outside the warehouse grid')
        return point

    def reconcile(self,state,plan,selected,*,observation=None,failure=None):
        if state['phase'] in TERMINAL:return self._result(state)
        try:current=observation or self._observe()
        except Exception as error:
            if state['phase']!='blocked':
                changes={'error':'Protected withdrawal ownership observation unavailable',
                         'observation_failure':{'error_type':type(error).__name__,
                                                'reason':str(error),'at':self.clock()}}
                if failure is not None:changes['failure']=failure
                state=self.journal.transition(state['operation_id'],state['phase'],'blocked',
                                              **changes)
            return self._result(state)
        before=state['intent']['before'];item=selected['item']
        if _withdrawn(before,current,item):
            state=self.journal.transition(state['operation_id'],state['phase'],'withdrawn',
                receipt={'operation_id':state['operation_id'],'plan_id':state['plan_id'],'item':item,
                         'before_digest':_digest(before),'after':_plain_observation(current),
                         'after_digest':_digest(_plain_observation(current)),'verified_at':self.clock(),
                         'verified_in_inventory':True,'verified_absent_from_warehouse':True})
        elif state['phase']=='prepared' and _same_ownership(before,current):
            receipt={'operation_id':state['operation_id'],'plan_id':state['plan_id'],'item':item,
                     'before_digest':_digest(before),'observed_digest':_digest(_plain_observation(current)),
                     'verified_at':self.clock(),'input_attempted':False}
            changes={'receipt':receipt}
            if failure is not None:
                receipt['preinput_failure']=failure
                changes.update(error=failure['reason'],failure=failure)
            state=self.journal.transition(state['operation_id'],'prepared','no_transfer',
                                          **changes)
        elif state['phase']!='blocked':
            changes={'error':'Protected withdrawal result is ambiguous; no repeat input is permitted',
                     'observation_digest':_digest(_plain_observation(current))}
            if failure is not None:changes['failure']=failure
            state=self.journal.transition(state['operation_id'],state['phase'],'blocked',
                                          **changes)
        return self._result(state)

    @staticmethod
    def _result(state):
        phase=state['phase']
        return {'operation_id':state['operation_id'],'plan_id':state['plan_id'],'uid':state['uid'],
                'phase':phase,'outcome':phase if phase in TERMINAL else 'unresolved',
                'input_attempted':phase not in ('prepared','no_transfer'),
                'receipt':state.get('receipt'),'reason':state.get('error'),
                'next_action':'continue_controlled_qualification' if phase=='withdrawn' else
                              ('retry_with_new_reviewed_plan' if phase=='no_transfer' else 'reconcile_read_only')}

    def run(self,plan_id,operation_id,uid):
        from conquest.desktop_runtime import physical_coordinates
        with physical_coordinates():
            return self._run(plan_id,operation_id,uid)

    def _run(self,plan_id,operation_id,uid):
        plan,selected=self._plan(plan_id,operation_id,uid)
        old=self.journal.get(operation_id)
        if old:
            if old['plan_id']!=plan_id or old['uid']!=uid:
                raise ValueError('Protected withdrawal operation ID was reused')
            intent=old.get('intent',{})
            if (intent.get('plan_sha256')!=plan['plan_sha256']
                    or intent.get('item')!=selected['item']
                    or intent.get('deposit_receipt')!=selected['deposit_receipt']):
                raise ValueError('Protected withdrawal plan changed after operation admission')
            return self.reconcile(old,plan,selected)
        check=getattr(self.town,'check_input',None)
        if not callable(check):
            raise ValueError('Protected withdrawal requires the authenticated town input guard')
        check()
        observation=self._observe();self._expected_preflight(plan,selected,observation)
        logical_point=self._point(observation,selected['item'])
        layout,layout_revision=self.town.warehouse_layout()
        point=self.town.warehouse_native_point(logical_point,layout_revision)
        now=self.clock();deadline=now+MAX_INPUT_SECONDS
        state={'version':1,'operation_id':operation_id,'plan_id':plan_id,'uid':uid,
               'phase':'prepared','created_at':now,'input_deadline':deadline,
               'intent':{'item':selected['item'],'plan_sha256':plan['plan_sha256'],
                         'deposit_receipt':selected['deposit_receipt'],
                         'before':_plain_observation(observation)}}
        state=self.journal.begin(state)
        if state['phase']!='prepared':return self.reconcile(state,plan,selected)

        def input_guard(*,hover=False):
            if self.clock()>=deadline:raise ValueError('Protected withdrawal input deadline expired')
            check()
            layout.assert_current(layout_revision)
            fresh=self._observe()
            if (not _same_ownership(observation,fresh)
                    or _npc_identity(fresh['npc'])!=_npc_identity(observation['npc']) or fresh['grid']!=observation['grid']
                    or self.town.warehouse_native_point(
                        self._point(fresh,selected['item']),layout_revision)!=point):
                raise ValueError('Protected warehouse layout, slot or ownership changed before input')
            if hover or state['phase']=='input_maybe_sent':
                from conquest.merchants.memory import unpack,HoverNotReady,GuiReader
                gui=GuiReader.for_session(self.town.observer.adapter)
                context=unpack(gui.session,gui.base+gui.context_rva,'<Q')[0]
                if unpack(gui.session,context+0x3ec0,'<Q')[0]!=fresh['grid']['address']:
                    raise HoverNotReady('Protected warehouse item is covered by another window')
            # Observation and hover sampling can consume most of the bounded
            # window.  Recheck authority after them, immediately before the
            # shared foreground helper is allowed to press.
            if self.clock()>=deadline:raise ValueError('Protected withdrawal input deadline expired')
            check()
            layout.assert_current(layout_revision)

        def before_press():
            from conquest.merchants.driver import wait_hover_validation
            wait_hover_validation(lambda:input_guard(hover=True),check or (lambda:None))
            nonlocal state
            state=self.journal.transition(operation_id,'prepared','input_maybe_sent',
                attempt={'stage':'warehouse_item_click','at':self.clock(),'point':list(point),
                         'source_digest':_digest(_plain_observation(observation))})
            self.town.input_attempted=True

        try:
            from conquest.foreground import foreground_click
            foreground_click(self.town.observer.operations.target,*point,
                tuple(layout_revision.client_size),require_foreground=True,
                before_press=before_press,layout_guard=input_guard)
            state=self.journal.transition(operation_id,'input_maybe_sent','reconciling',
                                          click_returned_at=self.clock())
        except Exception as error:
            state=self.journal.get(operation_id)
            failure={'stage':'pre_input' if state['phase']=='prepared' else 'input_maybe_sent',
                     'error_type':type(error).__name__,'reason':str(error),'at':self.clock()}
            return self.reconcile(state,plan,selected,failure=failure)
        end=time.monotonic()+3
        while time.monotonic()<end:
            try:current=self._observe()
            except Exception:
                self.sleep(.05);continue
            if _withdrawn(state['intent']['before'],current,selected['item']):
                return self.reconcile(state,plan,selected,observation=current)
            if not _same_ownership(state['intent']['before'],current):
                return self.reconcile(state,plan,selected,observation=current)
            self.sleep(.05)
        return self.reconcile(state,plan,selected)


def withdraw(town,plan_id,operation_id,uid,**options):
    return ProtectedWithdrawal(town,**options).run(plan_id,operation_id,uid)


def operator_override(operation_id, *, operator_confirmed=False,
                      confirmation_reference=None, operator=None,
                      fresh_evidence=None, journal=None, incident_digest=None):
    """Close one unresolved withdrawal hold without issuing warehouse input."""
    return (journal or ProtectedWithdrawalJournal()).operator_override(
        operation_id,operator_confirmed=operator_confirmed,
        confirmation_reference=confirmation_reference,operator=operator,
        fresh_evidence=fresh_evidence,incident_digest=incident_digest)
