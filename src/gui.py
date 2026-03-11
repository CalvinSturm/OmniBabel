import tkinter as tk
from tkinter import colorchooser, ttk, messagebox
import sounddevice as sd
import threading
import queue
from config import (
    DEFAULT_FONT_SIZE, DEFAULT_TEXT_COLOR, DEFAULT_BG_COLOR, DEFAULT_OPACITY, 
    AVAILABLE_MODELS, AVAILABLE_DEVICES, DEFAULT_MODEL_SIZE, DEFAULT_DEVICE
)
from src.dialogs import show_dialog

TRANSPARENT_KEY = "#000001"

class OverlayGUI:
    def __init__(self, on_close_callback, tts_toggle_callback, tts_device_callback, 
                 tts_voice_callback, get_voices_callback, model_change_callback,
                 initial_settings, settings_change_callback):
                 
        self.on_close_callback = on_close_callback
        self.tts_toggle_callback = tts_toggle_callback
        self.tts_device_callback = tts_device_callback
        self.tts_voice_callback = tts_voice_callback
        self.get_voices_callback = get_voices_callback
        self.model_change_callback = model_change_callback
        self.settings_change_callback = settings_change_callback
        
        # --- State ---
        self.font_size = initial_settings.get("font_size", DEFAULT_FONT_SIZE)
        self.text_color = DEFAULT_TEXT_COLOR
        self.bg_color = DEFAULT_BG_COLOR
        self.opacity = initial_settings.get("opacity", DEFAULT_OPACITY)
        self.wrap_width = 800
        self.tts_enabled = initial_settings.get("tts_enabled", False)
        self.selected_device_index = initial_settings.get("output_device_index")
        self.selected_voice_id = initial_settings.get("voice_id")
        
        # AI State
        self.current_model = initial_settings.get("model_size", DEFAULT_MODEL_SIZE)
        self.current_device = initial_settings.get("device", DEFAULT_DEVICE)
        self.is_loading_model = False # Lock to prevent double clicks
        
        # Interaction State
        self.settings_window = None
        self.mode = "none"
        self.pending_ui_actions = queue.SimpleQueue()
        self.is_stopping = False

        # --- Window Setup ---
        self.root = tk.Tk()
        self.root.title("Live Translator FG")
        self.root.overrideredirect(True)
        self.root.config(bg=TRANSPARENT_KEY)
        self.root.attributes("-topmost", True)
        self.root.wm_attributes("-transparentcolor", TRANSPARENT_KEY)
        self.root.attributes("-alpha", 1.0) 

        self.bg_window = tk.Toplevel(self.root)
        self.bg_window.title("Live Translator BG")
        self.bg_window.overrideredirect(True)
        self.bg_window.config(bg=self.bg_color)
        self.bg_window.attributes("-topmost", True)
        self.bg_window.attributes("-alpha", self.opacity)

        start_geo = "800x150+100+700"
        self.root.geometry(start_geo)
        self.bg_window.geometry(start_geo)

        self.setup_ui()
        self.root.update()
        self.sync_background_size()
        self.root.after(100, self.process_pending_updates)

    def setup_ui(self):
        self.text_var = tk.StringVar()
        self.text_var.set("Waiting for audio...\n(Shift+Drag Up/Down to Resize)")
        
        self.label = tk.Label(self.root, textvariable=self.text_var, 
                         font=("Helvetica", self.font_size, "bold"), 
                         fg=self.text_color, bg=TRANSPARENT_KEY, 
                         wraplength=self.wrap_width, justify="center")
        self.label.pack(expand=True, fill='both', padx=10, pady=10)

        for window in [self.root, self.bg_window, self.label]:
            window.bind("<Button-1>", self.on_click_start)
            window.bind("<B1-Motion>", self.on_drag_motion)
            window.bind("<ButtonRelease-1>", self.on_click_release)
            window.bind("<MouseWheel>", self.on_scroll)
            window.bind("<Button-3>", self.open_settings)
            window.bind("<Double-Button-3>", lambda e: self.on_close_callback())
        
        self.root.bind("<Configure>", self.on_configure)

    def on_configure(self, event):
        if self.mode == "none": self.sync_background_size()

    def sync_background_size(self):
        try:
            x = self.root.winfo_x()
            y = self.root.winfo_y()
            w = self.root.winfo_width()
            h = self.root.winfo_height()
            self.bg_window.geometry(f"{w}x{h}+{x}+{y}")
        except: pass

    def update_text(self, text):
        self.text_var.set(text)
        self.root.update_idletasks() 
        self.sync_background_size()

    def schedule_text_update(self, text):
        if not self.is_stopping:
            self.pending_ui_actions.put(lambda: self.update_text(text))

    def set_tts_enabled(self, is_enabled):
        self.tts_enabled = is_enabled

    def schedule_ui_action(self, action):
        if not self.is_stopping:
            self.pending_ui_actions.put(action)

    def persist_settings(self):
        self.settings_change_callback({
            "model_size": self.current_model,
            "device": self.current_device,
            "font_size": self.font_size,
            "opacity": self.opacity,
            "tts_enabled": self.tts_enabled,
            "voice_id": self.selected_voice_id,
            "output_device_index": self.selected_device_index,
        })

    def show_dialog(self, title, message, level="info"):
        self.schedule_ui_action(lambda: show_dialog(title, message, level=level, parent=self.root))

    def process_pending_updates(self):
        if self.is_stopping:
            return
        try:
            while True:
                action = self.pending_ui_actions.get_nowait()
                action()
        except queue.Empty:
            pass
        try:
            self.root.after(100, self.process_pending_updates)
        except tk.TclError:
            self.is_stopping = True

    # --- Interaction Logic ---
    def on_click_start(self, event):
        self.bg_window.lift()
        self.root.lift()
        if event.state & 0x0001: 
            self.mode = "resize_font"
            self.resize_start_y = event.y_root
            self.initial_font_size = self.font_size
        else:
            self.mode = "move"
            self.drag_start_x = event.x_root
            self.drag_start_y = event.y_root
            self.win_start_x = self.root.winfo_x()
            self.win_start_y = self.root.winfo_y()

    def on_drag_motion(self, event):
        if self.mode == "move":
            dx = event.x_root - self.drag_start_x
            dy = event.y_root - self.drag_start_y
            self.root.geometry(f"+{self.win_start_x+dx}+{self.win_start_y+dy}")
            self.bg_window.geometry(f"+{self.win_start_x+dx}+{self.win_start_y+dy}")
        elif self.mode == "resize_font":
            delta = self.resize_start_y - event.y_root
            scale_factor = delta // 5 
            new_size = max(10, min(self.initial_font_size + scale_factor, 150)) 
            if new_size != self.font_size:
                self.font_size = int(new_size)
                self.label.config(font=("Helvetica", self.font_size, "bold"))
                self.root.update_idletasks()
                self.sync_background_size()

    def on_click_release(self, event): self.mode = "none"

    def on_scroll(self, event):
        if event.delta > 0: self.opacity = min(1.0, self.opacity + 0.05)
        else: self.opacity = max(0.0, self.opacity - 0.05)
        self.apply_opacity()

    def apply_opacity(self): self.bg_window.attributes("-alpha", max(0.01, self.opacity))

    # --- Settings Window ---
    def get_audio_devices(self):
        try:
            return [f"{i}: {d['name']}" for i, d in enumerate(sd.query_devices()) if d['max_output_channels'] > 0]
        except: return []

    def open_settings(self, event=None):
        if self.settings_window:
            self.settings_window.lift()
            return

        sw = tk.Toplevel(self.root)
        sw.title("Settings")
        sw.geometry("380x750") 
        sw.attributes("-topmost", True)
        self.settings_window = sw
        sw.protocol("WM_DELETE_WINDOW", lambda: [sw.destroy(), setattr(self, 'settings_window', None)])

        # --- AI Settings ---
        tk.Label(sw, text="AI Model Settings", font=("Arial", 12, "bold")).pack(pady=10)
        
        tk.Label(sw, text="Model Size").pack()
        model_combo = ttk.Combobox(sw, values=AVAILABLE_MODELS, state="readonly", width=30)
        model_combo.set(self.current_model)
        model_combo.pack(pady=5)
        
        tk.Label(sw, text="Processing Device").pack()
        device_combo = ttk.Combobox(sw, values=AVAILABLE_DEVICES, state="readonly", width=30)
        device_combo.set(self.current_device)
        device_combo.pack(pady=5)
        
        # New: Status Label
        status_label = tk.Label(sw, text="", fg="blue")
        status_label.pack()

        # New: Threaded Apply Function
        def apply_ai_settings():
            if self.is_loading_model: return

            m = model_combo.get()
            d = device_combo.get()

            if m == self.current_model and d == self.current_device:
                return

            self.is_loading_model = True
            status_label.config(text="Loading Model... Please Wait...", fg="blue")
            
            # Disable buttons
            apply_btn.config(state="disabled")

            def run_load():
                success, msg, active_model, active_device = self.model_change_callback(m, d)
                
                def update_ui():
                    self.is_loading_model = False
                    apply_btn.config(state="normal")
                    self.current_model = active_model
                    self.current_device = active_device
                    model_combo.set(self.current_model)
                    device_combo.set(self.current_device)
                    self.persist_settings()

                    if success:
                        status_label.config(text=f"Loaded: {active_model} ({active_device})", fg="green")
                        messagebox.showinfo("Success", f"Model updated to {active_model} on {active_device}")
                    else:
                        status_label.config(text=f"Loaded fallback: {active_model} ({active_device})", fg="#b36b00")
                        messagebox.showwarning("Model Load Warning", msg)
                
                self.schedule_ui_action(update_ui)

            threading.Thread(target=run_load, daemon=True).start()
        
        apply_btn = tk.Button(sw, text="Apply Changes", command=apply_ai_settings, bg="#dddddd")
        apply_btn.pack(pady=5)
        
        tk.Label(sw, text="-------------------------").pack(pady=5)

        # --- Appearance ---
        tk.Label(sw, text="Appearance", font=("Arial", 12, "bold")).pack(pady=5)

        tk.Label(sw, text="Font Size").pack()
        s_size = tk.Scale(sw, from_=10, to=100, orient="horizontal", command=self.set_font_size_from_slider)
        s_size.set(self.font_size)
        s_size.pack(fill="x", padx=20)
        
        tk.Label(sw, text="Max Width").pack()
        s_width = tk.Scale(sw, from_=300, to=1800, orient="horizontal", command=self.set_width_from_slider)
        s_width.set(self.wrap_width)
        s_width.pack(fill="x", padx=20)

        tk.Label(sw, text="Opacity").pack()
        s_op = tk.Scale(sw, from_=0.0, to=1.0, resolution=0.05, orient="horizontal", command=self.set_opacity_from_slider)
        s_op.set(self.opacity)
        s_op.pack(fill="x", padx=20)

        tk.Button(sw, text="Change Text Color", command=self.pick_color).pack(pady=2)
        tk.Button(sw, text="Change Background Color", command=self.pick_bg_color).pack(pady=2)

        # --- Audio ---
        tk.Label(sw, text="Audio", font=("Arial", 12, "bold")).pack(pady=10)
        
        tts_var = tk.BooleanVar(value=self.tts_enabled) 
        tk.Checkbutton(
            sw,
            text="Read Translations Aloud",
            variable=tts_var,
            command=lambda: self._on_tts_toggle(tts_var.get())
        ).pack()

        tk.Label(sw, text="Voice Personality:").pack()
        available_voices = self.get_voices_callback() 
        voice_names = [v["name"] for v in available_voices]
        voice_combo = ttk.Combobox(sw, values=voice_names, state="readonly", width=40)
        voice_combo.pack(pady=2, padx=10)

        if self.selected_voice_id:
            for v in available_voices:
                if v["id"] == self.selected_voice_id: voice_combo.set(v["name"]); break
        elif voice_names: voice_combo.current(0)

        def on_voice_change(event):
            name = voice_combo.get()
            for v in available_voices:
                if v["name"] == name:
                    self.selected_voice_id = v["id"]
                    self.tts_voice_callback(v["id"])
                    self.persist_settings()
                    break
        voice_combo.bind("<<ComboboxSelected>>", on_voice_change)

        tk.Label(sw, text="Output Device:").pack()
        device_list = self.get_audio_devices()
        dev_combo = ttk.Combobox(sw, values=device_list, width=40)
        dev_combo.pack(pady=2, padx=10)
        
        if self.selected_device_index is not None:
            for d in device_list:
                if d.startswith(f"{self.selected_device_index}:"): dev_combo.set(d)
        
        def on_dev_change(event):
            sel = dev_combo.get()
            if sel:
                self.selected_device_index = int(sel.split(":")[0])
                self.tts_device_callback(self.selected_device_index)
                self.persist_settings()
        dev_combo.bind("<<ComboboxSelected>>", on_dev_change)

    def _on_tts_toggle(self, is_enabled):
        self.tts_enabled = is_enabled
        self.tts_toggle_callback(is_enabled)
        self.persist_settings()

    def set_font_size_from_slider(self, val):
        self.font_size = int(val)
        self.label.config(font=("Helvetica", self.font_size, "bold"))
        self.root.update_idletasks()
        self.sync_background_size()
        self.persist_settings()
    def set_width_from_slider(self, val):
        self.wrap_width = int(val)
        self.label.config(wraplength=self.wrap_width)
        self.root.update_idletasks()
        self.sync_background_size()
    def set_opacity_from_slider(self, val):
        self.opacity = float(val)
        self.apply_opacity()
        self.persist_settings()
    def pick_color(self):
        c = colorchooser.askcolor(title="Text Color")[1]
        if c: self.text_color = c; self.label.config(fg=c)
    def pick_bg_color(self):
        c = colorchooser.askcolor(title="Background Color")[1]
        if c: self.bg_color = c; self.bg_window.config(bg=c)
    def start(self): self.root.mainloop()
    def stop(self):
        if self.is_stopping:
            return
        self.is_stopping = True
        try:
            if self.settings_window and self.settings_window.winfo_exists():
                self.settings_window.destroy()
        except tk.TclError:
            pass
        try:
            if self.bg_window.winfo_exists():
                self.bg_window.destroy()
        except tk.TclError:
            pass
        try:
            if self.root.winfo_exists():
                self.root.quit()
                self.root.destroy()
        except tk.TclError:
            pass
