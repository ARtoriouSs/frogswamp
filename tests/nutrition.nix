{ pkgs, ... }:
pkgs.runCommand "test-nutrition"
  {
    nativeBuildInputs = [
      (pkgs.python3.withPackages (ps: [
        ps.pytest
        ps.tzdata
      ]))
    ];
  }
  ''
    cp ${../modules/services/nutrition}/*.py .
    python -m pytest -q -p no:cacheprovider test_*.py
    touch $out
  ''
