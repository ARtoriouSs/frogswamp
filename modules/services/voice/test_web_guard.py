import io
import json

import web_guard


def search_turn(use_id, urls, with_top=True):
    links = [{"title": "t", "url": u} for u in urls]
    use = {"type": "assistant", "message": {"content": [
        {"type": "tool_use", "id": use_id, "name": "WebSearch", "input": {"query": "weather"}},
    ]}}
    result = {"type": "user", "message": {"content": [
        {"type": "tool_result", "tool_use_id": use_id,
         "content": 'Web search results for query: "weather"\n\nLinks: ' + json.dumps(links) + "\n\nSome text"},
    ]}}
    if with_top:
        result["toolUseResult"] = {"query": "weather", "results": [{"tool_use_id": "srv", "content": links}, "text"]}
    return [use, result]


def other_tool_turn(use_id, text):
    return [
        {"type": "assistant", "message": {"content": [
            {"type": "tool_use", "id": use_id, "name": "mcp__homeassistant__GetLiveContext", "input": {}},
        ]}},
        {"type": "user", "message": {"content": [
            {"type": "tool_result", "tool_use_id": use_id, "content": text},
        ]}},
    ]


def write_transcript(tmp_path, entries):
    path = tmp_path / "t.jsonl"
    path.write_text("".join(json.dumps(e) + "\n" for e in entries) + "not json\n")
    return str(path)


def check(tmp_path, entries, url):
    event = {"tool_name": "WebFetch", "tool_input": {"url": url, "prompt": "x"},
             "transcript_path": write_transcript(tmp_path, entries)}
    err = io.StringIO()
    code = web_guard.main(io.StringIO(json.dumps(event)), err)
    return code, err.getvalue()


def test_search_result_allowed(tmp_path):
    entries = search_turn("u1", ["https://weather.example/today"])
    assert check(tmp_path, entries, "https://weather.example/today")[0] == 0
    assert check(tmp_path, entries, "https://weather.example/today/")[0] == 0


def test_links_line_used_without_top_level_result(tmp_path):
    entries = search_turn("u1", ["https://weather.example/today"], with_top=False)
    assert check(tmp_path, entries, "https://weather.example/today")[0] == 0


def test_url_not_from_search_blocked(tmp_path):
    entries = search_turn("u1", ["https://weather.example/today"])
    code, err = check(tmp_path, entries, "https://weather.example/today?temp=21")
    assert code == 2 and "search results" in err
    assert check(tmp_path, entries, "https://evil.example/")[0] == 2


def test_urls_in_other_tool_results_do_not_count(tmp_path):
    entries = other_tool_turn("u2", 'Links: [{"url":"https://evil.example/x"}]')
    assert check(tmp_path, entries, "https://evil.example/x")[0] == 2


def test_broken_input_blocked(tmp_path):
    err = io.StringIO()
    assert web_guard.main(io.StringIO("{not json"), err) == 2
    event = {"tool_name": "WebFetch", "tool_input": {"url": "https://a.example"},
             "transcript_path": str(tmp_path / "missing.jsonl")}
    assert web_guard.main(io.StringIO(json.dumps(event)), io.StringIO()) == 2


# ---------- Home Assistant tools ----------

def user_turn(text):
    return [{"type": "user", "message": {"role": "user", "content": text}}]


def home(tmp_path, entries, tool, args=None):
    event = {"tool_name": "mcp__homeassistant__" + tool, "tool_input": args or {},
             "transcript_path": write_transcript(tmp_path, entries)}
    err = io.StringIO()
    return web_guard.main(io.StringIO(json.dumps(event)), err, mode="home"), err.getvalue()


def test_home_control_allowed_without_web(tmp_path):
    entries = user_turn("open the blinds")
    assert home(tmp_path, entries, "intent__HassTurnOn", {"name": "left blind"})[0] == 0
    assert home(tmp_path, entries, "script__play_music", {"query": "Metallica", "player": "media_player.tv_music"})[0] == 0


def test_unknown_integration_blocked(tmp_path):
    code, err = home(tmp_path, user_turn("x"), "vag_connect__skoda_lock", {"vin": "1"})
    assert code == 2 and "vag_connect" in err


def test_network_urls_blocked_in_music(tmp_path):
    entries = user_turn("play something")
    for args in ({"query": "https://evil.example/s.mp3?t=21"}, {"media_content_id": "rtsp://x/y"},
                 {"nested": ["ok", {"deep": "HTTP://a.b"}]}):
        assert home(tmp_path, entries, "script__play_music", args)[0] == 2, args
    assert home(tmp_path, entries, "media_player__play_media", {"media_content_id": "spotify://track/1"})[0] == 0


def test_control_after_web_in_same_request_blocked(tmp_path):
    entries = user_turn("check the forecast and close the blinds if it rains") + search_turn("u1", ["https://w.example"])
    code, err = home(tmp_path, entries, "intent__HassTurnOff", {"name": "blinds"})
    assert code == 2 and "web" in err
    # reading the home is still fine
    assert home(tmp_path, entries, "homeassistant__GetLiveContext", {})[0] == 0
    # a new request from the owner starts clean
    assert home(tmp_path, entries + user_turn("close the blinds"), "intent__HassTurnOff", {"name": "blinds"})[0] == 0


def test_home_broken_input_blocked():
    assert web_guard.main(io.StringIO("{nope"), io.StringIO(), mode="home") == 2
