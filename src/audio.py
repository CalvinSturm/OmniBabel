import pyaudiowpatch as pyaudio
import numpy as np
import scipy.signal
import threading
import time
import queue
from config import (
    RAW_AUDIO_QUEUE_MAX_BLOCKS,
    TARGET_RATE,
    CHUNK_SIZE,
    INPUT_RESAMPLE_BLOCK_SECONDS,
    RESAMPLE_CONTEXT_SECONDS,
    TRANSCRIBER_INPUT_QUEUE_MAX_BLOCKS,
)


def find_loopback_device(p):
    wasapi_info = p.get_host_api_info_by_type(pyaudio.paWASAPI)
    default_speakers = p.get_device_info_by_index(wasapi_info["defaultOutputDevice"])

    if not default_speakers["isLoopbackDevice"]:
        for loopback in p.get_loopback_device_info_generator():
            if default_speakers["name"] in loopback["name"]:
                return loopback
    return default_speakers


def probe_loopback_device():
    p = None
    try:
        p = pyaudio.PyAudio()
        device_info = find_loopback_device(p)
        if not device_info:
            return False, "No system loopback device was found."
        return True, device_info["name"]
    except Exception as exc:
        return False, str(exc)
    finally:
        if p is not None:
            p.terminate()

class AudioRecorder:
    def __init__(self, audio_queue, stats_callback=None):
        self.audio_queue = audio_queue
        self.stats_callback = stats_callback or (lambda payload: None)
        self.running = False
        self.thread = None
        self.device_channels = 1
        self.device_rate = 44100
        self.raw_audio_queue = queue.Queue(maxsize=RAW_AUDIO_QUEUE_MAX_BLOCKS)
        self.pending_audio = np.array([], dtype=np.float32)
        self.resample_context = np.array([], dtype=np.float32)
        self.dropped_raw_audio_blocks = 0
        self.dropped_transcriber_blocks = 0

    def _emit_stats(self):
        self.stats_callback(
            {
                "capture_queue_depth": self.raw_audio_queue.qsize(),
                "transcriber_queue_depth": self.audio_queue.qsize(),
                "dropped_raw_audio_blocks": self.dropped_raw_audio_blocks,
                "dropped_transcriber_blocks": self.dropped_transcriber_blocks,
            }
        )

    def _put_with_drop_oldest(self, target_queue, item, dropped_attr):
        while True:
            try:
                target_queue.put_nowait(item)
                self._emit_stats()
                return
            except queue.Full:
                try:
                    target_queue.get_nowait()
                except queue.Empty:
                    continue
                setattr(self, dropped_attr, getattr(self, dropped_attr) + 1)
                self._emit_stats()

    def get_loopback_device(self, p):
        try:
            return find_loopback_device(p)
        except Exception as e:
            print(f"[Audio] Error finding system audio: {e}")
            return None

    def callback(self, in_data, frame_count, time_info, status):
        audio_data = np.frombuffer(in_data, dtype=np.int16).astype(np.float32) / 32768.0

        if self.device_channels > 1:
            audio_data = audio_data.reshape(-1, self.device_channels).mean(axis=1)

        self._put_with_drop_oldest(self.raw_audio_queue, audio_data, "dropped_raw_audio_blocks")
        return (in_data, pyaudio.paContinue)

    def _drain_raw_audio(self):
        chunks = []
        while True:
            try:
                chunks.append(self.raw_audio_queue.get_nowait())
            except queue.Empty:
                break
        if chunks:
            self.pending_audio = np.concatenate([self.pending_audio, *chunks])

    def _resample_block(self, audio_block):
        if len(audio_block) == 0:
            return np.array([], dtype=np.float32)

        if self.device_rate == TARGET_RATE:
            return audio_block.astype(np.float32, copy=False)

        context = self.resample_context
        combined = np.concatenate((context, audio_block)) if len(context) else audio_block
        resampled = scipy.signal.resample_poly(combined, TARGET_RATE, self.device_rate).astype(np.float32)

        context_output_samples = int(round(len(context) * TARGET_RATE / self.device_rate))
        trimmed = resampled[context_output_samples:]

        context_samples = max(int(self.device_rate * RESAMPLE_CONTEXT_SECONDS), 1)
        self.resample_context = combined[-context_samples:].copy()
        return trimmed

    def _emit_resampled_audio(self, flush=False):
        block_samples = max(int(self.device_rate * INPUT_RESAMPLE_BLOCK_SECONDS), CHUNK_SIZE)

        while len(self.pending_audio) >= block_samples or (flush and len(self.pending_audio) > 0):
            take = len(self.pending_audio) if flush and len(self.pending_audio) < block_samples else block_samples
            block = self.pending_audio[:take]
            self.pending_audio = self.pending_audio[take:]
            resampled = self._resample_block(block)
            if len(resampled):
                self._put_with_drop_oldest(
                    self.audio_queue,
                    (resampled, int(time.time() * 1000)),
                    "dropped_transcriber_blocks",
                )

    def _record_loop(self):
        p = pyaudio.PyAudio()
        device_info = self.get_loopback_device(p)
        
        if not device_info:
            print("[Audio] No device found. Exiting audio thread.")
            return

        self.device_rate = int(device_info["defaultSampleRate"])
        self.device_channels = device_info["maxInputChannels"]
        
        print(f"[Audio] Listening to: {device_info['name']}")

        try:
            stream = p.open(format=pyaudio.paInt16,
                            channels=self.device_channels,
                            rate=self.device_rate,
                            input=True,
                            input_device_index=device_info["index"],
                            frames_per_buffer=CHUNK_SIZE,
                            stream_callback=self.callback)
            
            stream.start_stream()
            while self.running:
                self._drain_raw_audio()
                self._emit_resampled_audio()
                self._emit_stats()
                time.sleep(0.1)
            stream.stop_stream()
            stream.close()
        except Exception as e:
            print(f"[Audio] Recording Error: {e}")
        finally:
            self._drain_raw_audio()
            self._emit_resampled_audio(flush=True)
            self._emit_stats()
            p.terminate()

    def start(self):
        self.running = True
        self.thread = threading.Thread(target=self._record_loop, daemon=True)
        self.thread.start()

    def stop(self):
        self.running = False
        if self.thread:
            self.thread.join()
