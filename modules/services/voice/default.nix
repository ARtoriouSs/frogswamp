{
  config,
  lib,
  pkgs,
  ...
}:
let
  cfg = config.frogswamp.voice;
  stateDir = "/var/lib/voice-agent";
  credentials = "/run/credentials/voice-bridge.service";
  # the bridge's Unix socket; only its own user and root (HA's container) can open it
  socketDir = "/run/voice-bridge";
  sileroPort = 10202;

  claudeSettings = pkgs.writeText "claude-settings.json" (
    builtins.toJSON {
      permissions = {
        allow = [
          "mcp__homeassistant"
          "WebSearch"
          "WebFetch"
        ];
        deny = [
          "Bash"
          "Edit"
          "Write"
          "NotebookEdit"
          # no reading files, so the tokens in its environment and credentials stay out of reach
          "Read"
          "Glob"
          "Grep"
          "Task"
          "Agent"
        ]
        # VW Group Connect adds these to HA's Assist API by itself, past the exposure settings
        ++ map (tool: "mcp__homeassistant__vag_connect__${tool}") [
          "skoda_ask_assistant"
          "skoda_send_destination"
          "skoda_set_location_target_soc"
        ];
      };
      # keep home data off the web, see web_guard.py
      hooks.PreToolUse =
        lib.mapAttrsToList
          (matcher: mode: {
            inherit matcher;
            hooks = [
              {
                type = "command";
                command = "${pkgs.python3}/bin/python3 ${./web_guard.py} ${mode}";
              }
            ];
          })
          {
            WebFetch = "fetch";
            "mcp__homeassistant__.*" = "home";
          };
    }
  );
  # ${HA_TOKEN} is filled in by Claude Code from the environment, so no token is in the store
  mcpConfig = pkgs.writeText "mcp.json" (
    builtins.toJSON {
      mcpServers.homeassistant = {
        type = "http";
        url = "http://127.0.0.1:8123/api/mcp";
        headers.Authorization = "Bearer \${HA_TOKEN}";
      };
    }
  );
  bridgeConfig = pkgs.writeText "voice-bridge.json" (
    builtins.toJSON {
      listen = "unix:${socketDir}/bridge.sock";
      backend = "claude-code";
      # web search plus reading a page takes 10-15 s; HA waits 30 s
      timeout = 25;
      # one Claude session for the whole home, forgotten after 6 hours of silence
      shared_session = true;
      session_ttl = 6 * 3600;
      debug_log = cfg.debugLog;
      system_prompt_file = "${./system-prompt.md}";
      # the home's name and place come from HA's general settings, the repo is public
      ha_config_url = "http://127.0.0.1:8123/api/config";
      # a template helper made in HA's UI, the street address is kept out of the repo
      ha_address_url = "http://127.0.0.1:8123/api/states/sensor.home_address";
      ha_token_file = "${credentials}/ha_token";
      claude-code = {
        claude = lib.getExe' cfg.claudePackage "claude";
        inherit (cfg) model;
        mcp_config = "${mcpConfig}";
        settings = "${claudeSettings}";
        workdir = "${stateDir}/work";
        config_dir = "${stateDir}/claude";
        home = stateDir;
        credentials_dir = credentials;
      };
    }
  );
  integration = lib.fileset.toSource {
    root = ./claude_bridge;
    fileset = lib.fileset.difference ./claude_bridge (
      lib.fileset.fileFilter (file: lib.hasPrefix "test_" file.name) ./claude_bridge
    );
  };
  sileroSource = lib.fileset.toSource {
    root = ./silero;
    fileset = lib.fileset.difference ./silero (
      lib.fileset.fileFilter (file: lib.hasPrefix "test_" file.name) ./silero
    );
  };
  sileroModel = pkgs.fetchurl {
    url = "https://models.silero.ai/models/tts/ru/v5_6_ru.pt";
    hash = "sha256-BMybHQjOOLyqnQLEiNgNPmgG0X9+qnyVmC82o8v1Cy4=";
  };
  sileroPython = pkgs.python3.withPackages (ps: [
    ps.torch
    ps.numpy
    ps.wyoming
    ps.num2words
  ]);
  languages = {
    ru = {
      sttPort = 10300;
      ttsPort = 10200;
    };
    en = {
      sttPort = 10301;
      ttsPort = 10201;
    };
  };
