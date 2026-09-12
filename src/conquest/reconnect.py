"""Reconnect through the ordinary login UI without screen capture.

The login caption/class and field coordinates were inspected once with explicit
user permission. Credentials stay DPAPI-encrypted at rest and never enter logs.
"""
from conquest.character_context import state_path
import ctypes as c
from ctypes import wintypes as w
import json
from pathlib import Path
import time

from conquest.capture import CaptureUnavailable
from conquest.merchants.coordination import coordinated_input


def login_screen(hwnd):
    import win32gui
    return (win32gui.IsWindow(hwnd) and win32gui.GetClassName(hwnd)=='ImGuiShell'
            and win32gui.GetWindowText(hwnd).strip()=='[ClassicConquer]')


def load_credentials(path=Path(state_path('.runtime/account.dpapi'))):
    import win32crypt
    _,plain=win32crypt.CryptUnprotectData(Path(path).read_bytes(),None,None,None,0)
    account=json.loads(plain.decode('utf-8'))
    if (set(account)!={'username','password'} or any(not isinstance(v,str) or not 1<=len(v)<=256 for v in account.values())):
        raise ValueError('Saved login is invalid')
    return account


def type_login_field(target,value):
    from conquest.foreground import Input,InputUnion,KeyboardInput,scan_key_event,press_scan_sequence
    from conquest.win32 import bind
    if not login_screen(target.hwnd) or target.backend.foreground()!=target.hwnd:
        raise CaptureUnavailable('Login field lost focus; no credential input sent')
    key_state=bind(target.backend.user,'GetAsyncKeyState',[c.c_int],c.c_short)
    if any(key_state(vk)&0x8000 for vk in (0x10,0x11,0x12)):
        raise CaptureUnavailable('Release physical modifiers before login')
    from conquest.mouse_priority import guarded_send, require_idle
    require_idle()
    send=guarded_send(bind(target.backend.user,'SendInput',[w.UINT,c.POINTER(Input),c.c_int],w.UINT))
    def send_selection(event):
        # Key-up must run even after focus loss to avoid a stuck modifier.
        if not event.data.ki.dwFlags & 2 and target.backend.foreground()!=target.hwnd:
            raise CaptureUnavailable('Login focus changed during selection')
        if send(1,c.byref(event),c.sizeof(Input))!=1:
            raise OSError('Login selection input failed')
    press_scan_sequence(send_selection,[0x1d,0x1e])
    if not login_screen(target.hwnd) or target.backend.foreground()!=target.hwnd:
        raise CaptureUnavailable('Login field lost focus before credential entry')
    events=[]
    encoded=value.encode('utf-16-le')
    for offset in range(0,len(encoded),2):
        code=int.from_bytes(encoded[offset:offset+2],'little')
        events.extend(Input(type=1,data=InputUnion(ki=KeyboardInput(0,code,flags,0,0))) for flags in (4,6))
    batch=(Input*len(events))(*events)
    if send(len(events),batch,c.sizeof(Input))!=len(events):
        # Always release our control modifier after a partial SendInput batch.
        release=scan_key_event(0x1d,True)
        send(1,c.byref(release),c.sizeof(Input))
        raise OSError('Login input was incomplete')



class LoginErrorReader:
    """Qualified c2b53437 ErrorModal object and final OK button layout."""
    def __init__(self, session):
        from conquest.memory_shop import MemoryGui
        self.gui = MemoryGui(session)
        self.session = session
        self.root = self.gui.base + 0x698be0

    def read(self):
        import struct
        from conquest.addressing import checked_address
        s = self.session
        s.assert_identity()
        raw = s.read_block(self.root + 0x6e8, 0x22)
        if raw[32] == 0:
            return None
        if raw[32] != 1:
            raise ValueError('Login error flag is invalid')
        length, capacity = struct.unpack_from('<QQ', raw, 16)
        if not 1 <= length <= 512 or not length <= capacity <= 4096:
            raise ValueError('Login error text bounds are invalid')
        data = (raw[:length] if capacity <= 15 else s.read_block(
            checked_address(struct.unpack_from('<Q', raw)[0], length), length))
        message = data.decode('utf-8').strip()
        if message != 'Error: Connection with the server is interrupted. Please re-login.':
            raise ValueError('Unrecognized login error requires attention')
        window = self.gui.read('##ErrorModal')
        if window.scroll != (0., 0.):
            raise ValueError('Login error geometry changed')
        # The qualified render function ends with a full-width OK button.
        # ImGui DC retains its previous-line Y/height and end X after rendering.
        dc = s.read_block(window.address + 0xe0, 0x38)
        end_x, top = struct.unpack_from('<2f', dc, 8)
        left = struct.unpack_from('<f', dc, 16)[0]
        height = struct.unpack_from('<f', dc, 0x34)[0]
        x, y = window.position
        import math
        if (not all(math.isfinite(v) for v in (left,end_x,top,height,*window.position,*window.size))
                or left!=x+8 or end_x!=x+window.size[0]-8 or height!=18.
                or not y+20<=top<top+height<=y+window.size[1]-8 or end_x-left<32):
            raise ValueError('Login error OK button layout changed')
        if (s.read_block(self.root+0x6e8, 0x22) != raw
                or s.read_block(window.address+0xe0, 0x38) != dc
                or self.gui.read('##ErrorModal') != window):
            raise ValueError('Login error changed during observation')
        return (round((left+end_x)/2), round(top+height/2))


