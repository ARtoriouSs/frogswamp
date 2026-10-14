{
  imports = [
    ./options.nix
    ./base
    ./tailscale.nix
    ./services/mqtt.nix
    ./services/home-assistant
    ./services/jellyfin.nix
    ./services/samba.nix
  ];
}
