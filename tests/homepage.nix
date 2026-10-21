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
    assert '"Downloads"' in services, services
    # widget settings are not exposed by the API, so check the generated config
    server.succeed("grep -q 'sensor.stale_downloads' /etc/homepage-dashboard/services.yaml")
    server.succeed("grep -q 'sensor.backup_disk' /etc/homepage-dashboard/services.yaml")
    server.succeed("grep -q ':8093' /etc/homepage-dashboard/services.yaml")
    bookmarks = server.succeed("curl -sf http://localhost:8082/api/bookmarks")
    for name in ["Documentation", "Admin", "Zigbee2MQTT devices", "TRaSH Guides", "Servarr wiki"]:
        assert name in bookmarks, f"{name} missing: {bookmarks}"
  '';
}
