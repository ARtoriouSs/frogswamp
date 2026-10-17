{
  imports = [
    ./options.nix
    ./base
    ./tailscale.nix
    ./backup.nix
    ./services/mqtt.nix
    ./services/home-assistant
    ./services/jellyfin.nix
    ./services/qbittorrent.nix
    ./services/arr.nix
    ./services/media-provision
    ./services/media-janitor
    ./services/samba.nix
    ./services/homepage.nix
  ];
}
