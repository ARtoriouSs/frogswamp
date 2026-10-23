# Yeelight Cube Matrix for Home Assistant

Play pixel art and animations on a Yeelight Cube Matrix lamp. The integration talks to the lamp over your local network. There is no cloud.

You get:

- a **light** (on/off and brightness),
- a **select** called Background (what the cube shows when nothing else plays),
- a **sensor** called Playing (the name of what is on the cube now),
- actions to play pixel art, animations and generated effects.

## Supported hardware

- Yeelight Cube Matrix (model `cubematrix`). The lamp must be a Cube Matrix, other Yeelight lamps are refused.
- One or more 5x5 matrix modules, with optional spot modules, chained in a row or in a column.
- Several cubes in one Home Assistant are fine, each one is its own entry.

Tested with Home Assistant 2026.10 (Python 3.14).

## Before you install

### Turn on LAN Control

In the Yeelight app, open the lamp, go to its settings and turn on **LAN Control**. Without it the lamp does not answer on the network.

### Reserve the lamp IP in your router

The integration finds the lamp by its IP address. Give each lamp a fixed address:

1. Find the lamp's MAC address in your router's device list.
2. In the router's DHCP settings, add a reservation (static lease) for that MAC and the IP you want.
3. Restart the lamp, or wait until it renews its lease.

This way the lamp keeps the same address after a restart of the lamp or the router.

### Music mode port

Normal LAN control allows only about 60 commands per minute, which is about 1 frame per second. For smooth animations the integration uses **music mode**: the lamp opens a TCP connection **back to Home Assistant** and then accepts frames at full speed.

- Default port: **55440**. Each cube has this port in its options (see below). Cubes with the same port share one listener, and lamps are told apart by their IP.
- Keep the same port on every cube (the default 55440 is fine), so the firewall needs only one port. A cube with a different port needs its own firewall rule.
- Home Assistant's host must accept incoming TCP connections on this port **from the lamp IPs**.
- Open the port only for the lamp IPs, not for your whole network.

Generic example with `iptables` (replace `<lamp ip>`):

```sh
iptables -A INPUT -p tcp -s <lamp ip> --dport 55440 -j ACCEPT
```

NixOS example (add one line per lamp):

```nix
networking.firewall.extraCommands = ''
  iptables -A nixos-fw -p tcp -s <lamp ip> --dport 55440 -j nixos-fw-accept
'';
networking.firewall.extraStopCommands = ''
  iptables -D nixos-fw -p tcp -s <lamp ip> --dport 55440 -j nixos-fw-accept || true
'';
```

If Home Assistant runs in a container, make sure the port is reachable from the lamp: use host networking, or publish the port (`-p 55440:55440`).

If the lamp can't connect, the integration falls back to normal mode (about 1 frame per second) and writes a warning to the log. See [Troubleshooting](#troubleshooting).

## Installation

### HACS (custom repository)

Not published yet. When it is:

1. In HACS, open the menu, choose **Custom repositories**.
2. Add the repository URL, category **Integration**.
3. Install **Yeelight Cube Matrix** and restart Home Assistant.

### Manual

1. Copy the folder `custom_components/cubematrix` into the `custom_components` folder of your Home Assistant config folder.
2. Restart Home Assistant.

## Setup

1. Go to **Settings > Devices & services > Add integration** and pick **Yeelight Cube Matrix**.
2. Enter the lamp's IP address.

The integration checks that the lamp answers and that it is a Cube Matrix. A lamp can be added only once.

### Options

Open the integration and choose **Configure**.

| Option | What it does |
| --- | --- |
| Module layout: `modules` | The modules in chain order, split by commas, for example `matrix, spot, matrix`. Default: `matrix`. At least one `matrix` is needed. |
| Module layout: `direction` | `horizontal` (modules side by side, canvas 5 pixels high) or `vertical` (modules stacked, canvas 5 pixels wide). Default: `horizontal`. The first matrix is the leftmost one, or the bottom one when vertical. |
| Music mode port: `music_port` | The TCP port the lamp connects back to. Default: 55440. Use the same port on every cube. |
| Add a preset (`name`, `text`) | Save a pixel art in the pixel art format. Shared by all cubes. |
| Edit a preset (`name`, `text`) | Change a saved preset. |
| Delete a preset (`name`) | Remove a saved preset. |

