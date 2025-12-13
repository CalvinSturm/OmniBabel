import numpy as np
import threading
import queue
import gc
import os
import ctypes
import site
import glob
from faster_whisper import WhisperModel
from config import DEFAULT_MODEL_SIZE, DEFAULT_DEVICE, TARGET_RATE, BUFFER_THRESHOLD_SECONDS

BANNED_PHRASES = [
    "thank you for watching", "English subtitles", "please subscribe", "hit the like button",
    "hit that like button", "subscribe and hit", "thanks for watching",
    "please like and subscribe", "amara.org", "subtitles by",
    "copyright", "all rights reserved", "subscribe to my channel"
]

class Transcriber:
    def __init__(self, audio_queue, result_callback):
        self.audio_queue = audio_queue
        self.result_callback = result_callback
        self.running = False
        self.thread = None
        self.model = None
        self.model_lock = threading.Lock()
        
        # 1. Inject the pip-installed libraries
        self.inject_nvidia_libs()
        
        # Initial Load
        self.change_model(DEFAULT_MODEL_SIZE, DEFAULT_DEVICE)

    def inject_nvidia_libs(self):
        """Finds the nvidia-cu11 libraries and adds them to the DLL path."""
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
        
        if device == "cpu":
            selected_compute_type = "int8"
        else:
            # FORCE float32 for stability
            print("[AI] APPLYING STABILITY FIX: Using float32 for CUDA")
            selected_compute_type = "float32"

        print("[AI] Waiting for transcriber to pause...")
        with self.model_lock:
            print("[AI] Unloading old model...")
            if self.model:
                del self.model
                self.model = None
            gc.collect() 
            
            print(f"[AI] Loading new model ({device}) as {selected_compute_type}...")
            try:
                new_model = WhisperModel(model_size, device=device, compute_type=selected_compute_type)
                self.model = new_model
                print(f"[AI] Success: Loaded {model_size}")
                return True, "Success"
            
            except Exception as e:
                error_msg = str(e)
                print(f"[AI] CRITICAL ERROR loading model: {error_msg}")
                
                # Check for common missing DLLs
                if "cublas" in error_msg.lower() or "cudnn" in error_msg.lower():
                    print("[AI] TIP: Try running: pip install nvidia-cudnn-cu11==8.9.6.50 nvidia-cublas-cu11==11.11.3.6")
                
                if device == "cuda":
                    print("[AI] Attempting fallback to CPU...")
                    try:
                        self.model = WhisperModel(model_size, device="cpu", compute_type="int8")
                        return False, f"GPU Failed. Fell back to CPU.\nError: {error_msg}"
                    except: pass
                return False, f"Failed to load model: {error_msg}"

    def _transcribe_loop(self):
        audio_buffer = np.array([], dtype=np.float32)
        while self.running:
            try:
                data = self.audio_queue.get(timeout=1)
                audio_buffer = np.concatenate((audio_buffer, data))
                
                if len(audio_buffer) >= TARGET_RATE * BUFFER_THRESHOLD_SECONDS:
                    with self.model_lock:
                        if self.model: 
                            try:
                                # Re-enable VAD now that we fixed the libraries
                                segments, info = self.model.transcribe(
                                    audio_buffer, 
                                    beam_size=5, 
                                    task="translate",
                                    vad_filter=True,
                                    vad_parameters=dict(min_silence_duration_ms=500),
                                    initial_prompt="Movie dialogue. English subtitles."
                                )
                                text_output = ""
                                for segment in segments: text_output += segment.text + " "
                                clean_text = text_output.strip()
                                if clean_text and not self.is_hallucination(clean_text):
                                    print(f"> {clean_text}")
                                    self.result_callback(clean_text)
                            except Exception as e:
                                print(f"[AI] Transcription Error: {e}")
                    audio_buffer = np.array([], dtype=np.float32)
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