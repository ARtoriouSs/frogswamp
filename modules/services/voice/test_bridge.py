import json
import logging
import subprocess
import threading
import time
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

import pytest

import bridge


class FakeRun:
    """Stands in for subprocess.run and records each call"""

    def __init__(self, outputs):
        self.outputs = list(outputs)
        self.calls = []

    def __call__(self, args, **kw):
        self.calls.append({"args": args, **kw})
        out = self.outputs.pop(0)
        if isinstance(out, Exception):
            raise out
        return subprocess.CompletedProcess(args, 0, stdout=json.dumps(out), stderr="")


class FakeBackend:
    def __init__(self, fail_on_resume=False, delay=0.0, text="Hello.", fail_with=None):
        self.fail_on_resume = fail_on_resume
        self.fail_with = fail_with
        self.delay = delay
        self.text = text
        self.calls = []
        self.active = 0
        self.max_active = 0
        self.lock = threading.Lock()

    def ask(self, prompt, system_prompt, session_id, timeout):
        with self.lock:
            self.calls.append((prompt, session_id))
            self.active += 1
            self.max_active = max(self.max_active, self.active)
        try:
            time.sleep(self.delay)
            if self.fail_on_resume and session_id:
                raise bridge.SessionLost("No conversation found")
            if self.fail_with:
                raise self.fail_with
            return bridge.Reply(self.text, "sess-%d" % len(self.calls))
        finally:
            with self.lock:
                self.active -= 1


def ok(result, session_id="sess-1"):
    return {"type": "result", "is_error": False, "result": result, "session_id": session_id}


@pytest.fixture
def claude_cfg(tmp_path):
    creds = tmp_path / "creds"
    creds.mkdir()
    (creds / "claude_token").write_text("claude-secret\n")
    (creds / "ha_token").write_text("ha-secret\n")
    return {
        "claude": "/bin/claude",
        "model": "haiku",
        "mcp_config": "/etc/mcp.json",
        "settings": "/etc/settings.json",
        "workdir": str(tmp_path / "work"),
        "config_dir": str(tmp_path / "config"),
        "home": str(tmp_path),
        "credentials_dir": str(creds),
    }


def request(text="hi", language="en", conversation_id="c1", earlier=()):
    return {"text": text, "language": language, "conversation_id": conversation_id, "earlier": list(earlier)}


# ---------- text helpers ----------

def test_language_normalized():
    assert bridge.normalize_language("ru-RU") == "ru"
    assert bridge.normalize_language("en") == "en"
    assert bridge.normalize_language("pl") == "en"
    assert bridge.normalize_language(None) == "en"
    assert bridge.normalize_language("") == "en"
    assert bridge.error_speech("pl") == "Claude is not available right now"
    assert bridge.error_speech("ru") == "Claude сейчас недоступен"


def test_prompt_has_language_and_earlier_turns():
    p = bridge.build_prompt("и правую тоже", "ru", [
        {"role": "user", "text": "открой левую штору"},
        {"role": "assistant", "text": "Открыла"},
    ])
    assert "[language: ru]" in p
    assert "User: открой левую штору" in p
    assert "Home: Открыла" in p
    assert p.rstrip().endswith("и правую тоже")


def test_prompt_without_earlier_turns():
    assert bridge.build_prompt("hi", "en", []) == "[language: en]\nhi"


def test_speech_sanitized():
    s = bridge.sanitize_speech("**Готово.** См. https://x.y/z\n- пункт " + "Очень. " * 200)
    assert "*" not in s and "http" not in s and "\n" not in s and " - " not in s
    assert len(s) <= 600
    assert s.endswith(".")


def test_speech_drops_sources_and_links():
    s = bridge.sanitize_speech("Две свёклы, см. [рецепт](https://a.b/c).\n\nSources: [Борщ](https://x.y) [Поварёнок](")
    assert s == "Две свёклы, см. рецепт."
    assert bridge.sanitize_speech("Ок.\nИсточники:\n- [a](https://a.b)") == "Ок."


def test_speech_keeps_sources_in_a_sentence():
    assert bridge.sanitize_speech("Energy sources: coal and gas.") == "Energy sources: coal and gas."


def test_empty_speech_is_an_error():
    assert converse(FakeBackend(text="https://a.b/c"), request(language="ru")) == {"speech": "Claude сейчас недоступен", "ok": False}


def test_speech_short_text_kept():
    assert bridge.sanitize_speech("  Открыла левую штору.  ") == "Открыла левую штору."


