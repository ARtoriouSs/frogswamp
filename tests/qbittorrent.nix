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
        speedLimits = {
          download = 28000;
          upload = 10000;
          altDownload = 6000;
          altUpload = 1500;
        };
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
    # KiB/s in the repo, bytes/s in the API
    assert (p["dl_limit"], p["up_limit"]) == (28000 * 1024, 10000 * 1024), (p["dl_limit"], p["up_limit"])
    assert (p["alt_dl_limit"], p["alt_up_limit"]) == (6000 * 1024, 1500 * 1024), (p["alt_dl_limit"], p["alt_up_limit"])
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

    # A lockfile left by a power cut, its PID now used by a process the sandbox hides (here PID 1).
    # qBittorrent must not take it for another running instance and quit
    server.succeed("systemctl stop qbittorrent")
    server.succeed("printf '1\\n\\nserver\\n\\n\\n' > /var/lib/qBittorrent/qBittorrent/config/lockfile")
    server.succeed("systemctl start qbittorrent")
    server.wait_for_open_port(8090, timeout=60)
    server.succeed("sleep 5; systemctl is-active qbittorrent")
  '';
}
