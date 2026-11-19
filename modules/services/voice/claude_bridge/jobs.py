"""One-shot voice jobs: "do X when Y". Pure Python, no Home Assistant imports, so it is unit-tested alone.

Claude writes a job as JSON; parse_job checks it against a fixed set of condition and action types.
A new type is one parser here (and one checker for conditions), plus a line in the prompt
"""

import json
import re
from dataclasses import asdict, dataclass
from datetime import datetime, time, timedelta
from typing import Callable, Protocol

PEOPLE = {"owner": "person.aleksander_cieslowski", "wife": "person.natallia_radziuk"}
WHO = ("owner", "wife", "anyone", "everyone")
HEAT_DEVICES = ("towel_master", "towel_guest", "floor_master", "floor_guest")
# the lease devices; runs of these go through script.heat_lease only
HEATER_ENTITIES = frozenset(
    {
        "switch.master_bathroom_towel_heat_relay",
        "switch.guest_towel_heat",
        "climate.guest_bathroom_towel_heat_guest",
        "switch.master_floor_warming",
        "switch.guest_floor_warming",
        "climate.thermostat_master_local",
        "climate.thermostat_guest_local",
    }
)
ALLOWED_DOMAINS = {
    "turn_on": {"light", "switch", "fan", "climate", "scene"},
    "turn_off": {"light", "switch", "fan", "climate", "media_player"},
    "set_temperature": {"climate"},
}
MAX_ACTIVE = 10
MAX_CONDITIONS = 5
MAX_ACTIONS = 5
MAX_LEASE_MIN = 360
MAX_EXPIRY_MIN = 43200
DEFAULT_EXPIRY_MIN = 1440
KEEP_FINISHED = timedelta(hours=24)

HHMM_RE = re.compile(r"^([01]?\d|2[0-3]):([0-5]\d)$")


class JobError(ValueError):
    """A job Claude can't have; the message goes back to Claude as is"""


class Home(Protocol):
    def exists(self, entity_id: str) -> bool: ...

    def exposed(self, entity_id: str) -> bool: ...

    def presence_sensors(self, area: str) -> list[str]: ...

    def satellites(self, area: str | None) -> list[str]: ...


@dataclass
class Job:
    id: str
    title: str
    arm_when: list[dict]
    run_when: list[dict]
    do: list[dict]
    created: datetime
    expires: datetime
    status: str
    armed_at: datetime | None = None
    finished_at: datetime | None = None


def _number(spec, key, low, high, required=True):
    value = spec.get(key)
    if value is None or value == "":
        if required:
            raise JobError(f"{spec.get('type')}: {key} is required")
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise JobError(f"{spec.get('type')}: {key} must be a number") from None
    if not low <= number <= high:
        raise JobError(f"{spec.get('type')}: {key} must be from {low} to {high}")
    return int(number) if number.is_integer() else number


def _text(spec, key, required=True):
    value = spec.get(key)
    if not isinstance(value, str) or not value.strip():
        if required:
            raise JobError(f"{spec.get('type')}: {key} is required")
        return None
    return value.strip()


def _choice(spec, key, options):
    value = spec.get(key)
    if value not in options:
        raise JobError(f"{spec.get('type')}: {key} must be one of {', '.join(options)}")
    return value


def _entity(spec, home):
    entity_id = _text(spec, "entity_id")
    if not home.exists(entity_id):
        raise JobError(f"{spec.get('type')}: no entity {entity_id}")
    return entity_id


def _hhmm(spec, key):
    value = _text(spec, key)
    if not HHMM_RE.match(value):
        raise JobError(f"{spec.get('type')}: {key} must be HH:MM")
    return value


def next_at(hhmm: str, now: datetime) -> datetime:
    """The next moment after now with this local time of day"""
    hour, minute = map(int, hhmm.split(":"))
    moment = datetime.combine(now.date(), time(hour, minute), tzinfo=now.tzinfo)
    return moment if moment > now else datetime.combine(
        now.date() + timedelta(days=1), time(hour, minute), tzinfo=now.tzinfo
    )


def _parse_person(spec, now, home):
    out = {"type": "person", "who": _choice(spec, "who", WHO), "state": _choice(spec, "state", ("home", "away"))}
    out["for_min"] = _number(spec, "for_min", 0, MAX_EXPIRY_MIN, required=False) or 0
    return out


