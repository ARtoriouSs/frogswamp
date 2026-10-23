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
    cubematrix = {
      enable = lib.mkEnableOption "the Yeelight Cube Matrix integration (mounted read-only from integrations/cubematrix)";
      musicPort = lib.mkOption {
        type = lib.types.port;
        default = 55440;
        description = "TCP port the lamps connect back to in music mode (shared by all cubes)";
      };
      lampAddresses = lib.mkOption {
        type = lib.types.listOf lib.types.str;
        default = [ ];
        description = "Lamp IPs allowed to reach the music mode port; it stays closed for the rest of the LAN";
      };
    };
    recoverBluetooth = lib.mkOption {
      type = lib.types.bool;
      default = true;
      description = "Reload Home Assistant's Bluetooth integration when the adapter re-appears (e.g. after a USB reset renames hci0 to hci1)";
    };
    haTokenFile = lib.mkOption {
      type = lib.types.nullOr lib.types.str;
      default = null;
      description = "Runtime path of an HA long-lived token. With it the Bluetooth recovery reloads only the Bluetooth integration; without it, it restarts Home Assistant";
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
        ++ lib.optionals cfg.cubematrix.enable [
          "${../../../integrations/cubematrix/custom_components/cubematrix}:/config/custom_components/cubematrix:ro"
          "${./config/cubematrix}:/config/cubematrix:ro"
        ];
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

    networking.firewall = {
      allowedTCPPorts = [ 8123 ];
      # Music mode: the lamp connects to HA, so accept the port from the lamp IPs only
      extraCommands = lib.mkIf cfg.cubematrix.enable (
        lib.concatMapStringsSep "\n" (
          ip:
          "iptables -A nixos-fw -p tcp -s ${ip} --dport ${toString cfg.cubematrix.musicPort} -j nixos-fw-accept"
        ) cfg.cubematrix.lampAddresses
      );
      extraStopCommands = lib.mkIf cfg.cubematrix.enable (
        lib.concatMapStringsSep "\n" (
          ip:
          "iptables -D nixos-fw -p tcp -s ${ip} --dport ${toString cfg.cubematrix.musicPort} -j nixos-fw-accept || true"
        ) cfg.cubematrix.lampAddresses
      );
    };

    # The Realtek BT chip sometimes hangs; the kernel resets it and it comes back as a new hciN.
    # HA's Bluetooth integration stays bound to the old adapter, so reload it when an adapter (not a BLE link) is added.
    services.udev.extraRules = lib.mkIf cfg.recoverBluetooth ''
      ACTION=="add", SUBSYSTEM=="bluetooth", ENV{DEVTYPE}=="host", TAG+="systemd", ENV{SYSTEMD_WANTS}+="home-assistant-bluetooth-recover.service"
    '';
    systemd.services.home-assistant-bluetooth-recover = lib.mkIf cfg.recoverBluetooth {
      description = "Reload Home Assistant's Bluetooth integration after the adapter re-appears";
      path = [
        pkgs.curl
        pkgs.jq
      ];
      # HA younger than this finds the new adapter by itself
      environment.MIN_AGE = lib.mkDefault "120";
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
            # give BlueZ a moment to register the new adapter
            sleep 5
            # one entry per adapter; HA finds the adapter again by its MAC address
            entries=$(api "http://127.0.0.1:8123/api/config/config_entries/entry?domain=bluetooth")
            ids=$(jq -r '.[] | select(.disabled_by == null) | .entry_id' <<<"$entries")
            if [ -z "$ids" ]; then
              echo "No enabled Bluetooth integration in Home Assistant, nothing to do"
              exit 0
            fi
            for id in $ids; do
              echo "Bluetooth adapter re-appeared, reloading Home Assistant Bluetooth entry $id"
              api -o /dev/null -X POST "http://127.0.0.1:8123/api/config/config_entries/entry/$id/reload"
            done
          ''
      );
    };
  };
}
