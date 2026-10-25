# Voice assistant: design

Date: 2026-10-25

## Intent

The owner wants an Alexa-like voice assistant at home that is not stupid and speaks Russian and English (Polish
maybe later). Typical moments: "close the blinds in the living room", "open the left blind" (to get to the
balcony), "play <something> on Spotify", random questions. Later it may track calories (a separate future
project) and watch the homelab.

The brain is Claude, through the owner's **Claude subscription** (not pay-per-token API), run headless with
Claude Code on frogswamp. Everything else stays local: wake word, speech-to-text, simple home commands and
text-to-speech run in the house. Claude only gets the **text** of what was said after the wake word, never audio
and never anything said without the wake word.

The backend must be easy to switch to the Anthropic API later, if the subscription turns out too slow or its
usage limits get in the way.

## Phases

1. **Server + phone (this design, build first).** Pixel 7 with the HA Companion app, HA Assist set as the
   default Android assistant (long-press power), tap-to-talk. Tests the whole chain except wake word and room
   speaker.
2. **Voice PE in the living room (this design).** One Home Assistant Voice Preview Edition (owner chose it),
   music from Spotify on it.
3. **Two more rooms (non-goal now).** Decided after living with phase 2.

## Goals

- Blinds and other simple home commands work fast (~1 s), without Claude and without internet
- Free-form requests ("what is…", "play …", follow-ups like "and the right one too") go to Claude and get a
  short spoken answer
- Russian and English, Russian first
- Spotify music played on the Voice PE (phase 2)
- Switching the brain from subscription to API is a settings change, not a rebuild
- Claude side can grow later: skills (calorie tracking), more MCP tools (homelab, read-only first)

## Non-goals

- Polish (only two wake words per Voice PE, see Languages)
- Rooms 2 and 3
- Calorie tracking (own project, plugs in later as a skill or MCP tool)
- Homelab access for Claude beyond HA entities
- Local LLM / GPU box (discussed, postponed)

## Anti-goals (fails even if it technically works, if…)

- It listens or sends anything without the wake word (or the phone tap)
- It creates, edits or deletes automations, scripts, scenes, helpers or any HA/repo config. The owner makes
  these from the PC
- It changes device states or runs anything the owner did not ask for in that request (no background actions,
  no scheduled jobs, no "helpful" extras)
- It breaks something in the homelab
- The subscription token leaks or can be used by something other than the bridge

These must be enforced by **technical limits** (users, tokens, permissions), not only by prompt text.

## Current state (from the repo)

- HA runs as the official image in Podman (`modules/services/home-assistant`), host networking. No Supervisor,
  so **no HA add-ons**: anything extra (Whisper, Piper, Music Assistant) runs as a NixOS service or its own
  container
- Custom integrations are mounted read-only from the repo/pins (pattern: `cubematrix.nix`). HACS is available
- Secrets are in sops (`hosts/frogswamp/secrets.nix`)
- Blinds are not in repo config; their entity IDs must be looked up in the live HA (builder, read-only)
- Phone is `mobile_app_pixel_7`

## Architecture

```
Pixel 7 (tap)  ─┐
Voice PE (wake  ├─► HA Assist pipeline (ru or en)
 word on device)┘     │
                      ├─ STT: wyoming-faster-whisper (host, local)
                      ├─ "Prefer handling commands locally" ── HA built-in intents ──► blinds, lights…
                      │        (no match)
                      ├─ Conversation agent: "Claude bridge" (custom integration)
                      │        └─► bridge service (host, user voice-agent)
                      │                 └─► claude -p … ──► Anthropic (text only)
                      │                        └─ MCP: HA MCP Server (exposed entities only)
                      └─ TTS: Piper (host, local), ru / en voice
Music: Music Assistant (container) ── Spotify ──► Voice PE speaker / 3.5 mm out
```

### Voice front-end (local)

