import datetime as dt
import json
import urllib.error
import urllib.request

import pytest

import nutrition_sync as ns
from test_nutrition_core import page
from test_nutrition_sync import NOW, TZ, FakeApi, cfg  # noqa: F401 (cfg is a fixture)


class NotionWriteApi(FakeApi):
    """FakeApi that also takes new pages and page changes"""

    def __init__(self):
        super().__init__()
        self.server.RequestHandlerClass.do_PATCH = self.server.RequestHandlerClass.do_POST
        self.pages = 0

    def route(self, method, path, raw):
        queued = self._queued(path)
        if queued is not None:
            return queued
        if method == "POST" and path == "/v1/pages":
            self.pages += 1
            return 200, {"object": "page", "id": f"new-{self.pages}"}, {}
        if method == "PATCH" and path.startswith("/v1/pages/"):
            return 200, {"object": "page", "id": path.rsplit("/", 1)[1]}, {}
        return super().route(method, path, raw)

    def writes(self):
        return [(r[0], r[1], json.loads(r[2] or "{}")) for r in self.requests if r[1].startswith("/v1/pages")]


@pytest.fixture
def api():
    fake = NotionWriteApi()
    yield fake
    fake.close()


@pytest.fixture
def service(api, cfg):  # noqa: F811
    api.meals = [page("phone-row", eaten="2026-11-14T12:00:00+01:00", kcal=300)]
    state = ns.State.load(cfg.state_dir)
    ns.sync_once(cfg.__class__(**{**cfg._asdict(), "google": None}), state, now=NOW)
    clock = {"now": NOW}
    server = ns.serve(state, 0, cfg, clock=lambda: clock["now"])
    port = server.server_address[1]

    def call(method, path, body=None):
        data = None if body is None else json.dumps(body).encode()
        request = urllib.request.Request(f"http://127.0.0.1:{port}{path}", data=data, method=method, headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(request) as response:
                return response.status, json.load(response)
        except urllib.error.HTTPError as error:
            return error.code, json.load(error)

    yield state, call, clock
    server.shutdown()


def test_log_creates_one_row_and_wakes(api, service):
    state, call, _ = service
    status, answer = call("POST", "/log", {"product": "Банан", "kcal": 105, "meal": "Перекус"})
    assert status == 200 and answer["ok"] and answer["id"] == "new-1"
    assert answer["day_total"] == {"kcal": 405.0, "water_ml": 0.0}
    writes = api.writes()
    assert len(writes) == 1
    method, path, body = writes[0]
    assert (method, path) == ("POST", "/v1/pages")
    assert body["parent"] == {"type": "data_source_id", "data_source_id": "meals"}
    assert body["properties"]["Kcal"] == {"number": 105.0}
    assert state.wake.is_set()


def test_log_rejects_bad_values_without_notion_call(api, service):
    _, call, _ = service
    status, answer = call("POST", "/log", {"product": "Банан", "kcal": 9000})
    assert status == 400 and answer == {"ok": False, "error": "kcal must be 0 to 3000"}
    assert api.writes() == []


def test_update_any_known_row(api, service):
    _, call, _ = service
    status, answer = call("POST", "/log/update", {"id": "phone-row", "kcal": 150})
    assert status == 200 and answer["ok"]
    assert api.writes() == [("PATCH", "/v1/pages/phone-row", {"properties": {"Kcal": {"number": 150.0}}})]


def test_update_unknown_id_refused(api, service):
    _, call, _ = service
    status, answer = call("POST", "/log/update", {"id": "made-up", "kcal": 150})
    assert status == 400 and answer["error"] == "unknown entry"
    assert api.writes() == []


def test_delete_one_row(api, service):
    _, call, _ = service
    status, _ = call("POST", "/log/delete", {"id": "phone-row"})
    assert status == 200
    assert api.writes() == [("PATCH", "/v1/pages/phone-row", {"in_trash": True})]
    assert all(row["id"] != "phone-row" for row in call("GET", "/log/recent")[1]["entries"])


def test_mass_delete_refused(api, service):
    state, call, clock = service
    status, answer = call("POST", "/log/delete", {"id": ["phone-row", "other"]})
    assert status == 400 and answer["error"] == "one entry per call"
    for i in range(5):
        state.meal_rows[f"row-{i}"] = state.meal_rows["phone-row"]
        clock["now"] = NOW + dt.timedelta(minutes=i)
        assert call("POST", "/log/delete", {"id": f"row-{i}"})[0] == 200
    status, answer = call("POST", "/log/delete", {"id": "phone-row"})
    assert status == 400 and answer["error"] == "too many removals, at most 5 per hour"
    assert len(api.writes()) == 5


def test_notion_failure_keeps_state(api, service):
    state, call, _ = service
    api.queue("/v1/pages/phone-row", (401, {"object": "error", "status": 401}, {}))
    status, answer = call("POST", "/log/delete", {"id": "phone-row"})
    assert status == 502 and answer["error"] == "notion 401"
    # Counted anyway, so failures can't be used to get past the limit
    assert len(state.deletes) == 1
    assert "phone-row" in state.meal_rows
    assert call("GET", "/status")[0] == 200


def test_day_limit(api, service):
    state, call, _ = service
    for i in range(39):
        assert call("POST", "/log", {"product": "Вода", "water": 100})[0] == 200
    status, answer = call("POST", "/log", {"product": "Вода", "water": 100})
    assert status == 400 and answer["error"] == "at most 40 entries a day"


def test_recent_lists_today_and_yesterday(api, service):
    state, call, _ = service
    old = ns.nc.parse_meals([page("old-row", eaten="2026-11-10T12:00:00+01:00", kcal=50)], TZ)[0][0]
    late = ns.nc.parse_meals([page("late-row", eaten="2026-11-14T19:00:00+01:00", kcal=70)], TZ)[0][0]
    state.meal_rows.update({"old-row": old, "late-row": late})
    entries = call("GET", "/log/recent")[1]["entries"]
    assert [entry["id"] for entry in entries] == ["late-row", "phone-row"]
    assert entries[1]["kcal"] == 300 and entries[1]["day"] == "2026-11-14"


def test_delete_limit_survives_restart(api, service, cfg):  # noqa: F811
    state, call, _ = service
    call("POST", "/log/delete", {"id": "phone-row"})
    assert len(ns.State.load(cfg.state_dir).deletes) == 1


def test_update_cannot_zero_a_row(api, service):
    _, call, _ = service
    status, answer = call("POST", "/log/update", {"id": "phone-row", "kcal": 0})
    assert status == 400 and answer["error"] == "kcal or water is required, remove the entry instead"
    assert api.writes() == []


def test_updates_limited_per_hour(api, service):
    _, call, clock = service
    for i in range(20):
        clock["now"] = NOW + dt.timedelta(minutes=i)
        assert call("POST", "/log/update", {"id": "phone-row", "kcal": 100 + i})[0] == 200
    status, answer = call("POST", "/log/update", {"id": "phone-row", "kcal": 99})
    assert status == 400 and answer["error"] == "too many changes, at most 20 per hour"


def test_just_logged_row_can_be_removed(api, service):
    state, call, _ = service
    new_id = call("POST", "/log", {"product": "Вода", "water": 250})[1]["id"]
    assert call("POST", "/log/update", {"id": new_id, "water": 300})[0] == 200
    assert call("POST", "/log/delete", {"id": new_id})[0] == 200
    assert all(entry["id"] != new_id for entry in call("GET", "/log/recent")[1]["entries"])


def test_bad_notion_answer_is_502(api, service):
    _, call, _ = service
    api.queue("/v1/pages", (200, {"object": "page"}, {}))
    status, answer = call("POST", "/log", {"product": "Банан", "kcal": 105})
    assert status == 502 and answer["ok"] is False


def test_corrupt_delete_file_does_not_stop_the_service(cfg):  # noqa: F811
    (cfg.state_dir / "voice_deletes.json").write_text("{broken")
    assert ns.State.load(cfg.state_dir).deletes == []


def test_read_that_started_before_a_write_does_not_undo_it(api, service, cfg):  # noqa: F811
    state, call, _ = service
    new_id = call("POST", "/log", {"product": "Вода", "water": 250})[1]["id"]
    call("POST", "/log/delete", {"id": "phone-row"})
    # A Notion read from before both writes: still has phone-row, not the new row yet
    ns.sync_once(cfg.__class__(**{**cfg._asdict(), "google": None}), state, now=NOW)
    ids = [entry["id"] for entry in call("GET", "/log/recent")[1]["entries"]]
    assert new_id in ids and "phone-row" not in ids
