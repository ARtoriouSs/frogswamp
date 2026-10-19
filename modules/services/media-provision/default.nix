{
  config,
  lib,
  pkgs,
  ...
}:
let
  cfg = config.frogswamp.mediaProvision;
  arr = config.frogswamp.arr;
  units = [
    "radarr.service"
    "sonarr.service"
    "prowlarr.service"
    "bazarr.service"
    "qbittorrent.service"
    "jellyfin.service"
  ];
  qualities =
    res:
    lib.concatMap (
      r:
      map (s: "${s}-${r}") [
        "Bluray"
        "WEBDL"
        "WEBRip"
      ]
    ) res;
  settings = pkgs.writeText "media-provision.json" (
    builtins.toJSON {
      radarr = {
        url = "http://127.0.0.1:${toString arr.ports.radarr}/api/v3";
        key_file = arr.apiKeyFiles.radarr;
        root = "/srv/media/movies";
        category = "radarr";
        qualities = qualities [
          "2160p"
          "1080p"
        ];
      };
      sonarr = {
        url = "http://127.0.0.1:${toString arr.ports.sonarr}/api/v3";
        key_file = arr.apiKeyFiles.sonarr;
        root = "/srv/media/shows";
        category = "sonarr";
        qualities = qualities [
          "2160p"
          "1080p"
        ];
      };
      prowlarr = {
        url = "http://127.0.0.1:${toString arr.ports.prowlarr}/api/v1";
        key_file = arr.apiKeyFiles.prowlarr;
      };
      bazarr = {
        url = "http://127.0.0.1:${toString arr.ports.bazarr}/api";
        key_file = arr.apiKeyFiles.bazarr;
        languages = [
          "ru"
          "en"
          "pl"
        ];
        providers = [
          "embeddedsubtitles"
          "podnapisi"
          "yifysubtitles"
        ];
      };
      qbittorrent = {
        host = "127.0.0.1";
        port = 8090;
        username = config.frogswamp.adminUser;
        password_file = cfg.uiPasswordFile;
      };
      ui = {
        username = config.frogswamp.adminUser;
        password_file = cfg.uiPasswordFile;
        allowed_hosts = [
          config.frogswamp.lan.publicAddress
          "frogswamp"
          "frogswamp.local"
          "localhost"
          "127.0.0.1"
        ];
      };
      jellyfin = {
        url = "http://127.0.0.1:8096";
        key_file = cfg.jellyfinApiKeyFile;
        # one library per type: a title shows up while downloading and stays after the import
        libraries = [
          {
            name = "Movies";
            paths = [
              "/srv/media/movies"
              "/srv/media/downloads/radarr"
            ];
            type = "movies";
          }
          {
            name = "Shows";
            paths = [
              "/srv/media/shows"
              "/srv/media/downloads/sonarr"
            ];
            type = "tvshows";
          }
        ];
      };
      min_free_mb = 20480;
      language_scores = {
        Russian = 100;
        English = 10;
        Polish = 10;
      };
    }
  );
in
{
  options.frogswamp.mediaProvision = {
    enable = lib.mkEnableOption "create-if-missing setup of the media apps";
    uiPasswordFile = lib.mkOption {
      type = lib.types.str;
      description = "Runtime path of the shared UI password (qBittorrent WebUI and Arr logins)";
    };
    jellyfinApiKeyFile = lib.mkOption {
      type = lib.types.str;
      description = "Runtime path of a Jellyfin API key; the Jellyfin step is skipped while it is missing";
    };
  };

  config = lib.mkIf cfg.enable {
    systemd.services.media-provision = {
      description = "Create missing media app settings (never changes existing ones)";
      wantedBy = [ "multi-user.target" ];
      wants = units;
      after = units;
      restartTriggers = [
        settings
        ./provision.py
      ];
      serviceConfig = {
        Type = "oneshot";
        RemainAfterExit = true;
        # the apps may still be migrating their databases on first start
        Restart = "on-failure";
        RestartSec = 30;
        ExecStart = "${pkgs.python3}/bin/python3 ${./provision.py} --config ${settings}";
      };
    };
  };
}
