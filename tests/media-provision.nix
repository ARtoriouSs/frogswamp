{ pkgs, ... }:
let
  keys = {
    radarr = "a1b2c3d4e5f60718293a4b5c6d7e8f90";
    sonarr = "b2c3d4e5f60718293a4b5c6d7e8f90a1";
    prowlarr = "c3d4e5f60718293a4b5c6d7e8f90a1b2";
    bazarr = "d4e5f60718293a4b5c6d7e8f90a1b2c3";
  };
  file = name: text: toString (pkgs.writeText name text);
in
pkgs.testers.runNixOSTest {
  name = "media-provision";
  nodes.server = {
    imports = [ ../modules ];
    virtualisation.memorySize = 4096;
    virtualisation.diskSize = 6144;
    frogswamp = {
      adminUser = "pupsich";
      jellyfin.enable = true;
      qbittorrent = {
        enable = true;
        webuiPasswordHashFile = "${./fixtures/qbittorrent-password-hash}"; # password: testpw
      };
      arr = {
        enable = true;
        environmentFiles = pkgs.lib.genAttrs [ "radarr" "sonarr" "prowlarr" ] (
          app: file "${app}.env" "${pkgs.lib.toUpper app}__AUTH__APIKEY=${keys.${app}}\n"
        );
        apiKeyFiles = pkgs.lib.mapAttrs (n: k: file "${n}.key" k) keys;
      };
      mediaProvision = {
        enable = true;
        uiPasswordFile = file "ui-password" "testpw";
        jellyfinApiKeyFile = "/run/jellyfin-test-key"; # written by the test after Jellyfin's wizard
      };
    };
  };
  testScript = ''
    import json
    keys = ${builtins.toJSON keys}
    ports = {"radarr": (7878, "v3"), "sonarr": (8989, "v3"), "prowlarr": (9696, "v1")}

    def api(app, path, method="GET", body=None):
        port, ver = ports[app]
        data = f"-X {method} -H 'Content-Type: application/json' -d '{json.dumps(body)}'" if body is not None else ""
        return json.loads(server.succeed(f"curl -sf {data} -H 'X-Api-Key: {keys[app]}' http://127.0.0.1:{port}/api/{ver}{path}") or "null")

    def fields(obj):
        return {f["name"]: f.get("value") for f in obj["fields"]}

    server.wait_for_unit("media-provision.service", timeout=900)

    for app, root, cat_field, cat in [("radarr", "/srv/media/movies", "movieCategory", "radarr"), ("sonarr", "/srv/media/shows", "tvCategory", "sonarr")]:
        assert [r["path"].rstrip("/") for r in api(app, "/rootfolder")] == [root], app
        dc = api(app, "/downloadclient")
        assert len(dc) == 1 and fields(dc[0])[cat_field] == cat and dc[0]["removeCompletedDownloads"] is False, dc
        assert fields(dc[0])["sequentialOrder"] is True and fields(dc[0])["firstAndLast"] is True
        qp = [p for p in api(app, "/qualityprofile") if p["name"] == "frogswamp"]
        assert len(qp) == 1 and qp[0]["upgradeAllowed"] is False, qp
        allowed = {i["quality"]["name"] for item in qp[0]["items"] for i in (item.get("items") or [item]) if i["allowed"]}
        assert any("2160p" in q for q in allowed) and any("1080p" in q for q in allowed), allowed
        assert not any(("720p" in q or "480p" in q) for q in allowed), allowed
        cfs = {cf["id"]: cf["name"] for cf in api(app, "/customformat")}
        scores = {cfs[f["format"]]: f["score"] for f in qp[0]["formatItems"] if f["format"] in cfs}
        assert scores == {"Russian audio": 100, "English audio": 10, "Polish audio": 10}, scores
        if "language" in qp[0]:  # Radarr only: "Original" would reject Russian releases of English films
            assert qp[0]["language"]["name"] == "Any", qp[0]["language"]
        mm = api(app, "/config/mediamanagement")
        assert mm["minimumFreeSpaceWhenImporting"] == 20480 and mm["copyUsingHardlinks"] is True, mm
        assert api(app, "/config/host")["username"] == "pupsich"

    assert {a["name"] for a in api("prowlarr", "/applications")} == {"Radarr", "Sonarr"}

    b = json.loads(server.succeed(f"curl -sf -H 'X-API-KEY: {keys['bazarr']}' http://127.0.0.1:6767/api/system/settings"))
    assert b["general"]["use_radarr"] and b["general"]["use_sonarr"], b["general"]
    profiles = json.loads(server.succeed(f"curl -sf -H 'X-API-KEY: {keys['bazarr']}' http://127.0.0.1:6767/api/system/languages/profiles"))
    assert [i["language"] for i in profiles[0]["items"]] == ["ru", "en", "pl"], profiles

    # Jellyfin: wizard, create an API key, re-run the provisioner, libraries appear
    server.wait_until_succeeds("curl -sf http://127.0.0.1:8096/health | grep -q Healthy", timeout=600)
    server.succeed("curl -sf -X POST -H 'Content-Type: application/json' -d '{\"UICulture\":\"en-US\",\"MetadataCountryCode\":\"US\",\"PreferredMetadataLanguage\":\"en\"}' http://127.0.0.1:8096/Startup/Configuration")
    server.succeed("curl -sf http://127.0.0.1:8096/Startup/User >/dev/null")
    server.succeed("curl -sf -X POST -H 'Content-Type: application/json' -d '{\"Name\":\"admin\",\"Password\":\"pw\"}' http://127.0.0.1:8096/Startup/User")
    server.succeed("curl -sf -X POST http://127.0.0.1:8096/Startup/Complete")
    auth = 'MediaBrowser Client="test", Device="test", DeviceId="test", Version="1"'
    token = json.loads(server.succeed(f"curl -sf -X POST -H 'Authorization: {auth}' -H 'Content-Type: application/json' -d '{{\"Username\":\"admin\",\"Pw\":\"pw\"}}' http://127.0.0.1:8096/Users/AuthenticateByName"))["AccessToken"]
    server.succeed(f"curl -sf -X POST -H 'X-Emby-Token: {token}' 'http://127.0.0.1:8096/Auth/Keys?App=provision'")
    key = json.loads(server.succeed(f"curl -sf -H 'X-Emby-Token: {token}' http://127.0.0.1:8096/Auth/Keys"))["Items"][0]["AccessToken"]
    server.succeed(f"echo -n {key} > /run/jellyfin-test-key")
    server.succeed("systemctl restart media-provision.service")
    libs = json.loads(server.succeed(f"curl -sf -H 'X-Emby-Token: {key}' http://127.0.0.1:8096/Library/VirtualFolders"))
    by_name = {l["Name"]: l for l in libs}
    assert sorted(by_name) == ["Movies", "Shows"], libs
    assert sorted(by_name["Movies"]["Locations"]) == ["/srv/media/downloads/radarr", "/srv/media/movies"], libs
    assert sorted(by_name["Shows"]["Locations"]) == ["/srv/media/downloads/sonarr", "/srv/media/shows"], libs

    # a folder missing from an existing library (older setup) is added back
    server.succeed(f"curl -sf -X DELETE -H 'X-Emby-Token: {key}' 'http://127.0.0.1:8096/Library/VirtualFolders/Paths?name=Movies&path=/srv/media/downloads/radarr&refreshLibrary=false'")
    server.succeed("systemctl restart media-provision.service")
    libs = json.loads(server.succeed(f"curl -sf -H 'X-Emby-Token: {key}' http://127.0.0.1:8096/Library/VirtualFolders"))
    movies = next(l for l in libs if l["Name"] == "Movies")
    assert sorted(movies["Locations"]) == ["/srv/media/downloads/radarr", "/srv/media/movies"], libs

    # Review Focus 5: a UI change survives a re-run, nothing is duplicated
    mm = api("radarr", "/config/mediamanagement"); mm["minimumFreeSpaceWhenImporting"] = 5000
    api("radarr", f"/config/mediamanagement/{mm['id']}", "PUT", mm)
    server.succeed("systemctl restart media-provision.service")
    assert api("radarr", "/config/mediamanagement")["minimumFreeSpaceWhenImporting"] == 5000
    assert len(api("radarr", "/rootfolder")) == 1 and len(api("radarr", "/downloadclient")) == 1
    assert len([p for p in api("radarr", "/qualityprofile") if p["name"] == "frogswamp"]) == 1
    assert len(json.loads(server.succeed(f"curl -sf -H 'X-Emby-Token: {key}' http://127.0.0.1:8096/Library/VirtualFolders"))) == len(libs)

    # Review Focus 1 (precondition): Radarr can hardlink a download into the library (same filesystem)
    server.succeed("install -o qbittorrent -g media -m 664 /dev/null /srv/media/downloads/radarr/x.mkv")
    server.succeed("su -s /bin/sh radarr -c 'ln /srv/media/downloads/radarr/x.mkv /srv/media/movies/x.mkv'")
    assert server.succeed("stat -c %i /srv/media/downloads/radarr/x.mkv") == server.succeed("stat -c %i /srv/media/movies/x.mkv")
  '';
}
