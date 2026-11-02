# SUMX Modal GPU configuration
# Change this value to switch the GPU used for TTS inference.
# Modal tries a list in order and uses the first type it can provision.

MODAL_GPU = ["A100", "L40S", "L4", "A10"]

# Modal autoscaling
MODAL_MIN_CONTAINERS = 0
MODAL_MAX_CONTAINERS = 1
MODAL_SCALEDOWN_WINDOW = 300

# Function limits
# One invocation now covers a whole narration, not a single chunk.
MODAL_TIMEOUT = 1800
MODAL_STARTUP_TIMEOUT = 1200

# Deployment names
MODAL_APP_NAME = "sumx-tts"
MODAL_VOLUME_NAME = "sumx-tts-models"
MODAL_PROGRESS_DICT = "sumx-tts-progress"

# Published GPU rates in USD per second (modal.com/pricing, November 2026). Used only for
# per-request estimates: they exclude CPU, memory and idle time, and are not invoices.
MODAL_GPU_USD_PER_SECOND = {
    "T4": "0.000164",
    "L4": "0.000222",
    "A10": "0.000306",
    "L40S": "0.000542",
    "A100-40GB": "0.000583",
    "A100": "0.000694",
}
