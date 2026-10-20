{
  config,
  lib,
  pkgs,
  utils,
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
    "/var/lib/radarr"
    "/var/lib/sonarr"
    "/var/lib/private/prowlarr"
    "/var/lib/bazarr"
  ];
  sqliteDbs = [
    "/var/lib/home-assistant/home-assistant_v2.db"
    "/var/lib/jellyfin/data/jellyfin.db"
    "/var/lib/jellyfin/data/library.db"
    "/var/lib/jellyfin/data/kodisyncqueue.db"
    "/var/lib/jellyfin/data/kodisyncqueue-log.db"
    "/var/lib/radarr/.config/Radarr/radarr.db"
    "/var/lib/sonarr/.config/NzbDrone/sonarr.db"
    "/var/lib/private/prowlarr/prowlarr.db"
    "/var/lib/bazarr/db/bazarr.db"
  ];
  # Service users whose state dirs get re-owned after restore (UIDs differ on a fresh install)
  owners = {
    "/var/lib/jellyfin" = "jellyfin";
    "/var/lib/zigbee2mqtt" = "zigbee2mqtt";
    "/var/lib/mosquitto" = "mosquitto";
    "/var/lib/qBittorrent" = "qbittorrent";
    "/var/lib/radarr" = "radarr";
    "/var/lib/sonarr" = "sonarr";
    "/var/lib/bazarr" = "bazarr";
  };
  units = [
    "podman-home-assistant"
    "jellyfin"
    "zigbee2mqtt"
    "mosquitto"
    "samba-smbd"
    "tailscaled"
    "qbittorrent"
    "radarr"
    "sonarr"
    "prowlarr"
    "bazarr"
  ];

  # Helpers that start apps or change their state; stopped while a restore writes that state
  helpers = [
    "media-provision.service"
    "media-janitor.timer"
    "media-janitor.service"
    "jellyfin-rescan.path"
    "jellyfin-rescan.service"
    "home-assistant-bluetooth-recover.service"
    "restic-backups-local.service"
    "restic-backups-b2.service"
  ];

  # Each job has its own DB copies, so two jobs running at once can't spoil each other's
  jobs = [
    "local"
    "b2"
  ];
  snapshotDbs = job: ''
    for db in ${lib.escapeShellArgs sqliteDbs}; do
      if [ -f "$db" ]; then ${pkgs.sqlite}/bin/sqlite3 "$db" ".backup '$db.snapshot-${job}'"; fi
    done
  '';
  cleanupDbs = job: ''
    for db in ${lib.escapeShellArgs sqliteDbs}; do rm -f "$db.snapshot-${job}"; done
  '';

  common = job: {
    dynamicFilesFrom = ''
      # `if` instead of `&&`: a missing last path must not make the whole step fail
      for p in ${lib.escapeShellArgs statePaths}; do if [ -e "$p" ]; then echo "$p"; fi; done
    '';
    exclude = [
      "/var/lib/home-assistant/deps"
      "/var/lib/home-assistant/tts"
      "/var/lib/home-assistant/*.log*"
      "/var/lib/jellyfin/transcodes"
      # Arr caches and their own backups: re-creatable, often large
      "/var/lib/radarr/.config/Radarr/MediaCover"
      "/var/lib/radarr/.config/Radarr/Backups"
      "/var/lib/radarr/.config/Radarr/logs"
      "/var/lib/sonarr/.config/NzbDrone/MediaCover"
      "/var/lib/sonarr/.config/NzbDrone/Backups"
      "/var/lib/sonarr/.config/NzbDrone/logs"
      "/var/lib/private/prowlarr/Backups"
      "/var/lib/private/prowlarr/logs"
      "/var/lib/bazarr/log"
      # log databases: copied live they can be inconsistent, and the apps create new ones
      "/var/lib/radarr/.config/Radarr/logs.db*"
      "/var/lib/sonarr/.config/NzbDrone/logs.db*"
      "/var/lib/private/prowlarr/logs.db*"
    ]
    ++ lib.concatMap (
      db:
      [
        db
        "${db}-wal"
        "${db}-shm"
        "${db}.bak"
      ]
      ++ map (other: "${db}.snapshot-${other}") (lib.remove job jobs)
    ) sqliteDbs;
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
    backupPrepareCommand = snapshotDbs job;
    backupCleanupCommand = cleanupDbs job;
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

  # Publishes the backup state to Home Assistant, for the dashboard
  status = pkgs.writeShellApplication {
    name = "frogswamp-backup-status";
    runtimeInputs = with pkgs; [
      coreutils
      curl
      jq
      util-linux
    ];
    text = ''
      token="$(cat ${toString cfg.haTokenFile})"
      post() {
        curl -fsS -o /dev/null -X POST -H "Authorization: Bearer $token" -H "Content-Type: application/json" \
          -d "$2" "${cfg.haUrl}/api/states/$1"
      }

      used=null
      if mountpoint -q ${cfg.local.mountPoint}; then
        disk=mounted
        used="$(df --output=pcent ${cfg.local.mountPoint} | tail -1 | tr -dc 0-9)"
      else
        disk=missing
      fi
      post sensor.backup_disk "$(jq -cn --arg s "$disk" --argjson u "$used" \
        '{state: $s, attributes: {friendly_name: "Backup disk", icon: "mdi:harddisk", used_percent: $u}}')"

      repos=(${lib.escapeShellArgs (lib.filter (j: cfg.${j}.enable) jobs)})
      for repo in "''${repos[@]}"; do
        # time of the newest snapshot, or why there is none
        if ! last="$("/run/current-system/sw/bin/restic-$repo" snapshots --no-lock --json 2>/dev/null \
          | jq -r 'max_by(.time) | .time // "none"')"; then
          last=unreachable
        fi
        post "sensor.backup_last_$repo" "$(jq -cn --arg s "$last" --arg r "$repo" \
          '{state: $s, attributes: {friendly_name: "Last backup (\($r))", icon: "mdi:backup-restore", device_class: "timestamp"}}')"
      done
    '';
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

      # helpers first: media-provision would otherwise start the apps again during the restore
      for u in ${lib.escapeShellArgs helpers}; do systemctl stop "$u" 2>/dev/null || true; done
      for u in ${lib.escapeShellArgs units}; do systemctl stop "$u.service" 2>/dev/null || true; done

      "$wrapper" restore "$snapshot" --target /

      for db in ${lib.escapeShellArgs sqliteDbs}; do
        # .bak: snapshots made before each job had its own copies
        for copy in "$db.snapshot-$repo" "$db.bak"; do
          if [ -f "$copy" ]; then
            mv -f "$copy" "$db"
            rm -f "$db-wal" "$db-shm"
            break
          fi
        done
        rm -f "$db".snapshot-* "$db.bak"
      done

      ${lib.concatStringsSep "\n" (
        lib.mapAttrsToList (dir: user: ''
          if [ -d ${dir} ] && id -u ${user} >/dev/null 2>&1; then chown -R ${user}:"$(id -gn ${user})" ${dir}; fi
        '') owners
      )}

      # restart, not start: an app started meanwhile would keep its old, replaced files open
      for u in ${lib.escapeShellArgs units}; do
        if systemctl cat "$u.service" >/dev/null 2>&1; then systemctl restart "$u.service" || true; fi
      done
      for u in media-provision.service media-janitor.timer jellyfin-rescan.path; do
        if systemctl cat "$u" >/dev/null 2>&1; then systemctl start "$u" || true; fi
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
    haTokenFile = lib.mkOption {
      type = lib.types.nullOr lib.types.str;
      default = null;
      description = "Runtime path of a Home Assistant token; when set, the backup state is published as HA sensors";
    };
    haUrl = lib.mkOption {
      type = lib.types.str;
      default = "http://127.0.0.1:8123";
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
        # The mount above only runs at boot. Mount the disk also when it shows up later
        # (plugged in again, or the USB link failed at boot and came back after a re-plug)
        services.udev.extraRules = lib.mkIf (lib.hasPrefix "/dev/disk/by-label/" cfg.local.device) ''
          SUBSYSTEM=="block", ENV{ID_FS_LABEL}=="${lib.removePrefix "/dev/disk/by-label/" cfg.local.device}", TAG+="systemd", ENV{SYSTEMD_WANTS}+="${utils.escapeSystemdPath cfg.local.mountPoint}.mount"
        '';
        services.restic.backups.local = common "local" // {
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
        services.restic.backups.b2 = common "b2" // {
          inherit (cfg.b2) repository environmentFile;
        };
        systemd = lib.mkMerge [
          (checkTimer "b2")
          # both start at 03:30; one after the other keeps the night's load low
          { services.restic-backups-b2.after = lib.optional cfg.local.enable "restic-backups-local.service"; }
        ];
      })
      (lib.mkIf (cfg.haTokenFile != null) {
        systemd = {
          services = {
            backup-status = {
              description = "Publish the backup state to Home Assistant";
              # when started by the disk's udev event, wait for the mount
              after = [
                "${utils.escapeSystemdPath cfg.local.mountPoint}.mount"
                "network-online.target"
              ];
              wants = [ "network-online.target" ];
              serviceConfig = {
                Type = "oneshot";
                # root: it reads the restic password and the HA token
                ExecStart = lib.getExe status;
              };
            };
          }
          // lib.genAttrs (map (j: "restic-backups-${j}") (lib.filter (j: cfg.${j}.enable) jobs)) (_: {
            onSuccess = [ "backup-status.service" ];
            onFailure = [ "backup-status.service" ];
          });
          timers.backup-status = {
            wantedBy = [ "timers.target" ];
            timerConfig = {
              OnBootSec = "3min";
              OnUnitActiveSec = "1h"; # also notices an unplugged disk
            };
          };
        };
        services.udev.extraRules =
          lib.mkIf (cfg.local.enable && lib.hasPrefix "/dev/disk/by-label/" cfg.local.device)
            ''
              SUBSYSTEM=="block", ENV{ID_FS_LABEL}=="${lib.removePrefix "/dev/disk/by-label/" cfg.local.device}", ENV{SYSTEMD_WANTS}+="backup-status.service"
            '';
      })
    ]
  );
}
