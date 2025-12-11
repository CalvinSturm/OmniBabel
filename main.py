import queue
import os
from src.audio import AudioRecorder
from src.transcriber import Transcriber
from src.gui import OverlayGUI
from src.tts import TTSHandle

def main():
    audio_queue = queue.Queue()
    tts_engine = TTSHandle()

    def on_translation_received(text):
        gui.update_text(text)
        tts_engine.speak(text)

    def toggle_tts_enabled(is_enabled):
        tts_engine.enabled = is_enabled

    def change_tts_device(device_id):
        tts_engine.set_output_device(device_id)

    def change_tts_voice(voice_id):
        tts_engine.set_voice(voice_id)

    def get_available_voices():
        return tts_engine.get_voices()

    # New Callback for Model Switching
    def change_ai_model(model_size, device):
        # Pause recorder temporarily? Not strictly necessary but safe.
        return transcriber.change_model(model_size, device)

    recorder = AudioRecorder(audio_queue) 
    transcriber = Transcriber(audio_queue, result_callback=on_translation_received)

    def shutdown_app():
        print("Shutting down...")
        recorder.stop()
        transcriber.stop()
        tts_engine.stop()
        gui.stop()
        os._exit(0)

    gui = OverlayGUI(
        on_close_callback=shutdown_app, 
        tts_toggle_callback=toggle_tts_enabled,
        tts_device_callback=change_tts_device,
        tts_voice_callback=change_tts_voice,
        get_voices_callback=get_available_voices,
        model_change_callback=change_ai_model # <--- Wired up
    )

    recorder.start()
    transcriber.start()

    gui.start()

if __name__ == "__main__":
    main()