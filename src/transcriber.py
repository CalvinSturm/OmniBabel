import gc
import json
import os
import queue
import re
import site
import threading
import time
import unicodedata
from pathlib import Path

import numpy as np
from faster_whisper import WhisperModel

from config import (
    AMBIENT_CALIBRATION_SECONDS,
    DEFAULT_BEAM_SIZE,
    DEFAULT_BEST_OF,
    DEFAULT_DEBUG_LOGGING,
    DEFAULT_DEVICE,
    DEFAULT_MAX_UTTERANCE_SECONDS,
    DEFAULT_MAX_SEGMENT_COMPRESSION_RATIO,
    DEFAULT_MAX_SEGMENT_NO_SPEECH_PROB,
    DEFAULT_MIN_SILENCE_DURATION_MS,
    DEFAULT_MIN_SEGMENT_AVG_LOGPROB,
    DEFAULT_MIN_UTTERANCE_SECONDS,
    DEFAULT_MODEL_SIZE,
    DEFAULT_NO_SPEECH_THRESHOLD,
    DEFAULT_PATIENCE,
    DEFAULT_REPETITION_PENALTY,
    DEFAULT_SOURCE_LANGUAGE,
    DEFAULT_TARGET_LANGUAGE,
    DEFAULT_TASK,
    DEFAULT_UTTERANCE_END_SILENCE_SECONDS,
    DEFAULT_VAD_ENERGY_THRESHOLD,
    LANGUAGE_CHOICES,
    MAX_IDLE_BUFFER_SECONDS,
    SEGMENT_EMIT_GUARD_SECONDS,
    TARGET_RATE,
    VAD_END_CONSECUTIVE_FRAMES,
    VAD_END_THRESHOLD_MULTIPLIER,
    VAD_FRAME_SECONDS,
    VAD_START_CONSECUTIVE_FRAMES,
    VAD_START_THRESHOLD_MULTIPLIER,
)
from src.streaming_contracts import ClauseInfo, TranslationUpdate

BANNED_PHRASES = [
    "thank you for watching",
    "english subtitles",
    "please subscribe",
    "hit the like button",
    "hit that like button",
    "subscribe and hit",
    "thanks for watching",
    "please like and subscribe",
    "amara.org",
    "subtitles by",
    "copyright",
    "all rights reserved",
    "subscribe to my channel",
    "telemundo network",
    "captioning and subtitling",
    "captioning by",
    "captioningandsubtitling",
    "captioningadio",
    "caption max",
    "closed captioning",
    "subtitling.com",
    "presented by",
    "next on",
    "coming up next",
]
METADATA_KEYWORDS = {
    "captioning",
    "subtitling",
    "subtitles",
    "telemundo",
    "network",
    "amara",
    "caption",
}

LANGUAGE_LABELS = dict(LANGUAGE_CHOICES)
DEBUG_LOG_PATH = Path(__file__).resolve().parent.parent / "omnibabel-debug.jsonl"
DOMAIN_PATTERN = re.compile(r"\b[a-z0-9-]+\.(com|org|net|tv|io|co)\b", re.IGNORECASE)
PHONE_PATTERN = re.compile(r"(?:\+?\d[\d\-\s().]{6,}\d)")
STREAMING_PREVIEW_HOP_SECONDS = 0.75
STREAMING_PREVIEW_COMMIT_HOP_SECONDS = 1.5
STREAMING_MIN_PREVIEW_SECONDS = 1.0
STREAMING_PREVIEW_CONFIRMATION_COUNT = 3
CLAUSE_ENDING_CHARS = ".?!;:"


