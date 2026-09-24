"""One-shot negative response to an exact incidental Farmer vending prompt.

This separate observation is never a generic closed trade snapshot. Unknown
confirmations retain the ordinary reader's refusal; only the proved No button
can be used here, with unchanged assets and a durable pre-mouse-down marker.
"""
from pathlib import Path
import json
import os
import tempfile
import time
import uuid

from conquest.character_context import farmer_name, state_path
from conquest.merchants.reader_1078 import CLIENT_SHA256_1078
from conquest.merchants.trade_reader_1078 import TradeMemory1078
from conquest.merchants import open_booth_control_1078 as control

JOURNAL = Path(state_path('reports/banking/open-booth-cancel.json'))
OWNED = ('identity','character','character_uid','server','inventory','booth',
         'silver','capacity','own_booth_uid','booth_open','map_id','position')


class _PromptMemory(TradeMemory1078):
    def _request(self, actual):
        # Base ownership traversal still verifies the open/closed model byte
        # and every asset twice. This local sentinel is renamed immediately;
        # it is never sent to the manual visitor or delivery admission system.
        return control.read(self.gui)


def observe(observer):
    if observer.character != farmer_name():
        raise ValueError('Open Booth cleanup is restricted to the Farmer')
    memory = _PromptMemory(observer)
    snapshot = memory.read_manual_ownership()
    from conquest.memory_life import MemoryLifeReader
    life=MemoryLifeReader.for_session(observer.adapter,observer.character).read()
    if (snapshot['trade'] is not None or snapshot['map_id'] != 1036
            or snapshot.get('hp', 0) <= 0 or life.dead_candidate
            or life.map_id!=snapshot['map_id'] or list(life.position)!=snapshot['position']
            or life.current_hp!=snapshot['hp']
            or life.max_hp!=snapshot['health']['max_hp_candidate']
            or life.object_address!=memory._actual_and_wrapper()[1]
            or not 0<=time.time()-snapshot['timestamp']<=2):
        raise ValueError('Open Booth cleanup requires a living Market Farmer with no trade')
    snapshot['confirmation'] = snapshot.pop('request')
    snapshot['request'] = None
    snapshot['canonical_manual_ownership'] = snapshot['confirmation'] is None
    snapshot['reader_build'] = '1078-open-booth-cancel-only'
    # The final model check excludes a prompt replacement during asset reads.
    if control.read(memory.gui) != snapshot['confirmation']:
        raise ValueError('Open Booth prompt changed during ownership observation')
    return snapshot


def _load():
    if not JOURNAL.exists():return {}
    value=json.loads(JOURNAL.read_text(encoding='utf-8'))
    if not isinstance(value,dict) or value.get('phase') not in ('prepared','submitted','verified'):
        raise ValueError('Open Booth cancellation journal is invalid')
    return value


def _save(value):
    JOURNAL.parent.mkdir(parents=True,exist_ok=True)
    fd,name=tempfile.mkstemp(prefix=JOURNAL.name+'.',dir=JOURNAL.parent)
    try:
        with os.fdopen(fd,'w',encoding='utf-8') as stream:
            json.dump(value,stream,sort_keys=True,allow_nan=False)
            stream.flush();os.fsync(stream.fileno())
        os.replace(name,JOURNAL)
    finally:
        if os.path.exists(name):os.unlink(name)


def _owned(snapshot):return {key:snapshot[key] for key in OWNED}


def cleanup(loop):
    """Native route hook; retain existing behavior for other client builds."""
    health=getattr(loop,'health',None)
    if health is None or health().get('expected_sha256')!=CLIENT_SHA256_1078:return None
    return loop.town('cancel-open-booth-confirm')


def _clear():
    from conquest.merchants.coordination import check_input,manual_session_blocked
    from conquest.merchants.delivery_operation import pending,guard_protected_assets
    from conquest.merchants.delivery_route import pending as route_pending
    from conquest.merchants.delivery_probe import read_probe,TERMINAL
    check_input()
    if manual_session_blocked('Farmer'):
        raise ValueError('Manual ownership blocks Open Booth cleanup')
    guard_protected_assets()
    probe=read_probe(read_only=True)
    if probe is not None and probe.get('phase') not in TERMINAL:
        raise ValueError('A pending staged trade blocks Open Booth cleanup')
    if pending() or route_pending():
        raise ValueError('A pending delivery blocks Open Booth cleanup')


