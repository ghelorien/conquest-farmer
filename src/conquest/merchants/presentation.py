"""Background UI data collection; Tk never waits on merchant memory or SQLite."""
import threading
import time
from conquest.merchants.sales import summary


class MerchantPresentation:
    def __init__(self, runtime):
        self.runtime=runtime
        self.stop=threading.Event();self.ready=threading.Event()
        self.latest=None
        self.thread=None

    def start(self):
        if self.thread:return
        self.thread=threading.Thread(target=self.run,name='merchant-ui-data',daemon=True)
        self.thread.start()

    def collect(self):
        runtime=self.runtime
        statuses=runtime.status()
        with runtime.journal.db() as db:
            events=[dict(r) for r in db.execute('SELECT * FROM events ORDER BY id DESC LIMIT 300')]
        tables={c:{key:runtime.journal.get(c,key,[]) for key in ('comparisons','deferred')} for c in statuses}
        previous=self.latest
        sales=(previous['sales'] if previous and time.time()-previous['sales']['at']<5
               else summary(runtime.journal))
        # Publish once, after all reads; readers only observe a complete model.
        from conquest.merchants.notification_health import describe
        self.latest={'notification_health':describe(), 'characters':statuses,'events':events,'tables':tables,'sales':sales,
                     'reporting':runtime.sales_worker.status(),'collected_at':time.monotonic()}
        self.ready.set()

    def run(self):
        while not self.stop.is_set():
            try:self.collect()
            except Exception:pass  # Keep the last view; the UI marks it stale.
            self.stop.wait(1)

    def close(self):
        self.stop.set()
        if self.thread:self.thread.join(timeout=1)
