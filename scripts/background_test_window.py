"""Temporary, empty foreground surface for checking window-directed input."""

import tkinter as tk

window = tk.Tk()
window.title("Conquest background input test")
window.geometry("420x150+80+80")
tk.Label(window, text="Background input test", font=("Segoe UI", 15)).pack(pady=12)
tk.Label(
    window,
    text="This empty test window closes automatically in 45 seconds.\nIt does not send game input or record typed text.",
).pack()
tk.Button(window, text="Close test window", command=window.destroy).pack(pady=12)
window.after(300, window.focus_force)
window.after(45000, window.destroy)
window.mainloop()
