"""Pure logic for the Google Health read: API points to sleep stages, workouts, heart rate and per-day summaries"""
import collections
import dataclasses
import datetime as dt
import typing

SLEEP_STAGES = {"DEEP": "deep", "REM": "rem", "LIGHT": "light", "AWAKE": "awake", "RESTLESS": "awake", "ASLEEP": "asleep"}
STAGED = {"deep", "rem", "light"}
AZM_ZONES = {"fatburn": "fat_burn"}
ZONE_FIELDS = {"lightTime": "light", "moderateTime": "moderate", "vigorousTime": "vigorous", "peakTime": "peak"}


@dataclasses.dataclass(frozen=True)
class SleepStage:
    start: dt.datetime
    end: dt.datetime
    stage: str


@dataclasses.dataclass(frozen=True)
class Workout:
    start: dt.datetime
    end: dt.datetime
    type: str
    name: str
    active_min: float
    kcal: typing.Optional[float]
    distance_m: typing.Optional[float]
    avg_hr: typing.Optional[float]
    steps: typing.Optional[int]
    zone_min: dict


class HealthSources(typing.NamedTuple):
    sleep: list
    workouts: list
    heart_rate: list
    resting_hr: dict
    hrv: dict
    azm: dict
    hr_zones: dict
    weight: list
    burned: dict
    burned_hours: list
    active: dict = {}


def _time(text):
    return dt.datetime.fromisoformat(text.replace("Z", "+00:00"))


def _seconds(text):
    return float(str(text).rstrip("s") or 0)


def _float(value):
    return None if value is None else float(value)


def _date(value):
    return dt.date(int(value["year"]), int(value["month"]), int(value["day"]))


def _ms(moment):
    return int(moment.timestamp() * 1000)


def parse_sleep(points):
    stages = []
    for point in points:
        sleep = point.get("sleep", {})
        listed = sleep.get("stages") or []
        if not listed:
            interval = sleep["interval"]
            listed = [{"startTime": interval["startTime"], "endTime": interval["endTime"], "type": "ASLEEP"}]
        for stage in listed:
            stages.append(SleepStage(_time(stage["startTime"]), _time(stage["endTime"]), SLEEP_STAGES.get(stage.get("type"), "asleep")))
    return sorted(stages, key=lambda s: s.start)


def parse_workouts(points):
    workouts = []
    for point in points:
        exercise = point["exercise"]
        summary = exercise.get("metricsSummary") or {}
        distance = _float(summary.get("distanceMillimeters"))
        zones = {name: round(_seconds(summary["heartRateZoneDurations"][key]) / 60, 1) for key, name in ZONE_FIELDS.items() if key in summary.get("heartRateZoneDurations", {})}
        workouts.append(
            Workout(
                start=_time(exercise["interval"]["startTime"]),
                end=_time(exercise["interval"]["endTime"]),
                type=exercise.get("exerciseType", ""),
                name=exercise.get("displayName", ""),
                active_min=round(_seconds(exercise.get("activeDuration", "0s")) / 60, 1),
                kcal=_float(summary.get("caloriesKcal")),
                distance_m=None if distance is None else round(distance / 1000, 1),
                avg_hr=_float(summary.get("averageHeartRateBeatsPerMinute")),
                steps=None if summary.get("steps") is None else int(summary["steps"]),
                zone_min=zones,
            )
        )
    return sorted(workouts, key=lambda w: w.start)


def parse_heart_rate(rows):
    """(minute in ms, mean bpm) from a heart-rate rollUp with 60 s windows"""
    return sorted((_ms(_time(row["startTime"])), round(float(row["heartRate"]["beatsPerMinuteAvg"]), 1)) for row in rows if "heartRate" in row)


def parse_daily(points, key):
    """{date: value} for daily types. The value field is the first number next to `date`, so HRV works whatever its name"""
    values = {}
    for point in points:
        body = point.get(key, {})
        number = next((v for k, v in body.items() if k != "date" and not isinstance(v, dict) and _is_number(v)), None)
        if "date" in body and number is not None:
            values[_date(body["date"])] = round(float(number), 1)
    return values


def _is_number(value):
    try:
        float(value)
        return True
    except (TypeError, ValueError):
        return False


def parse_daily_azm(rows):
    """{date: {zone: minutes}} from an active-zone-minutes dailyRollUp, zones without minutes left out"""
    days = {}
    for row in rows:
        sums = row.get("activeZoneMinutes", {})
        zones = {key[len("sumIn"):-len("HeartZone")].lower(): float(v) for key, v in sums.items() if key.startswith("sumIn") and float(v)}
        days[_date(row["civilStartTime"]["date"])] = {AZM_ZONES.get(zone, zone): v for zone, v in zones.items()}
    return days


