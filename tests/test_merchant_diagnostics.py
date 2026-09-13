from types import SimpleNamespace as NS
import importlib.util
from pathlib import Path
import pytest

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
