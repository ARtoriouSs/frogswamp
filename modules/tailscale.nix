{ config, lib, ... }:
let
  cfg = config.frogswamp.tailscale;
in
{
  options.frogswamp.tailscale = {
    enable = lib.mkEnableOption "Tailscale subnet router";
    authKeyFile = lib.mkOption {
      type = lib.types.str;
      description = "Runtime path of a Tailscale OAuth client secret (tag:homelab).";
    };
  };

  config = lib.mkIf cfg.enable {
    services.tailscale = {
      enable = true;
      openFirewall = true;
      useRoutingFeatures = "server";
      inherit (cfg) authKeyFile;
      authKeyParameters = {
        ephemeral = false;
        preauthorized = true;
      };
      extraUpFlags = [
        "--advertise-routes=${config.frogswamp.lan.subnet}"
        "--advertise-tags=tag:homelab"
      ];
    };
    networking.firewall.trustedInterfaces = [ "tailscale0" ];
  };
}
