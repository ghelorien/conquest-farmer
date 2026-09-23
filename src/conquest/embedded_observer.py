"""Memory observations live alongside the hosted client; no worker launcher."""
from dataclasses import asdict
from types import SimpleNamespace
import time
import threading

from conquest.memory import MemorySession
from conquest.memory_health import MemoryHealthReader
from conquest.memory_entities import MemoryEntityReader
from conquest.worker import Operations


class EmbeddedObserver:
    def __init__(self, pid, hwnd, health_layout, entity_layout, character, *, context=None):
        self.lock = threading.RLock()
        self.character_context=context
        self.bridge = None
        from conquest.memory_build_layout import CLIENT_SHA256_1078
        self.read_only_build=health_layout.player.expected_sha256==CLIENT_SHA256_1078
        # The exact 1078 readers have now been live-qualified.  Input remains
        # bounded by the existing foreground, identity and transaction guards.
        self.automation_ready_build=self.read_only_build
        if health_layout.player.expected_sha256 != entity_layout.expected_sha256:
            raise ValueError('Health and entity profiles describe different clients')
        self.session = MemorySession(pid, health_layout.player.expected_sha256).__enter__()
        try:
            self.operations = Operations(self.session, hwnd, read_only=not self.automation_ready_build)
            adapter = SimpleNamespace(expected_sha256=self.session.expected_sha256,
                identity=self.session.identity, modules=self.session.modules,
                read=self.session.read, read_block=self.session.read,
                assert_identity=self.session.assert_identity,
                request=lambda operation, body=None: self.operations.dispatch(operation,body or {}))
            from conquest.viewport import logical_client_size
            adapter.viewport_size=lambda:logical_client_size(hwnd)
            self.health = MemoryHealthReader(adapter, health_layout, character)
            self.entities = MemoryEntityReader(adapter, entity_layout)
            self.adapter,self.health_layout,self.character=adapter,health_layout,character
        except Exception:
            self.session.close()
            raise

    def __call__(self):
        with self.lock:
            if self.read_only_build:
                if self.character_context:
                    from conquest.client_attachment import verify_observer
                    verify_observer(self.character_context,self)
                return self._observe()
            if self.character_context:
                from conquest.reconnect import login_screen
                if not login_screen(self.operations.target.hwnd):
                    from conquest.client_attachment import verify_observer
                    from conquest.character_profiles import context_for
                    context=context_for(self.character_context.profile.id,self.character_context.root)
                    verify_observer(context,self)
            return self._observe()

    def read_life(self):
        if self.read_only_build:
            from conquest.memory_life import MemoryLifeReader
            return MemoryLifeReader.for_session(self.adapter,self.character).read()
        from conquest.memory_life import read_life
        return read_life(self.adapter,self.health_layout,self.character)

    def _observe(self):
        import win32gui
        from conquest.memory_life import read_life, MemoryLifeReader
        from conquest.reconnect import login_screen
        if self.read_only_build:
            life=self.read_life()
            writable_bridge=bool(self.bridge is not None and not self.bridge.read_only)
            try:
                entities = self.entities.read()
                monsters=[asdict(monster) for monster in entities.monsters]
                available,note=True,'Connected to exact-build read-only client'
            except ValueError as error:
                monsters,available,note=[],False,str(error)
            window=self.operations.target.snapshot();root=win32gui.GetAncestor(window['hwnd'],2)
            return {'monsters':monsters,'observations_available':available,'observation_note':note,
                'hp_candidate':life.current_hp,'max_hp_candidate':life.max_hp,
                'life':{**asdict(life),'dead_candidate':life.dead_candidate},
                'focused':window['foreground']==root and bool(win32gui.IsWindowVisible(window['hwnd'])),
                'minimized':bool(win32gui.IsIconic(root)) or not bool(win32gui.IsWindowVisible(window['hwnd'])),
                'observed_at':time.time(),'read_only_worker':not writable_bridge,
                'blockers':([] if writable_bridge else
                    ['This client version supports observation only; farming and input are disabled'])}
        if login_screen(self.operations.target.hwnd):
            return {'monsters':[],'observations_available':False,
                'observation_note':'Disconnected; reconnecting before reading player stats',
                'connection_state':'login','focused':False,'minimized':False,'observed_at':time.time(),
                'read_only_worker':True,'blockers':['Client is disconnected']}
        life=read_life(self.adapter,self.health_layout,self.character)
        try:
            entities = self.entities.read()
            monsters=[asdict(monster) for monster in entities.monsters]
            available,note=True,'Connected to embedded client'
        except ValueError as error:
            monsters,available,note=[],False,str(error)
        window = self.operations.target.snapshot()
        root = win32gui.GetAncestor(window['hwnd'], 2)
        return {'monsters':monsters,
            'observations_available':available, 'observation_note':note,
            'hp_candidate':life.current_hp, 'max_hp_candidate':life.max_hp,
            'life':{**asdict(life),'dead_candidate':life.dead_candidate},
            'focused':window['foreground'] == root and bool(win32gui.IsWindowVisible(window['hwnd'])),
            'minimized':bool(win32gui.IsIconic(root)) or not bool(win32gui.IsWindowVisible(window['hwnd'])), 'observed_at':time.time(),
            'read_only_worker':True,
            'blockers':['Monster life state and kill-linked loot need live validation',
                        'Input for the embedded client has not been verified']}

    def sample_npcs(self):
        from conquest.memory_life import read_life
        from conquest.memory_npcs import MemoryNpcReader
        from conquest.reconnect import login_screen
        with self.lock:
            if login_screen(self.operations.target.hwnd):
                raise ValueError('Reconnect before reading vendors or player stats')
            before = self.read_life()
            if before.dead_candidate:
                raise ValueError('Revive before interacting with town vendors')
            result = MemoryNpcReader(self.entities).read(before.map_id)
            if login_screen(self.operations.target.hwnd):
                raise ValueError('Client disconnected during vendor sampling')
            after = self.read_life()
            if (after.dead_candidate or after.map_id != before.map_id
                    or after.object_address != before.object_address):
                raise ValueError('Player or map changed during vendor sampling')
            if time.monotonic()-result.started_at > .5:
                raise ValueError('Vendor observation expired')
            return {'source':'read_only_memory', 'shop_items_qualified':False,
                    'process_identity':self.session.identity, 'snapshot':asdict(result)}

    def start_bridge(self,path,snapshot,**callbacks):
        if self.read_only_build and not self.automation_ready_build:
            raise ValueError('Writable bridge is unavailable for this client version')
        from conquest.embedded_bridge import EmbeddedBridge
        from conquest.town_trade import TownTrade
        self.town_trade = TownTrade(self)
        self.bridge = EmbeddedBridge(Operations(self.session,self.operations.target.hwnd,read_only=False),
            self.lock,path,snapshot,lifetime=None,on_sample_npcs=self.sample_npcs,on_town=self.town_trade,**callbacks)

    def start_read_only_bridge(self,path,snapshot):
        if not self.read_only_build:
            raise ValueError('Read-only bridge is only used for the exact-build observer')
        from conquest.embedded_bridge import EmbeddedBridge
        self.bridge=EmbeddedBridge(self.operations,self.lock,path,snapshot,lifetime=None,read_only=True)

    def close(self):
        if self.bridge:
            self.bridge.close()
            self.bridge = None
        with self.lock:
            self.session.close()
