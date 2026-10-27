{ config, lib, ... }:
let
  cfg = config.frogswamp.musicAssistant;
  stateDir = "/var/lib/music-assistant";
  # Own user: the image's default is root
  uid = 8095;
in
{
  options.frogswamp.musicAssistant = {
    enable = lib.mkEnableOption "Music Assistant server (Spotify to the Voice PE and other players)";
    image = lib.mkOption {
      type = lib.types.str;
      default = (import ./image.nix).image;
    };
    autoStart = lib.mkOption {
      type = lib.types.bool;
      default = true;
    };
  };

  config = lib.mkIf cfg.enable {
    users.users.music-assistant = {
      inherit uid;
      isSystemUser = true;
      group = "music-assistant";
    };
    users.groups.music-assistant.gid = uid;

    virtualisation = {
      podman.enable = true;
      oci-containers.backend = "podman";
      oci-containers.containers.music-assistant = {
        inherit (cfg) image autoStart;
        volumes = [ "${stateDir}:/data" ];
        extraOptions = [
          "--network=host"
          "--user=${toString uid}:${toString uid}"
        ];
      };
    };

    systemd.tmpfiles.settings."10-music-assistant".${stateDir}.d = {
      mode = "0750";
      user = "music-assistant";
      group = "music-assistant";
    };

    # 8095: web UI and HA's connection, 8097: the audio streams players fetch
    networking.firewall.allowedTCPPorts = [
      8095
      8097
    ];
  };
}