def _parse_state(spec, now, home):
    wanted = spec.get("state")
    wanted = [wanted] if isinstance(wanted, str) else wanted
    if not wanted or not all(isinstance(s, str) and s for s in wanted):
        raise JobError("state: state is required, a string or a list of strings")
    out = {"type": "state", "entity_id": _entity(spec, home), "state": wanted}
    out["for_min"] = _number(spec, "for_min", 0, MAX_EXPIRY_MIN, required=False) or 0
    return out


def _parse_changed(spec, now, home):
    return {
        "type": "changed",
        "entity_id": _entity(spec, home),
        "to": _text(spec, "to"),
        "within_min": _number(spec, "within_min", 1, MAX_EXPIRY_MIN),
    }


def _parse_numeric(spec, now, home):
    out = {"type": "numeric", "entity_id": _entity(spec, home)}
    if spec.get("attribute"):
        out["attribute"] = _text(spec, "attribute")
    out["below"] = _number(spec, "below", -1e9, 1e9, required=False)
    out["above"] = _number(spec, "above", -1e9, 1e9, required=False)
    if out["below"] is None and out["above"] is None:
        raise JobError("numeric: below or above is required")
    out["for_min"] = _number(spec, "for_min", 0, MAX_EXPIRY_MIN, required=False) or 0
    return out


def _parse_time(spec, now, home):
    if spec.get("at"):
        at = _text(spec, "at")
        if HHMM_RE.match(at):
            due = next_at(at, now)
        else:
            try:
                due = datetime.fromisoformat(at)
            except ValueError:
                raise JobError("time: at must be HH:MM or an ISO date-time") from None
            if due.tzinfo is None:
                due = due.replace(tzinfo=now.tzinfo)
        return {"type": "time", "due": due.isoformat()}
    minutes = _number(spec, "in_min", 0, MAX_EXPIRY_MIN)
    start = spec.get("from", "created")
    if start not in ("created", "armed"):
        raise JobError("time: from must be created or armed")
    if start == "armed":
        return {"type": "time", "in_min": minutes, "from": "armed"}
    return {"type": "time", "due": (now + timedelta(minutes=minutes)).isoformat()}


def _parse_time_window(spec, now, home):
    return {"type": "time_window", "from": _hhmm(spec, "from"), "to": _hhmm(spec, "to")}


def _parse_room_presence(spec, now, home):
    area = _text(spec, "area")
    entities = home.presence_sensors(area)
    if not entities:
        raise JobError(f"room_presence: no presence sensor in {area} yet")
    return {"type": "room_presence", "area": area, "entities": entities}


CONDITION_PARSERS = {
    "person": _parse_person,
    "state": _parse_state,
    "changed": _parse_changed,
    "numeric": _parse_numeric,
    "time": _parse_time,
    "time_window": _parse_time_window,
    "room_presence": _parse_room_presence,
}


def _allowed_entity(spec, home):
    kind = spec["type"]
    entity_id = _text(spec, "entity_id")
    if entity_id in HEATER_ENTITIES:
        raise JobError(f"{kind}: {entity_id} is a heater, use heat for heaters")
    if entity_id.split(".")[0] not in ALLOWED_DOMAINS[kind]:
        raise JobError(f"{kind}: {entity_id} can't be used, only {', '.join(sorted(ALLOWED_DOMAINS[kind]))}")
    if not home.exposed(entity_id):
        raise JobError(f"{kind}: {entity_id} is not exposed to the assistant")
    return entity_id


def _parse_heat(spec, home):
    return {
        "type": "heat",
        "device": _choice(spec, "device", HEAT_DEVICES),
        "minutes": _number(spec, "minutes", 1, MAX_LEASE_MIN),
    }


def _parse_turn_on(spec, home):
    out = {"type": "turn_on", "entity_id": _allowed_entity(spec, home)}
    minutes = _number(spec, "minutes", 1, MAX_LEASE_MIN, required=False)
    if minutes is not None:
        if out["entity_id"].startswith("scene."):
            raise JobError("turn_on: a scene can't be turned off, so no minutes for a scene")
        out["minutes"] = minutes
    return out


