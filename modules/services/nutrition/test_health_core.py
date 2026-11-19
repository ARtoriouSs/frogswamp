import datetime as dt
import zoneinfo

import health_core as hc

TZ = zoneinfo.ZoneInfo("Europe/Warsaw")
UTC = dt.timezone.utc


def iso(moment):
    return moment.astimezone(UTC).isoformat().replace("+00:00", "Z")


def local(*args):
    return dt.datetime(*args, tzinfo=TZ)


def sleep_point(start, end, kind="STAGES", stages=()):
    return {
        "sleep": {
            "interval": {"startTime": iso(start), "endTime": iso(end)},
            "type": kind,
            "stages": [{"startTime": iso(a), "endTime": iso(b), "type": t} for a, b, t in stages],
            "metadata": {"nap": False},
        }
    }


def workout_point(start, end, **summary):
    return {
        "exercise": {
            "interval": {"startTime": iso(start), "endTime": iso(end)},
            "exerciseType": "WALKING",
            "displayName": "Walk",
            "activeDuration": "1200s",
            "metricsSummary": summary,
        }
    }


def interval_point(key, start, minutes=1, **fields):
    return {key: {"interval": {"startTime": iso(start), "endTime": iso(start + dt.timedelta(minutes=minutes))}, **fields}}


def sources(**kwargs):
    values = dict(sleep=[], workouts=[], heart_rate=[], resting_hr={}, hrv={}, azm={}, hr_zones={}, weight=[], burned={}, burned_hours=[])
    values.update(kwargs)
    return hc.HealthSources(**values)


def test_classic_session_is_one_asleep_stage():
    stages = hc.parse_sleep([sleep_point(local(2026, 11, 17, 23), local(2026, 11, 18, 6), kind="CLASSIC")])
    assert stages == [hc.SleepStage(local(2026, 11, 17, 23), local(2026, 11, 18, 6), "asleep")]


def test_restless_counts_as_awake():
    start = local(2026, 11, 17, 23)
    point = sleep_point(start, start + dt.timedelta(hours=1), kind="CLASSIC", stages=[(start, start + dt.timedelta(minutes=50), "ASLEEP"), (start + dt.timedelta(minutes=50), start + dt.timedelta(hours=1), "RESTLESS")])
    assert [s.stage for s in hc.parse_sleep([point])] == ["asleep", "awake"]


def test_stages_split_at_midnight():
    start, end = local(2026, 11, 17, 23), local(2026, 11, 18, 7)
    stages = [(start, local(2026, 11, 17, 23, 30), "LIGHT"), (local(2026, 11, 17, 23, 30), local(2026, 11, 18, 0, 30), "DEEP"), (local(2026, 11, 18, 0, 30), end, "REM")]
    days = hc.day_summaries(sources(sleep=hc.parse_sleep([sleep_point(start, end, stages=stages)])), TZ)
    first, second = days["2026-11-17"]["sleep"], days["2026-11-18"]["sleep"]
    assert first["deep_min"] == 30 and second["deep_min"] == 30
    assert first["light_min"] + first["deep_min"] + second["deep_min"] + second["rem_min"] == 480
    assert first["staged"] and second["staged"]


def test_split_on_clock_change_night():
    # 2026-10-25 has 25 hours in Warsaw
    start, end = local(2026, 10, 24, 22), local(2026, 10, 26, 0)
    days = hc.day_summaries(sources(sleep=hc.parse_sleep([sleep_point(start, end, kind="CLASSIC")])), TZ)
    assert days["2026-10-24"]["sleep"]["asleep_min"] == 120
    assert days["2026-10-25"]["sleep"]["asleep_min"] == 25 * 60
    assert not days["2026-10-25"]["sleep"]["staged"]


def test_heart_rate_per_minute_mean():
    rows = [{"startTime": "2026-11-18T10:01:00Z", "heartRate": {"beatsPerMinuteAvg": 98.214}}, {"startTime": "2026-11-18T10:00:00Z", "heartRate": {"beatsPerMinuteAvg": 65}}, {"startTime": "2026-11-18T10:02:00Z"}]
    minute = int(dt.datetime(2026, 11, 18, 10, tzinfo=UTC).timestamp() * 1000)
    assert hc.parse_heart_rate(rows) == [(minute, 65.0), (minute + 60000, 98.2)]


def test_workout_fields_and_missing_metrics_are_none():
    start = local(2026, 11, 17, 20)
    full = workout_point(start, start + dt.timedelta(minutes=25), caloriesKcal=179, distanceMillimeters=1369200, steps="2072", averageHeartRateBeatsPerMinute="109", heartRateZoneDurations={"lightTime": "1380s", "moderateTime": "60s"})
    bare = workout_point(start, start + dt.timedelta(minutes=10))
    first, second = hc.parse_workouts([full, bare])
    assert first == hc.Workout(start, start + dt.timedelta(minutes=25), "WALKING", "Walk", 20.0, 179.0, 1369.2, 109.0, 2072, {"light": 23.0, "moderate": 1.0})
    assert (second.kcal, second.distance_m, second.avg_hr, second.steps, second.zone_min) == (None, None, None, None, {})


