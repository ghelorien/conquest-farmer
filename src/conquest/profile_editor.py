"""Offline profile editor: preferences never include a client's observed state."""
import json
import tkinter as tk
from tkinter import ttk, filedialog, simpledialog, messagebox
from pathlib import Path
from conquest.character_profiles import ROLES, SETTING_TYPES, write_json, context_for


OPEN_SELECTED_CHARACTER = 'Open selected character'


def profile_choice_rows(registry):
    """Return the concise rows used by the first, always-visible chooser."""
    return [(profile.id, (profile.label or profile.name, profile.server, profile.role))
            for profile in registry.profiles()]


def selected_profile_id(rows,preferred=None,previous=None):
    """Keep the chooser actionable: prefer an explicit choice, then a prior one."""
    ids={profile_id for profile_id,_ in rows}
    return preferred if preferred in ids else previous if previous in ids else (rows[0][0] if rows else None)


def new_character_role(value):
    """Validate the explicit role requested while creating a profile."""
    role=value.strip().title() if isinstance(value,str) else ''
    if role not in ROLES:raise ValueError('Role must be Farmer or Merchant')
    return role


def open_on_profile_double_click(event,tree,action):
    """Only activate a profile when the double-click hit a real chooser row."""
    if tree.identify_row(event.y):action()


def toggle_advanced_section(section,button):
    if section.winfo_manager():
        section.pack_forget();button.configure(text='Show character settings')
    else:
        section.pack(fill='x',pady=(6,0));button.configure(text='Hide character settings')


