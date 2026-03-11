import numpy as np
import threading
import queue
import gc
import os
import site
from faster_whisper import WhisperModel
from config import (
    DEFAULT_MODEL_SIZE,
    DEFAULT_DEVICE,
    TARGET_RATE,
    BUFFER_THRESHOLD_SECONDS,
    BUFFER_OVERLAP_SECONDS,
)

BANNED_PHRASES = [
    "thank you for watching", "English subtitles", "please subscribe", "hit the like button",
    "hit that like button", "subscribe and hit", "thanks for watching",
    "please like and subscribe", "amara.org", "subtitles by",
    "copyright", "all rights reserved", "subscribe to my channel"
]

class Transcriber:
    def __init__(self, audio_queue, result_callback, initial_model_size=None, initial_device=None):
        self.audio_queue = audio_queue
        self.result_callback = result_callback
        self.running = False
        self.thread = None
        self.model = None
        self.active_model_size = DEFAULT_MODEL_SIZE
        self.active_device = DEFAULT_DEVICE
        self.last_model_change_result = (True, "Success", DEFAULT_MODEL_SIZE, DEFAULT_DEVICE)
        self.last_emitted_text = ""
        self.model_lock = threading.Lock()
        
        # 1. Inject the pip-installed libraries
        self.inject_nvidia_libs()
        
        # Initial Load
        requested_model = initial_model_size or DEFAULT_MODEL_SIZE
        requested_device = initial_device or DEFAULT_DEVICE
        success, message, _, _ = self.change_model(requested_model, requested_device)
        if self.model is None:
            raise RuntimeError(message)

    def inject_nvidia_libs(self):
        """Find pip-installed NVIDIA libraries and add them to the DLL path."""
        try:
            # We look for the folder directly in site-packages
            site_packages = site.getsitepackages()
            
            libs_to_add = []
            
            for sp in site_packages:
                # Look for cublas and cudnn directories
                cublas_path = os.path.join(sp, "nvidia", "cublas", "bin")
                cudnn_path = os.path.join(sp, "nvidia", "cudnn", "bin")
                
                if os.path.exists(cublas_path): libs_to_add.append(cublas_path)
                if os.path.exists(cudnn_path): libs_to_add.append(cudnn_path)

            # Add local folder (for zlibwapi.dll)
            libs_to_add.append(os.getcwd())

            # Register them
            for p in libs_to_add:
                print(f"[AI] Adding DLL Path: {p}")
                if hasattr(os, 'add_dll_directory'):
                    try: os.add_dll_directory(p)
                    except: pass
                os.environ['PATH'] = p + ';' + os.environ['PATH']
                
        except Exception as e:
            print(f"[AI] Library Injection Error: {e}")

    def change_model(self, model_size, device):
        print(f"[AI] Request to switch to: {model_size} on {device}")

        if "turbo" in model_size.lower():
            fallback_model = "large-v3"
            self.last_model_change_result = (
                False,
                "large-v3-turbo is transcription-focused and does not reliably support translation here. "
                f"Loaded {fallback_model} instead.",
                fallback_model,
                device,
            )
            model_size = fallback_model

        selected_compute_type = self._get_compute_type(device)

        try:
            print(f"[AI] Loading candidate model ({device}) as {selected_compute_type}...")
            new_model = WhisperModel(model_size, device=device, compute_type=selected_compute_type)
            self._swap_model(new_model, model_size, device)
            print(f"[AI] Success: Loaded {model_size}")
            if self.last_model_change_result[2] == model_size and self.last_model_change_result[3] == device:
                if self.last_model_change_result[0]:
                    self.last_model_change_result = (True, "Success", model_size, device)
            else:
                self.last_model_change_result = (True, "Success", model_size, device)
            return self.last_model_change_result

        except Exception as exc:
            error_msg = str(exc)
            print(f"[AI] CRITICAL ERROR loading model: {error_msg}")

            if "cublas" in error_msg.lower() or "cudnn" in error_msg.lower():
                print("[AI] TIP: Verify the installed NVIDIA runtime packages match your ctranslate2 build.")

            if device == "cuda":
                print("[AI] Attempting fallback to CPU without unloading the active model...")
                try:
                    fallback_model = WhisperModel(model_size, device="cpu", compute_type="int8")
                    self._swap_model(fallback_model, model_size, "cpu")
                    self.last_model_change_result = (
                        False,
                        f"GPU failed. Fell back to CPU.\nError: {error_msg}",
                        model_size,
                        "cpu",
                    )
                    return self.last_model_change_result
                except Exception as fallback_error:
                    error_msg = f"{error_msg}\nCPU fallback failed: {fallback_error}"

            self.last_model_change_result = (
                False,
                f"Failed to load model: {error_msg}",
                self.active_model_size,
                self.active_device,
            )
            return self.last_model_change_result

    def _get_compute_type(self, device):
        if device == "cpu":
            return "int8"

        print("[AI] APPLYING STABILITY FIX: Using float32 for CUDA")
        return "float32"

    def _swap_model(self, new_model, model_size, device):
        with self.model_lock:
            old_model = self.model
            self.model = new_model
            self.active_model_size = model_size
            self.active_device = device

        if old_model is not None:
            del old_model
            gc.collect()

    def _transcribe_loop(self):
        audio_buffer = np.array([], dtype=np.float32)
        overlap_samples = int(TARGET_RATE * BUFFER_OVERLAP_SECONDS)
        while self.running:
            try:
                data = self.audio_queue.get(timeout=1)
                audio_buffer = np.concatenate((audio_buffer, data))
                
                if len(audio_buffer) >= TARGET_RATE * BUFFER_THRESHOLD_SECONDS:
                    process_buffer = audio_buffer.copy()
                    clean_text = ""
                    with self.model_lock:
                        if self.model: 
                            try:
                                segments, info = self.model.transcribe(
                                    process_buffer,
                                    beam_size=5, 
                                    task="translate",
                                    vad_filter=True,
                                    vad_parameters=dict(min_silence_duration_ms=500),
                                    initial_prompt="Translate all spoken dialogue to natural English subtitles."
                                )
                                text_output = ""
                                for segment in segments: text_output += segment.text + " "
                                clean_text = text_output.strip()
                                if (
                                    clean_text
                                    and not self.is_hallucination(clean_text)
                                    and clean_text != self.last_emitted_text
                                ):
                                    print(f"> {clean_text}")
                                    self.last_emitted_text = clean_text
                                    self.result_callback(clean_text)
                            except Exception as e:
                                print(f"[AI] Transcription Error: {e}")

                    if clean_text:
                        audio_buffer = audio_buffer[-overlap_samples:] if overlap_samples > 0 else np.array([], dtype=np.float32)
                    else:
                        max_buffer_samples = int(TARGET_RATE * max(BUFFER_THRESHOLD_SECONDS * 2, 6))
                        if len(audio_buffer) > max_buffer_samples:
                            audio_buffer = audio_buffer[-max_buffer_samples:]
            except queue.Empty: continue
            except Exception as e: print(f"[AI] Error: {e}")

    def is_hallucination(self, text):
        text_lower = text.lower()
        for phrase in BANNED_PHRASES:
            if phrase in text_lower: return True
        if len(text) < 2: return True
        return False

    def start(self):
        self.running = True
        self.thread = threading.Thread(target=self._transcribe_loop, daemon=True)
        self.thread.start()

    def stop(self):
        self.running = False
        if self.thread: self.thread.join()
