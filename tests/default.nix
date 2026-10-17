{
  pkgs,
  self,
  inputs,
}:
let
  call = file: import file { inherit pkgs self inputs; };
in
{
  test-check-secrets = call ./check-secrets.nix;
  test-base = call ./base.nix;
  eval = call ./eval.nix;
  test-mqtt = call ./mqtt.nix;
  test-home-assistant = call ./home-assistant.nix;
  test-ha-no-device-ids = call ./ha-no-device-ids.nix;
  test-jellyfin = call ./jellyfin.nix;
  test-samba = call ./samba.nix;
  test-homepage = call ./homepage.nix;
  test-backup = call ./backup.nix;
}
