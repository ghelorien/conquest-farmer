"""Non-secret service health for the Overview panel."""
import time
from pathlib import Path
from conquest.discord_notify import read_json,process_alive


def service(secret, status, *, now=None):
    if not Path(secret).exists():return 'not configured'
    row=read_json(status);now=time.time() if now is None else now
    if row.get('error'):return 'delivery failed; retry queued'
    if not process_alive(row.get('pid')) or now-row.get('updated_at',0)>30:return 'monitor not responding'
    if row.get('queued'):return str(row['queued'])+' messages queued'
    return 'monitor running'


def describe():
    return ('Discord farmer: '+service('.runtime/discord-webhook.dpapi','reports/discord-status.json')+
            ' | Discord shops: '+service('.runtime/merchants/shops-webhook.dpapi','reports/merchants/shops-alert-status.json'))