The canvas size follows the layout: two matrices side by side are 10x5, three are 15x5, two stacked are 5x10.

## Pixel art format

An art is a YAML mapping. This is a small arrow:

```yaml
name: arrow_demo
size: 5x5
palette:
  x: '#00b4ff'
fps: 8
frames:
  - |
    ..x..
    .xxx.
    x.x.x
    ..x..
    ..x..
```

| Key | Meaning |
| --- | --- |
| `name` | Name of the art. Needed in preset files and saved presets. |
| `size` | `WIDTHxHEIGHT`, for example `5x5` or `10x5`. |
| `palette` | Maps one character to a colour. `.` is always off (black). |
| `frames` | A list of frames, at least one. One frame means a still picture. |
| `fps` | Frames per second. Default: 5. |
| `spots` | Colours for the spot modules (see below). |
| `loop` | Accepted for compatibility, but not used. Use `repeat` in the `show` action. |

Frames are written **top-left first, row by row**, as you see the cube. Whitespace inside a row is ignored, so `. . x . .` works too.

Colours are `'#rrggbb'`, `off` (with or without quotes, both work), or `[r, g, b]` with values from 0 to 255.

### Raw arrays

Instead of a palette, a row can be a list of colours:

```yaml
name: red_dot
size: 2x1
frames:
  - - [[255, 0, 0], "off"]
```

A frame can also be a list of rows, each row a text or a list. Text rows and list rows can be mixed.

### Per-frame duration

Give a frame as a mapping with `pixels` and `duration` (seconds) to override `fps` for that frame:

```yaml
name: blink
size: 5x5
palette: {x: '#ffffff'}
frames:
  - pixels: |
      .....
      .....
      ..x..
      .....
      .....
    duration: 2
  - pixels: |
      .....
      .xxx.
      .xxx.
      .xxx.
      .....
    duration: 0.3
```

### Spots

Spot modules have no pixels. With no `spots` key they take the average colour of the frame. To set them, give one colour per spot module, in chain order:

```yaml
spots: ['#ff0000', '#0000ff']
```

### Fit

When an art is smaller than the cube canvas, the `show` action's `fit` option decides what happens:

| `fit` | Result |
| --- | --- |
| `error` | The action fails. This is the default. |
| `center` | The art sits in the middle, the rest is black. |
| `tile` | The art repeats to fill the canvas. |

An art bigger than the canvas always fails. The background always uses `center`.

## Presets

A preset is a named art. Names are found in three places. If the same name is in more than one place, the first one wins:

1. **Saved presets**: added in the integration options (stored by Home Assistant).
2. **Preset files**: `*.yaml` files in the `cubematrix` folder of your Home Assistant config folder. A file holds one art or a list of arts. After a change, run the `cubematrix.reload_presets` action. A broken file is skipped and logged.
3. **Built-in**: shipped with the integration.

Built-in arts, all 5x5:

- `arrow_up`, `arrow_down`, `arrow_left`, `arrow_right`
- `blinds_open`, `blinds_close`
- `check`

## Generated effects

These need no art, they are calculated for any canvas size. Use them as a preset name:

| Name | Look |
| --- | --- |
| `fire` | Flames. This is the default background. |
| `rainbow` | A moving band of colours. |
| `plasma` | Slow flowing waves. |

An effect plays for 5 seconds by default. Use `duration` to change that, or `repeat: forever` to let it run.

## Actions

All actions except `reload_presets` are used on cube lights (`target: entity_id: light.my_cube`).

### `cubematrix.show`

Plays an art or effect on top of the background. When it ends, the cube goes on as `then` says.

| Field | Meaning | Default |
| --- | --- | --- |
| `preset` | Name of a preset. Give `preset` or `definition`, not both. | |
| `definition` | One art in the pixel art format, as YAML text or a mapping. | |
| `repeat` | How many times an animation plays: `1`, `2`, `3`, any number, or `forever`. | `1` |
| `duration` | How long the show lasts, in seconds. Ends the show even if the animation is not finished. | still picture or effect: 5 s; animation: until `repeat` is done |
| `fps` | Speed of the animation, instead of the art's own `fps`. | the art's `fps` |
| `then` | What comes after: `restore` (the background), `hold` (keep the last frame), `off` (black), or a preset name (shown until something else is played). | `restore` |
| `fit` | `error`, `center` or `tile`. | `error` |
| `wait` | If `true`, the action finishes only when the show part ends (not the `then` part). | `false` |

