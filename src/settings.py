import json
from pathlib import Path

from config import (
    DEFAULT_DEBUG_LOGGING,
    DEFAULT_DEVICE,
    DEFAULT_FONT_SIZE,
    DEFAULT_MAX_UTTERANCE_SECONDS,
    DEFAULT_MIN_UTTERANCE_SECONDS,
    DEFAULT_MODEL_SIZE,
    DEFAULT_OPACITY,
    DEFAULT_SOURCE_LANGUAGE,
    DEFAULT_TARGET_LANGUAGE,
    DEFAULT_TASK,
    DEFAULT_UTTERANCE_END_SILENCE_SECONDS,
    DEFAULT_VAD_ENERGY_THRESHOLD,
)


SETTINGS_PATH = Path(__file__).resolve().parent.parent / "settings.json"

DEFAULT_SETTINGS = {
    "model_size": DEFAULT_MODEL_SIZE,
    "device": DEFAULT_DEVICE,
    "font_size": DEFAULT_FONT_SIZE,
    "opacity": DEFAULT_OPACITY,
    "source_language": DEFAULT_SOURCE_LANGUAGE,
    "target_language": DEFAULT_TARGET_LANGUAGE,
    "task": DEFAULT_TASK,
    "vad_energy_threshold": DEFAULT_VAD_ENERGY_THRESHOLD,
    "utterance_end_silence_seconds": DEFAULT_UTTERANCE_END_SILENCE_SECONDS,
    "min_utterance_seconds": DEFAULT_MIN_UTTERANCE_SECONDS,
    "max_utterance_seconds": DEFAULT_MAX_UTTERANCE_SECONDS,
    "debug_logging_enabled": DEFAULT_DEBUG_LOGGING,
    "tts_enabled": False,
    "tts_backend": "system",
    "voice_id": None,
    "output_device_index": None,
}


def load_settings():
    if not SETTINGS_PATH.exists():
        return dict(DEFAULT_SETTINGS)

    try:
        loaded = json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
    except Exception:
        return dict(DEFAULT_SETTINGS)

    settings = dict(DEFAULT_SETTINGS)
    if isinstance(loaded, dict):
        for key, default_value in DEFAULT_SETTINGS.items():
            if key in loaded and loaded[key] is not None:
                settings[key] = loaded[key]
            else:
                settings[key] = default_value
    return settings


def save_settings(settings):
    merged = dict(DEFAULT_SETTINGS)
    merged.update(settings)
    SETTINGS_PATH.write_text(json.dumps(merged, indent=2), encoding="utf-8")
