# Streaming Refactor Handoff

This document is the handoff state for the current OmniBabel streaming/TTS refactor. It is intended to let another agent continue the work with no thread context.

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

Open follow-up areas:
13. Improve the streaming agreement algorithm further if a fuller local-agreement engine is still desired
14. Continue UX polish and documentation as needed

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
- can display runtime status
- can display playback state
- supports a captions-only mode that hides the runtime/playback rows

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
- still exposes `speak(text)` as a manual/fallback entrypoint
- supports interrupt policies:
  - `QUEUE`
  - `INTERRUPT`
  - `FLUSH_AND_INTERRUPT`
- emits `PlaybackState` changes through callback

Backends:
- `Pyttsx3Backend` is the active default
- `KokoroBackend` is wired through `kokoro.KPipeline`
- repo-local model and HF assets now resolve under the project root `models/` tree

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
- settings now persist whether the overlay shows the runtime/playback rows

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

Current test coverage includes:

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

Additional current verification performed during follow-up work:

```bash
python -m unittest tests.test_phase1_transcriber tests.test_tts_scheduler tests.test_gui_contracts
python -m py_compile config.py src\transcriber.py src\tts.py src\gui.py src\settings.py main.py
python -m unittest tests.test_config_paths
```

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

## Execution Checklist

Use this as the recommended next execution sequence for moving the repo from a solid prototype toward production-ready behavior.

### Phase 1. Bound queues and define overload behavior

Goal:
- prevent unbounded latency drift and memory growth under sustained load

Target stage policy:

| Stage | Backlog priority | Overload rule |
| --- | --- | --- |
| Capture | freshness over completeness | drop oldest audio blocks once buffered audio age exceeds the configured cap |
| Transcription input | freshness over completeness | discard stale buffered audio when backlog exceeds the configured latency budget |
| TTS synthesis | committed meaning over full history | drop or merge superseded stale clauses before synthesis when spoken output is falling behind |
| TTS playback | bounded spoken lag | never allow spoken playback to remain more than the configured max lag behind committed text |

Checklist:
- [ ] replace unbounded queues with bounded queues where live backlog can accumulate:
  - `main.py` audio queue
  - `src/audio.py` raw capture queue
  - `src/tts.py` synthesis queue
  - `src/tts.py` playback queue
- [ ] add queue sizing constants to `config.py`
- [ ] define overload policy per stage:
  - capture: drop oldest blocks or collapse stale backlog
  - transcription: cap buffered audio age and discard stale data when necessary
  - TTS: flush, merge, or drop stale queued work instead of allowing indefinite lag
- [ ] emit explicit degraded/overloaded runtime state instead of silently drifting

Success criteria:
- [ ] overload does not cause unbounded queue growth
- [ ] latency stabilizes or sheds load instead of growing forever

### Phase 2. Add queue-depth and latency telemetry

Goal:
- make real-time health observable during manual runs and replay runs

Telemetry ownership:
- runtime telemetry should have a single ownership point instead of being spread ad hoc across callbacks
- preferred shape:
  - transcriber-owned runtime telemetry folded into runtime status payloads for capture/transcription-side measurements
  - TTS-owned playback telemetry folded into playback state or a closely paired payload for synthesis/playback-side measurements
- if this becomes too crowded for the existing payloads, introduce an explicit shared telemetry dataclass/contract rather than inventing unrelated dictionaries in multiple modules

Checklist:
- [ ] add telemetry for:
  - capture queue depth
  - transcription queue depth
  - TTS synthesis queue depth
  - TTS playback queue depth
  - dropped block/job counts
  - capture-to-commit latency
  - capture-to-playback latency
- [ ] extend runtime status payloads to include compact telemetry
- [ ] expose concise telemetry in the overlay status rows
- [ ] add richer telemetry to debug logging where appropriate

Success criteria:
- [ ] manual testing can distinguish healthy, degraded, and overloaded states
- [ ] telemetry is available for before/after comparison during tuning

Smoke-test targets to define during implementation:
- [ ] expected healthy queue depths during normal operation
- [ ] acceptable capture-to-commit latency range
- [ ] acceptable capture-to-playback latency range
- [ ] degraded-but-acceptable thresholds before load shedding is triggered

### Phase 3. Expand replay coverage around overload, latency, and filtering

Goal:
- make operational regressions testable instead of anecdotal

Checklist:
- [ ] extend `replay_audio.py` summaries with latency and drop metrics
- [ ] add replay cases for:
  - sustained backlog / overload
  - long continuous speech
  - bilingual or language-switching clips
  - known filtering false positives
  - subtitle/promo metadata junk
