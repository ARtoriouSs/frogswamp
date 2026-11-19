#!/usr/bin/env python3
"""Reads the meals from Notion, writes the daily totals CSV for HA, serves /status, and mirrors each meal to Google Health.

Only reaches out: Notion and Google are polled, nothing on the internet calls this service.
Tokens and meal names never reach the logs, only counts, HTTP codes and Notion page ids.
"""
import argparse
import datetime as dt
import http.server
import json
import logging
import os
import pathlib
import threading
import time
import typing
import urllib.error
import urllib.parse
import urllib.request
import zoneinfo

import health_sync as hs
import nutrition_core as nc

LOG = logging.getLogger("nutrition-sync")
NOTION_VERSION = "2025-09-03"
DELETE_CHUNK = 50
OPERATION_TRIES = 5


class GoogleConfig(typing.NamedTuple):
    api_url: str
    token_url: str
    client_id: str
    client_secret: str
    refresh_token: str


class Config(typing.NamedTuple):
    notion_url: str
    notion_token: str
    meals_source: str
    days_source: str
    google: typing.Optional[GoogleConfig]
    tz: zoneinfo.ZoneInfo
    state_dir: pathlib.Path


class HttpError(Exception):
    def __init__(self, status, body, headers=None):
        super().__init__(f"HTTP {status}")
        self.status = status
        self.body = body
        self.headers = headers or {}

    @property
    def reason(self):
        """A short code for /status, never the whole answer"""
        error = self.body.get("error") if isinstance(self.body, dict) else None
        if isinstance(error, str):
            return error
        if isinstance(error, dict) and error.get("status"):
            return error["status"]
        return f"HTTP {self.status}"


class AuthError(HttpError):
    """The Google login itself failed, so nothing can be sent until it is renewed"""


class OperationPending(Exception):
    def __init__(self, name):
        super().__init__("operation not done")
        self.name = name


def request_json(method, url, headers=None, body=None, form=None):
    headers = dict(headers or {})
    data = None
    if body is not None:
        data = json.dumps(body).encode()
        headers["Content-Type"] = "application/json"
    elif form is not None:
        data = urllib.parse.urlencode(form).encode()
    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.load(response)
    except urllib.error.HTTPError as error:
        try:
            answer = json.loads(error.read() or b"{}")
        except ValueError:
            answer = {}
        raise HttpError(error.code, answer, dict(error.headers)) from None


def notion_query(base, token, source_id):
    headers = {"Authorization": f"Bearer {token}", "Notion-Version": NOTION_VERSION}
    url = f"{base}/v1/data_sources/{source_id}/query"
    results, cursor = [], None
    while True:
        body = {"page_size": 100}
        if cursor:
            body["start_cursor"] = cursor
        try:
            answer = request_json("POST", url, headers, body=body)
        except HttpError as error:
            if error.status != 429:
                raise
            # Notion allows about 3 requests a second; one wait is enough at 5 minutes between reads
            time.sleep(min(float(error.headers.get("Retry-After") or 1), 60))
            answer = request_json("POST", url, headers, body=body)
        results.extend(answer.get("results", []))
        if not answer.get("has_more"):
            return results
        cursor = answer.get("next_cursor")


def _z(moment):
    return moment.astimezone(dt.timezone.utc).isoformat().replace("+00:00", "Z")


def google_body(meal, tz):
    start = meal.eaten
    # Google wants the end strictly after the start
    end = start + dt.timedelta(minutes=1)
    offset = f"{int(start.astimezone(tz).utcoffset().total_seconds())}s"
    return {
        "nutritionLog": {
            "interval": {"startTime": _z(start), "startUtcOffset": offset, "endTime": _z(end), "endUtcOffset": offset},
            "foodDisplayName": meal.product or "Еда",
            "mealType": nc.google_meal_type(meal.meal),
            "energy": {"kcal": meal.kcal},
            "totalCarbohydrate": {"grams": meal.carbs},
            "totalFat": {"grams": meal.fat},
            "nutrients": [{"nutrient": "PROTEIN", "quantity": {"grams": meal.protein}}],
        }
    }


