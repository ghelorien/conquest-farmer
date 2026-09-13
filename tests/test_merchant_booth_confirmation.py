import struct
from types import SimpleNamespace as NS
import pytest
from conquest.merchants import booth_confirmation as b

@pytest.mark.parametrize('change',['none','flag','callback','text','handler','window'])
def test_open_booth_control_pins_native_callback_and_flag(monkeypatch,change):
    base=0x140000000;model=0x100000;window=0x200000
    blocks={model+12:struct.pack('<B',1),model+0x100:struct.pack('<Q',model+0xc8),
            model+0xc8:struct.pack('<Q',base+0x5c7ca0),model+0xd0:struct.pack('<I',12 if change=='flag' else 11),
            base+0x5c7cb0:struct.pack('<Q',base+(0xd8b91 if change=='callback' else 0xd8b90))}
    for rva,code in b.CODE.items():blocks[base+rva]=bytes.fromhex(code)
    if change=='handler':blocks[base+0x95fd6]=bytes(5)
    raw=bytearray(0x250);struct.pack_into('<2f',raw,0xe8,1036,373);struct.pack_into('<f',raw,0x114,18)
    blocks[window]=bytes(raw)
    labels=['Open Booth###Confirm','Start Vending','Yes','No']
    if change=='text':labels[0]='Trade###Confirm'
    monkeypatch.setattr(b,'string',lambda s,a:labels[(a-model-0x48)//0x20])
    session=NS(read_block=lambda a,n:blocks[a][:n])
    gui=NS(base=base,model=lambda *a:model)
    driver=NS(memory=NS(gui=gui),observer=NS(adapter=session))
    w={'name':'Open Booth###Confirm','address':window,'geometry':[844,282,200,100]}
    snapshot={'windows':[] if change=='window' else [w]}
    if change=='none':assert b.control(driver,snapshot,{'uid':11})==(w,(944,360))
    else:
        with pytest.raises(ValueError):b.control(driver,snapshot,{'uid':11})


@pytest.mark.parametrize('owned',[True,False])
def test_submitted_claim_reconciles_without_second_confirmation(tmp_path,monkeypatch,owned):
    from conquest.merchants import stall_probe
    from conquest.merchants.journal import Journal
    before={'identity':{'pid':1},'silver':100,'inventory':[],'booth':[],
            'map_id':1036,'position':[232,193],'own_booth_uid':90 if owned else 0,'booth_open':False}
    record={'phase':'submitted','operation':'inspect_vacant_flag','identity':{'pid':1},'silver':100,
            'inventory_before':[],'booth_before':[],'confirmation_submitted':True,
            'flag':{'uid':11,'position':[230,193]}}
    monkeypatch.setattr(stall_probe,'owned_booth',lambda *a:{'position':[233,193]})
    monkeypatch.setattr(b,'submit',lambda *a:pytest.fail('Never repeat a submitted confirmation'))
    driver=NS(memory=NS(read=lambda:before),observer=NS(character='Dutch'))
    if owned:
        result=stall_probe.confirm_observed_flag(driver,None,Journal(tmp_path/'j.db'),record,lambda:None)
        assert result['claim_verified'] and not result['booth_open']
        assert result['phase']=='observed'
    else:
        with pytest.raises(ValueError,match='reconciliation'):
            stall_probe.confirm_observed_flag(driver,None,Journal(tmp_path/'j.db'),record,lambda:None)
