# modules

Each module does one thing and is switched on in `hosts/frogswamp/default.nix` (`frogswamp.<name>.enable`). Modules take secret **file paths** as options. The host points them at sops secrets, and tests use plain files.

| Module | What it does | Port |
|---|---|---|
| `options.nix` | Shared host settings (`frogswamp.adminUser`, `lan`, `disk`, …) | |
| `base/` | Nix settings, boot, static IP, SSH (keys only), users, firewall, mDNS, Bluetooth, Intel GPU | 22 |
| `tailscale.nix` | Remote access; advertises the LAN to your tailnet | |
| `backup.nix` | Nightly restic to the USB disk + B2; `frogswamp-restore`. Mounts the disk labelled `frogbackup` whenever it is plugged in; publishes the backup state to HA for the dashboard's Backups tile | |
| `services/home-assistant/` | HA container + repo-owned YAML ([README](services/home-assistant/README.md)) | 8123 |
| `services/mqtt.nix` | Mosquitto (localhost) + Zigbee2MQTT | 8080 |
| `services/jellyfin.nix` | Jellyfin, QuickSync, `/srv/media` folders | 8096 |
| `services/samba.nix` | `media` network share | 445 |
| `services/homepage.nix` | Start page with links and widgets | 8082 |
| `services/qbittorrent.nix` | Torrent client: sequential downloads, stops seeding at completion | 8090, 51413 |
| `services/arr.nix` | Radarr, Sonarr, Prowlarr, Bazarr | 7878, 8989, 9696, 6767 |
| `services/media-provision/` | Sets up the media apps once (create-if-missing); the UIs own the settings after that | |
| `services/media-janitor/` | Removes finished torrents, deletes watched titles (every 5 min); stale-downloads sensor in HA | |

How the media parts work together: [services/media/README.md](services/media/README.md).

Tests for each module live in `/tests` and run with `just check`.
