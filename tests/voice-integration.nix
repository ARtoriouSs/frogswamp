{ pkgs, ... }:
let
  src = ../modules/services/voice/claude_bridge;
in
pkgs.runCommand "test-voice-integration"
  {
    nativeBuildInputs = [
      (pkgs.python3.withPackages (ps: [ ps.pytest ]))
      pkgs.jq
    ];
  }
  ''
    cp -r ${src} claude_bridge
    chmod -R u+w claude_bridge
    # history.py has no HA imports; outside the package pytest doesn't load __init__.py
    mkdir unit
    cp claude_bridge/history.py claude_bridge/test_history.py unit/
    (cd unit && python -m pytest -q -p no:cacheprovider test_history.py)
    cd claude_bridge
    python -m py_compile *.py
    for f in manifest.json strings.json translations/en.json; do jq empty "$f"; done
    [ "$(jq -r .domain manifest.json)" = claude_bridge ]
    grep -q 'DOMAIN = "claude_bridge"' const.py
    # HA shows translations/en.json; it must match strings.json
    diff strings.json translations/en.json
    touch $out
  ''
