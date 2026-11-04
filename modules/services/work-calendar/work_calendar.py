#!/usr/bin/env python3
"""Serves the work meetings that should raise the desk, and the Out of Office days, from a private Google Calendar link.

Only start, end and title leave this service. The link itself must never reach the logs.
The calendar is read only when HA asks, so it isn't read while HA doesn't ask for it, during a day off.
"""
import argparse
import datetime as dt
import hashlib
import http.server
import json
import logging
import os
import pathlib
import re
import threading
import time
import urllib.request
import zoneinfo

import icalendar
import recurring_ical_events

LOG = logging.getLogger("work-calendar")
# Under HA's 5 minutes between requests
READ_AT_MOST_EVERY_S = 240
PAST = dt.timedelta(days=35)
FUTURE = dt.timedelta(days=90)
NOT_PEOPLE = {"ROOM", "RESOURCE"}


def _attendees(event):
    found = event.get("ATTENDEE", [])
    return found if isinstance(found, list) else [found]


def _email(attendee):
    return str(attendee).lower().removeprefix("mailto:")


def counts_as_meeting(event, email):
    """Accepted by me, shown as busy, not cancelled, and with at least one other person"""
    if str(event.get("STATUS", "")).upper() == "CANCELLED":
        return False
    if str(event.get("TRANSP", "OPAQUE")).upper() == "TRANSPARENT":
        return False
    mine = [a for a in _attendees(event) if _email(a) == email]
    others = [
        a
        for a in _attendees(event)
        if _email(a) != email and str(a.params.get("CUTYPE", "")).upper() not in NOT_PEOPLE
    ]
    return bool(others) and any(str(a.params.get("PARTSTAT", "")).upper() == "ACCEPTED" for a in mine)


def _times(event, tz):
    start = event["DTSTART"].dt
    end = event["DTEND"].dt if "DTEND" in event else start + event["DURATION"].dt
    # All-day events have dates here, and are never meetings
    if not isinstance(start, dt.datetime) or not isinstance(end, dt.datetime):
        return None
    return (
        start.replace(tzinfo=tz) if start.tzinfo is None else start,
        end.replace(tzinfo=tz) if end.tzinfo is None else end,
    )


def clip_to_hours(start, end, tz, day_start, day_end):
    """The parts of a meeting inside the allowed hours, one for each day it covers"""
    parts = []
    day = start.astimezone(tz).date()
    while dt.datetime.combine(day, day_start, tz) < end:
        part_start = max(start, dt.datetime.combine(day, day_start, tz))
        part_end = min(end, dt.datetime.combine(day, day_end, tz))
        if part_start < part_end:
            parts.append((part_start, part_end))
        day += dt.timedelta(days=1)
    return parts


def _is_out_of_office(event, email):
    title = re.sub(r"[\s-]+", "", str(event.get("SUMMARY", ""))).lower()
    if title != "outofoffice" or str(event.get("STATUS", "")).upper() == "CANCELLED":
        return False
    mine = [a for a in _attendees(event) if _email(a) == email]
    return not mine or any(str(a.params.get("PARTSTAT", "")).upper() == "ACCEPTED" for a in mine)


def _whole_days(event, tz):
    """The days of an all-day event. Google can store one as midnight to midnight instead of as dates"""
    start = event["DTSTART"].dt
    end = event["DTEND"].dt if "DTEND" in event else start + event["DURATION"].dt
    if isinstance(start, dt.datetime):
        start, end = (value.replace(tzinfo=tz) if value.tzinfo is None else value.astimezone(tz) for value in (start, end))
        if start.time() != dt.time(0) or end.time() != dt.time(0):
            return []
        start, end = start.date(), end.date()
    return [start + dt.timedelta(days=offset) for offset in range((end - start).days)]


def meetings(feed, email, now, tz, day_start, day_end):
    email = email.strip().lower()
    calendar = icalendar.Calendar.from_ical(feed)
    # Expanding the repeats also applies changes to a single meeting of a series, e.g. declining just that one
    occurrences = recurring_ical_events.of(calendar).between(now - PAST, now + FUTURE)
    away = {}
    for event in occurrences:
        if _is_out_of_office(event, email):
            for day in _whole_days(event, tz):
                away[day] = str(event["SUMMARY"]).strip()
    found = [{"uid": f"out-of-office|{day}", "day": day, "title": title} for day, title in away.items()]
    for event in occurrences:
        if not counts_as_meeting(event, email):
            continue
        times = _times(event, tz)
        if times is None:
            continue
        for start, end in clip_to_hours(*times, tz, day_start, day_end):
            if start.astimezone(tz).date() in away:
                continue
            found.append(
                {
                    "uid": f"{event.get('UID', '')}|{start.isoformat()}",
                    "start": start.astimezone(dt.timezone.utc),
                    "end": end.astimezone(dt.timezone.utc),
                    "title": str(event.get("SUMMARY", "")).strip() or "Busy",
                }
            )
    return found


