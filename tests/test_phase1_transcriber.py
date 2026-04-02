import sys
import types
import unittest
import queue
from unittest import mock
from types import SimpleNamespace
from collections import deque

import numpy as np


if "faster_whisper" not in sys.modules:
    sys.modules["faster_whisper"] = types.SimpleNamespace(WhisperModel=object)

from config import TARGET_RATE, VAD_FRAME_SECONDS, WHISPER_MODEL_CACHE_DIR
from src.streaming_contracts import TranslationUpdate
from src.transcriber import Transcriber


class TranscriberPhase1Tests(unittest.TestCase):
    def make_transcriber(self):
        class DummyLock:
            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

        transcriber = object.__new__(Transcriber)
        transcriber.user_source_language = "auto"
        transcriber.target_language = "en"
        transcriber.task = "translate"
        transcriber.active_model_size = "large-v3"
        transcriber.active_device = "cpu"
        transcriber.detected_language = None
        transcriber.detected_language_votes = []
        transcriber.vad_energy_threshold = 0.012
        transcriber.utterance_end_silence_seconds = 0.3
        transcriber.min_utterance_seconds = 0.2
        transcriber.max_utterance_seconds = 2.0
        transcriber.debug_logging_enabled = False
        transcriber.last_emitted_segment_end_sample = -1
        transcriber.revision_id = 0
        transcriber.commit_id = 0
        transcriber.next_clause_id = 1
        transcriber.committed_text = ""
        transcriber.current_utterance_committed_prefix = ""
        transcriber.last_preview_hypothesis = ""
        transcriber.preview_hypothesis_history = []
        transcriber.last_preview_decode_sample = 0
        transcriber.last_preview_commit_sample = 0
        transcriber.current_utterance_started_committing = False
        transcriber.noise_floor = 0.0
        transcriber.ambient_frame_history = []
        transcriber.ambient_calibrated = False
        transcriber.filtered_log_times = {}
        transcriber.model_lock = DummyLock()
        transcriber.model = object()
        transcriber.audio_queue = queue.Queue()
        transcriber.dropped_transcription_seconds = 0.0
        transcriber.overload_events = 0
        transcriber.capture_to_commit_latency_ms = None
        transcriber.load_shedding_active = False
        transcriber.status = {}
        transcriber.status_updates = []
        transcriber.status_callback = lambda payload: transcriber.status_updates.append(payload)
        transcriber.result_events = []
        transcriber.result_callback = lambda text, status: transcriber.result_events.append((text, status))
        transcriber._debug_log = lambda *args, **kwargs: None
        transcriber._emit_status = lambda **changes: changes
        return transcriber

    def test_hysteresis_rejects_background_noise_and_calibrates(self):
        transcriber = self.make_transcriber()
        frame_samples = int(TARGET_RATE * VAD_FRAME_SECONDS)
        noise_only = np.full(frame_samples * 20, 0.006, dtype=np.float32)

        utterance, consumed, remainder = transcriber._find_utterance_boundary(noise_only)

        self.assertIsNone(utterance)
        self.assertEqual(consumed, 0)
        self.assertEqual(len(remainder), len(noise_only))
        self.assertGreater(transcriber.noise_floor, 0.0)
        self.assertTrue(transcriber.ambient_calibrated)

    def test_hysteresis_emits_after_consecutive_speech_and_trailing_silence(self):
        transcriber = self.make_transcriber()
        frame_samples = int(TARGET_RATE * VAD_FRAME_SECONDS)
        ambient = np.full(frame_samples * 15, 0.004, dtype=np.float32)
        transcriber._find_utterance_boundary(ambient)

        speech = np.full(frame_samples * 4, 0.03, dtype=np.float32)
        silence = np.zeros(frame_samples * 4, dtype=np.float32)
        clip = np.concatenate([speech, silence])

        utterance, consumed, remainder = transcriber._find_utterance_boundary(clip)

        self.assertIsNotNone(utterance)
        self.assertEqual(consumed, len(utterance))
        self.assertEqual(len(remainder), len(clip) - len(utterance))
        self.assertGreaterEqual(len(utterance), len(speech))

    def test_flush_processes_final_speech_without_trailing_silence(self):
        transcriber = self.make_transcriber()
        transcriber._process_audio_chunk = (
            lambda chunk, start, is_final=False, capture_started_at_ms=None: transcriber.result_events.append(
                (len(chunk), start, is_final)
            )
        )

        frame_samples = int(TARGET_RATE * VAD_FRAME_SECONDS)
        speech = np.full(frame_samples * 5, 0.03, dtype=np.float32)

        remainder, next_start = transcriber._flush_audio_buffer(speech, 1234)

        self.assertEqual(remainder.tolist(), [])
        self.assertEqual(next_start, 1234 + len(speech))
        self.assertEqual(transcriber.result_events, [(len(speech), 1234, True)])

    def test_collect_new_segments_filters_low_confidence_segments(self):
        transcriber = self.make_transcriber()
        segments = [
            SimpleNamespace(
                text="hello there",
                start=0.0,
                end=0.8,
                avg_logprob=-0.4,
                no_speech_prob=0.1,
                compression_ratio=1.2,
            ),
            SimpleNamespace(
                text="unstable text",
                start=0.9,
                end=1.4,
                avg_logprob=-1.4,
                no_speech_prob=0.1,
                compression_ratio=1.2,
            ),
            SimpleNamespace(
                text="probably silence",
                start=1.5,
                end=2.0,
                avg_logprob=-0.2,
                no_speech_prob=0.95,
                compression_ratio=1.0,
            ),
        ]

        new_segments, segment_debug = transcriber._collect_new_segments(segments, chunk_start_sample=0)

        self.assertEqual(new_segments, ["hello there"])
        self.assertEqual(segment_debug[0]["accepted"], True)
        self.assertEqual(segment_debug[1]["rejection_reason"], "avg_logprob")
        self.assertEqual(segment_debug[2]["rejection_reason"], "no_speech_prob")

    def test_build_streaming_update_emits_append_only_contract(self):
        transcriber = self.make_transcriber()

        first = transcriber._build_streaming_update(
            "hello world",
            "hello world",
            0,
            int(TARGET_RATE * 1.0),
            is_final=False,
        )
        second = transcriber._build_streaming_update(
            "hello world next clause",
            "hello world next clause",
            int(TARGET_RATE * 1.0),
            int(TARGET_RATE * 0.5),
            is_final=True,
        )

        self.assertIsInstance(first, TranslationUpdate)
        self.assertEqual(first.revision_id, 1)
        self.assertEqual(first.commit_id, 1)
        self.assertEqual(first.committed_text, "hello world")
        self.assertEqual(first.committed_append, "hello world")
        self.assertIsNone(first.clause)

        self.assertEqual(second.revision_id, 2)
        self.assertEqual(second.commit_id, 2)
        self.assertEqual(second.committed_text, "hello world next clause")
        self.assertEqual(second.committed_append, " next clause")
        self.assertEqual(second.clause.clause_id, 1)

    def test_common_word_prefix_keeps_stable_prefix_only(self):
        transcriber = self.make_transcriber()

        prefix = transcriber._common_word_prefix("hello there general", "hello there world")

        self.assertEqual(prefix, "hello there")

    def test_rolling_common_word_prefix_uses_recent_preview_history(self):
        transcriber = self.make_transcriber()

        prefix = transcriber._rolling_common_word_prefix(
            [
                "hello there general kenobi",
                "hello there world again",
                "hello there friend",
            ]
        )

        self.assertEqual(prefix, "hello there")

    def test_determine_stable_prefix_requires_multiple_preview_confirmations(self):
        transcriber = self.make_transcriber()

        first = transcriber._determine_stable_prefix("alpha beta gamma", is_final=False)
        second = transcriber._determine_stable_prefix("alpha beta delta", is_final=False)
        third = transcriber._determine_stable_prefix("alpha beta epsilon", is_final=False)

        self.assertEqual(first, "")
        self.assertEqual(second, "")
        self.assertEqual(third, "alpha beta")

    def test_determine_stable_prefix_holds_new_word_until_confirmation_depth_met(self):
        transcriber = self.make_transcriber()

        transcriber._determine_stable_prefix("alpha beta gamma", is_final=False)
        transcriber._determine_stable_prefix("alpha beta gamma delta", is_final=False)
        stable = transcriber._determine_stable_prefix("alpha beta gamma epsilon", is_final=False)

        self.assertEqual(stable, "alpha beta gamma")

    def test_determine_stable_prefix_ignores_recent_regression_beyond_confirmed_prefix(self):
        transcriber = self.make_transcriber()

        transcriber._determine_stable_prefix("alpha beta gamma delta", is_final=False)
        transcriber._determine_stable_prefix("alpha beta gamma epsilon", is_final=False)
        transcriber._determine_stable_prefix("alpha beta gamma zeta", is_final=False)
        stable = transcriber._determine_stable_prefix("alpha beta theta", is_final=False)

        self.assertEqual(stable, "alpha beta")

    def test_determine_stable_prefix_final_decode_commits_full_hypothesis(self):
        transcriber = self.make_transcriber()
        transcriber.preview_hypothesis_history = ["alpha beta gamma", "alpha beta delta"]

        stable = transcriber._determine_stable_prefix("alpha beta final clause", is_final=True)

        self.assertEqual(stable, "alpha beta final clause")
        self.assertEqual(transcriber.preview_hypothesis_history, ["alpha beta gamma", "alpha beta delta"])

    def test_preview_commit_gate_allows_first_preview_commit(self):
        transcriber = self.make_transcriber()
        stable = transcriber._apply_preview_commit_gate("alpha beta", chunk_end_sample=int(TARGET_RATE * 1.0), is_final=False)

        self.assertEqual(stable, "alpha beta")

    def test_preview_commit_gate_holds_preview_commit_until_commit_hop_elapsed(self):
        transcriber = self.make_transcriber()
        transcriber.current_utterance_committed_prefix = "alpha"
        transcriber.last_preview_commit_sample = int(TARGET_RATE * 1.0)

        stable = transcriber._apply_preview_commit_gate("alpha beta", chunk_end_sample=int(TARGET_RATE * 1.8), is_final=False)

        self.assertEqual(stable, "alpha")

    def test_preview_commit_gate_allows_final_decode_without_hop_delay(self):
        transcriber = self.make_transcriber()
        transcriber.current_utterance_committed_prefix = "alpha"
        transcriber.last_preview_commit_sample = int(TARGET_RATE * 1.0)

        stable = transcriber._apply_preview_commit_gate("alpha beta", chunk_end_sample=int(TARGET_RATE * 1.1), is_final=True)

        self.assertEqual(stable, "alpha beta")

    def test_filtered_preview_still_advances_preview_decode_sample(self):
        transcriber = self.make_transcriber()
        transcriber._decode_text_from_audio = lambda active_model, audio_chunk, chunk_start_sample: (
            "Subtitles by the Amara.org community",
            [],
        )
        transcriber.is_hallucination = lambda text: True

        transcriber._process_audio_chunk(np.zeros(int(TARGET_RATE), dtype=np.float32), 3200, is_final=False)

        self.assertEqual(transcriber.last_preview_decode_sample, 3200 + int(TARGET_RATE))

    def test_filtered_output_cooldown_suppresses_repeated_phrase(self):
        transcriber = self.make_transcriber()

        first = transcriber._should_log_filtered_output("Subtitles by the Amara.org community", now_ts=100.0)
        second = transcriber._should_log_filtered_output("Subtitles by the Amara.org community", now_ts=103.0)

        self.assertTrue(first)
        self.assertFalse(second)

    def test_filtered_output_cooldown_uses_normalized_phrase_key(self):
        transcriber = self.make_transcriber()

        first = transcriber._should_log_filtered_output("TELEMUNDO NETWORK captioning by Daniela Martínez", now_ts=100.0)
        second = transcriber._should_log_filtered_output("telemundo   network captioning by daniela martinez", now_ts=101.0)
        third = transcriber._should_log_filtered_output("telemundo network captioning by daniela martinez", now_ts=109.0)

        self.assertTrue(first)
        self.assertFalse(second)
        self.assertTrue(third)

    def test_preview_boundary_preference_trims_to_last_clause_boundary(self):
        transcriber = self.make_transcriber()

        stable = transcriber._apply_preview_boundary_preference(
            "alpha beta. gamma delta",
            is_final=False,
        )

        self.assertEqual(stable, "alpha beta.")

    def test_preview_boundary_preference_keeps_full_prefix_without_clause_boundary(self):
        transcriber = self.make_transcriber()

        stable = transcriber._apply_preview_boundary_preference(
            "alpha beta gamma",
            is_final=False,
        )

        self.assertEqual(stable, "alpha beta gamma")

    def test_preview_boundary_preference_does_not_retract_existing_commit(self):
        transcriber = self.make_transcriber()
        transcriber.current_utterance_committed_prefix = "alpha beta."

        stable = transcriber._apply_preview_boundary_preference(
            "alpha beta. gamma delta",
            is_final=False,
        )

        self.assertEqual(stable, "alpha beta.")

    def test_preview_boundary_preference_keeps_final_decode_unchanged(self):
        transcriber = self.make_transcriber()

        stable = transcriber._apply_preview_boundary_preference(
            "alpha beta. gamma delta",
            is_final=True,
        )

        self.assertEqual(stable, "alpha beta. gamma delta")

    def test_hallucination_filter_blocks_broadcast_and_caption_junk(self):
        transcriber = self.make_transcriber()

        self.assertTrue(transcriber.is_hallucination("THE LORD OF THE SKIES"))
        self.assertTrue(
            transcriber.is_hallucination("TELEMUNDO NETWORK captioningandsubtitling.com 887-3060")
        )
        self.assertTrue(transcriber.is_hallucination("Subtitles by the Amara.org community"))
        self.assertTrue(transcriber.is_hallucination("LADY ALTAGRACIA"))
        self.assertTrue(transcriber.is_hallucination("TELEMUNDO NETWORK captioning by Daniela Martinez"))
        self.assertTrue(transcriber.is_hallucination("TELEMUNDO NETWORK captioning by Daniela Martinez"))
        self.assertTrue(transcriber.is_hallucination("TELEMUNDO NETWORK captioning by Daniela Martínez"))
        self.assertTrue(transcriber.is_hallucination("TELEMUNDO NETWORK captioningadio.com 887-3060"))

    def test_hallucination_filter_keeps_dialogue_lines(self):
        transcriber = self.make_transcriber()

        self.assertFalse(transcriber.is_hallucination("Prima! Yes?"))
        self.assertFalse(
            transcriber.is_hallucination("Did you finish your check? Because I have news for you about your donor.")
        )
        self.assertFalse(
            transcriber.is_hallucination("It's just that you've made the business he founded grow so much.")
        )

    def test_change_model_uses_project_local_whisper_cache(self):
        transcriber = self.make_transcriber()
        transcriber.active_model_size = "base"
        transcriber.active_device = "cpu"
        transcriber.last_model_change_result = (True, "Success", "base", "cpu")
        transcriber._get_compute_type = lambda device: "float32"
        transcriber._swap_model = lambda new_model, model_size, device: None

        with mock.patch("src.transcriber.WhisperModel", return_value=object()) as whisper_model:
            result = transcriber.change_model("large-v3", "cuda")

        self.assertEqual(result, (True, "Success", "large-v3", "cuda"))
        whisper_model.assert_called_once_with(
            "large-v3",
            device="cuda",
            compute_type="float32",
            download_root=str(WHISPER_MODEL_CACHE_DIR),
        )

    def test_cap_audio_buffer_drops_stale_backlog(self):
        transcriber = self.make_transcriber()
        transcriber._debug_log = lambda *args, **kwargs: None
        transcriber._emit_status = mock.Mock()

        oversized = np.zeros(int(TARGET_RATE * 15), dtype=np.float32)

        trimmed, next_start = transcriber._cap_audio_buffer(oversized, 800)

        self.assertEqual(len(trimmed), int(TARGET_RATE * 12.0))
        self.assertEqual(next_start, 800 + int(TARGET_RATE * 3.0))
        self.assertGreater(transcriber.dropped_transcription_seconds, 0.0)
        self.assertEqual(transcriber.overload_events, 1)
        self.assertTrue(transcriber.load_shedding_active)
        transcriber._emit_status.assert_called()

    def test_emit_status_clears_load_shedding_flag_after_recovery(self):
        transcriber = self.make_transcriber()
        transcriber.status = {"load_shedding_active": True}

        Transcriber._emit_status(transcriber, runtime_state="listening", message="Listening for speech")

        self.assertFalse(transcriber.status_updates[-1]["load_shedding_active"])

    def test_buffer_metadata_tracks_oldest_capture_timestamp(self):
        transcriber = self.make_transcriber()
        metadata = deque()

        transcriber._append_buffer_metadata(metadata, 1000, 111)
        transcriber._append_buffer_metadata(metadata, 2000, 222)
        started_at_ms = transcriber._consume_buffer_metadata(metadata, 1500)

        self.assertEqual(started_at_ms, 111)
        self.assertEqual(metadata[0][1], 222)

    def test_normalize_audio_item_accepts_timestamped_queue_payload(self):
        transcriber = self.make_transcriber()

        audio, captured_at_ms = transcriber._normalize_audio_item((np.ones(4, dtype=np.float32), 321))

        self.assertEqual(captured_at_ms, 321)
        self.assertEqual(len(audio), 4)


if __name__ == "__main__":
    unittest.main()
