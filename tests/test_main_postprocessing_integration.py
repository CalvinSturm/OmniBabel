import unittest
from unittest import mock

import main
from src.settings import DEFAULT_SETTINGS
from src.streaming_contracts import ClauseInfo, TranslationUpdate


class FakeRecorder:
    last_instance = None

    def __init__(self, audio_queue, stats_callback=None):
        self.audio_queue = audio_queue
        self.stats_callback = stats_callback
        FakeRecorder.last_instance = self

    def start(self):
        return None

    def stop(self):
        return None


class FakeTranscriber:
    last_instance = None

    def __init__(self, audio_queue, result_callback, status_callback=None, **kwargs):
        self.audio_queue = audio_queue
        self.result_callback = result_callback
        self.status_callback = status_callback or (lambda payload: None)
        self.active_model_size = kwargs.get("initial_model_size", "large-v3")
        self.active_device = kwargs.get("initial_device", "cpu")
        self.last_model_change_result = (True, "Success", self.active_model_size, self.active_device)
        self.status = {
            "runtime_state": "listening",
            "task": kwargs.get("initial_task", "translate"),
            "source_language": kwargs.get("initial_source_language", "auto"),
            "target_language": kwargs.get("initial_target_language", "en"),
            "detected_language": "en",
        }
        FakeTranscriber.last_instance = self

    def start(self):
        self.status_callback(dict(self.status))
        self.result_callback(
            TranslationUpdate(
                revision_id=1,
                commit_id=1,
                provisional_text="hello world",
                committed_text="hello world",
                committed_append="hello world",
                detected_language="en",
                clause=ClauseInfo(clause_id=1, is_final_clause=True, char_start=0, char_end=11),
                audio_start_ms=0,
                audio_end_ms=250,
            ),
            dict(self.status),
        )

    def stop(self):
        return None

    def change_model(self, model_size, device):
        self.active_model_size = model_size
        self.active_device = device
        self.last_model_change_result = (True, "Success", model_size, device)
        return self.last_model_change_result

    def configure_runtime(
        self,
        source_language,
        task,
        vad_energy_threshold,
        utterance_end_silence_seconds,
        min_utterance_seconds,
        max_utterance_seconds,
        debug_logging_enabled,
    ):
        self.status.update(
            {
                "source_language": source_language,
                "target_language": "en" if task == "translate" else "source",
                "task": task,
                "vad_energy_threshold": vad_energy_threshold,
                "utterance_end_silence_seconds": utterance_end_silence_seconds,
                "min_utterance_seconds": min_utterance_seconds,
                "max_utterance_seconds": max_utterance_seconds,
                "debug_logging_enabled": debug_logging_enabled,
            }
        )
        return dict(self.status)


class FakeGUI:
    last_instance = None

    def __init__(
        self,
        on_close_callback,
        tts_toggle_callback,
        tts_device_callback,
        tts_voice_callback,
        tts_backend_callback,
        get_voices_callback,
        get_tts_backends_callback,
        model_change_callback,
        postprocess_settings_callback,
        translation_settings_callback,
        initial_settings,
        settings_change_callback,
    ):
        self.on_close_callback = on_close_callback
        self.translation_updates = []
        self.runtime_updates = []
        self.playback_updates = []
        self.dialogs = []
        FakeGUI.last_instance = self

    def schedule_translation_update(self, update):
        self.translation_updates.append(update)

    def schedule_runtime_status_update(self, status):
        self.runtime_updates.append(status)

    def schedule_playback_state_update(self, state):
        self.playback_updates.append(state)

    def set_tts_enabled(self, is_enabled):
        return None

    def show_dialog(self, title, message, level="info"):
        self.dialogs.append((title, message, level))

    def start(self):
        return None

    def stop(self):
        return None


class FakeTTSHandle:
    last_instance = None

    def __init__(self, state_callback=None):
        self.state_callback = state_callback or (lambda state: None)
        self.enabled = False
        self.received_updates = []
        FakeTTSHandle.last_instance = self

    def set_backend(self, backend_name):
        self.backend_name = backend_name

    def set_output_device(self, device_id):
        self.output_device = device_id

    def set_voice(self, voice_id):
        self.voice_id = voice_id

    def get_voices(self):
        return []

    def get_available_backends(self):
        return ["system"]

    def get_telemetry(self):
        return {}

    def submit_translation_update(self, update):
        self.received_updates.append(update)

    def stop(self):
        return None


class FakePostProcessingHandle:
    last_instance = None

    def __init__(self, update_callback):
        self.update_callback = update_callback
        self.enabled = False
        self.config = None
        FakePostProcessingHandle.last_instance = self

    def start(self):
        return None

    def stop(self):
        return None

    def get_runtime_status(self):
        return {
            "postprocess_enabled": self.enabled,
            "postprocess_state": "ready" if self.enabled else "disabled",
            "postprocess_message": "ready" if self.enabled else "disabled",
        }

    def configure(self, enabled, executable_path=None, model_path=None):
        self.enabled = bool(enabled)
        self.config = (self.enabled, executable_path, model_path)
        return {
            "postprocess_enabled": self.enabled,
            "postprocess_executable_path": executable_path,
            "postprocess_model_path": model_path,
            "postprocess_state": "ready" if self.enabled else "disabled",
            "postprocess_message": "ready" if self.enabled else "disabled",
        }

    def submit_translation_update(self, update, status):
        committed_append = "Hello world." if self.enabled else update.committed_append
        committed_text = "Hello world." if self.enabled else update.committed_text
        rewritten = TranslationUpdate(
            revision_id=update.revision_id,
            commit_id=update.commit_id,
            provisional_text=committed_text,
            committed_text=committed_text,
            committed_append=committed_append,
            detected_language=update.detected_language,
            clause=ClauseInfo(clause_id=1, is_final_clause=True, char_start=0, char_end=len(committed_text)),
            audio_start_ms=update.audio_start_ms,
            audio_end_ms=update.audio_end_ms,
        )
        payload = dict(status)
        payload.update(self.get_runtime_status())
        self.update_callback(rewritten, payload)


class MainPostProcessingIntegrationTests(unittest.TestCase):
    def test_main_routes_postprocessed_updates_to_gui_and_tts(self):
        settings = dict(DEFAULT_SETTINGS)
        settings.update(
            {
                "postprocess_enabled": True,
                "postprocess_executable_path": "llama-cli",
                "postprocess_model_path": "C:\\models\\granite.gguf",
            }
        )

        with (
            mock.patch.object(main, "run_startup_checks", return_value=[]),
            mock.patch.object(main, "load_settings", return_value=settings),
            mock.patch.object(main, "save_settings"),
            mock.patch.object(
                main,
                "load_components",
                return_value=(
                    FakeRecorder,
                    FakeTranscriber,
                    FakeGUI,
                    FakeTTSHandle,
                    FakePostProcessingHandle,
                ),
            ),
        ):
            main.main()

        gui = FakeGUI.last_instance
        tts = FakeTTSHandle.last_instance
        self.assertIsNotNone(gui)
        self.assertIsNotNone(tts)
        self.assertEqual(len(gui.translation_updates), 1)
        self.assertEqual(gui.translation_updates[0].committed_append, "Hello world.")
        self.assertEqual(tts.received_updates[0].committed_append, "Hello world.")
        self.assertTrue(any(update.get("postprocess_state") == "ready" for update in gui.runtime_updates))


if __name__ == "__main__":
    unittest.main()
