from history import Forwarded, pending_turns


def test_pending_turns_skips_current_and_forwarded():
    e = [("user", "открой левую"), ("assistant", "Открыла"), ("user", "и правую тоже")]
    assert pending_turns(e, 0) == [
        {"role": "user", "text": "открой левую"},
        {"role": "assistant", "text": "Открыла"},
    ]
    assert pending_turns(e, 2) == []


def test_pending_turns_drops_system_and_empty():
    assert pending_turns([("system", "x"), ("assistant", ""), ("assistant", None), ("user", "q")], 0) == []


def test_pending_turns_after_log_shrank():
    # HA starts a new chat log for the same id after it expires
    assert pending_turns([("user", "q")], 5) == []


def test_forwarded_ttl():
    now = [0.0]
    f = Forwarded(300, clock=lambda: now[0])
    f.set("c", 3)
    assert f.get("c") == 3
    now[0] = 400
    assert f.get("c") == 0
    assert f.get(None) == 0
