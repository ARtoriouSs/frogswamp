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
    "tailscale login waits for the network and retries" =
      let
        unit = cfg.systemd.services.tailscaled-autoconnect;
      in
      lib.elem "network-online.target" unit.after
      && lib.elem "network-online.target" unit.wants
      && unit.serviceConfig.Restart == "on-failure";
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
    "jellyfin rescans when a download folder changes" =
      lib.elem "/srv/media/downloads/sonarr" cfg.systemd.paths.jellyfin-rescan.pathConfig.PathChanged
      && cfg.frogswamp.jellyfin.apiKeyFile == cfg.sops.secrets."jellyfin/api_key".path;
    "podman backend" = cfg.virtualisation.oci-containers.backend == "podman";
    "samba password rotation restarts sync" =
      cfg.sops.secrets."samba/password".restartUnits == [ "samba-set-password.service" ];
    "backup mount is nofail" = lib.elem "nofail" cfg.fileSystems."/mnt/backup".options;
    # evaluates the whole VM variant: a secret without a VM override breaks `just vm`
    "just vm builds" = cfg.system.build.vm.drvPath != "";
    "new Jellyfin key re-runs media setup" =
      lib.elem "media-provision.service"
        cfg.sops.secrets."jellyfin/api_key".restartUnits;
    "backup jobs run one after the other" =
      lib.elem "restic-backups-local.service" cfg.systemd.services.restic-backups-b2.after;
    # ghcr.io loses packets on this provider's route; Docker Hub has the same image (same digest)
    "acestream image from Docker Hub" =
      lib.hasPrefix "docker.io/" cfg.virtualisation.oci-containers.containers.acestream.image;
    "acestream image pinned by digest" =
      lib.hasInfix "@sha256:" cfg.virtualisation.oci-containers.containers.acestream.image;
    "acestream allows LAN access" =
      cfg.virtualisation.oci-containers.containers.acestream.environment.ALLOW_REMOTE_ACCESS == "yes";
    # the podman bridge had no DNS/internet on the server; host network also gives peers a direct path
    "acestream on the host network" =
      lib.elem "--network=host" cfg.virtualisation.oci-containers.containers.acestream.extraOptions;
    "acestream P2P port open" =
      lib.elem 8621 cfg.networking.firewall.allowedTCPPorts
      && lib.elem 8621 cfg.networking.firewall.allowedUDPPorts;
    "football tells Kodi the LAN engine URL" =
      cfg.frogswamp.football.settings.public_engine_url == "http://192.168.101.10:6878";
    "football casts to the living room TV" =
      cfg.frogswamp.football.settings.ha.cast_entity == "media_player.philips_tv";
    "football wakes the living room TV" =
      cfg.frogswamp.football.settings.ha.remote_entity == "remote.living_room_philips_tv";
    "mode switch sends the browser back to Homepage on the LAN" =
      cfg.frogswamp.modeSwitch.settings.back_url == "http://192.168.101.10:8082/";
    "mode switch flips the General actions modes" =
      cfg.frogswamp.modeSwitch.modes.guests == "input_boolean.guests_in_living_room";
    "homepage mode tiles stay in the same tab, inside Home" =
      let
        home = (lib.head (lib.filter (g: g ? Home) cfg.services.homepage-dashboard.services)).Home;
        modes = (lib.head (lib.filter (t: t ? Modes) home)).Modes;
      in
      modes != [ ] && lib.all (tile: (lib.head (lib.attrValues tile)).target == "_self") modes;
    "homepage groups in order: Home, Media, Infrastructure" =
      map (g: lib.head (lib.attrNames g)) cfg.services.homepage-dashboard.settings.layout == [
        "Home"
        "Media"
        "Infrastructure"
      ];
    "homepage Media: downloads first, then Jellyfin, Football, Media share" =
      map (t: lib.head (lib.attrNames t))
        (lib.head (lib.filter (g: g ? Media) cfg.services.homepage-dashboard.services)).Media == [
        "qBittorrent"
        "Radarr"
        "Sonarr"
        "Prowlarr"
        "Bazarr"
        "Jellyfin"
        "Football"
        "Media share"
      ];
    "matter server image pinned by digest" =
      lib.hasInfix "@sha256:" cfg.virtualisation.oci-containers.containers.matter-server.image;
    # HA reaches it on localhost; Matter needs the host network for mDNS and device traffic
    "matter server on the host network" =
      lib.elem "--network=host" cfg.virtualisation.oci-containers.containers.matter-server.extraOptions;
    "matter server WebSocket only on localhost" =
      cfg.virtualisation.oci-containers.containers.matter-server.environment.LISTEN_ADDRESS == "127.0.0.1"
      && !(lib.elem 5580 cfg.networking.firewall.allowedTCPPorts);
    # the fabric holds the keys of every paired device; losing it means pairing everything again
    "matter fabric is backed up" =
      lib.hasInfix "/var/lib/matter-server" cfg.services.restic.backups.local.dynamicFilesFrom;
    "backup disk mounts when plugged in later" =
      lib.hasInfix ''ENV{ID_FS_LABEL}=="frogbackup"'' cfg.services.udev.extraRules
      && lib.hasInfix ''ENV{SYSTEMD_WANTS}+="mnt-backup.mount"'' cfg.services.udev.extraRules;
    "local backup requires its mount" = lib.elem "/mnt/backup" (
      lib.toList cfg.systemd.services.restic-backups-local.unitConfig.RequiresMountsFor
    );
    "local backup refuses to run unmounted" =
      cfg.systemd.services.restic-backups-local.unitConfig.AssertPathIsMountPoint == "/mnt/backup";
    "b2 off-site repository configured" =
      cfg.services.restic.backups.b2.repository
      == "s3:https://s3.eu-central-003.backblazeb2.com/frogswamp-backup";
    "arr state backed up" =
      lib.all (p: lib.hasInfix p cfg.services.restic.backups.local.dynamicFilesFrom)
        [
          "/var/lib/radarr"
          "/var/lib/sonarr"
          "/var/lib/private/prowlarr"
          "/var/lib/bazarr"
        ];
    "arr ports open" = lib.all (p: lib.elem p cfg.networking.firewall.allowedTCPPorts) [
      7878
      8989
      9696
      6767
    ];
    "qbittorrent can read its password hash" =
      cfg.sops.secrets."qbittorrent/webui_password_hash".owner == "qbittorrent";
    "qbittorrent torrent port 51413" = cfg.services.qbittorrent.torrentingPort == 51413;
    "qbittorrent port open tcp+udp" =
      lib.elem 51413 cfg.networking.firewall.allowedTCPPorts
      && lib.elem 51413 cfg.networking.firewall.allowedUDPPorts;
    "qbittorrent webui 8090 open" = lib.elem 8090 cfg.networking.firewall.allowedTCPPorts;
    "janitor runs every 5 minutes" = cfg.systemd.timers.media-janitor.timerConfig.OnCalendar == "*:0/5";
    "janitor deletes for real" = !cfg.frogswamp.mediaJanitor.dryRun;
    "janitor waits 48h and warns after 5 days" =
      cfg.frogswamp.mediaJanitor.watchedAfterHours == 48
      && cfg.frogswamp.mediaJanitor.staleAfterDays == 5;
    "backups off in vm" = !(cfg.virtualisation.vmVariant.services.restic.backups ? local);
    "mosquitto localhost only" = lib.all (l: l.address == "127.0.0.1") cfg.services.mosquitto.listeners;
  };
  failed = lib.attrNames (lib.filterAttrs (_: ok: !ok) checks);
in
if failed == [ ] then
  pkgs.runCommand "eval-checks" { } "touch $out"
else
  throw "eval checks failed: ${lib.concatStringsSep "; " failed}"
