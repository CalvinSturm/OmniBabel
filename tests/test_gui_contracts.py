import sys
import types
import unittest


if "sounddevice" not in sys.modules:
    sys.modules["sounddevice"] = types.SimpleNamespace(query_devices=lambda: [])

from src.gui import OverlayGUI
from src.streaming_contracts import ClauseInfo, PlaybackState, PlaybackStatus, TTSJobSource, TranslationUpdate


class DummyVar:
    def __init__(self, value=""):
        self.value = value

    def set(self, value):
        self.value = value

    def get(self):
        return self.value


class DummyRoot:
    def update_idletasks(self):
        return None


class GUIContractsTests(unittest.TestCase):
    def make_gui(self):
        gui = object.__new__(OverlayGUI)
        gui.committed_text = ""
        gui.provisional_text = ""
        gui.text_var = DummyVar()
        gui.provisional_text_var = DummyVar()
        gui.runtime_status_var = DummyVar()
        gui.playback_status_var = DummyVar()
        gui.playback_state = PlaybackState(
            status=PlaybackStatus.IDLE,
            active_job_id=None,
            queued_jobs=0,
            source=None,
        )
        gui.root = DummyRoot()
        gui.sync_background_size = lambda: None
        return gui

    def test_update_translation_separates_committed_and_provisional_text(self):
        gui = self.make_gui()
        update = TranslationUpdate(
            revision_id=2,
            commit_id=1,
            provisional_text="Hello world and more",
            committed_text="Hello world",
            committed_append="Hello world",
            detected_language="en",
            clause=ClauseInfo(clause_id=1, is_final_clause=False, char_start=0, char_end=11),
            audio_start_ms=0,
            audio_end_ms=500,
        )

        gui.update_translation(update)

        self.assertEqual(gui.text_var.get(), "Hello world")
        self.assertIn("[provisional]", gui.provisional_text_var.get())
        self.assertIn("and more", gui.provisional_text_var.get())

    def test_update_playback_state_renders_source_and_queue_state(self):
        gui = self.make_gui()
        state = PlaybackState(
            status=PlaybackStatus.PLAYING,
            active_job_id=4,
            queued_jobs=2,
            source=TTSJobSource.COMMITTED_TRANSLATION,
        )

        gui.update_playback_state(state)

        rendered = gui.playback_status_var.get()
        self.assertIn("Playing", rendered)
        self.assertIn("Committed Translation", rendered)
        self.assertIn("Queued: 2", rendered)


if __name__ == "__main__":
    unittest.main()
