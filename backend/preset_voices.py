"""Mistral's documented Voxtral voices, cross-checked with OpenRouter.

Source: https://docs.mistral.ai/studio/audio/text-to-speech/voices
Only genders verified in SUMX's requested catalog are labeled; names aren't gender evidence.
"""

from .speech_contract import PREVIEW_TEXT

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

for _voice, _info in PRESETS.items():
    _info["group"] = "American English" if _voice.startswith("en_") else "British English"
    _info["gender"] = "female" if "jane" in _voice else "male"

# One documented speaker per language, plus both verified counterparts where available.
# Korean has two documented speakers but no published gender, so expose both without guessing.
for _id, _name, _group, _language, _gender in (
    ("voice_485", "Rob", "American English", "en", "male"),
    ("voice_478", "Sheila", "American English", "en", "female"),
    ("voice_1788", "David", "British English", "en", "male"),
    ("voice_1787", "Mary", "British English", "en", "female"),
    ("voice_3153", "Himanshu", "Hindi", "hi", "male"),
    ("voice_3139", "Ananya", "Hindi", "hi", "female"),
    ("voice_592", "Marie", "French", "fr", "female"),
    ("voice_481", "Heidi", "German", "de", "female"),
    ("voice_483", "Elena", "Spanish", "es", "female"),
    ("voice_482", "Lorenzo", "Italian", "it", "male"),
    ("voice_480", "Gabriela", "Portuguese", "pt", "female"),
    ("voice_3133", "Haruto", "Japanese", "ja", "male"),
    ("voice_3142", "Mei", "Chinese", "zh", "female"),
    ("voice_479", "Maryam", "Arabic", "ar", None),
    ("voice_3143", "Jordi", "Catalan", "ca", None),
    ("voice_3146", "Olivia", "Hungarian", "hu", None),
    ("voice_3136", "Sung", "Korean", "ko", None),
    ("voice_3135", "Jin", "Korean", "ko", None),
    ("voice_3145", "Lotte", "Dutch", "nl", None),
    ("voice_3134", "Lars", "Norwegian", "no", None),
    ("voice_3154", "Leon", "Polish", "pl", None),
    ("voice_3155", "Irina", "Romanian", "ro", None),
    ("voice_3132", "Tatiana", "Russian", "ru", None),
    ("voice_3138", "Oran", "Turkish", "tr", None),
    ("voice_3140", "Taras", "Ukrainian", "uk", None),
    ("voice_3144", "Huong", "Vietnamese", "vi", None),
):
    PRESETS[_id] = {
        "name": _name,
        "description": _group + (f" {_gender}" if _gender else ""),
        "group": _group,
        "language": _language,
        "gender": _gender,
    }

# English stays byte-for-byte identical to the existing v1 preview sentence.
PREVIEW_TEXTS = {
    "en": PREVIEW_TEXT,
    "hi": "नमस्ते। यह मेरी नई आवाज़ है। आपकी बैठक के नोट्स इस आवाज़ में साफ़ सुनाई देंगे।",
    "fr": "Bonjour. Voici ma nouvelle voix. Je peux lire clairement vos notes de réunion.",
    "de": "Hallo. Das ist meine neue Stimme. Ich kann Ihre Besprechungsnotizen klar vorlesen.",
    "es": "Hola. Esta es mi nueva voz. Puedo leer las notas de su reunión con claridad.",
    "it": "Ciao. Questa è la mia nuova voce. Posso leggere chiaramente gli appunti della riunione.",
    "pt": "Olá. Esta é a minha nova voz. Posso ler as suas notas de reunião com clareza.",
    "ja": "こんにちは。これが私の新しい声です。会議のメモをはっきり読み上げます。",
    "zh": "你好。这是我的新声音。我可以清晰地朗读你的会议笔记。",
    "ar": "مرحباً. هذا صوتي الجديد. يمكنني قراءة ملاحظات اجتماعك بوضوح.",
    "ca": "Hola. Aquesta és la meva nova veu. Puc llegir les notes de la reunió amb claredat.",
    "hu": "Helló. Ez az új hangom. Érthetően fel tudom olvasni a megbeszélés jegyzeteit.",
    "ko": "안녕하세요. 이것은 제 새로운 목소리입니다. 회의 내용을 또렷하게 읽어 드릴 수 있습니다.",
    "nl": "Hallo. Dit is mijn nieuwe stem. Ik kan uw vergadernotities duidelijk voorlezen.",
    "no": "Hei. Dette er min nye stemme. Jeg kan lese møtenotatene dine tydelig.",
    "pl": "Dzień dobry. To mój nowy głos. Mogę wyraźnie przeczytać notatki ze spotkania.",
    "ro": "Bună. Aceasta este noua mea voce. Pot citi clar notițele de la ședință.",
    "ru": "Здравствуйте. Это мой новый голос. Я могу чётко прочитать ваши заметки со встречи.",
    "tr": "Merhaba. Bu benim yeni sesim. Toplantı notlarınızı anlaşılır bir şekilde okuyabilirim.",
    "uk": "Вітаю. Це мій новий голос. Я можу чітко прочитати ваші нотатки із зустрічі.",
    "vi": "Xin chào. Đây là giọng nói mới của tôi. Tôi có thể đọc rõ ràng ghi chú cuộc họp của bạn.",
}


def preview_text(voice):
    return PREVIEW_TEXTS[PRESETS[voice]["language"]]
