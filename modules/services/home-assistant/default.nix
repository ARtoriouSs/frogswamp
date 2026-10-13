{ config, lib, ... }:
let
  cfg = config.frogswamp.homeAssistant;
  stateDir = "/var/lib/home-assistant";
  configDir = ./config;
  repoOwned = [
    "configuration.yaml"
    "packages"
    "automations"
    "scripts"
    "scenes"
    "dashboards"
  ];
in
{
  options.frogswamp.homeAssistant = {
    enable = lib.mkEnableOption "Home Assistant (OCI container)";
    image = lib.mkOption {
      type = lib.types.str;
      default = (import ./image.nix).image;
    };
    secretsFile = lib.mkOption {
      type = lib.types.str;
      description = "Runtime path of HA secrets.yaml (latitude, longitude, ...)";
    };
    autoStart = lib.mkOption {
      type = lib.types.bool;
      default = true;
    };
  };

  config = lib.mkIf cfg.enable {
    virtualisation = {
      podman.enable = true;
      oci-containers.backend = "podman";
      oci-containers.containers.home-assistant = {
        inherit (cfg) image autoStart;
        environment.TZ = config.time.timeZone;
        volumes = [
          "${stateDir}:/config"
          "/run/dbus:/run/dbus:ro"
          "${cfg.secretsFile}:/config/secrets.yaml:ro"
        ]
        ++ map (name: "${configDir}/${name}:/config/${name}:ro") repoOwned;
        extraOptions = [
          "--network=host"
          "--cap-add=NET_ADMIN"
          "--cap-add=NET_RAW"
        ];
      };
    };

    # UI-owned files: created once so the !include lines resolve, never overwritten
    systemd.tmpfiles.settings."10-home-assistant" = {
      ${stateDir}.d = {
        mode = "0755";
        user = "root";
        group = "root";
      };
      "${stateDir}/automations.yaml".f = {
        mode = "0644";
        argument = "[]";
      };
      "${stateDir}/scripts.yaml".f = {
        mode = "0644";
        argument = "{}";
      };
      "${stateDir}/scenes.yaml".f = {
        mode = "0644";
        argument = "[]";
      };
    };

    networking.firewall.allowedTCPPorts = [ 8123 ];
  };
}