def test_weight_day_average():
    points = [{"weight": {"sampleTime": {"physicalTime": iso(t)}, "weightGrams": g}} for t, g in [(local(2026, 11, 16, 8), 86000), (local(2026, 11, 16, 20), 86500)]]
    days = hc.day_summaries(sources(weight=hc.parse_weight(points)), TZ)
    assert days["2026-11-16"]["weight_kg"] == 86.2


def test_day_without_sleep_has_no_sleep_key():
    days = hc.day_summaries(sources(resting_hr={dt.date(2026, 11, 18): 84.0}), TZ)
    assert days == {"2026-11-18": {"resting_hr": 84.0}}


def test_daily_values_zones_and_rollups():
    resting = hc.parse_daily([{"dailyRestingHeartRate": {"date": {"year": 2026, "month": 11, "day": 18}, "beatsPerMinute": "84"}}], "dailyRestingHeartRate")
    assert resting == {dt.date(2026, 11, 18): 84.0}
    day = {"civilStartTime": {"date": {"year": 2026, "month": 11, "day": 18}}}
    azm = hc.parse_daily_azm([{**day, "activeZoneMinutes": {"sumInCardioHeartZone": "2", "sumInPeakHeartZone": "0", "sumInFatBurnHeartZone": "1"}}])
    assert azm == {dt.date(2026, 11, 18): {"fat_burn": 1.0, "cardio": 2.0}}
    zones = hc.parse_daily_zones([{**day, "timeInHeartRateZone": {"timeInHeartRateZones": [{"heartRateZone": "LIGHT", "duration": "60s"}]}}])
    assert zones == {dt.date(2026, 11, 18): {"light": 1.0}}
    assert hc.parse_rollup_days([{**day, "activeEnergyBurned": {"kcalSum": 665.62}}], "activeEnergyBurned") == {dt.date(2026, 11, 18): 665.6}
    rows = [{"civilStartTime": {"date": {"year": 2026, "month": 11, "day": 17}}, "totalCalories": {"kcalSum": 2968.41}}]
    assert hc.parse_rollup_days(rows) == {dt.date(2026, 11, 17): 2968.4}
    hours = hc.parse_rollup_hours([{"startTime": "2026-11-18T01:00:00Z", "totalCalories": {"kcalSum": 78.02}}, {"startTime": "2026-11-18T00:00:00Z", "totalCalories": {"kcalSum": 70.0}}])
    assert [kcal for _, kcal in hours] == [70.0, 78.0]
    days = hc.day_summaries(sources(azm=azm, hr_zones=zones), TZ)
    assert days["2026-11-18"]["azm"] == {"total": 3.0, "fat_burn": 1.0, "cardio": 2.0}
    assert days["2026-11-18"]["hr_zone_min"] == {"light": 1.0}


def test_hrv_takes_the_number_whatever_its_name():
    points = [{"dailyHeartRateVariability": {"date": {"year": 2026, "month": 11, "day": 18}, "averageHeartRateVariabilityMilliseconds": 41.26}}]
    assert hc.parse_daily(points, "dailyHeartRateVariability") == {dt.date(2026, 11, 18): 41.3}


def test_day_detail_keeps_only_that_day():
    start = local(2026, 11, 17, 23)
    stages = hc.parse_sleep([sleep_point(start, local(2026, 11, 18, 7), kind="CLASSIC")])
    morning = int(local(2026, 11, 18, 9).timestamp() * 1000)
    evening_before = int(local(2026, 11, 17, 21).timestamp() * 1000)
    walk = hc.parse_workouts([workout_point(local(2026, 11, 18, 10), local(2026, 11, 18, 10, 30))])
    detail = hc.day_detail(sources(sleep=stages, heart_rate=[(evening_before, 70.0), (morning, 80.0)], workouts=walk, burned_hours=[(morning, 90.0)]), dt.date(2026, 11, 18), TZ)
    assert detail["heart_rate"] == [[morning, 80.0]]
    assert detail["sleep"] == [{"from": int(local(2026, 11, 18).timestamp() * 1000), "to": int(local(2026, 11, 18, 7).timestamp() * 1000), "stage": "asleep"}]
    assert detail["burned"] == [[morning, 90.0]]
    assert detail["workouts"][0]["type"] == "WALKING" and detail["workouts"][0]["start"] == int(local(2026, 11, 18, 10).timestamp() * 1000)
