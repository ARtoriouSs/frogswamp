import json
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from jobs import JobError, parse_job

TZ = ZoneInfo("Europe/Warsaw")
NOW = datetime(2026, 11, 19, 8, 0, tzinfo=TZ)


class FakeHome:
    def __init__(self, exposed=(), presence=None, satellites=None):
        self._exposed = set(exposed)
        self._presence = presence or {}
        self._satellites = satellites or {}

    def exists(self, entity_id):
        return "missing" not in entity_id

    def exposed(self, entity_id):
        return entity_id in self._exposed

    def presence_sensors(self, area):
        return self._presence.get(area, [])

    def satellites(self, area):
        if area is None:
            return [e for entities in self._satellites.values() for e in entities]
        return self._satellites.get(area, [])


HOME = FakeHome(
    exposed={"light.hall", "scene.evening", "media_player.tv", "climate.bedroom", "lock.front", "switch.guest_towel_heat"}
)

BACK_HOME = {
    "title": "тёплые полы в большой ванной, когда вернусь",
    "arm_when": [{"type": "person", "who": "owner", "state": "away", "for_min": 10}],
    "run_when": [{"type": "person", "who": "owner", "state": "home", "for_min": 1}],
    "do": [{"type": "heat", "device": "floor_master", "minutes": 120}],
}


def job(do=None, run_when=None, **extra):
    return {
        "title": "test",
        "run_when": run_when or [{"type": "time", "in_min": 0}],
        "do": do or [{"type": "notify", "message": "hi"}],
        **extra,
    }


def parse(raw, home=HOME, active=0):
    return parse_job(raw, NOW, home, "j1", active)


def test_parse_back_home_job():
    parsed = parse(json.dumps(BACK_HOME))
    assert parsed.status == "waiting_arm"
    assert parsed.armed_at is None
    assert parsed.expires == NOW + timedelta(hours=24)
    assert parsed.title == BACK_HOME["title"]


def test_no_arm_is_armed_at_once():
    parsed = parse(job())
    assert parsed.status == "armed"
    assert parsed.armed_at == NOW


def test_numeric_strings_accepted():
    parsed = parse(job(do=[{"type": "heat", "device": "towel_guest", "minutes": "60"}]))
    assert parsed.do[0]["minutes"] == 60


def test_bad_json_error():
    with pytest.raises(JobError, match="not valid JSON"):
        parse("{")


def test_limits():
    cond = {"type": "time", "in_min": 1}
    act = {"type": "notify", "message": "x"}
    for bad in (
        job(run_when=[cond] * 6),
        job(do=[act] * 6),
        job(do=[{"type": "turn_on", "entity_id": "light.hall", "minutes": 361}]),
        job(expires_in_min=43201),
    ):
        with pytest.raises(JobError):
            parse(bad)
    with pytest.raises(JobError):
        parse(job(), active=10)
    assert parse(job(expires_in_min=43200)).expires == NOW + timedelta(days=30)
    assert parse(job(do=[{"type": "heat", "device": "towel_guest", "minutes": 360}])).do[0]["minutes"] == 360


def test_missing_title_and_unknown_types():
    for bad in (
        {k: v for k, v in job().items() if k != "title"},
        job(run_when=[{"type": "weather"}]),
        job(do=[{"type": "unlock"}]),
        job(run_when=[{"type": "person", "who": "neighbour", "state": "home"}]),
    ):
        with pytest.raises(JobError):
            parse(bad)


def test_heater_entity_refused():
    with pytest.raises(JobError, match="use heat"):
        parse(job(do=[{"type": "turn_on", "entity_id": "switch.guest_towel_heat"}]))


def test_domain_rules():
    def do(action):
        return parse(job(do=[action]))

    with pytest.raises(JobError):
        do({"type": "turn_on", "entity_id": "lock.front"})
    do({"type": "turn_on", "entity_id": "scene.evening"})
    with pytest.raises(JobError):
        do({"type": "turn_off", "entity_id": "scene.evening"})
    do({"type": "turn_off", "entity_id": "media_player.tv"})
    with pytest.raises(JobError):
        do({"type": "turn_on", "entity_id": "media_player.tv"})
    with pytest.raises(JobError):
        do({"type": "turn_on", "entity_id": "light.not_exposed"})
    do({"type": "set_temperature", "entity_id": "climate.bedroom", "temperature": "21.5"})
    with pytest.raises(JobError):
        do({"type": "set_temperature", "entity_id": "light.hall", "temperature": 21})


def test_announce_without_satellite_refused():
    with pytest.raises(JobError, match="use notify"):
        parse(job(do=[{"type": "announce", "message": "hi"}]))
    home = FakeHome(satellites={"living_room": ["assist_satellite.sat1"]})
    parsed = parse(job(do=[{"type": "announce", "message": "hi"}]), home=home)
    assert parsed.do[0]["entities"] == ["assist_satellite.sat1"]


def test_room_presence_without_sensor_refused():
    with pytest.raises(JobError, match="no presence sensor"):
        parse(job(run_when=[{"type": "room_presence", "area": "kitchen"}]))
    home = FakeHome(presence={"kitchen": ["binary_sensor.kitchen_presence"]})
    parsed = parse(job(run_when=[{"type": "room_presence", "area": "kitchen"}]), home=home)
    assert parsed.run_when[0]["entities"] == ["binary_sensor.kitchen_presence"]


def test_time_in_min_resolved():
    parsed = parse(job(run_when=[{"type": "time", "in_min": 40}]))
    assert datetime.fromisoformat(parsed.run_when[0]["due"]) == NOW + timedelta(minutes=40)


def test_at_time_already_passed_today_is_tomorrow():
    early = parse(job(run_when=[{"type": "time", "at": "07:00"}]))
    assert datetime.fromisoformat(early.run_when[0]["due"]) == datetime(2026, 11, 20, 7, 0, tzinfo=TZ)
    later = parse(job(run_when=[{"type": "time", "at": "09:00"}]))
    assert datetime.fromisoformat(later.run_when[0]["due"]) == datetime(2026, 11, 19, 9, 0, tzinfo=TZ)


def test_condition_entity_must_exist():
    with pytest.raises(JobError, match="no entity"):
        parse(job(run_when=[{"type": "state", "entity_id": "binary_sensor.missing_door", "state": "on"}]))
    parsed = parse(job(run_when=[{"type": "state", "entity_id": "binary_sensor.door", "state": "on"}]))
    assert parsed.run_when[0]["state"] == ["on"]
