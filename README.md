# 🌐 OmniBabel

**Real-Time Universal Audio Translator & Subtitler**

OmniBabel is a local AI tool that captures live audio from your computer (movies, streams, Zoom meetings), translates it to English instantly, and displays it as a customizable, transparent subtitle overlay on your screen.

![Python](https://img.shields.io/badge/python-3.8%2B-3776AB?style=flat&logo=python&logoColor=white)
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

1.  **Python 3.8+** installed on your system.
2.  **FFmpeg** (Required for audio processing):
    *   *Windows:* Download from [gyan.dev](https://www.gyan.dev/tt/ffmpeg/git/essentials/), extract, and add the `bin` folder to your System PATH.
    *   *Mac:* `brew install ffmpeg`
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

## ⚙️ Configuration & Settings

Right-click the overlay to access the settings menu:

### AI Model Settings
*   **Model Size:** Choose between `small` (fastest), `medium`, `large-v3` (most accurate), or `distil-large-v3` (balanced).
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
    ├── gui.py             # Tkinter overlay & Settings window
    ├── transcriber.py     # Whisper AI logic & Hallucination filter
    └── tts.py             # Text-to-Speech engine
```

## 🐛 Troubleshooting

*   **"Model not loading":** The first time you select a new model (e.g., `large-v3`), it downloads ~3GB of data. The app may freeze momentarily. Check your console/terminal for download progress.
*   **"Hallucinations" (Thank you for watching):** The app includes a filter for common Whisper hallucinations. If one sneaks through, the code in `src/transcriber.py` can be updated to add new banned phrases.
*   **Audio Lag:** Set the model to `small` or `distil-large-v3`. If using CPU, stick to `small` or `base`.

## 📜 License

This project is open-source and available under the [MIT License](LICENSE).

## 🙏 Acknowledgements

*   [Faster-Whisper](https://github.com/SYSTRAN/faster-whisper) for the incredible inference speed.
*   [PyAudioWPatch](https://github.com/s0d3s/PyAudioWPatch) for enabling system audio loopback on Windows.