- **Wake word:** on the Voice PE itself (microWakeWord). No server-side wake word service. Phone: tap-to-talk
  only. The Voice PE hardware mute switch is the "stop listening" control
- **STT:** `services.wyoming.faster-whisper` on the host, a **multilingual** model (not `*.en`), languages ru
  and en. Model size is the builder's call: pick the biggest one that keeps Russian recognition under ~2 s for a
  short phrase on the N100, measure it
- **TTS:** `services.wyoming.piper`, one Russian and one English voice. Voice choice is the builder's call,
  owner can change later
- HA connects to both through the Wyoming integration on localhost (host networking)

### Pipelines and languages

- Two Assist pipelines: **Russian** and **English**. Each: its STT language, conversation agent = Claude bridge
  with "Prefer handling commands locally" on, its Piper voice
- Phone: Russian pipeline is the default, English picked in the app when needed
- Voice PE: two wake words, each tied to a pipeline. **My call:** "Okay Nabu" → Russian, "Hey Jarvis" → English
  (owner can swap)
- Local intents: HA built-in sentences in both languages. Builder checks that "open the left blind" /
  "закрой шторы в гостиной" style phrases work with the real entity names and aliases. Adding entity aliases
  (in Russian too) is fine; adding custom sentences is fine if they live in the repo config

### Claude bridge

Two parts.

**1. HA custom integration `claude_bridge`** (conversation agent). Small, in its own folder in this repo,
mounted read-only like cubematrix. On each request it POSTs `{text, language, conversation_id}` to the bridge
service on localhost and returns the answer as the agent's speech. It has no tools of its own: HA control goes
through MCP on the Claude side.

**2. Bridge service** (host, systemd, user `voice-agent`).

- Receives the request, runs Claude Code headless (`claude -p`) with the text, returns plain text
- **Follow-ups:** maps HA `conversation_id` → Claude Code session and resumes it, so "and the right one too"
  works. Sessions older than ~5 min idle are dropped. Follow-ups are guaranteed only inside Claude-handled
  chains (my call). If HA gives the integration the earlier local turns of the same conversation (chat log),
  pass them along so "open the left blind" → "and the right one too" works; if not, Claude asks back. That is
  accepted
- **Model:** configurable. Default Haiku for speed (my call), Sonnet for harder asks if needed later
- **Speed target:** a short question answered and starting to speak in **≤ 8 s** on the phone. This is a
  goal, not a hard gate (my call). The plan starts with an **early spike** that measures `claude -p` + HA MCP
  on the N100. If plain `claude -p` per request is too slow, the builder may keep a warm Claude Code process
  (stream-json input). If even that misses 8 s by a lot, stop and show the owner the numbers before building
  the rest (the API switch is the fallback). Note the numbers in the README
- **System prompt (appended):** answer briefly in plain spoken sentences (no markdown, lists or URLs), in the
  request language; only do what this request asks; if the request is unclear about which device, ask back
  instead of guessing; never claim an action happened unless the tool call succeeded; for music, turn
  transliterated names back into their original spelling before searching
- **Failure:** if Claude fails (no internet, quota hit, timeout ~20 s) the bridge answers a short sentence in
  the request language, e.g. "Claude is not available right now". Local commands keep working
- **Logging:** request metadata (time, language, duration, success) only. Request and answer text are **not**
  logged by default; a debug flag can turn it on (my call)

**Claude Code setup for `voice-agent`:**

- `claude-code` from nixpkgs, own `CLAUDE_CONFIG_DIR` under the user's state dir, separate from the owner's
  dev setup
- **Auth:** the owner runs `claude setup-token` once on their PC and puts the token into sops; the service
  gets it as `CLAUDE_CODE_OAUTH_TOKEN`. Only `voice-agent` can read it
- **MCP:** HA's built-in **MCP Server** integration (`/api/mcp` on localhost) with a long-lived token of a
  **non-admin HA user** `voice-agent`. It exposes only the Assist API: read and control entities that are
  exposed to Assist. It cannot edit automations, scripts, scenes or config
