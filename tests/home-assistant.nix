{ pkgs, ... }:
pkgs.testers.runNixOSTest {
  name = "home-assistant";
  nodes.server = {
    imports = [ ../modules ];
    frogswamp = {
      adminUser = "pupsich";
      homeAssistant = {
        enable = true;
        autoStart = false; # no registry access in the test sandbox
        secretsFile = "${./fixtures/ha-secrets.yaml}";
        uiSnapshotDir = ./fixtures/ha-ui-snapshot;
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
    server.succeed("iptables -S | grep -q 'dport 8123'")
    # Bluetooth recovery: udev rule only for adapters (DEVTYPE=host), never for BLE connections
    rules = server.succeed("cat /etc/udev/rules.d/*.rules")
    assert 'ENV{DEVTYPE}=="host"' in rules and "home-assistant-bluetooth-recover.service" in rules, rules
    # With HA not running (e.g. adapter appears during boot) the recovery does nothing and succeeds
    server.succeed("systemctl start home-assistant-bluetooth-recover.service")
    server.fail("systemctl is-active podman-home-assistant.service")
  '';
}
