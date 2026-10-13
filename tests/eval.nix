{ pkgs, self, ... }:
let
  inherit (pkgs) lib;
  cfg = self.nixosConfigurations.frogswamp.config;
  checks = {
    "LAN unit matches any ethernet NIC" =
      cfg.systemd.network.networks."10-lan".matchConfig == { Type = "ether"; };
    "static IP 192.168.101.10/24" =
      cfg.systemd.network.networks."10-lan".address == [ "192.168.101.10/24" ];
    "gateway 192.168.101.1" = cfg.systemd.network.networks."10-lan".gateway == [ "192.168.101.1" ];
    "SSH password auth disabled" = cfg.services.openssh.settings.PasswordAuthentication == false;
    "root fs is btrfs @root" = cfg.fileSystems."/".fsType == "btrfs";
    "media subvolume mounted" = cfg.fileSystems ? "/srv/media";
    "disko targets frogswamp.disk" = cfg.disko.devices.disk.main.device == cfg.frogswamp.disk;
    "backup disk not managed by disko" =
      lib.attrNames cfg.disko.devices.disk == [ "main" ]
      && !(lib.hasInfix "frogbackup" cfg.disko.devices.disk.main.device);
    "hostname" = cfg.networking.hostName == "frogswamp";
    "sops uses host SSH key" = cfg.sops.age.sshKeyPaths == [ "/etc/ssh/ssh_host_ed25519_key" ];
    "admin password hash from sops" =
      cfg.users.users.pupsich.hashedPasswordFile == cfg.sops.secrets."admin/password_hash".path;
    "admin hash decrypted before users" = cfg.sops.secrets."admin/password_hash".neededForUsers;
  };
  failed = lib.attrNames (lib.filterAttrs (_: ok: !ok) checks);
in
if failed == [ ] then
  pkgs.runCommand "eval-checks" { } "touch $out"
else
  throw "eval checks failed: ${lib.concatStringsSep "; " failed}"
