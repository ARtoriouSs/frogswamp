{
  config,
  lib,
  pkgs,
  ...
}:
let
  cfg = config.frogswamp.nutrition;
  uid = 8131;
  stateDir = "/var/lib/nutrition-sync";
  python = pkgs.python3.withPackages (ps: [ ps.tzdata ]);
  src = pkgs.runCommand "nutrition-sync-src" { } ''
    mkdir $out
    cp ${./nutrition_sync.py} $out/nutrition_sync.py
    cp ${./nutrition_core.py} $out/nutrition_core.py
  '';
  secretOption =
    description:
    lib.mkOption {
      type = lib.types.str;
      inherit description;
    };
in
{
  options.frogswamp.nutrition = {
    enable = lib.mkEnableOption "meals from Notion as a daily CSV and /status for HA, mirrored to Google Health";
    port = lib.mkOption {
      type = lib.types.port;
      default = 8131;
    };
    interval = lib.mkOption {
      type = lib.types.ints.positive;
      default = 300;
      description = "Seconds between Notion reads";
    };
    google.enable = lib.mkOption {
      type = lib.types.bool;
      default = true;
    };
    notionTokenFile = secretOption "Notion internal integration token, read-only";
    notionMealsSourceFile = secretOption "Data source id of the Nutrition log database";
    notionDaysSourceFile = secretOption "Data source id of the Nutrition days database";
    googleClientIdFile = secretOption "Google OAuth Desktop client id";
    googleClientSecretFile = secretOption "Google OAuth Desktop client secret";
    googleRefreshTokenFile = secretOption "Refresh token from just nutrition-google-auth";
    # Only the VM test points these at a fake
    notionUrl = lib.mkOption {
      type = lib.types.str;
      default = "https://api.notion.com";
    };
    googleApiUrl = lib.mkOption {
      type = lib.types.str;
      default = "https://health.googleapis.com/v4/users/me/dataTypes/nutrition-log/dataPoints";
    };
    googleTokenUrl = lib.mkOption {
      type = lib.types.str;
      default = "https://oauth2.googleapis.com/token";
    };
  };

  config = lib.mkIf cfg.enable {
    users.users.nutrition-sync = {
      inherit uid;
      isSystemUser = true;
      group = "nutrition-sync";
    };
    users.groups.nutrition-sync.gid = uid;

    # HA serves www/ as /local, so the charts read /local/nutrition/daily.csv
    systemd.tmpfiles.settings."10-nutrition-sync"."${stateDir}/www".d = {
      mode = "0755";
      user = "nutrition-sync";
      group = "nutrition-sync";
    };

    systemd.services.nutrition-sync = {
      description = "Meals from Notion for Home Assistant and Google Health";
      wantedBy = [ "multi-user.target" ];
      wants = [ "network-online.target" ];
      after = [ "network-online.target" ];
      serviceConfig = {
        ExecStart = lib.escapeShellArgs (
          [
            "${python}/bin/python3"
            "${src}/nutrition_sync.py"
            "--credentials"
            "%d"
            "--state-dir"
            stateDir
            "--port"
            (toString cfg.port)
            "--timezone"
            config.time.timeZone
            "--interval"
            (toString cfg.interval)
            "--notion-url"
            cfg.notionUrl
            "--google-api-url"
            cfg.googleApiUrl
            "--google-token-url"
            cfg.googleTokenUrl
          ]
          ++ lib.optional (!cfg.google.enable) "--no-google"
        );
        LoadCredential = [
          "notion_token:${cfg.notionTokenFile}"
          "notion_meals_source:${cfg.notionMealsSourceFile}"
          "notion_days_source:${cfg.notionDaysSourceFile}"
        ]
        ++ lib.optionals cfg.google.enable [
          "google_client_id:${cfg.googleClientIdFile}"
          "google_client_secret:${cfg.googleClientSecretFile}"
          "google_refresh_token:${cfg.googleRefreshTokenFile}"
        ];
        User = "nutrition-sync";
        Group = "nutrition-sync";
        StateDirectory = "nutrition-sync";
        StateDirectoryMode = "0755";
        Restart = "always";
        RestartSec = 30;
        CapabilityBoundingSet = "";
        NoNewPrivileges = true;
        PrivateDevices = true;
        PrivateTmp = true;
        ProtectHome = true;
        ProtectSystem = "strict";
        ProtectKernelTunables = true;
        ProtectControlGroups = true;
        RestrictAddressFamilies = [
          "AF_INET"
          "AF_INET6"
        ];
      };
    };
  };
}