# ---------- sessions ----------

def test_session_store_ttl():
    now = [0.0]
    st = bridge.SessionStore(300, clock=lambda: now[0])
    st.put("c1", "s1")
    now[0] = 299
    assert st.get("c1") == "s1"
    now[0] = 600
    assert st.get("c1") is None
    assert st.get(None) is None


def test_busy_lock_survives_cleanup():
    now = [0.0]
    st = bridge.SessionStore(300, clock=lambda: now[0])
    st.put("c1", "s1")
    busy = st.lock("c1")
    busy.acquire()
    now[0] = 1000
    assert st.lock("c1") is busy


# ---------- Claude Code backend ----------

def test_args_resume_and_no_secrets(claude_cfg):
    b = bridge.ClaudeCodeBackend(claude_cfg, run=FakeRun([]))
    args = b.args("Be short.", None)
    assert args[:2] == ["/bin/claude", "-p"]
    assert args[args.index("--output-format") + 1] == "json"
    assert args[args.index("--model") + 1] == "haiku"
    assert args[args.index("--append-system-prompt") + 1] == "Be short."
    assert "--strict-mcp-config" in args and "--resume" not in args
    assert b.args("Be short.", "s1")[-2:] == ["--resume", "s1"]
    assert not any("secret" in a for a in args)


def test_claude_backend_ask(claude_cfg):
    run = FakeRun([ok("Hello.", "sess-1")])
    reply = bridge.ClaudeCodeBackend(claude_cfg, run=run).ask("hi", "Be short.", "s0", 20)
    assert reply == bridge.Reply("Hello.", "sess-1")
    call = run.calls[0]
    assert call["input"] == "hi"
    assert call["timeout"] == 20
    assert call["cwd"] == claude_cfg["workdir"]
    assert call["env"]["CLAUDE_CODE_OAUTH_TOKEN"] == "claude-secret"
    assert call["env"]["HA_TOKEN"] == "ha-secret"
    assert call["env"]["CLAUDE_CONFIG_DIR"] == claude_cfg["config_dir"]
    assert call["args"][-2:] == ["--resume", "s0"]


def test_claude_backend_errors(claude_cfg):
    error = {"type": "result", "is_error": True, "result": "No conversation found", "session_id": "x"}
    run = FakeRun([error, subprocess.TimeoutExpired("claude", 20)])
    b = bridge.ClaudeCodeBackend(claude_cfg, run=run)
    with pytest.raises(bridge.BackendError):
        b.ask("hi", "", "old", 20)
    with pytest.raises(bridge.BackendError):
        b.ask("hi", "", None, 20)


def test_claude_backend_lost_session(claude_cfg):
    def run(args, **kw):
        return subprocess.CompletedProcess(args, 1, stdout="", stderr="No conversation found with session ID: old")

    with pytest.raises(bridge.SessionLost):
        bridge.ClaudeCodeBackend(claude_cfg, run=run).ask("hi", "", "old", 20)
    lost = {"type": "result", "is_error": True, "result": "No conversation found with session ID: old"}
    with pytest.raises(bridge.SessionLost):
        bridge.ClaudeCodeBackend(claude_cfg, run=FakeRun([lost])).ask("hi", "", "old", 20)


def test_claude_backend_bad_output(claude_cfg):
    def run(args, **kw):
        return subprocess.CompletedProcess(args, 1, stdout="not json", stderr="boom")

    with pytest.raises(bridge.BackendError):
        bridge.ClaudeCodeBackend(claude_cfg, run=run).ask("hi", "", None, 20)


def test_make_backend_unknown():
    with pytest.raises(ValueError):
        bridge.make_backend({"backend": "nope"})


# ---------- converse ----------

def converse(backend, req, store=None, timeout=20):
    return bridge.converse(backend, store or bridge.SessionStore(300), req, "Be short.", timeout)


def test_converse_ok_and_resume():
    backend = FakeBackend()
    store = bridge.SessionStore(300)
    assert converse(backend, request("hi"), store) == {"speech": "Hello.", "ok": True}
    converse(backend, request("again"), store)
    assert backend.calls[1][1] == "sess-1"
    assert backend.calls[1][0].endswith("again")


def test_converse_without_conversation_id_starts_fresh():
    backend = FakeBackend()
    store = bridge.SessionStore(300)
    converse(backend, request(conversation_id=None), store)
    converse(backend, request(conversation_id=None), store)
    assert [c[1] for c in backend.calls] == [None, None]


