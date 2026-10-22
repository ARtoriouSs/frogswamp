{ config, lib, ... }:
let
  cfg = config.frogswamp.matterServer;
  stateDir = "/var/lib/matter-server";
  # Own user: the image's default (1000) is the admin user on the host
  uid = 5580;
in
{
  options.frogswamp.matterServer = {
    enable = lib.mkEnableOption "Matter Server for Home Assistant's Matter integration";
    image = lib.mkOption {
      type = lib.types.str;
      default = (import ./image.nix).image;
    };
    port = lib.mkOption {
      type = lib.types.port;
      default = 5580;
      readOnly = true;
      description = "WebSocket port; HA connects to ws://localhost:5580/ws";
    };
  };

  config = lib.mkIf cfg.enable {
    users.users.matter-server = {
      inherit uid;
      isSystemUser = true;
      group = "matter-server";
    };
    users.groups.matter-server.gid = uid;

    virtualisation = {
      podman.enable = true;
      oci-containers.backend = "podman";
      oci-containers.containers.matter-server = {
        inherit (cfg) image;
        # The fabric (keys of every paired device) lives here, so it's in the backup
        volumes = [ "${stateDir}:/data" ];
        # Only HA (same host) needs the WebSocket, so the port isn't on the LAN.
        # Matter itself (mDNS, device traffic) still uses the LAN through the host network
        environment.LISTEN_ADDRESS = "127.0.0.1";
        extraOptions = [
          "--network=host"
          "--user=${toString uid}:${toString uid}"
        ];
      };
    };

    systemd.tmpfiles.settings."10-matter-server".${stateDir}.d = {
      mode = "0750";
      user = "matter-server";
      group = "matter-server";
    };
  };
}
