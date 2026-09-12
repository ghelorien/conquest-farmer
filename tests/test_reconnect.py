from types import SimpleNamespace
from conquest.reconnect import Reconnector,load_credentials


def test_reconnect_submits_once_after_stable_login_then_backs_off():
    now=[0.];calls=[];events=[]
    r=Reconnector(lambda:calls.append('login'),events.append,clock=lambda:now[0])
    r.step(False);assert not calls
    r.step(True);assert not calls
    now[0]=1;r.step(True);assert calls==['login']
    now[0]=10;r.step(True);assert len(calls)==1
    now[0]=16;r.step(True);assert len(calls)==2
    r.step(False);assert events[-1]['state']=='connection_restored'


def test_reconnect_does_not_fire_in_game_including_death():
    r=Reconnector(lambda:(_ for _ in ()).throw(AssertionError('Must not reconnect')),lambda event:None)
    for _ in range(10):r.step(False)


def test_disconnected_observer_never_reads_player_stats(monkeypatch):
    from conquest.embedded_observer import EmbeddedObserver
    from conquest import reconnect,memory_life
    monkeypatch.setattr(reconnect,'login_screen',lambda hwnd:True)
    monkeypatch.setattr(memory_life,'read_life',lambda *args:(_ for _ in ()).throw(AssertionError('Disconnected stats')))
    o=EmbeddedObserver.__new__(EmbeddedObserver)
    o.operations=SimpleNamespace(target=SimpleNamespace(hwnd=1))
    result=o._observe()
    assert result['connection_state']=='login' and 'life' not in result


def test_focus_wait_does_not_consume_login_attempts():
    from conquest.capture import CaptureUnavailable
    now=[0.]
    def submit():
        raise CaptureUnavailable('No focus')
    r=Reconnector(submit,lambda event:None,clock=lambda:now[0])
    for second in range(8):
        now[0]=second
        r.step(True)
    assert r.attempts==0


def test_disconnected_vendor_reader_never_reads_stats(monkeypatch):
    import threading
    import pytest
    from conquest.embedded_observer import EmbeddedObserver
    from conquest import reconnect, memory_life
    monkeypatch.setattr(reconnect, 'login_screen', lambda hwnd:True)
    monkeypatch.setattr(memory_life, 'read_life', lambda *args:pytest.fail('Disconnected stats'))
    observer=EmbeddedObserver.__new__(EmbeddedObserver)
    observer.lock=threading.RLock()
    observer.operations=SimpleNamespace(target=SimpleNamespace(hwnd=1))
    with pytest.raises(ValueError, match='Reconnect'):
        observer.sample_npcs()


def test_failed_login_is_bounded_and_does_not_log_exception_details():
    now=[0.];events=[];calls=[]
    def submit():
        calls.append(1)
        raise ValueError('private exception detail')
    r=Reconnector(submit,events.append,clock=lambda:now[0])
    for second in range(200):
        now[0]=second
        r.step(True)
    assert len(calls)==3
    assert 'private exception detail' not in str(events)


def error_reader_fixture(monkeypatch, message=None, flag=1):
    import struct
    from conquest import memory_shop
    from conquest.reconnect import LoginErrorReader
    window=SimpleNamespace(address=0x2000, position=(313.,354.), size=(409.,84.), scroll=(0.,0.))
    monkeypatch.setattr(memory_shop,'MemoryGui',lambda session:SimpleNamespace(base=0,read=lambda name:window))
    message=(message or 'Error: Connection with the server is interrupted. Please re-login. ').encode()
    raw=struct.pack('<QQQQBB',0x30000,0,len(message),max(79,len(message)),flag,0)
    dc=bytearray(0x38)
    struct.pack_into('<2f',dc,8,714.,412.)
    struct.pack_into('<f',dc,16,321.)
    struct.pack_into('<f',dc,0x34,18.)
    blocks={0x698be0+0x6e8:raw,0x2000+0xe0:bytes(dc),0x30000:message}
    session=SimpleNamespace(assert_identity=lambda:None,read_block=lambda address,size:blocks[address][:size])
    return LoginErrorReader(session),blocks


def test_interrupted_server_dialog_uses_memory_button(monkeypatch):
    reader,_=error_reader_fixture(monkeypatch)
    assert reader.read()==(518,421)


def test_unknown_login_dialog_is_not_dismissed(monkeypatch):
    import pytest
    reader,_=error_reader_fixture(monkeypatch,'Invalid account or password')
    with pytest.raises(ValueError,match='Unrecognized'):
        reader.read()