def parse_daily_zones(rows):
    """{date: {zone: minutes}} from a time-in-heart-rate-zone dailyRollUp"""
    return {
        _date(row["civilStartTime"]["date"]): {
            zone["heartRateZone"].lower(): round(_seconds(zone["duration"]) / 60, 1) for zone in row.get("timeInHeartRateZone", {}).get("timeInHeartRateZones", [])
        }
        for row in rows
    }


def parse_weight(points):
    return sorted((_time(p["weight"]["sampleTime"]["physicalTime"]), float(p["weight"]["weightGrams"]) / 1000) for p in points)


def parse_rollup_days(rows, key="totalCalories"):
    """{date: kcal} from a total-calories or active-energy-burned dailyRollUp"""
    return {_date(row["civilStartTime"]["date"]): round(float(row[key]["kcalSum"]), 1) for row in rows if key in row}


def parse_rollup_hours(rows):
    return sorted((_ms(_time(row["startTime"])), round(float(row["totalCalories"]["kcalSum"]), 1)) for row in rows if "totalCalories" in row)


def _midnight(day, tz):
    return dt.datetime.combine(day, dt.time(), tz)


def _split_by_day(stages, tz):
    """Each stage cut at local midnights, {date: [SleepStage]}"""
    days = collections.defaultdict(list)
    for stage in stages:
        day = stage.start.astimezone(tz).date()
        while _midnight(day, tz) < stage.end:
            start = max(stage.start, _midnight(day, tz))
            end = min(stage.end, _midnight(day + dt.timedelta(days=1), tz))
            if end > start:
                days[day].append(SleepStage(start, end, stage.stage))
            day += dt.timedelta(days=1)
    return days


def _sleep_summary(stages, tz):
    minutes = collections.defaultdict(float)
    for stage in stages:
        minutes[stage.stage] += (stage.end - stage.start).total_seconds() / 60
    summary = {
        "start": min(s.start for s in stages).astimezone(tz).isoformat(),
        "end": max(s.end for s in stages).astimezone(tz).isoformat(),
        "staged": any(s.stage in STAGED for s in stages),
    }
    for name in ("deep", "rem", "light", "awake", "asleep"):
        summary[f"{name}_min"] = round(minutes[name])
    return summary


def _workout_dict(workout, as_ms, tz):
    out = dataclasses.asdict(workout)
    for key in ("start", "end"):
        out[key] = _ms(out[key]) if as_ms else out[key].astimezone(tz).isoformat()
    return out


def day_summaries(sources, tz):
    days = collections.defaultdict(dict)
    for day, stages in _split_by_day(sources.sleep, tz).items():
        days[day]["sleep"] = _sleep_summary(stages, tz)
    for workout in sources.workouts:
        day = days[workout.start.astimezone(tz).date()]
        day.setdefault("workouts", []).append(_workout_dict(workout, as_ms=False, tz=tz))
        day["exercise_min"] = round(day.get("exercise_min", 0) + workout.active_min, 1)
    weights = collections.defaultdict(list)
    for moment, kg in sources.weight:
        weights[moment.astimezone(tz).date()].append(kg)
    for day, values in weights.items():
        days[day]["weight_kg"] = round(sum(values) / len(values), 1)
    for key, values in (("resting_hr", sources.resting_hr), ("hrv_ms", sources.hrv), ("burned_kcal", sources.burned), ("active_kcal", sources.active)):
        for day, value in values.items():
            days[day][key] = value
    for day, zones in sources.azm.items():
        days[day]["azm"] = {"total": round(sum(zones.values()), 1), **zones}
    for day, zones in sources.hr_zones.items():
        days[day]["hr_zone_min"] = dict(zones)
    return {day.isoformat(): values for day, values in sorted(days.items())}


def day_detail(sources, day, tz):
    start, end = _ms(_midnight(day, tz)), _ms(_midnight(day + dt.timedelta(days=1), tz))
    inside = lambda ms: start <= ms < end
    return {
        "heart_rate": [[ms, bpm] for ms, bpm in sources.heart_rate if inside(ms)],
        "burned": [[ms, kcal] for ms, kcal in sources.burned_hours if inside(ms)],
        "sleep": [{"from": _ms(s.start), "to": _ms(s.end), "stage": s.stage} for s in _split_by_day(sources.sleep, tz).get(day, [])],
        "workouts": [_workout_dict(w, as_ms=True, tz=tz) for w in sources.workouts if inside(_ms(w.start))],
        "weight": [[_ms(moment), kg] for moment, kg in sources.weight if inside(_ms(moment))],
    }
