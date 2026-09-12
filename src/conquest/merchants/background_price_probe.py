"""Opt-in background price-entry/cancel qualification. Never submits a listing.

Caller owns the qualified code/handler barrier, input lease, isolated surface,
and unconditional queue/stock reconciliation. All controls come from live memory.
"""
from copy import deepcopy
import struct
import time
import zlib

from conquest.merchants.background_drag_cancel import _geometry
from conquest.merchants.qualification import modal_controls, stock, window


DIALOG = 'Add Item to Booth'


class _PriceProbe:
    def __init__(self, driver, before, reader, report, sample, check):
        self.driver, self.before, self.reader, self.report = driver, before, reader, report
        self.sample, self.check = sample, check
        self.target, self.session = driver.target, reader.session
        self.identity = deepcopy(self.session.identity)
        self.size = tuple(self.target.snapshot()['client_size'])
        self.viewport = driver.memory.gui.viewport_size()
        self.initial_stock = stock(before)
        self.started = time.monotonic()
        self.deadline = self.started + 9
        self.held = False
        self.release_point = None
        self.item = None
        self.model = driver.memory.gui.model(25, 0x5c27f8)

    def guard(self):
        if time.monotonic() >= self.deadline:
            raise ValueError('Background price diagnostic exceeded its time bound')
        self.check()
        self.session.assert_identity()
        if (self.session.identity != self.identity or tuple(self.target.snapshot()['client_size']) != self.size
                or self.driver.memory.gui.viewport_size() != self.viewport
                or self.driver.memory.gui.model(25, 0x5c27f8) != self.model):
            raise ValueError('Background price diagnostic identity or viewport changed')

    def fresh(self):
        self.guard()
        current = self.driver.read()
        if (stock(current) != self.initial_stock or current['position'] != self.before['position']
                or current.get('request') or current.get('trade')):
            raise ValueError('Background price diagnostic stock, position or trade changed')
        return current

    def read_model(self):
        raw = self.session.read_block(self.model + 0x50, 16)
        if len(raw) != 16:
            raise ValueError('Incomplete price dialog model')
        uid = struct.unpack_from('<I', raw)[0]
        price = raw[4:]
        if b'\0' not in price:
            raise ValueError('Unterminated price dialog value')
        return uid, price.split(b'\0', 1)[0]

    def point(self, logical):
        point = tuple(round(v*n/g) for v,n,g in zip(logical, self.size, self.viewport))
        if any(not 0 <= p < min(n, 32768) for p,n in zip(point, self.size)):
            raise ValueError('Price diagnostic point is outside the client')
        return point

    def burst(self, point, flags=0, button=None):
        self.guard()
        packed = point[0] | point[1] << 16
        self.report['input_messages_sent'] = True
        self.target.post(0x200, flags, packed)
        self.target.post(0x102, 1, 1)
        if button is not None:
            self.report['button_messages_sent'] = True
            if button == 0x201:
                self.held, self.release_point = True, point
            self.target.post(button, flags, packed)
            if button == 0x202:
                self.held = False
        for index in range(12):
            self.guard()
            self.target.post(0x200, flags, point[0] + index % 2 | point[1] << 16)
            self.target.post(0x102, 1, 1)

    def wait(self, stage, predicate, timeout=.8):
        until = min(self.deadline, time.monotonic() + timeout)
        while time.monotonic() < until:
            self.guard()
            self.sample(stage)
            state = self.report['samples'][-1]['gui']
            if predicate(state):
                return state
            time.sleep(.005)
        raise ValueError(f'Background price phase was not acknowledged: {stage}')

    def dialog(self):
        current = self.fresh()
        win = window(current, DIALOG)
        uid, price = self.read_model()
        if self.item is None or uid != self.item['uid']:
            raise ValueError('Price dialog belongs to another inventory item')
        controls = modal_controls(self.session, current)
        return current, win, controls, price

    def click_modal(self, control, label):
        _, win, controls, _ = self.dialog()
        spec = controls[control]
        point = self.point(tuple(a+b for a,b in zip(win['geometry'][:2], spec['offset'])))
        seed = self.session.read_block(win['address'] + 8, 4)
        if len(seed) != 4:
            raise ValueError('Incomplete price dialog control identity')
        expected = zlib.crc32(label.encode(), struct.unpack('<I', seed)[0])
        self.burst(point)
        def hovered(state):
            return state['hover'] == {'window': win['address'], 'id': expected} and state['want_capture_mouse']
        self.wait(control+'-hover', hovered)
        _, fresh_win, fresh_controls, _ = self.dialog()
        if fresh_win != win or fresh_controls != controls or not hovered(self.reader.snapshot()):
            raise ValueError('Price dialog control moved or hover expired')
        self.burst(point, 1, 0x201)
        self.wait(control+'-press', lambda s: s['active']['id'] == expected and s['mouse_down'][0])
        self.dialog()
        self.burst(point, 0, 0x202)
        self.wait(control+'-release', lambda s: not any(s['mouse_down']) and not s['backend']['buttons_down'])
        return expected

    def cancel(self):
        self.dialog()
        self.click_modal('cancel_listing', 'Cancel')
        def closed(_):
            current = self.fresh()
            return not any(w['name'] == DIALOG for w in current['windows']) and self.read_model()[0] == 0
        self.wait('price-cancelled', closed)
        self.report['price_cancel_verified'] = True
        self.report['listing_submitted'] = False
        self.report['release_verified'] = True

    def release(self):
        if self.held:
            self.session.assert_identity()
            if self.session.identity != self.identity:
                raise ValueError('Price probe release refused after process identity changed')
            point = self.release_point
            self.target.post(0x202, 0, point[0] | point[1] << 16)
            self.held = False
            self.report['price_emergency_release_sent'] = True


