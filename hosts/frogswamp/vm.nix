# Overrides for `just vm`: run the real config in QEMU without real secrets or hardware.
{ lib, ... }:
{
  virtualisation = {
    memorySize = 4096;
    cores = 4;
    diskSize = 20480;
    forwardPorts = [
      {
        from = "host";
        host.port = 2222;
        guest.port = 22;
      }
    ];
  };
  sops.secrets = lib.mkForce { };
  environment.etc."vm-dummy-secret".text = "vm-dummy";
  environment.etc."vm-homepage-env".text = "HOMEPAGE_VAR_HA_TOKEN=x\nHOMEPAGE_VAR_JELLYFIN_KEY=x\n";
  frogswamp = {
    lan.address = lib.mkForce null;
    adminPasswordHashFile = lib.mkForce null;
    tailscale.enable = lib.mkForce false;
    qbittorrent.webuiPasswordHashFile = lib.mkForce "${../../tests/fixtures/qbittorrent-password-hash}";
    backup.local.enable = lib.mkForce false;
    backup.b2.enable = lib.mkForce false;
    samba.passwordFile = lib.mkForce "/etc/vm-dummy-secret";
    homepage.environmentFile = lib.mkForce "/etc/vm-homepage-env";
    homeAssistant.secretsFile = lib.mkForce "${../../tests/fixtures/ha-secrets.yaml}";
    mqtt = {
      zigbee2mqtt.enable = lib.mkForce false;
      haPasswordFile = lib.mkForce "/etc/vm-dummy-secret";
      z2mPasswordFile = lib.mkForce "/etc/vm-dummy-secret";
    };
    # Later tasks add service overrides here.
  };
}
