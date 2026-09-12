"""App-owned five-minute booth capacity checks using durable price history."""
import time
from conquest.merchants.market import MarketSnapshot
from conquest.merchants.controller import MerchantController
from conquest.capture import CaptureUnavailable

INTERVAL = 300


class RefillController(MerchantController):
    """Listing-only permission; never grants trading or repricing permission."""
    def active(self):
        return self.journal.get(self.character,'refill_enabled',True) and not self.coordinator.stopped

    def check(self):
        if not self.active():raise CaptureUnavailable('Inventory refill is paused')
        self.coordinator.check()

    def apply_price(self, plan):
        if plan.get('old_price') is not None:
            raise ValueError('Inventory refill cannot reprice an existing listing')
        return super().apply_price(plan)

    def accept_request(self, snapshot):
        raise ValueError('Inventory refill cannot accept trades')

    def accept_delivery(self):
        raise ValueError('Inventory refill cannot accept trades')


class HistoricalComparisons:
    """Catalog-only planning view. No old listings are treated as live offers."""
    key_for = MarketSnapshot.key_for

    def __init__(self, catalog, *, now=None):
        # Plan creation time is separate from each quote's source_observed_at.
        self.data = {'observed_at':time.time() if now is None else now}
        self.history_catalog = catalog
        self.rows = []

    def comparisons(self, item):
        return []


class RefillSchedule:
    def __init__(self, character, journal, *, clock=time.time):
        self.character,self.journal,self.clock = character,journal,clock

    def state(self):
        state = self.journal.get(self.character,'refill')
        if state is None:
            state = {'next_check':self.clock()+INTERVAL,'last_checked':None,'pending':False,'status':'waiting'}
            self.journal.set(self.character,'refill',state)
        return state

    def due(self):
        state = self.state()
        return state['pending'] or self.clock()>=state['next_check']

    def start(self):
        state = self.state()
        state.update(pending=True,status='checking',last_checked=self.clock())
        self.journal.set(self.character,'refill',state)

    def complete(self, status, *, listed=0, deferred=0):
        state = self.state()
        now = self.clock()
        due = state['next_check']
        state.update(pending=False,status=status,last_checked=now,listed=listed,deferred=deferred,
                     next_check=due+(int(max(0,now-due)//INTERVAL)+1)*INTERVAL)
        self.journal.set(self.character,'refill',state)
        self.journal.event(self.character,'refill_checked',status=status,listed=listed,deferred=deferred)
