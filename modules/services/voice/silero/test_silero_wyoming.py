import asyncio

from wyoming.audio import AudioChunk, AudioStart, AudioStop
from wyoming.error import Error
from wyoming.info import Describe, Info
from wyoming.tts import Synthesize, SynthesizeVoice

import silero_wyoming


class FakeEngine:
    def __init__(self, fail=False):
        self.calls = []
        self.fail = fail

    def pcm(self, text, speaker):
        if self.fail:
            raise RuntimeError("boom")
        self.calls.append((text, speaker))
        return b"\x01\x00" * 3000


class Recorder(silero_wyoming.Handler):
    def __init__(self, engine):
        super().__init__(silero_wyoming.make_info("v5_6_ru"), engine, "xenia", None, None)
        self.events = []

    async def write_event(self, event):
        self.events.append(event)


def run(handler, event):
    return asyncio.run(handler.handle_event(event))


def test_describe_lists_russian_voices():
    h = Recorder(FakeEngine())
    run(h, Describe().event())
    info = Info.from_event(h.events[0])
    assert [v.name for v in info.tts[0].voices] == list(silero_wyoming.SPEAKERS)
    assert all(v.languages == ["ru"] for v in info.tts[0].voices)


def test_synthesize_speaks_normalized_sentences():
    engine = FakeEngine()
    h = Recorder(engine)
    run(h, Synthesize(text="На улице -5°. Заряд 52%").event())
    assert engine.calls == [("На улице минус пять градусов.", "xenia"), ("Заряд пятьдесят два процентов.", "xenia")]
    types = [e.type for e in h.events]
    assert AudioStart.is_type(types[0]) and AudioStop.is_type(types[-1])
    chunks = [AudioChunk.from_event(e) for e in h.events if AudioChunk.is_type(e.type)]
    assert sum(len(c.audio) for c in chunks) == 2 * 6000
    assert all(c.rate == 24000 and c.width == 2 for c in chunks)


def test_voice_from_request_or_default():
    engine = FakeEngine()
    h = Recorder(engine)
    run(h, Synthesize(text="Привет", voice=SynthesizeVoice(name="aidar")).event())
    run(h, Synthesize(text="Привет", voice=SynthesizeVoice(name="ru_RU-irina-medium")).event())
    assert [c[1] for c in engine.calls] == ["aidar", "xenia"]


def test_failure_sends_error():
    h = Recorder(FakeEngine(fail=True))
    run(h, Synthesize(text="Привет").event())
    assert any(Error.is_type(e.type) for e in h.events)
