{
  lib,
  modulesPath,
  pkgs,
  ...
}:
{
  imports = [ "${modulesPath}/installer/cd-dvd/installation-cd-minimal.nix" ];

  nixpkgs.hostPlatform = "x86_64-linux";
  networking.hostName = "frogswamp-installer";
  users.users.root.openssh.authorizedKeys.keys = [ (lib.fileContents ../../keys/admin.pub) ];
  services.openssh.settings.PermitRootLogin = lib.mkForce "prohibit-password";
  services.avahi = {
    enable = true;
    nssmdns4 = true;
    publish = {
      enable = true;
      addresses = true;
    };
  };
  environment.systemPackages = [ pkgs.nixos-facter ];
}