def _parse_turn_off(spec, home):
    return {"type": "turn_off", "entity_id": _allowed_entity(spec, home)}


def _parse_set_temperature(spec, home):
    return {
        "type": "set_temperature",
        "entity_id": _allowed_entity(spec, home),
        "temperature": _number(spec, "temperature", 5, 35),
    }


def _parse_notify(spec, home):
    return {"type": "notify", "message": _text(spec, "message")}


def _parse_announce(spec, home):
    area = _text(spec, "area", required=False)
    entities = home.satellites(area)
    if not entities:
        raise JobError("announce: no voice satellite yet, use notify")
    return {"type": "announce", "message": _text(spec, "message"), "area": area, "entities": entities}


ACTION_PARSERS = {
    "heat": _parse_heat,
    "turn_on": _parse_turn_on,
    "turn_off": _parse_turn_off,
    "set_temperature": _parse_set_temperature,
    "notify": _parse_notify,
    "announce": _parse_announce,
}


def _items(raw, key, low, high, parsers, parse):
    items = raw.get(key) or []
    if not isinstance(items, list) or not low <= len(items) <= high:
        raise JobError(f"{key} must be a list of {low} to {high} items")
    out = []
    for item in items:
        kind = item.get("type") if isinstance(item, dict) else None
        if kind not in parsers:
            raise JobError(f"{key}: unknown type {kind}, use one of {', '.join(parsers)}")
        out.append(parse(parsers[kind], item))
    return out


def parse_job(raw: str | dict, now: datetime, home: Home, job_id: str, active_count: int) -> Job:
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except ValueError as err:
            raise JobError(f"job is not valid JSON: {err}") from None
    if not isinstance(raw, dict):
        raise JobError("job must be a JSON object")
    if active_count >= MAX_ACTIVE:
        raise JobError(f"there are already {MAX_ACTIVE} active jobs, cancel one first")
    title = raw.get("title")
    if not isinstance(title, str) or not title.strip():
        raise JobError("title is required")

    def condition(parser, item):
        return parser(item, now, home)

    def action(parser, item):
        return parser(item, home)

    arm_when = _items(raw, "arm_when", 0, MAX_CONDITIONS, CONDITION_PARSERS, condition)
    run_when = _items(raw, "run_when", 1, MAX_CONDITIONS, CONDITION_PARSERS, condition)
    do = _items(raw, "do", 1, MAX_ACTIONS, ACTION_PARSERS, action)
    expiry = _number({"type": "job", **raw}, "expires_in_min", 1, MAX_EXPIRY_MIN, required=False)
    armed = not arm_when
    return Job(
        id=job_id,
        title=title.strip(),
        arm_when=arm_when,
        run_when=run_when,
        do=do,
        created=now,
        expires=now + timedelta(minutes=expiry or DEFAULT_EXPIRY_MIN),
        status="armed" if armed else "waiting_arm",
        armed_at=now if armed else None,
    )


# --- evaluation

UNKNOWN = ("unknown", "unavailable")


@dataclass(frozen=True)
class EntityState:
    state: str
    attributes: dict
    last_changed: datetime


StateLookup = Callable[[str], EntityState | None]


@dataclass
class Step:
    run: bool
    expired: bool
    next_check: datetime | None


def _held(match, since, for_min, now):
    """(true now, when it turns true) for a state that must hold for for_min"""
    if not match:
        return False, None
    ready = since + timedelta(minutes=for_min)
    return (True, None) if now >= ready else (False, ready)


def _earliest(moments):
    moments = [m for m in moments if m is not None]
    return min(moments) if moments else None


def _check_person(cond, job, now, states):
    who = cond["who"]
    names = list(PEOPLE) if who in ("anyone", "everyone") else [who]
    results = []
    for name in names:
        current = states(PEOPLE[name])
        if current is None or current.state in UNKNOWN:
            results.append((False, None))
            continue
        at_home = current.state == "home"
        match = at_home if cond["state"] == "home" else not at_home
        results.append(_held(match, current.last_changed, cond["for_min"], now))
    combine = any if who == "anyone" else all
    return combine(ok for ok, _ in results), _earliest(m for _, m in results)


def _check_state(cond, job, now, states):
    current = states(cond["entity_id"])
    if current is None or current.state in UNKNOWN:
        return False, None
    return _held(current.state in cond["state"], current.last_changed, cond["for_min"], now)


