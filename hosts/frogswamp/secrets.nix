{ config, lib, ... }:
{
  sops = {
    defaultSopsFile = ../../secrets/frogswamp.yaml;
    age.sshKeyPaths = [ "/etc/ssh/ssh_host_ed25519_key" ];
    secrets = lib.mkMerge [
      {
        "admin/password_hash".neededForUsers = true;
      }
      (lib.mkIf config.frogswamp.tailscale.enable {
        "tailscale/oauth_secret" = { };
      })
      # Later tasks append blocks here, one per module:
      # (lib.mkIf config.frogswamp.<module>.enable { ... })
    ];
  };

  frogswamp.adminPasswordHashFile = lib.mkDefault config.sops.secrets."admin/password_hash".path;
  frogswamp.tailscale.authKeyFile = lib.mkDefault config.sops.secrets."tailscale/oauth_secret".path;
}
