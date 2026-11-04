{
  config,
  lib,
  pkgs,
  ...
}:
let
  cfg = config.frogswamp.workCalendar;
  python = pkgs.python3.withPackages (ps: [
    ps.icalendar
    ps.recurring-ical-events
    ps.tzdata
  ]);
in
{
  options.frogswamp.workCalendar = {
    enable = lib.mkEnableOption "the accepted work meetings as a local calendar for Home Assistant";
    port = lib.mkOption {
      type = lib.types.port;
      default = 8129;
    };
    # Meetings are cut to these hours, and anything outside them is dropped
    dayStart = lib.mkOption {
      type = lib.types.str;
      default = "09:00";
    };
    dayEnd = lib.mkOption {
      type = lib.types.str;
      default = "18:00";
    };
    urlFile = lib.mkOption {
      type = lib.types.str;
      description = "The calendar's secret iCal link";
    };
    emailFile = lib.mkOption {
      type = lib.types.str;
      description = "The work email, to find the replies that are mine";
    };
  };

  config = lib.mkIf cfg.enable {
    systemd.services.work-calendar = {
      description = "Accepted work meetings for Home Assistant";
      wantedBy = [ "multi-user.target" ];
      wants = [ "network-online.target" ];
      after = [ "network-online.target" ];
      serviceConfig = {
        ExecStart = lib.escapeShellArgs [
          "${python}/bin/python3"
          "${./work_calendar.py}"
          "--credentials"
          "%d"
          "--state-dir"
          "%S/work-calendar"
          "--port"
          (toString cfg.port)
          "--timezone"
          config.time.timeZone
          "--day-start"
          cfg.dayStart
          "--day-end"
          cfg.dayEnd
        ];
        LoadCredential = [
          "url:${cfg.urlFile}"
          "email:${cfg.emailFile}"
        ];
        Restart = "always";
        RestartSec = 30;
        DynamicUser = true;
        StateDirectory = "work-calendar";
        CapabilityBoundingSet = "";
        NoNewPrivileges = true;
        PrivateDevices = true;
        ProtectHome = true;
        ProtectKernelTunables = true;
        ProtectControlGroups = true;
        RestrictAddressFamilies = [
          "AF_INET"
          "AF_INET6"
        ];
      };
    };
  };
}
