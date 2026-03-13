import queue
import threading
import tkinter as tk
from tkinter import colorchooser, messagebox, ttk

import sounddevice as sd

from config import (
    AVAILABLE_DEVICES,
    AVAILABLE_MODELS,
    DEFAULT_BG_COLOR,
    DEFAULT_DEBUG_LOGGING,
    DEFAULT_DEVICE,
    DEFAULT_FONT_SIZE,
    DEFAULT_MAX_UTTERANCE_SECONDS,
    DEFAULT_MODEL_SIZE,
    DEFAULT_MIN_UTTERANCE_SECONDS,
    DEFAULT_OPACITY,
    DEFAULT_SOURCE_LANGUAGE,
    DEFAULT_TARGET_LANGUAGE,
    DEFAULT_TASK,
    DEFAULT_TEXT_COLOR,
    DEFAULT_UTTERANCE_END_SILENCE_SECONDS,
    DEFAULT_VAD_ENERGY_THRESHOLD,
    LANGUAGE_CHOICES,
    TARGET_LANGUAGE_CHOICES,
    TASK_CHOICES,
)
from src.dialogs import show_dialog
from src.streaming_contracts import PlaybackState, PlaybackStatus, TTSJobSource

TRANSPARENT_KEY = "#000001"
LANGUAGE_LABELS = dict(LANGUAGE_CHOICES)
LANGUAGE_CODES_BY_LABEL = {label: code for code, label in LANGUAGE_CHOICES}
TARGET_LABELS = dict(TARGET_LANGUAGE_CHOICES)
TASK_LABELS = dict(TASK_CHOICES)
TASK_CODES_BY_LABEL = {label: code for code, label in TASK_CHOICES}


