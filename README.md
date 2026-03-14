# OmniBabel

**Real-time desktop audio translation and transcription overlay for Windows**

OmniBabel captures system audio through WASAPI loopback, runs local `faster-whisper` inference, and renders the result in a floating on-screen subtitle overlay. It can translate supported source languages into English or transcribe speech in the source language, and it can optionally read committed output aloud through a local TTS backend.

![Python](https://img.shields.io/badge/python-3.10--3.12-3776AB?style=flat&logo=python&logoColor=white)
![Windows](https://img.shields.io/badge/platform-Windows-0078D6?style=flat&logo=windows&logoColor=white)
![Privacy](https://img.shields.io/badge/privacy-local%20processing-2E7D32?style=flat&logo=shield&logoColor=white)
![License](https://img.shields.io/badge/license-MIT-6f42c1?style=flat)

## Features

- Local-only speech processing after model download.
- Live overlay with separate committed text and provisional preview text.
- Translation mode (`source -> English`) and transcription mode (`source -> source`).
- Runtime-switchable Whisper model and processing device (`cpu` or `cuda`).
- Detection tuning controls for VAD threshold, end silence, and utterance length.
- Optional TTS playback with `system` (`pyttsx3`) and `kokoro` backends when available.
- Repo-local model/cache storage under `models/` instead of user-global cache directories.
- Replay tooling for regression runs against saved audio fixtures.

## Current Platform Scope

OmniBabel currently targets **Windows only**.

The live app depends on:

- WASAPI loopback capture via `PyAudioWPatch`
- Tkinter overlay windows
- Windows audio output device selection

`main.py` will refuse startup on non-Windows platforms.

## Privacy

OmniBabel is designed for local execution:

- Whisper inference runs on your machine.
- TTS runs locally through installed system voices or the local Kokoro runtime.
- Audio and text are not sent to a hosted API by the application itself.

Note: the first run of a model/backend may download assets from upstream package/model sources. After assets are present in `models/`, normal use can stay offline.

## Requirements

- Windows
- Python 3.10, 3.11, or 3.12
- FFmpeg available on `PATH`
- A working loopback-capable output device
- Optional: CUDA-capable NVIDIA environment if you want GPU inference

## Installation

```powershell
git clone https://github.com/CalvinSturm/OmniBabel.git
cd OmniBabel
py -3.12 -m venv venv
.\venv\Scripts\activate
python -m pip install -r requirements.txt
```

If `ffmpeg` is missing, install it separately and add it to `PATH`.

## Run

```powershell
python main.py
```

On startup, the app validates:

- Windows platform support
- Python version
- required Python packages
- FFmpeg availability
- loopback audio availability

If a required dependency is missing, the app shows a startup error dialog and exits.

## Overlay Controls

- `Left click + drag`: move the overlay
- `Shift + left click + drag`: resize font
- `Mouse wheel`: change background opacity
- `Right click`: open settings
- `Double right click`: exit

## Settings UI

Right-click the overlay to open the settings window.

### AI model settings

- Whisper model: `tiny`, `base`, `small`, `medium`, `large-v3`, `large-v3-turbo`, `Systran/faster-distil-whisper-large-v3`
- Device: `cpu` or `cuda`
- Source language: auto-detect or a fixed supported language
- Output mode: `translate` or `transcribe`
- Target output is derived automatically:
- `translate` forces English output
- `transcribe` forces source-language output

### Detection tuning

- VAD energy threshold
- End silence seconds
- Min utterance seconds
- Max utterance seconds
- Debug logging toggle

### Appearance

- Font size
- Max overlay width
- Background opacity
- Show/hide runtime and TTS status rows
- Text color
- Background color

### Audio / TTS

- Enable or disable read-aloud playback
- Select TTS backend
- Select voice
- Select output device

## TTS Backends

Current backend support in `src/tts.py`:

- `system`: default backend using `pyttsx3`
- `kokoro`: optional backend using `kokoro.KPipeline` if the `kokoro` package is installed

Kokoro currently defaults to:

- voice: `af_heart`
- sample rate: `24000`
- device: CPU

If `kokoro` is not installed, the backend selector only exposes `system`.

## Local Model Storage

Downloaded assets are redirected into the repo under `models/`:

- `models/whisper`: Faster-Whisper downloads
- `models/huggingface`: Hugging Face cache used by Kokoro-related downloads
- `models/huggingface/hub`
- `models/huggingface/transformers`
- `models/kokoro`: Kokoro cache path

This behavior is configured in [config.py](/C:/Users/Calvin/Software%20Projects/OmniBabel/config.py).

## Streaming Contract

The transcriber emits structured `TranslationUpdate` objects instead of raw text strings.

Core invariants from [src/streaming_contracts.py](/C:/Users/Calvin/Software%20Projects/OmniBabel/src/streaming_contracts.py):

- `provisional_text` may change between revisions
- `committed_text` is append-only
- `committed_append` is the immutable suffix added at the current `commit_id`
- TTS jobs are derived from committed output, not provisional output
- `clause_id` identifies immutable spoken units

The current transcriber behavior in [src/transcriber.py](/C:/Users/Calvin/Software%20Projects/OmniBabel/src/transcriber.py) includes:

- rolling preview confirmation before a preview becomes committed
- a separate preview decode cadence and preview commit cadence
- preference for clause-ending punctuation when advancing committed text
- full utterance flush on finalization
- filtering for common Whisper hallucination / subtitle metadata patterns

## Runtime Status

The overlay can show two optional status rows:

- runtime state, source/target language, detected language, task, model/device, and ambient calibration state
- TTS playback state, source, active job id, and queued job count

If `Show Runtime and TTS Status Rows` is disabled, the overlay becomes a captions-only view.

## Replay Workflow

Use [replay_audio.py](/C:/Users/Calvin/Software%20Projects/OmniBabel/replay_audio.py) to feed saved audio through the live transcriber path:

```powershell
python replay_audio.py .\sample.wav --print-summary
python replay_audio.py .\sample.wav --summary-json .\summary.json --quiet
```

To run the manifest-based replay suite:

```powershell
python tests\run_replay_suite.py --manifest tests\replay_manifest.json --keep-summaries
```

Replay summaries include:

- emitted committed chunks
- final committed transcript
- revision / commit / clause id sequences
- append-only contract validation
- runtime config and final runtime status

## Project Layout

```text
OmniBabel/
├── config.py
├── main.py
├── replay_audio.py
├── requirements.txt
├── src/
│   ├── audio.py
│   ├── dialogs.py
│   ├── gui.py
│   ├── settings.py
│   ├── streaming_contracts.py
│   ├── transcriber.py
│   └── tts.py
└── tests/
    ├── run_replay_suite.py
    └── ...
```

## Troubleshooting

- Model load appears slow on first run: initial downloads can be large, especially `large-v3`.
- CUDA selection falls back to CPU: OmniBabel attempts CPU fallback if GPU model initialization fails.
- No startup audio capture: confirm loopback capture is available for your current Windows output device.
- TTS feedback loop: route playback to headphones or a separate output path.
- Strange subtitle junk such as promo/caption text: the transcriber filters many known patterns, but the phrase list may still need to grow over time.

## License

This project is licensed under the [MIT License](LICENSE.txt).

## Acknowledgements

- [faster-whisper](https://github.com/SYSTRAN/faster-whisper)
- [PyAudioWPatch](https://github.com/s0d3s/PyAudioWPatch)
