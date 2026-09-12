"""Independent, durable #shops failure notifications; no game input or AI."""
from conquest.character_context import state_path
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time
import uuid

from conquest.discord_notify import DeliveryError, deliver, read_json, write_json
from conquest.merchants.bridge import request
from conquest.merchants.sales_report import SECRET, load_webhook

STATE = Path(state_path('.runtime/merchants/shops-alerts.json'))
STATUS = Path(state_path('reports/merchants/shops-alert-status.json'))
LIFECYCLE = Path(state_path('.runtime/merchants/app-lifecycle.json'))
QUIET_WAITS = ('Automation stopped or manual input active','Mouse control is yours',
               'Waiting for input owner','Waiting for a safe farmer handoff',
               'Farmer handoff was revoked','Paused; recovery will not change manual intent')
MONITOR_VERSION = 2


def safe_note(value):
    text = re.sub(r'https?://\S+','[URL redacted]',str(value))
    text = re.sub(r'(?i)(password|token|authorization|webhook)\s*[:=]\s*\S+',r'\1=[redacted]',text)
    return text[:500]


def condition(state):
    attention = state.get('needs_attention')
    if attention:
        return safe_note(attention['note']),0
    pending = state.get('pending',[])
    if any(p['phase']=='uncertain' for p in pending):
        return 'A trade or listing has an uncertain result. Reconciliation is required before retrying.',0
    if pending:
        return 'A trade or listing has not completed. Check the transaction and game connection.',60
    if not state.get('connected'):
        return 'Client disconnected or memory observation unavailable. Check the client/reconnect status.',60
    error = state.get('error')
    if error and not str(error.get('note','')).startswith(QUIET_WAITS):
        return safe_note(error['note']),60
    returning=state.get('shop_return')
    if returning and returning['phase']!='complete':
        return 'Reconnect is not complete: returning to Market and restoring the shop.',60
    return None


class Alerts:
    def __init__(self, state=None):
        self.state = state if state is not None else {}
        self.state.setdefault('incidents',{})
        self.state.setdefault('queue',[])

    def observe(self, subject, problem, now):
        incident = self.state['incidents'].get(subject)
        if problem:
            note,delay = problem
            self.state['queue'] = [r for r in self.state['queue']
                if not (r['subject']==subject and r['kind']=='recovery')]
            if incident is None:
                incident = {'id':uuid.uuid4().hex,'since':now,'sent':False}
                self.state['incidents'][subject] = incident
            incident.update(note=safe_note(note),healthy_since=None)
            if now-incident['since']>=delay and not incident.get('queued') and not incident['sent']:
                self.state['queue'].append({'id':incident['id'],'subject':subject,'kind':'failure',
                    'content':f'**{subject} — needs attention**\n{incident["note"]}',
                    'created':now,'retry_at':0})
                incident['queued'] = True
            return
        if not incident:
            return
        if not incident['sent']:
            self.state['queue'] = [r for r in self.state['queue'] if r['id']!=incident['id']]
            self.state['incidents'].pop(subject)
            return
        if incident.get('healthy_since') is None:
            incident['healthy_since'] = now
        if now-incident['healthy_since']>=5:
            self.state['queue'].append({'id':uuid.uuid4().hex,'subject':subject,'kind':'recovery',
                'content':f'**{subject} — recovery confirmed**\nFresh status checks confirm the reported issue has cleared.',
                'created':now,'retry_at':0})
            self.state['incidents'].pop(subject)

    def poll(self, status, now, *, clean_shutdown=False):
        if status is None:
            if not clean_shutdown:
                self.observe('Conquest app',('Conquest is closed or not responding. Shop automation and sales observation may be interrupted.',60),now)
            return  # Missing observations can never confirm merchant recovery.
        stale_ui = status.get('ui_health',{}).get('tick_age_ms',0)>15000
        self.observe('Conquest app',('Conquest UI stopped responding; merchant input may be blocked.',60) if stale_ui else None,now)
        for character,state in status['characters'].items():
            returning=state.get('shop_return')
            if (returning and returning['phase']!='complete' and not state.get('enabled')
                    and not state.get('needs_attention')):
                continue  # Manual pause neither alerts nor confirms unfinished recovery.
            self.observe(character,condition(state),now)
        report = status.get('sales_reporting',{})
        failed = report.get('status') in ('worker_error','needs_attention','needs_configuration','retry_later')
        stale_report = report.get('last_checked_at') is not None and now-report['last_checked_at']>60
        problem = ('The four-hour sales-report worker stopped responding.',60) if stale_report else (
            ('The four-hour Discord report needs attention: '+str(report.get('status')),60) if failed else None)
        self.observe('Shop sales reporting',problem,now)

    def dispatch(self, now, *, send=deliver, load=load_webhook, persist=lambda state:None):
        due = next((r for r in self.state['queue'] if r['retry_at']<=now),None)
        if due is None:
            return
        # Persist the queue before network I/O. Like the farmer notifier, an
        # ambiguous network receipt can cause a duplicate on a later retry.
        persist(self.state)
        try:
            receipt = send(load(),due['content'])
            if not receipt:
                raise DeliveryError('Discord did not confirm the message')
        except Exception as error:
            delay = error.retry_after if isinstance(error,DeliveryError) else 60
            due['retry_at'] = now+max(2,min(3600,delay))
            self.state['delivery_error'] = 'Discord alert delivery failed; queued for retry'
        else:
            incident = self.state['incidents'].get(due['subject'])
            if incident and due['kind']=='failure' and incident['id']==due['id']:
                incident['sent'] = True
            self.state['queue'].remove(due)
            self.state.update(last_message_id=str(receipt),last_sent_at=now)
            self.state.pop('delivery_error',None)
        persist(self.state)


def ensure_monitor():
    if not SECRET.exists():
        return False
    repo = Path(__file__).resolve().parents[3]
    python = Path(sys.executable).with_name('pythonw.exe')
    subprocess.Popen([str(python if python.exists() else Path(sys.executable)),
                      str(repo/'scripts/run_shop_notifications.py')],cwd=repo,
        stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,
        creationflags=subprocess.CREATE_NO_WINDOW)
    return True


def run():
    import msvcrt
    STATE.parent.mkdir(parents=True,exist_ok=True)
    lock = STATE.with_suffix('.lock').open('a+b')
    lock.write(b'0');lock.flush();lock.seek(0)
    try:
        for attempt in range(10):
            try:
                msvcrt.locking(lock.fileno(),msvcrt.LK_NBLCK,1)
                break
            except OSError:
                if attempt==9:raise
                time.sleep(.1)
    except OSError:
        lock.close();return
    alerts = Alerts(read_json(STATE))
    try:
        while True:
            try:
                try:
                    status = request({'action':'status'})
                except Exception:
                    status = None
                now = time.time()
                lifecycle = read_json(LIFECYCLE)
                alerts.poll(status,now,clean_shutdown=lifecycle.get('state')=='closed')
                write_json(STATE,alerts.state)
                alerts.dispatch(now,persist=lambda state:write_json(STATE,state))
                write_json(STATUS,{'pid':os.getpid(),'version':MONITOR_VERSION,'updated_at':time.time(),'state':'watching',
                    'queued':len(alerts.state['queue']),'open_incidents':list(alerts.state['incidents']),
                    'last_sent_at':alerts.state.get('last_sent_at'),
                    'last_message_id':alerts.state.get('last_message_id'),
                    'error':alerts.state.get('delivery_error')})
            except Exception:
                # Never include raw exception text: it could contain secrets.
                pass
            time.sleep(5)
    finally:
        lock.close()
