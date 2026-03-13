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
    def __init__(self):
        self._width = 760
        self._height = 200
        self._x = 0
        self._y = 0

    def update_idletasks(self):
        return None

    def winfo_width(self):
        return self._width

    def winfo_height(self):
        return self._height

    def winfo_x(self):
        return self._x

    def winfo_y(self):
        return self._y

    def geometry(self, spec):
        size, _, position = spec.partition("+")
        width, _, height = size.partition("x")
        if width:
            self._width = int(width)
        if height:
            self._height = int(height)
        if position:
            parts = spec.split("+")
            if len(parts) >= 3:
                self._x = int(parts[1])
                self._y = int(parts[2])


class DummyFrame:
    def __init__(self, requested_height=180):
        self.requested_height = requested_height

    def winfo_reqheight(self):
        return self.requested_height


class DummyWidget:
    def __init__(self):
        self.pack_calls = []
        self.pack_forget_calls = 0

    def config(self, **kwargs):
        return None

    def pack(self, **kwargs):
        self.pack_calls.append(kwargs)

    def pack_forget(self):
        self.pack_forget_calls += 1


class GUIContractsTests(unittest.TestCase):
    def make_gui(self):
        gui = object.__new__(OverlayGUI)
        gui.committed_text = ""
        gui.provisional_text = ""
        gui.text_var = DummyVar()
        gui.provisional_text_var = DummyVar()
        gui.runtime_status_var = DummyVar()
        gui.playback_status_var = DummyVar()
        gui.current_model = "large-v3"
        gui.current_device = "cpu"
        gui.runtime_status = {
            "runtime_state": "processing",
            "message": "Transcribing utterance",
            "detected_language": "es",
            "source_language": "es",
            "target_language": "en",
            "task": "translate",
            "model_size": "large-v3",
            "device": "cpu",
            "noise_floor": 0.0123,
            "ambient_calibrated": True,
        }
        gui.playback_state = PlaybackState(
            status=PlaybackStatus.IDLE,
            active_job_id=None,
            queued_jobs=0,
            source=None,
        )
        gui.persist_settings = lambda: None
        gui.show_status_rows = True
        gui.root = DummyRoot()
        gui.content_frame = DummyFrame()
        gui.sync_background_size = lambda: None
        gui.label = DummyWidget()
        gui.provisional_label = DummyWidget()
        gui.status_label = DummyWidget()
        gui.playback_label = DummyWidget()
        gui.wrap_width = 720
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

    def test_format_provisional_suffix_trims_long_preview(self):
        gui = self.make_gui()

        formatted = gui._format_provisional_suffix("word " * 80)

        self.assertTrue(formatted.startswith("..."))
        self.assertLessEqual(len(formatted), 163)

    def test_format_runtime_status_wraps_to_multiple_lines(self):
        gui = self.make_gui()

        rendered = gui._format_runtime_status()

        self.assertNotIn("\n", rendered)
        self.assertIn("Spanish -> English", rendered)

    def test_format_committed_display_prefers_recent_caption_segments(self):
        gui = self.make_gui()
        committed = "One. Two. Three. Four. Five. Six."

        rendered = gui._format_committed_display(committed)

        self.assertNotIn("One.", rendered)
        self.assertNotIn("Three.", rendered)
        self.assertIn("Five.", rendered)
        self.assertIn("Six.", rendered)
        self.assertEqual(rendered.count("\n"), 1)

    def test_set_status_rows_visible_hides_status_widgets(self):
        gui = self.make_gui()

        gui.set_status_rows_visible(False)

        self.assertFalse(gui.show_status_rows)
        self.assertEqual(gui.status_label.pack_forget_calls, 1)
        self.assertEqual(gui.playback_label.pack_forget_calls, 1)

    def test_set_status_rows_visible_shows_status_widgets(self):
        gui = self.make_gui()
        gui.show_status_rows = False

        gui.set_status_rows_visible(True)

        self.assertTrue(gui.show_status_rows)
        self.assertTrue(gui.status_label.pack_calls)
        self.assertTrue(gui.playback_label.pack_calls)


if __name__ == "__main__":
    unittest.main()
