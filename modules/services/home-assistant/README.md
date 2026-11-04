# Home Assistant

Runs the official image in Podman (`podman-home-assistant.service`), host networking, Bluetooth via D-Bus.

## Where config lives

| Location | Owner | Editable in UI |
|---|---|---|
| `config/` in this repo, mounted read-only | git | No (shown read-only) |
| `/var/lib/home-assistant` on the server (`.storage/`, `automations.yaml`, …) | HA UI | Yes, covered by backups |

Integrations added in the UI (MQTT, Hue, …) always live in `.storage/`. HA doesn't support declaring them in YAML.

## UI integrations with keys that expire

These stop working when their key runs out. HA then shows a *Repair* or asks to re-authenticate on the integration's card.

| Integration | Key | Lifetime | Added | Renew by | How to renew |
|---|---|---|---|---|---|
| Spotify | the login made with the Spotify developer app (`Frogswamp HA`, redirect URI `https://my.home-assistant.io/redirect/oauth`, Web API) | 180 days | 2026-11-01 | 2027-04-30 | Settings → Devices & services → Spotify → *Reconfigure*, log in to Spotify again. The app's Client ID / secret stay the same (Settings → Application credentials) |
| Tailscale | API access token (admin console → Settings → Keys → *Generate access token*, not an auth key) | up to 90 days, set when made | 2026-11-01 | 2027-01-30 | Make a new access token, then Tailscale → *Reconfigure* and paste it. Tailnet name: `-` or `taila39d1b.ts.net` |

Update the *Added* and *Renew by* dates when you renew.

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
| Device offline | A listed device is `unavailable` for 1 h. **Add new devices to its list**. Skipped while its whole integration is down |
| Integration down | An integration with entities failed (`sensor.failed_integrations`) for 10 min. It's reloaded once by itself; the message says whether that helped. UPnP and Android TV Remote are skipped, because they often drop and come back |
| Zigbee bridge down | Z2M disconnected for 10 min |
| Work calendar is old | Outside a day off, the work-calendar service hasn't read the calendar for 2 h, or can't be reached, for 30 min |
| Internet was down | Router WAN back on after at least 1 min down. Also written to the logbook |
| Printer ink low | A cartridge drops below 15% |

## Schedule

Daily timings live in `config/packages/schedule.yaml` as `input_datetime` helpers, and automations use them in time triggers (with an offset when needed). A change in the UI is reset on the next HA restart, so change them in the file.

Workdays come from the **Workday** integration (`binary_sensor.workday_sensor_pl`), so a holiday counts like a weekend day. Wakeup goes by the day itself, bedtime by the next day: before a workday it is the early one, before a weekend day or a holiday the late one. Night automations ask `workday.check_date` about the next day. While **Day off** is on, every check counts as a weekend day, so that morning and that night get the late timings. Turn it off the next day, after the night is over.

| Timing | Value | Used by |
|---|---|---|
| Wakeup | `sensor.wakeup`: the phone alarm of that morning if it rings from 04:00 to before 12:00, otherwise the fallback (workdays 09:30, weekends and holidays 11:00) | floor warming: 3 h from 1 h before. Cube night ends |
| Bedtime | before a workday 00:00, before a weekend day or holiday 01:00 | floor warming and master towel heat relay: 3 h from 1 h before. Cube night starts |
| Work | workdays 10:00–19:00 | desk schedule runs on workdays. Cube: coffee at the start, moon at the end |

