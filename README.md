# 🐸 frogswamp

Home server as code. A GMKtec NucBox G3 (N100, 16 GB, 512 GB NVMe) runs NixOS 26.05 with Home Assistant, Zigbee2MQTT, Jellyfin, a media download stack (qBittorrent, Radarr, Sonarr, Prowlarr, Bazarr), a Samba media share, an Ace Stream engine with a "Football" page that plays matches on the TV, and a Homepage dashboard. It is reachable from anywhere via Tailscale.

Everything about the server is declared in this repo. Its state (HA integrations, Zigbee pairings, Jellyfin users and watched status, Arr settings and indexers) is backed up every night to a USB SSD (Kingston A400) and to Backblaze B2. Media files are **not** backed up.

| Service | URL |
|---|---|
| Dashboard | http://192.168.101.10:8082 |
| Home Assistant | http://192.168.101.10:8123 |
| Zigbee2MQTT | http://192.168.101.10:8080 |
| Jellyfin | http://192.168.101.10:8096 |
| qBittorrent | http://192.168.101.10:8090 |
| Radarr / Sonarr | http://192.168.101.10:7878 / :8989 |
| Prowlarr / Bazarr | http://192.168.101.10:9696 / :6767 |
| Football (Ace Stream → TV) | http://192.168.101.10:8093 |
| Mode switch (used by the Homepage *Modes* tiles) | http://192.168.101.10:8094 |
| Media share | `smb://frogswamp.local/media` |

The same URLs work away from home when the Tailscale app is on.

More detail: [hosts](hosts/README.md) · [modules](modules/README.md) · [Home Assistant](modules/services/home-assistant/README.md) · [media](modules/services/media/README.md) · [football](modules/services/football/README.md) · [secrets](secrets/README.md) · [scripts](scripts/README.md)

**Reinstalling the server?** Follow the checklist in [RESET.md](RESET.md).

## 1. Initial setup (first time, no backup yet)

Do these steps in order. Steps 1–3 are done once per router, account or PC.

### 1.1 Router DHCP pool

The server gives itself the fixed address `192.168.101.10`. The router must never hand that address to another device:

1. Open `http://192.168.101.1` → LAN / DHCP server settings.
2. Set the DHCP pool (start–end address) to **`192.168.101.100` – `192.168.101.254`**. Addresses `.2–.99` are then free for static hosts. Leave the static-IP list empty.
3. Devices currently on `.2–.99` move into the new range at their next lease renewal. Nothing breaks meanwhile, and rebooting a device moves it at once.
4. Check that `.10` is free: `ping -c 3 192.168.101.10` must show `100% packet loss`, and nothing is listed on `.10` in the router's client list.

If you replace the router or reset it to factory settings, do this again. A pool that covers the whole subnet would let another device take `.10`, and the server would lose its network.

Port forwarding (51413 for torrents, 8621 for Ace Stream) only helps with a public IPv4 address. The current provider uses CGNAT (the router's WAN address isn't public and changes), so it's not set up; downloads and streams still work, with fewer peers.

