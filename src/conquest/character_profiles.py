"""Portable preferences and machine-local identities; no gameplay policy."""
from contextlib import contextmanager
from dataclasses import dataclass, field, asdict
from pathlib import Path
import copy
import json
import os
import uuid

SCHEMA = 1
ROLES = ('Farmer', 'Merchant')
# Only preferences exposed by the existing engines may cross a PC boundary.
SETTING_TYPES = {'heal_below':float, 'healing_enabled':bool, 'potion_cooldown':float,
    'jump_scatter':bool, 'single_isolated_targets':bool, 'kite_when_surrounded':bool,
    'attack_range_tiles':int, 'pickup_enabled':bool, 'recover_after_death':bool}


def data_root():
    value = os.environ.get('CONQUEST_DATA_ROOT')
    if value:
        return Path(value).resolve()
    base = os.environ.get('LOCALAPPDATA')
    if not base:
        raise ValueError('LOCALAPPDATA is unavailable; configure a machine-local data directory')
    return Path(base) / 'Conquest'


def write_json(path, value):
    path = Path(path); path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + '.' + uuid.uuid4().hex + '.tmp')
    temporary.write_text(json.dumps(value, indent=2), encoding='utf-8')
    os.replace(temporary, path)


def identifier(value):
    try:
        if str(uuid.UUID(value)) != value:raise ValueError()
    except (ValueError, TypeError, AttributeError):
        raise ValueError('Invalid character profile identifier') from None
    return value


def preferences(values):
    if not isinstance(values,dict) or set(values)-set(SETTING_TYPES):
        raise ValueError('Unknown or non-portable setting')
    for name,value in values.items():
        kind=SETTING_TYPES[name]
        if kind is float:
            import math
            if type(value) not in (int,float) or not math.isfinite(value):raise ValueError('Invalid '+name)
        elif type(value) is not kind:raise ValueError('Invalid '+name)
        if name=='heal_below' and not 0<value<1:raise ValueError('Healing threshold must be between zero and one')
        if name=='potion_cooldown' and not .5<=value<=10:raise ValueError('Invalid potion cooldown')
        if name=='attack_range_tiles' and not 1<=value<=20:raise ValueError('Invalid attack range')
    return copy.deepcopy(values)


@dataclass
class CharacterProfile:
    id: str
    name: str
    server: str = 'America'
    role: str = 'Farmer'
    label: str = ''
    account_id: str = ''
    character_uid: int | None = None
    template: str = 'automatic'
    overrides: dict = field(default_factory=dict)
    trusted_sources: list = field(default_factory=list)
    local_enabled: bool = True

    def __post_init__(self):
        identifier(self.id)
        if not isinstance(self.name,str) or not 1<=len(self.name.strip())<=63 or any(ord(c)<32 for c in self.name):
            raise ValueError('Enter the exact in-game character name')
        if not isinstance(self.server,str) or not 1<=len(self.server)<=64:raise ValueError('Invalid server')
        if self.role not in ROLES:raise ValueError('Unknown role')
        if self.account_id:identifier(self.account_id)
        if self.character_uid is not None and (type(self.character_uid) is not int or self.character_uid<=0):
            raise ValueError('Invalid character UID')
        if not isinstance(self.label,str) or len(self.label)>100:raise ValueError('Invalid display label')
        if self.template!='automatic':identifier(self.template)
        self.overrides=preferences(self.overrides)
        if not isinstance(self.trusted_sources,list):raise ValueError('Invalid trusted sources')
        for source in self.trusted_sources:
            if set(source)!={'name','server','character_uid'} or not isinstance(source['name'],str) or not source['name']:
                raise ValueError('A trusted source needs an exact name, server and verified UID')
            if not isinstance(source['server'],str) or type(source['character_uid']) is not int or source['character_uid']<=0:
                raise ValueError('Trusted source identity has not been verified')
        if type(self.local_enabled) is not bool:raise ValueError('Invalid local activation setting')