def run_price_probe(driver, before, reader, report, sample, check):
    probe = _PriceProbe(driver, before, reader, report, sample, check)
    report['listing_submitted'] = False
    try:
        current = probe.fresh()
        if (len(current['booth']) >= 32 or not current.get('booth_open')
                or any(w['name'] == DIALOG for w in current['windows']) or probe.read_model()[0]):
            raise ValueError('Price diagnostic requires an open own booth with space and no existing dialog')
        source = None
        for item in current['inventory']:
            if item.get('bound') is not False:
                continue
            try:
                geometry = _geometry(driver, current, item, probe.size, probe.viewport)
            except ValueError:
                continue
            probe.item, source = deepcopy(item), geometry
            break
        if source is None:
            raise ValueError('No visible unbound item for price diagnostic')
        origin, displaced = source['points']
        booth = window(current, 'Booth')
        drop_windows = {w['address'] for w in current['windows']
                        if w['name'] in ('Booth', 'Booth/##BoothChild_FFE4633E')}
        x,y,width,height = booth['geometry']
        if width < 32 or height < 32:
            raise ValueError('Invalid booth drop rectangle')
        destination = probe.point((x+width/2, y+height/2))
        report['price_source'] = {'uid': probe.item['uid'], 'slot': probe.item['slot'],
                                  'origin': origin, 'destination': destination}
        state = reader.snapshot()
        if (state['active']['id'] or state['drag']['active'] or state['queue']['size']
                or state['backend']['buttons_down'] or any(state['mouse_down'])
                or any(state['modifiers'].values()) or state.get('key_mods', 0)):
            raise ValueError('Price diagnostic requires neutral input')

        def source_fresh():
            fresh = probe.fresh()
            items = [i for i in fresh['inventory'] if i['slot'] == probe.item['slot']]
            if (items != [probe.item] or _geometry(driver, fresh, probe.item, probe.size, probe.viewport) != source
                    or window(fresh, 'Booth') != booth or len(fresh['booth']) >= 32):
                raise ValueError('Price source or booth geometry changed')
            if {w['address'] for w in fresh['windows']
                    if w['name'] in ('Booth', 'Booth/##BoothChild_FFE4633E')} != drop_windows:
                raise ValueError('Price drop window identity changed')
        source_fresh()
        probe.burst(origin)
        state = probe.wait('price-source-hover', lambda s: s['hover']['window'] == source['window']
                           and s['hover']['id'] and s['want_capture_mouse'])
        expected = state['hover']['id']
        source_fresh()
        state = reader.snapshot()
        if state['hover'] != {'window': source['window'], 'id': expected} or not state['want_capture_mouse']:
            raise ValueError('Price source hover expired')
        probe.burst(origin, 1, 0x201)
        probe.wait('price-source-press', lambda s: s['active']['id'] == expected and s['mouse_down'][0])
        source_fresh()
        probe.burst(displaced, 1)
        def payload(s):
            return (s['drag']['active'] and s['drag']['payload_type'] == 'CQITEM'
                    and s['drag']['source_item_uid'] == probe.item['uid']
                    and s['drag']['source_id'] == expected and not s['drag']['delivery'] and s['mouse_down'][0])
        state = probe.wait('price-drag-payload', payload)
        report['price_drag_payload'] = deepcopy(state['drag'])
        source_fresh()
        probe.burst(destination, 1)
        # Destination is observed in logical GUI coordinates, never from pixels.
        logical_destination = [v*g/n for v,g,n in zip(destination, probe.viewport, probe.size)]
        drop = probe.wait('price-drop-hover', lambda s: payload(s) and s['want_capture_mouse']
                   and s['hover']['window'] in drop_windows
                   and all(abs(a-b) <= 2 for a,b in zip(s['mouse_position'], logical_destination)))
        # Standard preview is useful evidence but the game wraps drag acceptance;
        # do not infer acceptance merely from this flag. Exact window is required.
        report['price_drop_preview'] = drop['drag'].get('preview')
        source_fresh()
        state = reader.snapshot()
        if (not payload(state) or state['hover']['window'] not in drop_windows
                or not state['want_capture_mouse']
                or not all(abs(a-b) <= 2 for a,b in zip(state['mouse_position'], logical_destination))):
            raise ValueError('Price drop hover expired before release')
        probe.release_point = destination
        probe.burst(destination, 0, 0x202)
        def opened(_):
            fresh = probe.fresh()
            return any(w['name'] == DIALOG for w in fresh['windows'])
        probe.wait('price-dialog-open', opened)
        _, _, _, price = probe.dialog()
        if price != b'':
            raise ValueError('New price dialog is not blank')
        report['price_dialog_uid_verified'] = True
        field_id = probe.click_modal('price_field', '##Amount')
        _, _, _, price = probe.dialog()
        state = reader.snapshot()
        if price or state['active']['id'] != field_id:
            raise ValueError('Blank amount field is not the active control')
        for digit in b'123456':
            probe.dialog()
            if reader.snapshot()['active']['id'] != field_id:
                raise ValueError('Amount control lost active input ownership')
            report['input_messages_sent'] = True
            driver.target.post(0x102, digit, 1)
        probe.wait('price-text-verified', lambda _: probe.dialog()[3] == b'123456')
        report['price_text_verified'] = 123456
        probe.cancel()
        report['price_probe_verified'] = True
        return report
    except Exception:
        probe.release()
        try:
            probe.deadline = min(probe.started + 12, time.monotonic() + 3)
            fresh = probe.fresh()
            if any(w['name'] == DIALOG for w in fresh['windows']):
                probe.cancel()
                report['price_failure_cancel_verified'] = True
        except Exception as error:
            report['price_requires_attention'] = True
            report['price_cancel_error'] = str(error) if isinstance(error, (ValueError, OSError)) else type(error).__name__
        raise
    finally:
        probe.release()
        report['price_elapsed_seconds'] = round(time.monotonic() - probe.started, 4)
