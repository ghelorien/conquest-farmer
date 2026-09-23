"""Read-only live booth layout evidence, never an input qualification."""
import hashlib
import struct

from conquest.memory_build_layout import CLIENT_SHA256_1078, read_build_layout
from conquest.merchants.memory import GuiReader, HoverNotReady, unpack


_MODAL_RENDER_RVA = 0x75b90
_MODAL_RENDER_LENGTH = 5313
_MODAL_RENDER_SHA256 = '97c9069d8c500a9ff90b474dfb34871a36ba8dda689f1b537319c83568fcba04'


def _modal_code_verified(session, layout):
    """Pin the loaded 1078 UID/amount/OK/Cancel render function, read-only."""
    modules = [module for module in session.modules
               if module['name'].casefold() == 'imconquer.exe']
    if len(modules) != 1:
        raise ValueError('Expected one exact ImConquer module')
    module = modules[0]
    if _MODAL_RENDER_RVA + _MODAL_RENDER_LENGTH > module['size']:
        raise ValueError('1078 price-dialog code is outside the client module')
    base = module['base']
    vtable = base + layout.merchant_booth_vtable_rva
    render = struct.unpack('<Q', session.read(vtable + 0x10, 8))[0]
    if render != base + _MODAL_RENDER_RVA:
        raise ValueError('1078 booth render binding changed')
    code = session.read(render, _MODAL_RENDER_LENGTH)
    if hashlib.sha256(code).hexdigest() != _MODAL_RENDER_SHA256:
        raise ValueError('1078 price-dialog button/field code changed')
    return True


def _window(windows, name, *, prefix=False):
    matches = [w for w in windows
               if (w['name'].startswith(name) if prefix else w['name'] == name)]
    if len(matches) != 1:
        raise ValueError(f'Expected exactly one live {name} window; found {len(matches)}')
    return matches[0]


def _panel(window):
    return {key: window[key] for key in ('name', 'geometry', 'scroll')}


def _table(gui, window, label):
    table = gui.table(window, label)
    return {'window': window['name'], 'label': label,
            'columns': table['columns'], 'row_height': table['row_height'],
            'outer': table['outer'], 'clip': table['clip'],
            'window_ownership_verified': True, 'input_qualified': False}


def _modal_candidate(adapter, model, snapshot):
    """Read a provisional 1078 dialog value without assigning it control semantics."""
    # The legacy 1074 reader observed this contiguous selected-UID / 12-byte
    # text shape.  This only records a build-pinned 1078 candidate; it does
    # not qualify the amount field, buttons, or any submission behavior.
    raw = adapter.read_block(model + 0x50, 16)
    if len(raw) != 16:
        raise ValueError('Incomplete candidate price-dialog model read')
    uid = struct.unpack_from('<I', raw)[0]
    buffer = raw[4:]
    terminator = buffer.find(b'\0')
    if terminator < 0 or any(buffer[terminator:]):
        raise ValueError('Candidate price-dialog buffer is invalid')
    try:
        text = buffer[:terminator].decode('ascii')
    except UnicodeDecodeError as error:
        raise ValueError('Candidate price-dialog buffer is not ASCII') from error
    if not uid or sum(item.get('uid') == uid for item in snapshot['inventory']) != 1:
        raise ValueError('Candidate selected item does not match exactly one observed inventory item')
    return {'candidate_selected_item_uid': uid, 'candidate_price_buffer_text': text}