def dismiss_login_error(target, session):
    from conquest.desktop_runtime import physical_coordinates
    from conquest.foreground import foreground_click
    reader = LoginErrorReader(session)
    from conquest.viewport import size_for
    viewport=size_for(session)
    with physical_coordinates():
        if not login_screen(target.hwnd):
            raise CaptureUnavailable('Client left login before dialog check')
        point = reader.read()
        if point is None:
            return False
        size = target.snapshot()['client_size']
        if size_for(session)!=viewport:raise ValueError('Login viewport changed')
        if not login_screen(target.hwnd) or reader.read() != point:
            raise CaptureUnavailable('Login dialog changed before dismissal')
        foreground_click(target, round(point[0]*size[0]/viewport[0]),
                         round(point[1]*size[1]/viewport[1]), size)
        for _ in range(20):
            time.sleep(.05)
            if not login_screen(target.hwnd) or reader.read() is None:
                return True
        raise CaptureUnavailable('Waiting for login error dialog to close')

def login_form_points(session, window):
    """Pinned ImGui renderer: labels, two fields, server, checkbox, button, footer."""
    import hashlib
    import struct
    from conquest.memory_shop import MemoryGui
    base=MemoryGui(session).base
    for rva,size,digest in (
        (0xe67f0,211,'2eeddcb3c87af4385b8b65fa1c77f8ebfa9c6da5bd18d6688e110eef6e192006'),
        (0xe6b8a,36,'a414f6338c9dd8a7c33d0f7f64befc0d1484414dbca291b80f6efce3a676c298')):
        if hashlib.sha256(session.read_block(base+rva,size)).hexdigest()!=digest:
            raise ValueError('Login renderer changed')
    x,y=window.position
    dc=struct.unpack('<14f',session.read_block(window.address+0xe0,0x38))
    if (window.size!=(208.,186.) or window.scroll!=(0.,0.)
            or (dc[0],dc[1],dc[3],dc[4],dc[5],dc[6],dc[7],dc[13])
                !=(x+8,y+182,y+166,x+8,y+8,x+200,y+178,12.)):
        raise ValueError('Login form layout changed')
    return ((round(x+104),round(y+33)),(round(x+104),round(y+71)),
            (round(x+104),round(y+153)))


@coordinated_input
def submit_login(target,credential_path=Path(state_path('.runtime/account.dpapi')), *, session=None):
    from conquest.desktop_runtime import physical_coordinates
    from conquest.foreground import foreground_click
    if not login_screen(target.hwnd):
        raise CaptureUnavailable('Client is not at its qualified login screen')
    if session is None:
        raise ValueError('Memory session is required to check login dialogs')
    from conquest.focus_recovery import activate_client
    session.assert_identity()
    if not activate_client(target.hwnd,session.identity):
        raise CaptureUnavailable('Login client did not receive verified focus; no credentials entered')
    dismiss_login_error(target, session)
    from conquest.memory_shop import MemoryGui
    from conquest.viewport import size_for
    viewport=size_for(session)
    gui=MemoryGui(session);window=gui.read('Login')
    points=login_form_points(session,window)
    account=load_credentials(credential_path)
    with physical_coordinates():
        before=target.snapshot()
        size=before['client_size']
        def click(point):
            if not login_screen(target.hwnd):
                raise CaptureUnavailable('Client left login before input')
            if (size_for(session)!=viewport or gui.read('Login')!=window
                    or login_form_points(session,window)!=points):
                raise CaptureUnavailable('Login form moved before input')
            foreground_click(target,round(point[0]*size[0]/viewport[0]),round(point[1]*size[1]/viewport[1]),size)
        click(points[0])
        type_login_field(target,account['username'])
        click(points[1])
        type_login_field(target,account['password'])
        click(points[2])
    return {'submitted':True}  # Never include credential text or key events.


class Reconnector:
    def __init__(self,submit,notify,*,clock=time.monotonic):
        self.submit,self.notify,self.clock=submit,notify,clock
        self.since=None
        self.next_attempt=0
        self.attempts=0
        self.state='connected'
        self.failures=0

    def retry(self):
        self.since=None
        self.next_attempt=0
        self.attempts=0
        self.failures=0
        self.state='connected'

    def step(self,at_login):
        now=self.clock()
        if not at_login:
            if self.state!='connected':
                self.notify({'state':'connection_restored','attempts':self.attempts})
            self.since=None
            self.attempts=0
            self.failures=0
            self.state='connected'
            return
        if self.since is None:
            self.since=now
            self.next_attempt=now+1
            self.state='disconnected'
            self.notify({'state':'disconnected'})
        if now<self.next_attempt:
            return
        if self.failures>=3:
            if self.state!='reconnect_exhausted':
                self.state='reconnect_exhausted'
                self.notify({'state':self.state,'attempt':self.attempts})
            return
        try:
            self.submit()
        except CaptureUnavailable:
            self.next_attempt=now+1
            if self.state!='waiting_for_login_input':
                self.state='waiting_for_login_input'
                self.notify({'state':self.state,'attempt':self.attempts})
            return
        except (OSError,ValueError):
            self.state='reconnect_failed'
            self.next_attempt=now+15
            self.attempts+=1
            self.failures+=1
            self.notify({'state':self.state,'attempt':self.attempts})
            return
        self.attempts+=1
        self.failures=0
        self.state='login_submitted'
        self.next_attempt=now+(15,30,60)[min(self.attempts-1,2)]
        self.notify({'state':self.state,'attempt':self.attempts})
