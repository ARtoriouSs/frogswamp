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
  frogswamp = {
    lan.address = lib.mkForce null;
    adminPasswordHashFile = lib.mkForce null;
    tailscale.enable = lib.mkForce false;
    # Later tasks add service overrides here.
  };
}