def _check_changed(cond, job, now, states):
    current = states(cond["entity_id"])
    if current is None or current.state != cond["to"]:
        return False, None
    until = current.last_changed + timedelta(minutes=cond["within_min"])
    # checked again when the window closes, so a job can't run on a stale change
    return (True, until) if now <= until else (False, None)


def _check_numeric(cond, job, now, states):
    current = states(cond["entity_id"])
    if current is None:
        return False, None
    raw = current.attributes.get(cond["attribute"]) if cond.get("attribute") else current.state
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return False, None
    match = (cond.get("below") is None or value < cond["below"]) and (
        cond.get("above") is None or value > cond["above"]
    )
    return _held(match, current.last_changed, cond["for_min"], now)


def _check_time(cond, job, now, states):
    if "due" not in cond:
        return False, None
    due = datetime.fromisoformat(cond["due"])
    return (True, None) if now >= due else (False, due)


def _check_time_window(cond, job, now, states):
    start, end = cond["from"], cond["to"]
    clock = now.strftime("%H:%M")
    inside = start <= clock < end if start <= end else (clock >= start or clock < end)
    return (True, next_at(end, now)) if inside else (False, next_at(start, now))


def _check_room_presence(cond, job, now, states):
    return any((states(e) or EntityState("off", {}, now)).state == "on" for e in cond["entities"]), None


CONDITION_CHECKS = {
    "person": _check_person,
    "state": _check_state,
    "changed": _check_changed,
    "numeric": _check_numeric,
    "time": _check_time,
    "time_window": _check_time_window,
    "room_presence": _check_room_presence,
}


def check(cond: dict, job: Job, now: datetime, states: StateLookup) -> tuple[bool, datetime | None]:
    return CONDITION_CHECKS[cond["type"]](cond, job, now, states)


def _all_true(conditions, job, now, states):
    results = [check(c, job, now, states) for c in conditions]
    return all(ok for ok, _ in results), _earliest(m for _, m in results)


def _arm(job, now):
    job.status = "armed"
    job.armed_at = now
    for cond in job.run_when:
        if cond["type"] == "time" and cond.get("from") == "armed":
            cond["due"] = (now + timedelta(minutes=cond["in_min"])).isoformat()


def advance(job: Job, now: datetime, states: StateLookup) -> Step:
    """Moves the job on as far as the states allow; Step.run means: run its actions now"""
    if job.status not in ("waiting_arm", "armed"):
        return Step(False, False, None)
    if now >= job.expires:
        job.status = "expired"
        job.finished_at = now
        return Step(False, True, None)
    if job.status == "waiting_arm":
        ok, next_check = _all_true(job.arm_when, job, now, states)
        if not ok:
            return Step(False, False, _earliest([next_check, job.expires]))
        _arm(job, now)
    ok, next_check = _all_true(job.run_when, job, now, states)
    if not ok:
        return Step(False, False, _earliest([next_check, job.expires]))
    job.status = "done"
    job.finished_at = now
    return Step(True, False, None)


def entities_of(job: Job) -> set[str]:
    """Entities whose changes can move the job on"""
    if job.status not in ("waiting_arm", "armed"):
        return set()
    conditions = job.arm_when if job.status == "waiting_arm" else job.run_when
    out = set()
    for cond in conditions:
        if cond["type"] == "person":
            out.update(PEOPLE.values() if cond["who"] in ("anyone", "everyone") else [PEOPLE[cond["who"]]])
        elif "entity_id" in cond:
            out.add(cond["entity_id"])
        out.update(cond.get("entities", []))
    return out


# --- leases and storage


@dataclass
class Lease:
    entity_id: str
    ends: datetime
    title: str
    job_id: str
    tries: int = 0


def add_lease(leases: list[Lease], new: Lease) -> list[Lease]:
    """One lease per entity; a new one may extend it, never shorten it"""
    old = next((lease for lease in leases if lease.entity_id == new.entity_id), None)
    if old is not None and old.ends >= new.ends:
        return leases
    return [lease for lease in leases if lease is not old] + [new]


def _times_to_text(data, keys):
    return {k: (v.isoformat() if k in keys and v is not None else v) for k, v in data.items()}


