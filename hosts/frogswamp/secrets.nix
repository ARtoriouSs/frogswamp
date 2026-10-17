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
      (lib.mkIf config.frogswamp.mqtt.enable {
        "mosquitto/ha_password" = { };
        "mosquitto/z2m_password" = { };
        "zigbee2mqtt/mqtt_password" = {
          key = "mosquitto/z2m_password";
          owner = "zigbee2mqtt";
        };
      })
      (lib.mkIf config.frogswamp.homeAssistant.enable {
        "home_assistant/secrets_yaml" = { };
      })
      (lib.mkIf config.frogswamp.samba.enable {
        "samba/password".restartUnits = [ "samba-set-password.service" ];
      })
      (lib.mkIf config.frogswamp.homepage.enable {
        "homepage/env" = { };
      })
      (lib.mkIf (config.frogswamp.backup.local.enable || config.frogswamp.backup.b2.enable) {
        "restic/password" = { };
      })
      (lib.mkIf config.frogswamp.backup.b2.enable {
        "restic/b2_env" = { };
      })
      # Later tasks append blocks here, one per module:
      # (lib.mkIf config.frogswamp.<module>.enable { ... })
    ];
  };

  frogswamp = {
    adminPasswordHashFile = lib.mkDefault config.sops.secrets."admin/password_hash".path;
    tailscale.authKeyFile = lib.mkDefault config.sops.secrets."tailscale/oauth_secret".path;
    mqtt = {
      haPasswordFile = lib.mkDefault config.sops.secrets."mosquitto/ha_password".path;
      z2mPasswordFile = lib.mkDefault config.sops.secrets."mosquitto/z2m_password".path;
      zigbee2mqtt.passwordFile = lib.mkDefault config.sops.secrets."zigbee2mqtt/mqtt_password".path;
    };
    homeAssistant.secretsFile = lib.mkDefault config.sops.secrets."home_assistant/secrets_yaml".path;
    samba.passwordFile = lib.mkDefault config.sops.secrets."samba/password".path;
    homepage.environmentFile = lib.mkDefault config.sops.secrets."homepage/env".path;
    backup = {
      passwordFile = lib.mkDefault config.sops.secrets."restic/password".path;
      # Only evaluated when B2 is enabled (the secret exists only then)
      b2.environmentFile = lib.mkDefault config.sops.secrets."restic/b2_env".path;
    };
    # Later tasks add their secret file options here.
  };
}
