# --- Configuration ---
# Default Defaults
DEFAULT_MODEL_SIZE = "small"
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
BUFFER_THRESHOLD_SECONDS = 2.0 
BUFFER_OVERLAP_SECONDS = 0.75

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
