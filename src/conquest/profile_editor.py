"""Offline profile editor: preferences never include a client's observed state."""
import json
import tkinter as tk
from tkinter import ttk, filedialog, simpledialog, messagebox
from pathlib import Path
from conquest.character_profiles import SETTING_TYPES, write_json, context_for


def manage_profiles(registry,selected=None):
    from conquest.profile_bootstrap import offline_edit_ready
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
        'Adding a farmer never adds it to this list. Example structure: [{"name":"Name","server":"America","character_uid":123}]',wraplength=800).pack(fill='x')
    trust=tk.Text(frame,height=3,wrap='word');trust.pack(fill='x',pady=4)
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
        note.set(f'Profile {p.id}\nObserved level, skills and equipment are read from this character only when connected.')
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
            'trusted_sources':json.loads(trust.get('1.0','end'))},stopped=True,pending=not offline_edit_ready(registry.root))
        refresh(p.id);note.set('Saved. No running behavior was changed.')
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
    tree.bind('<<TreeviewSelect>>',load)
    row=ttk.Frame(frame);row.pack(fill='x')
    for text,action in [('Add character',add),('Save changes',save),('Export settings',export),('Import settings',import_settings),('Game installation',installation)]:
        ttk.Button(row,text=text,command=lambda f=action:guarded(f)).pack(side='left',padx=2)
    ttk.Button(frame,text='Open Conquest with selected profile',command=lambda:guarded(start)).pack(fill='x',pady=12)
    refresh(selected);scroll.bind_children();root.mainloop()
    return result[0]
