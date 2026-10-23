{
  config,
  lib,
  pkgs,
  ...
}:
let
  cfg = config.frogswamp.homeAssistant;
  stateDir = "/var/lib/home-assistant";
  configDir = ./config;
  hacsPin = import ./hacs.nix;
  hacs = pkgs.fetchzip {
    url = "https://github.com/hacs/integration/releases/download/${hacsPin.version}/hacs.zip";
    inherit (hacsPin) hash;
    stripRoot = false;
  };
  # Mounted as /dev/bus/usb in the container, see the Bluetooth recovery below
  usbWatchDir = "/run/home-assistant/usb-watch";
  repoOwned = [
    "configuration.yaml"
    "packages"
    "automations"
    "scripts"
    "scenes"
    "dashboards"
  ];
in
{
  options.frogswamp.homeAssistant = {
    enable = lib.mkEnableOption "Home Assistant (OCI container)";
    image = lib.mkOption {
      type = lib.types.str;
      default = (import ./image.nix).image;
    };
    secretsFile = lib.mkOption {
      type = lib.types.str;
      description = "Runtime path of HA secrets.yaml (latitude, longitude, ...)";
    };
    autoStart = lib.mkOption {
      type = lib.types.bool;
      default = true;
    };
    uiSnapshotDir = lib.mkOption {
      type = lib.types.path;
      default = ./ui-snapshot;
      description = "Files copied by `just ha-pull`; they seed the UI-owned files on a fresh install";
    };
    hacs = lib.mkOption {
      type = lib.types.bool;
      default = false;
      description = "HACS (community store), pinned in hacs.nix and mounted read-only. What it installs goes to the writable custom_components/";
    };
    recoverBluetooth = lib.mkOption {
      type = lib.types.bool;
      default = true;
      description = "Reload Home Assistant's Bluetooth integration when the adapter re-appears (e.g. after a USB reset renames hci0 to hci1)";
    };
    haTokenFile = lib.mkOption {
      type = lib.types.nullOr lib.types.str;
      default = null;
      description = "Runtime path of an HA long-lived token. With it the Bluetooth recovery refreshes HA's adapter list and reloads only the Bluetooth integration (HA is restarted only if that fails); without it, it restarts Home Assistant";
    };
  };

  config = lib.mkIf cfg.enable {
    virtualisation = {
      podman.enable = true;
      oci-containers.backend = "podman";
      oci-containers.containers.home-assistant = {
        inherit (cfg) image autoStart;
        environment.TZ = config.time.timeZone;
        volumes = [
          "${stateDir}:/config"
          "/run/dbus:/run/dbus:ro"
          "${cfg.secretsFile}:/config/secrets.yaml:ro"
        ]
        ++ map (name: "${configDir}/${name}:/config/${name}:ro") repoOwned
        ++ lib.optional cfg.hacs "${hacs}:/config/custom_components/hacs:ro"
        ++ lib.optional cfg.recoverBluetooth "${usbWatchDir}:/dev/bus/usb:ro";
        extraOptions = [
          "--network=host"
          "--cap-add=NET_ADMIN"
          "--cap-add=NET_RAW"
        ];
      };
    };

    # UI-owned files: created once so the !include lines resolve, never overwritten
    systemd.tmpfiles.settings."10-home-assistant" = {
      ${stateDir}.d = {
        mode = "0755";
        user = "root";
        group = "root";
      };
      # integrations installed by HACS land here (HACS itself is mounted in it)
      "${stateDir}/custom_components".d = {
        mode = "0755";
        user = "root";
        group = "root";
      };
      "${usbWatchDir}".d = lib.mkIf cfg.recoverBluetooth {
        mode = "0755";
        user = "root";
        group = "root";
      };
      # Copy from the UI snapshot only if missing, then make it writable (store files are read-only)
      "${stateDir}/automations.yaml" = {
        C.argument = "${cfg.uiSnapshotDir}/automations.yaml";
        z.mode = "0644";
      };
      "${stateDir}/scripts.yaml" = {
        C.argument = "${cfg.uiSnapshotDir}/scripts.yaml";
        z.mode = "0644";
      };
      "${stateDir}/scenes.yaml" = {
        C.argument = "${cfg.uiSnapshotDir}/scenes.yaml";
        z.mode = "0644";
      };
    };

    networking.firewall.allowedTCPPorts = [ 8123 ];

    # The Realtek BT chip sometimes hangs; the kernel resets it and it comes back as a new hciN.
    # HA keeps a cached list of adapter names, so it keeps looking for the old hciN even after a reload.
    # HA refreshes that list only when its USB watcher sees a change in /dev/bus/usb, and a USB reset makes no change there.
    # So the container gets an empty folder as /dev/bus/usb, and the recovery creates a file in it to wake the watcher up.
    services.udev.extraRules = lib.mkIf cfg.recoverBluetooth ''
      ACTION=="add", SUBSYSTEM=="bluetooth", ENV{DEVTYPE}=="host", TAG+="systemd", ENV{SYSTEMD_WANTS}+="home-assistant-bluetooth-recover.service"
    '';
    systemd.services.home-assistant-bluetooth-recover = lib.mkIf cfg.recoverBluetooth {
      description = "Reload Home Assistant's Bluetooth integration after the adapter re-appears";
      path = [
        pkgs.curl
        pkgs.jq
      ];
      environment = {
        # HA younger than this finds the new adapter by itself
        MIN_AGE = lib.mkDefault "120";
        # time for HA to refresh its adapter list after the watcher is woken up (two 5 s debounces)
        SETTLE = lib.mkDefault "15";
        # seconds to wait for the Bluetooth entry to load before restarting HA
        READY_TIMEOUT = lib.mkDefault "30";
      };
      serviceConfig.Type = "oneshot";
      script = ''
        # During boot the adapter appears before HA runs: nothing to recover
        if ! systemctl is-active --quiet podman-home-assistant.service; then
          echo "Home Assistant not running, nothing to do"
          exit 0
        fi
        started=$(systemctl show podman-home-assistant.service -P ActiveEnterTimestampMonotonic)
        uptime=$(cut -d. -f1 /proc/uptime)
        age=$(( uptime - started / 1000000 ))
        if [ "$age" -lt "$MIN_AGE" ]; then
          echo "Home Assistant started ''${age}s ago, skipping"
          exit 0
        fi
      ''
      + (
        if cfg.haTokenFile == null then
          ''
            echo "Bluetooth adapter re-appeared, restarting Home Assistant"
            systemctl restart podman-home-assistant.service
          ''
        else
          ''
            token="$(cat ${cfg.haTokenFile})"
            api() {
              curl -fsS -m 30 -H "Authorization: Bearer $token" "$@"
            }
            entries_url="http://127.0.0.1:8123/api/config/config_entries/entry?domain=bluetooth"
            # give BlueZ a moment to register the new adapter
            sleep 5
            # wake up HA's USB watcher, so HA refreshes its adapter list
            touch ${usbWatchDir}/bluetooth-recover
            rm -f ${usbWatchDir}/bluetooth-recover
            sleep "$SETTLE"
            # one entry per adapter; HA finds the adapter again by its MAC address
            entries=$(api "$entries_url")
            ids=$(jq -r '.[] | select(.disabled_by == null) | .entry_id' <<<"$entries")
            if [ -z "$ids" ]; then
              echo "No enabled Bluetooth integration in Home Assistant, nothing to do"
              exit 0
            fi
            for id in $ids; do
              echo "Bluetooth adapter re-appeared, reloading Home Assistant Bluetooth entry $id"
              api -o /dev/null -X POST "http://127.0.0.1:8123/api/config/config_entries/entry/$id/reload"
            done
            for _ in $(seq "$READY_TIMEOUT"); do
              if api "$entries_url" | jq -e '[.[] | select(.disabled_by == null) | .state] | all(. == "loaded")' >/dev/null; then
                echo "Bluetooth is back"
                exit 0
              fi
              sleep 1
            done
            echo "Bluetooth entry didn't load in ''${READY_TIMEOUT}s, restarting Home Assistant"
            systemctl restart podman-home-assistant.service
          ''
      );
    };
  };
}
