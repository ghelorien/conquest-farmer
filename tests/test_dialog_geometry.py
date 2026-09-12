import json
import math
from pathlib import Path

import pytest

from conquest.dialog_geometry import option_point,scroll_direction


def saved_choices():
    policy=json.loads(Path('profiles/meteor-banking.json').read_text())
    dialogs=list(policy['exchange']['dialogs'])
    for origin in policy['origins'].values():
        for leg in ('outbound','return'):dialogs+=origin[leg]['dialogs']
    return dialogs


@pytest.mark.parametrize('step',saved_choices())
@pytest.mark.parametrize('width,origin,viewport',[
    (280,(100,20),(1020,754)),(356,(378,20),(1420,1009)),
    (480,(900,400),(1920,1080))])
def test_every_saved_service_choice_handles_moved_resized_dialogs(step,width,origin,viewport):
    options=[r for r in step['records'] if r['kind']==1]
    rows=math.ceil(len(options)/2);x,y=origin
    data={'records':step['records'],
          'window':{'position':origin,'size':(width,160+rows*22),'scroll':(0,0)},
          'table':(x+20,y+110,x+width-20,rows*22)}
    for i,choice in enumerate(options):
        assert scroll_direction(data,choice['text'],viewport)==0
        px,py=option_point(data,choice['text'],viewport)
        assert px==round(x+20+(i%2+.5)*(width-40)/2)
        assert py==y+110+(i//2)*22+11


def choices(top=80,scroll=0):
    return {'records':[{'kind':1,'option':i,'text':str(i)} for i in range(8)],
            'window':{'position':(100,20),'size':(356,120),'scroll':(0,scroll)},
            'table':(120,top,436,88)}


def test_only_selected_row_needs_to_be_visible():
    data=choices()
    assert option_point(data,'0')==(199,91)
    assert scroll_direction(data,'6')==-1
    with pytest.raises(ValueError,match='clips'):option_point(data,'6')
    data=choices(top=30,scroll=50)
    assert scroll_direction(data,'0')==1
    assert scroll_direction(data,'6')==0
    assert option_point(data,'6')==(199,107)


def test_client_edges_also_clip_choices():
    data=choices()
    assert scroll_direction(data,'2',(800,110))==-1
    with pytest.raises(ValueError,match='clips'):option_point(data,'2',(800,110))
    with pytest.raises(ValueError,match='horizontally'):option_point(data,'1',(300,754))


@pytest.mark.parametrize('failure',['duplicate','input','nan','row_height','narrow'])
def test_unqualified_layout_never_produces_point(failure):
    data=choices()
    if failure=='duplicate':data['records'][1]['text']='0'
    if failure=='input':data['records'].append({'kind':2,'text':'amount'})
    if failure=='nan':data['table']=(120,float('nan'),436,88)
    if failure=='row_height':data['table']=(120,80,436,90)
    if failure=='narrow':data['window']['size']=(40,120)
    with pytest.raises(ValueError):option_point(data,'0')


def test_saved_route_scrolls_to_selected_option_before_selection(monkeypatch):
    from types import SimpleNamespace as NS
    from conquest import meteor_banking as m
    data=choices();calls=[]
    def town(action,**kw):
        calls.append((action,kw))
        if action=='service-dialog':return data
        if action=='service-scroll-dialog':
            assert kw['option']=='6'
            data['table']=(120,30,436,88);data['window']['scroll']=(0,50)
        if action=='service-select':return {'interacted':True}
    monkeypatch.setattr(m.time,'sleep',lambda seconds:None)
    loop=NS(town=town,check_stop=lambda:None)
    assert m.select_saved_dialog(loop,'MillionaireLee',{'records':data['records'],'option':'6'})=={'interacted':True}
    assert [a for a,_ in calls]==['service-dialog','service-scroll-dialog','service-dialog','service-select']


@pytest.mark.parametrize('option,top,scroll,direction',[('6',80,0,-1),('0',30,50,1)])
def test_service_scroll_uses_live_geometry_and_exact_option(monkeypatch,option,top,scroll,direction):
    from types import SimpleNamespace as NS
    from conquest import market_services as m,foreground
    data=choices(top,scroll);data['window']=NS(**data['window'])
    npc=NS(position=(200,200),draw_position=(400,400),entity_id=42)
    observer=NS(adapter=NS(viewport_size=lambda:(1420,1009)),entities=None,
                operations=NS(target=object()))
    trade=NS(observer=observer,service_npc=npc,life=lambda **kw:NS(map_id=1036,position=(200,200)),
             input_attempted=False,click=lambda p:pytest.fail('Use the wheel, not a guessed scrollbar button'))
    monkeypatch.setattr(m,'discover',lambda *a:(None,npc))
    monkeypatch.setattr(m,'read_dialog',lambda *a:data)
    calls=[];monkeypatch.setattr(foreground,'foreground_scroll',lambda *a:calls.append(a))
    body={'action':'service-scroll-dialog','name':'MillionaireLee','records':data['records'],'option':option}
    assert m.execute(trade,body)['interacted']
    assert calls[0][2:]==(direction,(1420,1009)) and trade.input_attempted
    calls.clear();trade.input_attempted=False
    body['records']=[]
    with pytest.raises(ValueError,match='changed'):m.execute(trade,body)
    assert not calls and not trade.input_attempted


def test_twin_city_scrolls_before_fare_and_honors_stop(monkeypatch):
    from types import SimpleNamespace as NS
    from conquest import conductress as c
    records=[{'kind':0,'option':0,'text':'Where are you heading? I can teleport you for a price of 100 silver.'}]
    records += [{'kind':1,'option':i,'text':name} for i,name in enumerate(
        ('Phoenix Castle','Desert City','Ape Mountain','Bird Island.','Mine Cave','Market','Just passing by.'))]
    data=choices();data['records']=records
    calls=[]
    def town(action,**kw):
        calls.append(action)
        if action=='service-dialog':return data
        data['table']=(120,60,436,88);data['window']['scroll']=(0,20)
    monkeypatch.setattr(c.time,'sleep',lambda t:None)
    loop=NS(town=town,check_stop=lambda:None)
    c.prepare_destination(loop,'Bird Island.')
    assert calls==['service-dialog']  # Its row is visible despite lower clipped rows.
    data['table']=(120,120,436,88)
    c.prepare_destination(loop,'Bird Island.')
    assert calls[-3:]==['service-dialog','service-scroll-dialog','service-dialog']
    def stopped():raise ValueError('Manual Stop')
    loop.check_stop=stopped;calls.clear()
    with pytest.raises(ValueError,match='Manual Stop'):c.prepare_destination(loop,'Bird Island.')
    assert not calls