Workday can only be added in the UI (once, it is kept in HA's state dir): **Settings → Devices & services → Add integration → Workday**. Country Poland (the entity becomes `binary_sensor.workday_sensor_pl`), workdays Monday–Friday, excludes Saturday, Sunday and Holidays, offset 0. Without it the floors, the towel heat nights and the desk don't run (Vacation still works).

## Modes

Switches in `config/packages/modes.yaml`. Automations check them in their conditions, so modes can be on together.

**Calendar.** Day off and Vacation also turn on from all-day events in the Google calendar `Home Assistant Events`, titled `Vacation` or `Day off` (`day-off` and `dayoff` work too, case doesn't matter). The switch always shows the mode, and the calendar sets it. Vacation starts at midnight on its first day. Bedtime and wakeup know about a day off the evening before. During an event, a switch turned off comes back within 15 minutes, so delete the event to cancel it. When an event ends, its switch turns off, even if it was turned on by hand. A switch turned on by hand with no event stays on. An event that ends while HA is down leaves its switch on. Day off also follows an all-day `Out of Office` in the work calendar (same title rules), and every day the Workday integration says isn't a workday, so the switch is on during weekends and holidays and turned off by hand comes back within 15 minutes. Automations read the switch, and the Schedule tab's calendar shows these events.

HA reads the calendar through **Remote Calendar**, added in the UI with the calendar's secret iCal link, so the link is not in this repo. It refreshes every hour, so a new event can take over an hour to count. If the link leaks, reset it in Google Calendar and add the integration again.

| Mode | Stops |
|---|---|
| Day off | desk schedule. Also counts the day as a weekend day for the schedule (late wakeup and bedtime) |
| Vacation | desk, floor warming, blinds, curtains. Also turns off both floors, guest towel heat and the master towel heat relay, and puts the cube in quiet mode. Nothing turns on again when Vacation ends, everything waits for its next scheduled time (the cube comes back at once, unless it is night) |
| Guests in the living room | blinds opening in the morning |
| Nobody home automations paused (`input_boolean.location_paused`) | the nobody-home quiet mode and the count to the Vacation suggestion, which need both people's locations. Turning it on also ends a running away state |
| Desk location rule paused (`input_boolean.desk_location_paused`) | stops the rule below, so desk automations run while Aleksander is away |

**Desk.** Automations ask for a lease (`script.desk_lease`, 30 min by default): the desk goes up, and down when the timer ends. A new lease can extend a running one, never shorten it. While Aleksander is away nothing moves, whatever Natallia's location is: no new lease starts, and one that ended then sends the desk down when Aleksander is back. Desk up and Desk down in HA end the lease, so the desk stays there until it is moved again. HA can't read the desk's height, so the pad on the desk isn't seen, and a lease can lower a desk raised by hand.

**Work meetings.** The `work-calendar` service (`modules/services/work-calendar`) reads the work calendar every 5 minutes and passes on only start, end and title. A meeting counts if Aleksander accepted it, it shows as busy, it isn't cancelled or all-day, and at least one other person is invited. All-day `Out of Office` events are passed on too, and meetings on those days are dropped. A meeting is cut to 09:00–18:00, so times on the Schedule calendar can differ from the real ones at the edges, and meetings fully outside these hours aren't shown. HA reads it through **Remote Calendar**, added in the UI: name `Work meetings`, URL `http://127.0.0.1:8129/work-meetings.ics`, then restart HA once so the automations find the calendar. A meeting asks for a desk lease until it ends, and the cube shows the blinking calendar page (`meeting` sign) for 30 s, 10 minutes before. Day off, Vacation, being away and the **Work meetings** button on the Schedule tab stop both. The cube sign also stops during quiet mode. New meetings reach HA within about 10 minutes. The service reads Google only when HA asks, and HA doesn't ask during a day off, apart from Remote Calendar's own read once a day, so changes made then can count late. Desk down during a meeting stays down until the next meeting, but an HA restart raises it again.

**TV mode** (`script.tv_mode`) turns the TV on and opens YouTube by its link.

## Room temperatures

The bathroom thermostats' air sensors hang outside, by the doors, so they measure the bedroom (master) and the hall (guest). Tuya gives this only as the `current_temperature` attribute of the thermostat, so `config/packages/room_temperature.yaml` makes `sensor.bedroom_temperature` and `sensor.hall_temperature` from it. The thermostats stay in the bathrooms. A template sensor's area can only be set in the UI (it is kept in HA's state dir): **Settings → Entities → the sensor → ⚙ → Area**.

## Energy costs

The Energy tab shows the cost of each device, Climate (floors + towel heaters), all devices and the fixed charge, from the 1st of the month. It's an estimate: only the devices in HA's Energy list, most of them calculated by Powercalc, not measured. The months stay in long-term statistics, the graphs under Compare months read them.

The Per kWh and Fixed fields on that tab are the prices. Enter gross prices, from the invoice. HA restores them after a restart, and they are in the backup with the rest of `/var/lib/home-assistant`. A new Per kWh price counts from that moment on, a new Fixed amount from the next month, and neither changes what was already counted. The whole Fixed amount is added on the 1st. It works only with one flat price (G11), not with day/night tariffs. A new device goes to `packages/energy_cost.yaml` and `dashboards/main.yaml`.

## Bluetooth recovery

If the Bluetooth chip hangs, the kernel resets it and it comes back under a new name (`hci0` → `hci1`). HA would keep scanning on the old one. A udev rule then reloads **only HA's Bluetooth integration** through the HA API (`home-assistant-bluetooth-recover.service`), so the rest of HA keeps running. HA finds the adapter again by its MAC address. It does nothing during boot or in HA's first 2 minutes. Check it with `just ssh journalctl -u home-assistant-bluetooth-recover`.

## Yeelight Cube Matrix

The integration lives in its own repo, [yeelight-cubematrix-ha](https://github.com/ARtoriouSs/yeelight-cubematrix-ha). `cubematrix.nix` pins a commit (update it with `just cubematrix-bump`), and it is mounted read-only at `/config/custom_components/cubematrix`; its tests run in that repo. Repo presets are in `config/cubematrix/*.yaml`, mounted at `/config/cubematrix`; `tests/cubematrix-presets.nix` checks that each one parses. The integration draws over the lamp's UDP session (outgoing to port 55444, replies on the same socket), so the firewall needs no extra rule.

**Quiet mode.** From bedtime to wakeup (`input_boolean.cube_night`, set by the schedule) and during Vacation, the cube shows only the `dot` preset (one green pixel) at 0%, which is in fact off. Frogs (hourly and button tap), coffee/moon and problem signs stop. Event animations (desk arrows, blinds, modes, TV play) still play through `script.cube_event` at 100%, and the brightness goes down to 0% again after them while the cube is still quiet. When quiet mode ends, a random frog comes at 100%. The steps are scripts: `script.cube_brightness_off` / `script.cube_brightness_on` (0% / 100%, also `switch.cube_bright`, the **Bright** toggle next to the cube's brightness on Overview), `script.cube_sleep` (the dot) and `script.cube_wake` (frogs). 0% is the raw brightness 1 of 255: HA shows it as 0%, and a real 0 would turn the light off. A wakeup missed while HA was down is not caught up: the cube stays dimmed until the next wakeup, or turn **Cube night** off by hand (Settings → Helpers).

## Update Home Assistant

`just ha-bump` (latest stable) or `just ha-bump 2026.11.0` → `just check` → `just deploy`. Roll back with `just rollback`.

## Debug

`ssh root@192.168.101.10 podman logs -f home-assistant`
