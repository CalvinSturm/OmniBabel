import tkinter as tk
from tkinter import messagebox


def show_dialog(title, message, level="info", parent=None):
    owns_root = False
    root = parent

    if root is None:
        root = tk.Tk()
        root.withdraw()
        root.attributes("-topmost", True)
        owns_root = True

    dialogs = {
        "info": messagebox.showinfo,
        "warning": messagebox.showwarning,
        "error": messagebox.showerror,
    }
    dialog_fn = dialogs.get(level, messagebox.showinfo)

    try:
        dialog_fn(title, message, parent=None if owns_root else root)
    finally:
        if owns_root:
            root.destroy()
