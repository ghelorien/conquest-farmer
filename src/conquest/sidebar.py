"""A bounded farmer sidebar whose lower controls remain reachable on resize."""
import tkinter as tk
from tkinter import ttk


class ScrollableSidebar(ttk.Frame):
    def __init__(self, parent):
        super().__init__(parent)
        self.canvas=tk.Canvas(self,highlightthickness=0,borderwidth=0)
        self.scroll=ttk.Scrollbar(self,orient='vertical',command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=self.scroll.set)
        self.scroll.pack(side='right',fill='y')
        self.canvas.pack(side='left',fill='both',expand=True)
        self.content=ttk.Frame(self.canvas,padding=12)
        self.window=self.canvas.create_window(0,0,window=self.content,anchor='nw')
        self.canvas.bind('<Configure>',self.refresh)
        self.content.bind('<Configure>',self.refresh)
        self.canvas.bind('<MouseWheel>',self.wheel)
        self.wheel_tag='FarmerSidebarWheel'+str(id(self))
        self.bind_class(self.wheel_tag,'<MouseWheel>',self.wheel)

    def wheel(self,event):
        if event.widget.winfo_class() in ('Treeview','TCombobox','Text','Listbox'):
            return None
        if self.content.winfo_reqheight()>self.canvas.winfo_height():
            self.canvas.yview_scroll(-int(event.delta/120),'units')
        return 'break'

    def bind_children(self):
        def visit(widget):
            tags=widget.bindtags()
            if self.wheel_tag not in tags:
                widget.bindtags((*tags,self.wheel_tag))
            for child in widget.winfo_children():visit(child)
        visit(self.content)

    def refresh(self,event=None):
        width=max(1,self.canvas.winfo_width())
        height=max(self.canvas.winfo_height(),self.content.winfo_reqheight())
        self.canvas.itemconfigure(self.window,width=width,height=height)
        self.canvas.configure(scrollregion=(0,0,width,height))

    def embedded(self):
        self.pack_configure(expand=False,fill='y')
        self.configure(width=500)
        self.pack_propagate(False)

    def compact(self):
        self.pack_configure(expand=True,fill='both')
        self.pack_propagate(True)
