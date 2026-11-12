# Voice assistant

Talk to the home in Russian or English. Speech-to-text and text-to-speech run on the server. Simple home commands are handled by Home Assistant itself in about a second. Everything else (questions, chat, games, music, anything HA doesn't know) goes as **text** to Claude, which runs headless on the Claude subscription.

| Part | Where | What it does |
|---|---|---|
| Whisper (STT) | `127.0.0.1:10300` ru, `:10301` en | Speech → text. `auto` model: GigaAM for Russian, Parakeet for English |
| Silero (TTS, Russian) | `127.0.0.1:10202`, `wyoming-silero` | Text → speech for the Russian pipeline, voice aidar. Model `v5_6_ru` (CC BY-NC, home use only) |
| Piper (TTS) | `127.0.0.1:10200` ru, `:10201` en | Text → speech for English (alan); Russian ruslan is the fallback |
| Local phrases | `home-assistant/config/custom_sentences/{ru,en}/voice.yaml` | Blinds, curtains, heating, room temperature without Claude |
| `claude_bridge` | HA custom integration (conversation entity *Claude*) | Hands the text and the turns HA handled itself to the bridge |
| `voice-bridge` | Unix socket `/run/voice-bridge/bridge.sock`, systemd, `DynamicUser` | Runs `claude -p` with HA's MCP server, keeps the session |
| Music Assistant | http://192.168.101.10:8095 | Spotify (librespot) on `media_player.tv_music` |

```
phone / Voice PE ─ wake word ─▶ HA pipeline ─▶ Whisper ─▶ local phrase? ── yes ─▶ HA does it
                                                              │ no
                                                              ▼
                                   claude_bridge ─▶ voice-bridge ─▶ claude -p ─▶ HA MCP (exposed entities only)
                                                                           └──▶ web search / pages from results
                                   answer text ─▶ Silero (ru) / Piper (en) ─▶ speaker
```

## What leaves the house

- Only the **text** of a request after the wake word, and only when HA can't handle it locally. No audio, nothing without the wake word.
- Claude's web searches, and pages it opens from its own search results.
- Nothing about the home is meant to go to the web, see the rules below. The one exception is the home location, which Claude may put in searches that need a place (weather, routes, shops nearby).

## Safety rules and how they are kept

| Rule | How |
|---|---|
| Only exposed entities | HA MCP uses the non-admin, local-only user `voice-agent`; Assist sees exposed entities only, "expose new entities" is off |
| No automations, scripts or scenes edited | No such tools in the Assist API; the prompt says it can't |
| No device changes the owner didn't ask for | Prompt: only the newest request, never repeat earlier actions |
| Read only: Zigbee bridge, car, qBittorrent, phone | Only sensors of these are exposed (Zigbee bridge: connection state and version). Integrations can add tools to the Assist API past exposure (VW Group Connect adds Škoda ones), so the hook `web_guard.py home` allows HA tools of known integrations only (`HA_INTEGRATIONS`); add a new one there on purpose |
| Never "Tuya mode" / "Home Assistant mode" | Not exposed, and named in the prompt |
| No home data or credentials on the web | `WebFetch` only for URLs from the session's own `WebSearch` results (hook `web_guard.py fetch`). No http/ftp/rtsp links in any HA tool call, so Music Assistant can't be made to fetch one (`play_music` checks it too). The prompt forbids home or owner data in search text. Both hooks block on any error |
| Web pages can't steer the home | After Claude read the web in a request, HA control tools are blocked until the owner's next request; reading the home stays allowed |
| Tokens stay secret | sops → `LoadCredential`, passed to Claude by env only; no token in the Nix store, a command line or the log. HA's state and `/run/secrets` are hidden from the service |
| Nothing else on the server can ask Claude | The bridge has no TCP port: its socket folder is `0700`, only the service and root (HA's container mounts it) can open it |
| No local tools | `Bash`, `Edit`, `Write`, `NotebookEdit`, `Read`, `Glob`, `Grep`, `Task`, `Agent` are denied; the unit has the usual systemd hardening (`ProtectProc`, no capabilities, private devices) |
| HA gets an answer in time | The bridge's 25 s budget includes waiting for a busy session; a failed or timed-out request is never run again, only a lost session is retried |

The journal has metadata only (language, ok, time). `frogswamp.voice.debugLog = true` also logs the text.

## Exposure and aliases

Exposed: all devices and scenes, except "Tuya mode", "Home Assistant mode", automations, schedules, the desk "(copy)" scenes and service entities; read-only sensors as above. 174 entities on 2026-11-01.

Every exposed entity has Russian aliases, often also English ones (*Settings → Voice assistants → Expose*, or the entity's *Voice assistants* tab). Areas have Russian aliases too (Гостиная, Спальня, Прихожая, …).

- In HA 2026.10 an alias list must keep its empty (`null`) entry: it stands for the entity's own name. Setting aliases through the API without it makes the name stop matching.
- The TV music player and `script.play_music` have no "Музыка" / "включить музыку" alias on purpose: "включи музыку" should go to Claude, which picks something to play, not turn on an empty player.
- HA's system language is English (entity names come from it); the Russian pipeline has its own language.

## Local phrases

| Russian | English | Does |
|---|---|---|
| открой / подними / сбрось жалюзи | open / raise / reset the blinds | script *Blinds: full close + open* |
| закрой / опусти жалюзи [полностью] | close / lower the blinds [all the way] | script *Blinds: full close* |
| открой дверь / проход на балкон | open the balcony door | script *Blinds: open door* |
| закрой дверь / проход на балкон | close the balcony door | script *Blinds: close door, slats open* |
| открой / закрой шторы | open / close the curtains | scenes *Curtains - open / close* |
| включи / выключи тёплый пол в гостевом санузле … | turn on / off the floor heating in the guest bathroom … | the guest or master thermostat |
| включи / выключи полотенцесушитель в … | turn on / off the towel heater in the … | guest towel heat or master relay |
| какая температура в спальне / прихожей | what's the temperature in the bedroom / hall | answers from `sensor.bedroom_temperature` / `sensor.hall_temperature` |

One named blind ("открой левые жалюзи", "open the left blind") stays with HA's built-in phrases. The actions are in `config/packages/voice_intents.yaml`; each phrase file sets a `lang` slot so the reply comes in the same language. `nix build .#checks.x86_64-linux.test-ha-custom-sentences` checks every phrase and reply.

## Silero

- Livelier Russian than Piper: automatic stress, questions and emphasis. First audio after about 0.6 s, a 13 s answer is ready in 1.3 s on the N100, about 600 MB of memory.
- Silero reads only Cyrillic words. `silero/ru_text.py` writes numbers, decimals, times, units (°C, %, km, zł, kWh) and Latin words (Polish street names) out in Russian first, drops markdown, links and emoji, and splits the text into sentences, which are spoken one by one.
- The model file is a torch package with Silero's own Python code, pinned by hash. The service has no network except localhost, no writable paths except its cache, no access to `/var/lib` or secrets, and a 3 GB memory limit.
- Voices: aidar, eugene (male), baya, kseniya, xenia (female). The pipeline sets the voice; `frogswamp.voice.silero.voice` is the default. Back to Piper: *Settings → Voice assistants → Russian → Text-to-speech*.

## Claude's behaviour

- Tone: casual everyday language; it may swear now and then, more if you do. It never insults you first, but may answer an insult in kind.
- Home location: the bridge reads HA's `/api/config` (home name, country, coordinates rounded to ~100 m, time zone) with the `voice-agent` token, every hour, and adds it to the end of the system prompt. Change it in *Settings → System → General*. The street address is the UI template helper `sensor.home_address` (exposed, aliases "Домашний адрес", "Адрес дома"); the bridge reads it too. Nothing of it is in the repo, which is public.
- Maintenance and energy: two read-only scripts in `home-assistant/config/packages/voice_info.yaml`. *Maintenance status* gives km and days left for car parts and the fridge filter; *Energy history* gives cost or kWh per device per day, week, month or year. `recorder.get_statistics` is admin-only and Assist runs as `voice-agent`, so the trigger sensor `sensor.energy_history_data` reads the statistics every hour without a user and the script reads its attribute (the recorder skips it). The web guard counts both scripts as reads.

- System prompt: `system-prompt.md` (home rules, scenes first, room temperatures, secrets, memory, web).
- **Memory:** one session for the whole home, forgotten after 6 hours of silence or when `voice-bridge` restarts. Transcripts are kept by Claude Code for 30 days in `/var/lib/private/voice-agent/claude/projects/`.
- Model: `frogswamp.voice.model` (default `haiku`). Bridge timeout 25 s, HA waits 30 s.

## Numbers (2026-11-01, N100)

| What | Time |
|---|---|
| STT after the phrase ends, Russian 3–4 s phrase | 1.5–2.3 s |
| STT, English 3 s phrase | 0.8 s |
| TTS, one sentence | 0.5–2 s |
| Local phrase (intent stage) | < 0.1 s |
| Free-form question via Claude | 2–3 s |
| Home question via Claude (MCP tools) | 4–12 s |
| Follow-up after a local turn ("а в прихожей?") | 9 s |
| Web search answer | 11–18 s |

## Using it

- Phone: HA Companion app as the default digital assistant, *Russian* pipeline preferred (*English* pipeline for English).
- From the PC: `just voice-ask "Сколько будет 2+2?"` or `just voice-ask "What time is it in Tokyo?" en` (the same request HA sends; Claude only, no local phrases).
- Logs: `journalctl -u voice-bridge`, HA *Settings → Voice assistants → pipeline → Debug*.

## One-time setup

1. `claude setup-token` on the PC → sops `voice/claude_oauth_token`.
2. HA user `voice-agent`: not an admin, local access only; its long-lived token → sops `voice/ha_token`. Then `just deploy`.
3. HA integrations: *Model Context Protocol Server* (Assist API; "Require an administrator account" off), Wyoming ×4 on `localhost` with the ports above (renamed per language), *Claude bridge* (default URL `unix:/run/voice-bridge/bridge.sock`; older entries with `http://127.0.0.1:8099` are switched to it automatically).
4. Pipelines *Russian* (preferred) and *English*: STT Whisper ru/en, agent *Claude* with "Prefer handling commands locally", TTS Silero aidar (Russian, Wyoming `127.0.0.1:10202`, entry *Silero ru*) and Piper alan (English).
5. Music Assistant: add Spotify (librespot engine), then HA's Music Assistant integration.

## Switching to the API or another model

- Another Claude model: set `frogswamp.voice.model` (e.g. `sonnet`: better at multi-step tool use, a few seconds slower).
- Another backend: `bridge.py` has a backend layer (`BACKENDS`, `ask(prompt, system_prompt, session_id, timeout)`). Add a class and set `backend` in the bridge config.
- Or skip the bridge: add HA's Anthropic integration with an API key and pick it as the agent in both pipelines. The web guard, memory and skills of this setup don't carry over.

## Not done yet

- Voice PE in the living room: wake words "Железяка" (Russian pipeline) and "Claptrap" (English) need custom microWakeWord models and ESPHome firmware; until then "Okay Nabu" / "Hey Jarvis".
- Not tested: local phrases with the server's internet cut.
