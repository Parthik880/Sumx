"""Curated Voxtral presets. Provider voice IDs come from OpenRouter's model metadata.

The catalog also lists unnamed voice_NNNN IDs; their language and gender are unpublished, so
only voices whose identity is known appear here.
"""

PRESET_MODEL = "mistralai/voxtral-mini-tts-3-0"
# Bump to publish a new set of preview files after changing the sentence or model.
PREVIEW_VERSION = "v1"
DEFAULT_PRESET = "en_paul_neutral"
PRESETS = {
    "en_paul_neutral": {"name": "Paul", "description": "American English male", "language": "en"},
    "en_paul_confident": {
        "name": "Paul, confident",
        "description": "American English male",
        "language": "en",
    },
    "gb_oliver_neutral": {"name": "Oliver", "description": "British English male", "language": "en"},
    "gb_oliver_confident": {
        "name": "Oliver, confident",
        "description": "British English male",
        "language": "en",
    },
    "gb_jane_neutral": {"name": "Jane", "description": "British English female", "language": "en"},
    "gb_jane_confident": {
        "name": "Jane, confident",
        "description": "British English female",
        "language": "en",
    },
}