def _settle(record, after):
    if _owned(after) != _owned(record['before']):
        raise ValueError('Ownership changed after Open Booth cancellation; no replay')
    if after['confirmation'] is not None:
        raise ValueError('Open Booth cancellation remains uncertain; no replay')
    record.update(phase='verified',verified_at=time.time(),after=after,
                  outcome='closed_unchanged',cancel_verified=True)
    _save(record)
    return {'closed_panel':'Open Booth###Confirm','cancel_verified':True,
            'request_id':record['request_id'],'outcome':'closed_unchanged'}


def cancel(trade, check=lambda:None):
    """No-op when closed; submitted records can only reconcile, never click."""
    observer=trade.observer
    if getattr(observer.adapter,'expected_sha256',None) != CLIENT_SHA256_1078:
        return None
    from conquest.merchants.memory import GuiReader,unpack
    gui=GuiReader.for_session(observer.adapter)
    # Frequent travel-panel checks must not rescan inventory or loaded code
    # when no confirmation and no uncertain cancellation exist.
    visible=unpack(gui.session,gui.model(15,control.MODEL_VTABLE)+12,'<B')[0]
    if visible not in (0,1):raise ValueError('Invalid confirmation visibility')
    record=_load()
    if not visible and record.get('phase') not in ('prepared','submitted'):return None
    from conquest.merchants.coordination import input_scope
    from conquest.desktop_runtime import physical_coordinates
    from conquest.merchants.driver import wait_hover_validation
    with input_scope(),physical_coordinates():
        def allowed():
            check()
            guard=getattr(trade,'check_input',None)
            if guard:guard()
            _clear()
        allowed()
        before=observe(observer)
        if record.get('phase') in ('prepared','submitted'):
            if _owned(before)!=_owned(record['before']):
                raise ValueError('Open Booth cancellation ownership changed; reconciliation required')
            if before['confirmation'] is None:return _settle(record,before)
            if record['phase']=='submitted':
                raise ValueError('Open Booth cancellation already submitted; no repeat input')
            if before['confirmation']!=record['before']['confirmation']:
                raise ValueError('Prepared Open Booth cancellation instance changed')
        else:
            if before['confirmation'] is None:return None
            history=record.get('history',[])+([record.copy()] if record else [])
            if history:history[-1].pop('history',None)
            record={'version':1,'request_id':'open-booth-cancel-'+uuid.uuid4().hex,
                    'phase':'prepared','created_at':time.time(),'before':before,'history':history}
            _save(record)
        layout,revision=trade.warehouse_layout()
        window,logical=control.locate(gui,before['confirmation'])
        point=trade.warehouse_native_point(logical,revision)
        def guard():
            allowed();layout.assert_current(revision)
            fresh=observe(observer)
            if (_owned(fresh)!=_owned(before) or fresh['confirmation']!=before['confirmation']
                    or control.locate(gui,fresh['confirmation'])!=(window,logical)):
                raise ValueError('Open Booth ownership or control changed before cancellation')
            gui.assert_hovered(window,control.LABEL)
        def submitted():
            allowed();layout.assert_current(revision)
            if control.read(gui)!=before['confirmation']:
                raise ValueError('Open Booth instance changed at cancellation boundary')
            gui.assert_hovered(window,control.LABEL)
            record.update(phase='submitted',submitted_at=time.time())
            _save(record)
            trade.input_attempted=True
        try:
            trade.click(point,before_press=lambda:wait_hover_validation(guard,allowed),
                        before_mouse_down=submitted)
            # Only observation repeats after the durable one-shot boundary.
            deadline=time.monotonic()+2
            while True:
                allowed();after=observe(observer)
                if after['confirmation'] is None:return _settle(record,after)
                if _owned(after)!=_owned(before):
                    raise ValueError('Ownership changed while reconciling Open Booth cancellation')
                if time.monotonic()>=deadline:
                    raise ValueError('Open Booth cancellation remains uncertain; no replay')
                time.sleep(.05)
        except Exception as error:
            record.update(error=str(error),error_at=time.time())
            _save(record)
            raise
