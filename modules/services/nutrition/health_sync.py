"""Reads wellness data from Google Health and writes the JSON files for the Wellness charts and the assistant"""
import datetime as dt
import json
import logging
import time
import urllib.error
import urllib.parse

import health_core as hc

LOG = logging.getLogger("nutrition-sync")
ROLLUP_DAYS = 14
LIST_PAGE = 25
FIRST_DAYS = 365
DETAIL_DAYS = 31
RECENT_DAYS = 3
RETRIES = 4
# Google answers 503 now and then, a retry a bit later usually works
RETRY_WAIT = (5, 15, 30)


class HealthError(Exception):
    def __init__(self, kind, reason):
        super().__init__(f"{kind}: {reason}")


def _z(moment):
    return moment.astimezone(dt.timezone.utc).isoformat().replace("+00:00", "Z")


def _date_body(day):
    return {"date": {"year": day.year, "month": day.month, "day": day.day}}


def _retry_after(error, default):
    headers = {key.lower(): value for key, value in (getattr(error, "headers", None) or {}).items()}
    try:
        return min(float(headers.get("retry-after", default)), 60)
    except ValueError:
        return default


class HealthReader:
    """Uses the meal push's GoogleClient, so both share one login and access token"""

    def __init__(self, client, tz):
        self.client = client
        self.tz = tz

    def _request(self, kind, suffix, body=None):
        url = self.client._root() + "users/me/dataTypes/" + kind + suffix
        for attempt in range(RETRIES):
            try:
                return self.client._call("POST" if body is not None else "GET", url, body)
            except Exception as error:
                status = getattr(error, "status", None)
                if (status == 429 or (status or 0) >= 500) and attempt < RETRIES - 1:
                    time.sleep(_retry_after(error, RETRY_WAIT[attempt]))
                    continue
                if status is not None:
                    # e.g. "400 invalid_grant" when the login is gone
                    raise HealthError(kind, f"{status} {getattr(error, 'reason', '')}".strip()) from None
                raise HealthError(kind, type(error).__name__) from None

    def list(self, kind, flt, page_size=1000):
        points, page = [], None
        while True:
            query = {"pageSize": page_size, "filter": flt}
            if page:
                query["pageToken"] = page
            answer = self._request(kind, "/dataPoints?" + urllib.parse.urlencode(query))
            points += answer.get("dataPoints", [])
            page = answer.get("nextPageToken")
            if not page:
                return points

    def daily(self, kind, start, end):
        """dailyRollUp rows for start..end (both included), in pieces of 14 days, Google's limit"""
        rows, piece = [], start
        while piece <= end:
            last = min(piece + dt.timedelta(days=ROLLUP_DAYS - 1), end)
            body = {"range": {"start": _date_body(piece), "end": _date_body(last + dt.timedelta(days=1))}}
            rows += self._request(kind, "/dataPoints:dailyRollUp", body).get("rollupDataPoints", [])
            piece = last + dt.timedelta(days=1)
        return rows

    def rollup(self, kind, day, window):
        """rollUp rows in windows of `window` seconds over one local day"""
        start = dt.datetime.combine(day, dt.time(), self.tz)
        end = dt.datetime.combine(day + dt.timedelta(days=1), dt.time(), self.tz)
        rows, page = [], None
        while True:
            body = {"range": {"startTime": _z(start), "endTime": _z(end)}, "windowSize": f"{window}s"}
            if page:
                body["pageToken"] = page
            answer = self._request(kind, "/dataPoints:rollUp", body)
            rows += answer.get("rollupDataPoints", [])
            page = answer.get("nextPageToken")
            if not page:
                return rows

    def read(self, start, end, detail_start):
        """Summaries for start..end, and heart rate and hourly burn for detail_start..end"""
        since = f'"{start.isoformat()}T00:00:00"'
        detail_days = [detail_start + dt.timedelta(days=i) for i in range((end - detail_start).days + 1)]
        heart, hours = [], []
        for day in detail_days:
            heart += self.rollup("heart-rate", day, 60)
            hours += self.rollup("total-calories", day, 3600)
        return hc.HealthSources(
            # A night that ends on start began the day before
            sleep=hc.parse_sleep(self.list("sleep", f'sleep.interval.civil_end_time >= {since}', LIST_PAGE)),
            workouts=hc.parse_workouts(self.list("exercise", f"exercise.interval.civil_start_time >= {since}", LIST_PAGE)),
            heart_rate=hc.parse_heart_rate(heart),
            resting_hr=hc.parse_daily(self.list("daily-resting-heart-rate", f'daily_resting_heart_rate.date >= "{start.isoformat()}"'), "dailyRestingHeartRate"),
            hrv=hc.parse_daily(self.list("daily-heart-rate-variability", f'daily_heart_rate_variability.date >= "{start.isoformat()}"'), "dailyHeartRateVariability"),
            azm=hc.parse_daily_azm(self.daily("active-zone-minutes", start, end)),
            hr_zones=hc.parse_daily_zones(self.daily("time-in-heart-rate-zone", start, end)),
            weight=hc.parse_weight(self.list("weight", f"weight.sample_time.civil_time >= {since}")),
            burned=hc.parse_rollup_days(self.daily("total-calories", start, end)),
            burned_hours=hc.parse_rollup_hours(hours),
            active=hc.parse_rollup_days(self.daily("active-energy-burned", start, end), "activeEnergyBurned"),
        )