class OverlayGUI:
    def __init__(
        self,
        on_close_callback,
        tts_toggle_callback,
        tts_device_callback,
        tts_voice_callback,
        get_voices_callback,
        model_change_callback,
        translation_settings_callback,
        initial_settings,
        settings_change_callback,
    ):
        self.on_close_callback = on_close_callback
        self.tts_toggle_callback = tts_toggle_callback
        self.tts_device_callback = tts_device_callback
        self.tts_voice_callback = tts_voice_callback
        self.get_voices_callback = get_voices_callback
        self.model_change_callback = model_change_callback
        self.translation_settings_callback = translation_settings_callback
        self.settings_change_callback = settings_change_callback

        self.font_size = initial_settings.get("font_size", DEFAULT_FONT_SIZE)
        self.text_color = DEFAULT_TEXT_COLOR
        self.bg_color = DEFAULT_BG_COLOR
        self.opacity = initial_settings.get("opacity", DEFAULT_OPACITY)
        self.wrap_width = 800
        self.tts_enabled = initial_settings.get("tts_enabled", False)
        self.selected_device_index = initial_settings.get("output_device_index")
        self.selected_voice_id = initial_settings.get("voice_id")

        self.current_model = initial_settings.get("model_size", DEFAULT_MODEL_SIZE)
        self.current_device = initial_settings.get("device", DEFAULT_DEVICE)
        self.current_source_language = initial_settings.get("source_language", DEFAULT_SOURCE_LANGUAGE)
        self.current_target_language = initial_settings.get("target_language", DEFAULT_TARGET_LANGUAGE)
        self.current_task = initial_settings.get("task", DEFAULT_TASK)
        self.current_vad_energy_threshold = initial_settings.get("vad_energy_threshold", DEFAULT_VAD_ENERGY_THRESHOLD)
        self.current_utterance_end_silence_seconds = initial_settings.get(
            "utterance_end_silence_seconds", DEFAULT_UTTERANCE_END_SILENCE_SECONDS
        )
        self.current_min_utterance_seconds = initial_settings.get(
            "min_utterance_seconds", DEFAULT_MIN_UTTERANCE_SECONDS
        )
        self.current_max_utterance_seconds = initial_settings.get(
            "max_utterance_seconds", DEFAULT_MAX_UTTERANCE_SECONDS
        )
        self.debug_logging_enabled = initial_settings.get("debug_logging_enabled", DEFAULT_DEBUG_LOGGING)
        self.is_loading_model = False
        self.runtime_status = {
            "runtime_state": "starting",
            "message": "Starting up",
            "detected_language": None,
            "source_language": self.current_source_language,
            "target_language": self.current_target_language,
            "task": self.current_task,
            "model_size": self.current_model,
            "device": self.current_device,
            "debug_logging_enabled": self.debug_logging_enabled,
            "vad_energy_threshold": self.current_vad_energy_threshold,
            "utterance_end_silence_seconds": self.current_utterance_end_silence_seconds,
            "min_utterance_seconds": self.current_min_utterance_seconds,
            "max_utterance_seconds": self.current_max_utterance_seconds,
            "noise_floor": 0.0,
            "ambient_calibrated": False,
        }
        self.committed_text = ""
        self.provisional_text = ""
        self.playback_state = PlaybackState(
            status=PlaybackStatus.IDLE,
            active_job_id=None,
            queued_jobs=0,
            source=None,
        )

        self.settings_window = None
        self.mode = "none"
        self.pending_ui_actions = queue.SimpleQueue()
        self.is_stopping = False

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

        start_geo = "900x180+100+700"
        self.root.geometry(start_geo)
        self.bg_window.geometry(start_geo)

        self.setup_ui()
        self.root.update()
        self.sync_background_size()
        self.root.after(100, self.process_pending_updates)

    def setup_ui(self):
        self.text_var = tk.StringVar(value="Waiting for audio...\n(Shift+Drag Up/Down to Resize)")
        self.provisional_text_var = tk.StringVar(value="")
        self.runtime_status_var = tk.StringVar(value=self._format_runtime_status())
        self.playback_status_var = tk.StringVar(value=self._format_playback_status())

        self.content_frame = tk.Frame(self.root, bg=TRANSPARENT_KEY)
        self.content_frame.pack(expand=True, fill="both", padx=10, pady=10)

        self.label = tk.Label(
            self.content_frame,
            textvariable=self.text_var,
            font=("Helvetica", self.font_size, "bold"),
            fg=self.text_color,
            bg=TRANSPARENT_KEY,
            wraplength=self.wrap_width,
            justify="center",
        )
        self.label.pack(expand=True, fill="both")

        self.provisional_label = tk.Label(
            self.content_frame,
            textvariable=self.provisional_text_var,
            font=("Helvetica", max(12, self.font_size // 2), "italic"),
            fg="#d0d0d0",
            bg=TRANSPARENT_KEY,
            wraplength=self.wrap_width,
            justify="center",
        )
        self.provisional_label.pack(fill="x", pady=(2, 0))

        self.status_label = tk.Label(
            self.content_frame,
            textvariable=self.runtime_status_var,
            font=("Helvetica", max(10, self.font_size // 3)),
            fg="#d9d9d9",
            bg=TRANSPARENT_KEY,
            wraplength=self.wrap_width,
            justify="center",
        )
        self.status_label.pack(fill="x", pady=(4, 0))

        self.playback_label = tk.Label(
            self.content_frame,
            textvariable=self.playback_status_var,
            font=("Helvetica", max(9, self.font_size // 4)),
            fg="#bfbfbf",
            bg=TRANSPARENT_KEY,
            wraplength=self.wrap_width,
            justify="center",
        )
        self.playback_label.pack(fill="x", pady=(2, 0))

        for window in [self.root, self.bg_window, self.content_frame, self.label, self.provisional_label, self.status_label, self.playback_label]:
            window.bind("<Button-1>", self.on_click_start)
            window.bind("<B1-Motion>", self.on_drag_motion)
            window.bind("<ButtonRelease-1>", self.on_click_release)
            window.bind("<MouseWheel>", self.on_scroll)
            window.bind("<Button-3>", self.open_settings)
            window.bind("<Double-Button-3>", lambda event: self.on_close_callback())

        self.root.bind("<Configure>", self.on_configure)

    def on_configure(self, event):
        if self.mode == "none":
            self.sync_background_size()

    def sync_background_size(self):
        try:
            x = self.root.winfo_x()
            y = self.root.winfo_y()
            w = self.root.winfo_width()
            h = self.root.winfo_height()
            self.bg_window.geometry(f"{w}x{h}+{x}+{y}")
        except Exception:
            pass

    def _format_runtime_status(self):
        source_code = self.runtime_status.get("source_language", DEFAULT_SOURCE_LANGUAGE)
        source_label = LANGUAGE_LABELS.get(source_code, source_code)
        detected_code = self.runtime_status.get("detected_language")
        detected_label = LANGUAGE_LABELS.get(detected_code, detected_code) if detected_code else "Unknown"
        target_code = self.runtime_status.get("target_language", DEFAULT_TARGET_LANGUAGE)
        target_label = TARGET_LABELS.get(target_code, target_code)
        task_code = self.runtime_status.get("task", DEFAULT_TASK)
        task_label = TASK_LABELS.get(task_code, task_code)
        runtime_state = self.runtime_status.get("runtime_state", "idle").replace("_", " ").title()
        model = self.runtime_status.get("model_size", self.current_model)
        device = self.runtime_status.get("device", self.current_device)
        noise_floor = float(self.runtime_status.get("noise_floor", 0.0) or 0.0)
        ambient_calibrated = bool(self.runtime_status.get("ambient_calibrated", False))
        ambient_label = "Calibrated" if ambient_calibrated else "Calibrating"
        return (
            f"{runtime_state} | Source: {source_label} | Detected: {detected_label} | "
            f"Output: {target_label} | Mode: {task_label} | Model: {model} ({device}) | "
            f"Ambient: {ambient_label} | Floor: {noise_floor:.4f}"
        )

    def _format_playback_status(self):
        state = self.playback_state.status.value.replace("_", " ").title()
        source = self.playback_state.source.value.replace("_", " ").title() if self.playback_state.source else "None"
        active_job = self.playback_state.active_job_id if self.playback_state.active_job_id is not None else "-"
        return f"TTS: {state} | Source: {source} | Active: {active_job} | Queued: {self.playback_state.queued_jobs}"

    def _refresh_text_display(self):
        if self.committed_text:
            self.text_var.set(self.committed_text)
        else:
            self.text_var.set("Waiting for audio...\n(Shift+Drag Up/Down to Resize)")

        provisional_suffix = self.provisional_text
        if self.committed_text and provisional_suffix.startswith(self.committed_text):
            provisional_suffix = provisional_suffix[len(self.committed_text):].lstrip()
        if provisional_suffix and provisional_suffix != self.committed_text:
            self.provisional_text_var.set(f"[provisional] {provisional_suffix}")
        else:
            self.provisional_text_var.set("")

        self.root.update_idletasks()
        self.sync_background_size()

    def update_translation(self, update):
        self.committed_text = update.committed_text
        self.provisional_text = update.provisional_text
        self._refresh_text_display()

    def schedule_translation_update(self, update):
        if not self.is_stopping:
            self.pending_ui_actions.put(lambda: self.update_translation(update))

    def update_text(self, text):
        self.committed_text = text
        self.provisional_text = text
        self._refresh_text_display()

    def schedule_text_update(self, text):
        if not self.is_stopping:
            self.pending_ui_actions.put(lambda: self.update_text(text))

    def update_runtime_status(self, status):
        self.runtime_status.update(status)
        self.runtime_status_var.set(self._format_runtime_status())
        self.root.update_idletasks()
        self.sync_background_size()

    def schedule_runtime_status_update(self, status):
        if not self.is_stopping:
            self.pending_ui_actions.put(lambda: self.update_runtime_status(status))

    def update_playback_state(self, playback_state):
        self.playback_state = playback_state
        self.playback_status_var.set(self._format_playback_status())
        self.root.update_idletasks()
        self.sync_background_size()

    def schedule_playback_state_update(self, playback_state):
        if not self.is_stopping:
            self.pending_ui_actions.put(lambda: self.update_playback_state(playback_state))

    def set_tts_enabled(self, is_enabled):
        self.tts_enabled = is_enabled

    def schedule_ui_action(self, action):
        if not self.is_stopping:
            self.pending_ui_actions.put(action)

    def persist_settings(self):
        self.settings_change_callback(
            {
                "model_size": self.current_model,
                "device": self.current_device,
                "source_language": self.current_source_language,
                "target_language": self.current_target_language,
                "task": self.current_task,
                "vad_energy_threshold": self.current_vad_energy_threshold,
                "utterance_end_silence_seconds": self.current_utterance_end_silence_seconds,
                "min_utterance_seconds": self.current_min_utterance_seconds,
                "max_utterance_seconds": self.current_max_utterance_seconds,
                "debug_logging_enabled": self.debug_logging_enabled,
                "font_size": self.font_size,
                "opacity": self.opacity,
                "tts_enabled": self.tts_enabled,
                "voice_id": self.selected_voice_id,
                "output_device_index": self.selected_device_index,
            }
        )

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
                self.provisional_label.config(font=("Helvetica", max(12, self.font_size // 2), "italic"))
                self.status_label.config(font=("Helvetica", max(10, self.font_size // 3)))
                self.playback_label.config(font=("Helvetica", max(9, self.font_size // 4)))
                self.root.update_idletasks()
                self.sync_background_size()

    def on_click_release(self, event):
        self.mode = "none"

    def on_scroll(self, event):
        if event.delta > 0:
            self.opacity = min(1.0, self.opacity + 0.05)
        else:
            self.opacity = max(0.0, self.opacity - 0.05)
        self.apply_opacity()

    def apply_opacity(self):
        self.bg_window.attributes("-alpha", max(0.01, self.opacity))

    def get_audio_devices(self):
        try:
            return [f"{i}: {d['name']}" for i, d in enumerate(sd.query_devices()) if d["max_output_channels"] > 0]
        except Exception:
            return []

    def open_settings(self, event=None):
        if self.settings_window:
            self.settings_window.lift()
            return

        sw = tk.Toplevel(self.root)
        sw.title("Settings")
        sw.geometry("420x820")
        sw.attributes("-topmost", True)
        self.settings_window = sw
        sw.protocol("WM_DELETE_WINDOW", lambda: [sw.destroy(), setattr(self, "settings_window", None)])

        tk.Label(sw, text="AI Model Settings", font=("Arial", 12, "bold")).pack(pady=10)

        tk.Label(sw, text="Model Size").pack()
        model_combo = ttk.Combobox(sw, values=AVAILABLE_MODELS, state="readonly", width=30)
        model_combo.set(self.current_model)
        model_combo.pack(pady=5)

        tk.Label(sw, text="Processing Device").pack()
        device_combo = ttk.Combobox(sw, values=AVAILABLE_DEVICES, state="readonly", width=30)
        device_combo.set(self.current_device)
        device_combo.pack(pady=5)

        tk.Label(sw, text="Source Language").pack()
        source_combo = ttk.Combobox(
            sw,
            values=[label for _, label in LANGUAGE_CHOICES],
            state="readonly",
            width=30,
        )
        source_combo.set(LANGUAGE_LABELS.get(self.current_source_language, LANGUAGE_LABELS[DEFAULT_SOURCE_LANGUAGE]))
        source_combo.pack(pady=5)

        tk.Label(sw, text="Output Mode").pack()
        task_combo = ttk.Combobox(
            sw,
            values=[label for _, label in TASK_CHOICES],
            state="readonly",
            width=30,
        )
        task_combo.set(TASK_LABELS.get(self.current_task, TASK_LABELS[DEFAULT_TASK]))
        task_combo.pack(pady=5)

        tk.Label(sw, text="Target Output").pack()
        target_combo = ttk.Combobox(sw, state="readonly", width=30)
        target_combo.pack(pady=5)

        def sync_target_combo():
            task_code = TASK_CODES_BY_LABEL.get(task_combo.get(), DEFAULT_TASK)
            allowed_target = "source" if task_code == "transcribe" else "en"
            target_combo.config(values=[TARGET_LABELS[allowed_target]])
            target_combo.set(TARGET_LABELS[allowed_target])

        task_combo.bind("<<ComboboxSelected>>", lambda event: sync_target_combo())
        sync_target_combo()

        status_label = tk.Label(sw, text="", fg="blue")
        status_label.pack()

        runtime_label = tk.Label(
            sw,
            textvariable=self.runtime_status_var,
            wraplength=360,
            justify="left",
            fg="#333333",
        )
        runtime_label.pack(pady=(4, 8), padx=16)

        tk.Label(sw, text="Detection Tuning", font=("Arial", 12, "bold")).pack(pady=(4, 4))

        tk.Label(sw, text="VAD Energy Threshold").pack()
        vad_slider = tk.Scale(sw, from_=0.001, to=0.05, resolution=0.001, orient="horizontal")
        vad_slider.set(self.current_vad_energy_threshold)
        vad_slider.pack(fill="x", padx=20)

        tk.Label(sw, text="End Silence Seconds").pack()
        silence_slider = tk.Scale(sw, from_=0.1, to=1.5, resolution=0.05, orient="horizontal")
        silence_slider.set(self.current_utterance_end_silence_seconds)
        silence_slider.pack(fill="x", padx=20)

        tk.Label(sw, text="Min Utterance Seconds").pack()
        min_utterance_slider = tk.Scale(sw, from_=0.2, to=5.0, resolution=0.1, orient="horizontal")
        min_utterance_slider.set(self.current_min_utterance_seconds)
        min_utterance_slider.pack(fill="x", padx=20)

        tk.Label(sw, text="Max Utterance Seconds").pack()
        max_utterance_slider = tk.Scale(sw, from_=1.0, to=20.0, resolution=0.5, orient="horizontal")
        max_utterance_slider.set(self.current_max_utterance_seconds)
        max_utterance_slider.pack(fill="x", padx=20)

        debug_var = tk.BooleanVar(value=self.debug_logging_enabled)
        tk.Checkbutton(sw, text="Enable Debug Logging", variable=debug_var).pack(pady=(4, 6))

        def apply_ai_settings():
            if self.is_loading_model:
                return

            model_size = model_combo.get()
            device = device_combo.get()
            source_language = LANGUAGE_CODES_BY_LABEL.get(source_combo.get(), DEFAULT_SOURCE_LANGUAGE)
            task = TASK_CODES_BY_LABEL.get(task_combo.get(), DEFAULT_TASK)
            vad_energy_threshold = float(vad_slider.get())
            utterance_end_silence_seconds = float(silence_slider.get())
            min_utterance_seconds = float(min_utterance_slider.get())
            max_utterance_seconds = float(max_utterance_slider.get())
            debug_logging_enabled = bool(debug_var.get())

            if (
                model_size == self.current_model
                and device == self.current_device
                and source_language == self.current_source_language
                and task == self.current_task
                and vad_energy_threshold == self.current_vad_energy_threshold
                and utterance_end_silence_seconds == self.current_utterance_end_silence_seconds
                and min_utterance_seconds == self.current_min_utterance_seconds
                and max_utterance_seconds == self.current_max_utterance_seconds
                and debug_logging_enabled == self.debug_logging_enabled
            ):
                return

            self.is_loading_model = True
            status_label.config(text="Applying AI settings... Please wait...", fg="blue")
            apply_btn.config(state="disabled")

            def run_load():
                success = True
                msg = "Success"
                active_model = self.current_model
                active_device = self.current_device

                if model_size != self.current_model or device != self.current_device:
                    success, msg, active_model, active_device = self.model_change_callback(model_size, device)

                runtime_config = self.translation_settings_callback(
                    source_language,
                    task,
                    vad_energy_threshold,
                    utterance_end_silence_seconds,
                    min_utterance_seconds,
                    max_utterance_seconds,
                    debug_logging_enabled,
                )

                def update_ui():
                    self.is_loading_model = False
                    apply_btn.config(state="normal")
                    self.current_model = active_model
                    self.current_device = active_device
                    self.current_source_language = runtime_config["source_language"]
                    self.current_target_language = runtime_config["target_language"]
                    self.current_task = runtime_config["task"]
                    self.current_vad_energy_threshold = runtime_config["vad_energy_threshold"]
                    self.current_utterance_end_silence_seconds = runtime_config["utterance_end_silence_seconds"]
                    self.current_min_utterance_seconds = runtime_config["min_utterance_seconds"]
                    self.current_max_utterance_seconds = runtime_config["max_utterance_seconds"]
                    self.debug_logging_enabled = runtime_config["debug_logging_enabled"]
                    model_combo.set(self.current_model)
                    device_combo.set(self.current_device)
                    source_combo.set(
                        LANGUAGE_LABELS.get(self.current_source_language, LANGUAGE_LABELS[DEFAULT_SOURCE_LANGUAGE])
                    )
                    task_combo.set(TASK_LABELS.get(self.current_task, TASK_LABELS[DEFAULT_TASK]))
                    sync_target_combo()
                    self.persist_settings()

                    if success:
                        status_label.config(
                            text=f"Active: {active_model} ({active_device}) | {TASK_LABELS[self.current_task]}",
                            fg="green",
                        )
                        messagebox.showinfo("Success", f"AI settings updated to {active_model} on {active_device}")
                    else:
                        status_label.config(
                            text=f"Loaded fallback: {active_model} ({active_device})",
                            fg="#b36b00",
                        )
                        messagebox.showwarning("Model Load Warning", msg)

                self.schedule_ui_action(update_ui)

            threading.Thread(target=run_load, daemon=True).start()

        apply_btn = tk.Button(sw, text="Apply Changes", command=apply_ai_settings, bg="#dddddd")
        apply_btn.pack(pady=5)

        tk.Label(sw, text="-------------------------").pack(pady=5)

        tk.Label(sw, text="Appearance", font=("Arial", 12, "bold")).pack(pady=5)

        tk.Label(sw, text="Font Size").pack()
        size_slider = tk.Scale(sw, from_=10, to=100, orient="horizontal", command=self.set_font_size_from_slider)
        size_slider.set(self.font_size)
        size_slider.pack(fill="x", padx=20)

        tk.Label(sw, text="Max Width").pack()
        width_slider = tk.Scale(sw, from_=300, to=1800, orient="horizontal", command=self.set_width_from_slider)
        width_slider.set(self.wrap_width)
        width_slider.pack(fill="x", padx=20)

        tk.Label(sw, text="Opacity").pack()
        opacity_slider = tk.Scale(sw, from_=0.0, to=1.0, resolution=0.05, orient="horizontal", command=self.set_opacity_from_slider)
        opacity_slider.set(self.opacity)
        opacity_slider.pack(fill="x", padx=20)

        tk.Button(sw, text="Change Text Color", command=self.pick_color).pack(pady=2)
        tk.Button(sw, text="Change Background Color", command=self.pick_bg_color).pack(pady=2)

        tk.Label(sw, text="Audio", font=("Arial", 12, "bold")).pack(pady=10)

        tts_var = tk.BooleanVar(value=self.tts_enabled)
        tk.Checkbutton(
            sw,
            text="Read Output Aloud",
            variable=tts_var,
            command=lambda: self._on_tts_toggle(tts_var.get()),
        ).pack()

        tk.Label(sw, text="Voice Personality:").pack()
        available_voices = self.get_voices_callback()
        voice_names = [voice["name"] for voice in available_voices]
        voice_combo = ttk.Combobox(sw, values=voice_names, state="readonly", width=40)
        voice_combo.pack(pady=2, padx=10)

        if self.selected_voice_id:
            for voice in available_voices:
                if voice["id"] == self.selected_voice_id:
                    voice_combo.set(voice["name"])
                    break
        elif voice_names:
            voice_combo.current(0)

        def on_voice_change(event):
            voice_name = voice_combo.get()
            for voice in available_voices:
                if voice["name"] == voice_name:
                    self.selected_voice_id = voice["id"]
                    self.tts_voice_callback(voice["id"])
                    self.persist_settings()
                    break

        voice_combo.bind("<<ComboboxSelected>>", on_voice_change)

        tk.Label(sw, text="Output Device:").pack()
        device_list = self.get_audio_devices()
        output_device_combo = ttk.Combobox(sw, values=device_list, width=40, state="readonly")
        output_device_combo.pack(pady=2, padx=10)

        if self.selected_device_index is not None:
            for device_name in device_list:
                if device_name.startswith(f"{self.selected_device_index}:"):
                    output_device_combo.set(device_name)

        def on_output_device_change(event):
            selection = output_device_combo.get()
            if selection:
                self.selected_device_index = int(selection.split(":")[0])
                self.tts_device_callback(self.selected_device_index)
                self.persist_settings()

        output_device_combo.bind("<<ComboboxSelected>>", on_output_device_change)

    def _on_tts_toggle(self, is_enabled):
        self.tts_enabled = is_enabled
        self.tts_toggle_callback(is_enabled)
        self.persist_settings()

    def set_font_size_from_slider(self, val):
        self.font_size = int(val)
        self.label.config(font=("Helvetica", self.font_size, "bold"))
        self.provisional_label.config(font=("Helvetica", max(12, self.font_size // 2), "italic"))
        self.status_label.config(font=("Helvetica", max(10, self.font_size // 3)))
        self.playback_label.config(font=("Helvetica", max(9, self.font_size // 4)))
        self.root.update_idletasks()
        self.sync_background_size()
        self.persist_settings()

    def set_width_from_slider(self, val):
        self.wrap_width = int(val)
        self.label.config(wraplength=self.wrap_width)
        self.provisional_label.config(wraplength=self.wrap_width)
        self.status_label.config(wraplength=self.wrap_width)
        self.playback_label.config(wraplength=self.wrap_width)
        self.root.update_idletasks()
        self.sync_background_size()

    def set_opacity_from_slider(self, val):
        self.opacity = float(val)
        self.apply_opacity()
        self.persist_settings()

    def pick_color(self):
        color = colorchooser.askcolor(title="Text Color")[1]
        if color:
            self.text_color = color
            self.label.config(fg=color)

    def pick_bg_color(self):
        color = colorchooser.askcolor(title="Background Color")[1]
        if color:
            self.bg_color = color
            self.bg_window.config(bg=color)

    def start(self):
        self.root.mainloop()

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
