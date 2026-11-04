import datetime as dt
import urllib.error
import zoneinfo

import icalendar
import pytest

import work_calendar as wc

TZ = zoneinfo.ZoneInfo("Europe/Warsaw")
NOW = dt.datetime(2026, 11, 5, 8, 0, tzinfo=dt.timezone.utc)
ME = "Me@Example.com"
START, END = dt.time(9), dt.time(18)
ME_YES = "ATTENDEE;PARTSTAT=ACCEPTED:mailto:me@example.com"
OTHER = "ATTENDEE;PARTSTAT=ACCEPTED:mailto:other@example.com"


def event(title="Sync", start="20261105T100000", end="20261105T110000", extra=(ME_YES, OTHER), uid=None):
    lines = [
        "BEGIN:VEVENT",
        f"UID:{uid or title}",
        "DTSTAMP:20261101T000000Z",
        f"DTSTART;TZID=Europe/Warsaw:{start}",
        f"DTEND;TZID=Europe/Warsaw:{end}",
        f"SUMMARY:{title}",
        *extra,
        "END:VEVENT",
    ]
    return "\r\n".join(lines)


def feed(*events):
    return ("BEGIN:VCALENDAR\r\nVERSION:2.0\r\nPRODID:test\r\n" + "\r\n".join(events) + "\r\nEND:VCALENDAR\r\n").encode()


def run(*events):
    return wc.meetings(feed(*events), ME, NOW, TZ, START, END)


def local(value):
    return value.astimezone(TZ).strftime("%d %H:%M")


def test_accepted_meeting_counts_and_keeps_its_title():
    found = run(event("Team sync"))
    assert [(m["title"], local(m["start"]), local(m["end"])) for m in found] == [("Team sync", "05 10:00", "05 11:00")]


@pytest.mark.parametrize("reply", ["DECLINED", "TENTATIVE", "NEEDS-ACTION"])
def test_other_replies_dont_count(reply):
    assert run(event(extra=(f"ATTENDEE;PARTSTAT={reply}:mailto:me@example.com", OTHER))) == []


def test_only_my_reply_matters():
    no_from_other = "ATTENDEE;PARTSTAT=DECLINED:mailto:other@example.com"
    assert len(run(event(extra=(ME_YES, no_from_other)))) == 1
    assert run(event(extra=("ATTENDEE;PARTSTAT=DECLINED:mailto:me@example.com", OTHER))) == []


def test_free_events_dont_count():
    assert run(event(extra=(ME_YES, OTHER, "TRANSP:TRANSPARENT"))) == []


def test_cancelled_events_dont_count():
    assert run(event(extra=(ME_YES, OTHER, "STATUS:CANCELLED"))) == []


def test_no_attendees_doesnt_count():
    assert run(event(extra=())) == []


def test_only_me_doesnt_count():
    assert run(event(extra=(ME_YES,))) == []


def test_a_room_isnt_another_person():
    room = "ATTENDEE;CUTYPE=RESOURCE;PARTSTAT=ACCEPTED:mailto:room@resource.example.com"
    assert run(event(extra=(ME_YES, room))) == []


def test_not_invited_doesnt_count():
    assert run(event(extra=(OTHER,))) == []


def test_all_day_events_dont_count():
    all_day = "\r\n".join(
        [
            "BEGIN:VEVENT",
            "UID:day",
            "DTSTAMP:20261101T000000Z",
            "DTSTART;VALUE=DATE:20261105",
            "DTEND;VALUE=DATE:20261106",
            "SUMMARY:Offsite",
            ME_YES,
            OTHER,
            "END:VEVENT",
        ]
    )
    assert run(all_day) == []


def test_hours_cut_the_edges():
    found = run(
        event("Early", "20261105T083000", "20261105T100000"),
        event("Late", "20261105T173000", "20261105T183000"),
    )
    assert [(m["title"], local(m["start"]), local(m["end"])) for m in found] == [
        ("Early", "05 09:00", "05 10:00"),
        ("Late", "05 17:30", "05 18:00"),
    ]


def test_outside_the_hours_is_dropped():
    assert run(event("Before", "20261105T070000", "20261105T090000"), event("After", "20261105T180000", "20261105T190000")) == []


def test_several_days_give_one_part_a_day():
    found = run(event("Workshop", "20261105T170000", "20261106T100000"))
    assert [(local(m["start"]), local(m["end"])) for m in found] == [("05 17:00", "05 18:00"), ("06 09:00", "06 10:00")]


def test_hours_follow_the_clock_over_a_time_change():
    # Winter time starts on 25 Oct 2026, the local hour stays the same
    found = wc.meetings(
        feed(event("Weekly", "20261019T100000", "20261019T110000", (ME_YES, OTHER, "RRULE:FREQ=WEEKLY;COUNT=2"))),
        ME,
        dt.datetime(2026, 10, 20, tzinfo=dt.timezone.utc),
        TZ,
        START,
        END,
    )
    assert [local(m["start"]) for m in found] == ["19 10:00", "26 10:00"]


def test_series_with_one_declined_and_one_moved():
    series = event("Weekly", "20261105T100000", "20261105T110000", (ME_YES, OTHER, "RRULE:FREQ=WEEKLY;COUNT=3"), "series")
    declined = event(
        "Weekly",
        "20261112T100000",
        "20261112T110000",
        ("RECURRENCE-ID;TZID=Europe/Warsaw:20261112T100000", "ATTENDEE;PARTSTAT=DECLINED:mailto:me@example.com", OTHER),
        "series",
    )
    moved = event(
        "Weekly",
        "20261119T150000",
        "20261119T160000",
        ("RECURRENCE-ID;TZID=Europe/Warsaw:20261119T100000", ME_YES, OTHER),
        "series",
    )
    assert [local(m["start"]) for m in run(series, declined, moved)] == ["05 10:00", "19 15:00"]


