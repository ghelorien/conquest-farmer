"""Independent memory-only merchant protection, including paused accounts."""
import time
from conquest.merchants.journal import CHARACTERS


def protect(runtime, character, identity, reason, *, close=None):
    # Latest user instruction: never close a merchant client automatically.
    # Pausing input and reconnect intent preserves the current live connection.
    runtime.enable(character, False)
    runtime.set_refill_enabled(character, False)
    runtime.journal.set(character, 'connect_hold', True)
    cancel = runtime.connect_cancel.get(character)
    if cancel: cancel.set()
    runtime.journal.set(character, 'attention', {
        'kind': 'market_safety', 'note': reason + '; automation paused, client kept connected. User assistance required.'})
    result = {'time': time.time(), 'identity': identity, 'reason': reason,
              'disconnected': False, 'action': 'pause_only'}
    previous = runtime.journal.get(character, 'market_safety') or {}
    runtime.journal.set(character, 'market_safety', result)
    if previous.get('reason') != reason or previous.get('action') != 'pause_only':
        runtime.journal.event(character, 'market_safety', **result)
    return result


class MarketGuard:
    def __init__(self, runtime):
        self.runtime = runtime
        self.readers = {}
        self.unknown_since = {}
        self.observations = {}

    def check(self, character, observer, *, clock=time.monotonic, read=None, close=None):
        from conquest.memory_life import read_life
        read = read or read_life
        identity = observer.adapter.identity
        from conquest.merchants.recovery_safety import arm
        from conquest.reconnect import login_screen
        # A known disconnected client is a recovery trigger, not an unsafe
        # manually positioned character. Never turn that into a pause-only hold.
        target = getattr(getattr(observer, 'operations', None), 'target', None)
        if target is not None and login_screen(target.hwnd):
            arm(self.runtime, character)
            from conquest.merchants.recovery_safety import active, observe
            if active(self.runtime, character):
                return observe(self.runtime, character, identity, close=close)
            return  # Never interpret stale player memory on the login screen.
        try:
            observer.adapter.assert_identity()
            life = read(observer.adapter, observer.health_layout, character)
        except Exception:
            from conquest.merchants.recovery_safety import active, observe
            if active(self.runtime, character):
                return observe(self.runtime, character, identity, close=close)
            started = self.unknown_since.setdefault(character, clock())
            if clock() - started < 2: return
            reason = 'Merchant location could not be verified for two seconds'
        else:
            self.unknown_since.pop(character, None)
            self.observations[character] = {'map_id':life.map_id, 'observed_at':time.time()}
            from conquest.merchants.recovery_safety import active, observe
            if active(self.runtime, character):
                return observe(self.runtime, character, identity, life, close=close)
            if life.map_id == 1036 and not life.dead_candidate: return
            reason = f'Merchant outside safe Market (map {life.map_id}) or death detected'
        return protect(self.runtime, character, identity, reason, close=close)

    def run(self):
        r = self.runtime
        while not r.stop_event.is_set():
            try:
                clients = r.catalog.windows()
                for character in CHARACTERS:
                    identity = r.journal.get(character, 'last_identity')
                    client = next((c for c in clients if c.identity == identity), None)
                    cached = self.readers.get(character)
                    if cached and (client is None or cached.adapter.identity != identity):
                        cached.close(); self.readers.pop(character, None)
                        self.unknown_since.pop(character, None)
                    if client is None: continue
                    if character not in self.readers:
                        # Separate handle: never wait on the automation's memory lock.
                        try:
                            self.readers[character] = r.observer_factory(client, character)
                        except Exception:
                            started = self.unknown_since.setdefault(character, time.monotonic())
                            if time.monotonic() - started >= 2:
                                from conquest.merchants.recovery_safety import active, observe
                                if active(r,character): observe(r,character,identity)
                                else: protect(r, character, identity, 'Merchant safety reader unavailable for two seconds')
                            continue
                    try:
                        self.check(character, self.readers[character])
                    except Exception:
                        # Preserve the failed-close record and retry next tick.
                        pass
            except Exception:
                pass
            from conquest.discord_notify import write_json
            try:
                write_json('reports/merchants/market-guard.json', {'running':True, 'updated_at':time.time(),
                    'interval_seconds':0.25, 'unknown_grace_seconds':2, 'observations':self.observations})
            except OSError: pass
            r.stop_event.wait(0.25)
        for observer in self.readers.values(): observer.close()
