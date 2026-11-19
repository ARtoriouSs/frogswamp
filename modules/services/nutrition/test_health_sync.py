import datetime as dt
import json
import urllib.request

import pytest

import health_sync as hs
import nutrition_sync as ns
from test_nutrition_sync import NOW, TZ, FakeApi, cfg, run  # noqa: F401 (cfg is a fixture)


class HealthApi(FakeApi):
    """FakeApi that also answers the Google Health reads; lists hold points per data type"""

    def __init__(self):
        super().__init__()
        self.health = {}

    def route(self, method, path, raw):
        queued = self._queued(path)
        if queued is not None:
            return queued
        if "/dataTypes/" in path and "nutrition-log" not in path:
            kind = path.split("/dataTypes/")[1].split("/")[0]
            if ":dailyRollUp" in path or ":rollUp" in path:
                return 200, {"rollupDataPoints": self.health.get(kind + path.split("dataPoints")[1].split("?")[0], [])}, {}
            return 200, {"dataPoints": self.health.get(kind, [])}, {}
        return super().route(method, path, raw)

    def health_calls(self, part):
        return [r for r in self.requests if part in r[1]]


@pytest.fixture
def api():
    fake = HealthApi()
    yield fake
    fake.close()


def night(start, end):
    z = lambda m: m.astimezone(dt.timezone.utc).isoformat().replace("+00:00", "Z")
    return {"sleep": {"interval": {"startTime": z(start), "endTime": z(end)}, "type": "CLASSIC", "stages": []}}


def files(cfg):
    folder = cfg.state_dir / "www" / "health"
    return json.loads((folder / "days.json").read_text()), folder / "day"


def test_health_files_written(api, cfg):
    api.health["sleep"] = [night(dt.datetime(2026, 11, 13, 23, tzinfo=TZ), dt.datetime(2026, 11, 14, 7, tzinfo=TZ))]
    api.health["total-calories:dailyRollUp"] = [{"civilStartTime": {"date": {"year": 2026, "month": 11, "day": 14}}, "totalCalories": {"kcalSum": 2500}}]
    state = run(cfg)
    days, day_dir = files(cfg)
    assert days["days"]["2026-11-14"]["sleep"]["asleep_min"] == 420
    assert days["days"]["2026-11-14"]["burned_kcal"] == 2500
    assert len(list(day_dir.glob("*.json"))) == 31
    assert state.status["health"]["ok"] is True
    assert json.loads((day_dir / "2026-11-14.json").read_text())["sleep"][0]["stage"] == "asleep"


def test_late_data_rewrites_yesterday(api, cfg):
    state = run(cfg)
    assert "2026-11-13" not in files(cfg)[0]["days"]
    api.health["sleep"] = [night(dt.datetime(2026, 11, 13, 1, tzinfo=TZ), dt.datetime(2026, 11, 13, 8, tzinfo=TZ))]
    api.requests.clear()
    ns.sync_once(cfg, state, now=NOW)
    days, day_dir = files(cfg)
    assert days["days"]["2026-11-13"]["sleep"]["asleep_min"] == 420
    assert json.loads((day_dir / "2026-11-13.json").read_text())["sleep"]
    # Later runs only read the last 3 days again
    assert len(api.health_calls("/heart-rate/dataPoints:rollUp")) == 3


def test_pages_of_25_all_read(api, cfg):
    first = [night(dt.datetime(2026, 11, 1, 1, tzinfo=TZ), dt.datetime(2026, 11, 1, 2, tzinfo=TZ))]
    second = [night(dt.datetime(2026, 11, 2, 1, tzinfo=TZ), dt.datetime(2026, 11, 2, 2, tzinfo=TZ))]
    api.queue("/dataTypes/sleep/", (200, {"dataPoints": first, "nextPageToken": "p2"}, {}), (200, {"dataPoints": second}, {}))
    run(cfg)
    calls = api.health_calls("/dataTypes/sleep/")
    assert "pageSize=25" in calls[0][1] and "pageToken=p2" in calls[1][1]
    assert {"2026-11-01", "2026-11-02"} <= set(files(cfg)[0]["days"])


def test_rollup_split_in_14_day_pieces(api, cfg):
    run(cfg)
    bodies = [json.loads(r[2]) for r in api.health_calls("/total-calories/dataPoints:dailyRollUp")]
    assert len(bodies) == 27
    assert bodies[0]["range"]["start"]["date"] == {"year": 2025, "month": 11, "day": 15}
    assert bodies[-1]["range"]["end"]["date"] == {"year": 2026, "month": 11, "day": 15}
    spans = [dt.date(**b["range"]["end"]["date"]) - dt.date(**b["range"]["start"]["date"]) for b in bodies]
    assert max(spans) == dt.timedelta(days=14)


def test_429_retried_after_retry_after(api, cfg):
    api.queue("/dataTypes/exercise/", (429, {"error": {"code": 429}}, {"Retry-After": "0"}), (200, {"dataPoints": []}, {}))
    state = run(cfg)
    assert len(api.health_calls("/dataTypes/exercise/")) == 2
    assert state.status["health"]["ok"] is True


def test_missing_scope_sets_health_error_and_meals_still_pushed(api, cfg):
    state = run(cfg)
    before = (cfg.state_dir / "www" / "health" / "days.json").read_text()
    api.queue("/dataTypes/sleep/", (403, {"error": {"code": 403, "status": "PERMISSION_DENIED"}}, {}))
    api.meals.append(__import__("test_nutrition_core").page("m2", eaten="2026-11-14T13:00:00+01:00", kcal=50))
    ns.sync_once(cfg, state, now=NOW)
    assert state.status["health"]["ok"] is False
    assert state.status["health"]["error"] == "sleep: 403"
    assert state.status["google"]["ok"] is True
    assert "m2" in state.synced
    assert (cfg.state_dir / "www" / "health" / "days.json").read_text() == before


def test_wellness_endpoint_days_param(api, cfg):
    state = run(cfg)
    server = ns.serve(state, 0, cfg)
    port = server.server_address[1]
    try:
        get = lambda q: json.load(urllib.request.urlopen(f"http://127.0.0.1:{port}/wellness{q}"))
        today = dt.datetime.now(TZ).date()
        assert hs.wellness(cfg.state_dir, state.eaten, NOW.date(), 1)["days"]["2026-11-14"]["eaten"]["kcal"] == 300
        assert get("?days=0")["days"].keys() <= {today.isoformat()}
        assert len(hs.wellness(cfg.state_dir, {}, NOW.date(), 500)["days"]) <= 90
        assert "updated" in get("?days=abc")
    finally:
        server.shutdown()


def test_wellness_clamps_to_90_days(cfg):
    eaten = {(NOW.date() - dt.timedelta(days=i)).isoformat(): {"kcal": 1} for i in range(200)}
    assert len(hs.wellness(cfg.state_dir, eaten, NOW.date(), 500)["days"]) == 90
    assert list(hs.wellness(cfg.state_dir, eaten, NOW.date(), 0)["days"]) == ["2026-11-14"]


def test_old_day_files_removed(api, cfg):
    day_dir = cfg.state_dir / "www" / "health" / "day"
    day_dir.mkdir(parents=True)
    (day_dir / "2026-10-01.json").write_text("{}")
    run(cfg)
    assert not (day_dir / "2026-10-01.json").exists()
    assert (day_dir / "2026-10-15.json").exists()
