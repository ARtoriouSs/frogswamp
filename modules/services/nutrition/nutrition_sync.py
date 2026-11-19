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


def request_json(method, url, headers=None, body=None, form=None, timeout=30):
    headers = dict(headers or {})
    data = None
    if body is not None:
        data = json.dumps(body).encode()
        headers["Content-Type"] = "application/json"
    elif form is not None:
        data = urllib.parse.urlencode(form).encode()
    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
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


class NotionWriter:
    """Writes only to the Nutrition log data source; the voice assistant's way in"""

    def __init__(self, base, token, source_id):
        self.base = base
        self.headers = {"Authorization": f"Bearer {token}", "Notion-Version": NOTION_VERSION}
        self.source_id = source_id

    # Short and no retry, so HA's 30 s timeout never cuts off a write that still goes through
    def _send(self, method, path, body):
        return request_json(method, self.base + path, self.headers, body=body, timeout=10)

    def create(self, properties):
        body = {"parent": {"type": "data_source_id", "data_source_id": self.source_id}, "properties": properties}
        return self._send("POST", "/v1/pages", body)["id"]

    def update(self, page_id, properties):
        self._send("PATCH", f"/v1/pages/{urllib.parse.quote(page_id)}", {"properties": properties})

    def trash(self, page_id):
        self._send("PATCH", f"/v1/pages/{urllib.parse.quote(page_id)}", {"in_trash": True})


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
        # The rows of the last Notion read, so the voice assistant can only change rows that exist
        self.meal_rows = {}
        self.logged = []
        self.deletes = []
        self.updates = []
        self.lock = threading.Lock()
        # Set after a voice write, so the next read comes at once
        self.wake = threading.Event()

    @classmethod
    def load(cls, state_dir):
        path = pathlib.Path(state_dir) / "google.json"
        synced = json.loads(path.read_text()) if path.exists() else {}
        state = cls(state_dir, synced)
        deletes = pathlib.Path(state_dir) / "voice_deletes.json"
        try:
            state.deletes = [dt.datetime.fromisoformat(moment) for moment in json.loads(deletes.read_text())]
        except (OSError, ValueError, TypeError):
            # Missing or broken: start counting again rather than not starting
            state.deletes = []
        return state

    def save_deletes(self, now):
        self.deletes = [moment for moment in self.deletes if now - moment < nc.DELETE_WINDOW]
        _write_atomic(self.state_dir / "voice_deletes.json", json.dumps([moment.isoformat() for moment in self.deletes]))

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
    with state.lock:
        state.meal_rows = {meal.id: meal for meal in meals}
        state.logged = []
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


class Refused(Exception):
    pass


def _day_total(state, day, tz):
    rows = list(state.meal_rows.values()) + state.logged
    mine = [meal for meal in rows if nc.meal_day(meal.eaten, tz) == day]
    return {"kcal": round(sum(m.kcal for m in mine), 1), "water_ml": round(sum(m.water for m in mine), 1)}


def food_log(state, writer, body, now, tz):
    entry = nc.parse_log_request(body, now, tz)
    day = nc.meal_day(entry.eaten, tz)
    with state.lock:
        count = sum(1 for meal in list(state.meal_rows.values()) + state.logged if nc.meal_day(meal.eaten, tz) == day)
        if count >= nc.DAY_LIMIT:
            raise Refused(f"at most {nc.DAY_LIMIT} entries a day")
        page_id = writer.create(nc.notion_properties(entry, tz))
        fields = {k: getattr(entry, k) for k in ("product", "kcal", "protein", "fat", "carbs", "water")}
        state.logged.append(nc.Meal(id=page_id, eaten=entry.eaten, meal=entry.meal, **fields))
        LOG.info("Voice added row %s", page_id)
        return {"id": page_id, "day_total": _day_total(state, day, tz)}


def _known(state):
    """Rows of the last read and the ones logged since, by id"""
    return {**state.meal_rows, **{meal.id: meal for meal in state.logged}}


