{
  config,
  lib,
  pkgs,
  ...
}:
let
  cfg = config.frogswamp.mqtt;
  z2m = cfg.zigbee2mqtt;
in
{
  options.frogswamp.mqtt = {
    enable = lib.mkEnableOption "Mosquitto MQTT broker (+ Zigbee2MQTT)";
    haPasswordFile = lib.mkOption { type = lib.types.str; };
    z2mPasswordFile = lib.mkOption { type = lib.types.str; };
    zigbee2mqtt = {
      enable = lib.mkOption {
        type = lib.types.bool;
        default = true;
      };
      package = lib.mkOption {
        type = lib.types.package;
        default = pkgs.zigbee2mqtt;
      };
      serialPort = lib.mkOption {
        type = lib.types.str;
        default = "";
        description = "Stable /dev/serial/by-id/... path of the Zigbee dongle.";
      };
      passwordFile = lib.mkOption {
        type = lib.types.str;
        default = cfg.z2mPasswordFile;
        description = "Copy of the z2m MQTT password readable by the zigbee2mqtt user.";
      };
    };
  };

  config = lib.mkIf cfg.enable (
    lib.mkMerge [
      {
        services.mosquitto = {
          enable = true;
          listeners = [
            {
              address = "127.0.0.1";
              port = 1883;
              users = {
                ha = {
                  passwordFile = cfg.haPasswordFile;
                  acl = [ "readwrite #" ];
                };
                z2m = {
                  passwordFile = cfg.z2mPasswordFile;
                  acl = [ "readwrite #" ];
                };
              };
            }
          ];
        };
      }
      (lib.mkIf z2m.enable {
        services.zigbee2mqtt = {
          enable = true;
          inherit (z2m) package;
          settings = {
            homeassistant.enabled = true;
            serial = {
              port = z2m.serialPort;
              adapter = "ember";
            };
            mqtt = {
              server = "mqtt://127.0.0.1:1883";
              user = "z2m";
              password = "!secret.yaml mqtt_password";
            };
            frontend = {
              enabled = true;
              port = 8080;
            };
          };
        };
        # Z2M reads `!secret.yaml` from its data dir; render it from the password file on every start
        systemd.services.zigbee2mqtt = {
          after = [ "mosquitto.service" ];
          serviceConfig.SupplementaryGroups = [ "dialout" ];
          preStart = lib.mkBefore ''
            printf 'mqtt_password: "%s"\n' "$(cat ${z2m.passwordFile})" > ${config.services.zigbee2mqtt.dataDir}/secret.yaml
            chmod 600 ${config.services.zigbee2mqtt.dataDir}/secret.yaml
          '';
        };
        networking.firewall.allowedTCPPorts = [ 8080 ];
      })
    ]
  );
}
