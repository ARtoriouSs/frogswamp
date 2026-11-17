import datetime as dt
import http.server
import json
import logging
import threading
import urllib.request
import zoneinfo

import pytest

import nutrition_core as nc
import nutrition_sync as ns
from test_nutrition_core import day_page, page

TZ = zoneinfo.ZoneInfo("Europe/Warsaw")
NOW = dt.datetime(2026, 11, 14, 20, 0, tzinfo=TZ)
TOKEN = "secret-notion-token"
REFRESH = "secret-refresh-token"


class FakeApi:
    """Plays Notion and Google. Each route answers from a list, the last answer repeats"""

    def __init__(self):
        self.requests = []
        self.meals = [page("m1", product="Секретный суп", eaten="2026-11-14T12:00:00+01:00", kcal=300)]
        self.days = [day_page("2026-11-14", True)]
        self.answers = {}
        self.created = 0
        api = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                self._answer()

            def do_POST(self):
                self._answer()

            def _answer(self):
                length = int(self.headers.get("Content-Length") or 0)
                raw = self.rfile.read(length).decode()
                api.requests.append((self.command, self.path, raw, dict(self.headers)))
                status, body, headers = api.route(self.command, self.path, raw)
                data = json.dumps(body).encode()
                self.send_response(status)
                for key, value in headers.items():
                    self.send_header(key, value)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def log_message(self, *args):
                pass

        self.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.server.server_port}"

    def queue(self, path_part, *answers):
        self.answers[path_part] = list(answers)

    def _queued(self, path):
        for part, answers in self.answers.items():
            if part in path and answers:
                return answers.pop(0) if len(answers) > 1 else answers[0]
        return None

    def route(self, method, path, raw):
        queued = self._queued(path)
        if queued is not None:
            return queued
        if path.startswith("/v1/data_sources/meals/query"):
            return 200, {"results": self.meals, "has_more": False, "next_cursor": None}, {}
        if path.startswith("/v1/data_sources/days/query"):
            return 200, {"results": self.days, "has_more": False, "next_cursor": None}, {}
        if path == "/token":
            return 200, {"access_token": "access-1", "expires_in": 3599}, {}
        if path.endswith("/dataPoints:batchDelete"):
            return 200, {"done": True}, {}
        if path.endswith("/dataPoints"):
            self.created += 1
            name = f"users/1/dataTypes/nutrition-log/dataPoints/{self.created}"
            return 200, {"done": True, "response": {"name": name}}, {}
        return 404, {"error": {"code": 404}}, {}

    def calls(self, suffix):
        return [r for r in self.requests if r[1].endswith(suffix)]

    def close(self):
        self.server.shutdown()


@pytest.fixture
def api():
    fake = FakeApi()
    yield fake
    fake.close()


@pytest.fixture
def cfg(api, tmp_path):
    return ns.Config(
        notion_url=api.url,
        notion_token=TOKEN,
        meals_source="meals",
        days_source="days",
        google=ns.GoogleConfig(
            api_url=api.url + "/v4/users/me/dataTypes/nutrition-log/dataPoints",
            token_url=api.url + "/token",
            client_id="client",
            client_secret="client-secret",
            refresh_token=REFRESH,
        ),
        tz=TZ,
        state_dir=tmp_path,
    )


def run(cfg, state=None):
    state = state or ns.State.load(cfg.state_dir)
    ns.sync_once(cfg, state, now=NOW)
    return state


def test_notion_pagination(api, cfg):
    first = page("a", kcal=1)
    second = page("b", kcal=2)
    api.queue("/v1/data_sources/meals/query", (200, {"results": [first], "has_more": True, "next_cursor": "c2"}, {}), (200, {"results": [second], "has_more": False, "next_cursor": None}, {}))
    pages = ns.notion_query(cfg.notion_url, TOKEN, "meals")
    assert [p["id"] for p in pages] == ["a", "b"]
    bodies = [json.loads(r[2]) for r in api.calls("/meals/query")]
    assert bodies[0] == {"page_size": 100}
    assert bodies[1] == {"page_size": 100, "start_cursor": "c2"}
    headers = api.calls("/meals/query")[0][3]
    assert headers["Authorization"] == f"Bearer {TOKEN}"
    assert headers["Notion-Version"] == "2025-09-03"


