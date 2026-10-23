{ pkgs, ... }:
let
  python = pkgs.python3.withPackages (p: [ p.pyyaml ]);
  presets = ../modules/services/home-assistant/config/cubematrix;
  artformat = ../integrations/cubematrix/custom_components/cubematrix/artformat.py;
  checker = pkgs.writeText "check-presets.py" ''
    import importlib.util, pathlib, sys

    spec = importlib.util.spec_from_file_location("artformat", sys.argv[1])
    artformat = importlib.util.module_from_spec(spec)
    sys.modules["artformat"] = artformat
    spec.loader.exec_module(artformat)

    files = sorted(pathlib.Path(sys.argv[2]).glob("*.yaml"))
    assert files, "no presets found"
    failed = False
    for path in files:
        try:
            arts = artformat.parse_text(path.read_text())
            print(f"ok {path.name}: {len(arts)} art(s)")
        except artformat.ArtError as err:
            failed = True
            print(f"FAIL {path.name}: {err}")
    sys.exit(1 if failed else 0)
  '';
in
# Every repo preset must parse with the integration's own parser
pkgs.runCommand "test-cubematrix-presets" { } ''
  ${python}/bin/python3 ${checker} ${artformat} ${presets}
  touch $out
''
