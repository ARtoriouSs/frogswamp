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
      };
    };
  };
  testScript = ''
    server.wait_for_unit("multi-user.target")
    # Review Focus 4: UI-owned files exist on a fresh state dir
    server.succeed("grep -qx '\\[\\]' /var/lib/home-assistant/automations.yaml")
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
  '';
}