def test_notion_429_retried(api, cfg):
    api.queue("/v1/data_sources/meals/query", (429, {"code": "rate_limited"}, {"Retry-After": "0"}), (200, {"results": [], "has_more": False, "next_cursor": None}, {}))
    assert ns.notion_query(cfg.notion_url, TOKEN, "meals") == []
    assert len(api.calls("/meals/query")) == 2


def test_sync_writes_csv_and_status(api, cfg):
    state = run(cfg)
    csv = (cfg.state_dir / "www" / "daily.csv").read_text()
    assert csv == "date,calories,fats,proteins,carbohydrates,full?\n2026-11-14,300,1.6,1.8,2.4,TRUE\n"
    status = state.status
    assert status["last_read"] == NOW.isoformat()
    assert status["skipped"] == 0
    assert status["today"] == {"kcal": 300, "fats": 1.6, "proteins": 1.8, "carbohydrates": 2.4}
    assert status["google"]["ok"] is True
    assert status["google"]["pending"] == 0


def test_today_is_zero_without_meals(api, cfg):
    api.meals = []
    state = run(cfg)
    assert state.status["today"] == {"kcal": 0, "fats": 0, "proteins": 0, "carbohydrates": 0}


def test_google_create_then_edit(api, cfg):
    state = run(cfg)
    assert len(api.calls("/dataPoints")) == 1
    assert json.loads((cfg.state_dir / "google.json").read_text())["m1"]["name"].endswith("/1")
    api.meals = [page("m1", product="Секретный суп", eaten="2026-11-14T12:00:00+01:00", kcal=350)]
    run(cfg, state)
    deletes = api.calls("/dataPoints:batchDelete")
    assert [json.loads(r[2]) for r in deletes] == [{"names": ["users/1/dataTypes/nutrition-log/dataPoints/1"]}]
    assert len(api.calls("/dataPoints")) == 2
    synced = json.loads((cfg.state_dir / "google.json").read_text())
    assert synced["m1"]["name"].endswith("/2")


def test_google_unchanged_meal_not_sent_again(api, cfg):
    state = run(cfg)
    run(cfg, state)
    assert len(api.calls("/dataPoints")) == 1
    assert len(api.calls("/token")) == 1


def test_synced_map_survives_restart(api, cfg):
    run(cfg)
    run(cfg)
    assert len(api.calls("/dataPoints")) == 1


def test_google_create_waits_for_operation(api, cfg):
    api.queue("/dataPoints", (200, {"done": False, "name": "operations/op1"}, {}))
    api.queue("/v4/operations/op1", (200, {"done": False, "name": "operations/op1"}, {}), (200, {"done": True, "response": {"name": "users/1/x/9"}}, {}))
    state = run(cfg)
    assert state.synced["m1"]["name"] == "users/1/x/9"


def test_google_body_shape(api, cfg):
    meal = nc.Meal(id="m", product="Kronenbourg 1664 Blanc", eaten=dt.datetime(2026, 11, 13, 19, 17, tzinfo=TZ), meal="Ужин", kcal=225.0, protein=2.0, fat=0.0, carbs=20.0)
    # Proven against the real API in the Task 1 spike, plus the local offset so Google keeps Warsaw civil time
    assert ns.google_body(meal, TZ) == {
        "nutritionLog": {
            "interval": {
                "startTime": "2026-11-13T18:17:00Z",
                "startUtcOffset": "3600s",
                "endTime": "2026-11-13T18:18:00Z",
                "endUtcOffset": "3600s",
            },
            "foodDisplayName": "Kronenbourg 1664 Blanc",
            "mealType": "DINNER",
            "energy": {"kcal": 225.0},
            "totalCarbohydrate": {"grams": 20.0},
            "totalFat": {"grams": 0.0},
            "nutrients": [{"nutrient": "PROTEIN", "quantity": {"grams": 2.0}}],
        }
    }