def test_hidden_login_dialog_needs_no_geometry_or_text(monkeypatch):
    reader,blocks=error_reader_fixture(monkeypatch,flag=0)
    blocks.pop(0x30000)
    assert reader.read() is None


def test_changed_dialog_button_is_rejected(monkeypatch):
    import pytest,struct
    reader,blocks=error_reader_fixture(monkeypatch)
    dc=bytearray(blocks[0x20e0]);struct.pack_into('<f',dc,8,800.)
    blocks[0x20e0]=bytes(dc)
    with pytest.raises(ValueError,match='layout changed'):
        reader.read()


def test_reconnect_error_follows_live_width_and_wrapped_text_height(monkeypatch):
    import struct,pytest
    reader,blocks=error_reader_fixture(monkeypatch)
    window=reader.gui.read('##ErrorModal')
    window.position=(800.,500.);window.size=(500.,120.)
    dc=bytearray(blocks[0x20e0])
    struct.pack_into('<2f',dc,8,1292.,594.)
    struct.pack_into('<f',dc,16,808.)
    blocks[0x20e0]=bytes(dc)
    assert reader.read()==(1050,603)
    window.size=(500.,110.)
    with pytest.raises(ValueError,match='layout changed'):reader.read()


def test_exhausted_reconnect_reports_attention_once():
    now=[0.];events=[]
    def blocked():raise ValueError('Unrecognized login error')
    r=Reconnector(blocked,events.append,clock=lambda:now[0])
    for second in range(200):
        now[0]=second;r.step(True)
    assert r.state=='reconnect_exhausted'
    assert sum(event['state']=='reconnect_exhausted' for event in events)==1
    r.step(False)
    assert r.state=='connected' and r.attempts==0


def test_transient_server_disconnects_keep_retrying_with_capped_backoff():
    now=[0.];calls=[];events=[]
    r=Reconnector(lambda:calls.append(now[0]),events.append,clock=lambda:now[0])
    for second in range(300):
        now[0]=second;r.step(True)
    assert calls==[1,16,46,106,166,226,286]
    assert not any(e['state']=='reconnect_exhausted' for e in events)


def test_explicit_retry_clears_exhausted_state_and_waits_for_stable_login():
    now=[0.];calls=[]
    def blocked():
        calls.append(now[0]);raise ValueError('Private data must not be logged')
    r=Reconnector(blocked,lambda e:None,clock=lambda:now[0])
    for second in range(80):now[0]=second;r.step(True)
    assert r.state=='reconnect_exhausted' and len(calls)==3
    r.retry();r.step(True)
    assert len(calls)==3
    now[0]+=1;r.step(True)
    assert len(calls)==4 and r.failures==1


def test_login_fields_and_button_follow_memory_form_origin(monkeypatch):
    import hashlib,struct,pytest
    from conquest import memory_shop
    from conquest.reconnect import login_form_points
    monkeypatch.setattr(memory_shop,'MemoryGui',lambda session:SimpleNamespace(base=0))
    digests={b'fields':'2eeddcb3c87af4385b8b65fa1c77f8ebfa9c6da5bd18d6688e110eef6e192006',
             b'button':'a414f6338c9dd8a7c33d0f7f64befc0d1484414dbca291b80f6efce3a676c298'}
    original=hashlib.sha256
    monkeypatch.setattr(hashlib,'sha256',lambda data:SimpleNamespace(hexdigest=lambda:digests[data]) if data in digests else original(data))
    blocks={0xe67f0:b'fields',0xe6b8a:b'button'}
    session=SimpleNamespace(read_block=lambda address,size:blocks[address])
    for x,y in [(606.,562.),(414.,397.)]:
        window=SimpleNamespace(address=0x2000,position=(x,y),size=(208.,186.),scroll=(0.,0.))
        dc=[x+8,y+182,x+125,y+166,x+8,y+8,x+200,y+178,x+8,y+8,0,0,0,12]
        blocks[0x20e0]=struct.pack('<14f',*dc)
        assert login_form_points(session,window)==((int(x+104),int(y+33)),(int(x+104),int(y+71)),(int(x+104),int(y+153)))
    dc[13]=16
    blocks[0x20e0]=struct.pack('<14f',*dc)
    with pytest.raises(ValueError,match='layout changed'):login_form_points(session,window)
    blocks[0xe67f0]=b'changed'
    with pytest.raises(ValueError,match='renderer changed'):login_form_points(session,window)
