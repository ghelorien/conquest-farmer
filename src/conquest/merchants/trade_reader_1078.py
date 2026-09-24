"""Canonical 1078 trade reads, pinned to observed loaded renderer code.

Read-only evidence captured through the authenticated farmer worker on
2026-09-23, process 18532 / creation 134345064188672222. Code identity proves
field semantics; it does not qualify clicks or certify a completed trade.
"""
from dataclasses import asdict
import hashlib
import time

from conquest.merchants.reader_1078 import (
    CLIENT_SHA256_1078, TradeObservationReader1078, ObservationUnavailable1078,
)


# Rendered own/other grids and silver, Accept Trade, and actor+FA4's
# Accepted/Waiting branch. The model's byte +99 is not that renderer's source.
CODE_BLOCKS = (
    (0x113740, 4912, 'ce09814f3180a2aca6a676ba1f7249a684b99761fa812619c7250a8535d8f070'),
    (0x114A70, 1168, '0b900e1653f054d24670287c9b66576ee861fe4ab597db83deb357841ffd8b91'),
    (0x96FE0, 448, '52650044a0af66913b84484e261b6248e2b22272dc90e8d8500702282dfa490b'),
)


def assert_trade_code(session):
    if session.expected_sha256 != CLIENT_SHA256_1078:
        raise ObservationUnavailable1078('1078 trade requires its exact executable fingerprint')
    modules=[m for m in session.modules if m['name'].casefold()=='imconquer.exe']
    if len(modules)!=1:
        raise ObservationUnavailable1078('Expected one 1078 trade module')
    module=modules[0]
    read=getattr(session,'read_block',None) or session.read
    session.assert_identity()
    for rva,size,digest in CODE_BLOCKS:
        if rva+size>module['size'] or hashlib.sha256(read(module['base']+rva,size)).hexdigest()!=digest:
            raise ObservationUnavailable1078('1078 loaded trade renderer differs from its field proof')
    session.assert_identity()
    return module['base']


def _silver(text,*,accepted=False,locked=False):
    # Renderer 114138..114162 sets +98, replaces untouched +78 with this
    # exact string, then locks +54. Never infer zero from the wallet balance.
    if text=='0 \u2714' and accepted and locked:return 0
    if text and text.isascii() and text.isdecimal():
        value=int(text)
        if 0<=value<=2_147_483_647:return value
    raise ObservationUnavailable1078('1078 trade silver is not a proved numeric value')


