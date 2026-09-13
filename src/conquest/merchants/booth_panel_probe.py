"""Close only the native booth view for a journaled recovery qualification."""
import time

from conquest.merchants.memory import unpack, HoverNotReady
from conquest.merchants.qualification import stock, window


CLOSE_CODE = {
    0x74b4c: '33d241b821000020488d0df9db5400e8e070faff4c8d4d180f14f666490f7ef0488d15cddb5400498bcce8d5e7ffff',
    0x73637: '486387d0000000488d0d772a54004c8b87d800000033d2458b4480fce828c2f9ff',
    0x7367c: '410f28c8c74424389a99193ff30f5c0de01856000f28c7c744243c9a99193ff30f58057d1856004c8d4c24388bd34c8d442430f30f5c0db5185600f30f11442434f30f114c2430e8e809000084c07403448826',
    0x75daa: '807d18007514498b0424498bccff5018498b0424498bccff5070',
    0x75e20: '33c04889414c4889415489415cc3',
    0x7332d: 'c6460c00',
}


def close_point(driver, snapshot):
    """Pinned custom #CLOSE button, distinct from the booth shutdown operation."""
    gui = driver.memory.gui
    s, base = gui.session, gui.base
    for rva, encoded in CLOSE_CODE.items():
        expected = bytes.fromhex(encoded)
        if s.read_block(base + rva, len(expected)) != expected:
            raise ValueError('Native booth close control changed')
    model = gui.model(25, 0x5c27f8)
    if (unpack(s, model + 0xc, '<B')[0] != 1
            or unpack(s, model + 0x4c, '<I')[0] != snapshot.get('own_booth_uid')
            or unpack(s, base + 0x5c27f8 + 0x18, '<Q')[0] != base + 0x75e20
            or unpack(s, base + 0x5c27f8 + 0x70, '<Q')[0] != base + 0x732b0):
        raise ValueError('Booth close control is not the owned panel')
    w = window(snapshot, 'Booth')
    x, y, width, height = w['geometry']
    # Custom header: right-16-15, top+4.5; the icon occupies 0.6*25 pixels.
    # Hover ID below verifies the exact interactive widget before mouse-down.
    point = (round(x + width - 23.5), round(y + 12))
    if not x < point[0] < x + width or not y < point[1] < y + height:
        raise ValueError('Booth close control is outside its window')
    return point, w


def close_owned_panel(driver, travel, journal, check):
    """One close click, preserving shop ownership and exact stock; no retries."""
    character = driver.observer.character
    previous = journal.get(character, 'booth_panel_probe', {})
    if previous.get('phase') == 'close_submitted':
        raise ValueError('Previous booth-panel close needs reconciliation')
    check()
    before = driver.memory.read()
    if (before['map_id'] != 1036 or not before.get('own_booth_uid')
            or not before['booth_open'] or before.get('trade') or before.get('request')
            or any(w['name'] == 'Add Item to Booth' for w in before['windows'])):
        raise ValueError('Booth-panel test needs an idle owned shop in Market')
    point, initial_window = close_point(driver, before)
    record = dict(phase='close_prepared', before=before, point=point, started_at=time.time())
    journal.set(character, 'booth_panel_probe', record)

    def unchanged():
        check()
        fresh = driver.memory.read()
        if (stock(fresh) != stock(before) or fresh.get('trade') or fresh.get('request')
                or any(fresh[k] != before[k] for k in ('map_id', 'position', 'own_booth_uid'))):
            raise ValueError('Booth ownership or stock changed during panel test')
        return fresh

    def guard():
        deadline = time.monotonic() + .35
        while True:
            current = unchanged()
            if close_point(driver, current) != (point, initial_window):
                raise ValueError('Booth close target changed before mouse-down')
            try:
                driver.memory.gui.assert_hovered(initial_window, '#CLOSE')
            except HoverNotReady:
                if time.monotonic() >= deadline:
                    raise ValueError('Booth close hover was not verified; no button pressed')
                time.sleep(.01)
                continue
            if time.monotonic() >= deadline:
                raise ValueError('Booth close observation expired; no button pressed')
            record.update(phase='close_submitted', submitted_at=time.time())
            journal.set(character, 'booth_panel_probe', record)
            return

    travel.click(point, check, before_press=guard)
    deadline = time.monotonic() + 2
    while True:
        fresh = unchanged()
        if not fresh['booth_open']:
            record.update(phase='closed_verified', verified_at=time.time(),
                          own_booth_uid=fresh['own_booth_uid'], stock_unchanged=True)
            journal.set(character, 'booth_panel_probe', record)
            return record
        if time.monotonic() >= deadline:
            raise ValueError('Booth panel close was not verified; no repeated input')
        time.sleep(.05)
