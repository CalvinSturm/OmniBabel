import importlib
import importlib.util
import os
import queue
import re
import tempfile
import threading

import numpy as np
import pythoncom
import pyttsx3
import sounddevice as sd
import soundfile as sf

from src.streaming_contracts import (
    InterruptPolicy,
    PlaybackState,
    PlaybackStatus,
    TranslationUpdate,
    TTSJob,
    TTSJobSource,
)

CLAUSE_ENDING_CHARS = ".?!;:"
KOKORO_DEFAULT_SAMPLE_RATE = 24000
KOKORO_DEFAULT_VOICE = "af_heart"


class TTSBackend:
    name = "base"

    def get_voices(self):
        return []

    def synthesize(self, text, voice_id=None):
        raise NotImplementedError


class Pyttsx3Backend(TTSBackend):
    name = "system"

    def get_voices(self):
        try:
            temp_engine = pyttsx3.init()
            voices = temp_engine.getProperty("voices")
            voice_list = [{"id": voice.id, "name": voice.name} for voice in voices]
            del temp_engine
            return voice_list
        except Exception:
            return []

    def synthesize(self, text, voice_id=None):
        temp_file = None
        try:
            fd, temp_file = tempfile.mkstemp(suffix=".wav")
            os.close(fd)
            engine = pyttsx3.init()
            if voice_id:
                try:
                    engine.setProperty("voice", voice_id)
                except Exception:
                    pass
            engine.save_to_file(text, temp_file)
            engine.runAndWait()
            del engine
            data, sample_rate = sf.read(temp_file)
            return data, sample_rate
        finally:
            if temp_file and os.path.exists(temp_file):
                os.remove(temp_file)


class KokoroBackend(TTSBackend):
    name = "kokoro"

    def __init__(self):
        spec = importlib.util.find_spec("kokoro")
        if spec is None:
            raise RuntimeError("Kokoro backend requested but the 'kokoro' package is not installed")
        self.kokoro = importlib.import_module("kokoro")
        self.pipeline_cache = {}

    def get_voices(self):
        return [{"id": KOKORO_DEFAULT_VOICE, "name": "Kokoro af_heart"}]

    def _get_lang_code(self, voice_id):
        voice_name = (voice_id or KOKORO_DEFAULT_VOICE).strip()
        return voice_name[0].lower() if voice_name else "a"

    def _get_pipeline(self, lang_code):
        pipeline = self.pipeline_cache.get(lang_code)
        if pipeline is None:
            pipeline = self.kokoro.KPipeline(lang_code=lang_code, device="cpu")
            self.pipeline_cache[lang_code] = pipeline
        return pipeline

    def synthesize(self, text, voice_id=None):
        if not text or not text.strip():
            raise ValueError("Kokoro synthesize requires non-empty text")

        selected_voice = voice_id or KOKORO_DEFAULT_VOICE
        pipeline = self._get_pipeline(self._get_lang_code(selected_voice))
        audio_parts = []

        for result in pipeline(text.strip(), voice=selected_voice, split_pattern=r"\n+"):
            audio = getattr(result, "audio", None)
            if audio is None:
                continue
            if hasattr(audio, "detach"):
                chunk = audio.detach().cpu().numpy()
            else:
                chunk = np.asarray(audio)
            if chunk.size:
                audio_parts.append(np.asarray(chunk, dtype=np.float32))

        if not audio_parts:
            raise RuntimeError("Kokoro synthesis returned no audio")

        return np.concatenate(audio_parts).astype(np.float32), KOKORO_DEFAULT_SAMPLE_RATE


