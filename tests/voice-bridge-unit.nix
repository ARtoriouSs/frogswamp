{ pkgs, ... }:
pkgs.runCommand "test-voice-bridge-unit"
  {
    nativeBuildInputs = [
      (pkgs.python3.withPackages (ps: [
        ps.pytest
        ps.num2words
        ps.wyoming
      ]))
    ];
  }
  ''
    cp ${../modules/services/voice/bridge.py} bridge.py
    cp ${../modules/services/voice/test_bridge.py} test_bridge.py
    cp ${../modules/services/voice/web_guard.py} web_guard.py
    cp ${../modules/services/voice/test_web_guard.py} test_web_guard.py
    python -m pytest -q -p no:cacheprovider test_bridge.py test_web_guard.py
    # Silero's server and text prep, with a fake engine instead of the model
    cp ${../modules/services/voice/silero}/*.py .
    python -m pytest -q -p no:cacheprovider test_ru_text.py test_silero_wyoming.py
    touch $out
  ''
