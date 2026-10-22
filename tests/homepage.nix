{ pkgs, ... }:
pkgs.testers.runNixOSTest {
  name = "homepage";
  nodes.server =
    { pkgs, ... }:
    {
      imports = [ ../modules ];
      frogswamp = {
        adminUser = "pupsich";
        homepage = {
          enable = true;
          environmentFile = toString (
            pkgs.writeText "env" ''
              HOMEPAGE_VAR_HA_TOKEN=dummy
              HOMEPAGE_VAR_JELLYFIN_KEY=dummy
            ''
          );
        };
        modeSwitch = {
          enable = true;
          haTokenFile = toString (pkgs.writeText "ha-token" "dummy");
          modes.vacation = "input_boolean.vacation";
        };
      };
    };
  testScript = ''
    server.wait_for_unit("homepage-dashboard.service")
    server.wait_for_open_port(8082)
    server.wait_until_succeeds("curl -sf http://localhost:8082/ >/dev/null", timeout=120)
    services = server.succeed("curl -sf http://localhost:8082/api/services")
    for name in ["Home Assistant", "Jellyfin", "Zigbee2MQTT", "Media share", "Router", "Tailscale", "Backblaze B2", "GitHub repo"]:
        assert name in services, f"{name} missing: {services}"
    # top-bar widgets get no HOMEPAGE_VAR_* substitution, so no placeholders may reach them
    widgets = server.succeed("curl -sf http://localhost:8082/api/widgets")
    assert "{{HOMEPAGE_VAR" not in widgets, widgets
    for name in ["qBittorrent", "Radarr", "Sonarr", "Prowlarr", "Bazarr"]:
        assert name in services, f"{name} missing: {services}"
    # downloads and watching share the Media group; no Downloads group any more
    assert '"Media"' in services and '"Downloads"' not in services, services
    # Modes is a subgroup of Home: Homepage serves it inside Home's group, before Media
    import json
    groups = json.loads(services)
    home = next(g for g in groups if g["name"] == "Home")
    modes = next(g for g in home["groups"] if g["name"] == "Modes")
    assert [t["name"] for t in modes["services"]] == ["Vacation"], modes
    assert [g["name"] for g in groups] == ["Home", "Media", "Infrastructure"], [g["name"] for g in groups]
    # widget settings are not exposed by the API, so check the generated config
    server.succeed("grep -q 'sensor.stale_downloads' /etc/homepage-dashboard/services.yaml")
    server.succeed("grep -q 'sensor.backup_disk' /etc/homepage-dashboard/services.yaml")
    server.succeed("grep -q ':8093' /etc/homepage-dashboard/services.yaml")
    bookmarks = server.succeed("curl -sf http://localhost:8082/api/bookmarks")
    for name in ["Documentation", "Admin", "Zigbee2MQTT devices", "TRaSH Guides", "Servarr wiki"]:
        assert name in bookmarks, f"{name} missing: {bookmarks}"
  '';
}
