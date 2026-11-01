# Voice Assistant Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Local voice pipeline (Whisper + Piper on frogswamp, HA Assist with local intents first) whose free-form requests go as text to Claude Code running headless on the owner's subscription, with HA control through HA's MCP Server, then Music Assistant + Voice PE.

**Architecture:** A NixOS module `frogswamp.voice` runs two Wyoming Whisper and two Wyoming Piper servers (ru, en) on localhost, and a small stdlib-Python bridge service (`DynamicUser`, sandboxed) that runs `claude -p` per request and keeps Claude sessions per HA conversation. A thin HA custom integration `claude_bridge` (conversation agent) is mounted read-only into the HA container and POSTs text to the bridge. Music Assistant runs as its own Podman container. Live HA setup (pipelines, exposure, aliases, entries) is done with the owner.

**Tech Stack:** NixOS 26.05 (`services.wyoming.*`, `virtualisation.oci-containers`), Python 3 stdlib, `claude-code` from nixpkgs (unfree), Home Assistant 2026.10.1 container, Music Assistant server container, pytest in `runCommand` checks, NixOS VM tests.

**Spec:** `docs/superpowers/specs/2026-10-25-voice-assistant-design.md`

## Global Constraints

- Phase 1 = server + Pixel 7 tap-to-talk; phase 2 = one Voice PE in the living room + music. Rooms 2–3, Polish, calorie tracking, homelab tools: out of scope
- Languages: `ru` (default) and `en`. Wake words (phase 2): "Okay Nabu" → Russian pipeline, "Hey Jarvis" → English pipeline
- Claude model default `haiku`; request timeout **20 s** in the bridge, **30 s** in the HA integration → bridge call
- Speed goal: short question starts speaking in **≤ 8 s** on the phone (goal, not gate; Task 1 decides)
- Follow-up sessions dropped after **300 s** idle
- Error speech when Claude fails: ru `"Claude сейчас недоступен"`, en `"Claude is not available right now"`
- Request/answer **text is never logged** unless `frogswamp.voice.debugLog = true`; tokens never in argv, logs or the Nix store
- Claude gets HA only through HA's MCP Server (`http://127.0.0.1:8123/api/mcp`) with the token of the **non-admin** HA user `voice-agent`. No Bash/Edit/Write/NotebookEdit/WebFetch; allowed: `mcp__homeassistant`, `WebSearch`. No timers, cron or background runs
- Ports (localhost only unless said): whisper ru `10300`, whisper en `10301`, piper ru `10200`, piper en `10201`, bridge `8099`; Music Assistant UI `8095` and stream `8097` (LAN, firewall open)
- Secrets (sops, `secrets/frogswamp.yaml`): `voice/claude_oauth_token`, `voice/ha_token`. The owner edits secrets with `just secrets`
- Comments: only for important or tricky parts, code should explain itself. Simple English (C1 level), no trailing period on single-sentence comments
- **Commits:** only after the owner says yes to that commit; `git add` each file by name, never `git add .`/`-A`; no Co-Authored-By; message ends with the session's `Claude-Session:` line
- **Server:** no deploy, no live HA change, no server command without the owner's yes at that moment. Steps marked **[owner]** run in the coordinating session with the owner, never in a subagent

## Review Focus

1. Claude hangs, no internet or quota hit: the `claude` process is killed at 20 s and the error sentence is spoken in the request language. Test: Task 2 `test_timeout_kills_and_speaks_error`
2. Phone and Voice PE talk at the same time, or one conversation sends twice quickly: no crash, same-conversation requests run one after another. Test: Task 2 `test_same_conversation_serialized`
3. Claude answers with markdown, URLs or a long text: spoken text is cleaned and cut at 600 chars on a sentence end. Test: Task 2 `test_speech_sanitized`
4. Resume of an expired/unknown Claude session fails: the bridge retries once as a fresh session. Test: Task 2 `test_resume_failure_starts_fresh`
5. HA sends `ru-RU`, `pl` or an empty language: normalized to `ru`/`en`, errors fall back to `en`. Test: Task 2 `test_language_normalized`

---

### Task 1: [owner] Speed spike on frogswamp

