{
  config,
  lib,
  pkgs,
  ...
}:
let
  cfg = config.frogswamp.qbittorrent;
  profileDir = "/var/lib/qBittorrent";
  confDir = "${profileDir}/qBittorrent/config";
  downloads = "/srv/media/downloads";

  # [Preferences] sorts last, so the password line appended at start lands in that section
  baseConf = pkgs.writeText "qBittorrent.conf" (
    lib.generators.toINI { } {
      LegalNotice.Accepted = true;
      BitTorrent = {
        "Session\\DefaultSavePath" = downloads;
        "Session\\TempPathEnabled" = false;
        "Session\\AddExtensionToIncompleteFiles" = false;
        # Use the category's folder (radarr → downloads/radarr) for new torrents, so Jellyfin sees them
        "Session\\DisableAutoTMMByDefault" = false;
        "Session\\Port" = 51413;
        # Below the line's speed, so torrents don't crowd out everything else (0 = no limit)
        "Session\\GlobalDLSpeedLimit" = cfg.speedLimits.download;
        "Session\\GlobalUPSpeedLimit" = cfg.speedLimits.upload;
        # The turtle button in the web UI switches to these, e.g. during a match
        "Session\\AlternativeGlobalDLSpeedLimit" = cfg.speedLimits.altDownload;
        "Session\\AlternativeGlobalUPSpeedLimit" = cfg.speedLimits.altUpload;
        # Stop uploading the moment a download completes; media-janitor removes the torrent after the import
        "Session\\GlobalMaxRatio" = 0;
        "Session\\GlobalMaxSeedingMinutes" = 0;
        "Session\\ShareLimitAction" = "Stop";
      };
      Preferences = {
        "WebUI\\Port" = 8090;
        "WebUI\\Username" = config.frogswamp.adminUser;
      };
    }
  );
  categories = pkgs.writeText "categories.json" (
    builtins.toJSON {
      radarr.save_path = "${downloads}/radarr";
      sonarr.save_path = "${downloads}/sonarr";
    }
  );
in
{
  options.frogswamp.qbittorrent = {
    enable = lib.mkEnableOption "qBittorrent with watch-while-downloading defaults";
    speedLimits =
      lib.genAttrs
        [
          "download"
          "upload"
          "altDownload"
          "altUpload"
        ]
        (
          name:
          lib.mkOption {
            type = lib.types.ints.unsigned;
            default = 0;
            description = "${name} limit in KiB/s, 0 = no limit (alt* = the turtle button)";
          }
        );
    webuiPasswordHashFile = lib.mkOption {
      type = lib.types.str;
      description = "Runtime path of the WebUI password as qBittorrent PBKDF2 (@ByteArray(salt:hash))";
    };
  };

  config = lib.mkIf cfg.enable {
    services.qbittorrent = {
      enable = true;
      inherit profileDir;
      webuiPort = 8090;
      torrentingPort = 51413;
      openFirewall = true;
    };

    users.users.qbittorrent.extraGroups = [ "media" ];
    # Peers connect over both TCP and uTP (UDP); the module's openFirewall doesn't cover both
    networking.firewall = {
      allowedTCPPorts = [ 51413 ];
      allowedUDPPorts = [ 51413 ];
    };

    # Started into a half-ready network (route up, DNS not yet), qBittorrent stays dead until a restart:
    # 0 DHT nodes and no peer connections, though tracker announces still work.
    # So restart it when the LAN comes back (router reboot, cable). At boot it isn't active yet, so it's skipped
    services.networkd-dispatcher = {
      enable = true;
      rules.qbittorrent-restart = {
        onState = [ "routable" ];
        script = ''
          [ -n "$(${pkgs.iproute2}/bin/ip route show default dev "$IFACE")" ] || exit 0
          if ${config.systemd.package}/bin/systemctl is-active --quiet qbittorrent.service; then
            echo "$IFACE is routable again, restarting qBittorrent"
            ${config.systemd.package}/bin/systemctl restart --no-block qbittorrent.service
          fi
        '';
      };
    };

    # The config is written here instead of `serverConfig`, so the password hash (a runtime secret) can be added
    systemd.services.qbittorrent = {
      restartTriggers = [
        baseConf
        categories
      ];
      serviceConfig = {
        UMask = "0002";
        ExecStartPre = [
          # network-online.target only means the route is up; also wait for DNS (DHT bootstrap needs it).
          # Starts anyway after 60 s, so torrents still run without DNS
          (pkgs.writeShellScript "qbittorrent-wait-dns" ''
            for _ in $(seq 60); do
              ${pkgs.getent}/bin/getent hosts dht.transmissionbt.com >/dev/null && exit 0
              sleep 1
            done
            echo "DNS still not working after 60 s, starting anyway" >&2
          '')
          (pkgs.writeShellScript "qbittorrent-config" ''
            set -eu
            install -d -m 700 ${confDir}
            install -m 600 ${baseConf} ${confDir}/qBittorrent.conf
            # read first: a failing $(cat) inside printf would not stop the script and leave an empty password
            hash=$(cat ${cfg.webuiPasswordHashFile})
            [ -n "$hash" ] || { echo "empty WebUI password hash" >&2; exit 1; }
            printf 'WebUI\\Password_PBKDF2="%s"\n' "$hash" >> ${confDir}/qBittorrent.conf
            install -m 600 ${categories} ${confDir}/categories.json
          '')
        ];
      };
    };
  };
}
