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
    gc = {
      automatic = true;
      dates = "weekly";
      options = "--delete-older-than 14d";
    };
  };

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
  systemd.network.enable = true;
  systemd.network.networks."10-lan" = {
    inherit (cfg.lan) matchConfig;
  }
  // (if cfg.lan.address == null then { networkConfig.DHCP = "ipv4"; } else staticLan);

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