- **Permissions** (Claude Code settings, deny wins): allow the HA MCP tools and WebSearch (random questions);
  deny Bash, Edit, Write, NotebookEdit, WebFetch; Read limited to its own working dir (skills live there)
- **Skills:** a `skills/` folder in its working dir, deployed from the repo. Empty at start; calorie tracking
  plugs in here later
- No cron, timers, `/loop`, scheduled routines or background agents. Claude runs only inside a bridge request

### Exposed entities (phase 1)

Exposed to Assist: blinds/curtains, lights, media players (and the Music Assistant players). **Not exposed**
(my call, owner decides later): anything that heats (towel heater relay, floor warming), locks, the mode
switches, the desk, anything that costs money. Exposure is set in the HA UI (lives in `.storage`), so the README
lists the intended set.

### Music (phase 2)

- **Music Assistant** server as its own Podman container (no add-ons here), plus the HA Music Assistant
  integration
- Spotify provider, owner's account (owner has Spotify Premium)
- Voice PE is a Music Assistant player; music plays on its speaker, or on any speaker plugged into its 3.5 mm
  jack
- Voice: "play X" goes to Claude, which starts it through an exposed HA script (e.g. `script.play_music` with
  `query` and `player` fields, in repo config) or Music Assistant's own Assist intent if it shows up in the MCP
  tools. Builder picks whichever works
- In phase 1 music is tested on any existing media player, or left for phase 2

## Switching to the API later

No bridge change needed: create HA's built-in **Anthropic** integration with an API key and pick it as the
conversation agent in the two pipelines. Local intents, STT, TTS and exposure stay the same. Skills would not
carry over (they are Claude Code only); that is accepted. The README describes the switch in a few steps.

## Security summary

| Rule | Enforced by |
|---|---|
| No listening without wake word | wake word on device, phone tap-to-talk, hardware mute |
| Only text leaves the house | STT local; bridge sends text only |
| No automation/config edits | non-admin HA user + MCP Assist API only; no Bash/Edit/Write; `voice-agent` has no access to HA state dir, repo, git, sudo |
| No unrequested actions | Claude only runs inside a bridge request; no schedulers; prompt rule |
| Small blast radius | only chosen entities exposed |
| Token safety | sops, readable only by `voice-agent`, not logged |

## Manual steps for the owner

1. `claude setup-token` on the PC, put the token into sops
2. In HA: create user `voice-agent` (non-admin), make its long-lived token, put it into sops; set entity exposure
3. Phone: HA Companion app → set HA Assist as default assistant; pick the Russian pipeline
4. Phase 2: buy Voice PE, adopt it in HA, choose wake words; log Spotify into Music Assistant

## Working on the live server

The builder may deploy to frogswamp and change live HA (pipelines, aliases, integrations, measurements), but
**asks the owner for an explicit yes before each deploy or live change**. Steps that need the owner's accounts
or hands stay in the manual list above. Entity aliases are a documented list in the README and are added live
after approval.

## Answered questions

- **Spotify Premium:** yes, the owner has it
- **Mixed languages:** usually not, but English artist/track names inside a Russian sentence are expected for
  music. STT stays locked per pipeline with a small/medium-size model (a bigger model costs ~5 s on every
  request, local commands included). Claude turns transliterated names back into the original spelling before
  searching ("металлика" → "Metallica"); this is a rule in the system prompt. The early spike tests a few real
  mixed phrases; if music search fails too often, go one Whisper size up and re-measure

## Left to the builder

- Whisper model size, Piper voices, ports
- Language of the bridge service (Python fits the repo) and the HTTP shape between the integration and the
  service
- Plain `claude -p` vs warm process, as long as the speed target holds
- How to test it: unit tests for the bridge and integration, a `just` recipe to send a text request to the
  bridge, a `tests/` check if it fits
- Docs: a README for the voice module, links from the main README