def render(found, now):
    calendar = icalendar.Calendar()
    calendar.add("PRODID", "-//frogswamp//work-calendar//EN")
    calendar.add("VERSION", "2.0")
    for meeting in found:
        event = icalendar.Event()
        event.add("UID", hashlib.sha256(meeting["uid"].encode()).hexdigest())
        event.add("DTSTAMP", now.astimezone(dt.timezone.utc))
        if "day" in meeting:
            event.add("DTSTART", meeting["day"])
            event.add("DTEND", meeting["day"] + dt.timedelta(days=1))
        else:
            event.add("DTSTART", meeting["start"])
            event.add("DTEND", meeting["end"])
        event.add("SUMMARY", meeting["title"])
        calendar.add_component(event)
    return calendar.to_ical()


def read_feed(url):
    request = urllib.request.Request(url, headers={"User-Agent": "frogswamp-work-calendar"})
    with urllib.request.urlopen(request, timeout=60) as response:
        return response.read()


def refresh(url, email, output, tz, day_start, day_end):
    now = dt.datetime.now(dt.timezone.utc)
    found = meetings(read_feed(url), email, now, tz, day_start, day_end)
    temp = output.with_suffix(".tmp")
    temp.write_bytes(render(found, now))
    os.replace(temp, output)
    return len(found)


class Reader:
    """Reads the calendar in the background when asked, so HA gets the last copy at once"""

    def __init__(self, read):
        self._read = read
        self._lock = threading.Lock()
        self._last_try = None

    def read_now(self):
        try:
            LOG.info("Read %d events", self._read())
        except Exception as error:  # pylint: disable=broad-except
            # Only the type: the text of a network error can contain the secret link.
            # The last good copy keeps being served
            LOG.error("Couldn't read the calendar: %s", type(error).__name__)

    def asked(self):
        with self._lock:
            now = time.monotonic()
            if self._last_try is not None and now - self._last_try < READ_AT_MOST_EVERY_S:
                return False
            self._last_try = now
        threading.Thread(target=self.read_now, daemon=True).start()
        return True


def make_handler(output, reader):
    class Handler(http.server.BaseHTTPRequestHandler):
        def _send(self, status, kind, body):
            self.send_response(status)
            self.send_header("Content-Type", kind)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):  # pylint: disable=invalid-name
            if self.path not in ("/work-meetings.ics", "/status"):
                self._send(404, "text/plain", b"Not found")
                return
            if self.path == "/work-meetings.ics":
                reader.asked()
            if not output.exists():
                self._send(503, "text/plain", b"Not read yet")
            elif self.path == "/status":
                read_at = dt.datetime.fromtimestamp(output.stat().st_mtime, dt.timezone.utc)
                self._send(200, "application/json", json.dumps({"last_read": read_at.isoformat()}).encode())
            else:
                self._send(200, "text/calendar; charset=utf-8", output.read_bytes())

        def log_message(self, format, *args):  # pylint: disable=redefined-builtin
            pass

    return Handler


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--credentials", required=True, type=pathlib.Path)
    parser.add_argument("--state-dir", required=True, type=pathlib.Path)
    parser.add_argument("--port", required=True, type=int)
    parser.add_argument("--timezone", required=True)
    parser.add_argument("--day-start", required=True, type=dt.time.fromisoformat)
    parser.add_argument("--day-end", required=True, type=dt.time.fromisoformat)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    url = (args.credentials / "url").read_text().strip()
    email = (args.credentials / "email").read_text().strip()
    output = args.state_dir / "work-meetings.ics"
    tz = zoneinfo.ZoneInfo(args.timezone)
    reader = Reader(lambda: refresh(url, email, output, tz, args.day_start, args.day_end))
    # The first copy, so HA finds one when it starts
    reader.asked()
    http.server.ThreadingHTTPServer(("127.0.0.1", args.port), make_handler(output, reader)).serve_forever()


if __name__ == "__main__":
    main()
