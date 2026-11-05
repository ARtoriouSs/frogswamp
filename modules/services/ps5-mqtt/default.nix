{ config, lib, ... }:
let
  cfg = config.frogswamp.ps5Mqtt;
  stateDir = "/var/lib/ps5-mqtt";
  uid = 8645;
in
{
  options.frogswamp.ps5Mqtt = {
    enable = lib.mkEnableOption "PS5-MQTT: wake and rest mode of the PS5 from Home Assistant";
    image = lib.mkOption {
      type = lib.types.str;
      default = (import ./image.nix).image;
    };
    environmentFile = lib.mkOption {
      type = lib.types.str;
      description = "Runtime path of the file with MQTT_PASSWORD";
    };
  };

  config = lib.mkIf cfg.enable {
    users.users.ps5-mqtt = {
      inherit uid;
      isSystemUser = true;
      group = "ps5-mqtt";
    };
    users.groups.ps5-mqtt.gid = uid;

    virtualisation = {
      podman.enable = true;
      oci-containers.backend = "podman";
      oci-containers.containers.ps5-mqtt = {
        inherit (cfg) image;
        # The console pairing lives here, so it's in the backup
        volumes = [ "${stateDir}:/config" ];
        environmentFiles = [ cfg.environmentFile ];
        environment = {
          HOME = "/config";
          MQTT_HOST = "127.0.0.1";
          MQTT_PORT = "1883";
          MQTT_USERNAME = "ps5";
          CREDENTIAL_STORAGE_PATH = "/config/credentials.json";
          INCLUDE_PS4_DEVICES = "false";
          # The firewall drops replies to a broadcast, but lets through a reply to a direct request.
          # The address is a DHCP reservation, see README
          DEVICE_DISCOVERY_BROADCAST_ADDRESS = "192.168.101.132";
          # The pairing page has no login, and the firewall keeps it off the LAN
          FRONTEND_PORT = "8645";
          # The sensitive namespaces print the MQTT password, keep them out of the journal
          DEBUG = "@ha:ps5,@ha:ps5:*,-@ha:ps5:poll*,-@ha:ps5:check*";
        };
        # Wakes the console and listens for its replies over UDP, so it needs the host network
        extraOptions = [
          "--network=host"
          "--user=${toString uid}:${toString uid}"
          "--cap-drop=ALL"
          "--security-opt=no-new-privileges"
        ];
      };
    };

    systemd.services.podman-ps5-mqtt.after = [ "mosquitto.service" ];

    systemd.tmpfiles.settings."10-ps5-mqtt".${stateDir}.d = {
      mode = "0750";
      user = "ps5-mqtt";
      group = "ps5-mqtt";
    };
  };
}
