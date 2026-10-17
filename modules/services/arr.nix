{
  config,
  lib,
  pkgs,
  ...
}:
let
  cfg = config.frogswamp.arr;
  ports = {
    radarr = 7878;
    sonarr = 8989;
    prowlarr = 9696;
    bazarr = 6767;
  };
  # Login with a form, but not from local addresses (LAN)
  servarrSettings = app: {
    server.port = ports.${app};
    auth = {
      method = "Forms";
      required = "DisabledForLocalAddresses";
    };
  };
  keyFileOption =
    app:
    lib.mkOption {
      type = lib.types.str;
      description = "Runtime path of the raw ${app} API key (for the provisioner and Homepage)";
    };
  envFileOption =
    app:
    lib.mkOption {
      type = lib.types.str;
      description = "Runtime path of an env file with ${lib.toUpper app}__AUTH__APIKEY=<key>";
    };
in
{
  options.frogswamp.arr = {
    enable = lib.mkEnableOption "Radarr, Sonarr, Prowlarr and Bazarr";
    environmentFiles = lib.genAttrs [ "radarr" "sonarr" "prowlarr" ] envFileOption;
    apiKeyFiles = lib.genAttrs [ "radarr" "sonarr" "prowlarr" "bazarr" ] keyFileOption;
    ports = lib.mkOption {
      type = lib.types.attrsOf lib.types.port;
      default = ports;
      readOnly = true;
    };
  };

  config = lib.mkIf cfg.enable {
    services = {
      radarr = {
        enable = true;
        openFirewall = true;
        settings = servarrSettings "radarr";
        environmentFiles = [ cfg.environmentFiles.radarr ];
      };
      sonarr = {
        enable = true;
        openFirewall = true;
        settings = servarrSettings "sonarr";
        environmentFiles = [ cfg.environmentFiles.sonarr ];
      };
      prowlarr = {
        enable = true;
        openFirewall = true;
        settings = servarrSettings "prowlarr";
        environmentFiles = [ cfg.environmentFiles.prowlarr ];
      };
      bazarr = {
        enable = true;
        openFirewall = true;
        listenPort = ports.bazarr;
      };
    };

    # Downloads, library and subtitles must stay writable by every app in the media group
    users.users = lib.genAttrs [ "radarr" "sonarr" "bazarr" ] (_: {
      extraGroups = [ "media" ];
    });
    systemd.services = {
      radarr.serviceConfig.UMask = lib.mkForce "0002";
      sonarr.serviceConfig.UMask = lib.mkForce "0002";
      bazarr.serviceConfig = {
        UMask = lib.mkForce "0002";
        # Bazarr reads its API key from config.yaml; seed it only on the very first start (create-if-missing)
        ExecStartPre = [
          (pkgs.writeShellScript "bazarr-seed-config" ''
            set -eu
            conf=${config.services.bazarr.dataDir}/config/config.yaml
            [ -e "$conf" ] && exit 0
            key=$(cat ${cfg.apiKeyFiles.bazarr})
            [ -n "$key" ] || { echo "empty Bazarr API key" >&2; exit 1; }
            install -d -m 750 "$(dirname "$conf")"
            # quoted: an all-digit key would otherwise be read as a number and replaced by Bazarr
            printf 'auth:\n  apikey: "%s"\n' "$key" > "$conf"
          '')
        ];
      };
    };
  };
}