def test_output_has_only_times_and_titles():
    secret = (
        ME_YES,
        OTHER,
        "DESCRIPTION:Secret plan",
        "LOCATION:Secret room",
        "ORGANIZER:mailto:boss@example.com",
        "URL:https://meet.example.com/secret",
        "X-GOOGLE-CONFERENCE:https://meet.example.com/secret",
    )
    text = wc.render(run(event("Team sync", extra=secret)), NOW).decode()
    assert "Team sync" in text
    for leak in ("Secret", "secret", "example.com", "ATTENDEE", "ORGANIZER", "DESCRIPTION", "LOCATION", "URL"):
        assert leak not in text


def test_output_can_be_read_back():
    text = wc.render(run(event("A, b; c")), NOW)
    parsed = icalendar.Calendar.from_ical(text)
    events = list(parsed.walk("VEVENT"))
    assert [str(e["SUMMARY"]) for e in events] == ["A, b; c"]
    assert events[0]["DTSTART"].dt == dt.datetime(2026, 11, 5, 9, 0, tzinfo=dt.timezone.utc)


def test_uids_are_stable_and_differ():
    series = event("Weekly", extra=(ME_YES, OTHER, "RRULE:FREQ=WEEKLY;COUNT=2"), uid="series")
    first, second = [wc.render([m], NOW) for m in run(series)]
    uid = lambda text: str(next(iter(icalendar.Calendar.from_ical(text).walk("VEVENT")))["UID"])
    assert uid(first) != uid(second)
    assert uid(wc.render(run(series)[:1], NOW + dt.timedelta(hours=1))) == uid(first)


def test_a_bad_read_keeps_the_last_copy(tmp_path, monkeypatch):
    output = tmp_path / "work-meetings.ics"
    output.write_bytes(b"last good")
    monkeypatch.setattr(wc, "read_feed", lambda url: b"<html>Sign in</html>")
    with pytest.raises(ValueError):
        wc.refresh("https://example.com/secret", ME, output, TZ, START, END)
    assert output.read_bytes() == b"last good"


def test_the_link_stays_out_of_the_log(caplog):
    def fail():
        raise urllib.error.URLError("failed https://example.com/secret")

    with caplog.at_level("INFO"):
        wc.Reader(fail).read_now()
    assert "secret" not in caplog.text
    assert "URLError" in caplog.text


def test_reads_only_when_asked_and_not_too_often(monkeypatch):
    reads = []
    clock = [1000.0]
    monkeypatch.setattr(wc.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(wc.threading, "Thread", lambda target, daemon: type("T", (), {"start": staticmethod(target)}))
    reader = wc.Reader(lambda: reads.append(1) or 0)
    assert reads == []
    assert reader.asked()
    assert not reader.asked()
    clock[0] += wc.READ_AT_MOST_EVERY_S
    assert reader.asked()
    assert len(reads) == 2


def all_day(title="Out of Office", start="20261105", end="20261106", extra=()):
    lines = ["BEGIN:VEVENT", f"UID:{title}{start}", "DTSTAMP:20261101T000000Z", f"DTSTART;VALUE=DATE:{start}", f"DTEND;VALUE=DATE:{end}", f"SUMMARY:{title}", *extra, "END:VEVENT"]
    return "\r\n".join(lines)


def days(*events):
    return sorted(str(m["day"]) for m in run(*events) if "day" in m)


@pytest.mark.parametrize("title", ["Out of Office", "out of office", "OUT-OF-OFFICE", "OutOfOffice"])
def test_out_of_office_titles(title):
    assert days(all_day(title)) == ["2026-11-05"]


def test_other_all_day_events_arent_out_of_office():
    assert days(all_day("Out of Office party"), all_day("Offsite")) == []


def test_out_of_office_over_several_days():
    assert days(all_day(end="20261108")) == ["2026-11-05", "2026-11-06", "2026-11-07"]


def test_out_of_office_stored_as_midnight_to_midnight():
    assert days(event("Out of office", "20261105T000000", "20261107T000000", extra=())) == ["2026-11-05", "2026-11-06"]


def test_part_of_a_day_out_of_office_isnt_a_day_off():
    found = run(event("Out of office", "20261105T090000", "20261105T130000", extra=()))
    assert found == []


def test_cancelled_or_declined_out_of_office_doesnt_count():
    assert days(all_day(extra=("STATUS:CANCELLED",))) == []
    assert days(all_day(extra=("ATTENDEE;PARTSTAT=DECLINED:mailto:me@example.com",))) == []


def test_meetings_on_an_out_of_office_day_are_dropped():
    found = run(all_day(), event("That day"), event("Next day", "20261106T100000", "20261106T110000"))
    assert sorted(m["title"] for m in found) == ["Next day", "Out of Office"]


def test_out_of_office_output_is_all_day():
    parsed = icalendar.Calendar.from_ical(wc.render(run(all_day()), NOW))
    only = next(iter(parsed.walk("VEVENT")))
    assert only["DTSTART"].dt == dt.date(2026, 11, 5)
    assert only["DTEND"].dt == dt.date(2026, 11, 6)
    assert str(only["SUMMARY"]) == "Out of Office"
