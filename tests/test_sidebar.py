import tkinter as tk
from tkinter import ttk
from conquest.sidebar import ScrollableSidebar


def test_lower_controls_reachable_without_resizing_game_pane():
    root=tk.Tk();root.geometry('1000x700');root.withdraw()
    side=ScrollableSidebar(root);side.pack(side='left',fill='y');side.embedded()
    pane=tk.Frame(root);pane.pack(side='right',fill='both',expand=True)
    for i in range(40):ttk.Label(side.content,text=f'Field {i}').pack(fill='x',pady=3)
    footer=ttk.Button(side.content,text='Client tools');footer.pack()
    try:
        root.deiconify();root.update()
        size=(pane.winfo_width(),pane.winfo_height())
        assert side.canvas.yview()[1]<1
        side.canvas.yview_moveto(1);root.update()
        assert footer.winfo_rooty()+footer.winfo_height()<=side.canvas.winfo_rooty()+side.canvas.winfo_height()
        assert (pane.winfo_width(),pane.winfo_height())==size
        root.geometry('1300x1000');root.update()
        assert pane.winfo_width()>size[0]
        assert side.winfo_width()==500
    finally:root.destroy()
