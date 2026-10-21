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
      description = "HTTP port: /ace/getstream?id=… (MPEG-TS) and /ace/manifest.m3u8?id=… (HLS)";
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
        ports = [
          "${toString cfg.port}:6878"
          # P2P: other peers connect here; without it the stream has fewer sources
          "8621:8621"
          "8621:8621/udp"
        ];
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