class GoogleClient:
    def __init__(self, cfg, tz):
        self.cfg = cfg
        self.tz = tz
        self._token = None
        self._expires = 0.0

    def _access_token(self):
        if self._token and time.monotonic() < self._expires:
            return self._token
        try:
            answer = self._refresh()
        except HttpError as error:
            raise AuthError(error.status, error.body) from None
        self._token = answer["access_token"]
        self._expires = time.monotonic() + float(answer.get("expires_in", 3600)) - 60
        return self._token

    def _refresh(self):
        return request_json(
            "POST",
            self.cfg.token_url,
            form={
                "client_id": self.cfg.client_id,
                "client_secret": self.cfg.client_secret,
                "refresh_token": self.cfg.refresh_token,
                "grant_type": "refresh_token",
            },
        )

    def _headers(self):
        return {"Authorization": f"Bearer {self._access_token()}"}

    def create(self, meal):
        """The new point's name; OperationPending when Google hasn't finished it yet"""
        answer = self._call("POST", self.cfg.api_url, google_body(meal, self.tz))
        for _ in range(OPERATION_TRIES):
            if answer.get("done"):
                return answer["response"]["name"]
            time.sleep(1)
            answer = self._call("GET", self._root() + answer["name"])
        raise OperationPending(answer["name"])

    def resolve(self, operation):
        """The point's name once the operation is done, else None"""
        answer = self._call("GET", self._root() + operation)
        return answer["response"]["name"] if answer.get("done") else None

    def delete(self, names):
        """Deletes the points; a 404 means they are gone already"""
        try:
            self._call("POST", self.cfg.api_url + ":batchDelete", {"names": names})
        except HttpError as error:
            if error.status != 404:
                raise
            # One missing point may fail the whole batch, so the others are tried one by one
            if len(names) > 1:
                for name in names:
                    self.delete([name])

    def _root(self):
        return self.cfg.api_url.split("/v4/")[0] + "/v4/"

    def _call(self, method, url, body=None):
        try:
            return request_json(method, url, self._headers(), body=body)
        except HttpError as error:
            if error.status in (401, 403) and not isinstance(error, AuthError):
                raise AuthError(error.status, error.body) from None
            raise


class State:
    def __init__(self, state_dir, synced):
        self.state_dir = pathlib.Path(state_dir)
        self.synced = synced
        self.status = {
            "last_read": None,
            "skipped": 0,
            "today": {"kcal": 0, "fats": 0, "proteins": 0, "carbohydrates": 0, "water": 0},
            "google": {"ok": True, "last_push": None, "error": None, "pending": 0, "rejected": 0},
            "health": {"ok": None, "error": None, "last_read": None},
        }
        self.google = None
        self.eaten = {}

    @classmethod
    def load(cls, state_dir):
        path = pathlib.Path(state_dir) / "google.json"
        synced = json.loads(path.read_text()) if path.exists() else {}
        return cls(state_dir, synced)

    def save_synced(self):
        _write_atomic(self.state_dir / "google.json", json.dumps(self.synced, indent=1))


def _write_atomic(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text)
    os.chmod(tmp, 0o644)
    os.replace(tmp, path)


def _push_google(cfg, state, meals, now):
    if cfg.google is None:
        return
    if state.google is None:
        state.google = GoogleClient(cfg.google, cfg.tz)
    pending, rejected, reject_reason = 0, 0, None
    try:
        for page_id, known in list(state.synced.items()):
            if "operation" in known:
                name = state.google.resolve(known["operation"])
                if name:
                    state.synced[page_id] = {"name": name, "hash": known["hash"]}
                    state.save_synced()
        plan = nc.plan_google(meals, state.synced, notion_ok=True)
        if plan.skipped_mass_delete:
            LOG.warning("Notion returned no meals while %d are in Google, not deleting them", len(state.synced))
        pending = len(plan.create) + len(plan.delete)
        by_name = {known["name"]: page_id for page_id, known in state.synced.items() if "name" in known}
        for start in range(0, len(plan.delete), DELETE_CHUNK):
            names = plan.delete[start : start + DELETE_CHUNK]
            state.google.delete(names)
            for name in names:
                state.synced.pop(by_name.get(name), None)
            state.save_synced()
            pending -= len(names)
        for meal in plan.create:
            pending -= 1
            try:
                name = state.google.create(meal)
            except OperationPending as waiting:
                # Kept, so the next read picks the result up instead of sending the meal twice
                state.synced[meal.id] = {"operation": waiting.name, "hash": nc.meal_hash(meal)}
                state.save_synced()
                continue
            except AuthError:
                pending += 1
                raise
            except HttpError as error:
                if not 400 <= error.status < 500:
                    pending += 1
                    raise
                # Google refuses this one meal; the others still go
                rejected += 1
                reject_reason = error.reason
                LOG.warning("Google rejected meal %s: %s", meal.id, error.reason)
                continue
            state.synced[meal.id] = {"name": name, "hash": nc.meal_hash(meal)}
            state.save_synced()
            LOG.info("Sent meal %s to Google", meal.id)
    except (HttpError, urllib.error.URLError, OSError, KeyError) as error:
        reason = error.reason if isinstance(error, HttpError) else type(error).__name__
        LOG.warning("Google Health failed: %s, %d changes wait", reason, pending)
        state.status["google"].update(ok=False, error=str(reason), pending=pending)
        return
    state.status["google"].update(
        ok=rejected == 0, error=reject_reason, pending=0, rejected=rejected, last_push=now.isoformat()
    )


