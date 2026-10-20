# Reset checklist: wipe and rebuild the server

Use this to reinstall the NUC from scratch (a drill, a broken system, or new hardware). The server comes back with the state of the **last backup**. Media files (`/srv/media`) are **lost**.

Time: about 10 minutes of preparation, 20–40 minutes of waiting, 10 minutes of checks.

You need:
- the management PC with this repo and the admin age key (`~/.config/sops/age/keys.txt`)
- the installer USB stick
- a monitor and a keyboard for the NUC
- the A400 backup disk plugged into the NUC

## A. Prepare (PC)

- [ ] **Repo is up to date and committed.** The install uses exactly what is in this folder. Run `git status`. Commit or drop anything you don't want on the server.
- [ ] **Secrets can be decrypted:** `just secret samba/password >/dev/null && echo OK` prints `OK`.
- [ ] **Write down what must come back**, to compare later:
  - one Home Assistant automation name
  - the number of devices in Zigbee2MQTT
  - one film or episode marked as watched in Jellyfin
  - the indexers in Prowlarr
- [ ] **Fresh backup:** `just backup-now`. Then check that both backups are from today:
  ```bash
  ssh root@192.168.101.10 'restic-local snapshots --latest 1 --compact; restic-b2 snapshots --latest 1 --compact'
  ```
- [ ] Optional, extra copy on this PC: `just backup-pull`.
- [ ] **Installer stick:** if you don't have one, or `keys/admin.pub` changed since you made it: `just iso`, then write `result/iso/*.iso` to the stick (Mint: right-click → *Make bootable USB stick*).
- [ ] You're OK with losing the downloaded films and shows.

## B. Boot the installer (at the NUC)

- [ ] Connect the monitor and keyboard. Plug in the USB stick.
- [ ] Reboot the server: `ssh root@192.168.101.10 reboot`.
- [ ] Press **`F7`** a few times right after it powers on. Choose the USB stick (the *UEFI* entry if there are two).
- [ ] Wait for the installer's command prompt on the screen (about 1 minute).
- [ ] On the PC: `ping -c 3 frogswamp-installer.local` answers.
  - No answer? On the NUC type `ip -4 addr` and use the `192.168.101.x` address instead of `frogswamp-installer.local` in the next step.

## C. Wipe, install, restore (PC)

- [ ] Run:
  ```bash
  just rebuild-from-scratch frogswamp-installer.local
  ```
- [ ] It shows the NUC's disk (`nvme0n1`, ~477 GB). Type **`wipe`** and press Enter.
- [ ] Wait (5–15 minutes). It partitions the disk and copies the system. **Keep the stick in during this time**: the installer runs from it.
- [ ] The PC prints **`Installed.`** and **`>>> PULL OUT THE USB STICK NOW`**, and the NUC reboots. **Pull out the stick at this moment.** The new system is already on the internal disk.
  - The NUC's screen may show USB errors after you pull the stick. That's normal. **Don't press the power button**; the new system is starting.
  - Only if the screen shows the installer again (the `nixos@frogswamp-installer` prompt, or a menu titled *NixOS … Installer*): pull the stick, then press the power button.
- [ ] Wait. The script waits for `192.168.101.10` (up to 10 minutes), then restores the backup. Don't touch the NUC until it ends.
- [ ] The end should look like this:
  ```
  restore from local (latest) complete
  Rebuild complete. Check HA, Z2M, Jellyfin and Homepage in the browser.
  ```
  If it says `Local backup unavailable, trying B2...`, the A400 wasn't found and it restored from B2 instead. That's fine.

## D. Check

- [ ] No failed services: `ssh root@192.168.101.10 systemctl --failed` lists `0 loaded units`.
- [ ] **Homepage** (http://192.168.101.10:8082) opens, and the widgets show numbers, not errors.
- [ ] **Home Assistant:** your automation is there, integrations are connected with no new login, and a light or switch works.
- [ ] **Zigbee2MQTT:** same number of devices, and one responds. If its page doesn't open, check the Zigbee dongle is plugged in; Z2M starts by itself once it is.
- [ ] **Jellyfin:** you can log in, and the watched film is still marked as watched.
- [ ] **Prowlarr / Radarr / Sonarr:** indexers are there, and the `frogswamp` profile exists.
- [ ] **qBittorrent:** old torrents show *Missing files*. Remove them: select → right-click → *Remove* (the files are gone anyway).
- [ ] **Radarr / Sonarr:** films and episodes whose files were lost are *Missing* again, and Radarr/Sonarr may download them again. If you don't want that, delete or unmonitor them.
- [ ] **TV:** Kodi opens *Movies* without asking to log in again.
- [ ] **Tailscale:** in the admin console (https://login.tailscale.com/admin/machines) there is one `frogswamp`, online. If there is also a `frogswamp-1`, remove it (it's a leftover from the first boot before the restore).
- [ ] **Phone on mobile data** (Wi-Fi off): Home Assistant opens.
- [ ] **Hardware report:** the install re-reads it into `hosts/frogswamp/facter.json`. On the same NUC, small changes are normal (another USB stick, the A400, keyboard or monitor plugged in). Keep the old file: `git checkout hosts/frogswamp/facter.json`. On new hardware, commit the new one.
- [ ] Next morning: the nightly backup ran: `ssh root@192.168.101.10 'restic-local snapshots --latest 1 --compact'` shows today's date.

## E. If something goes wrong

| Problem | What to do |
|---|---|
| `Target has no /dev/nvme0n1` | Another disk name (new hardware). Set `frogswamp.disk` in `hosts/frogswamp/default.nix`, start again at C |
| `ssh: Could not resolve hostname frogswamp-installer.local` | Use the IP shown by `ip -4 addr` on the NUC (step B) |
| The NUC starts the installer again after `Installed.` (you see the `nixos@frogswamp-installer` prompt) | Pull the stick, press the power button. The script is still waiting |
| You pressed the power button while the script was restoring (`Connection … closed by remote host`) | Wait until `just ssh uptime` works, then `just restore local --force` |
| The script stops before the restore (server took too long) | Wait until `just ssh uptime` works, then `just restore local --force` (or `just restore b2 --force`) |
| `existing Home Assistant state found` (exit code 3, from a manual `just restore`) | `just restore local --force`. A fresh install always has some HA state, so this is expected |
| `repo local unreachable or missing` | Check the A400 is plugged in: `ssh root@192.168.101.10 lsblk` shows `frogbackup`. Then `just restore local --force`, or use `just restore b2 --force` |
| `WARNING: REMOTE HOST IDENTIFICATION HAS CHANGED` on ssh | `ssh-keygen -R 192.168.101.10`, then try again. (Normally it doesn't happen: the host key comes from `secrets/`) |
| One app looks empty or broken after the restore | Look at its log: `ssh root@192.168.101.10 journalctl -u <unit> -e`. Restoring again is safe: `just restore local --force` |
| The server doesn't come back at all | Look at the NUC's screen. Check the BIOS boots the internal disk (`nvme`) and not the USB stick |

The old system can't be brought back once the disk is wiped. Everything after step C goes forward: install again, or restore again.
