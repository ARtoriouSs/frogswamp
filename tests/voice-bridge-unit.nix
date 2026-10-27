{ pkgs, ... }:
pkgs.runCommand "test-voice-bridge-unit"
  {
    nativeBuildInputs = [ (pkgs.python3.withPackages (ps: [ ps.pytest ])) ];
  }
  ''
    cp ${../modules/services/voice/bridge.py} bridge.py
    cp ${../modules/services/voice/test_bridge.py} test_bridge.py
    python -m pytest -q -p no:cacheprovider test_bridge.py
    touch $out
  ''
