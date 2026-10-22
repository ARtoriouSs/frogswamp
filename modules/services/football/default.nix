{
  config,
  lib,
  pkgs,
  ...
}:
let
  cfg = config.frogswamp.football;
  engine = config.frogswamp.acestream.port;
  credentials = "/run/credentials/football.service";
in
{
  options.frogswamp.football = {
    enable = lib.mkEnableOption "football page: play Ace Stream matches on the TV (Chromecast or Kodi)";
    port = lib.mkOption {
      type = lib.types.port;
      default = 8093;
    };
    kodi = {
      host = lib.mkOption {
        type = lib.types.str;
        description = "The TV's address (fixed by a DHCP reservation in the router)";
      };
      port = lib.mkOption {
        type = lib.types.port;
        default = 8080;
      };
      username = lib.mkOption {
        type = lib.types.str;
        default = "kodi";
      };
      passwordFile = lib.mkOption {
        type = lib.types.str;
        description = "Runtime path of Kodi's web control password";
      };
    };
    haTokenFile = lib.mkOption {
      type = lib.types.str;
      description = "Runtime path of a Home Assistant token, used to turn the TV on and to cast to it";
    };
    tvRemoteEntity = lib.mkOption {
      type = lib.types.nullOr lib.types.str;
      default = null;
      example = "remote.living_room_philips_tv";
      description = "Android TV Remote entity that turns the TV on before Kodi plays; null skips it";
    };
    tvCastEntity = lib.mkOption {
      type = lib.types.nullOr lib.types.str;
      default = null;
      example = "media_player.philips_tv";
      description = "Google Cast entity of the TV; \"Chromecast to TV\" casts the HLS stream to it";
    };
    settings = lib.mkOption {
      type = lib.types.attrs;
      readOnly = true;
      description = "The app's config (written to JSON)";
    };
  };

  config = lib.mkIf cfg.enable {
    frogswamp.football.settings = {
      listen = "0.0.0.0:${toString cfg.port}";
      engine_url = "http://127.0.0.1:${toString engine}";
      # Kodi runs on the TV, so it needs the server's LAN address, not localhost
      public_engine_url = "http://${config.frogswamp.lan.publicAddress}:${toString engine}";
      kodi = {
        url = "http://${cfg.kodi.host}:${toString cfg.kodi.port}";
        inherit (cfg.kodi) username;
        # handed over by systemd (LoadCredential), so the service doesn't need root
        password_file = "${credentials}/kodi_password";
      };
      ha = {
        url = "http://127.0.0.1:8123";
        token_file = "${credentials}/ha_token";
        remote_entity = cfg.tvRemoteEntity;
        cast_entity = cfg.tvCastEntity;
      };
      state_file = "/var/lib/football/recent.json";
    };

    systemd.services.football = {
      description = "Football page: play Ace Stream matches on the TV";
      wantedBy = [ "multi-user.target" ];
      after = [ "network-online.target" ];
      wants = [ "network-online.target" ];
      serviceConfig = {
        ExecStart = "${pkgs.python3}/bin/python3 ${./app.py} --config ${pkgs.writeText "football.json" (builtins.toJSON cfg.settings)}";
        # with DynamicUser systemd moves an existing /var/lib/football to /var/lib/private/football
        StateDirectory = "football";
        Restart = "on-failure";
        # a throw-away user instead of root; systemd reads the secrets for it
        DynamicUser = true;
        LoadCredential = [
          "kodi_password:${cfg.kodi.passwordFile}"
          "ha_token:${cfg.haTokenFile}"
        ];
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
