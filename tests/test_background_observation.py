import struct

import pytest

from conquest.memory_life import CLIENT_SHA256
from conquest.merchants.background_observation import BackgroundObservationReader
from conquest.merchants.memory import GuiObservationChanged


BASE, CONTEXT, BACKEND, EVENTS = 0x140000000, 0x200000, 0x300000, 0x400000


class Session:
    expected_sha256 = CLIENT_SHA256
    modules = [{'name': 'ImConquer.exe', 'base': BASE}]

    def __init__(self, events=()):
        self.identity = {'pid': 123, 'creation_time': 456}
        self.memory = {}
        self.calls = []
        self.identity_checks = 0
        self.hook = None
        self.put(BASE + 0x6966f0, '<Q', CONTEXT)
        self.put(CONTEXT + 0x90, '<Q', BACKEND)
        self.put(CONTEXT + 0x3e38, '<I', 12)
        self.put(BACKEND, '<QQB3xI', 111, 111, 1, 0)
        self.put(CONTEXT + 0x37c1, '<B', 1)
        self.put(CONTEXT + 0xd9c, '<5B', 0, 0, 0, 0, 0)
        self.put(CONTEXT + 0xd0, '<B', 1)
        self.put(CONTEXT + 0x3ec0, '<Q', 0x500000)
        self.put(CONTEXT + 0x3ef0, '<I', 999)
        self.put(CONTEXT + 8, '<I', 32)
        self.put(CONTEXT + 0xd94, '<ff', 120, 300)
        self.put(CONTEXT + 0xdac, '<4B', 1, 0, 1, 0)
        self.put(CONTEXT + 0xe00, '<I', 5)
        self.put(CONTEXT + 0x3f04, '<I', 1234)
        self.put(CONTEXT + 0x3f20, '<Q', 0x500000)
        self.memory[CONTEXT + 0x425c] = drag_state(active=0, frame=-1, payload_type=b'', size=0, address=0)
        self.put(CONTEXT + 0x37d8, '<iiQ', len(events), 8, EVENTS)
        self.memory[EVENTS] = b''.join(events)

    def put(self, address, fmt, *values):
        self.memory[address] = struct.pack(fmt, *values)

    def read_block(self, address, size):
        self.calls.append((address, size))
        if self.hook:
            self.hook(address, size)
        return self.memory[address][:size]

    def assert_identity(self):
        self.identity_checks += 1


def event(kind, source=1, payload=b''):
    return (struct.pack('<II', kind, source) + payload).ljust(24, b'\0')


def drag_state(*, active=1, frame=12, payload_type=b'CQITEM', size=4,
               address=CONTEXT + 0x42f0, preview=0, delivery=0):
    raw = bytearray(0x50)
    raw[0] = active
    struct.pack_into('<Iii', raw, 4, 0, frame, 0)
    struct.pack_into('<QiIIi', raw, 0x14, address, size, 999, 444, frame)
    raw[0x2c:0x2c + len(payload_type)] = payload_type
    raw[0x4d:0x4f] = bytes((preview, delivery))
    return bytes(raw)


def test_mouse_queue_and_backend_are_read_only_and_serializable():
    session = Session([event(1, payload=struct.pack('<ff', 120, 300)),
                       event(3, payload=struct.pack('<IB', 0, 1)),
                       event(1, payload=struct.pack('<ff', -3.402823466e38, -3.402823466e38)),
                       event(2, payload=struct.pack('<ff', 0, -1))])
    result = BackgroundObservationReader(session).snapshot()
    assert result['frame'] == 12
    assert result['identity'] == session.identity
    assert result['identity'] is not session.identity
    assert result['backend']['mouse_tracked'] is True
    assert result['hover'] == {'window': 0x500000, 'id': 999}
    assert result['queue']['events'][0]['position_valid'] is True
    assert result['queue']['events'][1]['down'] is True
    assert result['queue']['events'][2]['position_valid'] is False
    assert result['queue']['events'][3]['y'] == -1
    assert session.identity_checks == 2
    assert result['mouse_position'] == [120, 300]
    assert result['mouse_position_valid'] is True
    assert result['modifiers'] == {'ctrl': True, 'shift': False, 'alt': True, 'super': False}
    assert result['key_mods'] == 5
    assert result['config_flags'] == 32
    assert result['active'] == {'id': 1234, 'window': 0x500000}


