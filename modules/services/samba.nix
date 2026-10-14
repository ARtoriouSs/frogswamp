{ config, lib, ... }:
let
  cfg = config.frogswamp.samba;
  admin = config.frogswamp.adminUser;
  samba = config.services.samba.package;
in
{
  options.frogswamp.samba = {
    enable = lib.mkEnableOption "Samba share for /srv/media";
    passwordFile = lib.mkOption {
      type = lib.types.str;
      description = "Runtime path of the admin user's Samba password";
    };
  };

  config = lib.mkIf cfg.enable {
    services = {
      samba = {
        enable = true;
        openFirewall = true;
        settings = {
          global = {
            "server min protocol" = "SMB3";
            "map to guest" = "never";
            "server string" = "frogswamp";
          };
          media = {
            path = "/srv/media";
            browseable = "yes";
            "read only" = "no";
            "valid users" = admin;
            "force group" = "media";
            "create mask" = "0664";
            "directory mask" = "2775";
          };
        };
      };
      samba-wsdd = {
        enable = true;
        openFirewall = true;
      };
      avahi.extraServiceFiles.smb = ''
        <?xml version="1.0" standalone='no'?>
        <!DOCTYPE service-group SYSTEM "avahi-service.dtd">
        <service-group>
          <name replace-wildcards="yes">%h</name>
          <service><type>_smb._tcp</type><port>445</port></service>
        </service-group>
      '';
    };

    # Samba keeps its own password DB, sync it from the secret
    systemd.services.samba-set-password = {
      description = "Set Samba password for ${admin} from secret";
      after = [ "samba-smbd.service" ];
      wants = [ "samba-smbd.service" ];
      wantedBy = [ "multi-user.target" ];
      serviceConfig = {
        Type = "oneshot";
        RemainAfterExit = true;
      };
      script = ''
        pw="$(cat ${cfg.passwordFile})"
        printf '%s\n%s\n' "$pw" "$pw" | ${samba}/bin/smbpasswd -s -a ${admin}
      '';
    };
  };
}
