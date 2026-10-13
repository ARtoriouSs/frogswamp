{ config, lib, ... }:
let
  inherit (lib) mkOption types;
in
{
  options.frogswamp = {
    adminUser = mkOption {
      type = types.str;
      description = "Name of the admin login user.";
    };
    adminSshKeys = mkOption {
      type = types.listOf types.str;
      default = [ ];
      description = "SSH public keys allowed for the admin user and root.";
    };
    adminPasswordHashFile = mkOption {
      type = types.nullOr types.str;
      default = null;
      description = "Runtime path of a file containing the admin password hash (for sudo).";
    };
    timeZone = mkOption {
      type = types.str;
      default = "Europe/Warsaw";
    };
    disk = mkOption {
      type = types.str;
      default = "/dev/nvme0n1";
      description = "System disk that disko partitions on install.";
    };
    lan = {
      address = mkOption {
        type = types.nullOr types.str;
        default = null;
        description = "Static IPv4 address. null means DHCP.";
      };
      prefixLength = mkOption {
        type = types.int;
        default = 24;
      };
      gateway = mkOption {
        type = types.str;
        default = "";
      };
      subnet = mkOption {
        type = types.str;
        default = "";
        description = "LAN subnet in CIDR notation, advertised over Tailscale.";
      };
      matchConfig = mkOption {
        type = types.attrsOf types.str;
        default = {
          Type = "ether";
        };
        description = "systemd-networkd [Match] for the LAN NIC. Default matches any wired NIC, so hardware swaps keep working.";
      };
      publicAddress = mkOption {
        type = types.str;
        readOnly = true;
        default =
          if config.frogswamp.lan.address != null then config.frogswamp.lan.address else "localhost";
        description = "Address used in links to services.";
      };
    };
  };
}
