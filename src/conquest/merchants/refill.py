"""App-owned fifteen-minute booth capacity checks using durable price history."""
import time
from conquest.merchants.market import MarketSnapshot
from conquest.merchants.controller import MerchantController
from conquest.capture import CaptureUnavailable

INTERVAL = 900


class RefillController(MerchantController):
    """Listing-only permission; never grants trading or repricing permission."""
    listing_purpose='refill'
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
            state = {'next_check':self.clock()+INTERVAL,'last_checked':None,'pending':False,'status':'waiting','interval_seconds':INTERVAL}
            self.journal.set(self.character,'refill',state)
        elif state.get('interval_seconds') != INTERVAL:
            # Migrate the old cadence once; reconcile transactions separately.
            base=state.get('last_checked')
            if base is None:base=state.get('next_check',self.clock()+300)-300
            state.update(interval_seconds=INTERVAL,next_check=base+INTERVAL)
            if not state.get('pending'):state['status']='waiting'
            self.journal.set(self.character,'refill',state)
        if state.get('status')=='work_budget_finished':
            # The old implementation called an interrupted window complete.
            # Keep its original evidence, but do not carry that false completion
            # into the new scheduler. Input still requires a normal work grant.
            state['legacy_interrupted_check']=dict(state)
            state.update(pending=True,status='paused_budget',
                         last_attempt_at=state.get('last_checked'),
                         last_checked=state.get('last_completed_check_at'),
                         original_due_at=state.get('next_check'),
                         cursor=state.get('cursor',[]))
            self.journal.set(self.character,'refill',state)
        return state

    def due(self):
        state = self.state()
        return state['pending'] or self.clock()>=state['next_check']

    def start(self, *, visit_id=None, town_visit_id=None, operation_id=None, source_delivery_operation_id=None):
        state = self.state()
        now=self.clock()
        if not state.get('pending'):
            state.update(original_due_at=state['next_check'],cursor=[],listed=0,deferred=0,
                         attempt_started_at=now,source_delivery_operation_id=None)
        if source_delivery_operation_id is not None:
            if state.get('source_delivery_operation_id') not in (None,source_delivery_operation_id):
                raise ValueError('Pending refill belongs to another delivery operation')
            state['source_delivery_operation_id']=source_delivery_operation_id
        state.update(pending=True,status='checking',last_attempt_at=now)
        if visit_id is not None:state['visit_id']=visit_id
        if town_visit_id is not None:state['town_visit_id']=town_visit_id
        if operation_id is not None:state['operation_id']=operation_id
        self.journal.set(self.character,'refill',state)

    def checkpoint(self, remaining, *, listed=None, deferred=None):
        state=self.state()
        state.update(cursor=list(remaining),pending=True)
        if listed is not None:state['listed']=listed
        if deferred is not None:state['deferred']=deferred
        self.journal.set(self.character,'refill',state)

    def pause_budget(self):
        state=self.state()
        if not state.get('pending'):return
        state.update(status='paused_budget',paused_at=self.clock())
        self.journal.set(self.character,'refill',state)
        self.journal.event(self.character,'refill_deferred',status='paused_budget',
                           listed=state.get('listed',0),remaining=len(state.get('cursor',[])),
                           visit_id=state.get('visit_id'),town_visit_id=state.get('town_visit_id'))

    def complete(self, status, *, listed=0, deferred=0):
        if status=='work_budget_finished':
            return self.pause_budget()
        if status not in ('completed','no_stock','booth_full'):
            raise ValueError('A refill check requires an observed terminal result')
        state = self.state()
        now = self.clock()
        state.update(pending=False,status=status,last_checked=now,listed=listed,deferred=deferred,
                     next_check=now+INTERVAL,interval_seconds=INTERVAL,
                     last_completed_check_at=now,cursor=[],original_due_at=None)
        self.journal.set(self.character,'refill',state)
        self.journal.event(self.character,'refill_checked',status=status,listed=listed,deferred=deferred,
                           visit_id=state.get('visit_id'),town_visit_id=state.get('town_visit_id'))
