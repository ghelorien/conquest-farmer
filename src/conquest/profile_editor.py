"""Offline profile editor: preferences never include a client's observed state."""
import json
import tkinter as tk
from tkinter import ttk, filedialog, simpledialog, messagebox
from pathlib import Path
from conquest.character_profiles import SETTING_TYPES, write_json, context_for


def manage_profiles(registry,selected=None):
    from conquest.window_host import use_unaware_dpi
    use_unaware_dpi()  # Must precede the very first Tk HWND in this process.
    root=tk.Tk();root.title('Conquest — characters on this PC');root.geometry('900x670');root.minsize(640,480)
    from conquest.sidebar import ScrollableSidebar
    scroll=ScrollableSidebar(root);scroll.pack(fill='both',expand=True);frame=scroll.content
    ttk.Label(frame,text='Characters on this PC',font=('Segoe UI',18,'bold')).pack(anchor='w')
    ttk.Label(frame,text='Each character has its own role, credentials, routes and records. New characters start paused.\n'
        'Select a farmer to run here. Other PCs run their own selected farmer. Merchants share this desktop input coordinator.',wraplength=780).pack(fill='x',pady=8)
    tree=ttk.Treeview(frame,columns=('name','server','role'),show='headings',height=6)
    for key in ('name','server','role'):tree.heading(key,text=key.title());tree.column(key,width=170)
    tree.pack(fill='x')
    result=[None]
    label=tk.StringVar();role=tk.StringVar(value='Farmer');enabled=tk.BooleanVar(value=True)
    line=ttk.Frame(frame);line.pack(fill='x',pady=6)
    ttk.Label(line,text='Tab label').pack(side='left');ttk.Entry(line,textvariable=label,width=28).pack(side='left',padx=8)
    ttk.Combobox(line,textvariable=role,values=('Farmer','Merchant'),state='readonly',width=12).pack(side='left')
    ttk.Checkbutton(line,text='Enabled on this PC',variable=enabled).pack(side='left',padx=8)
    ttk.Label(frame,text='Overrides (JSON). {} uses the existing engine’s automatic defaults. Allowed settings:\n'+', '.join(SETTING_TYPES),wraplength=800).pack(fill='x')
    settings=tk.Text(frame,height=5,wrap='word');settings.pack(fill='x',pady=4)
    ttk.Label(frame,text='Trusted deliveries (JSON list). Each entry requires name, server and verified character_uid.\n'
        'These authorize automated delivery sources. Manual visitors are separate below and never enter this list. '
        'Example structure: [{"name":"Name","server":"America","character_uid":123}]',wraplength=800).pack(fill='x')
    trust=tk.Text(frame,height=3,wrap='word');trust.pack(fill='x',pady=4)
    ttk.Label(frame,text='Allowed manual visitors — exact profile-local permissions only. Revocation sends no game input and does not alter trusted delivery sources.',wraplength=800).pack(fill='x',pady=(6,2))
    visitors=ttk.Treeview(frame,columns=('name','server','uid'),show='headings',height=3,selectmode='extended')
    for key,title,width in (('name','Visitor',240),('server','Server',160),('uid','Verified UID',160)):
        visitors.heading(key,text=title);visitors.column(key,width=width)
    visitors.pack(fill='x')
    visitor_rows={}
    note=tk.StringVar(value='No client state or credentials are exported.')
    ttk.Label(frame,textvariable=note,wraplength=800).pack(fill='x',pady=8)

    def chosen():
        if not tree.selection():raise ValueError('Select a character')
        return registry.resolve(tree.selection()[0])
    def load(event=None):
        if not tree.selection():return
        p=chosen();label.set(p.label);role.set(p.role);enabled.set(p.local_enabled)
        settings.delete('1.0','end');settings.insert('1.0',json.dumps(registry.effective(p),indent=2))
        trust.delete('1.0','end');trust.insert('1.0',json.dumps(p.trusted_sources,indent=2))
        visitors.delete(*visitors.get_children());visitor_rows.clear()
        for index,row in enumerate(registry.list_visitors(p.id)):
            key=str(index);visitors.insert('','end',iid=key,values=(row['visitor_name'],row['visitor_server'],row['visitor_uid']))
            visitor_rows[key]={name:row[name] for name in ('target_profile_id','visitor_name','visitor_server','visitor_uid')}
        from conquest.profile_readiness import profile_transaction_blockers
        blockers=profile_transaction_blockers(registry.root,p.id)
        details=f'Profile {p.id}\nObserved level, skills and equipment are read from this character only when connected.'
        if visitor_rows:details+='\nRole change blocked: revoke the exact allowed manual visitor permissions first.'
        if blockers:
            first=blockers[0];details+=(f"\nLocal authority changes blocked for this profile: "
                f"{first.get('kind')} · {first.get('phase') or first.get('id') or 'needs reconciliation'}"
                + (f" (+{len(blockers)-1} more)" if len(blockers)>1 else ''))
        note.set(details)
    def refresh(profile_id=None):
        for item in tree.get_children():tree.delete(item)
        for p in registry.profiles():tree.insert('','end',iid=p.id,values=(p.label or p.name,p.server,p.role))
        if profile_id and tree.exists(profile_id):tree.selection_set(profile_id);load()
    def guarded(action):
        try:action()
        except (ValueError,KeyError,OSError,TypeError) as error:messagebox.showerror('Character settings',str(error),parent=root)
    def add():
        name=simpledialog.askstring('New character','Exact in-game name:',parent=root)
        if not name:return
        server=simpledialog.askstring('Server','Server (the current engine supports America):',initialvalue='America',parent=root)
        if not server:return
        p=registry.add(name,server,role.get());refresh(p.id)
    def save():
        p=chosen()
        registry.update(p.id,{'label':label.get(),'role':role.get(),'local_enabled':enabled.get(),
            'template':'automatic','overrides':json.loads(settings.get('1.0','end')),
            'trusted_sources':json.loads(trust.get('1.0','end'))})
        refresh(p.id);note.set('Saved. No running behavior was changed.')
    def revoke_visitors():
        p=chosen();selected=list(visitors.selection())
        exact=[dict(visitor_rows[key]) for key in selected]
        if not exact:raise ValueError('Select one or more exact manual visitor permissions to revoke')
        registry.revoke_visitors(p.id,exact,operator='profile UI')
        load();note.set('Selected exact manual visitor permission(s) revoked. Trusted delivery sources were unchanged.')
    def export():
        p=chosen();path=filedialog.asksaveasfilename(parent=root,defaultextension='.json',filetypes=[('Preferences','*.json')])
        if path:write_json(path,registry.export(p.id));note.set('Exported saved preferences only; credentials and trust identities excluded.')
    def import_settings():
        path=filedialog.askopenfilename(parent=root,filetypes=[('Preferences','*.json')])
        if not path:return
        name=simpledialog.askstring('Import preferences','Exact in-game name for the new character:',parent=root)
        if not name:return
        server=simpledialog.askstring('Server','Server:',initialvalue='America',parent=root)
        if server:refresh(registry.import_preferences(json.loads(Path(path).read_text()),name,server).id)
    def installation():
        p=chosen();path=filedialog.askopenfilename(parent=root,title='Select this PC’s ImConquer.exe',filetypes=[('Game client','ImConquer.exe')])
        if path:
            from conquest.client_attachment import remember_installation
            installed=remember_installation(context_for(p.id,registry.root),path)
            note.set('Installation saved on this PC: '+str(installed))
    def start():
        p=chosen()
        if not p.local_enabled:raise ValueError('Choose an enabled profile')
        result[0]=p.id;root.destroy()
    def save_template():
        p=chosen()
        title=simpledialog.askstring('Settings template','Template name:',parent=root)
        if not title:return
        key=registry.save_template(title,json.loads(settings.get('1.0','end')))
        registry.update(p.id,{'template':key,'overrides':{}})
        refresh(p.id)
    def login():
        p=chosen()
        username=simpledialog.askstring('Local account','Account login (stored encrypted on this PC):',parent=root)
        if username is None:return
        password=simpledialog.askstring('Local account','Password:',show='*',parent=root)
        if password is None:return
        from conquest.profile_secrets import save_login
        save_login(context_for(p.id,registry.root),username,password)
        note.set('Login saved encrypted for the selected profile. It is never exported.')
    def notification():
        p=chosen()
        value=simpledialog.askstring('Discord destination','Webhook URL (merchants share the shops channel on this PC):',show='*',parent=root)
        if value:
            from conquest.profile_secrets import save_notification_webhook
            save_notification_webhook(context_for(p.id,registry.root),value)
            note.set('Notification destination saved encrypted on this PC.')
    tree.bind('<<TreeviewSelect>>',load)
    row=ttk.Frame(frame);row.pack(fill='x')
    for index,(text,action) in enumerate([('Add character',add),('Save changes',save),('Revoke selected manual visitor',revoke_visitors),('Export settings',export),('Import settings',import_settings),('Game installation',installation),('Save as template',save_template),('Account login',login),('Discord destination',notification)]):
        ttk.Button(row,text=text,command=lambda f=action:guarded(f)).grid(row=index//3,column=index%3,sticky='ew',padx=2,pady=2)
        row.columnconfigure(index%3,weight=1)
    def wrap(event):
        width=max(240,event.width-24)
        for widget in frame.winfo_children():
            if isinstance(widget,ttk.Label) and int(widget.cget('wraplength') or 0)!=width:widget.configure(wraplength=width)
    frame.bind('<Configure>',wrap,add='+')
    ttk.Button(frame,text='Open Conquest with selected profile',command=lambda:guarded(start)).pack(fill='x',pady=12)
    refresh(selected);scroll.bind_children();root.mainloop()
    return result[0]
