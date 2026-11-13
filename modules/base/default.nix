{
  config,
  pkgs,
  ...
}:
let
  cfg = config.frogswamp;
  staticLan = {
    address = [ "${cfg.lan.address}/${toString cfg.lan.prefixLength}" ];
    gateway = [ cfg.lan.gateway ];
    dns = [ cfg.lan.gateway ];
  };
in
{
  imports = [ ./hardware.nix ];

  nix = {
    settings = {
      experimental-features = [
        "nix-command"
        "flakes"
      ];
      trusted-users = [
        "root"
        "@wheel"
      ];
      auto-optimise-store = true;
    };
    # Old system generations are removed by pruneGenerations below, so gc only cleans unused paths
    gc = {
      automatic = true;
      dates = "weekly";
      options = "";
    };
  };

  # Like --delete-older-than 14d, but the 3 newest generations always stay for rollback
  systemd.services.nix-gc.preStart = pkgs.lib.getExe (
    pkgs.writeShellApplication {
      name = "prune-system-generations";
      runtimeInputs = [
        config.nix.package
        pkgs.coreutils
        pkgs.gawk
      ];
      text = ''
        profile=/nix/var/nix/profiles/system
        cutoff=$(date -d '14 days ago' +%s)
        old=()
        while read -r gen day time; do
          if [ "$(date -d "$day $time" +%s)" -lt "$cutoff" ]; then
            old+=("$gen")
          fi
        done < <(nix-env -p "$profile" --list-generations | head -n -3 | awk '!/\(current\)/ { print $1, $2, $3 }')
        if [ "''${#old[@]}" -gt 0 ]; then
          echo "Deleting system generations: ''${old[*]}"
          nix-env -p "$profile" --delete-generations "''${old[@]}"
        fi
      '';
    }
  );

  boot.loader.systemd-boot = {
    enable = true;
    configurationLimit = 10;
  };
  boot.loader.efi.canTouchEfiVariables = true;
  zramSwap.enable = true;

  time.timeZone = cfg.timeZone;
  i18n.defaultLocale = "en_US.UTF-8";

  networking = {
    useNetworkd = true;
    useDHCP = false;
    firewall.enable = true;
  };
  # Our 10-lan unit configures the wired NIC. Facter would add DHCP units for every detected NIC,
  # including the unused Wi-Fi, which never gets online and makes networkd-wait-online fail
  hardware.facter.detected.dhcp.enable = false;
  systemd.network = {
    enable = true;
    networks."10-lan" = {
      inherit (cfg.lan) matchConfig;
    }
    // (if cfg.lan.address == null then { networkConfig.DHCP = "ipv4"; } else staticLan);
  };

  services.avahi = {
    enable = true;
    nssmdns4 = true;
    openFirewall = true;
    publish = {
      enable = true;
      addresses = true;
    };
  };

  services.openssh = {
    enable = true;
    openFirewall = true;
    settings = {
      PasswordAuthentication = false;
      KbdInteractiveAuthentication = false;
      PermitRootLogin = "prohibit-password";
    };
  };

  users = {
    mutableUsers = false;
    users.root.openssh.authorizedKeys.keys = cfg.adminSshKeys;
    users.${cfg.adminUser} = {
      isNormalUser = true;
      extraGroups = [ "wheel" ];
      openssh.authorizedKeys.keys = cfg.adminSshKeys;
      hashedPasswordFile = cfg.adminPasswordHashFile;
    };
  };

  environment.systemPackages = with pkgs; [
    git
    htop
    sqlite
    vim
  ];
}
