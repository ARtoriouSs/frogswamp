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


def test_recent_keeps_name_when_none_given():
    recent = app.add_recent([], ID, "derby", NOW)
    recent = app.add_recent(recent, ID, "", NOW + 60)
    assert recent == [{"id": ID, "label": "derby", "at": NOW + 60}]


# ---------- playing on the TV ----------

CFG = {
    "engine_url": "http://127.0.0.1:6878",
    "public_engine_url": BASE,
    "kodi": {"url": "http://192.168.101.119:8080", "username": "kodi", "password_file": "pw"},
    "ha": {"url": "http://127.0.0.1:8123", "token_file": "tok", "remote_entity": "remote.tv"},
}


class Fake:
    """Stands in for app.http; records calls and answers by URL"""

    def __init__(self, engine=True, ha=True, kodi_up=True, kodi_error=None, open_error=None, clock=None):
        self.calls, self.engine, self.ha, self.kodi_up = [], engine, ha, kodi_up
        self.kodi_error, self.open_error, self.clock = kodi_error, open_error, clock

    def __call__(self, method, url, headers=None, body=None, timeout=5):
        self.calls.append((method, url, body))
        if ":6878" in url:
            if not self.engine:
                raise OSError("connection refused")
            return {"result": {"version": "3.2"}}
        if ":8123" in url:
            if not self.ha:
                raise OSError("connection refused")
            return []
        if self.kodi_error:
            raise self.kodi_error
        if not self.kodi_up:
            if self.clock:
                self.clock[0] += timeout  # an asleep TV doesn't answer: each try costs its timeout
            raise OSError("no route to host")
        if body["method"] == "Player.Open" and self.open_error:
            raise self.open_error
        return {"result": "OK"}

    def kodi(self, method):
        return [b for _, u, b in self.calls if u.endswith("/jsonrpc") and b["method"] == method]


def run_play(fake, cfg=CFG):
    from unittest import mock
    sleeps, clock = [], fake.clock or [0.0]

    def sleep(s):
        sleeps.append(s)
        clock[0] += s
    with mock.patch.object(app, "http", fake), mock.patch.object(app, "read", return_value="x"):
        result = app.play(cfg, ID, sleep=sleep, clock=lambda: clock[0])
    return result, sleeps


def test_play_happy_path():
    fake = Fake()
    result, _ = run_play(fake)
    assert result["ok"] is True
    wake = [b for _, u, b in fake.calls if u.endswith("/api/services/remote/turn_on")]
    assert wake == [{"entity_id": "remote.tv"}]  # only power on: the TV rejects "open app" commands
    assert fake.kodi("Player.Open")[0]["params"] == {"item": {"file": f"{BASE}/ace/getstream?id={ID}"}}
    assert result["urls"]["hls"] == f"{BASE}/ace/manifest.m3u8?id={ID}"


def test_play_engine_down():
    fake = Fake(engine=False)
    result, _ = run_play(fake)
    assert result["ok"] is False and "Ace Stream engine isn't running" in result["message"]
    assert not any(u.endswith("/jsonrpc") for _, u, _ in fake.calls)


def test_play_kodi_never_answers():
    fake = Fake(kodi_up=False, clock=[0.0])
    result, _ = run_play(fake)
    assert result["ok"] is False and "Kodi didn't answer" in result["message"]
    assert fake.clock[0] <= 42, fake.clock[0]  # 40 s in total, also when every try times out
    assert fake.kodi("Player.Open") == []


def test_play_wrong_kodi_password():
    import urllib.error
    fake = Fake(kodi_error=urllib.error.HTTPError("http://tv/jsonrpc", 401, "Unauthorized", {}, None), clock=[0.0])
    result, sleeps = run_play(fake)
    assert result["ok"] is False and "password" in result["message"]
    assert sleeps == []  # told right away, no 40 s wait


def test_play_slow_open_counts_as_started():
    # Kodi holds its answer while it opens the stream; the match then starts anyway
    result, _ = run_play(Fake(open_error=TimeoutError("timed out")))
    assert result["ok"] is True and "opening the stream" in result["message"]


def test_play_open_waits_long_enough():
    fake = Fake()
    timeouts = []
    real = fake.__call__

    def spy(method, url, headers=None, body=None, timeout=5):
        if body and body.get("method") == "Player.Open":
            timeouts.append(timeout)
        return real(method, url, headers, body, timeout)
    from unittest import mock
    with mock.patch.object(app, "http", spy), mock.patch.object(app, "read", return_value="x"):
        app.play(CFG, ID, sleep=lambda s: None, clock=lambda: 0.0)
    assert timeouts and timeouts[0] >= 60


def test_play_wake_fails_kodi_already_open():
    fake = Fake(ha=False)
    result, _ = run_play(fake)
    assert result["ok"] is True and "couldn't be woken" in result["message"]
    assert len(fake.kodi("Player.Open")) == 1


def test_play_without_remote_entity():
    fake = Fake()
    cfg = {**CFG, "ha": {**CFG["ha"], "remote_entity": None}}
    result, _ = run_play(fake, cfg)
    assert result["ok"] is True
    assert not any(":8123" in u for _, u, _ in fake.calls)


# ---------- Chromecast through Home Assistant ----------

def run_cast(fake, cfg=CFG):
    from unittest import mock
    with mock.patch.object(app, "http", fake), mock.patch.object(app, "read", return_value="x"):
        return app.chromecast(cfg, ID)


CAST_CFG = {**CFG, "ha": {**CFG["ha"], "cast_entity": "media_player.tv_cast"}}


def test_chromecast_happy_path():
    fake = Fake()
    result = run_cast(fake, CAST_CFG)
    assert result["ok"] is True
    calls = [b for _, u, b in fake.calls if u.endswith("/api/services/media_player/play_media")]
    assert calls == [{"entity_id": "media_player.tv_cast", "media_content_type": "application/vnd.apple.mpegurl",
                      "media_content_id": f"{BASE}/ace/manifest.m3u8?id={ID}"}]
    assert not any(u.endswith("/jsonrpc") for _, u, _ in fake.calls)  # no Kodi involved


def test_chromecast_engine_down():
    fake = Fake(engine=False)
    result = run_cast(fake, CAST_CFG)
    assert result["ok"] is False and "Ace Stream engine isn't running" in result["message"]
    assert not any(":8123" in u for _, u, _ in fake.calls)


def test_chromecast_ha_fails():
    result = run_cast(Fake(ha=False), CAST_CFG)
    assert result["ok"] is False and "Home Assistant" in result["message"]


def test_chromecast_without_entity():
    result = run_cast(Fake(), CFG)
    assert result["ok"] is False and "No Chromecast" in result["message"]


def test_remember_is_safe_with_parallel_requests(tmp_path):
    # two phones, or a double tap: the recent file must stay whole
    import threading
    path = str(tmp_path / "recent.json")
    errors = []

    def worker(i):
        try:
            for j in range(20):
                app.remember(path, f"{i * 100 + j:040x}", "", NOW + j)
        except Exception as e:
            errors.append(e)
    threads = [threading.Thread(target=worker, args=(i,)) for i in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert errors == []
    assert len(app.load_recent(path)) == 8
