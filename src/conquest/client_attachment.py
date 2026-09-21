"""Native host diagnostics and geometry, separated from behavior readiness."""
from pathlib import Path
import json
import time
from conquest.character_profiles import write_json

STAGES=('discovery','identity','access','attachment','memory','behavior')


class ViewportTooSmall(ValueError):
    pass


def require_viewport(width,height,minimum=(1036,793)):
    if width<minimum[0] or height<minimum[1]:
        raise ViewportTooSmall(f'Game pane is {int(width)}×{int(height)}; the supported viewport needs {minimum[0]}×{minimum[1]}. Enlarge the app, hide farmer controls, or use a separate game window.')
    return int(width),int(height)


class AttachmentStatus:
    def __init__(self):
        self.stage='discovery';self.attached=False;self.ready=False;self.error=None;self.evidence={}
        self.history=[]
    def enter(self,stage,**evidence):
        if stage not in STAGES:raise ValueError('Unknown attachment stage')
        self.stage=stage;self.error=None;self.evidence.update(evidence)
        self.history.append({'stage':stage,'at':time.time()})
        self.history=self.history[-24:]
    def fail(self,error):
        # Never serialize arbitrary exception payloads: credential readers may appear in the stack.
        from conquest.memory import UnsupportedClientBuildError
        messages={'discovery':'Select a running client.', 'identity':'Verify the character, server and client build.',
            'access':'Memory access failed. Check whether the game runs as administrator.',
            'attachment':'Window hosting failed. Check the recorded DPI and window geometry.',
            'memory':'The client is attached but memory observations are unavailable.',
            'behavior':'The client is attached; automation setup failed. Check the installation path and recovery data.'}
        self.ready=False
        self.error={'type':type(error).__name__,'winerror':getattr(error,'winerror',None),
            'message':str(error) if isinstance(error,(ViewportTooSmall,UnsupportedClientBuildError)) else messages[self.stage]}
        import traceback
        self.error['frames']=[{'file':Path(f.filename).name,'function':f.name,'line':f.lineno}
                              for f in traceback.extract_tb(error.__traceback__)[-6:]]
        return self.error['message']
    def snapshot(self):return {'stage':self.stage,'attached':self.attached,'automation_ready':self.ready,
        'error':self.error,'evidence':self.evidence,'history':self.history,'checked_at':time.time()}
    def copy_text(self):return json.dumps(self.snapshot(),indent=2)


def find_installation(executable):
    path=Path(executable).resolve()
    if not path.is_file() or path.name.casefold()!='imconquer.exe':raise ValueError('Select a verified ImConquer executable')
    for parent in path.parents:
        if (parent/'ini').is_dir() and (parent/'map').is_dir():return parent
    raise ValueError('Game map and ini directories were not found beside the client installation')


def remember_installation(context,executable):
    root=find_installation(executable)
    if context:
        path=context.root/'machine.json';value=json.loads(path.read_text()) if path.exists() else {}
        value.setdefault('installations',{})[context.profile.id]=str(root)
        write_json(path,value)
        from conquest.character_context import _context
        _context.cache_clear()
    return root


def fit_geometry(width,height,x,y,work_areas):
    """Clamp a remembered wrapper to an existing monitor's logical work area."""
    if not work_areas:raise ValueError('No monitor work area is available')
    area=max(work_areas,key=lambda a:max(0,min(x+width,a[2])-max(x,a[0]))*max(0,min(y+height,a[3])-max(y,a[1])))
    left,top,right,bottom=area
    width=min(max(width,640),right-left);height=min(max(height,480),bottom-top)
    return width,height,max(left,min(x,right-width)),max(top,min(y,bottom-height))


def available_work_areas():
    import win32api
    return [win32api.GetMonitorInfo(monitor)['Work'] for monitor,_,_ in win32api.EnumDisplayMonitors()]


def memory_access(pid,expected_sha256):
    from conquest.memory import MemorySession
    with MemorySession(pid,expected_sha256):
        pass


def verify_observer(context,observer):
    """Reuse pinned identity readers without deriving gameplay capabilities."""
    from conquest.memory_life import read_life
    from conquest.merchants.memory import GuiReader, character_uid
    session=observer.adapter
    life=read_life(session,observer.health_layout,context.profile.name)
    base=GuiReader(session).base
    server=session.read_block(base+0x697860,64).split(b'\0')[0]
    if server!=b'Classic_US':raise ValueError('This engine supports the qualified America client only')
    uid=character_uid(session,base,life.object_address)
    evidence={'character':context.profile.name,'server':'America','character_uid':uid}
    context.verify(evidence)
    session.assert_identity()
    if context.profile.character_uid is None:
        from conquest.character_profiles import ProfileRegistry
        ProfileRegistry(context.root).bind(context.profile.id,context.profile.name,'America',uid)
    return evidence
