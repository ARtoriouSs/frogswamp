"""Voice bridge: text from Home Assistant's voice pipeline in, a short spoken answer out.

POST /converse {"text", "language", "conversation_id", "earlier"} -> {"speech", "ok"}

The answer comes from a backend (now only Claude Code run headless on the owner's
subscription). Text and tokens never reach the log or the command line.
"""

import argparse
import json
import logging
import os
import re
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import NamedTuple

log = logging.getLogger("voice-bridge")

LANGUAGES = ("ru", "en")
ERROR_SPEECH = {
    "ru": "Claude сейчас недоступен",
    "en": "Claude is not available right now",
}
URL_RE = re.compile(r"https?://\S+")
# the web search tool asks for a sources list at the end, it makes no sense in speech
SOURCES_RE = re.compile(r"\s*\b(?:Sources|Источники)\s*:.*$", re.DOTALL | re.IGNORECASE)
MD_LINK_RE = re.compile(r"\[([^\]]*)\]\([^)]*\)?")
LIST_MARK_RE = re.compile(r"^\s*(?:[-•]|\d+\.)\s+", re.MULTILINE)
MARKDOWN_RE = re.compile(r"[*_#`>]")
SHARED_SESSION = "shared"


class Reply(NamedTuple):
    text: str
    session_id: str | None


class BackendError(Exception):
    pass


def normalize_language(lang):
    base = (lang or "").split("-")[0].lower()
    return base if base in LANGUAGES else "en"


def error_speech(language):
    return ERROR_SPEECH[normalize_language(language)]


def build_prompt(text, language, earlier):
    lines = [f"[language: {language}]"]
    if earlier:
        lines.append("Earlier in this conversation (handled by the home without you):")
        for turn in earlier:
            who = "User" if turn["role"] == "user" else "Home"
            lines.append(f"{who}: {turn['text']}")
        lines.append("")
        lines.append(f"Now the user says: {text}")
    else:
        lines.append(text)
    return "\n".join(lines)


def sanitize_speech(text, limit=600):
    text = SOURCES_RE.sub("", text)
    text = MD_LINK_RE.sub(r"\1", text)
    text = URL_RE.sub("", text)
    text = LIST_MARK_RE.sub("", text)
    text = MARKDOWN_RE.sub("", text)
    text = " ".join(text.split())
    if len(text) <= limit:
        return text
    cut = text[:limit]
    end = max(cut.rfind(c) for c in ".!?")
    return cut[: end + 1] if end > 0 else cut


class SessionStore:
    """Backend session per HA conversation, forgotten after `ttl` seconds idle"""

    def __init__(self, ttl, clock=time.monotonic):
        self.ttl = ttl
        self.clock = clock
        self.sessions = {}
        self.locks = {}
        self.guard = threading.Lock()

    def get(self, conv_id):
        if conv_id is None:
            return None
        with self.guard:
            entry = self.sessions.get(conv_id)
            if entry is None or self.clock() - entry[1] > self.ttl:
                self.sessions.pop(conv_id, None)
                return None
            return entry[0]

    def put(self, conv_id, session_id):
        if conv_id is None:
            return
        with self.guard:
            self.sessions[conv_id] = (session_id, self.clock())

    def lock(self, conv_id):
        if conv_id is None:
            return threading.Lock()
        with self.guard:
            now = self.clock()
            for old in [c for c, (_, t) in self.sessions.items() if now - t > self.ttl]:
                self.sessions.pop(old)
                if not self.locks.get(old, threading.Lock()).locked():
                    self.locks.pop(old, None)
            return self.locks.setdefault(conv_id, threading.Lock())


