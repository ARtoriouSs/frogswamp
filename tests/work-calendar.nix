{ pkgs, ... }:
pkgs.runCommand "test-work-calendar"
  {
    nativeBuildInputs = [
      (pkgs.python3.withPackages (ps: [
        ps.pytest
        ps.icalendar
        ps.recurring-ical-events
        ps.tzdata
      ]))
    ];
  }
  ''
    cp ${../modules/services/work-calendar/work_calendar.py} work_calendar.py
    cp ${../modules/services/work-calendar/test_work_calendar.py} test_work_calendar.py
    python -m pytest -q -p no:cacheprovider test_work_calendar.py
    touch $out
  ''