class Transcriber:
    def __init__(
        self,
        audio_queue,
        result_callback,
        status_callback=None,
        initial_model_size=None,
        initial_device=None,
        initial_source_language=None,
        initial_target_language=None,
        initial_task=None,
        initial_vad_energy_threshold=None,
        initial_utterance_end_silence_seconds=None,
        initial_min_utterance_seconds=None,
        initial_max_utterance_seconds=None,
        initial_debug_logging_enabled=None,
    ):
        self.audio_queue = audio_queue
        self.result_callback = result_callback
        self.status_callback = status_callback or (lambda payload: None)
        self.running = False
        self.thread = None
        self.model = None
        self.active_model_size = DEFAULT_MODEL_SIZE
        self.active_device = DEFAULT_DEVICE
        self.last_model_change_result = (True, "Success", DEFAULT_MODEL_SIZE, DEFAULT_DEVICE)
        self.user_source_language = DEFAULT_SOURCE_LANGUAGE
        self.target_language = DEFAULT_TARGET_LANGUAGE
        self.task = DEFAULT_TASK
        self.detected_language = None
        self.detected_language_votes = []
        self.vad_energy_threshold = DEFAULT_VAD_ENERGY_THRESHOLD
        self.utterance_end_silence_seconds = DEFAULT_UTTERANCE_END_SILENCE_SECONDS
        self.min_utterance_seconds = DEFAULT_MIN_UTTERANCE_SECONDS
        self.max_utterance_seconds = DEFAULT_MAX_UTTERANCE_SECONDS
        self.debug_logging_enabled = DEFAULT_DEBUG_LOGGING
        self.last_emitted_segment_end_sample = -1
        self.revision_id = 0
        self.commit_id = 0
        self.next_clause_id = 1
        self.committed_text = ""
        self.current_utterance_committed_prefix = ""
        self.last_preview_hypothesis = ""
        self.preview_hypothesis_history = []
        self.last_preview_decode_sample = 0
        self.last_preview_commit_sample = 0
        self.current_utterance_started_committing = False
        self.noise_floor = 0.0
        self.ambient_frame_history = []
        self.ambient_calibrated = False
        self.model_lock = threading.Lock()
        self.flush_event = threading.Event()
        self.flush_event.set()
        self.status = {
            "runtime_state": "initializing",
            "message": "Initializing model",
            "detected_language": None,
            "source_language": self.user_source_language,
            "target_language": self.target_language,
            "task": self.task,
            "model_size": self.active_model_size,
            "device": self.active_device,
            "debug_logging_enabled": self.debug_logging_enabled,
            "vad_energy_threshold": self.vad_energy_threshold,
            "utterance_end_silence_seconds": self.utterance_end_silence_seconds,
            "min_utterance_seconds": self.min_utterance_seconds,
            "max_utterance_seconds": self.max_utterance_seconds,
            "noise_floor": self.noise_floor,
            "ambient_calibrated": self.ambient_calibrated,
        }

        self.inject_nvidia_libs()

        requested_model = initial_model_size or DEFAULT_MODEL_SIZE
        requested_device = initial_device or DEFAULT_DEVICE
        success, message, _, _ = self.change_model(requested_model, requested_device)
        self.configure_runtime(
            source_language=initial_source_language,
            target_language=initial_target_language,
            task=initial_task,
            vad_energy_threshold=initial_vad_energy_threshold,
            utterance_end_silence_seconds=initial_utterance_end_silence_seconds,
            min_utterance_seconds=initial_min_utterance_seconds,
            max_utterance_seconds=initial_max_utterance_seconds,
            debug_logging_enabled=initial_debug_logging_enabled,
        )
        if self.model is None:
            raise RuntimeError(message)

    def inject_nvidia_libs(self):
        try:
            site_packages = site.getsitepackages()
            libs_to_add = []

            for sp in site_packages:
                cublas_path = os.path.join(sp, "nvidia", "cublas", "bin")
                cudnn_path = os.path.join(sp, "nvidia", "cudnn", "bin")

                if os.path.exists(cublas_path):
                    libs_to_add.append(cublas_path)
                if os.path.exists(cudnn_path):
                    libs_to_add.append(cudnn_path)

            libs_to_add.append(os.getcwd())

            for path in libs_to_add:
                print(f"[AI] Adding DLL Path: {path}")
                if hasattr(os, "add_dll_directory"):
                    try:
                        os.add_dll_directory(path)
                    except OSError:
                        pass
                os.environ["PATH"] = path + ";" + os.environ["PATH"]
        except Exception as exc:
            print(f"[AI] Library Injection Error: {exc}")

    def configure_runtime(
        self,
        source_language=None,
        target_language=None,
        task=None,
        vad_energy_threshold=None,
        utterance_end_silence_seconds=None,
        min_utterance_seconds=None,
        max_utterance_seconds=None,
        debug_logging_enabled=None,
    ):
        source_language = source_language or self.user_source_language
        task = task or self.task

        if source_language not in LANGUAGE_LABELS:
            source_language = DEFAULT_SOURCE_LANGUAGE
        if task not in ("translate", "transcribe"):
            task = DEFAULT_TASK

        if task == "translate":
            normalized_target = "en"
        else:
            normalized_target = "source"

        if target_language in ("en", "source"):
            normalized_target = target_language if task == "transcribe" else "en"

        if vad_energy_threshold is not None:
            self.vad_energy_threshold = float(vad_energy_threshold)
        if utterance_end_silence_seconds is not None:
            self.utterance_end_silence_seconds = float(utterance_end_silence_seconds)
        if min_utterance_seconds is not None:
            self.min_utterance_seconds = float(min_utterance_seconds)
        if max_utterance_seconds is not None:
            self.max_utterance_seconds = float(max_utterance_seconds)
        if debug_logging_enabled is not None:
            self.debug_logging_enabled = bool(debug_logging_enabled)
        if self.min_utterance_seconds > self.max_utterance_seconds:
            self.min_utterance_seconds = DEFAULT_MIN_UTTERANCE_SECONDS
            self.max_utterance_seconds = DEFAULT_MAX_UTTERANCE_SECONDS

        self.user_source_language = source_language
        self.target_language = normalized_target
        self.task = task
        self.detected_language = None if source_language == DEFAULT_SOURCE_LANGUAGE else source_language
        self.detected_language_votes.clear()
        self.last_emitted_segment_end_sample = -1
        self.revision_id = 0
        self.commit_id = 0
        self.next_clause_id = 1
        self.committed_text = ""
        self._reset_current_utterance_state()
        self.noise_floor = 0.0
        self.ambient_frame_history.clear()
        self.ambient_calibrated = False
        self._emit_status(
            runtime_state="listening",
            message="Listening for speech",
            source_language=self.user_source_language,
            target_language=self.target_language,
            task=self.task,
            detected_language=self.detected_language,
        )
        self._debug_log(
            "runtime_configured",
            source_language=self.user_source_language,
            target_language=self.target_language,
            task=self.task,
            vad_energy_threshold=self.vad_energy_threshold,
            utterance_end_silence_seconds=self.utterance_end_silence_seconds,
            min_utterance_seconds=self.min_utterance_seconds,
            max_utterance_seconds=self.max_utterance_seconds,
            debug_logging_enabled=self.debug_logging_enabled,
        )
        return self.get_runtime_config()

    def get_runtime_config(self):
        return {
            "source_language": self.user_source_language,
            "target_language": self.target_language,
            "task": self.task,
            "vad_energy_threshold": self.vad_energy_threshold,
            "utterance_end_silence_seconds": self.utterance_end_silence_seconds,
            "min_utterance_seconds": self.min_utterance_seconds,
            "max_utterance_seconds": self.max_utterance_seconds,
            "debug_logging_enabled": self.debug_logging_enabled,
            "noise_floor": self.noise_floor,
            "ambient_calibrated": self.ambient_calibrated,
        }

    def change_model(self, model_size, device):
        print(f"[AI] Request to switch to: {model_size} on {device}")

        if "turbo" in model_size.lower():
            fallback_model = "large-v3"
            self.last_model_change_result = (
                False,
                "large-v3-turbo is transcription-focused and does not reliably support translation here. "
                f"Loaded {fallback_model} instead.",
                fallback_model,
                device,
            )
            model_size = fallback_model

        selected_compute_type = self._get_compute_type(device)
        self._emit_status(runtime_state="loading_model", message=f"Loading {model_size} on {device}")

        try:
            print(f"[AI] Loading candidate model ({device}) as {selected_compute_type}...")
            new_model = WhisperModel(model_size, device=device, compute_type=selected_compute_type)
            self._swap_model(new_model, model_size, device)
            print(f"[AI] Success: Loaded {model_size}")
            if self.last_model_change_result[2] == model_size and self.last_model_change_result[3] == device:
                if self.last_model_change_result[0]:
                    self.last_model_change_result = (True, "Success", model_size, device)
            else:
                self.last_model_change_result = (True, "Success", model_size, device)
            self._emit_status(
                runtime_state="listening",
                message="Listening for speech",
                model_size=model_size,
                device=device,
            )
            self._debug_log("model_loaded", model_size=model_size, device=device)
            return self.last_model_change_result
        except Exception as exc:
            error_msg = str(exc)
            print(f"[AI] CRITICAL ERROR loading model: {error_msg}")
            self._debug_log("model_load_failed", model_size=model_size, device=device, error=error_msg)

            if "cublas" in error_msg.lower() or "cudnn" in error_msg.lower():
                print("[AI] TIP: Verify the installed NVIDIA runtime packages match your ctranslate2 build.")

            if device == "cuda":
                print("[AI] Attempting fallback to CPU without unloading the active model...")
                try:
                    fallback_model = WhisperModel(model_size, device="cpu", compute_type="int8")
                    self._swap_model(fallback_model, model_size, "cpu")
                    self.last_model_change_result = (
                        False,
                        f"GPU failed. Fell back to CPU.\nError: {error_msg}",
                        model_size,
                        "cpu",
                    )
                    self._emit_status(
                        runtime_state="degraded",
                        message="GPU failed; running on CPU",
                        model_size=model_size,
                        device="cpu",
                    )
                    self._debug_log("model_fallback_cpu", model_size=model_size, original_device=device)
                    return self.last_model_change_result
                except Exception as fallback_error:
                    error_msg = f"{error_msg}\nCPU fallback failed: {fallback_error}"

            self.last_model_change_result = (
                False,
                f"Failed to load model: {error_msg}",
                self.active_model_size,
                self.active_device,
            )
            self._emit_status(runtime_state="error", message="Model load failed")
            return self.last_model_change_result

    def _get_compute_type(self, device):
        if device == "cpu":
            return "int8"
        print("[AI] APPLYING STABILITY FIX: Using float32 for CUDA")
        return "float32"

    def _swap_model(self, new_model, model_size, device):
        with self.model_lock:
            old_model = self.model
            self.model = new_model
            self.active_model_size = model_size
            self.active_device = device
            self.detected_language = None if self.user_source_language == DEFAULT_SOURCE_LANGUAGE else self.user_source_language
            self.detected_language_votes.clear()
            self.last_emitted_segment_end_sample = -1
            self.revision_id = 0
            self.commit_id = 0
            self.next_clause_id = 1
            self.committed_text = ""
            self._reset_current_utterance_state()
            self.noise_floor = 0.0
            self.ambient_frame_history.clear()
            self.ambient_calibrated = False

        if old_model is not None:
            del old_model
            gc.collect()

    def _debug_log(self, event_type, **payload):
        if not self.debug_logging_enabled:
            return

        entry = {
            "ts": round(time.time(), 3),
            "event": event_type,
            "model_size": self.active_model_size,
            "device": self.active_device,
            "source_language": self.user_source_language,
            "detected_language": self.detected_language,
            "task": self.task,
        }
        entry.update(payload)
        try:
            DEBUG_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
            with DEBUG_LOG_PATH.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(entry, ensure_ascii=True) + "\n")
        except Exception as exc:
            print(f"[AI] Debug log write error: {exc}")

    def _emit_status(self, **changes):
        self.status.update(changes)
        self.status["model_size"] = self.active_model_size
        self.status["device"] = self.active_device
        self.status["source_language"] = self.user_source_language
        self.status["target_language"] = self.target_language
        self.status["task"] = self.task
        self.status["debug_logging_enabled"] = self.debug_logging_enabled
        self.status["vad_energy_threshold"] = self.vad_energy_threshold
        self.status["utterance_end_silence_seconds"] = self.utterance_end_silence_seconds
        self.status["min_utterance_seconds"] = self.min_utterance_seconds
        self.status["max_utterance_seconds"] = self.max_utterance_seconds
        self.status["noise_floor"] = round(float(self.noise_floor), 6)
        self.status["ambient_calibrated"] = self.ambient_calibrated
        self.status_callback(dict(self.status))

    def _decode_language(self):
        return None if self.user_source_language == DEFAULT_SOURCE_LANGUAGE else self.user_source_language

    def _update_detected_language(self, language_code):
        if self.user_source_language != DEFAULT_SOURCE_LANGUAGE:
            self.detected_language = self.user_source_language
            return
        if not language_code:
            return

        self.detected_language_votes.append(language_code)
        self.detected_language_votes = self.detected_language_votes[-3:]

        if len(self.detected_language_votes) == 3 and len(set(self.detected_language_votes)) == 1:
            self.detected_language = self.detected_language_votes[-1]

        current_language = self.detected_language or language_code
        self._emit_status(detected_language=current_language, message="Detected speech")
        self._debug_log("language_detected", detected_language=current_language, votes=list(self.detected_language_votes))

    def _update_noise_floor(self, rms_values):
        if len(rms_values) == 0:
            return

        quiet_values = [float(value) for value in rms_values if value > 0]
        if not quiet_values:
            return

        max_history = max(int(round(AMBIENT_CALIBRATION_SECONDS / VAD_FRAME_SECONDS)) * 4, 8)
        self.ambient_frame_history.extend(quiet_values)
        if len(self.ambient_frame_history) > max_history:
            self.ambient_frame_history = self.ambient_frame_history[-max_history:]

        baseline = float(np.percentile(self.ambient_frame_history, 80))
        if self.noise_floor <= 0:
            self.noise_floor = baseline
        else:
            self.noise_floor = (self.noise_floor * 0.85) + (baseline * 0.15)

        required_frames = max(int(round(AMBIENT_CALIBRATION_SECONDS / VAD_FRAME_SECONDS)), 1)
        self.ambient_calibrated = len(self.ambient_frame_history) >= required_frames

    def _current_vad_thresholds(self):
        baseline_floor = self.noise_floor if self.noise_floor > 0 else self.vad_energy_threshold * 0.5
        start_threshold = max(self.vad_energy_threshold, baseline_floor * VAD_START_THRESHOLD_MULTIPLIER)
        end_threshold = max(self.vad_energy_threshold * 0.75, baseline_floor * VAD_END_THRESHOLD_MULTIPLIER)
        return start_threshold, end_threshold

    def _find_run_start(self, active_frames, minimum_length):
        run_length = 0
        for index, is_active in enumerate(active_frames):
            run_length = run_length + 1 if is_active else 0
            if run_length >= minimum_length:
                return index - minimum_length + 1
        return None

    def _find_utterance_boundary(self, audio_buffer):
        frame_samples = max(int(TARGET_RATE * VAD_FRAME_SECONDS), 1)
        min_utterance_samples = int(TARGET_RATE * self.min_utterance_seconds)
        max_utterance_samples = int(TARGET_RATE * self.max_utterance_seconds)
        max_idle_samples = int(TARGET_RATE * MAX_IDLE_BUFFER_SECONDS)
        end_silence_frames = max(
            int(round(self.utterance_end_silence_seconds / VAD_FRAME_SECONDS)),
            VAD_END_CONSECUTIVE_FRAMES,
        )

        if len(audio_buffer) < frame_samples:
            return None, 0, audio_buffer

        usable_samples = (len(audio_buffer) // frame_samples) * frame_samples
        frame_buffer = audio_buffer[:usable_samples].reshape(-1, frame_samples)
        rms = np.sqrt(np.mean(np.square(frame_buffer), axis=1) + 1e-10)
        start_threshold, end_threshold = self._current_vad_thresholds()
        start_active_frames = rms >= start_threshold
        speech_start_frame = self._find_run_start(start_active_frames, VAD_START_CONSECUTIVE_FRAMES)

        if speech_start_frame is None:
            self._update_noise_floor(rms)
            trimmed_samples = 0
            if len(audio_buffer) > max_idle_samples:
                trimmed_samples = len(audio_buffer) - max_idle_samples
                audio_buffer = audio_buffer[-max_idle_samples:]
            message = "Calibrating background noise" if not self.ambient_calibrated else "Waiting for speech"
            self._emit_status(runtime_state="listening", message=message)
            return None, trimmed_samples, audio_buffer

        if speech_start_frame > 0:
            self._update_noise_floor(rms[:speech_start_frame])

        end_active_frames = rms >= end_threshold
        active_after_start = np.flatnonzero(end_active_frames[speech_start_frame:])
        if len(active_after_start) == 0:
            self._emit_status(runtime_state="speech_detected", message="Speech detected; building utterance")
            return None, 0, audio_buffer

        last_active_frame = speech_start_frame + int(active_after_start[-1])
        speech_samples = int(np.count_nonzero(end_active_frames[speech_start_frame:last_active_frame + 1]) * frame_samples)
        trailing_silence_frames = len(end_active_frames) - last_active_frame - 1

        if trailing_silence_frames >= end_silence_frames and speech_samples >= min_utterance_samples:
            end_sample = (last_active_frame + 1) * frame_samples
            utterance = audio_buffer[:end_sample].copy()
            remainder = audio_buffer[end_sample:]
            self._debug_log(
                "utterance_ready",
                duration_seconds=round(len(utterance) / TARGET_RATE, 3),
                reason="silence",
                start_threshold=round(start_threshold, 6),
                end_threshold=round(end_threshold, 6),
                noise_floor=round(self.noise_floor, 6),
            )
            return utterance, end_sample, remainder

        if len(audio_buffer) >= max_utterance_samples and speech_samples >= min_utterance_samples:
            end_sample = max((last_active_frame + 1) * frame_samples, min_utterance_samples)
            utterance = audio_buffer[:end_sample].copy()
            remainder = audio_buffer[end_sample:]
            self._debug_log(
                "utterance_ready",
                duration_seconds=round(len(utterance) / TARGET_RATE, 3),
                reason="max_duration",
                start_threshold=round(start_threshold, 6),
                end_threshold=round(end_threshold, 6),
                noise_floor=round(self.noise_floor, 6),
            )
            return utterance, end_sample, remainder

        self._emit_status(runtime_state="speech_detected", message="Speech detected; building utterance")
        return None, 0, audio_buffer

    def _build_initial_prompt(self):
        if self.task == "transcribe":
            return "Transcribe spoken dialogue faithfully in the original language."
        return "Translate spoken dialogue into concise natural English subtitles."

    def _collect_new_segments(self, segments, chunk_start_sample):
        emit_guard_samples = int(TARGET_RATE * SEGMENT_EMIT_GUARD_SECONDS)
        new_segments = []
        segment_debug = []

        for segment in segments:
            raw_text = getattr(segment, "text", "")
            segment_text = raw_text.strip()
            if not segment_text:
                continue

            segment_start = getattr(segment, "start", 0.0) or 0.0
            segment_end = getattr(segment, "end", segment_start) or segment_start
            absolute_end_sample = chunk_start_sample + int(round(segment_end * TARGET_RATE))
            avg_logprob = getattr(segment, "avg_logprob", None)
            no_speech_prob = getattr(segment, "no_speech_prob", None)
            compression_ratio = getattr(segment, "compression_ratio", None)
            accepted, rejection_reason = self._is_reliable_segment(
                avg_logprob=avg_logprob,
                no_speech_prob=no_speech_prob,
                compression_ratio=compression_ratio,
            )
            segment_debug.append(
                {
                    "start": round(float(segment_start), 3),
                    "end": round(float(segment_end), 3),
                    "text": segment_text,
                    "absolute_end_sample": absolute_end_sample,
                    "avg_logprob": None if avg_logprob is None else round(float(avg_logprob), 4),
                    "no_speech_prob": None if no_speech_prob is None else round(float(no_speech_prob), 4),
                    "compression_ratio": None if compression_ratio is None else round(float(compression_ratio), 4),
                    "accepted": accepted,
                    "rejection_reason": rejection_reason,
                }
            )

            if absolute_end_sample <= self.last_emitted_segment_end_sample + emit_guard_samples:
                segment_debug[-1]["accepted"] = False
                segment_debug[-1]["rejection_reason"] = "duplicate_guard"
                continue

            if not accepted:
                continue

            new_segments.append(segment_text)
            self.last_emitted_segment_end_sample = absolute_end_sample

        return new_segments, segment_debug

    def _is_reliable_segment(self, avg_logprob=None, no_speech_prob=None, compression_ratio=None):
        if no_speech_prob is not None and float(no_speech_prob) > DEFAULT_MAX_SEGMENT_NO_SPEECH_PROB:
            return False, "no_speech_prob"
        if avg_logprob is not None and float(avg_logprob) < DEFAULT_MIN_SEGMENT_AVG_LOGPROB:
            return False, "avg_logprob"
        if compression_ratio is not None and float(compression_ratio) > DEFAULT_MAX_SEGMENT_COMPRESSION_RATIO:
            return False, "compression_ratio"
        return True, None

    def _collect_hypothesis_segments(self, segments):
        accepted_segments = []
        segment_debug = []

        for segment in segments:
            raw_text = getattr(segment, "text", "")
            segment_text = raw_text.strip()
            if not segment_text:
                continue

            avg_logprob = getattr(segment, "avg_logprob", None)
            no_speech_prob = getattr(segment, "no_speech_prob", None)
            compression_ratio = getattr(segment, "compression_ratio", None)
            accepted, rejection_reason = self._is_reliable_segment(
                avg_logprob=avg_logprob,
                no_speech_prob=no_speech_prob,
                compression_ratio=compression_ratio,
            )
            segment_debug.append(
                {
                    "start": round(float(getattr(segment, "start", 0.0) or 0.0), 3),
                    "end": round(float(getattr(segment, "end", 0.0) or 0.0), 3),
                    "text": segment_text,
                    "avg_logprob": None if avg_logprob is None else round(float(avg_logprob), 4),
                    "no_speech_prob": None if no_speech_prob is None else round(float(no_speech_prob), 4),
                    "compression_ratio": None if compression_ratio is None else round(float(compression_ratio), 4),
                    "accepted": accepted,
                    "rejection_reason": rejection_reason,
                }
            )
            if accepted:
                accepted_segments.append(segment_text)

        return " ".join(accepted_segments).strip(), segment_debug

    def _reset_current_utterance_state(self):
        self.current_utterance_committed_prefix = ""
        self.last_preview_hypothesis = ""
        self.preview_hypothesis_history = []
        self.last_preview_decode_sample = 0
        self.last_preview_commit_sample = 0
        self.current_utterance_started_committing = False

    def _common_word_prefix(self, left_text, right_text):
        left_words = left_text.split()
        right_words = right_text.split()
        common = []
        for left_word, right_word in zip(left_words, right_words):
            if left_word != right_word:
                break
            common.append(right_word)
        return " ".join(common)

    def _rolling_common_word_prefix(self, hypotheses):
        normalized = [hypothesis.strip() for hypothesis in hypotheses if hypothesis and hypothesis.strip()]
        if not normalized:
            return ""
        stable_prefix = normalized[0]
        for hypothesis in normalized[1:]:
            stable_prefix = self._common_word_prefix(stable_prefix, hypothesis)
            if not stable_prefix:
                break
        return stable_prefix

    def _confirmed_prefix_from_history(self, hypotheses, required_confirmations):
        normalized = [hypothesis.strip() for hypothesis in hypotheses if hypothesis and hypothesis.strip()]
        if len(normalized) < required_confirmations:
            return ""

        recent = normalized[-required_confirmations:]
        common_prefix = self._rolling_common_word_prefix(recent)
        if not common_prefix:
            return ""
        return common_prefix

    def _determine_stable_prefix(self, hypothesis_text, is_final):
        if is_final:
            return hypothesis_text

        self.preview_hypothesis_history.append(hypothesis_text)
        if len(self.preview_hypothesis_history) > STREAMING_PREVIEW_CONFIRMATION_COUNT:
            self.preview_hypothesis_history = self.preview_hypothesis_history[-STREAMING_PREVIEW_CONFIRMATION_COUNT:]

        return self._confirmed_prefix_from_history(
            self.preview_hypothesis_history,
            required_confirmations=STREAMING_PREVIEW_CONFIRMATION_COUNT,
        )

    def _apply_preview_commit_gate(self, stable_prefix_text, chunk_end_sample, is_final):
        if is_final or not stable_prefix_text:
            return stable_prefix_text
        if len(stable_prefix_text) <= len(self.current_utterance_committed_prefix):
            return stable_prefix_text
        if not self.current_utterance_committed_prefix:
            return stable_prefix_text

        preview_commit_hop_samples = int(TARGET_RATE * STREAMING_PREVIEW_COMMIT_HOP_SECONDS)
        if self.last_preview_commit_sample and (chunk_end_sample - self.last_preview_commit_sample) < preview_commit_hop_samples:
            return self.current_utterance_committed_prefix
        return stable_prefix_text

    def _apply_preview_boundary_preference(self, stable_prefix_text, is_final):
        if is_final or not stable_prefix_text:
            return stable_prefix_text
        if len(stable_prefix_text) <= len(self.current_utterance_committed_prefix):
            return stable_prefix_text

        search_start = len(self.current_utterance_committed_prefix)
        last_boundary_index = -1
        for index in range(search_start, len(stable_prefix_text)):
            if stable_prefix_text[index] not in CLAUSE_ENDING_CHARS:
                continue
            next_index = index + 1
            if next_index < len(stable_prefix_text) and not stable_prefix_text[next_index].isspace():
                continue
            last_boundary_index = next_index

        if last_boundary_index <= 0:
            committed_prefix = self.current_utterance_committed_prefix.rstrip()
            if committed_prefix and committed_prefix[-1] in CLAUSE_ENDING_CHARS:
                return self.current_utterance_committed_prefix
            return stable_prefix_text

        boundary_prefix = stable_prefix_text[:last_boundary_index].rstrip()
        if len(boundary_prefix) < len(self.current_utterance_committed_prefix):
            return self.current_utterance_committed_prefix
        return boundary_prefix

    def _build_streaming_update(self, hypothesis_text, stable_prefix_text, chunk_start_sample, audio_chunk_samples, is_final):
        committed_append = ""
        clause = None

        if stable_prefix_text and len(stable_prefix_text) < len(self.current_utterance_committed_prefix):
            stable_prefix_text = self.current_utterance_committed_prefix

        if stable_prefix_text != self.current_utterance_committed_prefix:
            delta = stable_prefix_text[len(self.current_utterance_committed_prefix):]
            if delta:
                if self.committed_text and not self.current_utterance_started_committing:
                    committed_append = f"\n{delta}"
                else:
                    committed_append = delta
                self.committed_text = f"{self.committed_text}{committed_append}"
                self.current_utterance_committed_prefix = stable_prefix_text
                self.current_utterance_started_committing = True
                self.commit_id += 1

        provisional_suffix = hypothesis_text[len(self.current_utterance_committed_prefix):]
        provisional_text = f"{self.committed_text}{provisional_suffix}"

        if is_final and committed_append:
            clause = ClauseInfo(
                clause_id=self.next_clause_id,
                is_final_clause=True,
                char_start=len(self.committed_text) - len(committed_append),
                char_end=len(self.committed_text),
            )
            self.next_clause_id += 1

        self.revision_id += 1
        audio_start_ms = int(round((chunk_start_sample / TARGET_RATE) * 1000))
        audio_end_ms = int(round(((chunk_start_sample + audio_chunk_samples) / TARGET_RATE) * 1000))
        return TranslationUpdate(
            revision_id=self.revision_id,
            commit_id=self.commit_id,
            provisional_text=provisional_text,
            committed_text=self.committed_text,
            committed_append=committed_append,
            detected_language=self.detected_language,
            clause=clause,
            audio_start_ms=audio_start_ms,
            audio_end_ms=audio_end_ms,
        )

    def _emit_translation_update(self, update):
        self.result_callback(update, dict(self.status))

    def _decode_text_from_audio(self, active_model, audio_chunk, chunk_start_sample):
        segments, info = active_model.transcribe(
            audio_chunk,
            beam_size=DEFAULT_BEAM_SIZE,
            best_of=DEFAULT_BEST_OF,
            patience=DEFAULT_PATIENCE,
            task=self.task,
            vad_filter=True,
            vad_parameters=dict(min_silence_duration_ms=DEFAULT_MIN_SILENCE_DURATION_MS),
            initial_prompt=self._build_initial_prompt(),
            language=self._decode_language(),
            condition_on_previous_text=False,
            repetition_penalty=DEFAULT_REPETITION_PENALTY,
            no_speech_threshold=DEFAULT_NO_SPEECH_THRESHOLD,
        )
        self._update_detected_language(getattr(info, "language", None))
        segments = list(segments)
        hypothesis_text, segment_debug = self._collect_hypothesis_segments(segments)
        return hypothesis_text, segment_debug

    def _process_audio_chunk(self, audio_chunk, chunk_start_sample, is_final):
        with self.model_lock:
            active_model = self.model

        if active_model is None:
            return

        try:
            utterance_duration = round(len(audio_chunk) / TARGET_RATE, 3)
            self._emit_status(runtime_state="processing", message="Transcribing utterance")
            self._debug_log(
                "decode_started",
                utterance_duration_seconds=utterance_duration,
                chunk_start_sample=chunk_start_sample,
                is_final=is_final,
            )
            hypothesis_text, segment_debug = self._decode_text_from_audio(active_model, audio_chunk, chunk_start_sample)
            chunk_end_sample = chunk_start_sample + len(audio_chunk)
            self._debug_log(
                "decode_finished",
                emitted_text=hypothesis_text,
                segment_count=len(segment_debug),
                segments=segment_debug,
                is_final=is_final,
            )

            if hypothesis_text and not self.is_hallucination(hypothesis_text):
                stable_prefix = self._determine_stable_prefix(hypothesis_text, is_final)
                stable_prefix = self._apply_preview_boundary_preference(stable_prefix, is_final=is_final)
                stable_prefix = self._apply_preview_commit_gate(
                    stable_prefix,
                    chunk_end_sample=chunk_start_sample + len(audio_chunk),
                    is_final=is_final,
                )
                update = self._build_streaming_update(
                    hypothesis_text,
                    stable_prefix,
                    chunk_start_sample,
                    len(audio_chunk),
                    is_final=is_final,
                )
                self._emit_translation_update(update)
                self.last_preview_hypothesis = hypothesis_text
                self.last_preview_decode_sample = chunk_end_sample
                if update.committed_append:
                    self.last_preview_commit_sample = chunk_end_sample
                if update.committed_append.strip():
                    print(f"> {update.committed_append.strip()}")
                self._emit_status(runtime_state="listening", message="Listening for speech")
                self._debug_log(
                    "text_emitted",
                    text=hypothesis_text,
                    revision_id=update.revision_id,
                    commit_id=update.commit_id,
                    clause_id=update.clause.clause_id if update.clause is not None else None,
                    is_final=is_final,
                )
                if is_final:
                    self._reset_current_utterance_state()
            elif hypothesis_text:
                self.last_preview_decode_sample = chunk_end_sample
                print(f"[AI] Filtered suspicious output: {hypothesis_text}")
                self._emit_status(runtime_state="listening", message="Filtered suspicious output")
                self._debug_log("text_filtered", text=hypothesis_text, is_final=is_final)
                if is_final:
                    self._reset_current_utterance_state()
            else:
                self.last_preview_decode_sample = chunk_end_sample
                self._emit_status(runtime_state="listening", message="No new speech emitted")
                if is_final:
                    self._reset_current_utterance_state()
        except Exception as exc:
            print(f"[AI] Transcription Error: {exc}")
            self._emit_status(runtime_state="error", message=f"Transcription error: {exc}")
            self._debug_log("decode_error", error=str(exc))

    def _maybe_process_preview_chunk(self, audio_buffer, buffer_start_sample):
        min_preview_samples = int(TARGET_RATE * STREAMING_MIN_PREVIEW_SECONDS)
        preview_hop_samples = int(TARGET_RATE * STREAMING_PREVIEW_HOP_SECONDS)

        if len(audio_buffer) < min_preview_samples:
            return
        if self.last_preview_decode_sample and (buffer_start_sample + len(audio_buffer) - self.last_preview_decode_sample) < preview_hop_samples:
            return
        self._process_audio_chunk(audio_buffer.copy(), buffer_start_sample, is_final=False)

    def _flush_audio_buffer(self, audio_buffer, buffer_start_sample):
        if len(audio_buffer) == 0:
            return np.array([], dtype=np.float32), buffer_start_sample

        frame_samples = max(int(TARGET_RATE * VAD_FRAME_SECONDS), 1)
        usable_samples = (len(audio_buffer) // frame_samples) * frame_samples
        if usable_samples <= 0:
            return np.array([], dtype=np.float32), buffer_start_sample

        frame_buffer = audio_buffer[:usable_samples].reshape(-1, frame_samples)
        rms = np.sqrt(np.mean(np.square(frame_buffer), axis=1) + 1e-10)
        active_frames = rms >= self.vad_energy_threshold
        if not np.any(active_frames):
            self._emit_status(runtime_state="listening", message="No buffered speech to flush")
            return np.array([], dtype=np.float32), buffer_start_sample + len(audio_buffer)

        last_active_frame = int(np.flatnonzero(active_frames)[-1])
        end_sample = (last_active_frame + 1) * frame_samples
        utterance = audio_buffer[:end_sample].copy()
        self._debug_log(
            "utterance_ready",
            duration_seconds=round(len(utterance) / TARGET_RATE, 3),
            reason="flush",
        )
        self._process_audio_chunk(utterance, buffer_start_sample, is_final=True)
        return np.array([], dtype=np.float32), buffer_start_sample + end_sample

    def _transcribe_loop(self):
        audio_buffer = np.array([], dtype=np.float32)
        buffer_start_sample = 0
        while self.running:
            try:
                data = self.audio_queue.get(timeout=1)
                if data is None:
                    audio_buffer, buffer_start_sample = self._flush_audio_buffer(audio_buffer, buffer_start_sample)
                    self.flush_event.set()
                    continue
                audio_buffer = np.concatenate((audio_buffer, data))

                while self.running:
                    utterance, consumed_samples, audio_buffer = self._find_utterance_boundary(audio_buffer)
                    if consumed_samples and utterance is None:
                        buffer_start_sample += consumed_samples
                        self._reset_current_utterance_state()
                    if utterance is None:
                        if len(audio_buffer):
                            self._maybe_process_preview_chunk(audio_buffer, buffer_start_sample)
                        break
                    chunk_start_sample = buffer_start_sample
                    buffer_start_sample += consumed_samples
                    self._process_audio_chunk(utterance, chunk_start_sample, is_final=True)
            except queue.Empty:
                continue
            except Exception as exc:
                print(f"[AI] Error: {exc}")
                self._emit_status(runtime_state="error", message=f"Unexpected error: {exc}")
                self._debug_log("loop_error", error=str(exc))

    def is_hallucination(self, text):
        normalized_text = " ".join(text.split())
        text_lower = self._normalize_filter_text(normalized_text)
        for phrase in BANNED_PHRASES:
            if phrase in text_lower:
                return True
        if len(normalized_text) < 2:
            return True
        if self._is_metadata_or_promo_text(normalized_text):
            return True
        return False

    def _normalize_filter_text(self, text):
        lowered = text.lower()
        ascii_text = unicodedata.normalize("NFKD", lowered).encode("ascii", "ignore").decode("ascii")
        return " ".join(ascii_text.split())

    def _is_metadata_or_promo_text(self, text):
        normalized_text = self._normalize_filter_text(text)
        letters = [char for char in text if char.isalpha()]
        uppercase_letters = [char for char in letters if char.isupper()]
        digit_count = sum(char.isdigit() for char in text)
        word_count = len(text.split())

        if DOMAIN_PATTERN.search(normalized_text):
            return True

        if PHONE_PATTERN.search(normalized_text) and digit_count >= 6:
            return True

        keyword_hits = sum(keyword in normalized_text for keyword in METADATA_KEYWORDS)
        if keyword_hits >= 2:
            return True

        if " by " in normalized_text and any(keyword in normalized_text for keyword in {"captioning", "subtitles", "subtitling"}):
            return True

        if letters:
            uppercase_ratio = len(uppercase_letters) / len(letters)
            if uppercase_ratio >= 0.85 and word_count <= 8:
                return True

        if digit_count >= 7 and word_count <= 8:
            return True

        if self._looks_like_non_dialogue_title(text):
            return True

        return False

    def _looks_like_non_dialogue_title(self, text):
        stripped = text.strip()
        if not stripped:
            return False

        punctuation_count = sum(char in ".?!,:;'-" for char in stripped)
        contains_quote_like = any(char in stripped for char in "\"'?!")
        contains_common_dialogue = any(
            token in stripped.lower().split()
            for token in {"i", "you", "we", "he", "she", "they", "yes", "no", "okay", "ok", "hello", "hi"}
        )

        if punctuation_count == 0 and not contains_common_dialogue and len(stripped.split()) <= 7:
            title_case_words = [word for word in stripped.split() if word[:1].isupper()]
            if len(title_case_words) >= max(len(stripped.split()) - 1, 2):
                return True

        if not contains_quote_like and not contains_common_dialogue and stripped.isupper():
            return True

        return False

    def start(self):
        self.running = True
        self.thread = threading.Thread(target=self._transcribe_loop, daemon=True)
        self.thread.start()

    def flush(self, timeout=None):
        self.flush_event.clear()
        self.audio_queue.put(None)
        return self.flush_event.wait(timeout=timeout)

    def stop(self):
        if self.running:
            self.flush(timeout=1.0)
        self.running = False
        self.audio_queue.put(None)
        if self.thread:
            self.thread.join()
