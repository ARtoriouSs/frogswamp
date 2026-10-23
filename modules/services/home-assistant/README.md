# Home Assistant

Runs the official image in Podman (`podman-home-assistant.service`), host networking, Bluetooth via D-Bus.

## Where config lives

| Location | Owner | Editable in UI |
|---|---|---|
| `config/` in this repo, mounted read-only | git | No (shown read-only) |
| `/var/lib/home-assistant` on the server (`.storage/`, `automations.yaml`, …) | HA UI | Yes, covered by backups |

Integrations added in the UI (MQTT, Hue, …) always live in `.storage/`. HA doesn't support declaring them in YAML.

## Promote a UI automation into the repo

1. Build it in the UI, open it → ⋮ → *Edit in YAML*, copy.
2. Save it as `config/automations/<name>.yaml` as a one-item list. Give it a unique `id:`.
3. `just check` → `just deploy`.
4. Delete the UI copy.

Scripts → `config/scripts/<name>.yaml` (mapping). Helpers/template sensors → a file in `config/packages/`. Dashboards → `config/dashboards/` + register in `configuration.yaml`.

## UI automations in git (`just ha-pull`)

| | Edit in UI | In git | Survives reinstall without backup |
|---|---|---|---|
| UI only | yes | no | no |
| UI + `just ha-pull` | yes | yes, as of the last pull | yes, as of the last pull |
| Promoted to `config/automations/` | no (read-only) | yes | yes |

`just ha-pull` copies the server's `automations.yaml`, `scripts.yaml` and `scenes.yaml` into `ui-snapshot/`, validates them, and shows the diff. Review it and commit. On a fresh install the server files are created from this snapshot. After that the server copy is the live one again, and the UI can edit it.

## Rules for automations kept in git

1. **Use entity IDs, never device IDs.** Device IDs (`device_id: 8f3c…`) are random per install and break after a reinstall without backup. Use *State* triggers and `target: entity_id: …` actions instead of *Device* triggers/actions. `just check` fails on `device_id:` in `config/`.
2. **Give devices stable, readable names.** Rename the device in Zigbee2MQTT or HA (e.g. `living_room_lamp`) and accept "rename entity IDs", giving `light.living_room_lamp`. Keep "Use device name" on for entities.
3. **Missing entities aren't fatal.** HA loads the automation and logs "entity not found" when it runs. `just check` can't see the live entity list, so check the HA log after deploying.
4. **When promoting a UI automation**, convert device triggers/actions to entity form first.

## Phone alerts

`config/automations/alerts.yaml` sends these to the Pixel 7 (`notify.mobile_app_pixel_7`). Each one fires once per problem.

| Alert | When |
|---|---|
| Backup is old | Newest local or B2 snapshot older than 48 h, or the repo is unreachable |
| Backup disk missing | `sensor.backup_disk` is `missing` for 30 min. The sensor is refreshed hourly, so it can take up to 1.5 h |
| Stale downloads | `sensor.stale_downloads` goes above 0 |
| Device offline | A listed device is `unavailable` for 1 h. **Add new devices to its list**. Skipped while its whole integration is down |
| Integration down | An integration with entities failed (`sensor.failed_integrations`) for 10 min. It's reloaded once by itself; the message says whether that helped |
| Zigbee bridge down | Z2M disconnected for 10 min |
| Internet was down | Router WAN back on after at least 1 min down. Also written to the logbook |
| Printer ink low | A cartridge drops below 15% |

## Modes (General actions card)

Switches in `config/packages/modes.yaml`. Automations check them in their conditions, so modes can be on together.

| Mode | Stops | When turned on, also runs the Tuya scene |
|---|---|---|
| Day off | desk schedule | `Days off - on` |
| Vacation | desk, floor warming, blinds, curtains | `Vacation - on` |
| Guests in the living room | blinds opening in the morning | `Guests in the living room - on` |
| Tuya rule mode | every HA automation that moves Tuya devices (alerts keep running) | none |

Turning a mode off runs no Tuya scene, so the Tuya app's own automations stay off. **TV mode** (`script.tv_mode`) turns the TV on and opens YouTube by its link.

## Bluetooth recovery

If the Bluetooth chip hangs, the kernel resets it and it comes back under a new name (`hci0` → `hci1`). HA would keep scanning on the old one. A udev rule then reloads **only HA's Bluetooth integration** through the HA API (`home-assistant-bluetooth-recover.service`), so the rest of HA keeps running. HA finds the adapter again by its MAC address. It does nothing during boot or in HA's first 2 minutes. Check it with `just ssh journalctl -u home-assistant-bluetooth-recover`.

## Yeelight Cube Matrix

The integration lives in `integrations/cubematrix/` (a standalone repo-shaped folder) and is mounted read-only at `/config/custom_components/cubematrix`. Repo presets are in `config/cubematrix/*.yaml`, mounted at `/config/cubematrix`; `tests/cubematrix-presets.nix` checks that each one parses. The integration draws over the lamp's UDP session (outgoing to port 55444, replies on the same socket), so the firewall needs no extra rule.

## Update Home Assistant

`just ha-bump` (latest stable) or `just ha-bump 2026.11.0` → `just check` → `just deploy`. Roll back with `just rollback`.

## Debug

`ssh root@192.168.101.10 podman logs -f home-assistant`
