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
    # Media is made of untitled subgroups shown side by side as columns
    import json
    groups = json.loads(services)
    assert [g["name"] for g in groups] == ["Home", "Media", "Infrastructure"], [g["name"] for g in groups]
    rows = lambda name: {r["name"]: [t["name"] for t in r["services"]] for r in next(g for g in groups if g["name"] == name)["groups"]}
    assert rows("Media")["Stream"] == ["Jellyfin", "Ace Stream"], rows("Media")
    # the subgroups have no titles and list their tiles top to bottom
    server.succeed("grep -A3 'Stream:' /etc/homepage-dashboard/settings.yaml | grep -q 'header: false'")
    server.succeed("grep -A3 'Stream:' /etc/homepage-dashboard/settings.yaml | grep -q 'style: column'")
    # the mode toggles are gone: anyone on the LAN could flip them without a login
    assert "Modes" not in services, services
    css = server.succeed("curl -sf http://localhost:8082/api/config/custom.css")
    # the frog wallpaper is embedded in the stylesheet, on the box that covers the page
    assert 'html body #__next { background:' in css and 'url("data:image/png;base64,iVBOR' in css, css[:2000]
    # widget settings are not exposed by the API, so check the generated config
    server.succeed("grep -q 'sensor.backup_disk' /etc/homepage-dashboard/services.yaml")
    server.succeed("grep -q ':8093' /etc/homepage-dashboard/services.yaml")
    bookmarks = server.succeed("curl -sf http://localhost:8082/api/bookmarks")
    for name in ["Documentation", "Admin", "Zigbee2MQTT devices", "TRaSH Guides", "Servarr wiki"]:
        assert name in bookmarks, f"{name} missing: {bookmarks}"
  '';
}
