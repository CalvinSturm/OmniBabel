import importlib
import queue
import shutil
import sys

from src.dialogs import show_dialog
from src.settings import load_settings, save_settings
from config import AVAILABLE_MODELS, DEFAULT_DEVICE, DEFAULT_MODEL_SIZE


REQUIRED_MODULES = {
    "numpy": "numpy",
    "scipy": "scipy",
    "sounddevice": "sounddevice",
    "soundfile": "soundfile",
    "faster_whisper": "faster-whisper",
    "pyttsx3": "pyttsx3",
    "pyaudiowpatch": "PyAudioWPatch",
    "pythoncom": "pywin32",
}


def print_setup_instructions():
    print("[Startup] Create the project virtual environment and install dependencies:")
    print("  py -3.12 -m venv venv")
    print("  .\\venv\\Scripts\\activate")
    print("  python -m pip install -r requirements.txt")
    print("[Startup] If installation fails, make sure you are using Python 3.10-3.12 on Windows.")


def run_startup_checks():
    issues = []

    if sys.platform != "win32":
        issues.append("OmniBabel currently supports Windows only.")

    if sys.version_info < (3, 10) or sys.version_info >= (3, 13):
        issues.append(
            f"Unsupported Python version: {sys.version.split()[0]}. Use Python 3.10-3.12."
        )

    missing_packages = []
    for module_name, package_name in REQUIRED_MODULES.items():
        try:
            importlib.import_module(module_name)
        except ModuleNotFoundError:
            missing_packages.append(package_name)

    if missing_packages:
        issues.append("Missing Python packages: " + ", ".join(sorted(set(missing_packages))))

    if shutil.which("ffmpeg") is None:
        issues.append("FFmpeg is not available on PATH.")

    if "PyAudioWPatch" not in missing_packages:
        try:
            from src.audio import probe_loopback_device

            has_loopback, detail = probe_loopback_device()
            if not has_loopback:
                issues.append(f"System loopback audio is unavailable: {detail}")
        except Exception as exc:
            issues.append(f"System loopback audio probe failed: {exc}")

    return issues


def load_components():
    from src.audio import AudioRecorder
    from src.transcriber import Transcriber
    from src.gui import OverlayGUI
    from src.tts import TTSHandle

    return AudioRecorder, Transcriber, OverlayGUI, TTSHandle


def normalize_settings(settings):
    normalized = dict(settings)
    warnings = []

    model_size = normalized.get("model_size", DEFAULT_MODEL_SIZE)
    device = normalized.get("device", DEFAULT_DEVICE)

    if model_size not in AVAILABLE_MODELS:
        replacement_model = DEFAULT_MODEL_SIZE
        warnings.append(
            f"Saved model '{model_size}' is not supported for translation. Using '{replacement_model}' instead."
        )
        normalized["model_size"] = replacement_model

    if device == "cuda":
        try:
            import torch

            if not torch.cuda.is_available():
                warnings.append("Saved device 'cuda' is unavailable. Using 'cpu' instead.")
                normalized["device"] = "cpu"
        except ImportError:
            warnings.append("Saved device 'cuda' is unavailable. Using 'cpu' instead.")
            normalized["device"] = "cpu"
    elif device not in ("cpu", "cuda"):
        warnings.append(f"Saved device '{device}' is invalid. Using '{DEFAULT_DEVICE}' instead.")
        normalized["device"] = DEFAULT_DEVICE

    return normalized, warnings


def main():
    issues = run_startup_checks()
    if issues:
        print("[Startup] OmniBabel cannot start:")
        for issue in issues:
            print(f"  - {issue}")
        print_setup_instructions()
        show_dialog("OmniBabel Startup Error", "\n\n".join(issues), level="error")
        raise SystemExit(1)

    AudioRecorder, Transcriber, OverlayGUI, TTSHandle = load_components()
    settings = load_settings()
    settings, normalization_warnings = normalize_settings(settings)
    if normalization_warnings:
        save_settings(settings)
    audio_queue = queue.Queue()
    tts_engine = TTSHandle()
    shutting_down = False

    def persist_settings(changes):
        settings.update(changes)
        save_settings(settings)

    tts_engine.enabled = settings["tts_enabled"]
    if settings["output_device_index"] is not None:
        tts_engine.set_output_device(settings["output_device_index"])
    if settings["voice_id"]:
        tts_engine.set_voice(settings["voice_id"])

    def on_translation_received(text):
        gui.schedule_text_update(text)
        tts_engine.speak(text)

    def toggle_tts_enabled(is_enabled):
        tts_engine.enabled = is_enabled
        gui.set_tts_enabled(is_enabled)

    def change_tts_device(device_id):
        tts_engine.set_output_device(device_id)

    def change_tts_voice(voice_id):
        tts_engine.set_voice(voice_id)

    def get_available_voices():
        return tts_engine.get_voices()

    recorder = AudioRecorder(audio_queue)
    try:
        transcriber = Transcriber(
            audio_queue,
            result_callback=on_translation_received,
            initial_model_size=settings["model_size"],
            initial_device=settings["device"],
        )
    except Exception as exc:
        print("[Startup] OmniBabel could not initialize the transcription model.")
        print(f"[Startup] {exc}")
        show_dialog("Model Initialization Error", str(exc), level="error")
        raise SystemExit(1) from exc

    persist_settings(
        {
            "model_size": transcriber.active_model_size,
            "device": transcriber.active_device,
        }
    )

    def change_ai_model(model_size, device):
        result = transcriber.change_model(model_size, device)
        persist_settings(
            {
                "model_size": transcriber.active_model_size,
                "device": transcriber.active_device,
            }
        )
        return result

    def shutdown_app():
        nonlocal shutting_down
        if shutting_down:
            return
        shutting_down = True
        print("Shutting down...")
        try:
            recorder.stop()
            transcriber.stop()
            tts_engine.stop()
        finally:
            gui.stop()

    gui = OverlayGUI(
        on_close_callback=shutdown_app,
        tts_toggle_callback=toggle_tts_enabled,
        tts_device_callback=change_tts_device,
        tts_voice_callback=change_tts_voice,
        get_voices_callback=get_available_voices,
        model_change_callback=change_ai_model,
        initial_settings=settings,
        settings_change_callback=persist_settings,
    )

    initial_load_success, initial_load_message, _, _ = transcriber.last_model_change_result
    if not initial_load_success:
        gui.show_dialog("Startup Warning", initial_load_message, level="warning")
    elif normalization_warnings:
        gui.show_dialog("Startup Warning", "\n\n".join(normalization_warnings), level="warning")

    recorder.start()
    transcriber.start()

    gui.start()


if __name__ == "__main__":
    main()
