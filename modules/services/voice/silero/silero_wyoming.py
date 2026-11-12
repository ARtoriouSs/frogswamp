"""Wyoming TTS server for Silero's Russian voices, so HA can use it like Piper

The model file is a torch package with Silero's own code inside; it runs in a sandboxed service
without network access (see default.nix)
"""

import argparse
import asyncio
import logging
import threading
from functools import partial

from wyoming.audio import AudioChunk, AudioStart, AudioStop
from wyoming.error import Error
from wyoming.event import Event
from wyoming.info import Attribution, Describe, Info, TtsProgram, TtsVoice
from wyoming.server import AsyncEventHandler, AsyncServer
from wyoming.tts import Synthesize

from ru_text import sentences

log = logging.getLogger("wyoming-silero")

SPEAKERS = ("aidar", "baya", "kseniya", "xenia", "eugene")
RATE = 24000
WIDTH = 2
SAMPLES_PER_CHUNK = 2048
ATTRIBUTION = Attribution(name="Silero", url="https://github.com/snakers4/silero-models")


class Silero:
    def __init__(self, path, threads):
        import torch

        self.torch = torch
        torch.set_num_threads(threads)
        self.model = torch.package.PackageImporter(path).load_pickle("tts_models", "model")
        self.model.to(torch.device("cpu"))
        # one synthesis at a time, they would only fight for the same cores
        self.lock = threading.Lock()

    def pcm(self, text, speaker):
        with self.lock, self.torch.inference_mode():
            audio = self.model.apply_tts(text=text, speaker=speaker, sample_rate=RATE)
        return (audio.clamp(-1, 1) * 32767).to(self.torch.int16).numpy().tobytes()


def make_info(model_name):
    return Info(tts=[TtsProgram(
        name="silero",
        description=f"Silero {model_name}",
        attribution=ATTRIBUTION,
        installed=True,
        version=model_name,
        voices=[
            TtsVoice(name=s, description=s, attribution=ATTRIBUTION, installed=True, version=None, languages=["ru"])
            for s in SPEAKERS
        ],
        supports_synthesize_streaming=False,
    )])


class Handler(AsyncEventHandler):
    def __init__(self, info, engine, default_voice, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.info_event = info.event()
        self.engine = engine
        self.default_voice = default_voice

    async def handle_event(self, event: Event) -> bool:
        if Describe.is_type(event.type):
            await self.write_event(self.info_event)
            return True
        if not Synthesize.is_type(event.type):
            return True
        synthesize = Synthesize.from_event(event)
        voice = synthesize.voice.name if synthesize.voice and synthesize.voice.name in SPEAKERS else self.default_voice
        try:
            await self.speak(synthesize.text, voice)
        except Exception as e:
            log.exception("synthesis failed")
            await self.write_event(Error(text=str(e), code=type(e).__name__).event())
        return True

    async def speak(self, text, voice):
        loop = asyncio.get_running_loop()
        await self.write_event(AudioStart(rate=RATE, width=WIDTH, channels=1).event())
        # sentence by sentence, so HA can start playing before the whole answer is ready
        for sentence in sentences(text):
            pcm = await loop.run_in_executor(None, self.engine.pcm, sentence, voice)
            step = SAMPLES_PER_CHUNK * WIDTH
            for i in range(0, len(pcm), step):
                await self.write_event(AudioChunk(rate=RATE, width=WIDTH, channels=1, audio=pcm[i:i + step]).event())
        await self.write_event(AudioStop().event())


async def run(args):
    engine = Silero(args.model, args.threads)
    log.info("model loaded, listening on %s", args.uri)
    server = AsyncServer.from_uri(args.uri)
    await server.run(partial(Handler, make_info(args.model_name), engine, args.voice))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--uri", required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--model-name", default="v5_6_ru")
    parser.add_argument("--voice", default="xenia", choices=SPEAKERS)
    parser.add_argument("--threads", type=int, default=4)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    asyncio.run(run(args))


if __name__ == "__main__":
    main()
