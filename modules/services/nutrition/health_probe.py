#!/usr/bin/env python3
"""Shows which Google Health data types this account really has, run on the laptop with `just nutrition-google-probe`.

Prints counts and field names, not values, except the few labels the charts depend on
(sleep type, heart rate zone names, exercise types)
"""
import argparse
import collections
import datetime as dt
import json
import pathlib
import urllib.error
import urllib.parse
import urllib.request

TOKEN_URL = "https://oauth2.googleapis.com/token"
ROOT = "https://health.googleapis.com/v4/users/me/dataTypes/"
TYPES = [
    "exercise",
    "sleep",
    "heart-rate",
    "daily-resting-heart-rate",
    "daily-heart-rate-variability",
    "active-zone-minutes",
    "time-in-heart-rate-zone",
    "hydration-log",
    "weight",
    "body-fat",
    "total-calories",
    "active-energy-burned",
    "steps",
]
MAX_PAGES = 20


def call(method, url, token=None, body=None, form=None):
    data, headers = None, {}
    if form is not None:
        data = urllib.parse.urlencode(form).encode()
    elif body is not None:
        data = json.dumps(body).encode()
        headers["Content-Type"] = "application/json"
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return response.status, json.load(response)
    except urllib.error.HTTPError as error:
        text = error.read().decode(errors="replace")
        try:
            message = json.loads(text)["error"]["message"]
        except (ValueError, KeyError, TypeError):
            message = text[:300]
        return error.code, {"error": message}


def paths(value, prefix=""):
    """Every key path in a JSON value, lists shown as []"""
    found = set()
    if isinstance(value, dict):
        for key, item in value.items():
            found.add(prefix + key)
            found |= paths(item, prefix + key + ".")
    elif isinstance(value, list):
        for item in value[:5]:
            found |= paths(item, prefix.rstrip(".") + "[].")
    return found


def strings_at(value, key):
    """All string values of one key anywhere in a JSON value"""
    if isinstance(value, dict):
        for k, item in value.items():
            if k == key and isinstance(item, str):
                yield item
            else:
                yield from strings_at(item, key)
    elif isinstance(value, list):
        for item in value:
            yield from strings_at(item, key)


def list_points(token, kind, since):
    """Points of one type, newest pages first if Google sorts that way; (points, error)"""
    snake = kind.replace("-", "_")
    filters = [
        f'{snake}.interval.civil_start_time >= "{since.isoformat()}"',
        f'{snake}.sample_time.civil_time >= "{since.isoformat()}"',
        f'{snake}.date >= "{since.date().isoformat()}"',
        None,
    ]
    last_error = None
    for flt in filters:
        points, page = [], None
        for _ in range(MAX_PAGES):
            query = {"pageSize": 1000}
            if flt:
                query["filter"] = flt
            if page:
                query["pageToken"] = page
            status, answer = call("GET", ROOT + kind + "/dataPoints?" + urllib.parse.urlencode(query), token)
            if status != 200:
                last_error = f"{status}: {answer['error']}"
                break
            points += answer.get("dataPoints", [])
            page = answer.get("nextPageToken")
            if not page:
                return points, flt or "no filter", None
        else:
            return points, (flt or "no filter") + f" (stopped after {MAX_PAGES} pages)", None
    return [], None, last_error


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--credentials", required=True, help="folder with google_client_id, google_client_secret, google_refresh_token")
    parser.add_argument("--days", type=int, default=14)
    parser.add_argument("--only", nargs="*", help="probe only these data types")
    args = parser.parse_args()
    creds = pathlib.Path(args.credentials)
    secret = lambda name: (creds / name).read_text().strip()
    status, answer = call(
        "POST",
        TOKEN_URL,
        form={
            "client_id": secret("google_client_id"),
            "client_secret": secret("google_client_secret"),
            "refresh_token": secret("google_refresh_token"),
            "grant_type": "refresh_token",
        },
    )
    if status != 200:
        raise SystemExit(f"Token refresh failed: {status} {answer['error']}")
    print("Granted scopes:", " ".join(s.rsplit("/", 1)[-1] for s in answer.get("scope", "").split()))
    token = answer["access_token"]
    since = dt.datetime.now().replace(microsecond=0) - dt.timedelta(days=args.days)

    for kind in args.only or TYPES:
        points, how, error = list_points(token, kind, since)
        print(f"\n== {kind}")
        if error:
            print("  error:", error)
            continue
        print(f"  points: {len(points)} ({how})")
        if not points:
            continue
        print("  fields:", ", ".join(sorted(paths(points[0]))))
        for key in ("type", "exerciseType", "zone", "heartRateZone", "heartRateZoneType", "stage"):
            values = collections.Counter(v for p in points for v in strings_at(p, key))
            if values:
                print(f"  {key}:", dict(values.most_common(12)))
        times = sorted(v for p in points for v in strings_at(p, "civilStartTime") if v)
        if not times:
            times = sorted(v for p in points for v in strings_at(p, "physicalTime") if v)
        if times:
            print(f"  span: {times[0]} .. {times[-1]}")

    end = dt.date.today()
    start = end - dt.timedelta(days=args.days)
    body = {
        "range": {
            "start": {"date": {"year": start.year, "month": start.month, "day": start.day}},
            "end": {"date": {"year": end.year, "month": end.month, "day": end.day}},
        }
    }
    status, answer = call("POST", ROOT + "total-calories/dataPoints:dailyRollUp", token, body)
    print("\n== total-calories dailyRollUp")
    if status != 200:
        print("  error:", f"{status}: {answer['error']}")
    else:
        rows = answer.get("rollupDataPoints") or answer.get("dataPoints") or []
        print(f"  days: {len(rows)}")
        if rows:
            print("  fields:", ", ".join(sorted(paths(rows[0]))))


if __name__ == "__main__":
    main()