class ClaudeCodeBackend:
    def __init__(self, cfg, run=subprocess.run):
        self.cfg = cfg
        self.run = run

    def args(self, system_prompt, session_id):
        c = self.cfg
        args = [
            c["claude"], "-p",
            "--output-format", "json",
            "--model", c["model"],
            "--append-system-prompt", system_prompt,
            "--mcp-config", c["mcp_config"],
            "--strict-mcp-config",
            "--settings", c["settings"],
        ]
        if session_id:
            args += ["--resume", session_id]
        return args

    def secret(self, name):
        with open(os.path.join(self.cfg["credentials_dir"], name)) as f:
            return f.read().strip()

    def env(self):
        c = self.cfg
        return {
            "HOME": c["home"],
            "PATH": os.environ.get("PATH", "/run/current-system/sw/bin"),
            "CLAUDE_CONFIG_DIR": c["config_dir"],
            "CLAUDE_CODE_OAUTH_TOKEN": self.secret("claude_token"),
            # read by the MCP config as ${HA_TOKEN}
            "HA_TOKEN": self.secret("ha_token"),
            "DISABLE_AUTOUPDATER": "1",
            "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1",
        }

    def ask(self, prompt, system_prompt, session_id, timeout):
        try:
            done = self.run(
                self.args(system_prompt, session_id),
                input=prompt,
                capture_output=True,
                text=True,
                timeout=timeout,
                cwd=self.cfg["workdir"],
                env=self.env(),
            )
        except subprocess.TimeoutExpired:
            raise BackendError("timeout")
        try:
            out = json.loads(done.stdout)
        except ValueError:
            raise BackendError(f"exit {done.returncode}, no JSON")
        if out.get("is_error") or not out.get("result"):
            raise BackendError(out.get("subtype") or "error result")
        return Reply(out["result"], out.get("session_id"))


BACKENDS = {"claude-code": ClaudeCodeBackend}


def make_backend(cfg):
    name = cfg.get("backend")
    if name not in BACKENDS:
        raise ValueError(f"unknown backend {name!r}")
    return BACKENDS[name](cfg[name])


def converse(backend, store, req, system_prompt, timeout, debug_log=False, shared=False):
    language = normalize_language(req.get("language"))
    # shared: one backend session for the whole home, whatever HA conversation the request is in
    conv_id = SHARED_SESSION if shared else req.get("conversation_id")
    prompt = build_prompt(req["text"], language, req.get("earlier") or [])
    started = time.monotonic()
    with store.lock(conv_id):
        session_id = store.get(conv_id)
        try:
            try:
                reply = backend.ask(prompt, system_prompt, session_id, timeout)
            except BackendError:
                if not session_id:
                    raise
                # an old session can be gone on the backend side
                reply = backend.ask(prompt, system_prompt, None, timeout)
        except BackendError as e:
            log.warning("lang=%s ok=False %.1fs error=%s", language, time.monotonic() - started, e)
            return {"speech": error_speech(language), "ok": False}
        store.put(conv_id, reply.session_id)
    speech = sanitize_speech(reply.text)
    log.info("lang=%s ok=True %.1fs", language, time.monotonic() - started)
    if debug_log:
        log.info("text=%r speech=%r", req["text"], speech)
    return {"speech": speech, "ok": True}


def valid_request(req):
    return (
        isinstance(req, dict)
        and isinstance(req.get("text"), str)
        and req["text"].strip() != ""
        and isinstance(req.get("earlier", []), list)
    )


def make_handler(cfg, backend, system_prompt):
    store = SessionStore(cfg["session_ttl"])

    class Handler(BaseHTTPRequestHandler):
        def reply(self, code, body=None):
            data = json.dumps(body or {}, ensure_ascii=False).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            self.reply(404)

        def do_POST(self):
            if self.path != "/converse":
                return self.reply(404)
            try:
                req = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))))
            except ValueError:
                return self.reply(400)
            if not valid_request(req):
                return self.reply(400)
            self.reply(200, converse(
                backend, store, req, system_prompt, cfg["timeout"], cfg["debug_log"], cfg.get("shared_session", False)
            ))

        def log_message(self, *args):
            pass

    return Handler


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True)
    with open(parser.parse_args().config) as f:
        cfg = json.load(f)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    with open(cfg["system_prompt_file"]) as f:
        system_prompt = f.read()
    host, port = cfg["listen"].rsplit(":", 1)
    server = ThreadingHTTPServer((host, int(port)), make_handler(cfg, make_backend(cfg), system_prompt))
    log.info("listening on %s, backend %s", cfg["listen"], cfg["backend"])
    server.serve_forever()


if __name__ == "__main__":
    main()
