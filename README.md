# 🌐 OmniBabel

**Real-Time Universal Audio Translator & Subtitler**

OmniBabel is a local AI tool that captures live audio from your computer (movies, streams, Zoom meetings), translates it to English instantly, and displays it as a customizable, transparent subtitle overlay on your screen.

![Python](https://img.shields.io/badge/python-3.10--3.12-3776AB?style=flat&logo=python&logoColor=white)
![AI](https://img.shields.io/badge/AI-Faster_Whisper-green?style=flat)
![License](https://img.shields.io/badge/License-MIT-purple?style=flat)
![Privacy](https://img.shields.io/badge/Privacy-100%25_Local-darkgreen?style=flat&logo=lock&logoColor=white)

## ✨ Features

*   **Live Translation:** Captures system audio (Loopback) and translates foreign languages to English in near real-time.
*   **🔒 Local & Secure:** Running entirely on your machine means your meetings, movies, and conversations remain private. No cloud processing, no data harvesting.
*   **Floating Overlay:**
    *   **Transparent Background:** Adjustable opacity (from solid box to floating text).
    *   **Click-through:** Interactions work through the clear parts of the window.
    *   **Draggable & Resizable:** Move it anywhere on your desktop.
*   **Text-to-Speech (TTS):** Reads the translated English captions aloud using installed system voices.
*   **Customization:** Change font size, text color, background color, box width, and AI model size via a Settings GUI.
*   **Audio Routing:** Supports splitting audio output to prevent echo/feedback loops (requires VB-Cable).

## 🔒 Privacy & Security

OmniBabel is designed for users who care about data sovereignty. Unlike browser extensions or cloud-based translation services, **OmniBabel runs completely offline**.

*   **100% Local Processing:** All AI inference (listening, transcribing, and translating) happens locally on your CPU/GPU using `faster-whisper`.
*   **No Data Exfiltration:** Your audio streams and translated subtitles never leave your computer. Nothing is sent to OpenAI, Google, or any third-party server.
*   **No API Keys Required:** You do not need to create an account, pay for credits, or hand over personal data to use the tool.
*   **Works Offline:** Once the models are downloaded, you can disconnect your internet and the tool will continue to function perfectly.

## 🛠️ Prerequisites

1.  **Python 3.10-3.12 on Windows** installed on your system.
2.  **FFmpeg** (Required for audio processing):
    *   *Windows:* Download from [gyan.dev](https://www.gyan.dev/tt/ffmpeg/git/essentials/), extract, and add the `bin` folder to your System PATH.
3.  **(Optional) VB-Cable:** Recommended if you want to use the Text-to-Speech feature to avoid the microphone hearing the computer voice. [Download Here](https://vb-audio.com/Cable/).

## 📦 Installation

1.  **Clone the repository:**
    ```bash
    git clone https://github.com/calvinsturm/OmniBabel.git
    cd OmniBabel
    ```

2.  **Create a Virtual Environment (Recommended):**
    ```bash
    python -m venv venv
    # Windows
    venv\Scripts\activate
    # Mac/Linux
    source venv/bin/activate
    ```

3.  **Install Dependencies:**
    ```bash
    pip install -r requirements.txt
    ```

4.  **Optional CUDA acceleration:**
    Install a CUDA-enabled PyTorch build and any matching NVIDIA runtime packages for your environment if you want GPU inference. The base requirements target CPU mode by default.

## 🚀 Usage

1.  **Run the Application:**
    ```bash
    python main.py
    ```

2.  **Controls:**
    *   **Left Click + Drag:** Move the subtitle window.
    *   **Shift + Left Click + Drag Up/Down:** Increase/Decrease Font Size.
    *   **Mouse Wheel:** Increase/Decrease Background Opacity.
    *   **Right Click:** Open **Settings Menu**.
    *   **Double Right Click:** Exit the application.

## 🔁 Streaming Contract

OmniBabel now uses a contract-based streaming pipeline:

```text
audio -> provisional decode -> append-only committed text -> clause chunking
-> queued TTS synthesis/playback -> playback state updates
```

The runtime invariants are:

* `revision_id` tracks mutable UI state.
* `commit_id` tracks append-only committed state.
* `clause_id` identifies immutable spoken units.
* `provisional_text` may revise between updates.
* `committed_text` may only append.
* TTS only consumes `committed_append` and final committed clauses.

### `TranslationUpdate`

`src/transcriber.py` emits `TranslationUpdate` objects instead of raw text callbacks.

* `provisional_text`: full UI-facing string including any revisable suffix.
* `committed_text`: append-only transcript accumulated so far.
* `committed_append`: the immutable suffix added at the current `commit_id`.
* `clause`: optional final-clause metadata for immutable spoken spans.
* `audio_start_ms` / `audio_end_ms`: source timing for the update window.

### Current Streaming Agreement Behavior

The current transcriber logic is no longer a single previous/current prefix check. It now uses:

* a rolling preview confirmation window before new preview text can become committed,
* separate preview decode cadence and preview commit cadence,
* boundary-aware preview commits that prefer clause-ending punctuation before crossing into the next clause,
* full final-hypothesis commit on utterance completion.

In practice, this means the overlay can still show responsive provisional text, while committed text advances more conservatively and is shaped into more natural immutable chunks before TTS sees it.

### Playback State

`src/tts.py` emits `PlaybackState` updates for the overlay:

* `idle`: no active synthesis or playback work.
* `queued`: at least one job is waiting in synthesis or playback queues.
* `synthesizing`: a queued clause is being rendered by the active backend.
* `playing`: synthesized audio is currently playing.
* `completed`: a job finished playback.
* `cancelled`: playback was interrupted or flushed.
* `error`: synthesis or playback failed.

The overlay shows committed text, provisional suffix, runtime status, and playback status independently.

### TTS Scheduler Semantics

The main app does not create TTS jobs directly. It forwards `TranslationUpdate` objects to `TTSHandle.submit_translation_update(update)`, which:

* ignores provisional-only updates with no committed append,
* enforces append-only committed text,
* segments committed deltas into clauses,
* queues immutable clauses for synthesis,
* keeps synthesis and playback as separate worker queues.

### Backend Support

Current backend status:

* `system`: active default via `pyttsx3`; usable in this environment.
* `kokoro`: runtime synthesis is now wired through `kokoro.KPipeline` with a default `af_heart` voice and 24 kHz mono output.

The settings UI now lets you switch between `system` and `kokoro` backends. Kokoro may download model or voice assets on first use.

## ⚙️ Configuration & Settings

Right-click the overlay to access the settings menu:

### AI Model Settings
*   **Model Size:** `large-v3` is now the default for best translation accuracy. Use `distil-large-v3` or `small` only if you need lower latency.
*   **Device:** Select `cpu` or `cuda` (if you have an NVIDIA GPU).

### Appearance
*   **Font Size & Colors:** Customize the look of the subtitles.
*   **Max Width:** Control how wide the text box is before wrapping to a new line.
*   **Opacity:** 0.0 = Floating text only (ghost mode), 1.0 = Solid background box.

### Audio & TTS
*   **Read Aloud:** Toggles the computer voice reading the translations.
*   **Voice Personality:** Select different system voices (e.g., Microsoft Zira, David).
*   **Output Device:** *Critical for preventing echo.* Select where the TTS audio plays.

## 🎧 preventing Echo (Feedback Loop)

If you enable "Read Translations Aloud," the computer might "hear" its own voice and try to translate it again. To fix this:

1.  **Method 1: Headphones (Easiest)**
    *   Plug in headphones.
    *   In the App Settings -> **Output Device**, select your Headphones.
    *   The AI will listen to the system (speakers), but speak into your ears.

2.  **Method 2: Virtual Cable (For Speakers)**
    *   Install **VB-Cable**.
    *   Set Windows Output to **CABLE Input**.
    *   In Windows Sound Settings -> Recording -> **CABLE Output** -> Properties -> Listen -> Check **"Listen to this device"** and select your real speakers.
    *   In the App Settings, select your **Real Speakers** as the Output Device.

## 📂 Project Structure

```text
live-video-translator/
├── main.py                # Entry point
├── config.py              # Configuration constants
├── requirements.txt       # Python dependencies
└── src/
    ├── audio.py           # System audio loopback recording
    ├── gui.py             # Tkinter overlay, runtime status, playback state
    ├── streaming_contracts.py  # Shared streaming and playback contracts
    ├── transcriber.py     # Whisper streaming decode and commit logic
    └── tts.py             # Queued TTS scheduler and backend abstraction
```

## 🐛 Troubleshooting

*   **"Model not loading":** The first time you select a new model (e.g., `large-v3`), it downloads ~3GB of data. The app may freeze momentarily. Check your console/terminal for download progress.
*   **"Hallucinations" (Thank you for watching):** The app includes a filter for common Whisper hallucinations. If one sneaks through, the code in `src/transcriber.py` can be updated to add new banned phrases.
*   **Audio Lag:** The default settings now favor accuracy over speed. If latency is too high, switch to `distil-large-v3` or `small`.

## 🧪 Replay Regression Workflow

Use `replay_audio.py` to run saved audio through the live transcriber path and export a structured summary:

```bash
python replay_audio.py .\sample.wav --print-summary
python replay_audio.py .\sample.wav --summary-json .\summary.json --quiet
```

To build a small audio regression corpus, add fixture clips under `tests/fixtures/`, update `tests/replay_manifest.json`, and run:

```bash
python tests/run_replay_suite.py --manifest tests/replay_manifest.json --keep-summaries
```

Replay summaries now reflect the streaming contract directly:

* `emitted_text` contains each immutable `committed_append` chunk as it was emitted.
* `final_committed_text` is the append-only aggregate committed transcript for the clip.
* `revision_ids`, `commit_ids`, and `clause_ids` let the suite verify monotonic contract behavior.
* `append_only_valid` flags whether the replay maintained append-only committed growth.

This is the intended verification path for tuning VAD thresholds, end-of-file flush behavior, preview confirmation behavior, and streaming commit chunking against real clips.

## 📜 License

This project is open-source and available under the [MIT License](LICENSE).

## 🙏 Acknowledgements

*   [Faster-Whisper](https://github.com/SYSTRAN/faster-whisper) for the incredible inference speed.
*   [PyAudioWPatch](https://github.com/s0d3s/PyAudioWPatch) for enabling system audio loopback on Windows.