in
{
  options.frogswamp.voice = {
    enable = lib.mkEnableOption "voice assistant: local STT/TTS and a bridge to Claude Code";
    model = lib.mkOption {
      type = lib.types.str;
      default = "haiku";
      description = "Claude model (alias or full name)";
    };
    claudePackage = lib.mkOption {
      type = lib.types.package;
      default = pkgs.claude-code;
      description = "Unfree: the host allows it in nixpkgs.config.allowUnfreePredicate";
    };
    claudeTokenFile = lib.mkOption {
      type = lib.types.str;
      description = "Runtime path of the token from `claude setup-token`";
    };
    haTokenFile = lib.mkOption {
      type = lib.types.str;
      description = "Runtime path of the long-lived token of the non-admin HA user voice-agent";
    };
    silero = {
      enable =
        lib.mkEnableOption "Silero TTS for Russian (Wyoming on ${toString sileroPort}), livelier than Piper"
        // {
          default = true;
        };
      voice = lib.mkOption {
        type = lib.types.enum [
          "aidar"
          "baya"
          "kseniya"
          "xenia"
          "eugene"
        ];
        default = "aidar";
        description = "Default voice; the Russian pipeline picks aidar itself";
      };
    };
    debugLog = lib.mkOption {
      type = lib.types.bool;
      default = false;
      description = "Also log request and answer text";
    };
    stt = {
      library = lib.mkOption {
        type = lib.types.str;
        default = "auto";
        description = "`auto` picks GigaAM for Russian and Parakeet for English";
      };
      model = lib.mkOption {
        type = lib.types.str;
        default = "auto";
      };
    };
    voices = lib.mkOption {
      type = lib.types.attrsOf lib.types.str;
      default = {
        ru = "ru_RU-irina-medium";
        en = "en_US-lessac-medium";
      };
    };
  };

  config = lib.mkIf cfg.enable {
    services.wyoming.faster-whisper.servers = lib.mapAttrs (lang: l: {
      enable = true;
      language = lang;
      sttLibrary = cfg.stt.library;
      inherit (cfg.stt) model;
      uri = "tcp://127.0.0.1:${toString l.sttPort}";
      zeroconf.enable = false;
    }) languages;

    services.wyoming.piper.servers = lib.mapAttrs (lang: l: {
      enable = true;
      voice = cfg.voices.${lang};
      uri = "tcp://127.0.0.1:${toString l.ttsPort}";
      zeroconf.enable = false;
    }) languages;

    # The model is a torch package with Silero's own code inside, so it runs with no network
    # (only HA on localhost talks to it), no writable paths and a memory limit
    # the conversation agent HA uses to reach the bridge
    virtualisation.oci-containers.containers = lib.mkIf config.frogswamp.homeAssistant.enable {
      home-assistant.volumes = [
        "${integration}:/config/custom_components/claude_bridge:ro"
        "${socketDir}:${socketDir}"
      ];
    };

    systemd.services = {
      wyoming-silero = lib.mkIf cfg.silero.enable {
        description = "Silero TTS (Russian) for HA's voice pipeline";
        wantedBy = [ "multi-user.target" ];
        environment = {
          HOME = "/var/cache/wyoming-silero";
          TORCH_HOME = "/var/cache/wyoming-silero";
        };
        serviceConfig = {
          ExecStart = lib.escapeShellArgs [
            "${sileroPython}/bin/python3"
            "${sileroSource}/silero_wyoming.py"
            "--uri"
            "tcp://127.0.0.1:${toString sileroPort}"
            "--model"
            "${sileroModel}"
            "--voice"
            cfg.silero.voice
            "--threads"
            "4"
          ];
          Restart = "on-failure";
          DynamicUser = true;
          CacheDirectory = "wyoming-silero";
          MemoryMax = "3G";
          Nice = -5;
          IPAddressAllow = "localhost";
          IPAddressDeny = "any";
          RestrictAddressFamilies = [
            "AF_INET"
            "AF_INET6"
            "AF_UNIX"
          ];
          ProtectSystem = "strict";
          ProtectHome = true;
          PrivateTmp = true;
          PrivateDevices = true;
          NoNewPrivileges = true;
          ProtectProc = "invisible";
          ProtectKernelTunables = true;
          ProtectKernelModules = true;
          ProtectKernelLogs = true;
          ProtectControlGroups = true;
          ProtectClock = true;
          ProtectHostname = true;
          RestrictNamespaces = true;
          RestrictRealtime = true;
          RestrictSUIDSGID = true;
          LockPersonality = true;
          SystemCallArchitectures = "native";
          SystemCallFilter = [
            "@system-service"
            "~@privileged"
          ];
          CapabilityBoundingSet = "";
          UMask = "0077";
          InaccessiblePaths = [
            "-/var/lib"
            "-/run/secrets"
            "-/run/credentials"
            "-/etc/nixos"
          ];
        };
      };

      # with DynamicUser the socket folder is /run/private/voice-bridge behind a symlink, made when the
      # bridge starts; HA's container must mount it after that
      podman-home-assistant = lib.mkIf config.frogswamp.homeAssistant.enable {
        after = [ "voice-bridge.service" ];
        wants = [ "voice-bridge.service" ];
      };

      voice-bridge = {
        description = "Voice bridge: HA voice requests to Claude Code";
        wantedBy = [ "multi-user.target" ];
        after = [ "network-online.target" ];
        wants = [ "network-online.target" ];
        preStart = ''
          mkdir -p ${stateDir}/work/.claude ${stateDir}/claude
          ln -sfn ${./skills} ${stateDir}/work/.claude/skills
        '';
        serviceConfig = {
          ExecStart = "${pkgs.python3}/bin/python3 ${./bridge.py} --config ${bridgeConfig}";
          Restart = "on-failure";
          DynamicUser = true;
          StateDirectory = "voice-agent";
          RuntimeDirectory = "voice-bridge";
          RuntimeDirectoryMode = "0700";
          # HA's container keeps the folder mounted, it must stay the same folder across restarts
          RuntimeDirectoryPreserve = true;
          LoadCredential = [
            "claude_token:${cfg.claudeTokenFile}"
            "ha_token:${cfg.haTokenFile}"
          ];
          ProtectSystem = "strict";
          ProtectHome = true;
          PrivateTmp = true;
          NoNewPrivileges = true;
          ProtectProc = "invisible";
          ProtectKernelTunables = true;
          ProtectKernelModules = true;
          ProtectKernelLogs = true;
          ProtectControlGroups = true;
          ProtectClock = true;
          ProtectHostname = true;
          PrivateDevices = true;
          RestrictNamespaces = true;
          RestrictRealtime = true;
          LockPersonality = true;
          SystemCallArchitectures = "native";
          CapabilityBoundingSet = "";
          InaccessiblePaths = [
            "-/var/lib/home-assistant"
            "-/run/secrets"
            "-/etc/nixos"
          ];
          RestrictAddressFamilies = [
            "AF_INET"
            "AF_INET6"
            "AF_UNIX"
          ];
        };
      };
    };
  };
}