def manage_profiles(registry,selected=None):
    from conquest.window_host import use_unaware_dpi
    use_unaware_dpi()  # Must precede the very first Tk HWND in this process.
    root=tk.Tk();root.title('Conquest — characters on this PC');root.geometry('900x670');root.minsize(640,480)
    from conquest.sidebar import ScrollableSidebar
    scroll=ScrollableSidebar(root);scroll.pack(fill='both',expand=True);frame=scroll.content
    ttk.Label(frame,text='Choose a character to open',font=('Segoe UI',18,'bold')).pack(anchor='w')
    chooser_note=tk.StringVar(value='Select a character below, then open it.')
    ttk.Label(frame,textvariable=chooser_note,wraplength=780).pack(fill='x',pady=(2,8))
    tree=ttk.Treeview(frame,columns=('name','server','role'),show='headings',height=7)
    for key in ('name','server','role'):tree.heading(key,text=key.title());tree.column(key,width=170)
    tree.pack(fill='x')
    result=[None]
    quick_actions=ttk.Frame(frame);quick_actions.pack(fill='x',pady=(8,4))
    open_button=ttk.Button(quick_actions,text=OPEN_SELECTED_CHARACTER)
    open_button.pack(fill='x')
    quick_secondary=ttk.Frame(quick_actions);quick_secondary.pack(fill='x',pady=(4,0))
    ttk.Button(quick_secondary,text='Add character',command=lambda:guarded(add)).pack(side='left',expand=True,fill='x',padx=(0,2))
    ttk.Button(quick_secondary,text='Import settings',command=lambda:guarded(import_settings)).pack(side='left',expand=True,fill='x',padx=2)
    advanced_button=ttk.Button(quick_secondary,text='Show character settings',command=lambda:toggle_advanced_section(advanced,advanced_button))
    advanced_button.pack(side='left',expand=True,fill='x',padx=(2,0))

    advanced=ttk.LabelFrame(frame,text='Character settings and advanced options',padding=8)
    label=tk.StringVar();role=tk.StringVar(value='Farmer');enabled=tk.BooleanVar(value=True)
    ttk.Label(advanced,text='Select a character above to edit its settings.').pack(anchor='w',pady=(0,6))
    line=ttk.Frame(advanced);line.pack(fill='x',pady=6)
    ttk.Label(line,text='Tab label').pack(side='left');ttk.Entry(line,textvariable=label,width=28).pack(side='left',padx=8)
    ttk.Combobox(line,textvariable=role,values=('Farmer','Merchant'),state='readonly',width=12).pack(side='left')
    ttk.Checkbutton(line,text='Enabled on this PC',variable=enabled).pack(side='left',padx=8)
    ttk.Label(advanced,text='Overrides (JSON) — {} keeps automatic defaults. Allowed: '+', '.join(SETTING_TYPES),wraplength=800).pack(fill='x')
    settings=tk.Text(advanced,height=5,wrap='word');settings.pack(fill='x',pady=4)
    ttk.Label(advanced,text='Trusted delivery sources (JSON): name, server, verified character_uid.',wraplength=800).pack(fill='x')
    trust=tk.Text(advanced,height=3,wrap='word');trust.pack(fill='x',pady=4)
    ttk.Label(advanced,text='Allowed manual visitors (exact profile-local permissions)',wraplength=800).pack(fill='x',pady=(6,2))
    visitors=ttk.Treeview(advanced,columns=('name','server','uid'),show='headings',height=3,selectmode='extended')
    for key,title,width in (('name','Visitor',240),('server','Server',160),('uid','Verified UID',160)):
        visitors.heading(key,text=title);visitors.column(key,width=width)
    visitors.pack(fill='x')
    visitor_rows={}
    note=tk.StringVar(value='No client state or credentials are exported.')
    ttk.Label(advanced,textvariable=note,wraplength=800).pack(fill='x',pady=8)

    def chosen():
        if not tree.selection():raise ValueError('Select a character')
        return registry.resolve(tree.selection()[0])
    def load(event=None):
        if not tree.selection():return
        p=chosen();label.set(p.label);role.set(p.role);enabled.set(p.local_enabled)
        chooser_note.set(f'Selected: {p.label or p.name}. Choose “{OPEN_SELECTED_CHARACTER}” when ready.')
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
        prior=tree.selection()
        for item in tree.get_children():tree.delete(item)
        rows=profile_choice_rows(registry)
        for profile_id_value,values in rows:tree.insert('','end',iid=profile_id_value,values=values)
        target=selected_profile_id(rows,profile_id,prior[0] if prior else None)
        if target:
            tree.selection_set(target);tree.focus(target);load()
        else:chooser_note.set('No characters are saved on this PC. Add or import a character to continue.')
    def guarded(action):
        try:action()
        except (ValueError,KeyError,OSError,TypeError) as error:messagebox.showerror('Character settings',str(error),parent=root)
    def add():
        name=simpledialog.askstring('New character','Exact in-game name:',parent=root)
        if not name:return
        server=simpledialog.askstring('Server','Server (the current engine supports America):',initialvalue='America',parent=root)
        if not server:return
        requested_role=simpledialog.askstring('New character role','Role for this character: Farmer or Merchant',
            initialvalue='Farmer',parent=root)
        if requested_role is None:return
        p=registry.add(name,server,new_character_role(requested_role));refresh(p.id)
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
    tree.bind('<Double-1>',lambda event:open_on_profile_double_click(event,tree,lambda:guarded(start)))
    open_button.configure(command=lambda:guarded(start))
    row=ttk.Frame(advanced);row.pack(fill='x')
    for index,(text,action) in enumerate([('Save changes',save),('Revoke selected manual visitor',revoke_visitors),('Export settings',export),('Game installation',installation),('Save as template',save_template),('Account login',login),('Discord destination',notification)]):
        ttk.Button(row,text=text,command=lambda f=action:guarded(f)).grid(row=index//3,column=index%3,sticky='ew',padx=2,pady=2)
        row.columnconfigure(index%3,weight=1)

    def wrap(event):
        width=max(240,event.width-24)
        def configure_labels(parent):
            for widget in parent.winfo_children():
                if isinstance(widget,ttk.Label) and int(widget.cget('wraplength') or 0)!=width:widget.configure(wraplength=width)
                configure_labels(widget)
        configure_labels(frame)
    frame.bind('<Configure>',wrap,add='+')
    refresh(selected);scroll.bind_children();root.mainloop()
    return result[0]
