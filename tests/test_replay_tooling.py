import sys
import tempfile
import types
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import numpy as np

if "soundfile" not in sys.modules:
    sys.modules["soundfile"] = types.SimpleNamespace(read=lambda *args, **kwargs: (np.zeros(1, dtype=np.float32), 16000))
if "scipy" not in sys.modules:
    scipy_signal = types.SimpleNamespace(resample_poly=lambda audio, up, down: audio)
    sys.modules["scipy"] = types.SimpleNamespace(signal=scipy_signal)
    sys.modules["scipy.signal"] = scipy_signal
if "faster_whisper" not in sys.modules:
    sys.modules["faster_whisper"] = types.SimpleNamespace(WhisperModel=object)

import replay_audio
from src.streaming_contracts import ClauseInfo, TranslationUpdate
from tests.run_replay_suite import evaluate_case


class FakeTranscriber:
    def __init__(self, audio_queue, result_callback, status_callback, **kwargs):
        self.audio_queue = audio_queue
        self.result_callback = result_callback
        self.status_callback = status_callback
        self.runtime_config = {
            "source_language": kwargs.get("initial_source_language", "auto"),
            "target_language": "en",
            "task": kwargs.get("initial_task", "translate"),
            "vad_energy_threshold": kwargs.get("initial_vad_energy_threshold", 0.012),
            "utterance_end_silence_seconds": kwargs.get("initial_utterance_end_silence_seconds", 0.45),
            "min_utterance_seconds": kwargs.get("initial_min_utterance_seconds", 0.8),
            "max_utterance_seconds": kwargs.get("initial_max_utterance_seconds", 8.0),
            "debug_logging_enabled": kwargs.get("initial_debug_logging_enabled", False),
            "transcriber_queue_depth": 0,
            "transcriber_buffer_seconds": 0.0,
            "dropped_transcription_seconds": 0.0,
            "overload_events": 0,
            "capture_to_commit_latency_ms": 42,
            "load_shedding_active": False,
        }
        self.status = {
            "runtime_state": "listening",
            "message": "Listening for speech",
            "detected_language": "en",
            "transcriber_queue_depth": 0,
            "transcriber_buffer_seconds": 0.0,
            "dropped_transcription_seconds": 0.0,
            "overload_events": 0,
            "capture_to_commit_latency_ms": 42,
            "load_shedding_active": False,
        }

    def start(self):
        self.status_callback(dict(self.status))

    def flush(self, timeout=None):
        self.status_callback({"runtime_state": "processing", "message": "Transcribing utterance"})
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
                audio_end_ms=500,
            ),
            {
                "detected_language": "en",
                "runtime_state": "listening",
                "capture_to_commit_latency_ms": 42,
                "transcriber_queue_depth": 0,
                "transcriber_buffer_seconds": 0.25,
                "load_shedding_active": False,
            },
        )
        self.status_callback(dict(self.status))
        return True

    def stop(self):
        return None

    def get_runtime_config(self):
        return dict(self.runtime_config)


class ReplayToolingTests(unittest.TestCase):
    def test_run_replay_returns_structured_summary(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            audio_path = Path(tmpdir) / "sample.wav"
            audio_path.write_bytes(b"placeholder")
            args = SimpleNamespace(
                input=audio_path,
                model="small",
                device="cpu",
                source_language="auto",
                task="translate",
                vad_energy_threshold=0.012,
                utterance_end_silence_seconds=0.45,
                min_utterance_seconds=0.8,
                max_utterance_seconds=8.0,
                debug=False,
                block_seconds=0.25,
                realtime=False,
                quiet=True,
            )

            with (
                mock.patch.object(replay_audio, "Transcriber", FakeTranscriber),
                mock.patch.object(replay_audio, "load_audio", return_value=(np.zeros(1600, dtype=np.float32), 16000)),
            ):
                summary = replay_audio.run_replay(args)

        self.assertEqual(summary["emission_count"], 1)
        self.assertEqual(summary["emitted_text"], ["hello world"])
        self.assertEqual(summary["final_committed_text"], "hello world")
        self.assertTrue(summary["append_only_valid"])
        self.assertEqual(summary["revision_ids"], [1])
        self.assertEqual(summary["commit_ids"], [1])
        self.assertEqual(summary["clause_ids"], [1])
        self.assertTrue(summary["flush_completed"])
        self.assertIn("processing", summary["status_sequence"])
        self.assertEqual(summary["runtime_config"]["task"], "translate")
        self.assertEqual(summary["max_capture_to_commit_latency_ms"], 42)
        self.assertEqual(summary["final_capture_to_commit_latency_ms"], 42)
        self.assertEqual(summary["max_transcriber_queue_depth"], 0)
        self.assertEqual(summary["load_shedding_event_count"], 0)
        self.assertEqual(summary["degraded_event_count"], 0)

    def test_evaluate_case_reports_expectation_failures(self):
        summary = {
            "flush_completed": True,
            "emission_count": 1,
            "emitted_text": ["hello world"],
            "status_sequence": ["processing", "listening"],
            "append_only_valid": False,
            "max_capture_to_commit_latency_ms": 500,
            "max_transcriber_queue_depth": 9,
            "max_transcriber_buffer_seconds": 4.5,
            "load_shedding_event_count": 2,
            "degraded_event_count": 1,
        }
        case = {
            "expect": {
                "flush_completed": True,
                "min_emission_count": 2,
                "contains_any_text": ["missing phrase"],
                "require_status": ["error"],
                "append_only_valid": True,
                "monotonic_revision_ids": True,
                "max_capture_to_commit_latency_ms_at_most": 250,
                "max_transcriber_queue_depth_at_most": 4,
                "max_transcriber_buffer_seconds_at_most": 1.0,
                "load_shedding_event_count_at_most": 0,
                "degraded_event_count_at_most": 0,
            }
        }

        errors = evaluate_case(case, summary)

        self.assertEqual(len(errors), 9)
        self.assertTrue(any("emission_count>=" in error for error in errors))
        self.assertTrue(any("missing phrase" in error for error in errors))
        self.assertTrue(any("status_sequence" in error for error in errors))
        self.assertTrue(any("append_only_valid" in error for error in errors))
        self.assertTrue(any("max_capture_to_commit_latency_ms" in error for error in errors))
        self.assertTrue(any("max_transcriber_queue_depth" in error for error in errors))
        self.assertTrue(any("max_transcriber_buffer_seconds" in error for error in errors))
        self.assertTrue(any("load_shedding_event_count" in error for error in errors))
        self.assertTrue(any("degraded_event_count" in error for error in errors))


if __name__ == "__main__":
    unittest.main()
