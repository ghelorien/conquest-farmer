from types import SimpleNamespace as NS
import pytest
from conquest import panel_close as p


@pytest.mark.parametrize('case',['success','moved','hover'])
def test_exact_close_widget_must_be_verified_before_press(monkeypatch,case):
    calls=[];pressed=[];w={'name':'Inventory','address':1,'geometry':[50,50,200,300]}
    def windows():
        calls.append('read')
        return [{**w,'address':2 if case=='moved' and len(calls)>1 else 1}]
    def hover(window,label):
        assert label=='#CLOSE'
        if case=='hover':raise ValueError('Wrong hover')
    monkeypatch.setattr(p,'GuiReader',lambda a:NS(windows=windows,assert_hovered=hover))
    monkeypatch.setattr(p,'wait_hover_validation',lambda guard,check:guard())
    def click(point,*,before_press):before_press();pressed.append(point)
    trade=NS(observer=NS(adapter=object()),click=click)
    if case=='success':p.click_close(trade,'Inventory');assert pressed==[(226,62)]
    else:
        with pytest.raises(ValueError):p.click_close(trade,'Inventory')
        assert not pressed