class ProfileRegistry:
    def __init__(self, root=None):
        self.root=Path(root) if root is not None else data_root()
        self.path=self.root/'profiles.json'

    def read(self):
        if not self.path.exists():return {'schema_version':SCHEMA,'revision':0,'profiles':[],'templates':{}}
        value=json.loads(self.path.read_text(encoding='utf-8'))
        if value.get('schema_version')!=SCHEMA:raise ValueError('Unsupported character registry version')
        for profile in value['profiles']:CharacterProfile(**profile)
        ids=[p['id'] for p in value['profiles']]
        identities=[(p['server'].casefold(),p['name'].casefold()) for p in value['profiles']]
        if len(ids)!=len(set(ids)) or len(identities)!=len(set(identities)):raise ValueError('Duplicate character identity')
        for key,template in value.get('templates',{}).items():identifier(key);preferences(template['settings'])
        return value

    @contextmanager
    def edit(self):
        self.root.mkdir(parents=True,exist_ok=True)
        import msvcrt
        with (self.root/'profiles.lock').open('a+b') as lock:
            lock.seek(0,2)
            if lock.tell()==0:lock.write(b'0');lock.flush()
            lock.seek(0)
            try:msvcrt.locking(lock.fileno(),msvcrt.LK_NBLCK,1)
            except OSError:raise ValueError('Character settings are being changed in another app') from None
            value=self.read()
            yield value
            value['revision']+=1
            write_json(self.path,value)

    def profiles(self):return [CharacterProfile(**p) for p in self.read()['profiles']]

    def resolve(self, value, *, role=None, server=None):
        matches=[p for p in self.profiles() if (p.id==value or p.name.casefold()==str(value).casefold())
            and (role is None or p.role==role) and (server is None or p.server==server)]
        if len(matches)!=1:raise ValueError('Choose a profile ID; character name is missing or ambiguous')
        return matches[0]

    def add(self,name,server='America',role='Farmer',**kwargs):
        profile=CharacterProfile(id=str(uuid.uuid4()),name=name,server=server,role=role,
            account_id=str(uuid.uuid4()),**kwargs)
        with self.edit() as value:
            if any(p['server'].casefold()==server.casefold() and p['name'].casefold()==name.casefold() for p in value['profiles']):
                raise ValueError('This character already has a profile')
            value['profiles'].append(asdict(profile))
        return profile

    def update(self,profile_id,changes,*,stopped,pending):
        if not stopped or pending:raise ValueError('Stop this character and reconcile unfinished work before editing')
        if set(changes)-{'role','label','template','overrides','trusted_sources','local_enabled'}:
            raise ValueError('Identity cannot be changed through profile preferences')
        with self.edit() as value:
            index=next((i for i,p in enumerate(value['profiles']) if p['id']==profile_id),None)
            if index is None:raise ValueError('Unknown profile ID')
            updated=CharacterProfile(**{**value['profiles'][index],**changes})
            value['profiles'][index]=asdict(updated)
        return updated

    def save_template(self,label,settings):
        if not isinstance(label,str) or not 1<=len(label)<=100:raise ValueError('Enter a template label')
        key=str(uuid.uuid4())
        with self.edit() as value:value['templates'][key]={'label':label,'settings':preferences(settings)}
        return key

    def bind(self,profile_id,name,server,uid):
        with self.edit() as value:
            profile=next(p for p in value['profiles'] if p['id']==profile_id)
            if profile['name']!=name or profile['server']!=server or type(uid) is not int or uid<=0:
                raise ValueError('Observed character/server does not match this profile')
            if profile['character_uid'] not in (None,uid):raise ValueError('Character UID changed; no automatic rebind')
            profile['character_uid']=uid

    def effective(self,profile):
        templates=self.read().get('templates',{})
        if profile.template!='automatic' and profile.template not in templates:raise ValueError('Selected template is unavailable')
        base={} if profile.template=='automatic' else templates[profile.template]['settings']
        return {**preferences(base),**preferences(profile.overrides)}

    def export(self,profile_id):
        p=self.resolve(profile_id)
        # Explicit allowlist: adding a field to the profile never exports it implicitly.
        return {'schema_version':SCHEMA,'kind':'conquest-preferences','role':p.role,
                'label':p.label,'settings':self.effective(p)}

    def import_preferences(self,payload,name,server='America'):
        if (not isinstance(payload,dict) or set(payload)!={'schema_version','kind','role','label','settings'}
                or payload['schema_version']!=SCHEMA or payload['kind']!='conquest-preferences'):
            raise ValueError('Not a portable preferences export')
        return self.add(name,server,payload['role'],label=payload['label'],overrides=preferences(payload['settings']))


@dataclass(frozen=True)
class CharacterContext:
    profile: CharacterProfile
    root: Path
    settings: dict
    installation: Path | None = None

    @property
    def state_dir(self):return self.root/'characters'/self.profile.id
    @property
    def credentials(self):return self.root/'accounts'/self.profile.account_id/'account.dpapi'

    def verify(self,snapshot):
        if snapshot.get('character')!=self.profile.name or snapshot.get('server')!=self.profile.server:
            raise ValueError('Connected client does not match the selected character/server')
        if self.profile.character_uid is not None and snapshot.get('character_uid')!=self.profile.character_uid:
            raise ValueError('Connected character UID does not match the profile')


def context_for(profile_id,root=None):
    registry=ProfileRegistry(root);profile=registry.resolve(profile_id)
    machine=registry.root/'machine.json'
    settings=json.loads(machine.read_text()) if machine.exists() else {}
    installed=settings.get('installations',{}).get(profile.id)
    return CharacterContext(profile,registry.root,registry.effective(profile),Path(installed) if installed else None)
