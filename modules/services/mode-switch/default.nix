{
  config,
  lib,
  pkgs,
  ...
}:
let
  cfg = config.frogswamp.modeSwitch;
in
{
  options.frogswamp.modeSwitch = {
    enable = lib.mkEnableOption "mode switch: Homepage tiles that flip Home Assistant modes";
    port = lib.mkOption {
      type = lib.types.port;
      default = 8094;
    };
    haTokenFile = lib.mkOption {
      type = lib.types.str;
      description = "Runtime path of a Home Assistant token, used to toggle the modes";
    };
    modes = lib.mkOption {
      type = lib.types.attrsOf lib.types.str;
      default = { };
      example = {
        vacation = "input_boolean.vacation";
      };
      description = "Name in the URL (/toggle/<name>) → the input_boolean it flips. Only these can be switched";
    };
    settings = lib.mkOption {
      type = lib.types.attrs;
      readOnly = true;
      description = "The app's config (written to JSON)";
    };
  };

  config = lib.mkIf cfg.enable {
    frogswamp.modeSwitch.settings = {
      listen = "0.0.0.0:${toString cfg.port}";
      ha = {
        url = "http://127.0.0.1:8123";
        # handed over by systemd (LoadCredential), so the service doesn't need root
        token_file = "/run/credentials/mode-switch.service/ha_token";
      };
      # Homepage reloads after the redirect and shows the new state
      back_url = "http://${config.frogswamp.lan.publicAddress}:8082/";
      # names Homepage is opened by; a toggle from any other site is refused
      trusted_hosts = [
        config.frogswamp.lan.publicAddress
        config.networking.hostName
        "${config.networking.hostName}.local"
        "localhost"
      ];
      inherit (cfg) modes;
    };

    systemd.services.mode-switch = {
      description = "Mode switch: flip Home Assistant modes from Homepage";
      wantedBy = [ "multi-user.target" ];
      after = [ "network-online.target" ];
      wants = [ "network-online.target" ];
      serviceConfig = {
        ExecStart = "${pkgs.python3}/bin/python3 ${./app.py} --config ${pkgs.writeText "mode-switch.json" (builtins.toJSON cfg.settings)}";
        Restart = "on-failure";
        # a throw-away user instead of root; systemd reads the token for it
        DynamicUser = true;
        LoadCredential = [ "ha_token:${cfg.haTokenFile}" ];
        ProtectHome = true;
        RestrictAddressFamilies = [
          "AF_INET"
          "AF_INET6"
          "AF_UNIX" # nscd
        ];
      };
    };
    networking.firewall.allowedTCPPorts = [ cfg.port ];
  };
}
