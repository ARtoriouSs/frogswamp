# Voice jobs: one-shot "do X when Y" by voice

Date: 2026-11-19. Status: approved by the owner.

## Intent

The owner wants to tell the home things like "когда я вернусь, включи тёплые полы на 2 часа" or
"включи полотенцесушитель на час" and be sure the job runs once, at the right moment, and never "stays":
no heater left on and no job hanging around after a restart, a deploy or a power cut. There is no
burning use case yet; this is a foundation built for the future, so it must be easy to add new trigger
and action types later.

Claude (the voice assistant behind `voice-bridge`) must never get free-form automations. It fills in a
fixed form; fixed code in the repo checks the form and runs it.

## Goals

- One-shot jobs created by voice: optional arm conditions, run conditions, one or more actions, expiry.
- Leases: anything a job turns on "for N minutes" is turned off by stored, restart-proof code.
- Survives HA restarts and deploys: jobs and leases are on disk, missed turn-offs happen at startup.
- "Когда я вернусь" never fires on the current presence or on the door opening as the owner leaves.
- A push to the owner's phone every time a job runs, with the job's details.
- Timers ("поставь таймер на 10 минут") as jobs: a phone push now, a spoken announcement once a
  satellite exists.
- Voice can list and cancel jobs.
- Easy to extend: a new condition or action type is one class/function plus a prompt line.

## Non-goals

- Speaker identification. "Я" means the owner unless the wife is named.
- Recurring jobs (every day at 7). HA automations and schedules in the repo cover those.
- Blinds, locks, alarm, car, automations, scripts, input helpers as job actions.
- Native HA Assist timers. Once a Satellite1 or Voice PE exists, HA handles "поставь таймер" said to
  it locally before Claude sees it; this design only covers what reaches Claude.

## Anti-goals (a failure even if it technically works)

- A heater or light left on because a restart or crash lost a countdown.
- A job firing on stale state: the owner is home at the moment he speaks and the job counts that as
  "came back".
- Claude creating something that keeps running beyond what was asked, or touching things nobody
  asked for.
- A job silently lingering for days.

## Constraints

- HA runs in a podman container; `claude_bridge` is mounted read-only from the Nix store
  (`modules/services/voice/default.nix`). Persistent data goes to HA's `.storage` via `Store`.
- Claude reaches HA only through HA's MCP server as the non-admin user `voice-agent`, and only sees
  exposed entities and exposed scripts. It can't call integration services directly, so the job
  services are wrapped in exposed scripts, the same way as `script.food_log` (`packages/voice_food.yaml`).
- `web_guard.py home` blocks home control after web use in the same request; list is a read, add and
  cancel are control.
- The repo is public: no addresses, tokens or voiceprints in it. Person entity IDs are already in
  `automations/presence.yaml`.
- Existing heater leases: `packages/timed_heat.yaml` and `scripts/timed_heat.yaml`.
  `script.heat_lease` (device `towel_master`, `towel_guest`, `floor_master`, `floor_guest`, duration)
  starts or extends a stored timer, and `script.heat_timed_off` turns off with retries and phone pushes.
  Heater actions must go through it, not around it.
