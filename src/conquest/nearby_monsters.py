"""Species selection that automatically covers the group's live entity IDs."""
import tkinter as tk
from tkinter import ttk
from tkinter.font import nametofont


class NearbyMonsters(ttk.LabelFrame):
    def __init__(self,parent,on_toggle):
        super().__init__(parent,text='Nearby monster groups',padding=6)
        self.on_toggle = on_toggle
        self.names = {}
        self.tree = ttk.Treeview(self,columns=('selected','name','count'),show='headings',height=3,
                                selectmode='browse')
        for column,title,width in [('selected','✓',28),('name','Monster group',160),('count','Nearby IDs',100)]:
            self.tree.heading(column,text=title)
            self.tree.column(column,width=width,minwidth=width,stretch=column=='name')
        scroll = ttk.Scrollbar(self,orient='vertical',command=self.tree.yview)
        self.tree.configure(yscrollcommand=scroll.set)
        self.tree.grid(row=0,column=0,sticky='nsew')
        scroll.grid(row=0,column=1,sticky='ns')
        self.columnconfigure(0,weight=1)
        self.note = tk.StringVar(value='Embed a client to see nearby monsters')
        # Reserve two lines so changing counts/status cannot move controls below.
        note_frame = ttk.Frame(self,height=nametofont('TkDefaultFont').metrics('linespace')*2+4)
        note_frame.grid(row=1,column=0,columnspan=2,sticky='ew',pady=(4,0))
        note_frame.pack_propagate(False)
        ttk.Label(note_frame,textvariable=self.note,wraplength=350).pack(anchor='nw')
        self.tree.bind('<Button-1>',self.click)
        self.tree.bind('<space>',self.keyboard_toggle)
        self.tree.bind('<Return>',self.keyboard_toggle)

    def click(self,event):
        row = self.tree.identify_row(event.y)
        if row and self.tree.identify_region(event.x,event.y)=='cell':
            self.tree.focus_set()
            self.tree.focus(row)
            self.tree.selection_set(row)
            self.on_toggle(int(row))
            return 'break'

    def keyboard_toggle(self,event=None):
        row = self.tree.focus()
        if row and self.tree.exists(row):
            self.on_toggle(int(row))
        return 'break'

    def refresh(self,monsters,selected_types,*,available=True):
        selected = set(selected_types)
        groups = {}
        if not available:
            # Retain rows through transient moving-actor sample failures. The
            # dash explicitly means unavailable; these are never live targets.
            groups = {int(row):set() for row in self.tree.get_children()}
        for monster in monsters if available else []:
            type_id = monster.get('type_id',0)
            if type_id>0:
                self.names[type_id] = monster['name']
                groups.setdefault(type_id,set()).add(monster['entity_id'])
        for type_id in selected:
            groups.setdefault(type_id,set())
        records = sorted(groups,key=lambda type_id:(self.names.get(type_id,'').casefold(),type_id))
        present = {str(type_id) for type_id in records}
        for row in self.tree.get_children():
            if row not in present:
                self.tree.delete(row)
        for index,type_id in enumerate(records):
            row = str(type_id)
            values = ('✓' if type_id in selected else '',self.names.get(type_id,f'Monster type {type_id}'),
                      str(len(groups[type_id])) if available else '—')
            if self.tree.exists(row):
                if tuple(str(v) for v in self.tree.item(row,'values'))!=values:
                    self.tree.item(row,values=values)
                if self.tree.index(row) != index:
                    self.tree.move(row,'',index)
            else:
                self.tree.insert('',index,iid=row,values=values)
        if not available:
            note = 'Waiting for live monster observations'
        elif not any(groups.values()):
            note = 'No monsters nearby'
        else:
            note = f'{sum(map(len,groups.values()))} nearby IDs · New spawns included automatically'
        text = f'{note} · {len(selected)} groups selected'
        if self.note.get() != text:
            self.note.set(text)
