import importlib
import queue
import shutil
import sys

from src.dialogs import show_dialog
from src.settings import load_settings, save_settings
from src.streaming_contracts import PlaybackState, PlaybackStatus
from config import (
    DEFAULT_DEBUG_LOGGING,
    AVAILABLE_MODELS,
    DEFAULT_DEVICE,
    DEFAULT_MAX_UTTERANCE_SECONDS,
    DEFAULT_MIN_UTTERANCE_SECONDS,
    DEFAULT_MODEL_SIZE,
    DEFAULT_SOURCE_LANGUAGE,
    DEFAULT_TARGET_LANGUAGE,
    DEFAULT_TASK,
    DEFAULT_UTTERANCE_END_SILENCE_SECONDS,
    DEFAULT_VAD_ENERGY_THRESHOLD,
    LANGUAGE_CHOICES,
)


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
    valid_languages = {code for code, _ in LANGUAGE_CHOICES}

    model_size = normalized.get("model_size", DEFAULT_MODEL_SIZE)
    device = normalized.get("device", DEFAULT_DEVICE)
    source_language = normalized.get("source_language", DEFAULT_SOURCE_LANGUAGE)
    target_language = normalized.get("target_language", DEFAULT_TARGET_LANGUAGE)
    task = normalized.get("task", DEFAULT_TASK)
    vad_energy_threshold = normalized.get("vad_energy_threshold", DEFAULT_VAD_ENERGY_THRESHOLD)
    utterance_end_silence_seconds = normalized.get(
        "utterance_end_silence_seconds", DEFAULT_UTTERANCE_END_SILENCE_SECONDS
    )
    min_utterance_seconds = normalized.get("min_utterance_seconds", DEFAULT_MIN_UTTERANCE_SECONDS)
    max_utterance_seconds = normalized.get("max_utterance_seconds", DEFAULT_MAX_UTTERANCE_SECONDS)
    debug_logging_enabled = normalized.get("debug_logging_enabled", DEFAULT_DEBUG_LOGGING)
    tts_backend = normalized.get("tts_backend", "system")

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

    if source_language not in valid_languages:
        warnings.append(
            f"Saved source language '{source_language}' is invalid. Using '{DEFAULT_SOURCE_LANGUAGE}' instead."
        )
        normalized["source_language"] = DEFAULT_SOURCE_LANGUAGE

    if task not in ("translate", "transcribe"):
        warnings.append(f"Saved task '{task}' is invalid. Using '{DEFAULT_TASK}' instead.")
        normalized["task"] = DEFAULT_TASK

    expected_target = "en" if normalized.get("task", task) == "translate" else "source"
    if target_language != expected_target:
        normalized["target_language"] = expected_target

    def ensure_float(name, value, default, minimum, maximum):
        try:
            parsed = float(value)
        except (TypeError, ValueError):
            warnings.append(f"Saved {name} is invalid. Using default value {default}.")
            normalized[name] = default
            return
        if parsed < minimum or parsed > maximum:
            warnings.append(f"Saved {name} is out of range. Using default value {default}.")
            normalized[name] = default
            return
        normalized[name] = parsed

    ensure_float("vad_energy_threshold", vad_energy_threshold, DEFAULT_VAD_ENERGY_THRESHOLD, 0.001, 0.2)
    ensure_float(
        "utterance_end_silence_seconds",
        utterance_end_silence_seconds,
        DEFAULT_UTTERANCE_END_SILENCE_SECONDS,
        0.1,
        2.5,
    )
    ensure_float("min_utterance_seconds", min_utterance_seconds, DEFAULT_MIN_UTTERANCE_SECONDS, 0.2, 5.0)
    ensure_float("max_utterance_seconds", max_utterance_seconds, DEFAULT_MAX_UTTERANCE_SECONDS, 1.0, 20.0)

    if normalized["min_utterance_seconds"] > normalized["max_utterance_seconds"]:
        warnings.append("Saved utterance duration settings were inconsistent. Restored defaults.")
        normalized["min_utterance_seconds"] = DEFAULT_MIN_UTTERANCE_SECONDS
        normalized["max_utterance_seconds"] = DEFAULT_MAX_UTTERANCE_SECONDS

    normalized["debug_logging_enabled"] = bool(debug_logging_enabled)

    if tts_backend not in ("system", "kokoro"):
        warnings.append("Saved TTS backend is invalid. Using 'system' instead.")
        normalized["tts_backend"] = "system"
    else:
        normalized["tts_backend"] = tts_backend

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
    gui = None

    def on_tts_playback_state(state):
        if gui is not None:
            gui.schedule_playback_state_update(state)

    tts_engine = TTSHandle(state_callback=on_tts_playback_state)
    shutting_down = False
    pending_status = None

    def persist_settings(changes):
        settings.update(changes)
        save_settings(settings)

    tts_engine.enabled = settings["tts_enabled"]
    tts_engine.set_backend(settings["tts_backend"])
    if settings["output_device_index"] is not None:
        tts_engine.set_output_device(settings["output_device_index"])
    if settings["voice_id"]:
        tts_engine.set_voice(settings["voice_id"])

    def on_translation_received(update, status):
        text = update.committed_append.strip()
        if gui is not None:
            gui.schedule_translation_update(update)
            gui.schedule_runtime_status_update(status)
        should_notify_tts = bool(text) or bool(update.clause and update.clause.is_final_clause)
        if not should_notify_tts:
            return
        tts_engine.submit_translation_update(update)

    def on_transcriber_status(status):
        nonlocal pending_status
        pending_status = status
        if gui is not None:
            gui.schedule_runtime_status_update(status)

    def toggle_tts_enabled(is_enabled):
        tts_engine.enabled = is_enabled
        gui.set_tts_enabled(is_enabled)
        on_tts_playback_state(
            PlaybackState(
                status=PlaybackStatus.IDLE,
                active_job_id=None,
                queued_jobs=0,
                source=None,
            )
        )

    def change_tts_device(device_id):
        tts_engine.set_output_device(device_id)

    def change_tts_voice(voice_id):
        tts_engine.set_voice(voice_id)

    def change_tts_backend(backend_name):
        tts_engine.set_backend(backend_name)
        current_voices = tts_engine.get_voices()
        selected_voice_id = settings.get("voice_id")
        if selected_voice_id and any(voice["id"] == selected_voice_id for voice in current_voices):
            tts_engine.set_voice(selected_voice_id)
        elif current_voices:
            selected_voice_id = current_voices[0]["id"]
            tts_engine.set_voice(selected_voice_id)
        else:
            selected_voice_id = None
            tts_engine.set_voice(None)
        persist_settings({"tts_backend": backend_name, "voice_id": selected_voice_id})
        return {
            "tts_backend": backend_name,
            "voice_id": selected_voice_id,
        }

    def get_available_voices():
        return tts_engine.get_voices()

    def get_available_tts_backends():
        return tts_engine.get_available_backends()

    recorder = AudioRecorder(audio_queue)
    try:
        transcriber = Transcriber(
            audio_queue,
            result_callback=on_translation_received,
            status_callback=on_transcriber_status,
            initial_model_size=settings["model_size"],
            initial_device=settings["device"],
            initial_source_language=settings["source_language"],
            initial_target_language=settings["target_language"],
            initial_task=settings["task"],
            initial_vad_energy_threshold=settings["vad_energy_threshold"],
            initial_utterance_end_silence_seconds=settings["utterance_end_silence_seconds"],
            initial_min_utterance_seconds=settings["min_utterance_seconds"],
            initial_max_utterance_seconds=settings["max_utterance_seconds"],
            initial_debug_logging_enabled=settings["debug_logging_enabled"],
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

    def change_translation_settings(
        source_language,
        task,
        vad_energy_threshold,
        utterance_end_silence_seconds,
        min_utterance_seconds,
        max_utterance_seconds,
        debug_logging_enabled,
    ):
        runtime_config = transcriber.configure_runtime(
            source_language=source_language,
            task=task,
            vad_energy_threshold=vad_energy_threshold,
            utterance_end_silence_seconds=utterance_end_silence_seconds,
            min_utterance_seconds=min_utterance_seconds,
            max_utterance_seconds=max_utterance_seconds,
            debug_logging_enabled=debug_logging_enabled,
        )
        persist_settings(
            {
                "source_language": runtime_config["source_language"],
                "target_language": runtime_config["target_language"],
                "task": runtime_config["task"],
                "vad_energy_threshold": runtime_config["vad_energy_threshold"],
                "utterance_end_silence_seconds": runtime_config["utterance_end_silence_seconds"],
                "min_utterance_seconds": runtime_config["min_utterance_seconds"],
                "max_utterance_seconds": runtime_config["max_utterance_seconds"],
                "debug_logging_enabled": runtime_config["debug_logging_enabled"],
            }
        )
        return runtime_config

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
        tts_backend_callback=change_tts_backend,
        get_voices_callback=get_available_voices,
        get_tts_backends_callback=get_available_tts_backends,
        model_change_callback=change_ai_model,
        translation_settings_callback=change_translation_settings,
        initial_settings=settings,
        settings_change_callback=persist_settings,
    )
    if pending_status is not None:
        gui.schedule_runtime_status_update(pending_status)

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
