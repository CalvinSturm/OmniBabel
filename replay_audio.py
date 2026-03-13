import argparse
import json
import queue
import time
from pathlib import Path

import numpy as np
import soundfile as sf
from scipy.signal import resample_poly

from config import (
    DEFAULT_DEVICE,
    DEFAULT_MAX_UTTERANCE_SECONDS,
    DEFAULT_MIN_UTTERANCE_SECONDS,
    DEFAULT_MODEL_SIZE,
    DEFAULT_SOURCE_LANGUAGE,
    DEFAULT_TASK,
    DEFAULT_UTTERANCE_END_SILENCE_SECONDS,
    DEFAULT_VAD_ENERGY_THRESHOLD,
    TARGET_RATE,
)
from src.transcriber import Transcriber


def load_audio(path):
    audio, sample_rate = sf.read(path, always_2d=False)
    audio = np.asarray(audio, dtype=np.float32)

    if audio.ndim > 1:
        audio = audio.mean(axis=1)

    if sample_rate != TARGET_RATE:
        audio = resample_poly(audio, TARGET_RATE, sample_rate).astype(np.float32)

    peak = np.max(np.abs(audio)) if len(audio) else 0.0
    if peak > 1.0:
        audio = audio / peak

    return audio.astype(np.float32), TARGET_RATE


def run_replay(args):
    audio, sample_rate = load_audio(args.input)
    audio_queue = queue.Queue()
    emitted_events = []
    status_events = []

    def on_result(update, status):
        text = update.committed_append.strip()
        if not text:
            return
        detected_language = update.detected_language or status.get("detected_language") or "unknown"
        event = {
            "text": text,
            "detected_language": detected_language,
            "revision_id": update.revision_id,
            "commit_id": update.commit_id,
            "clause_id": update.clause.clause_id if update.clause is not None else None,
            "status": dict(status),
        }
        emitted_events.append(event)
        if not args.quiet:
            print(f"[emit] ({detected_language}) {text}")

    def on_status(status):
        event = {
            "runtime_state": status.get("runtime_state", "unknown"),
            "message": status.get("message", ""),
            "status": dict(status),
        }
        status_events.append(event)
        if not args.quiet:
            print(f"[status] {event['runtime_state']}: {event['message']}")

    transcriber = Transcriber(
        audio_queue,
        result_callback=on_result,
        status_callback=on_status,
        initial_model_size=args.model,
        initial_device=args.device,
        initial_source_language=args.source_language,
        initial_task=args.task,
        initial_vad_energy_threshold=args.vad_energy_threshold,
        initial_utterance_end_silence_seconds=args.utterance_end_silence_seconds,
        initial_min_utterance_seconds=args.min_utterance_seconds,
        initial_max_utterance_seconds=args.max_utterance_seconds,
        initial_debug_logging_enabled=args.debug,
    )

    block_samples = max(int(TARGET_RATE * args.block_seconds), 1)
    transcriber.start()
    flush_completed = False
    started_at = time.time()
    try:
        for start in range(0, len(audio), block_samples):
            block = audio[start:start + block_samples]
            audio_queue.put(block)
            if args.realtime:
                time.sleep(len(block) / sample_rate)

        flush_completed = transcriber.flush(timeout=max(args.max_utterance_seconds + 5.0, 5.0))
    finally:
        transcriber.stop()

    runtime_config = transcriber.get_runtime_config()
    final_status = dict(transcriber.status)
    summary = {
        "input": str(args.input),
        "sample_rate": sample_rate,
        "duration_seconds": round(len(audio) / sample_rate, 3),
        "block_seconds": args.block_seconds,
        "realtime": bool(args.realtime),
        "flush_completed": bool(flush_completed),
        "elapsed_seconds": round(time.time() - started_at, 3),
        "emission_count": len(emitted_events),
        "emitted_text": [event["text"] for event in emitted_events],
        "detected_languages": [event["detected_language"] for event in emitted_events],
        "status_sequence": [event["runtime_state"] for event in status_events],
        "runtime_config": runtime_config,
        "final_status": final_status,
    }
    return summary


def main():
    parser = argparse.ArgumentParser(description="Replay a saved audio file through OmniBabel's transcriber.")
    parser.add_argument("input", type=Path, help="Path to an audio file supported by soundfile.")
    parser.add_argument("--model", default=DEFAULT_MODEL_SIZE)
    parser.add_argument("--device", default=DEFAULT_DEVICE)
    parser.add_argument("--source-language", default=DEFAULT_SOURCE_LANGUAGE)
    parser.add_argument("--task", choices=["translate", "transcribe"], default=DEFAULT_TASK)
    parser.add_argument("--vad-energy-threshold", type=float, default=DEFAULT_VAD_ENERGY_THRESHOLD)
    parser.add_argument("--utterance-end-silence-seconds", type=float, default=DEFAULT_UTTERANCE_END_SILENCE_SECONDS)
    parser.add_argument("--min-utterance-seconds", type=float, default=DEFAULT_MIN_UTTERANCE_SECONDS)
    parser.add_argument("--max-utterance-seconds", type=float, default=DEFAULT_MAX_UTTERANCE_SECONDS)
    parser.add_argument("--debug", action="store_true", help="Enable JSONL debug logging during replay.")
    parser.add_argument("--block-seconds", type=float, default=0.25, help="Replay block size in seconds.")
    parser.add_argument("--realtime", action="store_true", help="Sleep between blocks to simulate live input.")
    parser.add_argument("--quiet", action="store_true", help="Suppress per-event console output.")
    parser.add_argument("--summary-json", type=Path, help="Write a machine-readable replay summary JSON file.")
    parser.add_argument("--print-summary", action="store_true", help="Print the replay summary as JSON.")
    args = parser.parse_args()
    summary = run_replay(args)
    if args.summary_json is not None:
        args.summary_json.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    if args.print_summary:
        print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
