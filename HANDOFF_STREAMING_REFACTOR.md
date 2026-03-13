# Streaming Refactor Handoff

This document is the handoff state for the current OmniBabel refactor. It is intended to let another agent continue the work with no thread context.

## Goal

Move OmniBabel from:

```text
audio -> utterance decode -> raw text callback -> immediate TTS speak()
```

to:

```text
audio -> streaming/provisional decode -> append-only committed text -> clause chunking
-> queued TTS scheduler -> playback state -> optional Kokoro backend
```

The critical architectural rule is:

- `provisional_text` may revise
- `committed_text` may only append
- TTS must only consume committed text

## Backbone Invariants

These are already encoded into the contracts and tests and must remain true:

- `revision_id` tracks mutable UI state
- `commit_id` tracks append-only committed state
- `clause_id` identifies immutable spoken units
- TTS never consumes provisional text
- committed text only appends

## Current Plan Status

Completed:
1. Define shared contracts in `src/streaming_contracts.py`
2. Add contract-level invariant tests
3. Refactor transcriber to emit `TranslationUpdate`
4. Update GUI to render committed vs provisional text and playback state
5. Refactor TTS into separate synthesis and playback queues
6. Add clause segmentation and committed-text-to-TTS job creation
7. Add integration-style tests for scheduler and GUI wiring
8. Add backend abstraction in TTS with optional Kokoro path
9. Implement first-pass rolling preview decode and stable-prefix commit logic
10. Re-run broader verification and refresh replay/docs for the streaming contract
11. Strengthen streaming agreement logic with rolling confirmation, separate preview commit cadence, and boundary-aware preview commits
12. Finish Kokoro runtime integration and expose backend selection in settings

Remaining:
13. Improve the streaming agreement algorithm further if a fuller local-agreement engine is still desired

## Current Architecture

### Shared contracts

File:
- `src/streaming_contracts.py`

Defines:
- `ClauseInfo`
- `TranslationUpdate`
- `TTSJob`
- `SynthAudioChunk`
- `PlaybackState`
- `InterruptPolicy`
- `TTSJobSource`
- `PlaybackStatus`

These are the cross-boundary types. Do not put implementation logic here.

### Transcriber

File:
- `src/transcriber.py`

Current behavior:
- still uses `faster-whisper`
- does ambient-aware RMS/hysteresis VAD
- emits `TranslationUpdate` instead of raw text
- maintains:
  - `revision_id`
  - `commit_id`
  - `next_clause_id`
  - `committed_text`
  - per-utterance preview state
- performs periodic preview decodes during active speech
- uses a rolling preview confirmation window across recent hypotheses
- separates preview decode cadence from preview commit cadence
- prefers clause-ending boundaries for preview commits when available
- commits stable prefixes incrementally
- flushes final suffix on utterance completion

Important state:
- `current_utterance_committed_prefix`
- `last_preview_hypothesis`
- `preview_hypothesis_history`
- `last_preview_decode_sample`
- `last_preview_commit_sample`
- `current_utterance_started_committing`

Important note:
- This is a stronger pragmatic agreement implementation, but still not a full UFAL-style local agreement engine yet.

### GUI

File:
- `src/gui.py`

Current behavior:
- displays committed text in main label
- displays provisional suffix in a secondary label
- displays runtime status
- displays playback state

Important methods:
- `schedule_translation_update(update)`
- `schedule_runtime_status_update(status)`
- `schedule_playback_state_update(playback_state)`

### TTS

File:
- `src/tts.py`

Current behavior:
- has separate `synthesis_queue` and `playback_queue`
- primary entrypoint for app flow is:
  - `submit_translation_update(update)`
- creates `TTSJob`s internally from committed text only
- enforces append-only commit behavior
- segments committed deltas into clauses
- supports interrupt policies:
  - `QUEUE`
  - `INTERRUPT`
  - `FLUSH_AND_INTERRUPT`
- emits `PlaybackState` changes through callback

Backends:
- `Pyttsx3Backend` is the active default
- `KokoroBackend` is wired through `kokoro.KPipeline`

Important methods:
- `submit_translation_update(update)`
- `submit_job(job)`
- `flush_queues(cancel_current=False)`
- `cancel_current()`
- `set_backend(name)`
- `get_available_backends()`

Important note:
- `main.py` should not create `TTSJob`s directly anymore.
- backend selection is now persisted via settings and exposed in the GUI TTS controls

