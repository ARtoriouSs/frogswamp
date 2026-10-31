{ pkgs, ... }:
let
  config = ../modules/services/home-assistant/config;
  check = pkgs.writeText "check-sentences.py" ''
    import sys, yaml
    from hassil import Intents, recognize

    config = sys.argv[1]
    with open(f"{config}/custom_sentences/ru/voice.yaml") as f:
        intents = Intents.from_dict(yaml.safe_load(f))
    with open(f"{config}/packages/voice_intents.yaml") as f:
        scripts = yaml.safe_load(f)["intent_script"]

    # every phrase intent has an action, and the other way round
    assert set(intents.intents) == set(scripts), (sorted(intents.intents), sorted(scripts))

    expected = {
        "открой жалюзи": ("FrogBlindsOpen", {}),
        "подними жалюзи в гостиной": ("FrogBlindsOpen", {}),
        "закрой жалюзи": ("FrogBlindsClose", {}),
        "закрой жалюзи в гостиной полностью": ("FrogBlindsClose", {}),
        "открой дверь на балкон": ("FrogBalconyDoorOpen", {}),
        "открой шторы": ("FrogCurtainsOpen", {}),
        "закрой шторы в спальне": ("FrogCurtainsClose", {}),
        "включи тёплый пол в гостевом санузле": ("FrogFloorHeatingOn", {"bathroom": "guest"}),
        "включи теплый пол в гостевой ванной": ("FrogFloorHeatingOn", {"bathroom": "guest"}),
        "выключи тёплый пол в мастер санузле": ("FrogFloorHeatingOff", {"bathroom": "master"}),
        "выключи тёплый пол в мастер ванной": ("FrogFloorHeatingOff", {"bathroom": "master"}),
        "включи полотенцесушитель в мастер санузле": ("FrogTowelHeatOn", {"bathroom": "master"}),
        "выключи полотенцесушитель в гостевой ванной": ("FrogTowelHeatOff", {"bathroom": "guest"}),
    }
    for text, (intent, slots) in expected.items():
        result = recognize(text, intents)
        assert result is not None, f"no match: {text}"
        got = {name: e.value for name, e in result.entities.items()}
        assert (result.intent.name, got) == (intent, slots), (text, result.intent.name, got)

    # single devices stay with HA's built-in phrases
    for text in ("открой левые жалюзи", "закрой правые жалюзи", "включи тёплый пол"):
        assert recognize(text, intents) is None, f"should not match: {text}"
    print("ok", len(expected), "phrases")
  '';
in
pkgs.runCommand "test-ha-custom-sentences"
  {
    nativeBuildInputs = [
      (pkgs.python3.withPackages (ps: [
        ps.hassil
        ps.pyyaml
      ]))
    ];
  }
  ''
    python ${check} ${config}
    touch $out
  ''
