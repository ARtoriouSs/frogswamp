{
  pkgs,
  self,
  inputs,
  ...
}:
let
  inherit (pkgs) lib;
  cfg = self.nixosConfigurations.frogswamp.config;
  checks = {
    "LAN unit matches any ethernet NIC" =
      cfg.systemd.network.networks."10-lan".matchConfig == { Type = "ether"; };
    "static IP 192.168.101.10/24" =
      cfg.systemd.network.networks."10-lan".address == [ "192.168.101.10/24" ];
    "gateway 192.168.101.1" = cfg.systemd.network.networks."10-lan".gateway == [ "192.168.101.1" ];
    "only our LAN config manages NICs (no facter DHCP, e.g. on Wi-Fi)" =
      !(lib.any (lib.hasPrefix "40-") (lib.attrNames cfg.systemd.network.networks));
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
    "installer accepts admin key" =
      self.nixosConfigurations.installer.config.users.users.root.openssh.authorizedKeys.keys
      == cfg.frogswamp.adminSshKeys;
    "installer ships nixos-facter" = lib.any (
      p: (p.pname or "") == "nixos-facter"
    ) self.nixosConfigurations.installer.config.environment.systemPackages;
    "vm variant uses DHCP" =
      cfg.virtualisation.vmVariant.systemd.network.networks."10-lan".networkConfig.DHCP == "ipv4";
    "tailscale advertises LAN" =
      lib.elem "--advertise-routes=192.168.101.0/24" cfg.services.tailscale.extraUpFlags;
    "tailscale tagged" = lib.elem "--advertise-tags=tag:homelab" cfg.services.tailscale.extraUpFlags;
    "tailscale0 trusted" = lib.elem "tailscale0" cfg.networking.firewall.trustedInterfaces;
    "tailscale off in vm" = !cfg.virtualisation.vmVariant.services.tailscale.enable;
    "z2m uses ember adapter" = cfg.services.zigbee2mqtt.settings.serial.adapter == "ember";
    "z2m waits for the dongle instead of crash-looping" =
      lib.elem "dev-zigbee.device" cfg.systemd.services.zigbee2mqtt.bindsTo
      && lib.elem "dev-zigbee.device" cfg.systemd.services.zigbee2mqtt.wantedBy;
    "dongle udev link is a systemd device" = lib.any (
      line: lib.hasInfix ''SYMLINK+="zigbee"'' line && lib.hasInfix ''TAG+="systemd"'' line
    ) (lib.splitString "\n" cfg.services.udev.extraRules);
    "z2m uses serial-free /dev/zigbee link" =
      cfg.services.zigbee2mqtt.settings.serial.port == "/dev/zigbee";
    "udev creates /dev/zigbee for SLZB-07Mg24" =
      lib.hasInfix ''ATTRS{product}=="SMLIGHT SLZB-07Mg24"'' cfg.services.udev.extraRules
      && lib.hasInfix ''SYMLINK+="zigbee"'' cfg.services.udev.extraRules;
    "z2m from unstable" =
      cfg.services.zigbee2mqtt.package == inputs.nixpkgs-unstable.legacyPackages.x86_64-linux.zigbee2mqtt;
    "HA image pinned by digest" =
      lib.hasInfix "@sha256:" cfg.virtualisation.oci-containers.containers.home-assistant.image;
    "podman backend" = cfg.virtualisation.oci-containers.backend == "podman";
    "samba password rotation restarts sync" =
      cfg.sops.secrets."samba/password".restartUnits == [ "samba-set-password.service" ];
    "backup mount is nofail" = lib.elem "nofail" cfg.fileSystems."/mnt/backup".options;
    "local backup requires its mount" = lib.elem "/mnt/backup" (
      lib.toList cfg.systemd.services.restic-backups-local.unitConfig.RequiresMountsFor
    );
    "local backup refuses to run unmounted" =
      cfg.systemd.services.restic-backups-local.unitConfig.AssertPathIsMountPoint == "/mnt/backup";
    "b2 off-site repository configured" =
      cfg.services.restic.backups.b2.repository
      == "s3:https://s3.eu-central-003.backblazeb2.com/frogswamp-backup";
    "backups off in vm" = !(cfg.virtualisation.vmVariant.services.restic.backups ? local);
    "mosquitto localhost only" = lib.all (l: l.address == "127.0.0.1") cfg.services.mosquitto.listeners;
  };
  failed = lib.attrNames (lib.filterAttrs (_: ok: !ok) checks);
in
if failed == [ ] then
  pkgs.runCommand "eval-checks" { } "touch $out"
else
  throw "eval checks failed: ${lib.concatStringsSep "; " failed}"