def collect(session, snapshot):
    """Caller brackets this with identical exact merchant ownership reads."""
    if session.expected_sha256 != CLIENT_SHA256_1078:
        raise ValueError('Listing preflight is restricted to the exact 1078 build')
    session.assert_identity()
    result = {'read_only': True, 'layout_observed': False,
              'input_qualified': False, 'refill_input_ready': False,
              'blockers': [], 'inventory_grid': None, 'owned_booth': None,
              'booth_grid': None, 'price_modal': None}

    def blocked(code, note):
        result['blockers'].append({'code': code, 'note': note})
        return result

    if snapshot['trade'] is not None or snapshot['request'] is not None:
        return blocked('merchant_modal_open', 'Close the merchant trade/request before listing preflight')
    if snapshot['map_id'] != 1036 or snapshot['hp'] <= 0:
        return blocked('merchant_not_alive_in_market', 'Listing preflight requires a living merchant in Market')
    if not snapshot['own_booth_uid']:
        return blocked('owned_booth_missing', 'Memory does not identify an owned booth; no booth target was inferred')
    if not snapshot['booth_open']:
        return blocked('owned_booth_closed', 'Open this merchant\'s owned booth panel; its native model is closed')

    layout = read_build_layout(session)
    gui = GuiReader.for_session(session)
    adapter = gui.session
    model = gui.model(25, layout.merchant_booth_vtable_rva)
    model_owner = unpack(adapter, model + 0x4c, '<I')[0]
    model_active = unpack(adapter, model + 12, '<B')[0]
    if not model_active or model_owner != snapshot['own_booth_uid']:
        raise ValueError('Live booth model does not identify this merchant\'s owned booth')
    windows = gui.windows()
    viewport = gui.viewport_size()
    result['viewport_size'] = viewport
    relevant = [w for w in windows if w['name'] == 'Booth'
                or w['name'].startswith(('Inventory/', 'Booth/'))
                or w['name'] == 'Add Item to Booth']
    try:
        booth = _window(windows, 'Booth')
    except ValueError as error:
        return blocked('owned_booth_panel_unavailable', str(error))
    result['owned_booth'] = {**_panel(booth), 'owner_uid': model_owner,
                            'model_key': 25, 'model_owner_verified': True,
                            'drop_input_qualified': False}
    table_reads = []
    for key, prefix, label in (('inventory_grid', 'Inventory/', '##ItemTable'),
                               ('booth_grid', 'Booth/', 'BoothTable')):
        try:
            panel = _window(windows, prefix, prefix=True)
            evidence = _table(gui, panel, label)
        except ValueError as error:
            blocked(key + '_unavailable', str(error))
        except OSError:
            blocked(key + '_unavailable', 'Native GUI table memory is unreadable; retry observation')
        else:
            result[key] = evidence
            table_reads.append((panel, label, evidence))
    modals = [w for w in windows if w['name'] == 'Add Item to Booth']
    if len(modals) > 1:
        raise ValueError('Multiple live listing price dialogs are ambiguous')
    if modals:
        modal = modals[0]
        candidate = _modal_candidate(adapter, model, snapshot)
        _modal_code_verified(session, layout)
        hovered = []
        # A native label hash under the current pointer is passive evidence,
        # not proof of the label's handler, button geometry, or input behavior.
        for label in ('##Amount', 'OK', 'Cancel'):
            try:
                gui.assert_hovered(modal, label)
            except HoverNotReady:
                continue
            hovered.append(label)
        result['price_modal'] = {**_panel(modal), 'observed': True,
            'hovered_label_hash_matches': hovered,
            **candidate,
            'selected_item_uid_verified': True,
            'price_buffer_binding_verified': True,
            'native_ok_cancel_handlers_verified': True,
            'server_acceptance_verified': False,
            'controls_qualified': False}
        blocked('price_modal_input_unqualified',
                'Native field and button bindings are pinned, but click geometry and server outcome remain unverified')
    else:
        result['price_modal'] = {'observed': False, 'controls_qualified': False}
        blocked('price_modal_not_observed', 'No live Add Item to Booth dialog; no price controls were inferred')
    fresh = gui.windows()
    fresh_relevant = [w for w in fresh if w['name'] == 'Booth'
                      or w['name'].startswith(('Inventory/', 'Booth/'))
                      or w['name'] == 'Add Item to Booth']
    if (gui.viewport_size() != viewport
            or sorted(relevant, key=lambda w: w['address']) != sorted(fresh_relevant, key=lambda w: w['address'])
            or gui.model(25, layout.merchant_booth_vtable_rva) != model
            or unpack(adapter, model + 0x4c, '<I')[0] != model_owner
            or unpack(adapter, model + 12, '<B')[0] != model_active):
        raise ValueError('Merchant booth or GUI layout changed during preflight')
    if modals and _modal_candidate(adapter, model, snapshot) != candidate:
        raise ValueError('Candidate price-dialog model changed during preflight')
    for panel, label, evidence in table_reads:
        if _table(gui, panel, label) != evidence:
            raise ValueError('Merchant table layout changed during preflight')
    session.assert_identity()
    result['layout_observed'] = bool(result['inventory_grid'] and result['booth_grid'])
    blocked('listing_input_unqualified',
            'Read-only layout evidence does not qualify drag, typing, cancellation or listing submission')
    return result