def test_text_and_key_payloads_are_not_exposed():
    session = Session([event(5, 2, b'SECRET!'), event(4, 2, b'PASSWORD')])
    result = BackgroundObservationReader(session).snapshot()
    assert result['queue']['events'] == [{'type': 'text', 'source': 2}, {'type': 'key', 'source': 2}]
    assert 'SECRET' not in str(result) and 'PASSWORD' not in str(result)


@pytest.mark.parametrize('count,capacity,address', [(-1, 8, EVENTS), (9, 8, EVENTS),
    (257, 300, EVENTS), (1, 4097, EVENTS), (0, 0, EVENTS), (1, 8, 0)])
def test_bad_queue_bounds_never_read_event_memory(count, capacity, address):
    session = Session()
    session.put(CONTEXT + 0x37d8, '<iiQ', count, capacity, address)
    with pytest.raises(ValueError):
        BackgroundObservationReader(session).snapshot()
    assert not any(where == EVENTS for where, size in session.calls)


def test_empty_zero_capacity_vector():
    session = Session()
    session.put(CONTEXT + 0x37d8, '<iiQ', 0, 0, 0)
    assert BackgroundObservationReader(session).snapshot()['queue']['events'] == []


@pytest.mark.parametrize('bad_event', [event(1, payload=struct.pack('<ff', float('nan'), 1)),
    event(2, payload=struct.pack('<ff', 1, float('inf'))), event(7), event(1, 999),
    event(3, payload=struct.pack('<IB', 5, 1)), event(3, payload=struct.pack('<IB', 0, 2)),
    event(6, 0, b'\x02')])
def test_malformed_events_rejected(bad_event):
    with pytest.raises(ValueError):
        BackgroundObservationReader(Session([bad_event])).snapshot()


def test_frame_crossing_retries_boundedly():
    session = Session()
    frame_reads = 0
    def race(address, size):
        nonlocal frame_reads
        if address == CONTEXT + 0x3e38:
            frame_reads += 1
            if frame_reads == 2:
                session.put(address, '<I', 13)
    session.hook = race
    assert BackgroundObservationReader(session).snapshot()['frame'] == 13
    assert session.identity_checks == 4


def test_unstable_backend_cannot_produce_mixed_sample():
    session = Session()
    reads = 0
    def race(address, size):
        nonlocal reads
        if address == BACKEND:
            reads += 1
            session.put(BACKEND, '<QQB3xI', 111, 111, reads % 2, 0)
    session.hook = race
    with pytest.raises(GuiObservationChanged):
        BackgroundObservationReader(session).snapshot(attempts=2)
    assert reads == 4
    assert session.identity_checks == 4


def test_same_size_queue_content_race_is_detected():
    session = Session([event(1, payload=struct.pack('<ff', 1, 1))])
    reads = 0
    def race(address, size):
        nonlocal reads
        if address == EVENTS:
            reads += 1
            session.memory[EVENTS] = event(1, payload=struct.pack('<ff', reads, 1))
    session.hook = race
    with pytest.raises(GuiObservationChanged):
        BackgroundObservationReader(session).snapshot(attempts=1)


def test_identity_change_is_not_retried():
    session = Session()
    def identity():
        session.identity_checks += 1
        if session.identity_checks == 2:
            session.identity['creation_time'] += 1
    session.assert_identity = identity
    with pytest.raises(ValueError, match='identity changed'):
        BackgroundObservationReader(session).snapshot()
    assert session.identity_checks == 2


def test_fingerprint_pin_and_attempt_bounds():
    session = Session()
    session.expected_sha256 = 'unknown'
    with pytest.raises(ValueError, match='fingerprint'):
        BackgroundObservationReader(session)
    for attempts in (0, 6, True, 1.5):
        with pytest.raises(ValueError, match='attempts'):
            BackgroundObservationReader(Session()).snapshot(attempts=attempts)


def test_short_read_is_rejected():
    session = Session()
    session.memory[BACKEND] = b'\x00'
    with pytest.raises(ValueError, match='Incomplete'):
        BackgroundObservationReader(session).snapshot()


def test_invalid_position_sentinel_and_no_active_window_are_valid_observations():
    session = Session()
    session.put(CONTEXT + 0xd94, '<ff', -3.402823466e38, -3.402823466e38)
    session.put(CONTEXT + 0x3f04, '<I', 0)
    session.put(CONTEXT + 0x3f20, '<Q', 0)
    result = BackgroundObservationReader(session).snapshot()
    assert result['mouse_position'][0] < -1e38
    assert result['mouse_position_valid'] is False
    assert result['active'] == {'id': 0, 'window': 0}


