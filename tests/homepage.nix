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
    for name in ["Home Assistant", "Jellyfin", "Zigbee2MQTT", "Media share"]:
        assert name in services, f"{name} missing: {services}"
  '';
}
