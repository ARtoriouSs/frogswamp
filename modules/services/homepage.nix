{ config, lib, ... }:
let
  cfg = config.frogswamp.homepage;
  addr = config.frogswamp.lan.publicAddress;
  link = port: "http://${addr}:${toString port}";
in
{
  options.frogswamp.homepage = {
    enable = lib.mkEnableOption "Homepage dashboard";
    environmentFile = lib.mkOption {
      type = lib.types.str;
      description = "Runtime env file with HOMEPAGE_VAR_HA_TOKEN and HOMEPAGE_VAR_JELLYFIN_KEY";
    };
  };

  config = lib.mkIf cfg.enable {
    services.homepage-dashboard = {
      enable = true;
      listenPort = 8082;
      openFirewall = true;
      inherit (cfg) environmentFile;
      allowedHosts = lib.concatStringsSep "," [
        "${addr}:8082"
        "frogswamp:8082"
        "frogswamp.local:8082"
        "localhost:8082"
      ];
      settings = {
        title = "frogswamp";
        theme = "dark";
        color = "emerald";
      };
      services = [
        {
          Home = [
            {
              "Home Assistant" = {
                href = link 8123;
                icon = "home-assistant.png";
                widget = {
                  type = "homeassistant";
                  url = "http://127.0.0.1:8123";
                  key = "{{HOMEPAGE_VAR_HA_TOKEN}}";
                };
              };
            }
            {
              Zigbee2MQTT = {
                href = link 8080;
                icon = "zigbee2mqtt.png";
              };
            }
          ];
        }
        {
          Media = [
            {
              Jellyfin = {
                href = link 8096;
                icon = "jellyfin.png";
                widget = {
                  type = "jellyfin";
                  url = "http://127.0.0.1:8096";
                  key = "{{HOMEPAGE_VAR_JELLYFIN_KEY}}";
                  enableBlocks = true;
                };
              };
            }
            {
              "Media share" = {
                icon = "samba.png";
                description = "smb://frogswamp.local/media";
              };
            }
          ];
        }
      ];
      widgets = [
        {
          resources = {
            cpu = true;
            memory = true;
            disk = [
              "/"
              "/srv/media"
            ];
          };
        }
      ];
    };
  };
}
