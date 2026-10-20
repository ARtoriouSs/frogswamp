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
    # On first boot the login ran before DNS worked and never tried again
    systemd.services.tailscaled-autoconnect = {
      after = [ "network-online.target" ];
      wants = [ "network-online.target" ];
      serviceConfig = {
        Restart = "on-failure";
        RestartSec = "15s";
      };
    };
    networking.firewall.trustedInterfaces = [ "tailscale0" ];
  };
}
