{ config, lib, ... }:
let
  cfg = config.frogswamp.jellyfin;
in
{
  options.frogswamp.jellyfin.enable = lib.mkEnableOption "Jellyfin media server";

  config = lib.mkIf cfg.enable {
    services.jellyfin = {
      enable = true;
      openFirewall = true;
    };
    users = {
      groups.media = { };
      users.jellyfin.extraGroups = [
        "media"
        "render"
        "video"
      ];
      users.${config.frogswamp.adminUser}.extraGroups = [ "media" ];
    };
    systemd.tmpfiles.settings."10-media"."/srv/media".d = {
      mode = "2775";
      user = "root";
      group = "media";
    };
  };
}
