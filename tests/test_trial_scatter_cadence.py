import pytest
from conquest.trial import scatter_receipt_ready

@pytest.mark.parametrize('elapsed,before,after,ready',[
    (.19,100,97,False),(.2,100,97,True),(.4,100,99,False),
    (.4,100,100,False),(.4,100,5000,False),(.4,100,94,True)])
def test_repositioning_requires_full_scatter_consumption(elapsed,before,after,ready):
    assert scatter_receipt_ready(elapsed,before,after) is ready
