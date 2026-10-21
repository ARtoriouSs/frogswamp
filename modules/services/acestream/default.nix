{ config, lib, ... }:
let
  cfg = config.frogswamp.acestream;
in
{
  options.frogswamp.acestream = {
    enable = lib.mkEnableOption "Ace Stream engine with its HTTP proxy";
    image = lib.mkOption {
      type = lib.types.str;
      default = (import ./image.nix).image;
    };
    port = lib.mkOption {
      type = lib.types.port;
      default = 6878;
      readOnly = true;
      description = "HTTP port: /ace/getstream?id=… (MPEG-TS) and /ace/manifest.m3u8?id=… (HLS); fixed by the engine";
    };
  };

  config = lib.mkIf cfg.enable {
    virtualisation = {
      podman.enable = true;
      oci-containers.backend = "podman";
      oci-containers.containers.acestream = {
        inherit (cfg) image;
        # without this the engine only answers requests from inside the container
        environment.ALLOW_REMOTE_ACCESS = "yes";
        # host network, like Home Assistant: the podman bridge had no DNS or internet here,
        # and peers reach the P2P port (8621) directly, without a second NAT
        extraOptions = [ "--network=host" ];
      };
    };
    networking.firewall = {
      allowedTCPPorts = [
        cfg.port
        8621
      ];
      allowedUDPPorts = [ 8621 ];
    };
  };
}
