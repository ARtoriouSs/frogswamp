DOMAIN = "claude_bridge"
DEFAULT_URL = "http://127.0.0.1:8099"
# a bit longer than the bridge's own timeout, so the bridge answers first
TIMEOUT = 30
SESSION_TTL = 300
# same sentences as the bridge speaks
ERROR_SPEECH = {
    "ru": "Claude сейчас недоступен",
    "en": "Claude is not available right now",
}


def error_speech(language):
    base = (language or "").split("-")[0].lower()
    return ERROR_SPEECH.get(base, ERROR_SPEECH["en"])