def sync_once(cfg, state, now=None):
    now = now or dt.datetime.now(cfg.tz)
    try:
        meal_pages = notion_query(cfg.notion_url, cfg.notion_token, cfg.meals_source)
        day_pages = notion_query(cfg.notion_url, cfg.notion_token, cfg.days_source)
    except (HttpError, urllib.error.URLError, OSError) as error:
        # Without a good read nothing changes, so a Notion outage can't delete anything in Google
        LOG.warning("Couldn't read Notion: %s", error.reason if isinstance(error, HttpError) else type(error).__name__)
        return
    meals, skipped = nc.parse_meals(meal_pages, cfg.tz)
    today_date = nc.meal_day(now, cfg.tz)
    days = nc.daily_totals(meals, nc.parse_day_flags(day_pages), cfg.tz)
    _write_atomic(cfg.state_dir / "www" / "daily.csv", nc.to_csv(days))
    _write_atomic(cfg.state_dir / "www" / "meals.csv", nc.meals_csv(meals, cfg.tz))
    today = next((d for d in days if d.day == today_date), None)
    state.status.update(
        last_read=now.isoformat(),
        skipped=skipped,
        today={
            "kcal": today.kcal if today else 0,
            "fats": today.fat if today else 0,
            "proteins": today.protein if today else 0,
            "carbohydrates": today.carbs if today else 0,
            "water": today.water if today else 0,
        },
    )
    state.eaten = {
        d.day.isoformat(): {"kcal": d.kcal, "fats": d.fat, "proteins": d.protein, "carbohydrates": d.carbs, "water_ml": d.water, "full": d.full}
        for d in days
    }
    if skipped:
        LOG.info("Skipped %d rows without Kcal or Eaten", skipped)
    _push_google(cfg, state, meals, now)
    if state.google is not None:
        try:
            hs.sync_health(cfg, state, state.google, now, _write_atomic)
        except Exception as error:
            # The meals are done already, a health bug must not stop them next time
            LOG.exception("Google Health read crashed")
            state.status["health"] = {**state.status["health"], "ok": False, "error": type(error).__name__}


def serve(state, port, cfg=None):
    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            url = urllib.parse.urlparse(self.path)
            if url.path == "/status":
                answer = state.status
            elif url.path == "/wellness" and cfg is not None:
                try:
                    days = int(urllib.parse.parse_qs(url.query).get("days", ["30"])[0])
                except ValueError:
                    days = 30
                answer = hs.wellness(cfg.state_dir, state.eaten, dt.datetime.now(cfg.tz).date(), days)
            else:
                self.send_error(404)
                return
            data = json.dumps(answer, ensure_ascii=False).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, *args):
            pass

    server = http.server.ThreadingHTTPServer(("127.0.0.1", port), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--credentials", required=True, help="Directory with the credential files")
    parser.add_argument("--state-dir", required=True)
    parser.add_argument("--port", type=int, default=8131)
    parser.add_argument("--timezone", default="Europe/Warsaw")
    parser.add_argument("--interval", type=int, default=300)
    parser.add_argument("--notion-url", default="https://api.notion.com")
    parser.add_argument("--google-api-url", default="https://health.googleapis.com/v4/users/me/dataTypes/nutrition-log/dataPoints")
    parser.add_argument("--google-token-url", default="https://oauth2.googleapis.com/token")
    parser.add_argument("--no-google", action="store_true")
    return parser.parse_args(argv)


def read_config(args):
    creds = pathlib.Path(args.credentials)

    def secret(name):
        return (creds / name).read_text().strip()

    google = None
    if not args.no_google:
        google = GoogleConfig(
            api_url=args.google_api_url,
            token_url=args.google_token_url,
            client_id=secret("google_client_id"),
            client_secret=secret("google_client_secret"),
            refresh_token=secret("google_refresh_token"),
        )
    return Config(
        notion_url=args.notion_url,
        notion_token=secret("notion_token"),
        meals_source=secret("notion_meals_source"),
        days_source=secret("notion_days_source"),
        google=google,
        tz=zoneinfo.ZoneInfo(args.timezone),
        state_dir=pathlib.Path(args.state_dir),
    )


def main():
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    args = parse_args()
    cfg = read_config(args)
    state = State.load(cfg.state_dir)
    serve(state, args.port, cfg)
    LOG.info("Serving /status on 127.0.0.1:%d, reading Notion every %d s", args.port, args.interval)
    while True:
        try:
            sync_once(cfg, state)
        except Exception:
            LOG.exception("Sync failed")
        time.sleep(args.interval)


if __name__ == "__main__":
    main()