### Main wiring

File:
- `main.py`

Current behavior:
- consumes `TranslationUpdate` from transcriber
- forwards update to GUI
- forwards update to TTS scheduler
- playback state comes from TTS callback, not hand-built UI logic

## Files Changed in This Refactor

Core:
- `src/streaming_contracts.py`
- `src/transcriber.py`
- `src/tts.py`
- `src/gui.py`
- `main.py`

Support:
- `replay_audio.py`
- `tests/run_replay_suite.py`
- `tests/replay_manifest.json`
- `tests/fixtures/*`

Tests:
- `tests/test_streaming_contracts.py`
- `tests/test_phase1_transcriber.py`
- `tests/test_replay_tooling.py`
- `tests/test_tts_scheduler.py`
- `tests/test_gui_contracts.py`

## What Is Verified

Current test suite passes:

```bash
python -m unittest tests.test_streaming_contracts tests.test_phase1_transcriber tests.test_replay_tooling tests.test_tts_scheduler tests.test_gui_contracts
```

Compile checks used during refactor:

```bash
python -m py_compile src\streaming_contracts.py src\transcriber.py src\tts.py src\gui.py main.py replay_audio.py
```

Replay suite exists and passes independently under the project venv:

```bash
venv\Scripts\python.exe tests\run_replay_suite.py --manifest tests\replay_manifest.json --keep-summaries
```

Replay verification should now be interpreted against the streaming contract:
- `emitted_text` is the sequence of committed appends, not necessarily one utterance-final string
- `final_committed_text` is the append-only aggregate transcript for the replay clip
- replay expectations should prefer append-only and monotonic ID checks over exact utterance counts when preview commits are expected

## Remaining Recommended Work

### 1. Improve streaming agreement further if needed

Current streaming commit logic now includes:
- preview decode on active speech every hop
- rolling confirmation across recent preview hypotheses
- separate preview commit hop gating
- boundary-aware preview commits
- full final-hypothesis flush on utterance end

Likely further improvements:
- explicit confidence weighting across hypotheses instead of plain word-prefix overlap
- clause ownership unification between transcriber and TTS
- richer punctuation-aware chunking beyond simple boundary preference
- adaptive confirmation depth based on pause structure or confidence

This work belongs primarily in:
- `src/transcriber.py`

## Known Caveats

### 1. Current agreement logic is still heuristic

It is materially stronger than the original consecutive-prefix method, but it still relies on pragmatic word-prefix agreement and boundary trimming rather than a full local-agreement decoder.

### 2. `clause_id` ownership is currently split conceptually

The contracts expect immutable spoken units.
Right now:
- transcriber can mark a final clause in `TranslationUpdate.clause`
- TTS also assigns job/clause IDs to internal spoken jobs

If continuing the refactor, decide whether:
- transcriber owns immutable clause IDs, or
- TTS owns them after segmentation

This should be unified later.

### 3. Kokoro runtime is dependency-backed

The environment now has the `kokoro` package installed in the project venv.
The backend uses `kokoro.KPipeline` and a default `af_heart` voice.
First use may still download Hugging Face model or voice assets.

### 4. Replay expectations now follow the streaming contract

The replay harness no longer assumes one final emission per utterance.
It now validates:
- append-only committed growth
- monotonic `revision_id`
- monotonic `commit_id`
- monotonic `clause_id` when present
- aggregate `final_committed_text`

## Recommended Next Task Order

If another agent picks this up, the safest order is:

1. Finish Kokoro integration if neural TTS is the next priority
2. If transcript stability still needs work, continue transcriber-side agreement improvements
3. If clause ownership becomes a maintenance issue, unify `clause_id` responsibility between transcriber and TTS

My recommendation:

1. do a short real-app Kokoro smoke test through the GUI
2. then revisit agreement only if replay/manual UX still shows instability

## Commands Used Frequently

Run tests:

```bash
python -m unittest tests.test_streaming_contracts tests.test_phase1_transcriber tests.test_replay_tooling tests.test_tts_scheduler tests.test_gui_contracts
```

Run replay suite:

```bash
venv\Scripts\python.exe tests\run_replay_suite.py --manifest tests\replay_manifest.json --keep-summaries
```

Run app with correct venv:

```bash
run.bat
```

## Current Commit

The current refactor work was committed as:

```text
2edd59d Refactor streaming translation and queued TTS pipeline
```