- Phone pushes go to `notify.mobile_app_pixel_7` (the owner's phone, the same as `automations/alerts.yaml`).
- Code comments in simple English, no trailing period on one-sentence comments.

## Decided with the owner

- Every job: `arm_when` (optional), `run_when` (required), `do` (one or more actions), expiry.
- Default expiry 24 h, unless the owner says another or the request clearly needs a longer or shorter one
  ("когда вернусь из отпуска" needs days, "через 10 минут" needs much less). At most 30 days.
- Leases at most 6 h.
- "Я" = the owner. Two people: the owner (`person.aleksander_cieslowski`) and his wife
  (`person.natallia_radziuk`).
- "Когда я вернусь": arm when the owner has been away for at least 10 min (if he is already away when
  the job is made, it is armed at once). Run when he is home again, plus "the front door opened within
  about 10 min" once a door sensor exists; without one, home for about 1 min.
- Phone push on every run, with the job's details.
- Timers by voice, announcements written into the prompt now even though no satellite exists yet.

## Design

### Pieces

```
Claude ── MCP ──▶ script.voice_job_add (field job: JSON text) ──▶ claude_bridge.add_job ─┐
                  script.voice_jobs                           ──▶ claude_bridge.list_jobs │  returns
                  script.voice_job_cancel (field job_id)      ──▶ claude_bridge.cancel_job┘  a response
                                                                         │
                       claude_bridge/jobs.py (pure Python: schema, checks, evaluation)
                       claude_bridge/scheduler.py (HA glue: Store, listeners, actions, pushes)
                                                                         │
                       sensor.voice_jobs (state: active count, attribute jobs: list for the dashboard)
```

- `jobs.py` has no HA imports, so it's unit-tested like `history.py`. It takes a "state lookup"
  (state, attributes, last_changed per entity) and a clock.
- `scheduler.py` loads jobs at startup, subscribes to state changes of the entities the active jobs
  use, schedules point-in-time checks, runs actions, saves, and sends pushes.
- The services are registered in `async_setup_entry` with `supports_response`. The scripts `stop` with
  the service response, so Claude sees the result or the error text.

### Job format (what Claude writes)

```json
{
  "title": "тёплые полы в большой ванной, когда вернусь",
  "arm_when": [{"type": "person", "who": "owner", "state": "away", "for_min": 10}],
  "run_when": [{"type": "person", "who": "owner", "state": "home", "for_min": 1}],
  "do": [{"type": "heat", "device": "floor_master", "minutes": 120}],
  "expires_in_min": 1440
}
```

`title` is required, in Russian; it's what the push and the list show. `arm_when` may be missing or
empty. `expires_in_min` defaults to 1440 and is at most 30 days (43200).

### Conditions

All conditions in a list must be true at the same time (AND). No OR; two jobs cover that.

| type | fields | true when |
|---|---|---|
| `person` | `who`: owner, wife, anyone, everyone; `state`: home, away; `for_min` | the person(s) are in that state, for at least `for_min` (from `last_changed`) |
| `state` | `entity_id`, `state` (string or list), `for_min` | the entity's state matches, for at least `for_min` |
| `changed` | `entity_id`, `to`, `within_min` | the entity went to `to` within the last `within_min` and is still there (door opened recently) |
| `numeric` | `entity_id`, `attribute` (optional), `below` and/or `above`, `for_min` | the number is in range, for at least `for_min` |
| `time` | `in_min` with `from`: created or armed; or `at`: local "HH:MM" (next one) or ISO date-time | that moment has passed |
| `time_window` | `from`, `to` as "HH:MM" (may cross midnight) | the local time is inside the window |
| `room_presence` | `area` | an occupancy or presence `binary_sensor` in that area is on; no such sensor yet → the job is refused with a clear error |

`unknown` and `unavailable` states never count as true. "Away" for a person means any state other than
`home`.

### Actions

| type | fields | does |
|---|---|---|
| `heat` | `device`: towel_master, towel_guest, floor_master, floor_guest; `minutes` | a lease through the timed-heat scripts. The owner asked for it, so it acts like the button, not like an automation: it starts a lease even if the heater is on by hand, and sets the lease to exactly the asked length. `script.heat_lease` gets a `force` field for this (today it leaves a heater on by hand alone and never shortens) |
| `turn_on` | `entity_id`, `minutes` (optional) | turns it on; with `minutes`, stores a lease that turns it off later |
| `turn_off` | `entity_id` | turns it off |
| `set_temperature` | `entity_id` (climate), `temperature` | sets the target temperature |
| `notify` | `message` | push to the owner's phone |
| `announce` | `message`, `area` (optional) | `assist_satellite.announce` on the satellite in that area, or on all; no satellite → refused with an error saying to use `notify` |

Allowed entities for `turn_on`, `turn_off` and `set_temperature`: exposed to Assist **and** in the
domains `light`, `switch`, `fan`, `climate`, `scene` (turn_on only), `media_player` (turn_off only).
The heater switches and climates of the four lease devices are refused there with "use heat", so heater
runs always go through the lease scripts.

Limits: 10 active jobs, 5 conditions per list, 5 actions per job, leases at most 360 min (6 h).

### Life of a job

```
add ──▶ waiting_arm ──(arm_when all true)──▶ armed ──(run_when all true)──▶ done
          │                                     │
          └───────────── expires_at passes ─────┴──▶ expired        cancel ──▶ cancelled
```

- With no `arm_when`, the job is armed at creation.
- When it's armed, `run_when` is checked at once: if everything is already true, it runs at once.
  This is why "когда вернусь" has an `arm_when`.
- `time.in_min` with `from: armed` counts from the moment of arming.
- Checks happen on every state change of a used entity and at computed moments (a `for_min` that
  will be reached, a `time`, the start of a `time_window`, the expiry). No polling.
- Run: the job is marked `done` and saved **before** the actions run, so a crash can't run it twice
  (at most once). Each action runs; a failed action doesn't stop the others. Then the push.
- `done`, `expired` and `cancelled` jobs stay in the store for 24 h for the list and the dashboard,
  then are dropped.

### Leases (generic `turn_on` with `minutes`)

- Stored next to the jobs: entity, end time, the job title.
- At the end: `turn_off`, then check after 1 min; retry every 5 min up to 1 h; push if it still isn't
  off. If the entity was turned off earlier by someone, the lease just ends.
- A later `turn_on` with minutes for the same entity extends the lease, never shortens it (the same
  rule as `heat_lease`).

### Restarts and power cuts

- Jobs and leases are saved on every change.
- On startup the scheduler waits for `homeassistant_started`, so the restored states are in place. Then:
  leases whose end passed are turned off at once; jobs past expiry become `expired` (with a push);
  the others are checked as normal.
- `last_changed` starts again at HA startup, so a `for_min` counts again after a restart. That only ever
  delays a job; it never fires one early.
- A full power cut: nothing runs while it's out. When power comes back, the heaters' own power-on
  setting applies, then HA starts and handles the missed leases as above. The heater leases also have
  `heat_timed_off`'s retries for a day.

### Pushes

- On run: title "Задание выполнено", message
  "<title>: <what was done>; выключу в 21:40" (the lease end, when there is one).
- On expiry: "Задание истекло: <title>".
- On a failed action, or a lease that didn't turn off: what failed.
- `tag` per job, so a later push for the same job replaces the earlier one.

### Voice: what Claude gets

Exposed scripts, with Russian aliases:

- `script.voice_job_add` (field `job`, the JSON above as text). Returns `{id, summary}` or `{error}`.
- `script.voice_jobs`. Returns the active jobs with id, title, step ("ждёт, пока ты уйдёшь", "ждёт,
  пока вернёшься", "ждёт условий"), expiry, and running leases with end times.
- `script.voice_job_cancel` (field `job_id`, or "all").

`web_guard.py`: add `script__voice_jobs` to `HA_READ_TOOLS`. The other two stay control tools.

A new prompt section "Jobs, timers and reminders" in `system-prompt.md`:

- When to use it: anything with "когда…", "через…", "в … часов", "на N минут/часов", "напомни",
  "поставь таймер". A plain "включи X" with no time stays a direct command, as now.
- The building blocks (the tables above, short), the people (owner = "я", wife), and the rules:
  "когда я вернусь / уйду" always gets an `arm_when` for the opposite state; expiry 24 h unless the owner
  says another or the request clearly needs longer or shorter (a trip: days, up to 30; a timer: a bit past
  its time);
  ask one short question if the time or the device is unclear; never add actions the owner didn't ask
  for.
- Heaters: always `heat`, never `turn_on` on the heater entities. "Включи полотенцесушитель на час" is a
  job with no conditions to wait for: `run_when` `time in_min 0`, `do` heat 60.
- Timers: `run_when` `time in_min N`, `do` `notify` with "Таймер N минут" (plus `announce` once a
  satellite exists; until then the service refuses `announce`, so use `notify` only).
- Reminders ("напомни …"): the same, with the owner's words in the message.
- After adding: read back in one sentence what will happen and when it expires. On an error from the
  service, fix the job and try again once; if it fails again, say what didn't work.
- "Какие у тебя задания?" → `voice_jobs`. "Отмени …" → find the id in `voice_jobs`, then cancel.
- Examples: towel heater for 1 h; floors when I'm back; floors when my wife is back; timer 10 min;
  "если вечером станет холоднее 20 в спальне, включи полы в большой ванной на 2 часа" (numeric plus
  time_window); "напомни через час выключить духовку"; "когда вернёмся оба".

### Dashboard

A card on the Schedule tab built from `sensor.voice_jobs`: active jobs with step and expiry, running
leases with end times. Cancel buttons call `script.voice_job_cancel`.

### Tests

- `claude_bridge/test_jobs.py`, run by `tests/voice-bridge-unit.nix`. That check doesn't run the existing
  `claude_bridge` tests (`test_const.py`, `test_history.py`) today; add them too. The tests must not import
  `homeassistant`:
  schema checks (unknown type, missing field, limits, refused entity), each condition with a fake state
  lookup and clock, the state machine (arm on away ≥10 min, no firing on current presence, run at once
  when already true, expiry), next-check time computation, the lease extend rule.
- Live checks on the server after deploy: add a timer job through the script, see the push; a
  `turn_on` with a 2 min lease on a light; restart HA during a lease and see it turned off at startup;
  "когда я вернусь" walked through by setting the person state in a test, not by leaving the flat.

## Left to the builder

- The exact Python structure of `jobs.py` (classes or functions per condition type) and the response
  texts, as long as new types stay one small addition each.
- How to find the "satellite in an area" and the "presence sensor in an area" (the entity and area
  registries).
- Russian aliases of the scripts and the wording of the pushes.
- The dashboard card type, following the existing dashboard style (`ha-percent-bar-card` and the
  custom element rules in memory don't apply; it's a list).

## Calls I made (not discussed with the owner)

- The engine lives inside `claude_bridge`, with jobs in HA's `.storage`, not in `voice-bridge`.
- The 10 min "away" arm, 1 min "home" debounce, 10 min door window (from the conversation, as defaults).
- Conditions are AND only.
- At most once: a job is marked done before it runs.
- Allowed domains and the job limits (10 active jobs, 5 conditions, 5 actions).
- Pushes also on expiry and on failure, not only on run.
- A voice `heat` request acts like the button (`force`): starts a lease on a heater turned on by hand and
  sets the exact length, even if that shortens a running lease.
- Finished jobs kept 24 h for the list.
- A dashboard card on the Schedule tab.
