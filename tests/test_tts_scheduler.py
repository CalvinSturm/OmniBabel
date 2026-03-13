import sys
import types
import unittest
import queue
from unittest import mock

import numpy as np

if "pythoncom" not in sys.modules:
    sys.modules["pythoncom"] = types.SimpleNamespace(CoInitialize=lambda: None, CoUninitialize=lambda: None)
if "pyttsx3" not in sys.modules:
    sys.modules["pyttsx3"] = types.SimpleNamespace(init=lambda: None)
if "sounddevice" not in sys.modules:
    sys.modules["sounddevice"] = types.SimpleNamespace(play=lambda *args, **kwargs: None, stop=lambda: None)
if "soundfile" not in sys.modules:
    sys.modules["soundfile"] = types.SimpleNamespace(read=lambda *args, **kwargs: ([], 16000))

from src.streaming_contracts import ClauseInfo, InterruptPolicy, PlaybackStatus, TTSJob, TTSJobSource, TranslationUpdate
from src.tts import KOKORO_DEFAULT_SAMPLE_RATE, KOKORO_DEFAULT_VOICE, KokoroBackend, Pyttsx3Backend, TTSHandle


class TTSSchedulerTests(unittest.TestCase):
    def make_handle(self):
        class DummyLock:
            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

        handle = object.__new__(TTSHandle)
        handle.enabled = True
        handle.last_commit_id = 0
        handle.last_committed_text = ""
        handle.pending_clause_text = ""
        handle.next_job_id = 1
        handle.next_clause_id = 1
        handle.submit_job_calls = []
        handle.state_events = []
        handle.synthesis_queue = queue.Queue()
        handle.playback_queue = queue.Queue()
        handle.state_lock = DummyLock()
        handle.state_callback = lambda state: handle.state_events.append(state)
        handle._emit_state = TTSHandle._emit_state.__get__(handle, TTSHandle)
        handle.submit_job = lambda job: handle.submit_job_calls.append(job)
        return handle

    def test_extract_complete_clauses_preserves_remainder_until_flush(self):
        handle = self.make_handle()

        clauses, remainder = handle._extract_complete_clauses("Hello world. How are you", flush=False)

        self.assertEqual(clauses, ["Hello world."])
        self.assertEqual(remainder.strip(), "How are you")

    def test_submit_translation_update_segments_multiple_clauses(self):
        handle = self.make_handle()
        update = TranslationUpdate(
            revision_id=1,
            commit_id=1,
            provisional_text="Hello world. How are you?",
            committed_text="Hello world. How are you?",
            committed_append="Hello world. How are you?",
            detected_language="en",
            clause=ClauseInfo(clause_id=1, is_final_clause=True, char_start=0, char_end=25),
            audio_start_ms=0,
            audio_end_ms=1200,
        )

        handle.submit_translation_update(update)

        self.assertEqual(len(handle.submit_job_calls), 2)
        self.assertEqual(handle.submit_job_calls[0].text, "Hello world.")
        self.assertEqual(handle.submit_job_calls[0].source, TTSJobSource.COMMITTED_TRANSLATION)
        self.assertEqual(handle.submit_job_calls[1].text, "How are you?")
        self.assertEqual(handle.submit_job_calls[1].source, TTSJobSource.FINAL_CLAUSE)
        self.assertEqual(handle.submit_job_calls[0].job_id, 1)
        self.assertEqual(handle.submit_job_calls[1].job_id, 2)

    def test_submit_translation_update_rejects_non_append_only_commit(self):
        handle = self.make_handle()
        first = TranslationUpdate(
            revision_id=1,
            commit_id=1,
            provisional_text="Hello world.",
            committed_text="Hello world.",
            committed_append="Hello world.",
            detected_language="en",
            clause=ClauseInfo(clause_id=1, is_final_clause=True, char_start=0, char_end=12),
            audio_start_ms=0,
            audio_end_ms=500,
        )
        second = TranslationUpdate(
            revision_id=2,
            commit_id=2,
            provisional_text="Mutated line",
            committed_text="Mutated line",
            committed_append="Mutated line",
            detected_language="en",
            clause=ClauseInfo(clause_id=2, is_final_clause=True, char_start=0, char_end=12),
            audio_start_ms=500,
            audio_end_ms=1000,
        )

        handle.submit_translation_update(first)

        with self.assertRaises(ValueError):
            handle.submit_translation_update(second)

    def test_final_clause_flushes_pending_tail_without_new_commit(self):
        handle = self.make_handle()
        handle.pending_clause_text = "unfinished clause tail"
        handle.last_commit_id = 2
        handle.last_committed_text = "hello world"
        update = TranslationUpdate(
            revision_id=3,
            commit_id=2,
            provisional_text="hello world",
            committed_text="hello world",
            committed_append="",
            detected_language="en",
            clause=ClauseInfo(clause_id=3, is_final_clause=True, char_start=0, char_end=11),
            audio_start_ms=0,
            audio_end_ms=1000,
        )

        handle.submit_translation_update(update)

        self.assertEqual(len(handle.submit_job_calls), 1)
        self.assertEqual(handle.submit_job_calls[0].text, "unfinished clause tail")
        self.assertEqual(handle.submit_job_calls[0].source, TTSJobSource.FINAL_CLAUSE)

    def test_flush_queues_clears_pending_work_and_emits_idle(self):
        handle = self.make_handle()
        handle.synthesis_queue.put("pending")
        handle.playback_queue.put("pending")

        handle.flush_queues(cancel_current=False)

        self.assertTrue(handle.synthesis_queue.empty())
        self.assertTrue(handle.playback_queue.empty())
        self.assertEqual(handle.state_events[-1].status, PlaybackStatus.IDLE)

    def test_submit_job_with_interrupt_policy_flushes_then_queues(self):
        handle = self.make_handle()
        flushed = {"count": 0}
        original_flush = TTSHandle.flush_queues.__get__(handle, TTSHandle)

        def flush_wrapper(cancel_current=False):
            flushed["count"] += 1
            return original_flush(cancel_current=cancel_current)

        handle.flush_queues = flush_wrapper
        job = TTSJob(
            job_id=1,
            commit_id=1,
            clause_id=1,
            text="hello world.",
            source=TTSJobSource.COMMITTED_TRANSLATION,
            interrupt_policy=InterruptPolicy.FLUSH_AND_INTERRUPT,
        )

        TTSHandle.submit_job(handle, job)

        self.assertEqual(flushed["count"], 1)
        self.assertFalse(handle.synthesis_queue.empty())
        self.assertEqual(handle.state_events[-1].status, PlaybackStatus.QUEUED)

    def test_available_backends_includes_system(self):
        handle = self.make_handle()

        available = TTSHandle.get_available_backends(handle)

        self.assertIn("system", available)

    def test_build_system_backend_returns_pyttsx3_backend(self):
        handle = self.make_handle()

        backend = TTSHandle._build_backend(handle, "system")

        self.assertIsInstance(backend, Pyttsx3Backend)

    def test_build_kokoro_backend_returns_backend(self):
        handle = self.make_handle()
        fake_module = object()

        with (
            mock.patch("src.tts.importlib.util.find_spec", return_value=object()),
            mock.patch("src.tts.importlib.import_module", return_value=fake_module),
        ):
            backend = TTSHandle._build_backend(handle, "kokoro")

        self.assertIsInstance(backend, KokoroBackend)
        self.assertIs(backend.kokoro, fake_module)

    def test_kokoro_backend_synthesize_concatenates_pipeline_audio(self):
        backend = object.__new__(KokoroBackend)
        pipeline = mock.Mock()
        pipeline.side_effect = lambda text, voice, split_pattern: iter(
            [
                types.SimpleNamespace(audio=np.array([0.1, 0.2], dtype=np.float32)),
                types.SimpleNamespace(audio=np.array([0.3], dtype=np.float32)),
            ]
        )
        backend.pipeline_cache = {"a": pipeline}
        backend.kokoro = mock.Mock()
        backend._get_pipeline = KokoroBackend._get_pipeline.__get__(backend, KokoroBackend)
        backend._get_lang_code = KokoroBackend._get_lang_code.__get__(backend, KokoroBackend)

        audio, sample_rate = KokoroBackend.synthesize(backend, "Hello world", voice_id=KOKORO_DEFAULT_VOICE)

        self.assertEqual(sample_rate, KOKORO_DEFAULT_SAMPLE_RATE)
        self.assertTrue(np.array_equal(audio, np.array([0.1, 0.2, 0.3], dtype=np.float32)))

    def test_unknown_backend_raises(self):
        handle = self.make_handle()

        with self.assertRaises(ValueError):
            TTSHandle._build_backend(handle, "missing-backend")


if __name__ == "__main__":
    unittest.main()
