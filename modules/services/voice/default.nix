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

    # the conversation agent HA uses to reach the bridge
    virtualisation.oci-containers.containers = lib.mkIf config.frogswamp.homeAssistant.enable {
      home-assistant.volumes = [
        "${integration}:/config/custom_components/claude_bridge:ro"
        "${socketDir}:${socketDir}"
      ];
    };
    # with DynamicUser the socket folder is /run/private/voice-bridge behind a symlink, made when the
    # bridge starts; HA's container must mount it after that
    systemd.services.podman-home-assistant = lib.mkIf config.frogswamp.homeAssistant.enable {
      after = [ "voice-bridge.service" ];
      wants = [ "voice-bridge.service" ];
    };

    systemd.services.voice-bridge = {
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
}