Fixed addresses below `.100`: the server (`.10`) and the TV (`.20`, static IP set on the TV, see the [football README](modules/services/football/README.md#one-time-setup)).

### 1.2 Accounts

- **Tailscale:** paste [`docs/tailscale-acl.hujson`](docs/tailscale-acl.hujson) into *Access controls*. Then create an OAuth client (scope *Auth Keys: write*, tag `tag:homelab`). Its secret goes into `tailscale/oauth_secret` (step 1.4).
- **Backblaze B2:** create a private bucket (now: `frogswamp-backup`, region `eu-central-003`) and an application key limited to it. The key goes into `restic/b2_env` (step 1.4).

### 1.3 Management PC (Linux Mint / Ubuntu)

```bash
git clone git@github.com:ARtoriouSs/frogswamp.git && cd frogswamp
bash scripts/bootstrap-local.sh      # Nix (asks for sudo), direnv, age + SSH keys, Tailscale (off)
# open a new terminal, then:
cd frogswamp && direnv allow
just                                  # lists all commands
```

The shell helpers (`ts-up` / `ts-down` for reaching the server through Tailscale away from home, Nix and direnv loading) live in the [dotfiles repo](https://github.com/ARtoriouSs/dotfiles/tree/master/frogswamp), which should be cloned to `~/dotfiles`. Its `bin/bootstrap_frogswamp.sh` runs this script from GitHub.

**Store `~/.config/sops/age/keys.txt` in your password manager.** It decrypts every secret in this repo. Without it (and without the server) the secrets can't be recovered. On a second PC, copy it from the password manager instead of creating a new one.

Optional, makes `just check` much faster: let Nix's test VMs use hardware virtualization (KVM). Without it the VM tests still pass, only several times slower:

```bash
echo 'KERNEL=="kvm", GROUP="kvm", MODE="0666", OPTIONS+="static_node=kvm"' | sudo tee /etc/udev/rules.d/99-kvm.rules && sudo udevadm control --reload-rules && sudo udevadm trigger --name-match=kvm && ls -l /dev/kvm
```

It should print `crw-rw-rw-`. Any local user can then start VMs, which is fine on a single-user PC.

### 1.4 Secrets (only if `secrets/` doesn't exist yet)

```bash
just gen-host-key     # the server's SSH host key = its sops identity, plus .sops.yaml
just secrets          # opens the encrypted file in $EDITOR
```

Fill in these values (see also [secrets/README.md](secrets/README.md)):

| Secret | Value |
|---|---|
| `admin/password_hash` | `mkpasswd -m yescrypt` output for the `pupsich` user (sudo, console login) |
| `tailscale/oauth_secret` | OAuth client secret from step 1.2 |
| `mosquitto/ha_password`, `mosquitto/z2m_password` | random, e.g. `openssl rand -base64 24` |
| `home_assistant/secrets_yaml` | content of HA's `secrets.yaml` (keys used in `config/`) |
| `home_assistant/api_token` | leave empty for now, filled in step 1.7 |
| `samba/password` | password for the media share |
| `restic/password` | random, long. Store a copy in your password manager too |
| `restic/b2_env` | `AWS_ACCESS_KEY_ID=<keyID>` and `AWS_SECRET_ACCESS_KEY=<applicationKey>`, one per line |
| `arr/ui_password` | random. Login for qBittorrent and (outside home) Radarr/Sonarr/Prowlarr |
| `arr/radarr_api_key`, `arr/sonarr_api_key`, `arr/prowlarr_api_key`, `arr/bazarr_api_key` | 32 hex characters each, `openssl rand -hex 16`. Each must contain at least one letter (Bazarr breaks on an all-digit key) |
| `qbittorrent/webui_password_hash` | hash of `arr/ui_password`, see below |
| `jellyfin/api_key` | leave empty for now, filled in step 1.7 |
| `kodi/password` | random, e.g. `openssl rand -hex 5`. Typed into Kodi on the TV (step 1.7) |

The qBittorrent hash, computed from the saved password without showing it:

```bash
hash=$(just secret arr/ui_password | python3 -c 'import sys,os,hashlib,base64; pw=sys.stdin.read().strip().encode(); s=os.urandom(16); print("@ByteArray(%s:%s)" % (base64.b64encode(s).decode(), base64.b64encode(hashlib.pbkdf2_hmac("sha512", pw, s, 100000)).decode()))')
sops set secrets/frogswamp.yaml '["qbittorrent"]["webui_password_hash"]' "\"$hash\""; unset hash
```

### 1.5 Install the server

1. `just iso`, then write `result/iso/*.iso` to a USB stick (Mint: right-click the file → *Make bootable USB stick*). The stick has your SSH key built in, so make a new one when `keys/admin.pub` changes.
2. NUC BIOS (monitor + keyboard, once): *Restore on AC power loss* = on, Secure Boot = off.
3. Plug in Ethernet and the stick. Boot the stick via the one-time boot menu (press `F7` right after power-on).
4. On the PC: `just install frogswamp-installer.local`. **This erases the NUC's disk.** It shows the disk and asks you to type `wipe`.
5. When it says *Installed*, **pull out the stick**. The NUC reboots into the new system on `192.168.101.10` (2–3 minutes). Check with `just ssh uptime`.
6. Commit `hosts/frogswamp/facter.json` (the hardware report, created by the install) after a quick look at it.

### 1.6 Backup disk

1. Plug the A400 (USB-SATA case) into the NUC.
2. Find it: `ssh root@192.168.101.10 lsblk` (the ~224 GB disk, usually `/dev/sda`).
3. `just init-backup-disk /dev/sdX`. **This erases that disk.** Type `wipe`.
4. `just backup-now`. It ends with a list of snapshots.

The server mounts the disk at `/mnt/backup` by its label `frogbackup`, at boot and also whenever it is plugged in later. Other USB sticks or disks are never mounted, and `init-backup-disk` refuses the system disk. If the disk is missing, nothing breaks: the server boots normally, the nightly local backup fails (and writes nothing), and the B2 backup still runs.

The dashboard's **Backups** tile shows whether the A400 is mounted (and how full it is) and the time of the newest backup on the A400 and on B2. A ⚠ means that backup is older than 36 h. It's updated every hour, after each backup, and when the disk is plugged in.

### 1.7 Apps, in the browser

1. **Home Assistant** (`:8123`): onboarding (create your user), then add the **MQTT** integration: broker `127.0.0.1`, port `1883`, user `ha`, password from `just secret mosquitto/ha_password`.
   Then *Profile → Security → Long-lived access tokens → Create*, and save it: `just secrets` → `home_assistant/api_token`.
2. **Zigbee2MQTT** (`:8080`): *Permit join*, then pair the devices. Give them readable names (see the [HA rules](modules/services/home-assistant/README.md#rules-for-automations-kept-in-git)).
3. **Jellyfin** (`:8096`): setup wizard: create your user. **Skip adding libraries.** The server creates *Movies* and *Shows* by itself once it has an API key. *Dashboard → Playback → Transcoding* → **Intel QuickSync**.
   Then *Dashboard → API Keys → +* (name `frogswamp`), and save it: `just secrets` → `jellyfin/api_key`.
   *Dashboard → Plugins → Catalog* → install **Kodi Sync Queue**, then restart Jellyfin.
4. `just deploy`. This passes the two new keys to Homepage, the media setup and the cleanup job. Check that the Homepage widgets show numbers.
5. **Prowlarr** (`:9696`): add indexers (*Indexers → Add*). They are synced to Radarr and Sonarr by themselves. Download clients, the `frogswamp` quality profile, folders and Bazarr were already set up by the server.
6. **Radarr / Sonarr:** optional, *Settings → Indexers → (each) → Minimum Seeders* = 3.
7. **Phone:** Tailscale app (always-on VPN), HA Companion app (`http://192.168.101.10:8123`), Jellyfin app.
8. **TV:** Kodi with the Jellyfin add-on, see the [media README](modules/services/media/README.md#on-the-tv-kodi). For the Football page: the TV's static IP (set on the TV) and Kodi's remote control, see the [football README](modules/services/football/README.md#one-time-setup).
9. `just backup-now`, so the backup contains all of the above.

## 2. Making changes

```bash
# edit files
just check     # tests + HA config check
just deploy    # apply to the server (shows the diff first)
```

| Task | How |
|---|---|
| Risky change (network, SSH) | `just try`: active now, a reboot undoes it. Then `just deploy` |
| Undo the last deploy | `just rollback` (or pick an older generation in the boot menu) |
| Update NixOS + packages | `just update` → `just check` → `just deploy` |
| Update Home Assistant | `just ha-bump` → `just check` → `just deploy` |
| Add or rotate a secret | `just secrets` → `just deploy` ([details](secrets/README.md)) |
| Add an HA automation in git | see the [Home Assistant README](modules/services/home-assistant/README.md) |
| Save UI-made automations into git | `just ha-pull` → review the diff → commit ([details](modules/services/home-assistant/README.md#ui-automations-in-git-just-ha-pull)) |
| Download a movie or show | see the [media README](modules/services/media/README.md) |
| Back up now | `just backup-now` |
| Extra backup copy on this PC | `just backup-pull` (to `~/frogswamp-backup`) |
| Try the config locally | `just vm`, then `ssh -p 2222 pupsich@localhost` |
| NixOS 26.11 upgrade | change `nixos-26.05` → `nixos-26.11` in `flake.nix`, read the release notes, `just update` → `just try` → `just deploy` |

## 3. Resetting from scratch (reinstall or new hardware)

One command reinstalls the server and restores its state:

```bash
just rebuild-from-scratch frogswamp-installer.local
```

It wipes the NUC's disk, installs the system from this repo, waits for the server, then restores the latest backup from the USB SSD (or from B2 if the SSD is missing). Afterwards everything is back as it was at the last backup: HA, Zigbee devices, Tailscale, Jellyfin users and watched status, Arr settings and indexers. Only media files are lost.

**Follow [RESET.md](RESET.md) step by step**: what to prepare, what to do at the NUC, what to check after, and what to do when a step fails.

| Situation | Do |
|---|---|
| New hardware with another disk name | set `frogswamp.disk` in `hosts/frogswamp/default.nix` first |
| New or reset router | redo the [DHCP pool](#11-router-dhcp-pool) first |
| USB SSD lost or broken | `just rebuild-from-scratch` uses B2 by itself; later `just init-backup-disk` a new disk |
| Only restore (system fine, app state broken) | `just restore local --force` (or `b2`) |
| Lost the admin age key | see [secrets/README.md](secrets/README.md#recovery) |

## Troubleshooting

- Service logs: `ssh root@192.168.101.10 journalctl -u <unit> -e`. Units: `podman-home-assistant`, `zigbee2mqtt`, `mosquitto`, `jellyfin`, `samba-smbd`, `homepage-dashboard`, `qbittorrent`, `radarr`, `sonarr`, `prowlarr`, `bazarr`, `media-provision`, `media-janitor`, `restic-backups-local`, `restic-backups-b2`
- HA container: `ssh root@192.168.101.10 podman logs -f home-assistant`
- Failed units: `ssh root@192.168.101.10 systemctl --failed`
- `just check` very slow: KVM isn't available to Nix builds, see the optional KVM step in [1.3](#13-management-pc-linux-mint--ubuntu)
- Local backup failed, the Backups tile says `missing`, or `/mnt/backup` is empty: the A400 wasn't detected. `ssh root@192.168.101.10 lsblk` shows no `frogbackup` disk, and `dmesg` shows `device not accepting address … error -62`. Its USB-SATA case sometimes doesn't answer at a cold start. Unplug its cable, wait 5 seconds, plug it back in; it is mounted by itself. If it happens often, use another port (a USB 2 port is enough) or another cable
- Zigbee2MQTT UI down or restarting: the dongle was unplugged (`/dev/zigbee` missing). Plug it back into any USB port. Z2M resumes the same network by itself
- A new download doesn't show up in Kodi: Jellyfin add-on → *Manage libraries → Update libraries*, or start a library scan in Jellyfin (*Dashboard → Libraries → Scan all libraries*)
