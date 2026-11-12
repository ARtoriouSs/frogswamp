"""Turn an answer into text Silero can read: it knows only Cyrillic words, so numbers, units and
Latin words (Polish street names, złoty) are written out in Russian letters first"""

import re

from num2words import num2words

MAX_CHARS = 800

# longest first, so "szcz" wins over "sz"
LATIN = sorted({
    "szcz": "щ", "sz": "ш", "cz": "ч", "rz": "ж", "ch": "х", "dż": "дж", "dź": "дь",
    "ią": "ён", "ię": "ен", "ia": "я", "ie": "е", "iu": "ю", "io": "ё",
    "ó": "у", "ł": "л", "ż": "ж", "ź": "зь", "ś": "сь", "ć": "ць", "ń": "нь", "ą": "он", "ę": "эн",
    "a": "а", "b": "б", "c": "ц", "d": "д", "e": "е", "f": "ф", "g": "г", "h": "х", "i": "и",
    "j": "й", "k": "к", "l": "л", "m": "м", "n": "н", "o": "о", "p": "п", "q": "к", "r": "р",
    "s": "с", "t": "т", "u": "у", "v": "в", "w": "в", "x": "кс", "y": "ы", "z": "з",
}.items(), key=lambda kv: -len(kv[0]))
LATIN_RE = re.compile("|".join(re.escape(k) for k, _ in LATIN))
LATIN_MAP = dict(LATIN)

# after a number; the case doesn't always fit, but it is understood
UNITS = [
    (r"°\s*[CС]|°", "градусов"),
    (r"%", "процентов"),
    (r"zł|zl|PLN|злотых|злотого|злотый|злоты", "злотых"),
    (r"gr|grosz[a-zy]*|грош[а-я]*", "грошей"),
    (r"кВт·ч|кВт\*ч|кВтч|kWh", "киловатт-часов"),
    (r"кВт|kW", "киловатт"),
    (r"Вт|W", "ватт"),
    (r"км|km", "километров"),
    (r"м|m", "метров"),
    (r"мин|min", "минут"),
    (r"ч|h", "часов"),
]
NUMBER = r"[-−–]?\d+(?:[.,]\d+)?"
UNIT_RE = re.compile(rf"({NUMBER})\s*(?:{'|'.join(f'(?P<u{i}>{p})' for i, (p, _) in enumerate(UNITS))})(?![A-Za-zА-Яа-яЁё])")
TIME_RE = re.compile(r"\b([01]?\d|2[0-3]):([0-5]\d)\b")
RANGE_RE = re.compile(r"(\d)\s*[-–—]\s*(\d)")
NUMBER_RE = re.compile(NUMBER)
SENTENCE_RE = re.compile(r"(?<=[.!?…])\s+")


def say_number(text):
    negative = text[0] in "-−–"
    text = text.lstrip("-−–")
    whole, _, frac = text.replace(",", ".").partition(".")
    words = num2words(int(whole), lang="ru")
    if frac.strip("0"):
        # spoken style: "двадцать шесть и семь", not "целых ... десятых"
        words += " и " + num2words(int(frac), lang="ru")
    return ("минус " if negative else "") + words


def say_time(m):
    hours, minutes = int(m.group(1)), int(m.group(2))
    return num2words(hours, lang="ru") + (" ноль ноль" if minutes == 0 else " " + num2words(minutes, lang="ru"))


def say_unit(m):
    unit = next(word for i, (_, word) in enumerate(UNITS) if m.group(f"u{i}"))
    return f"{m.group(1)} {unit}"


def transliterate(m):
    return LATIN_RE.sub(lambda c: LATIN_MAP[c.group(0)], m.group(0).lower())


def normalize(text):
    text = re.sub(r"[*_#`>\[\]]", " ", text)
    text = re.sub(r"https?://\S+", " ", text)
    text = TIME_RE.sub(say_time, text)
    text = RANGE_RE.sub(r"\1 – \2", text)
    text = UNIT_RE.sub(say_unit, text)
    # a minus only right before a number at the start or after a space
    text = re.sub(r"(^|\s)[-−]\s*(?=\d)", r"\1-", text)
    text = NUMBER_RE.sub(lambda m: (" " if m.group(0)[0] in "-−–" else "") + say_number(m.group(0)), text)
    text = re.sub(r"[A-Za-zÀ-ÿĀ-ž]+", transliterate, text)
    # whatever is still not a letter, digit or punctuation (emoji, symbols) is dropped
    text = re.sub(r"[^\w\s.,!?…:;'\"«»()+—–-]", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def sentences(text):
    """Pieces short enough for one Silero call, each ending with punctuation"""
    out = []
    for sentence in SENTENCE_RE.split(normalize(text)):
        while len(sentence) > MAX_CHARS:
            cut = sentence.rfind(",", 0, MAX_CHARS)
            cut = cut if cut > 0 else sentence.rfind(" ", 0, MAX_CHARS)
            cut = cut if cut > 0 else MAX_CHARS
            out.append(sentence[:cut].strip(" ,") + ".")
            sentence = sentence[cut + 1:].strip()
        if sentence:
            out.append(sentence if sentence[-1] in ".!?…" else sentence + ".")
    return out
