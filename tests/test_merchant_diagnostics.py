from types import SimpleNamespace as NS
import importlib.util
from pathlib import Path
import pytest
import subprocess

spec=importlib.util.spec_from_file_location('merchant_diagnostics',
    Path(__file__).resolve().parents[1]/'scripts/start_merchant_diagnostics.py')
diagnostics=importlib.util.module_from_spec(spec)
spec.loader.exec_module(diagnostics)


@pytest.mark.parametrize('case',['hidden','missing','duplicate','launcher_only'])
def test_diagnostics_find_hidden_merchants_without_changing_visibility(case):
    windows=[dict(hwnd=10,title='[Spiritual - ClassicConquer]',client_size=[1416,907],visible=True),
             dict(hwnd=11,title='[Dutch - ClassicConquer]',client_size=[1888,665],visible=False)]
    if case=='missing':windows.pop()
    if case=='duplicate':windows.append(dict(windows[1],hwnd=12))
    if case=='launcher_only':windows[1]['client_size']=[100,100]
    catalog=NS(identities=lambda:[{'pid':1}],backend=NS(windows=lambda pid:windows))
    if case=='hidden':
        result=diagnostics.merchant_candidates(catalog)
        assert [n for n,w in result]==['Spiritual','Dutch']
        assert [w.hwnd for n,w in result]==[10,11]
        assert windows[1]['visible'] is False
    else:
        with pytest.raises(ValueError,match='Dutch: expected one client'):
            diagnostics.merchant_candidates(catalog)


def test_readonly_diagnostics_launches_the_script_from_its_source_release(tmp_path,monkeypatch):
    from conquest.merchants import ui as merchant_ui
    calls=[]
    class Process:
        pid=123
    monkeypatch.setattr(merchant_ui,'state_path',lambda value:str(tmp_path/Path(value).name))
    monkeypatch.setattr(subprocess,'Popen',lambda command,**options:
                        calls.append((command,options)) or Process())
    memory=type('Memory',(),{'read':lambda self:{}})()
    controller=type('Controller',(),{'driver':type('Driver',(),{'memory':memory})()})()
    ui=type('UI',(),{})()
    ui.runtime=type('Runtime',(),{
        'observers':{name:object() for name in merchant_ui.CHARACTERS},
        'controllers':{name:controller for name in merchant_ui.CHARACTERS}})()
    result=merchant_ui.UnifiedUI.dispatch(ui,{'action':'start-readonly-diagnostics'})
    repo=Path(merchant_ui.__file__).resolve().parents[3]
    assert result=={'pid':123,'read_only':True}
    assert calls[0][0][1]==str(repo/'scripts/start_merchant_diagnostics.py')
    assert calls[0][1]['cwd']==repo
