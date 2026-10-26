{
  config,
  lib,
  pkgs,
  ...
}:
let
  cfg = config.frogswamp.mediaJanitor;
  arr = config.frogswamp.arr;
  settings = pkgs.writeText "media-janitor.json" (
    builtins.toJSON {
      watched_after_h = cfg.watchedAfterHours;
      qbittorrent = {
        url = "http://127.0.0.1:8090";
        username = config.frogswamp.adminUser;
        password_file = cfg.qbittorrentPasswordFile;
      };
      jellyfin = {
        url = "http://127.0.0.1:8096";
        key_file = cfg.jellyfinApiKeyFile;
      };
      radarr = {
        url = "http://127.0.0.1:${toString arr.ports.radarr}/api/v3";
        key_file = arr.apiKeyFiles.radarr;
      };
      sonarr = {
        url = "http://127.0.0.1:${toString arr.ports.sonarr}/api/v3";
        key_file = arr.apiKeyFiles.sonarr;
      };
    }
  );
in
{
  options.frogswamp.mediaJanitor = {
    enable = lib.mkEnableOption "watched-media cleanup";
    dryRun = lib.mkOption {
      type = lib.types.bool;
      default = true;
      description = "Only log what would be deleted";
    };
    watchedAfterHours = lib.mkOption {
      type = lib.types.int;
      default = 48;
    };
    qbittorrentPasswordFile = lib.mkOption { type = lib.types.str; };
    jellyfinApiKeyFile = lib.mkOption { type = lib.types.str; };
  };

  config = lib.mkIf cfg.enable {
    systemd.services.media-janitor = {
      description = "Remove finished torrents, delete watched titles";
      after = [
        "qbittorrent.service"
        "jellyfin.service"
        "radarr.service"
        "sonarr.service"
      ];
      serviceConfig = {
        Type = "oneshot";
        # root: it must stat media files and read the API key files
        ExecStart = "${pkgs.python3}/bin/python3 ${./janitor.py} --config ${settings}${lib.optionalString cfg.dryRun " --dry-run"}";
      };
    };
    systemd.timers.media-janitor = {
      wantedBy = [ "timers.target" ];
      timerConfig = {
        # often, so a finished title shows only once in Jellyfin after a few minutes
        OnCalendar = "*:0/5";
        Persistent = true;
      };
    };
  };
}