With `repeat: forever` and no `duration`, the show never ends by itself. Use `stop` or another `show`. A new `show` replaces the current one.

### `cubematrix.set_background`

| Field | Meaning |
| --- | --- |
| `preset` | Name of a preset, or `off` for black. Needed. |

The same can be done with the Background select. A new cube starts with `fire`.

### `cubematrix.stop`

Ends the current show and goes back to the background.

### `cubematrix.reload_presets`

Reads the preset files again. It has no target.

## Automation examples

### Desk up / down arrow

```yaml
automation:
  - alias: Desk arrow
    triggers:
      - trigger: state
        entity_id: cover.desk
        attribute: current_position
    actions:
      - action: cubematrix.show
        target:
          entity_id: light.desk_cube
        data:
          preset: >-
            {{ 'arrow_up' if trigger.to_state.attributes.current_position
               > trigger.from_state.attributes.current_position else 'arrow_down' }}
          repeat: 3
```

### An arrow running over two cubes

`wait: true` on the first `show` makes the action wait until the arrow has left the first cube. Then the second cube starts.

```yaml
actions:
  - action: cubematrix.show
    target:
      entity_id: light.cube_left
    data:
      preset: arrow_right
      wait: true
  - action: cubematrix.show
    target:
      entity_id: light.cube_right
    data:
      preset: arrow_right
```

### Fire by day, off at night

Use fixed times. The `fire` effect is the background in the day and the cube is black at night.

```yaml
automation:
  - alias: Cube fire in the morning
    triggers:
      - trigger: time
        at: "07:00:00"
    actions:
      - action: cubematrix.set_background
        target:
          entity_id: light.desk_cube
        data:
          preset: fire

  - alias: Cube off at night
    triggers:
      - trigger: time
        at: "23:00:00"
    actions:
      - action: cubematrix.set_background
        target:
          entity_id: light.desk_cube
        data:
          preset: "off"
```

### Play your own art once

```yaml
actions:
  - action: cubematrix.show
    target:
      entity_id: light.desk_cube
    data:
      duration: 10
      then: hold
      definition:
        name: heart
        size: 5x5
        palette: {r: '#ff0030'}
        frames:
          - |
            .r.r.
            rrrrr
            rrrrr
            .rrr.
            ..r..
```

## Troubleshooting

**The colour changes by itself, or the picture disappears.**
Matter, the Yeelight app or another integration changed the lamp's colour. The lamp then leaves direct mode. The integration notices this and recovers, but there can be a short flash. A still picture (for example the `off` background) is checked and sent again every 30 seconds, so it can take up to 30 seconds to come back. The same check finds a lamp that lost power: the light becomes unavailable and comes back by itself. Do not control the same lamp from other apps or integrations while the cube is in use.

**The lamp ignores frames, or the cube stays dark or shows wrong pixels.**
- Check that **LAN Control** is on in the Yeelight app.
- Check that the IP in the integration is the lamp's current IP (use the DHCP reservation).
- Check the module layout in the options: the order and the number of `matrix` and `spot` modules must match your chain.
- Make sure the light is on.

**Animations are slow (about 1 frame per second) and the log says music mode didn't arrive.**
The lamp could not connect back to Home Assistant, so the integration uses normal mode. Check the firewall for port 55440 (or your own port), only for the lamp IP. Check that the lamp and Home Assistant can reach each other (same network or a route between them), and that the container, if any, publishes the port. Check that no other program uses the port. After you fix it, reload the integration, or wait: while the background plays, music mode is tried again every 5 minutes.

**Frame rate limit.**
In music mode the integration sends at most `MUSIC_MAX_FPS` frames per second. The current value is 10, which is a placeholder: the tested limit is to be measured on real hardware. Higher `fps` values are cut to this limit. In normal mode the limit is about 1 frame per second.

## Development

The tests need Python 3.14 (Home Assistant 2026.10). Run them from the integration folder:

```sh
python3.14 -m venv .venv && .venv/bin/pip install -r requirements_test.txt && .venv/bin/pytest
```

The core modules (`artformat.py`, `layout.py`, `effects.py`, `presets.py`, `protocol.py`, `connection.py`, `player.py`) do not import Home Assistant, so they can be tested alone.

## License

MIT, see `LICENSE`.
