{ config, lib, ... }:
let
  cfg = config.frogswamp.jellyfin;
in
{
  options.frogswamp.jellyfin = {
    enable = lib.mkEnableOption "Jellyfin media server";
    libraryDirs = lib.mkOption {
      type = lib.types.listOf lib.types.str;
      default = [
        "movies"
        "shows"
        "downloads"
        "downloads/radarr"
        "downloads/sonarr"
      ];
      description = "Folders under /srv/media created on every boot, so Jellyfin libraries resolve after a reinstall";
    };
  };

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
    systemd.tmpfiles.settings."10-media" =
      lib.genAttrs ([ "/srv/media" ] ++ map (d: "/srv/media/${d}") cfg.libraryDirs)
        (_: {
          d = {
            mode = "2775";
            user = "root";
            group = "media";
          };
        });
  };
}
