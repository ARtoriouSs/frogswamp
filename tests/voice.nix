{ pkgs, ... }:
let
  # stands in for Claude Code: records what it gets and answers like `claude -p --output-format json`
  fakeClaude = pkgs.writeShellScriptBin "claude" ''
    {
      echo "ARGS $*"
      echo "ENV $(${pkgs.coreutils}/bin/env | ${pkgs.coreutils}/bin/cut -d= -f1 | ${pkgs.coreutils}/bin/sort | ${pkgs.coreutils}/bin/tr '\n' ' ')"
      echo "TOKENS_SET $([ "$CLAUDE_CODE_OAUTH_TOKEN" = claude-secret ] && [ "$HA_TOKEN" = ha-secret ] && echo yes)"
      echo "PWD $PWD"
      echo "STDIN $(${pkgs.coreutils}/bin/cat)"
      ${pkgs.coreutils}/bin/ls -la .claude/
    } >> "$HOME/claude-calls"
    echo '{"type":"result","is_error":false,"result":"**Готово.**","session_id":"s1"}'
  '';
in
pkgs.testers.runNixOSTest {
  name = "voice";
  nodes.server =
    { lib, ... }:
    {
      imports = [ ../modules ];
      frogswamp = {
        adminUser = "pupsich";
        voice = {
          enable = true;
          claudePackage = fakeClaude;
          claudeTokenFile = toString (pkgs.writeText "claude-token" "claude-secret");
          haTokenFile = toString (pkgs.writeText "ha-token" "ha-secret");
        };
      };
      # the STT/TTS servers download their models at start, there is no internet in the test
      services.wyoming.faster-whisper.servers = lib.mkForce { };
      services.wyoming.piper.servers = lib.mkForce { };
    };
  testScript = ''
    import json

    server.wait_for_unit("voice-bridge.service")
    server.wait_for_open_port(8099)

    def ask(text, conv="c1"):
        body = json.dumps({"text": text, "language": "ru", "conversation_id": conv, "earlier": []})
        return json.loads(server.succeed(f"curl -s localhost:8099/converse -d '{body}'"))

    assert ask("привет") == {"speech": "Готово.", "ok": True}

    calls = server.succeed("cat /var/lib/private/voice-agent/claude-calls")
    assert "--strict-mcp-config" in calls and "--model haiku" in calls, calls
    assert "--resume" not in calls, calls
    assert "TOKENS_SET yes" in calls, calls
    assert "STDIN [language: ru]\nпривет" in calls, calls
    assert "voice-agent/work\n" in calls, calls
    assert "skills ->" in calls, calls

    # a follow-up in the same conversation resumes the session
    ask("и ещё")
    assert "--resume s1" in server.succeed("tail -n 20 /var/lib/private/voice-agent/claude-calls")

    # the MCP config keeps the token as a placeholder, the store has no token
    mcp = json.loads(server.succeed("cat $(grep -o '/nix/store/[^\"]*mcp.json' $(systemctl cat voice-bridge | grep -o '/nix/store/[^ ]*voice-bridge.json'))"))
    assert mcp["mcpServers"]["homeassistant"]["headers"]["Authorization"] == "Bearer ''${HA_TOKEN}", mcp
    assert mcp["mcpServers"]["homeassistant"]["url"] == "http://127.0.0.1:8123/api/mcp", mcp

    # only localhost, no text or tokens in the journal, token files only for the service
    server.fail("ss -ltn | grep -E '0.0.0.0:8099|\\*:8099|\\[::\\]:8099'")
    server.fail("journalctl -u voice-bridge | grep -E 'claude-secret|ha-secret|привет|Готово'")
    server.fail("cat /proc/*/cmdline 2>/dev/null | tr '\\0' ' ' | grep -q 'claude-secre[t]'")
    server.fail("runuser -u nobody -- cat /run/credentials/voice-bridge.service/claude_token")

    # the sandbox hides HA's state and the sops secrets
    hidden = server.succeed("systemctl show voice-bridge -P InaccessiblePaths")
    assert "/var/lib/home-assistant" in hidden and "/run/secrets" in hidden, hidden
  '';
}
