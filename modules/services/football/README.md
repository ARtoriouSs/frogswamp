# Football: Ace Stream matches on the TV

Pick a match on the phone or PC, tap one button, and it plays on the TV (through its Chromecast, or in Kodi).

| Part | Where | What it does |
|---|---|---|
| Football page | http://192.168.101.10:8093 (dashboard → Media → Football) | Paste the match, *Chromecast to TV* or *Play in Kodi* |
| Ace Stream engine | http://192.168.101.10:6878 (`acestream` container) | Streams the match over P2P |
| Kodi on the TV | 192.168.101.20 | Plays the stream |

The same URLs work away from home with the Tailscale app on (playing still happens on the TV at home).

## Watching a match

1. Open the **Football** page on the phone.
2. Tap the **search-ace.stream** link, find the match, copy its ID (40 characters) or its `acestream://` link.
3. Back on the page, paste it. Optional: give it a name, so it's easy to find in *Recent*.
4. Pick how to watch:
   - **📺 Chromecast to TV** (easiest): Home Assistant casts the HLS stream to the TV's built-in Chromecast. Works whatever app is open on the TV; Kodi isn't needed.
   - **▶ Play in Kodi**: Kodi on the TV plays the stream. Kodi must be **open** and its remote control on (see setup); the TV doesn't let the server open Kodi by itself.
   - **Open / cast (HLS)**: opens the HLS link in the browser. From Chrome on the PC, cast that tab, like before.

   The first picture can take up to a minute: the engine has to find peers first.
5. **Recent** keeps the last 8 matches. Tapping one fills the form; then pick Kodi or Chromecast.

## One-time setup

1. **TV address:** the TV has the static IP `192.168.101.20`, set on the TV itself: *Settings → Sieć i internet → pupsich-network → Ustawienia IP → Statyczny*, gateway and DNS `192.168.101.1`, prefix length `24`. (The *Wired* section has its own static IP setting; it only applies to a cable.) `.20` is outside the router's DHCP pool, so nothing else gets it. If it changes, update `football.kodi.host` in `hosts/frogswamp/default.nix` and `just deploy`.
2. **Kodi on the TV:** *Settings → Services → Control*:
   - *Allow remote control via HTTP*: on, port `8080`
   - Username: `kodi`
   - Password: the one from `just secret kodi/password` (run it in your own terminal)
3. Home Assistant needs the **Google Cast** integration for the TV (`media_player.philips_tv`) for *Chromecast to TV*, and the **Android TV Remote** integration (`remote.living_room_philips_tv`) to wake the TV before Kodi. Both already exist. (The TV rejects HA's "open app" command, so Kodi has to be opened by hand.)

## When it doesn't work

| Message | What to do |
|---|---|
| *Ace Stream engine isn't running on the server.* | `ssh root@192.168.101.10 systemctl restart podman-acestream`, then check `journalctl -u podman-acestream -e` |
| *Kodi didn't answer…* | Kodi must be open on the TV. Then check step 2 of the setup; if it's all on and it still fails, turn *Allow remote control via HTTP* off and on again (Kodi on Android sometimes doesn't restart its web server). Or use **Chromecast to TV** |
| *Home Assistant couldn't cast to the TV.* | Check the TV's *Google Cast* entity (`media_player.philips_tv`) in HA. The TV must be on, or allow *Switch on with Chromecast* |
| *The TV couldn't be woken through Home Assistant.* | Kodi still played if it was open. Check the Android TV Remote integration in HA |
| Kodi shows an error or stays black | The match may have no peers right now. Try another link for the same match |
| *That isn't an Ace Stream ID or link.* | Copy the 40-character ID or the full `acestream://…` link |

Logs: `just ssh journalctl -u football -n 20`.

## Notes

- **Ports:** 8093 (page), 6878 (engine), 8621 TCP+UDP (P2P).
- **Fewer peers than possible:** the provider puts the home connection behind its own NAT (CGNAT: the router's WAN address isn't public and changes). So no peer can connect to the server, and port forwarding or UPnP in the router can't change that. The engine still connects out to other peers, which is enough for most matches. Only a public IPv4 address from the provider (a paid "static IP" option) would change it.
- **Engine version:** pinned in `modules/services/acestream/image.nix`. To update, change the tag and digest there (`skopeo inspect docker://docker.io/martinbjeldbak/acestream-http-proxy:<tag>`; Docker Hub, because the route to ghcr.io loses packets), then `just deploy`.
- **Legal:** Ace Stream is legal, but many football streams on it are unlicensed broadcasts. While you watch, the engine also uploads to other viewers, which counts as distribution in the EU. What you watch is your responsibility.
