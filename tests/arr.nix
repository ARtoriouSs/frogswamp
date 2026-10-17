{ pkgs, ... }:
let
  keys = {
    radarr = "a1b2c3d4e5f60718293a4b5c6d7e8f90";
    sonarr = "b2c3d4e5f60718293a4b5c6d7e8f90a1";
    prowlarr = "c3d4e5f60718293a4b5c6d7e8f90a1b2";
    bazarr = "d4e5f60718293a4b5c6d7e8f90a1b2c3";
  };
  envFile =
    app:
    toString (pkgs.writeText "${app}.env" "${pkgs.lib.toUpper app}__AUTH__APIKEY=${keys.${app}}\n");
in
pkgs.testers.runNixOSTest {
  name = "arr";
  nodes.server = {
    imports = [ ../modules ];
    virtualisation.memorySize = 3072;
    virtualisation.diskSize = 4096;
    frogswamp = {
      adminUser = "pupsich";
      jellyfin.enable = true;
      arr = {
        enable = true;
        environmentFiles = {
          radarr = envFile "radarr";
          sonarr = envFile "sonarr";
          prowlarr = envFile "prowlarr";
        };
        apiKeyFiles = pkgs.lib.mapAttrs (n: k: toString (pkgs.writeText "${n}.key" k)) keys;
      };
    };
  };
  testScript = ''
    apps = {"radarr": (7878, "v3"), "sonarr": (8989, "v3"), "prowlarr": (9696, "v1")}
    keys = ${builtins.toJSON keys}
    for app, (port, ver) in apps.items():
        server.wait_for_unit(f"{app}.service")
        server.wait_for_open_port(port, timeout=300)
        server.wait_until_succeeds(f"curl -sf -H 'X-Api-Key: {keys[app]}' http://127.0.0.1:{port}/api/{ver}/system/status", timeout=300)
        server.fail(f"curl -sf -H 'X-Api-Key: wrongwrongwrongwrongwrongwrong00' http://127.0.0.1:{port}/api/{ver}/system/status")
    server.wait_for_unit("bazarr.service")
    server.wait_for_open_port(6767, timeout=300)
    server.wait_until_succeeds(f"curl -sf 'http://127.0.0.1:6767/api/system/status?apikey={keys['bazarr']}'", timeout=300)
    for user in ["radarr", "sonarr", "bazarr"]:
        server.succeed(f"id -nG {user} | grep -qw media")
        assert server.succeed(f"systemctl show {user} -P UMask").strip() == "0002", user
  '';
}
