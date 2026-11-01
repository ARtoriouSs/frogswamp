{ pkgs, ... }:
pkgs.runCommand "test-voice-bridge-unit"
  {
    nativeBuildInputs = [ (pkgs.python3.withPackages (ps: [ ps.pytest ])) ];
  }
  ''
    cp ${../modules/services/voice/bridge.py} bridge.py
    cp ${../modules/services/voice/test_bridge.py} test_bridge.py
    cp ${../modules/services/voice/web_guard.py} web_guard.py
    cp ${../modules/services/voice/test_web_guard.py} test_web_guard.py
    python -m pytest -q -p no:cacheprovider test_bridge.py test_web_guard.py
    touch $out
  ''
