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

## Update Home Assistant

`just ha-bump` (latest stable) or `just ha-bump 2026.11.0` → `just check` → `just deploy`. Roll back with `just rollback`.

## Debug

`ssh root@192.168.101.10 podman logs -f home-assistant`
