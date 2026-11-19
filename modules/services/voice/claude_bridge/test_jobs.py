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


# --- conditions and steps

from jobs import EntityState, advance, check, entities_of  # noqa: E402

OWNER = "person.aleksander_cieslowski"
WIFE = "person.natallia_radziuk"


def st(state, minutes_ago=60, attributes=None):
    return EntityState(state, attributes or {}, NOW - timedelta(minutes=minutes_ago))


def lookup(states):
    return lambda entity_id: states.get(entity_id)


def at(minutes):
    return NOW + timedelta(minutes=minutes)


def test_back_home_does_not_fire_on_current_presence():
    parsed = parse(BACK_HOME)
    step = advance(parsed, NOW, lookup({OWNER: st("home", 60)}))
    assert parsed.status == "waiting_arm"
    assert not step.run


def test_short_away_does_not_arm():
    parsed = parse(BACK_HOME)
    away_3 = {OWNER: EntityState("not_home", {}, at(-3))}
    step = advance(parsed, NOW, lookup(away_3))
    assert parsed.status == "waiting_arm"
    assert step.next_check == at(7)
    step = advance(parsed, at(1), lookup({OWNER: st("home", 0)}))
    assert parsed.status == "waiting_arm"
    assert not step.run


def test_away_ten_minutes_arms_then_home_runs():
    parsed = parse(BACK_HOME)
    advance(parsed, NOW, lookup({OWNER: st("not_home", 10)}))
    assert parsed.status == "armed"
    assert parsed.armed_at == NOW
    step = advance(parsed, at(60), lookup({OWNER: EntityState("home", {}, at(59))}))
    assert step.run
    assert parsed.status == "done"
    assert parsed.finished_at == at(60)


def test_runs_at_once_when_already_true():
    parsed = parse(job(run_when=[{"type": "numeric", "entity_id": "sensor.bedroom_temp", "below": 20}]))
    step = advance(parsed, NOW, lookup({"sensor.bedroom_temp": st("19.4")}))
    assert step.run


def test_unknown_is_never_true():
    for state in ("unknown", "unavailable"):
        states = lookup({OWNER: st(state, 600)})
        for want in ("home", "away"):
            ok, _ = check({"type": "person", "who": "owner", "state": want, "for_min": 0}, parse(job()), NOW, states)
            assert not ok
    ok, _ = check({"type": "numeric", "entity_id": "sensor.t", "below": 20, "for_min": 0}, parse(job()), NOW,
                  lookup({"sensor.t": st("unknown")}))
    assert not ok
    ok, _ = check({"type": "person", "who": "owner", "state": "away", "for_min": 0}, parse(job()), NOW, lookup({}))
    assert not ok


def test_everyone_and_anyone():
    states = lookup({OWNER: st("home"), WIFE: st("not_home")})
    parsed = parse(job())

    def person(who, state):
        return check({"type": "person", "who": who, "state": state, "for_min": 0}, parsed, NOW, states)[0]

    assert person("anyone", "home")
    assert not person("everyone", "home")
    assert person("wife", "away")
    assert not person("everyone", "away")


def test_changed_within():
    cond = {"type": "changed", "entity_id": "binary_sensor.door", "to": "on", "within_min": 10}
    parsed = parse(job())
    assert check(cond, parsed, NOW, lookup({"binary_sensor.door": st("on", 5)}))[0]
    assert not check(cond, parsed, NOW, lookup({"binary_sensor.door": st("on", 11)}))[0]
    assert not check(cond, parsed, NOW, lookup({"binary_sensor.door": st("off", 1)}))[0]


def test_time_from_armed():
    parsed = parse(dict(BACK_HOME, run_when=[{"type": "time", "in_min": 30, "from": "armed"}]))
    advance(parsed, NOW, lookup({OWNER: st("not_home", 10)}))
    assert parsed.status == "armed"
    assert datetime.fromisoformat(parsed.run_when[0]["due"]) == at(30)
    step = advance(parsed, at(10), lookup({OWNER: st("not_home", 20)}))
    assert not step.run
    assert step.next_check == at(30)
    assert advance(parsed, at(30), lookup({})).run


def test_time_window_across_midnight():
    cond = {"type": "time_window", "from": "22:00", "to": "06:00"}
    parsed = parse(job())

    def window(hour, minute=0, day=19):
        return check(cond, parsed, datetime(2026, 11, day, hour, minute, tzinfo=TZ), lookup({}))

    assert window(23, 30)[0]
    assert window(5, 0, day=20)[0]
    ok, next_check = window(12)
    assert not ok
    assert next_check == datetime(2026, 11, 19, 22, 0, tzinfo=TZ)


def test_expiry():
    parsed = parse(BACK_HOME)
    step = advance(parsed, NOW + timedelta(hours=24), lookup({OWNER: st("home")}))
    assert step.expired
    assert not step.run
    assert parsed.status == "expired"


def test_next_check_never_after_expiry():
    parsed = parse(job(run_when=[{"type": "time", "in_min": 600}], expires_in_min=60))
    assert advance(parsed, NOW, lookup({})).next_check == at(60)


def test_finished_job_does_nothing():
    parsed = parse(job())
    assert advance(parsed, NOW, lookup({})).run
    step = advance(parsed, at(1), lookup({}))
    assert not step.run and not step.expired and step.next_check is None


def test_entities_of():
    parsed = parse(
        dict(BACK_HOME, run_when=[{"type": "state", "entity_id": "binary_sensor.door", "state": "on"}])
    )
    assert entities_of(parsed) == {OWNER}
    advance(parsed, NOW, lookup({OWNER: st("not_home", 10)}))
    assert entities_of(parsed) == {"binary_sensor.door"}