def test_resume_failure_starts_fresh():
    backend = FakeBackend(fail_on_resume=True)
    store = bridge.SessionStore(300)
    store.put("c1", "expired")
    assert converse(backend, request(), store) == {"speech": "Hello.", "ok": True}
    assert [c[1] for c in backend.calls] == ["expired", None]


def test_timeout_or_error_is_not_run_again():
    for error in (bridge.BackendError("timeout"), bridge.BackendError("error_max_turns")):
        backend = FakeBackend(fail_with=error)
        store = bridge.SessionStore(300)
        store.put("c1", "s1")
        assert converse(backend, request(), store)["ok"] is False
        assert len(backend.calls) == 1


def test_busy_session_waits_only_within_budget():
    backend = FakeBackend()
    store = bridge.SessionStore(300)
    lock = store.lock("c1")
    lock.acquire()
    try:
        started = time.monotonic()
        r = bridge.converse(backend, store, request(), "Be short.", 0.3)
        assert r["ok"] is False and backend.calls == []
        assert time.monotonic() - started < 1
    finally:
        lock.release()


def test_wait_for_lock_shortens_backend_timeout():
    seen = []

    class Recorder(FakeBackend):
        def ask(self, prompt, system_prompt, session_id, timeout):
            seen.append(timeout)
            return super().ask(prompt, system_prompt, session_id, timeout)

    store = bridge.SessionStore(300)
    lock = store.lock("c1")
    lock.acquire()
    threading.Timer(0.3, lock.release).start()
    assert bridge.converse(Recorder(), store, request(), "Be short.", 20)["ok"]
    assert seen and seen[0] <= 19.8


def test_timeout_kills_and_speaks_error(claude_cfg):
    run = FakeRun([subprocess.TimeoutExpired("claude", 20)])
    backend = bridge.ClaudeCodeBackend(claude_cfg, run=run)
    r = converse(backend, request("x", language="ru-RU", conversation_id=None))
    assert r == {"speech": "Claude сейчас недоступен", "ok": False}
    assert 19 < run.calls[0]["timeout"] <= 20


def test_converse_sanitizes_answer():
    r = converse(FakeBackend(text="**Да.** https://a.b"), request())
    assert r == {"speech": "Да.", "ok": True}


def test_same_conversation_serialized():
    backend = FakeBackend(delay=0.2)
    store = bridge.SessionStore(300)
    same = [threading.Thread(target=converse, args=(backend, request(), store)) for _ in range(2)]
    for t in same:
        t.start()
    for t in same:
        t.join()
    assert backend.max_active == 1

    other = [threading.Thread(target=converse, args=(backend, request(conversation_id=c), store)) for c in ("a", "b")]
    for t in other:
        t.start()
    for t in other:
        t.join()
    assert backend.max_active == 2


def test_shared_session_spans_conversations():
    backend = FakeBackend()
    store = bridge.SessionStore(300)
    for conv in ("a", "b", None):
        bridge.converse(backend, store, request(conversation_id=conv), "Be short.", 20, shared=True)
    assert [c[1] for c in backend.calls] == [None, "sess-1", "sess-2"]


# ---------- HTTP ----------

@pytest.fixture
def server():
    backend = FakeBackend()
    cfg = {"session_ttl": 300, "timeout": 20, "debug_log": False}
    srv = ThreadingHTTPServer(("127.0.0.1", 0), bridge.make_handler(cfg, backend, "Be short."))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield "http://127.0.0.1:%d" % srv.server_port
    srv.shutdown()


def post(url, body):
    req = urllib.request.Request(url, data=body, method="POST", headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


def test_http_endpoint(server):
    status, body = post(server + "/converse", json.dumps(request("secret words")).encode())
    assert status == 200
    assert json.loads(body) == {"speech": "Hello.", "ok": True}
    assert post(server + "/converse", b"{not json")[0] == 400
    assert post(server + "/converse", json.dumps({"language": "en"}).encode())[0] == 400
    assert post(server + "/other", b"{}")[0] == 404
    with pytest.raises(urllib.error.HTTPError) as e:
        urllib.request.urlopen(server + "/converse", timeout=5)
    assert e.value.code == 404


def test_no_text_in_logs(server, caplog):
    caplog.set_level(logging.DEBUG)
    post(server + "/converse", json.dumps(request("secret words")).encode())
    assert caplog.records, "one line per request is expected"
    assert "secret words" not in caplog.text
    assert "Hello" not in caplog.text
