import app

ID = "dd1e67078381739d14beca697356ab76d49d1a2b"
BASE = "http://192.168.101.10:6878"
NOW = 1_000_000_000.0


# ---------- reading the ID ----------

def test_parse_id_bare():
    assert app.parse_id(ID) == ID


def test_parse_id_acestream_link():
    assert app.parse_id("acestream://" + ID.upper() + "\n") == ID


def test_parse_id_url():
    assert app.parse_id(f"http://127.0.0.1:6878/ace/getstream?id={ID}&x=1") == ID


def test_parse_id_rejects():
    for text in ("", "hello", "dd1e6707", "g" + ID[1:], ID + "a"):
        assert app.parse_id(text) is None, text


# ---------- URLs ----------

def test_stream_urls():
    assert app.stream_urls(ID, BASE) == {
        "tv": f"{BASE}/ace/getstream?id={ID}",
        "hls": f"{BASE}/ace/manifest.m3u8?id={ID}",
    }


# ---------- recent list ----------

def test_recent_dedupes_and_limits():
    recent = []
    for i in range(8):
        recent = app.add_recent(recent, f"{i:040x}", f"match {i}", NOW + i)
    recent = app.add_recent(recent, f"{3:040x}", "again", NOW + 10)
    assert len(recent) == 8 and recent[0] == {"id": f"{3:040x}", "label": "again", "at": NOW + 10}
    assert [r["id"] for r in recent].count(f"{3:040x}") == 1
    recent = app.add_recent(recent, ID, "", NOW + 11)
    assert len(recent) == 8 and recent[0]["id"] == ID
    assert f"{0:040x}" not in [r["id"] for r in recent]  # the oldest dropped
