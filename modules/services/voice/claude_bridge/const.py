DOMAIN = "claude_bridge"
# the bridge listens on a Unix socket that only root (HA's container) and its own user can open
DEFAULT_URL = "unix:/run/voice-bridge/bridge.sock"
# entries made before the socket; migrated to DEFAULT_URL
OLD_DEFAULT_URL = "http://127.0.0.1:8099"
# a bit longer than the bridge's own timeout, so the bridge answers first
TIMEOUT = 30
SESSION_TTL = 300
# same sentences as the bridge speaks
ERROR_SPEECH = {
    "ru": "Claude сейчас недоступен",
    "en": "Claude is not available right now",
}


def endpoint(url):
    """(Unix socket path or None, base URL for requests)"""
    if url.startswith("unix:"):
        return url[len("unix:"):], "http://localhost"
    return None, url.rstrip("/")


def error_speech(language):
    base = (language or "").split("-")[0].lower()
    return ERROR_SPEECH.get(base, ERROR_SPEECH["en"])
