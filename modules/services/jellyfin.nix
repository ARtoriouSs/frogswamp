{
  config,
  lib,
  pkgs,
  ...
}:
let
  cfg = config.frogswamp.jellyfin;
  # Jellyfin only watches library folders that had files at its last scan, so new titles in an
  # empty folder (first torrent, first show, after cleanup) stay hidden until the 12 h scan
  watchedDirs = [
    "movies"
    "shows"
    "downloads/radarr"
    "downloads/sonarr"
  ];
in
{
  options.frogswamp.jellyfin = {
    enable = lib.mkEnableOption "Jellyfin media server";
    libraryDirs = lib.mkOption {
      type = lib.types.listOf lib.types.str;
      default = [
        "movies"
        "shows"
        "downloads"
        "downloads/radarr"
        "downloads/sonarr"
      ];
      description = "Folders under /srv/media created on every boot, so Jellyfin libraries resolve after a reinstall";
    };
    apiKeyFile = lib.mkOption {
      type = lib.types.nullOr lib.types.str;
      default = null;
      description = "Runtime path of a Jellyfin API key; when set, a change in a library folder starts a library scan";
    };
  };

  config = lib.mkIf cfg.enable {
    services.jellyfin = {
      enable = true;
      openFirewall = true;
    };
    users = {
      groups.media = { };
      users.jellyfin.extraGroups = [
        "media"
        "render"
        "video"
      ];
      users.${config.frogswamp.adminUser}.extraGroups = [ "media" ];
    };
    systemd = {
      tmpfiles.settings."10-media" =
        lib.genAttrs ([ "/srv/media" ] ++ map (d: "/srv/media/${d}") cfg.libraryDirs)
          (_: {
            d = {
              mode = "2775";
              user = "root";
              group = "media";
            };
          });

      paths.jellyfin-rescan = lib.mkIf (cfg.apiKeyFile != null) {
        wantedBy = [ "paths.target" ];
        pathConfig.PathChanged = map (d: "/srv/media/${d}") watchedDirs;
      };
      services.jellyfin-rescan = lib.mkIf (cfg.apiKeyFile != null) {
        description = "Scan Jellyfin libraries after a library folder changes";
        after = [ "jellyfin.service" ];
        serviceConfig = {
          Type = "oneshot";
          # root: it reads the API key file
          ExecStart = pkgs.writeShellScript "jellyfin-rescan" ''
            set -eu
            # give qBittorrent or Radarr/Sonarr time to write the files, so the scan finds them
            sleep 60
            key=$(cat ${cfg.apiKeyFile})
            [ -n "$key" ] || { echo "empty Jellyfin API key" >&2; exit 1; }
            code=$(${pkgs.curl}/bin/curl -sS -o /dev/null -w '%{http_code}' -X POST \
              -H "Authorization: MediaBrowser Token=\"$key\"" http://127.0.0.1:8096/Library/Refresh)
            case "$code" in
              2??) echo "library scan started" ;;
              # fresh install before the restore: Jellyfin doesn't know the key yet, nothing to scan
              401) echo "API key not accepted by Jellyfin (fresh install?), skipping the scan" ;;
              *) echo "library scan failed: HTTP $code" >&2; exit 1 ;;
            esac
          '';
        };
      };
    };
  };
}
