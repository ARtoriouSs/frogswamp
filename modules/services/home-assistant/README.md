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

## Bluetooth recovery

If the Bluetooth chip hangs, the kernel resets it and it comes back under a new name (`hci0` → `hci1`). HA would keep scanning on the old one. A udev rule restarts HA automatically when an adapter re-appears and HA has been running for over 2 minutes (`home-assistant-bluetooth-recover.service`). Check it with `just ssh journalctl -u home-assistant-bluetooth-recover`.

## Update Home Assistant

`just ha-bump` (latest stable) or `just ha-bump 2026.11.0` → `just check` → `just deploy`. Roll back with `just rollback`.

## Debug

`ssh root@192.168.101.10 podman logs -f home-assistant`
