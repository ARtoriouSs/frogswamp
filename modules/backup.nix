{
  config,
  lib,
  pkgs,
  ...
}:
let
  cfg = config.frogswamp.backup;

  statePaths = [
    "/var/lib/home-assistant"
    "/var/lib/jellyfin"
    "/var/lib/zigbee2mqtt"
    "/var/lib/mosquitto"
    "/var/lib/tailscale"
    "/var/lib/samba/private"
    "/var/lib/qBittorrent"
  ];
  sqliteDbs = [
    "/var/lib/home-assistant/home-assistant_v2.db"
    "/var/lib/jellyfin/data/jellyfin.db"
    "/var/lib/jellyfin/data/library.db"
  ];
  # Service users whose state dirs get re-owned after restore (UIDs differ on a fresh install)
  owners = {
    "/var/lib/jellyfin" = "jellyfin";
    "/var/lib/zigbee2mqtt" = "zigbee2mqtt";
    "/var/lib/mosquitto" = "mosquitto";
    "/var/lib/qBittorrent" = "qbittorrent";
  };
  units = [
    "podman-home-assistant"
    "jellyfin"
    "zigbee2mqtt"
    "mosquitto"
    "samba-smbd"
    "tailscaled"
    "qbittorrent"
  ];

  snapshotDbs = ''
    for db in ${lib.escapeShellArgs sqliteDbs}; do
      if [ -f "$db" ]; then ${pkgs.sqlite}/bin/sqlite3 "$db" ".backup '$db.bak'"; fi
    done
  '';
  cleanupDbs = ''
    for db in ${lib.escapeShellArgs sqliteDbs}; do rm -f "$db.bak"; done
  '';

  common = {
    dynamicFilesFrom = ''
      # `if` instead of `&&`: a missing last path must not make the whole step fail
      for p in ${lib.escapeShellArgs statePaths}; do if [ -e "$p" ]; then echo "$p"; fi; done
    '';
    exclude = [
      "/var/lib/home-assistant/deps"
      "/var/lib/home-assistant/tts"
      "/var/lib/home-assistant/*.log*"
      "/var/lib/jellyfin/transcodes"
    ]
    ++ lib.concatMap (db: [
      db
      "${db}-wal"
      "${db}-shm"
    ]) sqliteDbs;
    inherit (cfg) passwordFile;
    initialize = true;
    createWrapper = true;
    pruneOpts = [
      "--keep-daily 7"
      "--keep-weekly 4"
      "--keep-monthly 6"
    ];
    timerConfig = {
      OnCalendar = "03:30";
      RandomizedDelaySec = "30m";
      Persistent = true;
    };
    backupPrepareCommand = snapshotDbs;
    backupCleanupCommand = cleanupDbs;
  };

  checkTimer = name: {
    services."restic-check-${name}" = {
      description = "Verify restic repository ${name}";
      serviceConfig.Type = "oneshot";
      script = "/run/current-system/sw/bin/restic-${name} check --read-data-subset=5%";
    };
    timers."restic-check-${name}" = {
      wantedBy = [ "timers.target" ];
      timerConfig = {
        OnCalendar = "Sun 05:00";
        Persistent = true;
      };
    };
  };

  restore = pkgs.writeShellApplication {
    name = "frogswamp-restore";
    runtimeInputs = with pkgs; [
      coreutils
      systemd
    ];
    text = ''
      repo=local
      snapshot=latest
      force=0
      while [ $# -gt 0 ]; do
        case "$1" in
          --repo) repo="$2"; shift 2 ;;
          --snapshot) snapshot="$2"; shift 2 ;;
          --force) force=1; shift ;;
          *) echo "usage: frogswamp-restore [--repo local|b2] [--snapshot ID] [--force]" >&2; exit 2 ;;
        esac
      done
      wrapper="/run/current-system/sw/bin/restic-$repo"
      if [ ! -x "$wrapper" ]; then echo "unknown or disabled repo: $repo" >&2; exit 2; fi
      if ! "$wrapper" cat config >/dev/null 2>&1; then echo "repo $repo unreachable or missing" >&2; exit 4; fi
      if [ "$force" -ne 1 ] && [ -e /var/lib/home-assistant/.storage/core.config_entries ]; then
        echo "existing Home Assistant state found; re-run with --force to overwrite it" >&2
        exit 3
      fi

      for u in ${lib.escapeShellArgs units}; do systemctl stop "$u.service" 2>/dev/null || true; done

      "$wrapper" restore "$snapshot" --target /

      for db in ${lib.escapeShellArgs sqliteDbs}; do
        if [ -f "$db.bak" ]; then
          mv -f "$db.bak" "$db"
          rm -f "$db-wal" "$db-shm"
        fi
      done

      ${lib.concatStringsSep "\n" (
        lib.mapAttrsToList (dir: user: ''
          if [ -d ${dir} ] && id -u ${user} >/dev/null 2>&1; then chown -R ${user}:"$(id -gn ${user})" ${dir}; fi
        '') owners
      )}

      for u in ${lib.escapeShellArgs units}; do
        if systemctl cat "$u.service" >/dev/null 2>&1; then systemctl start "$u.service" || true; fi
      done
      echo "restore from $repo ($snapshot) complete"
    '';
  };
in
{
  options.frogswamp.backup = {
    passwordFile = lib.mkOption {
      type = lib.types.str;
      description = "Runtime path of the restic repository password (shared by all repos)";
    };
    local = {
      enable = lib.mkEnableOption "restic backups to the external USB disk";
      mountPoint = lib.mkOption {
        type = lib.types.str;
        default = "/mnt/backup";
      };
      device = lib.mkOption {
        type = lib.types.str;
        default = "/dev/disk/by-label/frogbackup";
      };
    };
    b2 = {
      enable = lib.mkEnableOption "restic backups to Backblaze B2";
      repository = lib.mkOption {
        type = lib.types.str;
        default = "";
        example = "s3:https://s3.eu-central-003.backblazeb2.com/frogswamp-backup";
      };
      environmentFile = lib.mkOption {
        type = lib.types.str;
        default = "";
        description = "Env file with AWS_ACCESS_KEY_ID and AWS_SECRET_ACCESS_KEY (B2 key id/key)";
      };
    };
  };

  config = lib.mkIf (cfg.local.enable || cfg.b2.enable) (
    lib.mkMerge [
      {
        # rsync lets `just backup-pull` copy the local repository to the management PC
        environment.systemPackages = [
          restore
          pkgs.rsync
        ];
      }
      (lib.mkIf cfg.local.enable {
        fileSystems.${cfg.local.mountPoint} = {
          inherit (cfg.local) device;
          fsType = "ext4";
          options = [
            "nofail"
            "noatime"
            "x-systemd.device-timeout=5s"
          ];
        };
        services.restic.backups.local = common // {
          repository = "${cfg.local.mountPoint}/restic";
        };
        systemd = lib.mkMerge [
          {
            # Mount the disk first, and refuse to run at all unless it is really mounted;
            # otherwise restic would initialise a repository on the root filesystem
            services.restic-backups-local.unitConfig = {
              RequiresMountsFor = [ cfg.local.mountPoint ];
              AssertPathIsMountPoint = cfg.local.mountPoint;
            };
          }
          (checkTimer "local")
        ];
      })
      (lib.mkIf cfg.b2.enable {
        services.restic.backups.b2 = common // {
          inherit (cfg.b2) repository environmentFile;
        };
        systemd = checkTimer "b2";
      })
    ]
  );
}
