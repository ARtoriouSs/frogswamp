{ pkgs, ... }:
pkgs.runCommand "test-football-unit"
  {
    nativeBuildInputs = [ (pkgs.python3.withPackages (ps: [ ps.pytest ])) ];
  }
  ''
    cp ${../modules/services/football/app.py} app.py
    cp ${../modules/services/football/test_app.py} test_app.py
    python -m pytest -q -p no:cacheprovider test_app.py
    touch $out
  ''
