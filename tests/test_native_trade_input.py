from types import SimpleNamespace as NS

import pytest

from conquest.merchants import native_trade_input as native


@pytest.mark.parametrize('accepted,other,count',[(True,False,0),(False,True,0),(False,False,20)])
def test_native_drop_rejects_locked_or_full_trade(monkeypatch,accepted,other,count):
    monkeypatch.setattr(native,'trade_grid',lambda *a:(None,{}))
    monkeypatch.setattr(native,'cell',lambda *a:pytest.fail('No drop cell may be selected'))
    driver=NS(memory=NS(gui=None))
    with pytest.raises(ValueError):
        native.locate(driver,{'trade':dict(accepted=accepted,other_accepted=other,
                      own_items=[{}]*count)},'native_trade_drop')


def test_native_trade_buttons_use_the_correct_hover_scope(monkeypatch):
    seen=[];window={'address':7}
    gui=NS(assert_hovered=lambda w,label,**kw:seen.append((w,label,kw)))
    driver=NS(memory=NS(gui=gui))
    monkeypatch.setattr(native,'confirm_control',lambda *a:(window,(100,200),123))
    monkeypatch.setattr(native,'request_control',lambda *a:(window,(300,400)))
    native.hover(driver,{},'native_trade_confirm')
    native.hover(driver,{},'native_trade_request')
    assert seen==[(window,'Accept Trade',{'seeds':[123]}),(window,'Accept',{'seeds':None})]
    with pytest.raises(ValueError,match='Unknown'):
        native.locate(driver,{},'unqualified_control')


@pytest.mark.parametrize('point,valid',[((400,300),True),((0,300),False),((800,300),False),((400,600),False)])
def test_native_point_scales_only_visible_gui_coordinates(monkeypatch,point,valid):
    monkeypatch.setattr(native,'locate',lambda *a:({},point,None,None))
    driver=NS(memory=NS(gui=NS(viewport_size=lambda:(800,600))),
              target=NS(snapshot=lambda:{'client_size':(1600,1200)}))
    if valid:assert native.point(driver,{},'native_trade_drop')==(800,600)
    else:
        with pytest.raises(ValueError,match='outside'):
            native.point(driver,{},'native_trade_drop')


@pytest.mark.parametrize('changed',[None,'inventory_item','booth_drop','remove_listing'])
def test_listing_preflight_rejects_resized_panels_before_using_controls(changed):
    from conquest.merchants.driver import MerchantDriver
    controls={name:{'window':name,'size':[400,200]} for name in
              ('inventory_item','booth_drop','remove_listing')}
    snapshot={'windows':[{'name':name,'geometry':[0,0,401 if name==changed else 400,200]}
                         for name in controls]}
    driver=NS(require_qualified=lambda capability:{'controls':controls},read=lambda:snapshot)
    if changed:
        with pytest.raises(ValueError,match='resized'):
            MerchantDriver.verify_listing_layout(driver)
    else:MerchantDriver.verify_listing_layout(driver)
