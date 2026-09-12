"""Profile-facing UI adapters; behavior policy remains in the existing engines."""
import json
import os
from tkinter import ttk, messagebox
from conquest.character_context import registry, current, profile_status


def normalize_command(body):
    r=registry()
    if not r:return body
    body=dict(body)
    profile_id=body.pop('profile_id',None)
    if profile_id is not None:
        p=r.resolve(profile_id)
        if p.id!=profile_id:raise ValueError('profile_id must be a stable profile ID')
        if 'character' in body and r.resolve(body['character']).id!=p.id:
            raise ValueError('Profile ID and character disagree')
        from conquest.character_context import resolve_merchant
        body['character']=resolve_merchant(p.id)
    elif 'character' in body:
        from conquest.character_context import resolve_merchant, ProfileName
        if not isinstance(body['character'],ProfileName):
            body['character']=resolve_merchant(r.resolve(body['character']).id)
    return body


def settings_description(profile):
    r=registry();values=r.effective(profile)
    if profile.role!='Farmer':return 'Merchant rules come from the existing engine. No farmer preferences apply.'
    from conquest.trial import TrialConfig
    supported=set(TrialConfig.model_fields)|{'recover_after_death'}
    lines=['Automatic skill, ammunition and route choices: existing engine.']
    for key,value in values.items():
        lines.append(f'{key}: {value}' + (' — unavailable: engine does not expose this setting' if key not in supported else ''))
    if not values:lines.append('No character overrides.')
    lines.append('Attack range is capped by the engine’s memory-derived range. Overrides cannot unlock skills or input.')
    return '\n'.join(lines)


def edit_profiles(ui,profile_id=None):
    from conquest.profile_bootstrap import offline_edit_ready
    if (not ui.safe_to_yield() or ui.coordinator.owner or ui.calibrating
            or any(ui.runtime.enabled(c) or ui.runtime.refill_enabled(c) for c in ui.runtime.recoveries)
            or not offline_edit_ready(registry().root)):
        messagebox.showerror('Character settings','Stop farming, merchant management and refilling, and reconcile unfinished transactions before editing profiles.',parent=ui.root)
        return
    ui.app.profile_editor_requested=profile_id or os.environ.get('CONQUEST_PROFILE_ID')
    if ui.app.close() is False:ui.app.profile_editor_requested=None


def install(ui):
    r=registry()
    if not r:return
    ctx=current()
    if ctx and ctx.profile.role=='Farmer':
        ui.notebook.tab(ui.frames['Farmer'],text=(ctx.profile.label or ctx.profile.name)+' · Farmer')
    else:ui.notebook.hide(ui.frames['Farmer'])
    for p in r.profiles():
        from conquest.character_context import resolve_merchant
        if p.role=='Merchant' and p.local_enabled and p.server=='America':
            ui.notebook.tab(ui.frames[resolve_merchant(p.id)],text=(p.label or p.name)+' · Merchant')
            continue
        if ctx and p.id==ctx.profile.id and p.role=='Farmer':continue
        frame=ttk.Frame(ui.notebook,padding=16)
        ui.notebook.add(frame,text=(p.label or p.name)+' · '+p.role)
        ttk.Label(frame,text=f'{p.name} · {p.server}\nProfile {p.id}',font=('Segoe UI',12,'bold')).pack(anchor='w')
        reason=('Disabled on this PC' if not p.local_enabled else 'This server is not qualified by the current engine'
                if p.server!='America' else 'Saved farmer; select it at restart to use this desktop input environment')
        ttk.Label(frame,text=reason,wraplength=680).pack(anchor='w',pady=10)
        ttk.Label(frame,text=settings_description(p),wraplength=680).pack(anchor='w')
        ttk.Label(frame,text='Observed level, class, learned skills and equipment: unavailable until this character connects.',wraplength=680).pack(anchor='w',pady=12)
        ttk.Button(frame,text='Select / edit profile (restart)',command=lambda pid=p.id:edit_profiles(ui,pid)).pack(anchor='w')
    ttk.Button(ui.frames['Overview'],text='Manage characters / import or export settings (restart)',command=lambda:edit_profiles(ui)).pack(anchor='w',padx=20,pady=8)
    if ctx and ctx.profile.role=='Farmer':
        box=ttk.LabelFrame(ui.app.sidebar,text='Character preferences',padding=8);box.pack(fill='x')
        ttk.Label(box,text=settings_description(ctx.profile),wraplength=420).pack(anchor='w')
        ttk.Button(box,text='Edit preferences (restart)',command=lambda:edit_profiles(ui)).pack(anchor='w')
        import tkinter as tk
        from conquest.profile_capabilities import CapabilityView
        view=CapabilityView();text=tk.StringVar(value=view.text())
        ttk.Label(box,textvariable=text,wraplength=420).pack(anchor='w',pady=8)
        def refresh():
            if ui.closed:return
            view.refresh(ui.app.observer,current());text.set(view.text())
            ui.root.after(1000,refresh)
        ui.root.after(1000,refresh)
    ui.root.title('Conquest — '+ ' · '.join(p.label or p.name for p in r.profiles() if p.local_enabled))


def diagnostics(ui,character):
    from conquest.character_context import merchant_context
    ctx=merchant_context(character);observer=ui.runtime.observers.get(character);host=ui.hosts.get(character)
    # Explicit allowlist. Neither bridge tokens nor arbitrary exception strings
    # or credentials are copied into support diagnostics.
    return {'profile_id':ctx.profile.id if ctx else None,'character':str(character),
        'attached':bool(host and host.saved),'memory_connected':observer is not None,
        'stage':'attachment' if observer else 'discovery_or_identity',
        'geometry':ui.layout_status.get(character,{}),
        'automation_ready':bool(ui.runtime.status().get(character,{}).get('ready'))}


def copy_diagnostics(ui,character):
    ui.root.clipboard_clear();ui.root.clipboard_append(json.dumps(diagnostics(ui,character),indent=2))
