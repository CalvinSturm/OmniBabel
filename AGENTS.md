# AGENTS.md

Scope: applies to the entire repository rooted here.

## Purpose

Use this file as a map, not an encyclopedia. Keep it short and point to the real sources of truth in the repo.

Primary references:
- `README.md`: user-facing setup, runtime behavior, and replay workflow
- `HANDOFF_STREAMING_REFACTOR.md`: streaming/TTS architecture notes and follow-up context
- `config.py`: runtime defaults, model cache paths, supported models, language/task lists
- `main.py`: startup checks, dependency expectations, and top-level wiring

## Repo Summary

OmniBabel is a Windows-only desktop app that:
- captures system audio through WASAPI loopback
- runs local `faster-whisper` transcription/translation
- renders a floating Tkinter subtitle overlay
- optionally reads committed output aloud through local TTS backends

Key runtime constraint:
- `main.py` refuses startup on non-Windows platforms

## Source of Truth by Area

- Audio capture: `src/audio.py`
- Overlay UI and settings window: `src/gui.py`
- Settings persistence: `src/settings.py`
- Streaming contracts: `src/streaming_contracts.py`
- Transcription / streaming commit logic: `src/transcriber.py`
- TTS scheduling and backends: `src/tts.py`
- Replay tooling: `replay_audio.py`, `tests/run_replay_suite.py`, `tests/replay_manifest.json`

When code and docs disagree, trust code first and update docs in the same change.

## Project-Specific Rules

- Preserve the streaming contract:
- `provisional_text` may revise
- `committed_text` must be append-only
- TTS must only consume committed output

- Treat `models/`, `.cache/`, and `settings.json` as local runtime artifacts, not source files.
- Keep repo-local model cache behavior intact unless the task explicitly changes it.
- Kokoro support is optional. Do not assume the `kokoro` package is installed unless you verify it.
- Prefer updating existing docs over adding new ad hoc notes when behavior changes.

## Working Norms

- Keep changes focused. Avoid unrelated refactors.
- If you change behavior, update affected docs and tests in the same task when practical.
- Do not hardcode environment-specific assumptions into docs or handoff files.
- Prefer small, verifiable changes over speculative architectural rewrites.

## Common Commands

Setup on Windows:

```powershell
py -3.12 -m venv venv
.\venv\Scripts\activate
python -m pip install -r requirements.txt
```

Run the app:

```powershell
python main.py
```

Run targeted tests:

```powershell
python -m unittest tests.test_streaming_contracts tests.test_phase1_transcriber tests.test_replay_tooling tests.test_tts_scheduler tests.test_gui_contracts tests.test_config_paths
```

Run replay suite:

```powershell
venv\Scripts\python.exe tests\run_replay_suite.py --manifest tests\replay_manifest.json --keep-summaries
```

Basic compile check:

```powershell
python -m py_compile config.py src\audio.py src\gui.py src\settings.py src\streaming_contracts.py src\transcriber.py src\tts.py main.py replay_audio.py
```

## Docs Maintenance

Keep these files aligned with the codebase when behavior changes:
- `README.md`
- `HANDOFF_STREAMING_REFACTOR.md`
- `tests/fixtures/README.md`

Do not treat commit hashes, local cache state, or installed optional packages as durable documentation.
