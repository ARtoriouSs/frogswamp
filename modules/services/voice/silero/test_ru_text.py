from ru_text import MAX_CHARS, normalize, sentences


def test_numbers_and_units():
    assert normalize("В гостевом санузле 26,7 °C") == "В гостевом санузле двадцать шесть и семь градусов"
    assert normalize("на улице -5°") == "на улице минус пять градусов"
    assert normalize("заряд 52%") == "заряд пятьдесят два процентов"
    assert normalize("осталось 496 км") == "осталось четыреста девяносто шесть километров"
    assert normalize("это 16 złotych 93 grosze") == "это шестнадцать злотых девяносто три грошей"
    assert normalize("149 zł") == "сто сорок девять злотых"
    assert normalize("12.50 zł") == "двенадцать и пятьдесят злотых"


def test_time_and_ranges():
    assert normalize("с 8:00 до 21:30") == "с восемь ноль ноль до двадцать один тридцать"
    assert normalize("7-8 километров") == "семь – восемь километров"


def test_latin_words():
    assert normalize("Wawerska 20") == "ваверска двадцать"
    assert normalize("Apteka DOZ") == "аптека доз"
    assert normalize("улица Świętokrzyska") == "улица сьвентокжыска"


def test_markdown_links_and_emoji_dropped():
    assert normalize("**Готово** 👍 https://example.com/x") == "Готово"


def test_plain_russian_unchanged():
    text = "Ну смотри, завтра будет солнечно. Хочешь, включу свет?"
    assert normalize(text) == text


def test_sentences_split_and_end_with_punctuation():
    assert sentences("Привет. Как дела? Нормально") == ["Привет.", "Как дела?", "Нормально."]
    long = ", ".join(["очень длинная фраза"] * 100)
    parts = sentences(long)
    assert all(len(p) <= MAX_CHARS + 1 for p in parts) and len(parts) > 1
    assert sentences("") == []
