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
  frogswamp = {
    lan.address = lib.mkForce null;
    adminPasswordHashFile = lib.mkForce null;
    tailscale.enable = lib.mkForce false;
    samba.passwordFile = lib.mkForce "/etc/vm-dummy-secret";
    homeAssistant.secretsFile = lib.mkForce "${../../tests/fixtures/ha-secrets.yaml}";
    mqtt = {
      zigbee2mqtt.enable = lib.mkForce false;
      haPasswordFile = lib.mkForce "/etc/vm-dummy-secret";
      z2mPasswordFile = lib.mkForce "/etc/vm-dummy-secret";
    };
    # Later tasks add service overrides here.
  };
}
