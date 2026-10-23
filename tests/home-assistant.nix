{ pkgs, ... }:
let
  # Answers the config-entry calls the Bluetooth recovery makes, and logs every request.
  # The enabled entry's state is read from /tmp/ha-state (default "loaded")
  fakeHa = pkgs.writeText "fake-ha.py" ''
    import http.server, json

    class H(http.server.BaseHTTPRequestHandler):
        def log(self):
            with open("/tmp/ha-requests", "a") as f:
                f.write(f"{self.command} {self.path} {self.headers.get('Authorization')}\n")

        def do_GET(self):
            self.log()
            try:
                state = open("/tmp/ha-state").read().strip()
            except FileNotFoundError:
                state = "loaded"
            body = json.dumps([
                {"entry_id": "adapter1", "disabled_by": None, "state": state},
                {"entry_id": "adapter2", "disabled_by": "user", "state": "not_loaded"},
            ]).encode()
            self.send_response(200)
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self):
            self.log()
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"{}")

    http.server.HTTPServer(("127.0.0.1", 8123), H).serve_forever()
  '';
in
pkgs.testers.runNixOSTest {
  name = "home-assistant";
  # HA itself replaced by a fake API, to test the Bluetooth recovery's reload path
  nodes.recover =
    { lib, ... }:
    {
      imports = [ ../modules ];
      frogswamp = {
        adminUser = "pupsich";
        homeAssistant = {
          enable = true;
          secretsFile = "${./fixtures/ha-secrets.yaml}";
          uiSnapshotDir = ./fixtures/ha-ui-snapshot;
          haTokenFile = "${pkgs.writeText "ha-token" "test-token"}";
        };
      };
      virtualisation.oci-containers.containers = lib.mkForce { };
      systemd.services.podman-home-assistant = {
        wantedBy = [ "multi-user.target" ];
        serviceConfig.ExecStart = "${pkgs.python3}/bin/python3 ${fakeHa}";
      };
      systemd.services.home-assistant-bluetooth-recover.environment = {
        MIN_AGE = "0";
        SETTLE = "0";
        READY_TIMEOUT = "2";
      };
    };
  nodes.server = {
    imports = [ ../modules ];
    frogswamp = {
      adminUser = "pupsich";
      homeAssistant = {
        enable = true;
        autoStart = false; # no registry access in the test sandbox
        secretsFile = "${./fixtures/ha-secrets.yaml}";
        uiSnapshotDir = ./fixtures/ha-ui-snapshot;
        hacs = true;
        cubematrix.enable = true;
      };
    };
  };
  testScript = ''
    server.wait_for_unit("multi-user.target")
    # Review Focus 4: UI-owned files exist on a fresh state dir, seeded from the UI snapshot and writable
    server.succeed("grep -q 'id: snapshot_test' /var/lib/home-assistant/automations.yaml")
    server.succeed("test \"$(stat -c '%a' /var/lib/home-assistant/automations.yaml)\" = 644")
    server.succeed("grep -qx '{}' /var/lib/home-assistant/scripts.yaml")
    server.succeed("grep -qx '\\[\\]' /var/lib/home-assistant/scenes.yaml")
    # existing UI content is never overwritten by tmpfiles
    server.succeed("echo '- id: mine' > /var/lib/home-assistant/automations.yaml")
    server.succeed("systemd-tmpfiles --create")
    server.succeed("grep -q 'id: mine' /var/lib/home-assistant/automations.yaml")
    # container definition: host network, read-only repo config, secrets, dbus
    # the podman flags live in the unit's start script, not in the unit file itself
    unit = server.succeed("cat $(systemctl cat podman-home-assistant.service | sed -n 's/^ExecStart=//p')")
    assert "--network=host" in unit, unit
    assert "/config/configuration.yaml:ro" in unit, unit
    assert "/config/automations:ro" in unit, unit
    assert "/config/secrets.yaml:ro" in unit, unit
    assert "/run/dbus:/run/dbus:ro" in unit, unit
    assert "/config/custom_components/hacs:ro" in unit, unit
    assert "/config/custom_components/cubematrix:ro" in unit, unit
    assert "/config/cubematrix:ro" in unit, unit
    # an empty folder stands in for /dev/bus/usb, so the recovery can wake up HA's USB watcher
    assert "/run/home-assistant/usb-watch:/dev/bus/usb:ro" in unit, unit
    server.succeed("test -d /run/home-assistant/usb-watch")
    server.succeed("test -d /var/lib/home-assistant/custom_components")
    # the lamp UDP session needs no inbound rule: no music mode port in the firewall
    fw = server.succeed("iptables -S nixos-fw")
    assert "dport 55440" not in fw, fw
    server.succeed("iptables -S | grep -q 'dport 8123'")
    # Bluetooth recovery: udev rule only for adapters (DEVTYPE=host), never for BLE connections
    rules = server.succeed("cat /etc/udev/rules.d/*.rules")
    assert 'ENV{DEVTYPE}=="host"' in rules and "home-assistant-bluetooth-recover.service" in rules, rules
    # With HA not running (e.g. adapter appears during boot) the recovery does nothing and succeeds
    server.succeed("systemctl start home-assistant-bluetooth-recover.service")
    server.fail("systemctl is-active podman-home-assistant.service")

    # With a token, the USB watcher is woken up, only the enabled Bluetooth entry is reloaded, and HA isn't restarted
    recover.wait_for_unit("podman-home-assistant.service")
    recover.wait_for_open_port(8123)
    pid = recover.succeed("systemctl show podman-home-assistant.service -P MainPID")
    recover.succeed("systemctl start home-assistant-bluetooth-recover.service")
    requests = recover.succeed("cat /tmp/ha-requests")
    assert "GET /api/config/config_entries/entry?domain=bluetooth Bearer test-token" in requests, requests
    assert "POST /api/config/config_entries/entry/adapter1/reload Bearer test-token" in requests, requests
    assert "adapter2" not in requests, requests
    assert recover.succeed("systemctl show podman-home-assistant.service -P MainPID") == pid
    recover.succeed("journalctl -u home-assistant-bluetooth-recover.service | grep -q 'Bluetooth is back'")
    recover.succeed("test -z \"$(ls -A /run/home-assistant/usb-watch)\"")

    # If the entry doesn't load after the reload, HA is restarted as a last resort
    recover.succeed("echo setup_retry > /tmp/ha-state")
    recover.succeed("systemctl start home-assistant-bluetooth-recover.service")
    recover.succeed("journalctl -u home-assistant-bluetooth-recover.service | grep -q 'restarting Home Assistant'")
    recover.wait_until_succeeds("systemctl is-active podman-home-assistant.service")
    assert recover.succeed("systemctl show podman-home-assistant.service -P MainPID") != pid
  '';
}