def sync_health(cfg, state, client, now, write):
    """First run reads a year of summaries and 31 days of detail, later runs the last 3 days again, since data comes late"""
    folder = cfg.state_dir / "www" / "health"
    days_file = folder / "days.json"
    saved = json.loads(days_file.read_text()) if days_file.exists() else None
    old = saved["days"] if saved else None
    today = now.astimezone(cfg.tz).date()
    start = today - dt.timedelta(days=(FIRST_DAYS if old is None else RECENT_DAYS) - 1)
    if saved and saved.get("updated"):
        # After a stop, read from the last good run, so the gap fills in
        last = dt.datetime.fromisoformat(saved["updated"]).astimezone(cfg.tz).date()
        start = max(min(start, last), today - dt.timedelta(days=FIRST_DAYS - 1))
    detail_start = max(start, today - dt.timedelta(days=DETAIL_DAYS - 1))
    try:
        sources = HealthReader(client, cfg.tz).read(start, today, detail_start)
    except HealthError as error:
        LOG.warning("Google Health read failed: %s", error)
        state.status["health"] = {**state.status.get("health", {}), "ok": False, "error": str(error)}
        return
    days = {day: values for day, values in (old or {}).items() if day < start.isoformat()}
    # A night that ends on start also gives the day before a part; that day is not fully read, so it keeps its old entry
    days.update({day: values for day, values in hc.day_summaries(sources, cfg.tz).items() if day >= start.isoformat()})
    write(days_file, json.dumps({"updated": now.isoformat(), "days": days}, ensure_ascii=False))
    day = detail_start
    while day <= today:
        write(folder / "day" / f"{day.isoformat()}.json", json.dumps(hc.day_detail(sources, day, cfg.tz)))
        day += dt.timedelta(days=1)
    oldest = (today - dt.timedelta(days=DETAIL_DAYS - 1)).isoformat()
    for path in (folder / "day").glob("*.json"):
        if path.stem < oldest:
            path.unlink()
    weighed = [(day, values["weight_kg"]) for day, values in sorted(days.items()) if "weight_kg" in values]
    state.status["health"] = {"ok": True, "error": None, "last_read": now.isoformat()}
    if weighed:
        state.status["latest"] = {"weight_kg": weighed[-1][1], "weight_day": weighed[-1][0]}


def wellness(state_dir, eaten, today, days):
    """Up to 90 days of everything for the assistant, the newest last"""
    days = max(1, min(int(days), 90))
    path = state_dir / "www" / "health" / "days.json"
    health = json.loads(path.read_text()) if path.exists() else {"updated": None, "days": {}}
    out = {}
    for i in range(days - 1, -1, -1):
        day = (today - dt.timedelta(days=i)).isoformat()
        values = dict(health["days"].get(day, {}))
        if day in eaten:
            values["eaten"] = eaten[day]
        if values:
            out[day] = values
    return {"updated": health["updated"], "days": out}
