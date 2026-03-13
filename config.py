# --- Configuration ---
# Default Defaults
DEFAULT_MODEL_SIZE = "large-v3"
DEFAULT_DEVICE = "cpu"

def detect_cuda_device():
    """Detect if CUDA is available and return appropriate device."""
    try:
        import torch
        if torch.cuda.is_available():
            print("[CONFIG] CUDA detected - defaulting to GPU acceleration")
            return "cuda"
        else:
            print("[CONFIG] CUDA not available - using CPU")
            return "cpu"
    except ImportError:
        print("[CONFIG] PyTorch not available - using CPU")
        return "cpu"
        
DEFAULT_DEVICE = detect_cuda_device()        
COMPUTE_TYPE = "int8"

# Audio Settings
TARGET_RATE = 16000
CHUNK_SIZE = 1024
INPUT_RESAMPLE_BLOCK_SECONDS = 0.25
RESAMPLE_CONTEXT_SECONDS = 0.05
MIN_UTTERANCE_SECONDS = 0.8
MAX_UTTERANCE_SECONDS = 8.0
UTTERANCE_END_SILENCE_SECONDS = 0.45
MAX_IDLE_BUFFER_SECONDS = 2.0
VAD_FRAME_SECONDS = 0.1
VAD_ENERGY_THRESHOLD = 0.012
SEGMENT_EMIT_GUARD_SECONDS = 0.15
AMBIENT_CALIBRATION_SECONDS = 1.5
VAD_START_THRESHOLD_MULTIPLIER = 1.6
VAD_END_THRESHOLD_MULTIPLIER = 1.15
VAD_START_CONSECUTIVE_FRAMES = 2
VAD_END_CONSECUTIVE_FRAMES = 3

DEFAULT_MIN_UTTERANCE_SECONDS = MIN_UTTERANCE_SECONDS
DEFAULT_MAX_UTTERANCE_SECONDS = MAX_UTTERANCE_SECONDS
DEFAULT_UTTERANCE_END_SILENCE_SECONDS = UTTERANCE_END_SILENCE_SECONDS
DEFAULT_VAD_ENERGY_THRESHOLD = VAD_ENERGY_THRESHOLD

# Accuracy-oriented transcription defaults
DEFAULT_BEAM_SIZE = 8
DEFAULT_BEST_OF = 8
DEFAULT_PATIENCE = 1.5
DEFAULT_REPETITION_PENALTY = 1.05
DEFAULT_NO_SPEECH_THRESHOLD = 0.4
DEFAULT_MIN_SILENCE_DURATION_MS = 350
DEFAULT_MIN_SEGMENT_AVG_LOGPROB = -1.0
DEFAULT_MAX_SEGMENT_NO_SPEECH_PROB = 0.6
DEFAULT_MAX_SEGMENT_COMPRESSION_RATIO = 2.4

# GUI Defaults
DEFAULT_FONT_SIZE = 24
DEFAULT_TEXT_COLOR = "yellow"
DEFAULT_BG_COLOR = "#1e1e1e"
DEFAULT_OPACITY = 0.85

# Options Lists
AVAILABLE_MODELS = [
    "tiny",
    "base",
    "small",
    "medium",
    "large-v3",
    "Systran/faster-distil-whisper-large-v3"
]

AVAILABLE_DEVICES = ["cpu", "cuda"]

LANGUAGE_CHOICES = [
    ("auto", "Auto Detect"),
    ("en", "English"),
    ("es", "Spanish"),
    ("fr", "French"),
    ("de", "German"),
    ("it", "Italian"),
    ("pt", "Portuguese"),
    ("ru", "Russian"),
    ("ja", "Japanese"),
    ("ko", "Korean"),
    ("zh", "Chinese"),
    ("ar", "Arabic"),
    ("hi", "Hindi"),
    ("uk", "Ukrainian"),
]

TASK_CHOICES = [
    ("translate", "Translate to English"),
    ("transcribe", "Transcribe Original Speech"),
]

TARGET_LANGUAGE_CHOICES = [
    ("en", "English"),
    ("source", "Source Language"),
]

DEFAULT_SOURCE_LANGUAGE = "auto"
DEFAULT_TARGET_LANGUAGE = "en"
DEFAULT_TASK = "translate"
DEFAULT_DEBUG_LOGGING = False