class TradeMemory1078(TradeObservationReader1078):
    """Observation adapter only; input still requires separate qualification."""

    def __init__(self,observer,*,definitions=None):
        super().__init__(observer.adapter,observer.character)
        from conquest.merchants.memory import GuiReader
        self.observer=observer
        self.s=self.session
        self.gui=GuiReader.for_session(self.session)
        self.definitions=definitions or {}

    def read_manual_ownership(self):
        assert_trade_code(self.session)
        snapshot=super().read_manual_ownership()
        snapshot['reader_build']='1078-canonical-trade'
        return snapshot

    def item(self,pointer,slot,booth=False):
        """Decode a warehouse-owned item with the same exact-build stock fields.

        MemoryWarehouseReader retains ownership of the deque and compares this
        richer record against its independently decoded UID/type/amount fields.
        This decoder supplies no input or withdrawal qualification.
        """
        from conquest.merchants.memory import StockItem
        self.session.assert_identity()
        item=self._item(pointer,slot,booth=booth)
        self.session.assert_identity()
        return StockItem(**asdict(item),category=self.definitions.get(item.type_id))

    def _trade(self,actual):
        model=self._model(14,self.trade_vtable_rva)
        raw=self._read(model,0x9A)
        if raw[12] not in (0,1):
            raise ObservationUnavailable1078('Invalid 1078 trade visibility')
        if not raw[12]:return None
        participant=self._string(actual+0xFB0,63)
        participant_uid=self._u32(actual+0xFAC)
        counterpart_accepted=self._u32(actual+0xFA4)
        if (not participant or not participant_uid or counterpart_accepted not in (0,1)
                or raw[0x98] not in (0,1) or raw[0x54] not in (0,1)):
            raise ObservationUnavailable1078('Invalid 1078 trade participant or acceptance flags')
        own,own_header=self._deque(actual+0xF50,20)
        other,other_header=self._deque(actual+0xF78,20)
        own_text=self._string(model+0x78,32)
        other_text=self._string(model+0x58,32)
        result={'participant':participant,'participant_uid':participant_uid,
                'own_items':[asdict(self._item(p,i)) for i,p in enumerate(own)],
                'items':[asdict(self._item(p,i)) for i,p in enumerate(other)],
                'own_silver':_silver(own_text,accepted=bool(raw[0x98]),locked=bool(raw[0x54])),
                'other_silver':_silver(other_text),
                'accepted':bool(raw[0x98]),'other_accepted':bool(counterpart_accepted)}
        if (self._read(model,0x9A)!=raw
                or self._read(actual+0xF50,32)!=own_header
                or self._read(actual+0xF78,32)!=other_header
                or self._u32(actual+0xFA4)!=counterpart_accepted
                or self._u32(actual+0xFAC)!=participant_uid
                or self._string(actual+0xFB0,63)!=participant
                or self._string(model+0x78,32)!=own_text
                or self._string(model+0x58,32)!=other_text):
            raise ObservationUnavailable1078('1078 trade changed during canonical observation')
        return result

    def read(self,*,max_seconds=3,recovery=False,farmer_preflight=False):
        from conquest.memory_life import MemoryLifeReader
        from conquest.character_context import farmer_name
        started=time.monotonic()
        assert_trade_code(self.session)
        life=MemoryLifeReader.for_session(self.session,self.character).read()
        maps=(1002,1036) if recovery else (1036,)
        if farmer_preflight:
            if self.character!=farmer_name():
                raise ObservationUnavailable1078('Town trade preflight is restricted to the farmer')
            maps=(1002,1011,1036)
        if life.dead_candidate or life.current_hp<=0 or life.map_id not in maps:
            raise ObservationUnavailable1078('1078 trade observation needs a living character in its allowed town')
        snapshot=self.read_manual_ownership()
        windows=self.gui.windows()
        # Re-read complete offers and requests after renderer traversal. Open
        # modals must not change their participant, silver, acceptance or items.
        if (self._trade(life.object_address)!=snapshot['trade']
                or self._request(life.object_address)!=snapshot['request']):
            raise ObservationUnavailable1078('1078 trade changed while reading rendered windows')
        fresh=MemoryLifeReader.for_session(self.session,self.character).read()
        if (fresh.object_address!=life.object_address or fresh.map_id!=life.map_id
                or fresh.position!=life.position or fresh.dead_candidate or fresh.current_hp<=0
                or snapshot['position']!=list(fresh.position)
                or snapshot['map_id']!=fresh.map_id
                or time.monotonic()-started>max_seconds):
            raise ObservationUnavailable1078('1078 trade observation expired or changed identity/location')
        stock=snapshot['inventory']+snapshot['booth']
        free=snapshot['capacity']-len(stock)
        if free<0:raise ObservationUnavailable1078('1078 combined owned stock exceeds capacity')
        for item in stock:
            item['category']=self.definitions.get(item['type_id'])
        if snapshot['trade']:
            for item in snapshot['trade']['own_items']+snapshot['trade']['items']:
                item['category']=self.definitions.get(item['type_id'])
        assert_trade_code(self.session)
        snapshot.update(windows=windows,hp=fresh.current_hp,owned_free_slots=free,
                        source='read_only_memory',client_sha256=CLIENT_SHA256_1078,
                        reader_build='1078-canonical-trade',observation_only=True,
                        capabilities={'read_inventory':True,'read_owned_booth':True,
                                      'read_capacity':True,'read_trade':True,
                                      'read_trade_request':True,'automatic_input':False})
        return snapshot


def manual_ownership(session,character):
    """Canonical modal evidence for manual fences, on any observed map."""
    from types import SimpleNamespace
    if not hasattr(session,'read_block'):
        session=SimpleNamespace(expected_sha256=session.expected_sha256,
            modules=session.modules,identity=session.identity,read=session.read,
            read_block=session.read,assert_identity=session.assert_identity,
            viewport_size=getattr(session,'viewport_size',None))
    observer=SimpleNamespace(adapter=session,character=character)
    return TradeMemory1078(observer).read_manual_ownership()