def test_processed_mouse_state_is_distinct_from_queued_backend_mask():
    session = Session()
    session.put(CONTEXT + 0xd9c, '<5B', 1, 0, 0, 0, 0)
    result = BackgroundObservationReader(session).snapshot()
    assert result['mouse_down'] == [True, False, False, False, False]
    assert result['want_capture_mouse'] is True
    assert result['backend']['buttons_down'] == 0


@pytest.mark.parametrize('offset,fmt,values', [
    (0xd94, '<ff', (float('nan'), 1)), (0xd94, '<ff', (0, float('inf'))),
    (0xdac, '<4B', (0, 2, 0, 0)), (0xe00, '<I', (16,)),
    (0xd9c, '<5B', (0, 0, 2, 0, 0)), (0xd0, '<B', (2,)),
    (0x3f20, '<Q', (1,))])
def test_bad_processed_input_state_rejected(offset, fmt, values):
    session = Session()
    session.put(CONTEXT + offset, fmt, *values)
    with pytest.raises(ValueError):
        BackgroundObservationReader(session).snapshot()


@pytest.mark.parametrize('offset,fmt,values', [
    (8, '<I', (64,)), (0xd94, '<ff', (40, 50)),
    (0xdac, '<4B', (0, 0, 1, 0)), (0xe00, '<I', (4,)),
    (0xd9c, '<5B', (1, 0, 0, 0, 0)), (0xd0, '<B', (0,)),
    (0x3f04, '<I', (4321,)), (0x3f20, '<Q', (0x600000,))])
def test_processed_state_content_races_rejected(offset, fmt, values):
    session = Session()
    reads = 0
    def race(address, size):
        nonlocal reads
        if address == CONTEXT + offset:
            reads += 1
            if reads == 2:
                session.put(address, fmt, *values)
    session.hook = race
    with pytest.raises(GuiObservationChanged):
        BackgroundObservationReader(session).snapshot(attempts=1)


def test_drag_payload_uid_and_actual_preview_delivery_offsets():
    session = Session()
    session.memory[CONTEXT + 0x425c] = drag_state(preview=1, delivery=1)
    session.put(CONTEXT + 0x42f0, '<I', 293205390)
    result = BackgroundObservationReader(session).snapshot()['drag']
    assert result['active'] is True
    assert result['preview'] is True and result['delivery'] is True
    assert result['source_item_uid'] == 293205390
    assert result['source_id'] == 999 and result['source_parent_id'] == 444
    assert result['payload_type'] == 'CQITEM' and result['data_frame'] == 12


def test_unknown_drag_payload_is_never_dereferenced_or_decoded_as_item():
    session = Session()
    session.memory[CONTEXT + 0x425c] = drag_state(payload_type=b'CQITEMTYPEID', address=0x900000)
    result = BackgroundObservationReader(session).snapshot()['drag']
    assert result['source_item_uid'] is None
    assert all(address != 0x900000 for address, _ in session.calls)


@pytest.mark.parametrize('changes', [dict(size=8), dict(address=0x900000),
    dict(address=0), dict(size=-1), dict(size=4097), dict(frame=-2),
    dict(preview=2), dict(delivery=2), dict(active=2),
    dict(payload_type=b'A' * 33), dict(payload_type=b'BAD\x01TYPE')])
def test_malformed_drag_rejected_before_payload_read(changes):
    session = Session()
    session.memory[CONTEXT + 0x425c] = drag_state(**changes)
    with pytest.raises(ValueError):
        BackgroundObservationReader(session).snapshot()
    assert all(address != CONTEXT + 0x42f0 for address, _ in session.calls)


@pytest.mark.parametrize('target', ['header', 'uid'])
def test_drag_content_race_invalidates_entire_observation(target):
    session = Session()
    session.memory[CONTEXT + 0x425c] = drag_state()
    session.put(CONTEXT + 0x42f0, '<I', 123)
    reads = 0
    address_to_change = CONTEXT + (0x425c if target == 'header' else 0x42f0)
    def race(address, size):
        nonlocal reads
        if address == address_to_change:
            reads += 1
            if reads == 2:
                if target == 'header':
                    session.memory[address] = drag_state(preview=1)
                else:
                    session.put(address, '<I', 456)
    session.hook = race
    with pytest.raises(GuiObservationChanged):
        BackgroundObservationReader(session).snapshot(attempts=1)