def food_update(state, writer, body, now, tz):
    page_id = body.get("id")
    # HA sends the fields the assistant left out as null
    changes = {k: v for k, v in body.items() if k != "id" and v is not None}
    props = nc.log_update_properties(changes, now, tz)
    with state.lock:
        row = _known(state).get(page_id) if isinstance(page_id, str) else None
        if row is None:
            raise Refused("unknown entry")
        kcal = props.get("Kcal", {}).get("number", row.kcal)
        water = props.get("Water", {}).get("number", row.water)
        # Zeroing rows would be a removal that skips the removal limit
        if not kcal and not water:
            raise Refused("kcal or water is required, remove the entry instead")
        state.updates = [moment for moment in state.updates if now - moment < nc.DELETE_WINDOW]
        if len(state.updates) >= nc.UPDATE_LIMIT:
            raise Refused(f"too many changes, at most {nc.UPDATE_LIMIT} per hour")
        state.updates.append(now)
        writer.update(page_id, props)
    LOG.info("Voice changed row %s", page_id)
    return {"id": page_id}


def food_delete(state, writer, body, now):
    ids = body.get("id")
    ids = ids if isinstance(ids, list) else [ids] if ids else []
    with state.lock:
        reason = nc.delete_refusal(ids, set(_known(state)), state.deletes, now)
        if reason:
            raise Refused(reason)
        # Counted before the call, so a timeout after Notion did it still counts
        state.deletes.append(now)
        state.save_deletes(now)
        writer.trash(ids[0])
        state.meal_rows.pop(ids[0], None)
        state.logged = [meal for meal in state.logged if meal.id != ids[0]]
    LOG.info("Voice removed row %s", ids[0])
    return {"id": ids[0]}


def food_recent(state, now, tz):
    today = nc.meal_day(now, tz)
    days = {today, today - dt.timedelta(days=1)}
    with state.lock:
        rows = [meal for meal in list(state.meal_rows.values()) + state.logged if nc.meal_day(meal.eaten, tz) in days]
    rows.sort(key=lambda meal: meal.eaten, reverse=True)
    return {
        "entries": [
            {"id": m.id, "product": m.product, "kcal": m.kcal, "water": m.water, "eaten": m.eaten.astimezone(tz).isoformat(timespec="minutes"), "day": nc.meal_day(m.eaten, tz).isoformat()}
            for m in rows
        ]
    }


def serve(state, port, cfg=None, clock=None):
    clock = clock or (lambda: dt.datetime.now(cfg.tz))
    writer = NotionWriter(cfg.notion_url, cfg.notion_token, cfg.meals_source) if cfg else None
    writes = {"/log": food_log, "/log/update": food_update}

    class Handler(http.server.BaseHTTPRequestHandler):
        def _answer(self, status, answer):
            data = json.dumps(answer, ensure_ascii=False).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_POST(self):
            path = urllib.parse.urlparse(self.path).path
            if writer is None or path not in (*writes, "/log/delete"):
                self.send_error(404)
                return
            try:
                length = int(self.headers.get("Content-Length") or 0)
            except ValueError:
                length = -1
            if not 0 <= length <= 4096:
                self._answer(400, {"ok": False, "error": "request too big"})
                return
            try:
                body = json.loads(self.rfile.read(length) or b"{}")
                if not isinstance(body, dict):
                    raise ValueError
            except ValueError:
                self._answer(400, {"ok": False, "error": "body must be a JSON object"})
                return
            now = clock()
            try:
                if path == "/log/delete":
                    result = food_delete(state, writer, body, now)
                else:
                    result = writes[path](state, writer, body, now, cfg.tz)
            except (nc.LogError, Refused) as error:
                self._answer(400, {"ok": False, "error": str(error)})
                return
            except HttpError as error:
                LOG.warning("Notion refused a voice write: HTTP %d", error.status)
                self._answer(502, {"ok": False, "error": f"notion {error.status}"})
                return
            except (urllib.error.URLError, OSError) as error:
                self._answer(502, {"ok": False, "error": f"notion unreachable ({type(error).__name__}), check Recent food entries"})
                return
            except (ValueError, KeyError):
                self._answer(502, {"ok": False, "error": "notion gave an odd answer, check Recent food entries"})
                return
            state.wake.set()
            self._answer(200, {"ok": True, **result})

        def do_GET(self):
            url = urllib.parse.urlparse(self.path)
            if url.path == "/log/recent" and cfg is not None:
                self._answer(200, {"ok": True, **food_recent(state, clock(), cfg.tz)})
                return
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
        state.wake.wait(args.interval)
        state.wake.clear()


if __name__ == "__main__":
    main()
