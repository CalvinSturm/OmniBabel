import pyttsx3
import threading
import queue
import pythoncom
import sounddevice as sd
import soundfile as sf
import os
import tempfile

class TTSHandle:
    def __init__(self):
        self.queue = queue.Queue()
        self.running = True
        self.enabled = False
        self.output_device_index = None 
        self.voice_id = None # <--- Store selected voice ID
        
        self.thread = threading.Thread(target=self._worker, daemon=True)
        self.thread.start()

    def get_voices(self):
        """Returns a list of available voices"""
        try:
            # We need a temporary engine just to read the list
            temp_engine = pyttsx3.init()
            voices = temp_engine.getProperty('voices')
            voice_list = []
            for v in voices:
                # Save just the ID and the Name
                voice_list.append({"id": v.id, "name": v.name})
            del temp_engine
            return voice_list
        except:
            return []

    def set_voice(self, voice_id):
        """Sets the voice (Male/Female)"""
        print(f"[TTS] Setting Voice ID: {voice_id}")
        self.voice_id = voice_id

    def set_output_device(self, device_index):
        self.output_device_index = device_index

    def _worker(self):
        try:
            pythoncom.CoInitialize()
        except: pass

        while self.running:
            try:
                text = self.queue.get(timeout=1)
                
                if self.enabled and text:
                    temp_file = tempfile.mktemp(suffix=".wav")
                    
                    engine = pyttsx3.init()
                    
                    # 1. APPLY VOICE
                    if self.voice_id:
                        try:
                            engine.setProperty('voice', self.voice_id)
                        except: pass
                    
                    # 2. Optional: Make it slightly faster (150 is standard, 175 is upbeat)
                    # engine.setProperty('rate', 170) 

                    engine.save_to_file(text, temp_file)
                    engine.runAndWait()
                    del engine

                    try:
                        data, fs = sf.read(temp_file)
                        sd.play(data, fs, device=self.output_device_index, blocking=True)
                    except Exception as e:
                        print(f"[TTS] Playback Error: {e}")
                    
                    if os.path.exists(temp_file):
                        os.remove(temp_file)
            
            except queue.Empty:
                continue
            except Exception as e:
                print(f"[TTS] Error: {e}")

        try:
            pythoncom.CoUninitialize()
        except: pass

    def speak(self, text):
        if self.enabled:
            self.queue.put(text)
            
    def stop(self):
        self.running = False
        try:
            sd.stop()
        except Exception:
            pass
        if self.thread:
            self.thread.join(timeout=5)