class TTSHandle:
    def __init__(self, state_callback=None):
        self.synthesis_queue = queue.Queue()
        self.playback_queue = queue.Queue()
        self.running = True
        self.enabled = False
        self.output_device_index = None
        self.voice_id = None
        self.backend_name = "system"
        self.backend = Pyttsx3Backend()
        self.state_callback = state_callback or (lambda state: None)
        self.state_lock = threading.Lock()
        self.last_commit_id = 0
        self.last_committed_text = ""
        self.pending_clause_text = ""
        self.next_job_id = 1
        self.next_clause_id = 1

        self.synthesis_thread = threading.Thread(target=self._synthesis_worker, daemon=True)
        self.playback_thread = threading.Thread(target=self._playback_worker, daemon=True)
        self.synthesis_thread.start()
        self.playback_thread.start()
        self._emit_state(PlaybackStatus.IDLE, active_job_id=None, source=None)

    def get_voices(self):
        return self.backend.get_voices()

    def get_available_backends(self):
        backends = ["system"]
        if importlib.util.find_spec("kokoro") is not None:
            backends.append("kokoro")
        return backends

    def set_backend(self, backend_name):
        if backend_name == self.backend_name:
            return
        self.backend = self._build_backend(backend_name)
        self.backend_name = backend_name

    def _build_backend(self, backend_name):
        if backend_name == "system":
            return Pyttsx3Backend()
        if backend_name == "kokoro":
            return KokoroBackend()
        raise ValueError(f"Unknown TTS backend: {backend_name}")

    def set_voice(self, voice_id):
        print(f"[TTS] Setting Voice ID: {voice_id}")
        self.voice_id = voice_id

    def set_output_device(self, device_index):
        self.output_device_index = device_index

    def submit_job(self, job):
        if not self.enabled or job is None:
            return
        if job.interrupt_policy == InterruptPolicy.FLUSH_AND_INTERRUPT:
            self.flush_queues(cancel_current=True)
        elif job.interrupt_policy == InterruptPolicy.INTERRUPT:
            self.cancel_current()
        self.synthesis_queue.put(job)
        self._emit_state(PlaybackStatus.QUEUED, active_job_id=job.job_id, source=job.source)

    def submit_translation_update(self, update):
        if not self.enabled or update is None:
            return
        is_final_flush = bool(update.clause and update.clause.is_final_clause and self.pending_clause_text.strip())
        if update.commit_id < self.last_commit_id:
            return
        if update.commit_id > self.last_commit_id:
            if not update.committed_text.startswith(self.last_committed_text):
                raise ValueError("Committed transcript must be append-only")
            if update.committed_append and not update.committed_text.endswith(update.committed_append):
                raise ValueError("Committed append must remain a suffix of committed text")
            self.last_commit_id = update.commit_id
            self.last_committed_text = update.committed_text
            self.pending_clause_text += update.committed_append
        elif not is_final_flush:
            return

        clauses, remainder = self._extract_complete_clauses(
            self.pending_clause_text,
            flush=bool(update.clause and update.clause.is_final_clause),
        )
        self.pending_clause_text = remainder

        for index, clause_text in enumerate(clauses):
            is_last_clause = index == len(clauses) - 1
            job_source = (
                TTSJobSource.FINAL_CLAUSE
                if update.clause and update.clause.is_final_clause and is_last_clause
                else TTSJobSource.COMMITTED_TRANSLATION
            )
            job = TTSJob(
                job_id=self.next_job_id,
                commit_id=update.commit_id,
                clause_id=self.next_clause_id,
                text=clause_text,
                source=job_source,
                interrupt_policy=InterruptPolicy.QUEUE,
            )
            self.next_job_id += 1
            self.next_clause_id += 1
            self.submit_job(job)

    def speak(self, text):
        if not self.enabled or not text or not text.strip():
            return
        fallback_job = TTSJob(
            job_id=0,
            commit_id=0,
            clause_id=0,
            text=text,
            source=TTSJobSource.MANUAL_TEST,
            interrupt_policy=InterruptPolicy.QUEUE,
        )
        self.submit_job(fallback_job)

    def _extract_complete_clauses(self, text, flush=False):
        clauses = []
        start = 0
        length = len(text)

        for index, char in enumerate(text):
            if char not in CLAUSE_ENDING_CHARS:
                continue
            next_index = index + 1
            if next_index < length and not text[next_index].isspace():
                continue
            clause = text[start:next_index].strip()
            if clause:
                clauses.append(clause)
            start = next_index

        remainder = text[start:]
        if flush:
            tail = remainder.strip()
            if tail:
                clauses.append(tail)
            remainder = ""
        return clauses, remainder

    def _emit_state(self, status, active_job_id, source):
        with self.state_lock:
            state = PlaybackState(
                status=status,
                active_job_id=active_job_id,
                queued_jobs=self.synthesis_queue.qsize() + self.playback_queue.qsize(),
                source=source,
            )
        self.state_callback(state)

    def _clear_queue(self, target_queue):
        while True:
            try:
                item = target_queue.get_nowait()
            except queue.Empty:
                break
            if item is None:
                target_queue.put(None)
                break

    def flush_queues(self, cancel_current=False):
        self._clear_queue(self.synthesis_queue)
        self._clear_queue(self.playback_queue)
        self.pending_clause_text = ""
        if cancel_current:
            try:
                sd.stop()
            except Exception:
                pass
            self._emit_state(PlaybackStatus.CANCELLED, active_job_id=None, source=None)
        self._emit_state(PlaybackStatus.IDLE, active_job_id=None, source=None)

    def cancel_current(self):
        try:
            sd.stop()
        except Exception:
            pass
        self._emit_state(PlaybackStatus.CANCELLED, active_job_id=None, source=None)

    def _synthesis_worker(self):
        try:
            pythoncom.CoInitialize()
        except Exception:
            pass

        while self.running:
            try:
                job = self.synthesis_queue.get(timeout=1)
            except queue.Empty:
                continue

            if job is None:
                break

            if not self.enabled:
                continue

            temp_file = None
            try:
                self._emit_state(PlaybackStatus.SYNTHESIZING, active_job_id=job.job_id, source=job.source)
                data, sample_rate = self.backend.synthesize(job.text, voice_id=self.voice_id)
                self.playback_queue.put((job, data, sample_rate))
                self._emit_state(PlaybackStatus.QUEUED, active_job_id=job.job_id, source=job.source)
            except Exception as exc:
                print(f"[TTS] Synthesis Error: {exc}")
                self._emit_state(PlaybackStatus.ERROR, active_job_id=job.job_id, source=job.source)

        try:
            pythoncom.CoUninitialize()
        except Exception:
            pass

    def _playback_worker(self):
        while self.running:
            try:
                item = self.playback_queue.get(timeout=1)
            except queue.Empty:
                continue

            if item is None:
                break

            job, data, sample_rate = item
            if not self.enabled:
                continue

            try:
                self._emit_state(PlaybackStatus.PLAYING, active_job_id=job.job_id, source=job.source)
                sd.play(data, sample_rate, device=self.output_device_index, blocking=True)
                next_status = PlaybackStatus.IDLE if self.synthesis_queue.empty() and self.playback_queue.empty() else PlaybackStatus.QUEUED
                next_active = None if next_status == PlaybackStatus.IDLE else job.job_id
                next_source = None if next_status == PlaybackStatus.IDLE else job.source
                self._emit_state(PlaybackStatus.COMPLETED, active_job_id=job.job_id, source=job.source)
                self._emit_state(next_status, active_job_id=next_active, source=next_source)
            except Exception as exc:
                print(f"[TTS] Playback Error: {exc}")
                self._emit_state(PlaybackStatus.ERROR, active_job_id=job.job_id, source=job.source)

    def stop(self):
        self.running = False
        try:
            sd.stop()
        except Exception:
            pass
        self.synthesis_queue.put(None)
        self.playback_queue.put(None)
        if self.synthesis_thread:
            self.synthesis_thread.join(timeout=5)
        if self.playback_thread:
            self.playback_thread.join(timeout=5)
        self._emit_state(PlaybackStatus.IDLE, active_job_id=None, source=None)
