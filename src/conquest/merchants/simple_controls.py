"""Simple merchant switch preserving independently selected permissions."""
def toggle(ui, character):
    runtime=ui.runtime
    trading=runtime.enabled(character)
    refill=runtime.refill_enabled(character)
    if trading or refill:
        runtime.journal.set(character,'resume_permissions',{'trading':trading,'refill':refill})
        ui.pause(character)
        runtime.set_refill_enabled(character,False)
        return
    saved=runtime.journal.get(character,'resume_permissions',{'trading':True,'refill':True})
    if saved.get('trading'):ui.resume(character)
    if saved.get('refill'):ui.resume_refill(character)


def summary(state, *, now, global_stopped=False):
    from conquest.merchants.dashboard import countdown
    trading=bool(state.get('enabled'));refill=state.get('refill') or {}
    running=trading or refill.get('enabled',False)
    snapshot=state.get('snapshot') or {}
    attention=state.get('needs_attention')
    uncertain=any(p.get('phase')=='uncertain' for p in state.get('pending',[]))
    if global_stopped:title='STOPPED — Global Stop is active'
    elif not running:title='PAUSED — merchant automation is off'
    elif not state.get('connected'):title='WAITING FOR CLIENT — automation enabled'
    elif uncertain:title='NEEDS ATTENTION — transaction result must be reconciled'
    elif state.get('input_active'):title='WORKING — '+(state.get('activity') or 'updating shop')
    elif state.get('error'):title='WAITING — '+state['error'].get('note','check status details')
    else:title='ACTIVE — '+('ready for the next shop check' if not refill.get('pending') else 'waiting for safe farmer handoff')
    stock=f"Shop {len(snapshot.get('booth',[]))}/32 · Inventory {len(snapshot.get('inventory',[]))}/{snapshot.get('capacity','?')}"
    modes=('Trading & repricing: '+('enabled' if trading else 'paused')+' · Refill: '+
           ('every 15 min; next '+countdown(refill.get('next_check') or now,now) if refill.get('enabled') else 'paused'))
    warning=('Unresolved incident: '+attention.get('note','see status details')) if attention else ''
    return '\n'.join(x for x in (title,stock,modes,warning) if x)
