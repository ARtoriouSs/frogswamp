from const import DEFAULT_URL, endpoint


def test_unix_url():
    assert endpoint(DEFAULT_URL) == ("/run/voice-bridge/bridge.sock", "http://localhost")


def test_http_url():
    assert endpoint("http://127.0.0.1:8099/") == (None, "http://127.0.0.1:8099")
