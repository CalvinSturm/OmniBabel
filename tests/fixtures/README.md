Place replay fixtures in this directory and reference them from `tests/replay_manifest.json`.

The repository currently includes seven offline-synthesized speech fixtures generated from the local Windows TTS voices:
- `quiet_speech.wav`
- `rapid_pauses.wav`
- `noisy_speech.wav`
- `speech_at_eof.wav`
- `music_under_speech.wav`
- `low_gain_speech.wav`
- `calibration_lead_in.wav`

Suggested starter corpus:
- `quiet_speech.wav`: single speaker, low background noise
- `noisy_speech.wav`: speech over fan noise or room noise
- `music_under_speech.wav`: speech mixed with synthetic background tones
- `rapid_pauses.wav`: short speech bursts separated by brief silences
- `speech_at_eof.wav`: speech ending without trailing silence
- `low_gain_speech.wav`: attenuated speech to exercise confidence filtering
- `calibration_lead_in.wav`: extended quiet lead-in to validate ambient calibration

Run the fixture suite with:

```bash
venv\Scripts\python.exe tests/run_replay_suite.py --manifest tests/replay_manifest.json --keep-summaries
```

Each case writes a structured summary JSON file under `tests/replay-output/` when `--keep-summaries` is set.

Replay summaries now also include operational metrics used for overload validation:
- `max_capture_to_commit_latency_ms`
- `final_capture_to_commit_latency_ms`
- `max_transcriber_queue_depth`
- `max_transcriber_buffer_seconds`
- `load_shedding_event_count`
- `degraded_event_count`

The current fixture manifest treats load shedding and degraded-state events as regressions for the shipped offline fixtures, and it expects transcriber buffering to remain within the configured cap.