def test_google_body_summer_offset_and_empty_name():
    meal = nc.Meal(id="m", product="", eaten=dt.datetime(2026, 7, 1, 8, 0, tzinfo=TZ), meal="", kcal=1.0, protein=0.0, fat=0.0, carbs=0.0)
    body = ns.google_body(meal, TZ)["nutritionLog"]
    assert body["interval"]["startUtcOffset"] == "7200s"
    assert body["foodDisplayName"] == "Еда"
    assert body["mealType"] == "MEAL_TYPE_UNSPECIFIED"


def test_notion_error_keeps_google(api, cfg):
    state = run(cfg)
    csv_before = (cfg.state_dir / "www" / "daily.csv").read_text()
    api.queue("/v1/data_sources/meals/query", (500, {"code": "internal_server_error"}, {}))
    requests_before = len(api.requests)
    later = NOW + dt.timedelta(minutes=5)
    ns.sync_once(cfg, state, now=later)
    google_calls = [r for r in api.requests[requests_before:] if "/v4/" in r[1] or r[1] == "/token"]
    assert google_calls == []
    assert (cfg.state_dir / "www" / "daily.csv").read_text() == csv_before
    assert state.status["last_read"] == NOW.isoformat()
    assert state.synced["m1"]


def test_google_auth_error_reported(api, cfg):
    api.queue("/token", (400, {"error": "invalid_grant", "error_description": "Token has been expired or revoked."}, {}))
    state = run(cfg)
    assert state.status["google"]["ok"] is False
    assert state.status["google"]["error"] == "invalid_grant"
    assert state.status["google"]["pending"] == 1
    assert (cfg.state_dir / "www" / "daily.csv").exists()
    assert state.status["last_read"] == NOW.isoformat()


def test_google_recovers_after_error(api, cfg):
    api.queue("/token", (400, {"error": "invalid_grant"}, {}))
    state = run(cfg)
    api.answers.clear()
    ns.sync_once(cfg, state, now=NOW + dt.timedelta(minutes=5))
    assert state.status["google"] == {"ok": True, "last_push": (NOW + dt.timedelta(minutes=5)).isoformat(), "error": None, "pending": 0}


def test_deleted_point_already_gone_is_forgotten(api, cfg):
    state = run(cfg)
    api.meals = []
    api.queue("/dataPoints:batchDelete", (404, {"error": {"code": 404, "status": "NOT_FOUND"}}, {}))
    ns.sync_once(cfg, state, now=NOW)
    assert state.synced == {}
    assert state.status["google"]["ok"] is True


def test_no_google(api, cfg):
    cfg = cfg._replace(google=None)
    state = run(cfg)
    assert api.calls("/dataPoints") == []
    assert state.status["google"] == {"ok": True, "last_push": None, "error": None, "pending": 0}


def test_status_served(api, cfg):
    state = run(cfg)
    server = ns.serve(state, 0)
    try:
        port = server.server_address[1]
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/status") as response:
            assert json.load(response)["today"]["kcal"] == 300
    finally:
        server.shutdown()


def test_logs_have_no_secrets(api, cfg, caplog):
    caplog.set_level(logging.DEBUG)
    api.queue("/token", (400, {"error": "invalid_grant"}, {}))
    state = run(cfg)
    api.answers.clear()
    ns.sync_once(cfg, state, now=NOW)
    for secret in [TOKEN, REFRESH, "client-secret", "access-1", "Секретный суп"]:
        assert secret not in caplog.text


def test_read_config(tmp_path):
    creds = tmp_path / "creds"
    creds.mkdir()
    for name, value in [("notion_token", TOKEN), ("notion_meals_source", "m"), ("notion_days_source", "d"), ("google_client_id", "c"), ("google_client_secret", "s"), ("google_refresh_token", REFRESH)]:
        (creds / name).write_text(value + "\n")
    args = ns.parse_args(["--credentials", str(creds), "--state-dir", str(tmp_path), "--timezone", "Europe/Warsaw"])
    cfg = ns.read_config(args)
    assert cfg.notion_token == TOKEN
    assert cfg.google.refresh_token == REFRESH
    assert cfg.notion_url == "https://api.notion.com"
    assert cfg.google.token_url == "https://oauth2.googleapis.com/token"
    assert ns.read_config(ns.parse_args([*["--credentials", str(creds), "--state-dir", str(tmp_path)], "--no-google"])).google is None
