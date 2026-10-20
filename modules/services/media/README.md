# Media: download, watch, forget

Pick a movie or show on your phone, watch it on the TV in minutes, and it cleans itself up after you've seen it.

| App | URL | What it does |
|---|---|---|
| Radarr | http://192.168.101.10:7878 | Movies: search and add |
| Sonarr | http://192.168.101.10:8989 | Shows: search and add |
| Prowlarr | http://192.168.101.10:9696 | Torrent sites (indexers), synced to Radarr and Sonarr |
| qBittorrent | http://192.168.101.10:8090 | Downloads |
| Bazarr | http://192.168.101.10:6767 | Subtitles for finished downloads (RU, EN, PL) |
| Jellyfin | http://192.168.101.10:8096 | Libraries the TV plays from |

The same URLs work away from home with the Tailscale app on. Login: `pupsich`, password from `just secret arr/ui_password`. Radarr, Sonarr and Prowlarr skip the login on the home network. qBittorrent and Bazarr always ask.

## From the phone

- **Movie:** Radarr → *Add New* → search → **Quality profile `frogswamp`** → *Add + Search*.
- **Show:** Sonarr → *Add New* → search → **profile `frogswamp`**, **Monitor: Latest season** (or pick one season) → *Add + Search*. Only that season is ever grabbed. New seasons are never monitored automatically.
- The `frogswamp` profile prefers **4K**, falls back to **1080p**, never upgrades, and prefers **Russian audio** (then English, Polish).

## On the TV (Kodi)

1. Install the **Jellyfin for Kodi** add-on (or **JellyCon**) from the `https://kodi.jellyfin.org` repository and connect to `http://192.168.101.10:8096` (it finds `frogswamp` by itself). Playback mode: **Add-on**. Sync both libraries (*Movies*, *Shows*). Say yes to the **Kodi Sync Queue** server plugin (Jellyfin → Dashboard → Plugins), so Kodi catches up on changes made while the TV was off.
2. Libraries:
   - **Movies / Shows**: a title shows up here as soon as its download starts, so you can watch right away.
   - When the download finishes, the title shows **twice** for a few minutes (download copy + library copy). Then the download copy goes away. If you're watching it at that moment, it waits until you stop.
   - A new title shows up in Jellyfin about a minute after it appears on disk (`jellyfin-rescan` starts a library scan). If Kodi still doesn't show it, run *Jellyfin add-on → Manage libraries → Update libraries*.
3. Kodi → *Settings → Player → Language*: preferred audio **Russian**, subtitles **Russian**. Kodi has no "subtitles only when the audio isn't Russian" option; turn subtitles on or off per film in the player menu.
4. While a title is still downloading, Bazarr can't help. Use Kodi's subtitle search (*Subtitles → Download*) if needed.

## Cleanup

The cleanup (`media-janitor`) runs every 5 minutes:

- A **finished torrent** is removed from qBittorrent as soon as Radarr/Sonarr has imported it, together with its download copy. The library copy is a hardlink (same data on disk), so the title stays. Your watched mark and resume position move over to the library copy.
- A movie, or a **single episode**, is deleted once it's **watched** (Jellyfin *Played*, ~90%) **and** it was last played **more than 48 h** ago. Each episode counts on its own, so a season goes away episode by episode. That covers the file and the entry in Radarr/unmonitoring in Sonarr.
- **Unwatched titles are never deleted.** After **5 days** they're counted on the dashboard (*Home Assistant → stale downloads*, HA entity `sensor.stale_downloads`).
- A torrent with videos that Radarr/Sonarr **didn't import** (for example a full-series pack while you follow one season) is **kept**, because removing it would delete those videos. It's counted as a stale download right away; the log says which files. Remove it in qBittorrent when you don't need them. Sample files don't count.
- Nothing is removed while someone is watching it.
- Check what it did: `just ssh journalctl -u media-janitor -n 20`. For a test mode that only logs, set `mediaJanitor.dryRun = true;` in `hosts/frogswamp/default.nix` and `just deploy`.

## Where settings live

- **Repo (seeded once):** folders, qBittorrent behaviour (sequential download, no `.!qB`, stop seeding at completion), the `frogswamp` profile and language scores, Prowlarr↔Radarr/Sonarr links, Bazarr languages, the Jellyfin Movies/Shows libraries and their folders. The setup (`media-provision.service`) only **adds what's missing**. Anything you change in an app's UI stays, with one exception: **qBittorrent's settings come from the repo** and are written again at every restart, so change them in `modules/services/qbittorrent.nix`. Torrents themselves are kept.
- **Backups (nightly, A400 + B2):** everything else in the apps: indexers, your lists, history, UI changes.
- **Not backed up:** the media files themselves.

## Notes

- **Disk:** Radarr and Sonarr don't import a download when less than 20 GB would be left free (they still grab it). 4K films are 15–80 GB each.
- **Router (optional):** forwarding TCP+UDP **51413** to `192.168.101.10` gives more peers and faster downloads.
- **Legal:** these tools are legal, but torrenting copyrighted films or shows uploads them to others while downloading, which counts as distribution in Poland/the EU. What you download is your responsibility.
