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
}
