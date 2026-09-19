"""Cancel an unopened reserved request; prove no items/currency moved."""
import json,threading,time
from pathlib import Path
from conquest.discord_notify import read_json,write_json
from conquest.character_context import state_path
from conquest.merchants.delivery_operation import JOURNAL
from conquest.merchants.journal import Journal
from conquest.merchants.delivery_cancel_probe import unchanged,cancelled,run
from conquest.merchants.delivery_bridge import pair
from conquest.merchants.delivery_reservation import save
OUTPUT=Path('reports/merchants/reserved-request-cancellation.json')


def start(ui):
    if getattr(ui,'delivery_probe_thread',None) and ui.delivery_probe_thread.is_alive():raise ValueError('Delivery probe running')
    if any(t.is_alive() for t in getattr(ui,'delivery_workers',{}).values()):raise ValueError('Delivery still running')
    output=Path(state_path(OUTPUT))
    active=read_json(state_path('reports/banking/merchant-route.json')).get('active') or {}
    key=active.get('request_id');j=Journal(JOURNAL)
    with j.db() as db:row=db.execute('SELECT * FROM transactions WHERE id=?',(key,)).fetchone()
    if not row or row['phase']!='uncertain':raise ValueError('No uncertain reserved request')
    intent=json.loads(row['before_json']);character=row['character']
    if ui.runtime.enabled(character) or not ui.safe_to_yield():raise ValueError('Pause merchant and release farmer input first')
    f,m=pair(ui,character);unchanged(intent,f,m)
    state={'phase':'request_verified','kind':'cancel_reserved_request','request_id':key,'character':character,'intent':intent,'created_at':time.time()}
    write_json(output,state)
    def work():
        try:
            run(ui,state,output_path=output)
            f,m=pair(ui,character)
            if not cancelled(intent,f,m):raise ValueError('Cancellation remains unverified')
            reservation=ui.runtime.journal.get(character,'delivery_reservation')
            if not reservation or reservation['request_id']!=key:raise ValueError('Reservation changed')
            evidence={'outcome':'request_cancelled_no_transfer','farmer':f,'merchant':m,'verified_at':time.time()}
            j.transition(key,'aborted',evidence)
            reservation.update(phase='cancelled_before_input',cancellation=evidence)
            save(ui.runtime.journal,character,reservation)
            state.update(phase='aborted_verified',evidence=evidence)
            attention=ui.runtime.journal.get(character,'attention') or {}
            if attention.get('request_id')==key:ui.runtime.journal.set(character,'attention',None)
        except Exception as error:state.update(error=str(error))
        write_json(output,state)
    ui.delivery_probe_thread=threading.Thread(target=work,daemon=True,name='cancel-reserved-request')
    ui.delivery_probe_thread.start()
    return {'started':True,'request_id':key}


def prove_request_replaced(intent,farmer,merchant):
    from conquest.merchants.delivery_cancel_probe import participants_unchanged
    participants_unchanged(intent,farmer,merchant)
    if farmer.get('trade') or merchant.get('trade') or farmer.get('request'):
        raise ValueError('A trade is still active')
    request=merchant.get('request')
    if request and request.get('participant')==farmer['character']:
        raise ValueError('Original request is still active')
    return {'outcome':'original_request_absent_no_transfer','farmer':farmer,'merchant':merchant,'verified_at':time.time()}
