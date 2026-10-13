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
  frogswamp.lan.address = lib.mkForce null;
  sops.secrets = lib.mkForce { };
  frogswamp.adminPasswordHashFile = lib.mkForce null;
  # Later tasks append service overrides below.
}
