{ pkgs, ... }:
pkgs.runCommand "test-media-janitor"
  {
    nativeBuildInputs = [ (pkgs.python3.withPackages (ps: [ ps.pytest ])) ];
  }
  ''
    cp ${../modules/services/media-janitor/janitor.py} janitor.py
    cp ${../modules/services/media-janitor/test_janitor.py} test_janitor.py
    python -m pytest -q -p no:cacheprovider test_janitor.py
    touch $out
  ''
