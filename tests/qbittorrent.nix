{ pkgs, ... }:
pkgs.testers.runNixOSTest {
  name = "qbittorrent";
  nodes.server = {
    imports = [ ../modules ];
    virtualisation.diskSize = 4096;
    frogswamp = {
      adminUser = "pupsich";
      jellyfin.enable = true;
      qbittorrent = {
        enable = true;
        webuiPasswordHashFile = "${./fixtures/qbittorrent-password-hash}"; # password: testpw
      };
    };
  };
  testScript = ''
    import json
    server.wait_for_unit("qbittorrent.service")
    server.wait_for_open_port(8090)
    # qBittorrent 5.x: success = 2xx + SID cookie (204, empty body); a wrong password sets no SID
    server.succeed("curl -sf -c /tmp/c -d 'username=pupsich&password=testpw' http://127.0.0.1:8090/api/v2/auth/login && grep -q SID /tmp/c")
    server.fail("curl -s -c /tmp/x -d 'username=pupsich&password=wrong' http://127.0.0.1:8090/api/v2/auth/login; grep -q SID /tmp/x")
    p = json.loads(server.succeed("curl -sf -b /tmp/c http://127.0.0.1:8090/api/v2/app/preferences"))
    assert p["save_path"].rstrip("/") == "/srv/media/downloads", p["save_path"]
    assert p["auto_tmm_enabled"] is True, "category save paths are ignored without automatic torrent management"
    assert p["incomplete_files_ext"] is False and p["temp_path_enabled"] is False
    assert p["max_ratio_enabled"] and p["max_ratio"] == 0, (p["max_ratio_enabled"], p["max_ratio"])
    assert p["max_seeding_time_enabled"] and p["max_seeding_time"] == 0
    assert p["max_ratio_act"] == 0, p["max_ratio_act"]  # 0 = pause/stop, never remove
    assert p["listen_port"] == 51413
    cats = json.loads(server.succeed("curl -sf -b /tmp/c http://127.0.0.1:8090/api/v2/torrents/categories"))
    assert cats["radarr"]["savePath"].rstrip("/") == "/srv/media/downloads/radarr", cats
    assert cats["sonarr"]["savePath"].rstrip("/") == "/srv/media/downloads/sonarr", cats
    for d in ["downloads", "downloads/radarr", "downloads/sonarr"]:
        server.succeed(f"test \"$(stat -c '%U:%G %a' /srv/media/{d})\" = 'root:media 2775'")
    server.succeed("id -nG qbittorrent | grep -qw media")
    assert server.succeed("systemctl show qbittorrent -P UMask").strip() == "0002"
  '';
}
