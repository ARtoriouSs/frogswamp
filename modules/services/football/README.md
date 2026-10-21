# Football: Ace Stream matches on the TV

Pick a match on the phone or PC, tap one button, and it plays on the TV's Kodi.

| Part | Where | What it does |
|---|---|---|
| Football page | http://192.168.101.10:8093 (dashboard → Media → Football) | Paste the match, *Play on TV* |
| Ace Stream engine | http://192.168.101.10:6878 (`acestream` container) | Streams the match over P2P |
| Kodi on the TV | 192.168.101.119 | Plays the stream |

The same URLs work away from home with the Tailscale app on (playing still happens on the TV at home).

## Watching a match

1. Open the **Football** page on the phone.
2. Tap the **search-ace.stream** link, find the match, copy its ID (40 characters) or its `acestream://` link.
3. Back on the page, paste it. Optional: give it a name, so it's easy to find in *Recent*.
4. Tap **▶ Play on TV**. The server checks the engine, wakes the TV and opens Kodi through Home Assistant, waits for Kodi (up to 40 s) and starts the stream. The first picture can take up to a minute: the engine has to find peers first.
5. **■ Stop on TV** stops it. **Recent** replays one of the last 8 matches with one tap (handy after the break).

**Cast instead of Kodi:** *Open / cast (HLS)* opens the HLS stream (`/ace/manifest.m3u8?id=…`) in the browser. From Chrome on the PC, cast that tab to the TV, like before.

## One-time setup

1. **Router:** give the TV (MAC `9c:95:61:90:4c:9b`) a fixed address with a DHCP reservation for `192.168.101.119`. If it ever changes, update `football.kodi.host` in `hosts/frogswamp/default.nix` and `just deploy`.
2. **Kodi on the TV:** *Settings → Services → Control*:
   - *Allow remote control via HTTP*: on, port `8080`
   - Username: `kodi`
   - Password: the one from `just secret kodi/password` (run it in your own terminal)
3. Home Assistant needs the **Android TV Remote** integration for the TV (`remote.living_room_philips_tv`). It already has it; it's what wakes the TV and opens Kodi.

## When it doesn't work

| Message | What to do |
|---|---|
| *Ace Stream engine isn't running on the server.* | `ssh root@192.168.101.10 systemctl restart podman-acestream`, then check `journalctl -u podman-acestream -e` |
| *Kodi didn't answer…* | Is the TV on, and Kodi open? Check step 2 of the setup (remote control on, same password). The TV may not wake from deep standby; turn it on with the remote and try again |
| *The TV couldn't be woken through Home Assistant.* | Kodi still played if it was open. Check the Android TV Remote integration in HA |
| Kodi shows an error or stays black | The match may have no peers right now. Try another link for the same match |
| *That isn't an Ace Stream ID or link.* | Copy the 40-character ID or the full `acestream://…` link |

Logs: `just ssh journalctl -u football -n 20`.

## Notes

- **Ports:** 8093 (page), 6878 (engine), 8621 TCP+UDP (P2P; forwarding it in the router can give more peers).
- **Engine version:** pinned in `modules/services/acestream/image.nix`. To update, change the tag and digest there (`skopeo inspect docker://ghcr.io/martinbjeldbak/acestream-http-proxy:<tag>`), then `just deploy`.
- **Legal:** Ace Stream is legal, but many football streams on it are unlicensed broadcasts. While you watch, the engine also uploads to other viewers, which counts as distribution in the EU. What you watch is your responsibility.
