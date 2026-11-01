{ pkgs, ... }:
let
  config = ../modules/services/home-assistant/config;
  check = pkgs.writeText "check-sentences.py" ''
    import sys, yaml
    from hassil import Intents, recognize

    config = sys.argv[1]
    phrases = {}
    for lang in ("ru", "en"):
        with open(f"{config}/custom_sentences/{lang}/voice.yaml") as f:
            phrases[lang] = Intents.from_dict(yaml.safe_load(f))
    with open(f"{config}/packages/voice_intents.yaml") as f:
        scripts = yaml.safe_load(f)["intent_script"]

    # every phrase intent has an action in both languages, and the other way round
    for lang, intents in phrases.items():
        assert set(intents.intents) == set(scripts), (lang, sorted(intents.intents), sorted(scripts))

    expected = {
        "открой жалюзи": ("FrogBlindsOpen", {"lang": "ru"}),
        "подними жалюзи в гостиной": ("FrogBlindsOpen", {"lang": "ru"}),
        "закрой жалюзи": ("FrogBlindsClose", {"lang": "ru"}),
        "закрой жалюзи в гостиной полностью": ("FrogBlindsClose", {"lang": "ru"}),
        "открой дверь на балкон": ("FrogBalconyDoorOpen", {"lang": "ru"}),
        "открой шторы": ("FrogCurtainsOpen", {"lang": "ru"}),
        "закрой шторы в спальне": ("FrogCurtainsClose", {"lang": "ru"}),
        "включи тёплый пол в гостевом санузле": ("FrogFloorHeatingOn", {"lang": "ru", "bathroom": "guest"}),
        "включи теплый пол в гостевой ванной": ("FrogFloorHeatingOn", {"lang": "ru", "bathroom": "guest"}),
        "выключи тёплый пол в мастер санузле": ("FrogFloorHeatingOff", {"lang": "ru", "bathroom": "master"}),
        "выключи тёплый пол в мастер ванной": ("FrogFloorHeatingOff", {"lang": "ru", "bathroom": "master"}),
        "включи полотенцесушитель в мастер санузле": ("FrogTowelHeatOn", {"lang": "ru", "bathroom": "master"}),
        "выключи полотенцесушитель в гостевой ванной": ("FrogTowelHeatOff", {"lang": "ru", "bathroom": "guest"}),
        "какая температура в спальне": ("FrogRoomTemperature", {"lang": "ru", "room": "bedroom"}),
        "сколько сейчас градусов в коридоре": ("FrogRoomTemperature", {"lang": "ru", "room": "hall"}),
        "какая сейчас температура в прихожей": ("FrogRoomTemperature", {"lang": "ru", "room": "hall"}),
    }
    expected_en = {
        "open the blinds": ("FrogBlindsOpen", {}),
        "raise the blinds in the living room": ("FrogBlindsOpen", {}),
        "close the blinds": ("FrogBlindsClose", {}),
        "lower the blinds all the way": ("FrogBlindsClose", {}),
        "open the balcony door": ("FrogBalconyDoorOpen", {}),
        "open the curtains in the bedroom": ("FrogCurtainsOpen", {}),
        "close the curtains": ("FrogCurtainsClose", {}),
        "turn on the floor heating in the guest bathroom": ("FrogFloorHeatingOn", {"bathroom": "guest"}),
        "switch the heated floor off in the master bathroom": ("FrogFloorHeatingOff", {"bathroom": "master"}),
        "turn on the towel heater in the main bathroom": ("FrogTowelHeatOn", {"bathroom": "master"}),
        "turn the towel rail off in the guest toilet": ("FrogTowelHeatOff", {"bathroom": "guest"}),
        "what's the temperature in the bedroom": ("FrogRoomTemperature", {"room": "bedroom"}),
        "how warm is it in the hallway": ("FrogRoomTemperature", {"room": "hall"}),
    }
    cases = [("ru", t, i, sl) for t, (i, sl) in expected.items()]
    cases += [("en", t, i, {"lang": "en", **sl}) for t, (i, sl) in expected_en.items()]
    for lang, text, intent, slots in cases:
        result = recognize(text, phrases[lang])
        assert result is not None, f"no match: {text}"
        got = {name: e.value for name, e in result.entities.items()}
        assert (result.intent.name, got) == (intent, slots), (text, result.intent.name, got)

    # single devices stay with HA's built-in phrases
    for text in ("открой левые жалюзи", "закрой правые жалюзи", "включи тёплый пол", "какая температура в гостиной"):
        assert recognize(text, phrases["ru"]) is None, f"should not match: {text}"
    for text in ("open the left blind", "turn on the floor heating", "what's the temperature in the living room"):
        assert recognize(text, phrases["en"]) is None, f"should not match: {text}"

    # every reply has both languages
    import jinja2
    for name, script in scripts.items():
        if name == "FrogRoomTemperature":
            continue
        tpl = jinja2.Environment().from_string(script["speech"]["text"])
        ru, en = tpl.render(lang="ru"), tpl.render(lang="en")
        assert ru != en and en.isascii() and not ru.isascii(), (name, ru, en)

    # the temperature answer, with HA's template functions faked
    def say(value, room="bedroom", lang="ru"):
        env = jinja2.Environment()
        env.globals.update(states=lambda e: value, is_number=lambda v: v.replace(".", "", 1).isdigit())
        return env.from_string(scripts["FrogRoomTemperature"]["speech"]["text"]).render(room=room, lang=lang).strip()
    assert say("23.54") == "В спальне 23,5 градуса", say("23.54")
    assert say("21.0", "hall") == "В прихожей 21 градус", say("21.0", "hall")
    assert say("22.0") == "В спальне 22 градуса"
    assert say("25") == "В спальне 25 градусов"
    assert say("11.0") == "В спальне 11 градусов"
    assert say("unavailable") == "Не знаю, датчик в спальне сейчас недоступен", say("unavailable")
    assert say("23.54", lang="en") == "It's 23.5 degrees in the bedroom", say("23.54", lang="en")
    assert say("21.0", "hall", "en") == "It's 21 degrees in the hall", say("21.0", "hall", "en")
    assert say("unavailable", lang="en") == "I don't know, the bedroom sensor is unavailable right now"
    print("ok", len(cases), "phrases")
  '';
in
pkgs.runCommand "test-ha-custom-sentences"
  {
    nativeBuildInputs = [
      (pkgs.python3.withPackages (ps: [
        ps.hassil
        ps.pyyaml
        ps.jinja2
      ]))
    ];
  }
  ''
    python ${check} ${config}
    touch $out
  ''