Decides whether the subscription path is worth building. Nothing is committed.

**Files:** none (numbers go into Task 5's README later)

- [ ] **Step 1: Owner prepares access.** In HA UI: create user `voice-agent` (not admin), log in as it, create a long-lived token. Add the **Model Context Protocol Server** integration with LLM API "Assist". On the PC: `claude setup-token`. Put both into sops with `just secrets` as `voice/ha_token` and `voice/claude_oauth_token`

- [ ] **Step 2: Owner says yes, then run on the server as the admin user**

```bash
NIXPKGS_ALLOW_UNFREE=1 nix shell --impure nixpkgs#claude-code
export CLAUDE_CONFIG_DIR="$(mktemp -d)" CLAUDE_CODE_OAUTH_TOKEN=... HA_TOKEN=...
cat > /tmp/mcp.json <<'EOF'
{"mcpServers":{"homeassistant":{"type":"http","url":"http://127.0.0.1:8123/api/mcp","headers":{"Authorization":"Bearer ${HA_TOKEN}"}}}}
EOF
claude --version; claude --help | grep -E -- '--(output-format|resume|model|append-system-prompt|mcp-config|strict-mcp-config|settings)'
for q in "Сколько будет 17 умножить на 23?" "Открыты ли жалюзи в гостиной?"; do
  for i in 1 2 3 4 5; do /usr/bin/time -f %e claude -p --output-format json --model haiku \
    --mcp-config /tmp/mcp.json --strict-mcp-config <<<"$q" >/dev/null; done; done
```

Expected: all seven flags listed; JSON has `result` and `session_id`; the blinds question used an `mcp__homeassistant__*` tool. Delete `/tmp/mcp.json` and the temp config dir after.

- [ ] **Step 3: Decide with the owner.** Median Claude time ≤ ~5 s (leaves ~3 s for STT + TTS): continue with Task 2 as written. 5–8 s: continue, Task 2 keeps the per-request design and the README says a warm process is the next step. Clearly > 8 s: stop and show the owner the numbers (the API switch from the spec is the fallback)

---

### Task 2: Bridge service core

**Files:**
- Create: `modules/services/voice/bridge.py`
- Create: `modules/services/voice/test_bridge.py`
- Create: `tests/voice-bridge-unit.nix`
- Modify: `tests/default.nix` (add `test-voice-bridge-unit`)

**Interfaces:**
- Produces (HTTP, used by Task 4): `POST /converse` body `{"text": str, "language": str, "conversation_id": str | null, "earlier": [{"role": "user" | "assistant", "text": str}]}` → `200 {"speech": str, "ok": bool}`. Bad body → `400`. Any other path → `404`
- Produces (CLI, used by Task 3): `python3 bridge.py --config <json>` with config keys `listen` (`"127.0.0.1:8099"`), `claude` (path to binary), `model`, `timeout` (20), `session_ttl` (300), `workdir`, `config_dir`, `mcp_config`, `settings`, `system_prompt_file`, `credentials_dir` (files `claude_token`, `ha_token`), `debug_log` (bool)
- Pure functions (tested directly): `normalize_language(lang: str | None) -> str`, `build_prompt(text: str, language: str, earlier: list[dict]) -> str`, `build_claude_args(cfg: dict, session_id: str | None) -> list[str]`, `sanitize_speech(text: str, limit: int = 600) -> str`, `error_speech(language: str) -> str`, class `SessionStore(ttl: float, clock=time.monotonic)` with `get(conv_id) -> str | None`, `put(conv_id, session_id)`, `lock(conv_id) -> threading.Lock`, `converse(cfg: dict, store: SessionStore, req: dict, run=subprocess.run) -> dict`

- [ ] **Step 1: Write the failing tests in `test_bridge.py`**

```python
def test_language_normalized():
    assert normalize_language("ru-RU") == "ru"
    assert normalize_language("en") == "en"
    assert normalize_language("pl") == "en"
    assert normalize_language(None) == "en"
    assert error_speech("pl") == "Claude is not available right now"
    assert error_speech("ru") == "Claude сейчас недоступен"

def test_prompt_has_language_and_earlier_turns():
    p = build_prompt("и правую тоже", "ru", [
        {"role": "user", "text": "открой левую штору"},
        {"role": "assistant", "text": "Открыла"}])
    assert "[language: ru]" in p and "User: открой левую штору" in p and "Home: Открыла" in p
    assert p.rstrip().endswith("и правую тоже")

def test_args_resume_and_no_secrets():
    cfg = sample_cfg()
    args = build_claude_args(cfg, None)
    assert args[:2] == [cfg["claude"], "-p"] and "--resume" not in args
    assert ["--output-format", "json"] == args[args.index("--output-format"):][:2]
    assert "--strict-mcp-config" in args and "haiku" in args
    assert build_claude_args(cfg, "s1")[-2:] == ["--resume", "s1"]
    assert not any("token" in a.lower() for a in args)  # text goes on stdin, tokens in env

def test_speech_sanitized():
    s = sanitize_speech("**Готово.** См. https://x.y/z\n- пункт " + "Очень. " * 200)
    assert "*" not in s and "http" not in s and "\n-" not in s
    assert len(s) <= 600 and s.endswith(".")

def test_session_store_ttl():
    now = [0.0]; st = SessionStore(300, clock=lambda: now[0])
    st.put("c1", "s1"); now[0] = 299; assert st.get("c1") == "s1"
    now[0] = 600; assert st.get("c1") is None

def test_converse_ok_and_resume(fake_run):  # fake_run returns {"result","session_id","is_error":false}
    st = SessionStore(300)
    r = converse(sample_cfg(), st, {"text": "hi", "language": "en", "conversation_id": "c1", "earlier": []}, run=fake_run)
    assert r == {"speech": "Hello.", "ok": True}
    converse(sample_cfg(), st, {"text": "again", "language": "en", "conversation_id": "c1", "earlier": []}, run=fake_run)
    assert fake_run.calls[-1].args[-2:] == ["--resume", "sess-1"]
    assert fake_run.calls[-1].stdin.endswith("again")
    assert fake_run.calls[-1].env["CLAUDE_CODE_OAUTH_TOKEN"] == "claude-secret"
    assert fake_run.calls[-1].env["HA_TOKEN"] == "ha-secret"

def test_resume_failure_starts_fresh(fake_run_resume_fails):
    ...  # first call with --resume returns is_error, second call has no --resume; answer is spoken, ok True

def test_timeout_kills_and_speaks_error(fake_run_timeout):  # raises subprocess.TimeoutExpired
    r = converse(sample_cfg(), SessionStore(300), {"text": "x", "language": "ru-RU", "conversation_id": None, "earlier": []}, run=fake_run_timeout)
    assert r == {"speech": "Claude сейчас недоступен", "ok": False}
    assert fake_run_timeout.calls[0].timeout == 20

def test_same_conversation_serialized(slow_fake_run):  # sleeps 0.2 s, records overlap
    ...  # two threads, same conversation_id → no overlap; different ids → overlap allowed

def test_http_endpoint(server):  # real ThreadingHTTPServer on port 0 with a fake run
    ...  # POST /converse → 200 JSON; bad JSON → 400; GET /x → 404

def test_no_text_in_logs(server, caplog):
    ...  # with debug_log false, neither request text nor speech appear in log output
```

`sample_cfg()` uses a `tmp_path` credentials dir with `claude_token` = `claude-secret`, `ha_token` = `ha-secret`. Fixtures live in the same file.

- [ ] **Step 2: Run, verify they fail**

Run: `cd modules/services/voice && python3 -m pytest -q test_bridge.py`
Expected: FAIL, `ModuleNotFoundError: No module named 'bridge'`

- [ ] **Step 3: Implement `bridge.py`** (stdlib only, like `modules/services/mode-switch/app.py`)
- `build_claude_args`: `[claude, "-p", "--output-format", "json", "--model", model, "--append-system-prompt", <file content>, "--mcp-config", mcp_config, "--strict-mcp-config", "--settings", settings]` + `["--resume", sid]` last when set
- `converse`: lock per conversation; run with `input=prompt`, `cwd=workdir`, `timeout=cfg["timeout"]`, env = `{HOME: state dir, CLAUDE_CONFIG_DIR, PATH, CLAUDE_CODE_OAUTH_TOKEN, HA_TOKEN, DISABLE_AUTOUPDATER: "1", CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC: "1"}` with tokens read from `credentials_dir` per request; parse stdout JSON; on `is_error` with a resume → one fresh retry; on any failure → `error_speech`, `ok: False`, log the kind of failure only
- `sanitize_speech`: drop URLs and markdown marks (`*_#`` ` ``>`, list dashes), join lines with spaces, cut at the last sentence end before `limit`
- Log one line per request: language, duration, ok. Text only when `debug_log`

- [ ] **Step 4: Run, verify they pass**

Run: `cd modules/services/voice && python3 -m pytest -q test_bridge.py`
Expected: all pass

- [ ] **Step 5: Add `tests/voice-bridge-unit.nix`** (copy the shape of `tests/football-unit.nix`, files `bridge.py` + `test_bridge.py`) and register it in `tests/default.nix`. Run: `nix build .#checks.x86_64-linux.test-voice-bridge-unit -L` → builds

- [ ] **Step 6: Commit** (after the owner's yes): `modules/services/voice/bridge.py`, `modules/services/voice/test_bridge.py`, `tests/voice-bridge-unit.nix`, `tests/default.nix` — `feat(voice): bridge from HA text to headless Claude Code`

---

### Task 3: NixOS module `frogswamp.voice`

**Files:**
- Create: `modules/services/voice/default.nix`
- Create: `modules/services/voice/system-prompt.md`
- Create: `modules/services/voice/skills/.gitkeep`
- Create: `tests/voice.nix`
- Modify: `modules/default.nix` (import `./services/voice`), `tests/default.nix` (`test-voice`), `hosts/frogswamp/secrets.nix` (secrets + defaults), `hosts/frogswamp/default.nix` (`voice.enable = true`), `justfile` (`voice-ask`)

**Interfaces:**
- Consumes: Task 2 CLI and config keys
- Produces: options `frogswamp.voice.{enable, port (8099), model ("haiku"), claudePackage (pkgs.claude-code), claudeTokenFile, haTokenFile, debugLog (false), whisper.model ("small"), piper.voices ({ ru = "ru_RU-irina-medium"; en = "en_US-lessac-medium"; })}`; systemd unit `voice-bridge.service`; Wyoming endpoints `tcp://127.0.0.1:10300` (whisper ru), `:10301` (whisper en), `:10200` (piper ru), `:10201` (piper en)

- [ ] **Step 1: Write the failing VM test `tests/voice.nix`** (shape of `tests/mode-switch.nix`). Node imports `../modules`, sets `voice.enable`, token files from `pkgs.writeText`, `claudePackage` = a fake `claude` script that writes its argv, env names and stdin to `/tmp/claude-calls` and prints `{"type":"result","is_error":false,"result":"Готово.","session_id":"s1"}`. Disable the Wyoming servers in the test (`services.wyoming.faster-whisper.servers = lib.mkForce {}` and the same for piper; they download models at start). Assertions:

```python
server.wait_for_unit("voice-bridge.service"); server.wait_for_open_port(8099)
out = server.succeed("curl -s localhost:8099/converse -d '{\"text\":\"привет\",\"language\":\"ru\",\"conversation_id\":\"c1\",\"earlier\":[]}'")
assert json.loads(out) == {"speech": "Готово.", "ok": True}, out
# bound to localhost only
server.fail("ss -ltn | grep -E '0.0.0.0:8099|\\*:8099'")
# tokens not in argv of anything, not in the journal, not readable by other users
server.fail("cat /proc/*/cmdline 2>/dev/null | tr '\\0' ' ' | grep -q claude-secret")
server.fail("journalctl -u voice-bridge | grep -E 'claude-secret|привет|Готово'")
server.fail("sudo -u nobody cat /run/credentials/voice-bridge.service/claude_token")
# the sandbox can't see HA's state or sops secrets
calls = server.succeed("cat /tmp/claude-calls")
assert "--strict-mcp-config" in calls and "haiku" in calls
server.succeed("systemctl show voice-bridge -P InaccessiblePaths | grep -q /var/lib/home-assistant")
```

The fake writes to `/tmp/claude-calls`, so in the test node only set `systemd.services.voice-bridge.serviceConfig.PrivateTmp = lib.mkForce false` and `ReadWritePaths = [ "/tmp" ]`.

- [ ] **Step 2: Run, verify it fails**

Run: `nix build .#checks.x86_64-linux.test-voice -L`
Expected: FAIL, attribute `voice` / option `frogswamp.voice` does not exist

- [ ] **Step 3: Implement `modules/services/voice/default.nix`**
- `nixpkgs.config.allowUnfreePredicate` for `claude-code` only
- `services.wyoming.faster-whisper.servers.{ru,en}`: `model = cfg.whisper.model`, `language`, `uri`, `device = "cpu"`. `services.wyoming.piper.servers.{ru,en}`: `voice`, `uri`. No firewall ports
- Generated files in the store (no secrets): `settings.json` = `{"permissions":{"allow":["mcp__homeassistant","WebSearch"],"deny":["Bash","Edit","Write","NotebookEdit","WebFetch"]}}`; `mcp.json` exactly as in Task 1 Step 2 (`${HA_TOKEN}` stays literal); bridge config JSON (Task 2 keys, `credentials_dir = "/run/credentials/voice-bridge.service"`, `workdir = "/var/lib/voice-agent/work"`, `config_dir = "/var/lib/voice-agent/claude"`)
- `systemd.services.voice-bridge`: `DynamicUser`, `StateDirectory = "voice-agent"`, `LoadCredential = [ "claude_token:…" "ha_token:…" ]`, `ProtectSystem = "strict"`, `ProtectHome`, `PrivateTmp`, `NoNewPrivileges`, `InaccessiblePaths = [ "/var/lib/home-assistant" "/run/secrets" "/etc/nixos" ]`, `RestrictAddressFamilies` INET/INET6/UNIX, `Restart = "on-failure"`. `preStart` makes `work/.claude` and links `work/.claude/skills` → `${./skills}`
- `system-prompt.md`: the spec's rules (short spoken sentences, no markdown/lists/URLs, answer in the `[language: …]` of the request, only do what this request asks, ask back when the device is unclear, never claim an action without a successful tool call, turn transliterated music names back into the original spelling before searching)
- Wire secrets in `hosts/frogswamp/secrets.nix` in the existing pattern: `(lib.mkIf config.frogswamp.voice.enable { "voice/claude_oauth_token" = { }; "voice/ha_token" = { }; })` and `voice.claudeTokenFile` / `voice.haTokenFile` defaults
- `justfile`: `voice-ask text lang="ru"` → `ssh pupsich@{{host}} curl -s 127.0.0.1:{{port}}/converse -d '<json>'` (build the JSON with `jq -n`)

- [ ] **Step 4: Run, verify it passes**

Run: `nix build .#checks.x86_64-linux.test-voice -L && nix build .#nixosConfigurations.frogswamp.config.system.build.toplevel`
Expected: both build

- [ ] **Step 5: Commit** (after the owner's yes): the files above — `feat(voice): Wyoming STT/TTS and the Claude bridge service`

---

### Task 4: HA integration `claude_bridge`

**Files:**
- Create: `modules/services/voice/claude_bridge/{manifest.json,__init__.py,const.py,config_flow.py,conversation.py,history.py,strings.json,translations/en.json}`
- Create: `modules/services/voice/claude_bridge/test_history.py`
- Create: `tests/voice-integration.nix`
- Modify: `modules/services/voice/default.nix` (mount into HA), `tests/default.nix`

**Interfaces:**
- Consumes: Task 2 HTTP API
- Produces: domain `claude_bridge`, name "Claude bridge", `config_flow: true`, `iot_class: local_polling`, `requirements: []`, `version: 0.1.0`, `dependencies: ["conversation"]`; one config entry with `url` (default `http://127.0.0.1:8099`), single instance; one conversation entity `conversation.claude_bridge`, `supported_languages = MATCH_ALL`
- `history.py` (no `homeassistant` import): `pending_turns(entries: list[tuple[str, str]], forwarded: int) -> list[dict]` returns `entries[forwarded:-1]` keeping only roles `user`/`assistant` with non-empty text, as `{"role", "text"}`; class `Forwarded(ttl=300, clock=time.monotonic)` with `get(conv_id) -> int`, `set(conv_id, count)`

- [ ] **Step 1: Write the failing tests `test_history.py`**

```python
def test_pending_turns_skips_current_and_forwarded():
    e = [("user", "открой левую"), ("assistant", "Открыла"), ("user", "и правую тоже")]
    assert pending_turns(e, 0) == [{"role": "user", "text": "открой левую"}, {"role": "assistant", "text": "Открыла"}]
    assert pending_turns(e, 2) == []

def test_pending_turns_drops_system_and_empty():
    assert pending_turns([("system", "x"), ("assistant", ""), ("user", "q")], 0) == []

def test_forwarded_ttl():
    now = [0.0]; f = Forwarded(300, clock=lambda: now[0])
    f.set("c", 3); assert f.get("c") == 3
    now[0] = 400; assert f.get("c") == 0
```

- [ ] **Step 2: Run, verify they fail** — `cd modules/services/voice/claude_bridge && python3 -m pytest -q test_history.py` → `ModuleNotFoundError`

- [ ] **Step 3: Implement**
- `history.py` per the interface
- `conversation.py`: `ConversationEntity` implementing `_async_handle_message(user_input, chat_log)` (check the exact base-class API in the HA 2026.10.1 image: `python -c "import homeassistant.components.conversation as c; help(c.ConversationEntity)"` inside the container, or HA's source at tag `2026.10.1`). Map `chat_log.content` to `(role, text)`; POST `{"text", "language": user_input.language, "conversation_id": chat_log.conversation_id, "earlier": pending_turns(...)}` with `aiohttp` from `async_get_clientsession`, 30 s timeout; add the answer with `chat_log.async_add_assistant_content_without_tools(AssistantContent(agent_id=..., content=speech))`; set `Forwarded` to `len(chat_log.content)`; return `async_get_result_from_chat_log(user_input, chat_log)`. Bridge unreachable → speak the same error sentences as Task 2
- `config_flow.py`: one step, `url` field, `async_set_unique_id(DOMAIN)` + abort if configured
- Mount in `default.nix`: `virtualisation.oci-containers.containers.home-assistant.volumes = lib.mkIf config.frogswamp.homeAssistant.enable [ "${./claude_bridge}:/config/custom_components/claude_bridge:ro" ]` (exclude `test_history.py` with `lib.fileset` or `builtins.path` filter)
- `tests/voice-integration.nix`: `runCommand` with pytest running `test_history.py`, and `python -m json.tool` on `manifest.json`, `strings.json` and `translations/en.json`, and assert the manifest `domain` = folder name

- [ ] **Step 4: Run, verify pass** — `nix build .#checks.x86_64-linux.test-voice-integration -L` and `just ha-check` → both pass

- [ ] **Step 5: Commit** (after the owner's yes) — `feat(voice): claude_bridge conversation agent for HA`

---

### Task 5: [owner] Deploy phase 1 and set up HA live

Each sub-step that touches the server or HA waits for the owner's yes.

**Files:**
- Create: `modules/services/voice/README.md`
- Modify: `README.md` (one line in the intro, link in "More detail")

- [ ] **Step 1: Deploy** — `just deploy`. Check: `just ssh systemctl is-active voice-bridge wyoming-faster-whisper-ru wyoming-faster-whisper-en wyoming-piper-ru wyoming-piper-en` → all `active`; `just voice-ask "Сколько будет 2+2?"` → `{"speech": …, "ok": true}`
- [ ] **Step 2: HA entries** — Wyoming ×4 (`localhost` ports from Global Constraints), `claude_bridge` (default URL)
- [ ] **Step 3: Pipelines** — "Russian" and "English": STT whisper ru/en, agent Claude bridge, "Prefer handling commands locally" on, TTS piper ru/en. Russian is the preferred pipeline
- [ ] **Step 4: Exposure and aliases** — expose blinds/curtains, lights, media players only; nothing that heats, locks, mode switches, the desk. Add ru/en aliases so "открой левую штору" / "open the left blind" match locally. Write the final exposed list and aliases into the README
- [ ] **Step 5: Phone** — Companion app on the Pixel 7: HA Assist as default digital assistant, Russian pipeline
- [ ] **Step 6: Acceptance** (numbers into the README):
  - "закрой шторы в гостиной" and "open the left blind" run locally in ~1 s, also with WAN unplugged (or HA's internet blocked for a minute)
  - Whisper `small`: time for a 3–5 s Russian phrase (`journalctl -u wyoming-faster-whisper-ru` or HA pipeline debug). If clearly > 3 s, try `base`; if Russian is poor, the owner decides on `medium`
  - free-form question answered in ≤ 8 s (from the pipeline debug view)
  - follow-up "а правую?" after a Claude-handled turn works; after a locally handled one, record what happens
  - Claude cannot change an unexposed entity, and refuses to edit an automation
- [ ] **Step 7: README** — what runs where, the voice-agent user and token, exposure list + aliases, measured numbers, `just voice-ask`, how to read logs, **switch to the API** (add HA's Anthropic integration with an API key, pick it as the agent in both pipelines; skills don't carry over), `debugLog`
- [ ] **Step 8: Commit** (after the owner's yes) — `docs(voice): setup, numbers and API switch`

---

### Task 6: Music Assistant + `play_music` script

**Files:**
- Create: `modules/services/music-assistant/default.nix`, `modules/services/music-assistant/image.nix`
- Create: `modules/services/home-assistant/config/scripts/play_music.yaml`
- Modify: `modules/default.nix`, `hosts/frogswamp/default.nix` (`musicAssistant.enable = true`), `justfile` (`ma-bump`), `tests/eval.nix` or a new `tests/music-assistant.nix`

**Interfaces:**
- Produces: option `frogswamp.musicAssistant.{enable, image, autoStart}`; container `music-assistant` (`--network=host`, volume `/var/lib/music-assistant:/data`); firewall `8095`, `8097`; HA script `script.play_music` with fields `query` (text, required) and `player` (entity, `media_player` domain, required) that calls `music_assistant.play_media` with `media_id: "{{ query }}"`, `enqueue: replace`, target the player

- [ ] **Step 1: Failing test** — VM test node with `musicAssistant.enable = true; autoStart = false;` asserts `systemctl cat podman-music-assistant` has `--network=host` and `/var/lib/music-assistant`, and the firewall has 8095 and 8097 (`iptables -S | grep -E 'dport (8095|8097)'`). Run `nix build .#checks.x86_64-linux.test-music-assistant -L` → FAIL (no option)
- [ ] **Step 2: Implement** the module in the style of `modules/services/home-assistant/default.nix` (oci-containers, tmpfiles for the state dir). `ma-bump` copies the `ha-bump` recipe for `ghcr.io/music-assistant/server`. `play_music.yaml` per the interface, with a `description` that tells Claude to pass the original-spelling name
- [ ] **Step 3: Verify** — the test passes; `just ha-check` passes
- [ ] **Step 4: Commit** (after the owner's yes) — `feat(music-assistant): server container and play_music script`

---

### Task 7: [owner] Phase 2: Voice PE and music live

- [ ] **Step 1:** `just deploy` (owner's yes). Open `http://192.168.101.10:8095`, add the Spotify provider (owner logs in, Premium), add the HA Music Assistant integration
- [ ] **Step 2:** Adopt the Voice PE in HA, wake words "Okay Nabu" → Russian, "Hey Jarvis" → English. Expose its media player and `script.play_music` to Assist
- [ ] **Step 3: Acceptance:** "Okay Nabu, включи Metallica" plays on the Voice PE; "Hey Jarvis, close the living room blinds" works locally; mute switch on → no wake; nothing is sent with no wake word (HA pipeline debug shows no runs)
- [ ] **Step 4:** README: music section, wake word mapping, mixed-language results. Commit after the owner's yes — `docs(voice): Voice PE and music`
