"""Idempotent four-hour report dispatch to the separately configured #shops webhook."""
from conquest.character_context import state_path
import json
from pathlib import Path
import time
from conquest.discord_notify import webhook_url, deliver, DeliveryError
from conquest.merchants.journal import Journal
from conquest.merchants.sales import summary, format_summary

SECRET = Path(state_path('.runtime/merchants/shops-webhook.dpapi'))
INTERVAL = 4*60*60


class SalesReportWorker:
    """App-owned timer: durable cadence, bounded retries, no AI or game input."""
    def __init__(self, journal, stop, *, clock=time.time, dispatch=None):
        self.journal,self.stop,self.clock = journal,stop,clock
        self.dispatch = dispatch or send_report

    def status(self):
        with self.journal.db() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT * FROM sales_schedule WHERE id=1').fetchone()
            if not row:
                last = db.execute("SELECT MAX(created) FROM sales_reports WHERE phase='delivered'").fetchone()[0]
                due = (last if last is not None else self.clock())+INTERVAL
                db.execute('INSERT INTO sales_schedule VALUES(1,?,?,?)',(due,0,'waiting'))
                row = db.execute('SELECT * FROM sales_schedule WHERE id=1').fetchone()
        return {**dict(row),'last_checked_at':getattr(self,'last_checked_at',None)}

    def step(self):
        now = self.clock();self.last_checked_at = now;state = self.status()
        if now < max(state['next_due'],state['retry_at']) or self.stop.is_set():
            return state
        try:
            result = self.dispatch(journal=self.journal,now=now)
        except Exception:
            result = {'status':'worker_error','retry_after':300}
        status = result['status']
        if status in ('delivered','already_delivered'):
            # Missed intervals coalesce to one current summary after downtime.
            due = state['next_due']+(int((now-state['next_due'])//INTERVAL)+1)*INTERVAL
            retry_at = 0
        else:
            due = state['next_due']
            retry_at = now+max(30,min(3600,result.get('retry_after') or 300))
        with self.journal.db() as db:
            db.execute('UPDATE sales_schedule SET next_due=?,retry_at=?,status=? WHERE id=1',(due,retry_at,status))
        return self.status()

    def run(self):
        while not self.stop.is_set():
            try:
                self.step()
            except Exception:
                pass  # An unavailable DB cannot send; retry after a bounded wait.
            self.stop.wait(15)


def save_webhook(value):
    import win32crypt
    webhook_url(value)
    SECRET.parent.mkdir(parents=True,exist_ok=True)
    SECRET.write_bytes(win32crypt.CryptProtectData(value.strip().encode(),'Conquest #shops',None,None,None,0))


def load_webhook():
    import win32crypt
    return webhook_url(win32crypt.CryptUnprotectData(SECRET.read_bytes(),None,None,None,0)[1].decode())


def send_report(*, journal=None, now=None, send=deliver, load=load_webhook, preview=False):
    journal = journal or Journal()
    now = time.time() if now is None else now
    data = summary(journal,now=now)
    content = format_summary(data)
    if preview:
        return {'status':'preview','content':content}
    # No fallback to the farmer channel and no report marked sent without a receipt.
    try:
        url = load()
    except Exception:
        return {'status':'needs_configuration','note':'Configure the #shops webhook in Conquest Overview.'}
    key = f'shops:4h:{int(now//14400)}'
    with journal.db() as db:
        db.execute('BEGIN IMMEDIATE')
        old = db.execute('SELECT * FROM sales_reports WHERE id=?',(key,)).fetchone()
        if old and old['phase']=='delivered':
            return {'status':'already_delivered','message_id':old['message_id']}
        if old and old['phase'] in ('sending','uncertain'):
            return {'status':'needs_attention','note':'Previous delivery was interrupted or unconfirmed; reconcile before resending.'}
        if old:
            content = old['content']
        else:
            db.execute('INSERT INTO sales_reports VALUES(?,?,?, ?,NULL)',(key,now,'prepared',content))
        db.execute("UPDATE sales_reports SET phase='sending' WHERE id=?",(key,))
    try:
        message_id = send(url,content)
        if not message_id:
            raise DeliveryError('Discord did not confirm the message')
    except Exception as error:
        # An HTTP rejection is safe to retry; a lost network receipt is ambiguous.
        rejected = isinstance(error,DeliveryError) and str(error).startswith('Discord HTTP ')
        with journal.db() as db:
            db.execute('UPDATE sales_reports SET phase=? WHERE id=?',('prepared' if rejected else 'uncertain',key))
        return {'status':'retry_later' if rejected else 'needs_attention',
                'note':'Discord rejected the report.' if rejected else 'Delivery unconfirmed; reconcile before resending.',
                'retry_after':error.retry_after if rejected else None}
    with journal.db() as db:
        db.execute("UPDATE sales_reports SET phase='delivered',message_id=? WHERE id=?",(str(message_id),key))
    return {'status':'delivered','message_id':str(message_id)}
