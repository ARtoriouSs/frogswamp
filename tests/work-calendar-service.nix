{ pkgs, ... }:
let
  feed = pkgs.writeText "feed.ics" ''
    BEGIN:VCALENDAR
    VERSION:2.0
    PRODID:test
    BEGIN:VEVENT
    UID:yes
    DTSTAMP:20300101T000000Z
    DTSTART:20300107T110000Z
    DTEND:20300107T120000Z
    SUMMARY:Accepted sync
    DESCRIPTION:Hidden notes
    ATTENDEE;PARTSTAT=ACCEPTED:mailto:me@example.com
    ATTENDEE;PARTSTAT=ACCEPTED:mailto:other@example.com
    END:VEVENT
    BEGIN:VEVENT
    UID:no
    DTSTAMP:20300101T000000Z
    DTSTART:20300107T130000Z
    DTEND:20300107T140000Z
    SUMMARY:Declined sync
    ATTENDEE;PARTSTAT=DECLINED:mailto:me@example.com
    ATTENDEE;PARTSTAT=ACCEPTED:mailto:other@example.com
    END:VEVENT
    END:VCALENDAR
  '';
in
pkgs.testers.runNixOSTest {
  name = "work-calendar-service";
  nodes.server = _: {
    imports = [ ../modules ];
    environment.etc."wc-url".text = "http://127.0.0.1:9000/secret-part/feed.ics\n";
    environment.etc."wc-email".text = "me@example.com\n";
    # Close enough to the feed's events for the 90 days ahead
    virtualisation.qemu.options = [ "-rtc base=2030-01-01T10:00:00" ];
    frogswamp = {
      adminUser = "pupsich";
      workCalendar = {
        enable = true;
        urlFile = "/etc/wc-url";
        emailFile = "/etc/wc-email";
      };
    };
  };
  testScript = ''
    import json

    server.succeed("mkdir -p /srv/secret-part && cp ${feed} /srv/secret-part/feed.ics")
    server.succeed("systemd-run --unit=fake-google --working-directory=/srv ${pkgs.python3}/bin/python3 -m http.server --bind 127.0.0.1 9000")
    server.wait_for_open_port(9000)
    server.systemctl("restart work-calendar.service")
    server.wait_for_unit("work-calendar.service")
    server.wait_for_open_port(8129)
    server.wait_until_succeeds("curl -fs http://127.0.0.1:8129/work-meetings.ics", timeout=30)

    text = server.succeed("curl -fs http://127.0.0.1:8129/work-meetings.ics")
    assert "Accepted sync" in text, text
    for leak in ["Declined", "Hidden", "example.com", "ATTENDEE"]:
        assert leak not in text, leak
    json.loads(server.succeed("curl -fs http://127.0.0.1:8129/status"))["last_read"]

    # Only on localhost, not as root, and the link stays out of the log
    server.fail("ss -ltn | grep ':8129' | grep -v '127.0.0.1'")
    server.succeed("test \"$(systemctl show -p MainPID --value work-calendar | xargs ps -o user= -p)\" != root")

    # Google gone: the last copy stays, and the log has no link
    server.succeed("systemctl stop fake-google")
    server.systemctl("restart work-calendar.service")
    server.wait_for_open_port(8129)
    server.wait_until_succeeds("journalctl -u work-calendar | grep -q \"Couldn't read the calendar\"", timeout=30)
    assert "Accepted sync" in server.succeed("curl -fs http://127.0.0.1:8129/work-meetings.ics")
    server.fail("journalctl -u work-calendar | grep -q secret-part")
  '';
}