- [ ] add targeted unit/integration coverage for queue saturation and TTS shedding behavior
- [ ] update `tests/replay_manifest.json`
- [ ] update `tests/fixtures/README.md`

Success criteria:
- [ ] replay suite can detect latency drift and overload behavior regressions
- [ ] filtering regressions are covered by explicit fixtures or assertions

### Phase 4. Introduce runtime presets

Goal:
- let users choose latency vs quality without hand-tuning every knob

Checklist:
- [ ] add `Live`, `Balanced`, and `Accuracy` presets
- [ ] define preset values for at least:
  - model choice
  - beam size
  - best-of
  - patience
  - preview/commit cadence
  - relevant VAD/end-silence defaults if needed
- [ ] persist preset selection in settings
- [ ] expose preset selection in the settings UI
- [ ] preserve advanced manual controls for override cases

Success criteria:
- [ ] CPU users get a realistic low-latency default path
- [ ] users can switch between responsiveness and quality intentionally

### Phase 5. Split installation profiles

Goal:
- make optional capabilities explicit instead of implied

Checklist:
- [ ] define installation profiles or clearly documented variants for:
  - core runtime
  - CUDA support
  - Kokoro support
  - dev / replay / test tooling
- [ ] document that Torch is optional and only affects CUDA/device behavior
- [ ] document that Kokoro is optional
- [ ] align startup/setup messaging with the supported install variants
- [ ] update `README.md` accordingly

Success criteria:
- [ ] fresh users can choose the correct install path without guesswork
- [ ] optional features are not represented as always present

Preferred future package/profile shape:
- `core`
- `cuda`
- `kokoro`
- `dev/replay/test`

### Phase 6. Tighten public positioning

Goal:
- make the project description match the current implementation

Checklist:
- [ ] update GitHub repo description/about text manually to reflect current scope
- [ ] keep positioning aligned with actual implementation:
  - Windows-only
  - local live captions / translation overlay
  - translation to English
  - transcription in source language
  - optional local spoken playback
- [ ] keep `README.md` and `AGENTS.md` aligned with that positioning

Success criteria:
- [ ] public copy does not imply universal multilingual dubbing or target-language voice output beyond what the code actually supports

## Known Caveats

### 1. Current agreement logic is still heuristic

It is materially stronger than the original consecutive-prefix method, but it still relies on pragmatic word-prefix agreement and boundary trimming rather than a full local-agreement decoder.

### 2. `clause_id` ownership is currently split conceptually

This is the main remaining contract ambiguity and should be treated as an explicit architectural decision pending.

The contracts expect immutable spoken units.
Right now:
- transcriber can mark a final clause in `TranslationUpdate.clause`
- TTS also assigns job/clause IDs to internal spoken jobs

If continuing the refactor, decide whether:
- transcriber owns immutable clause IDs, or
- TTS owns them after segmentation

This should be unified later.

### 3. Kokoro runtime is optional and dependency-backed

The backend uses `kokoro.KPipeline` and a default `af_heart` voice when the `kokoro` package is installed.
The repo supports predownloading runtime assets into the root `models/` tree, but that warmed-cache state is environment-specific and should not be assumed by future agents.

### 4. Repo-local model storage is now explicit

Model assets are no longer just whatever the libraries pick by default.
Current paths are rooted under:

- `models/whisper`
- `models/huggingface`
- `models/kokoro`

### 5. Replay expectations now follow the streaming contract

The replay harness no longer assumes one final emission per utterance.
It now validates:
- append-only committed growth
- monotonic `revision_id`
- monotonic `commit_id`
- monotonic `clause_id` when present
- aggregate `final_committed_text`

## Recommended Next Task Order

If another agent picks this up, the safest order is:

1. Do a short end-to-end manual smoke test against the current repo-local `models/` downloads and the captions-only overlay option
2. If transcript stability still needs work, continue transcriber-side agreement improvements
3. If clause ownership becomes a maintenance issue, unify `clause_id` responsibility between transcriber and TTS
4. If the settings surface keeps growing, break the Tk settings window into smaller helper sections

My recommendation:

1. keep the current architecture and contracts unchanged
2. do targeted manual smoke testing before any additional refactor
3. revisit agreement only if replay/manual UX still shows instability

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

## Current Repo State

Do not rely on this handoff for exact commit IDs or working tree state.
Before continuing work, check the live repository state with:

```bash
git status --short
git log --oneline -5
```

Features that are now part of the current codebase and should be treated as landed include:
- `large-v3-turbo` runtime selection
- captions-only overlay status-row toggle
- repo-root `models/` storage and cache env wiring
- startup validation for platform, dependencies, FFmpeg, and loopback audio
- config-path tests for repo-local model cache directories
