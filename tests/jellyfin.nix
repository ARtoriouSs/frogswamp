{ pkgs, ... }:
pkgs.testers.runNixOSTest {
  name = "jellyfin";
  nodes.server = {
    imports = [ ../modules ];
    virtualisation.memorySize = 2048;
    # Jellyfin 10.11 refuses to start with less than 2 GiB free in its data dir
    virtualisation.diskSize = 4096;
    frogswamp = {
      adminUser = "pupsich";
      jellyfin = {
        enable = true;
        apiKeyFile = "/etc/jellyfin-test-key";
      };
    };
    environment.etc.jellyfin-test-key.text = "test";
  };
  testScript = ''
    server.wait_for_unit("jellyfin.service")
    server.wait_for_open_port(8096)
    server.wait_until_succeeds("curl -sf http://127.0.0.1:8096/health | grep -q Healthy", timeout=600)
    server.succeed("test \"$(stat -c '%U:%G %a' /srv/media)\" = 'root:media 2775'")
    # library folders exist from the start (also after a reinstall), same owner and setgid
    for d in ["movies", "shows"]:
        server.succeed(f"test \"$(stat -c '%U:%G %a' /srv/media/{d})\" = 'root:media 2775'")
    server.succeed("id -nG jellyfin | grep -qw media")
    server.succeed("id -nG jellyfin | grep -qw render")
    server.succeed("id -nG pupsich | grep -qw media")
    # a new title in an empty library folder starts a library scan
    server.wait_for_unit("jellyfin-rescan.path")
    server.succeed("mkdir /srv/media/downloads/sonarr/Some.Show.S01")
    server.wait_until_succeeds("systemctl show -p ActiveState --value jellyfin-rescan.service | grep -qx activating", timeout=30)
  '';
}
