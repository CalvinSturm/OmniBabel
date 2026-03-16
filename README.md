# OmniBabel

> Local-first real-time desktop translation and transcription for Windows.

OmniBabel captures system audio, runs local speech recognition and translation, and renders the result as a live on-screen subtitle overlay.

It is built for people who want real-time captions and optional voice playback without sending audio to the cloud.

## Why OmniBabel

Most real-time translation tools make you give something up:

- privacy
- control
- low-latency local use

OmniBabel is built to keep those tradeoffs tighter.

With OmniBabel, you get:

- local speech processing after model download
- live overlay with separate committed text and provisional preview text
- translation mode and transcription mode
- runtime-switchable Whisper model and device selection
- optional local TTS playback
- replay tooling for regression and contract testing

## What it does

- captures desktop audio through WASAPI loopback
- transcribes or translates speech in real time
- displays live captions in a floating overlay
- optionally reads committed output aloud
- keeps model assets and caches local to the repo
- replays saved audio through the live pipeline for testing

## Platform scope

OmniBabel currently targets Windows only.

The live app depends on:

- WASAPI loopback capture
- Tkinter overlay windows
- Windows audio output device selection

`main.py` refuses startup on non-Windows platforms.

## Privacy

OmniBabel is designed for local execution:

- Whisper inference runs on your machine
- TTS runs locally through installed system voices or the local Kokoro runtime
- audio and text are not sent to a hosted API by the application itself

Note: the first run of a model or backend may download assets from upstream package or model sources. After assets are present under `models/`, normal use can stay offline.

## Requirements

- Windows
- Python 3.10, 3.11, or 3.12
- FFmpeg available on `PATH`
- a working loopback-capable output device
- optional: CUDA-capable NVIDIA environment for GPU inference

## Quick start

```bash
git clone https://github.com/CalvinSturm/OmniBabel.git
cd OmniBabel
py -3.12 -m venv venv
.\venv\Scripts\activate
python -m pip install -r requirements.txt
python main.py
````

If `ffmpeg` is missing, install it separately and add it to `PATH`.

On startup, OmniBabel validates:

* Windows platform support
* Python version
* required Python packages
* FFmpeg availability
* loopback audio availability

If a required dependency is missing, the app shows a startup error dialog and exits.

## Overlay controls

* `Left click + drag`: move the overlay
* `Shift + left click + drag`: resize font
* `Mouse wheel`: change background opacity
* `Right click`: open settings
* `Double right click`: exit

## Settings

Right-click the overlay to open the settings window.

### AI model settings

* Whisper model selection
* device selection: `cpu` or `cuda`
* source language: auto-detect or fixed language
* output mode: `translate` or `transcribe`

### Detection tuning

* VAD energy threshold
* end silence seconds
* min utterance seconds
* max utterance seconds
* debug logging toggle

### Appearance

* font size
* max overlay width
* background opacity
* show or hide runtime and TTS status rows
* text color
* background color

### Audio / TTS

* enable or disable read-aloud playback
* select TTS backend
* select voice
* select output device

## TTS backends

Current backend support includes:

* `system`: default backend using `pyttsx3`
* `kokoro`: optional backend using `kokoro.KPipeline` when the `kokoro` package is installed

If `kokoro` is not installed, the backend selector only exposes `system`.

## Local model storage

Downloaded assets are redirected into the repo under `models/`:

* `models/whisper`
* `models/huggingface`
* `models/kokoro`

This keeps model and cache state local to the project instead of using user-global cache directories.

## Replay tooling

OmniBabel includes replay tooling for regression and contract testing against saved audio fixtures.

Useful entry points:

* `replay_audio.py`
* `tests/run_replay_suite.py`

## Documentation

Start with:

* `README.md`
* `tests/run_replay_suite.py`
* `replay_audio.py`

## License

MIT