def _times_from_text(data, keys):
    return {k: (datetime.fromisoformat(v) if k in keys and v is not None else v) for k, v in data.items()}


JOB_TIMES = ("created", "expires", "armed_at", "finished_at")


def dump(jobs: list[Job], leases: list[Lease]) -> dict:
    return {
        "jobs": [_times_to_text(asdict(job), JOB_TIMES) for job in jobs],
        "leases": [_times_to_text(asdict(lease), ("ends",)) for lease in leases],
    }


def load(data: dict) -> tuple[list[Job], list[Lease]]:
    jobs = [Job(**_times_from_text(item, JOB_TIMES)) for item in data.get("jobs", [])]
    leases = [Lease(**_times_from_text(item, ("ends",))) for item in data.get("leases", [])]
    return jobs, leases


def is_active(job: Job) -> bool:
    return job.status in ("waiting_arm", "armed")


def prune(jobs: list[Job], now: datetime) -> list[Job]:
    """Finished jobs stay a day for the list and the dashboard"""
    return [job for job in jobs if is_active(job) or job.finished_at > now - KEEP_FINISHED]


# --- Russian texts for pushes, the list and the dashboard

DEVICE_NAMES = {
    "towel_master": "полотенцесушитель в большой ванной",
    "towel_guest": "полотенцесушитель в гостевой ванной",
    "floor_master": "тёплые полы в большой ванной",
    "floor_guest": "тёплые полы в гостевой ванной",
}
FINISHED_LABELS = {"done": "выполнено", "expired": "истекло", "cancelled": "отменено"}
LEAVE_LABELS = {
    "owner": "ждёт, пока ты уйдёшь",
    "wife": "ждёт, пока уйдёт жена",
    "anyone": "ждёт, пока кто-нибудь уйдёт",
    "everyone": "ждёт, пока все уйдут",
}
BACK_LABELS = {
    "owner": "ждёт, пока вернёшься",
    "wife": "ждёт, пока вернётся жена",
    "anyone": "ждёт, пока кто-нибудь вернётся",
    "everyone": "ждёт, пока все вернутся",
}


def duration_text(minutes) -> str:
    hours, rest = divmod(int(minutes), 60)
    if hours and rest:
        return f"{hours} ч {rest} мин"
    return f"{hours} ч" if hours else f"{rest} мин"


def step_label(job: Job) -> str:
    if not is_active(job):
        return FINISHED_LABELS[job.status]
    conditions = job.arm_when if job.status == "waiting_arm" else job.run_when
    for cond in conditions:
        if cond["type"] == "person":
            labels = LEAVE_LABELS if cond["state"] == "away" else BACK_LABELS
            return labels[cond["who"]]
    for cond in conditions:
        if cond["type"] == "time" and "due" in cond:
            return f"сработает в {datetime.fromisoformat(cond['due']).strftime('%H:%M')}"
    return "ждёт условий"


def describe_action(action: dict, name: str | None = None) -> str:
    """What the action did, for the push; name is the entity's friendly name when known"""
    label = f"«{name or action.get('entity_id')}»"
    kind = action["type"]
    if kind == "heat":
        return f"включил {DEVICE_NAMES[action['device']]} на {duration_text(action['minutes'])}"
    if kind == "turn_on":
        return f"включил {label}" + (f" на {duration_text(action['minutes'])}" if "minutes" in action else "")
    if kind == "turn_off":
        return f"выключил {label}"
    if kind == "set_temperature":
        return f"поставил {label} на {action['temperature']} °C"
    if kind == "notify":
        return f"напомнил: {action['message']}"
    return f"объявил: {action['message']}"


def needs_summary(job: Job) -> bool:
    """A job of only phone pushes (a timer, a reminder) is its own push"""
    return any(action["type"] != "notify" for action in job.do)


def push_text(job: Job, done: list[str], failed: list[str], lease_end: datetime | None) -> tuple[str, str]:
    message = f"{job.title}: {', '.join(done) or 'ничего'}"
    if lease_end is not None:
        message += f"; выключу в {lease_end.strftime('%H:%M')}"
    if failed:
        message += ". Не получилось: " + ", ".join(failed)
        return "Задание выполнено не полностью", message
    return "Задание выполнено", message
