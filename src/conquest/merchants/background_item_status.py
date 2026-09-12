"""Read-only reconciliation of an exact diagnostic inventory item."""
from dataclasses import asdict
import struct
import time
from conquest.equipment import SLOTS, item_details
from conquest.memory_life import read_life
from conquest.memory_ground import MemoryGroundReader


def item_status(ui, character, uid):
    if type(uid) is not int or uid<=0:
        raise ValueError('Expected a positive exact item UID')
    observer=ui.runtime.observers[character]
    driver=ui.runtime.controllers[character].driver
    with observer.lock:
        session=observer.adapter
        session.assert_identity()
        life=read_life(session,observer.health_layout,character)
        state=driver.read()
        result={'character':character,'uid':uid,'timestamp':time.time(),'identity':state['identity'],
                'inventory':[i for i in state['inventory'] if i['uid']==uid],
                'booth':[i for i in state['booth'] if i['uid']==uid],
                'position':state['position'],'silver':state['silver'],'equipment':{}}
        for slot,offset in SLOTS.items():
            raw=session.read_block(life.object_address+offset,8)
            ptr=struct.unpack('<Q',raw)[0]
            if ptr:
                item=item_details(session,ptr,driver.memory.gui.base)
                if session.read_block(life.object_address+offset,8)!=raw:
                    raise ValueError('Equipped item pointer changed')
                result['equipment'][slot]=item
        try:
            result['ground']=[asdict(i) for i in MemoryGroundReader(observer.entities).read()]
        except (ValueError,OSError) as error:
            result['ground_error']=str(error)
        session.assert_identity()
        return result
