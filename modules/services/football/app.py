"""Football: pick an Ace Stream match on the phone, play it on the TV's Kodi.

The page takes an Ace Stream ID (or an acestream:// link, or a URL with id=),
wakes the TV and opens Kodi through Home Assistant, then tells Kodi to play
the stream of the Ace Stream engine running on this server. Or it casts the
stream to the TV's built-in Chromecast through Home Assistant. Pure helpers come
first; the I/O and the web server are below them.
"""

import argparse
import base64
import json
import os
import re
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ID_RE = re.compile(r"(?:^|://|id=)([0-9a-fA-F]{40})(?![0-9a-fA-F])")


def parse_id(text):
    """The 40-hex content ID in a pasted ID or link, lowercased, or None"""
    m = ID_RE.search((text or "").strip())
    return m.group(1).lower() if m else None


def stream_urls(content_id, engine_base):
    base = engine_base.rstrip("/")
    return {
        "tv": f"{base}/ace/getstream?id={content_id}",
        "hls": f"{base}/ace/manifest.m3u8?id={content_id}",
    }


def add_recent(recent, content_id, label, now, limit=8):
    """Newest first, no duplicates, at most `limit` entries; an empty label keeps the old one"""
    old = next((r["label"] for r in recent if r["id"] == content_id), "")
    label = label or old
    rest = [r for r in recent if r["id"] != content_id]
    return [{"id": content_id, "label": label, "at": now}] + rest[: limit - 1]


# ---------- I/O ----------

def log(msg):
    print(msg, flush=True)


def read(path):
    # read on every call: sops may have rotated the secret
    with open(path) as f:
        return f.read().strip()


def http(method, url, headers=None, body=None, timeout=5):
    data, headers = None, dict(headers or {})
    if body is not None:
        data, headers["Content-Type"] = json.dumps(body).encode(), "application/json"
    req = urllib.request.Request(url, data=data, method=method, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read()
    return json.loads(raw) if raw.strip() else None


def kodi(cfg, method, params=None):
    k = cfg["kodi"]
    auth = base64.b64encode(f"{k['username']}:{read(k['password_file'])}".encode()).decode()
    body = {"jsonrpc": "2.0", "id": 1, "method": method}
    if params is not None:
        body["params"] = params
    answer = http("POST", k["url"] + "/jsonrpc", {"Authorization": "Basic " + auth}, body)
    if answer and answer.get("error"):
        raise RuntimeError(f"Kodi {method}: {answer['error']}")
    return (answer or {}).get("result")


def wake_tv(cfg):
    ha = cfg["ha"]
    http("POST", ha["url"] + "/api/services/remote/turn_on", {"Authorization": "Bearer " + read(ha["token_file"])},
         {"entity_id": ha["remote_entity"], "activity": "org.xbmc.kodi"})


def play(cfg, content_id, sleep=time.sleep):
    urls = stream_urls(content_id, cfg["public_engine_url"])
    try:
        http("GET", cfg["engine_url"] + "/webui/api/service?method=get_version")
    except Exception as e:
        log(f"engine check failed: {e}")
        return {"ok": False, "message": "Ace Stream engine isn't running on the server.", "urls": urls}
    note = ""
    if cfg["ha"].get("remote_entity"):
        try:
            wake_tv(cfg)
        except Exception as e:
            log(f"waking the TV failed: {e}")
            note = "The TV couldn't be woken through Home Assistant. "
    for _ in range(20):  # 20 × 2 s: Kodi needs a while after the TV wakes up
        try:
            kodi(cfg, "JSONRPC.Ping")
            break
        except Exception:
            sleep(2)
    else:
        return {"ok": False, "urls": urls,
                "message": note + "Kodi didn't answer. Is the TV on and Kodi allowed remote control?"}
    try:
        kodi(cfg, "Player.Open", {"item": {"file": urls["tv"]}})
    except Exception as e:
        log(f"Player.Open failed: {e}")
        return {"ok": False, "message": note + "Kodi refused to play the stream.", "urls": urls}
    log(f"playing {content_id} on the TV")
    return {"ok": True, "message": note + "Playing on the TV. It may take up to a minute to start.", "urls": urls}


def chromecast(cfg, content_id):
    """Cast the HLS stream to the TV's built-in Chromecast through Home Assistant; no Kodi needed"""
    urls = stream_urls(content_id, cfg["public_engine_url"])
    entity = cfg["ha"].get("cast_entity")
    if not entity:
        return {"ok": False, "message": "No Chromecast set up for the TV.", "urls": urls}
    try:
        http("GET", cfg["engine_url"] + "/webui/api/service?method=get_version")
    except Exception as e:
        log(f"engine check failed: {e}")
        return {"ok": False, "message": "Ace Stream engine isn't running on the server.", "urls": urls}
    ha = cfg["ha"]
    try:
        http("POST", ha["url"] + "/api/services/media_player/play_media",
             {"Authorization": "Bearer " + read(ha["token_file"])},
             {"entity_id": entity, "media_content_type": "application/vnd.apple.mpegurl",
              "media_content_id": urls["hls"]}, timeout=15)
    except Exception as e:
        log(f"cast failed: {e}")
        return {"ok": False, "message": "Home Assistant couldn't cast to the TV.", "urls": urls}
    log(f"casting {content_id} to the TV")
    return {"ok": True, "message": "Casting to the TV. It may take up to a minute to start.", "urls": urls}


def load_recent(path):
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, ValueError):
        return []


def save_recent(path, recent):
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(recent, f)
    os.replace(tmp, path)


# ---------- web ----------

PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Football</title>
<style>
:root { color-scheme: dark; --bg: #18181b; --card: #27272a; --text: #f4f4f5; --muted: #a1a1aa; --accent: #22c55e; --bad: #f87171; }
* { box-sizing: border-box; }
body { margin: 0; padding: 16px; background: var(--bg); color: var(--text); font: 16px/1.4 system-ui, sans-serif; }
main { max-width: 520px; margin: 0 auto; display: grid; gap: 14px; }
h1 { margin: 0; font-size: 22px; }
a { color: var(--accent); }
textarea, input { width: 100%; padding: 12px; border: 1px solid #3f3f46; border-radius: 10px; background: var(--card); color: var(--text); font: inherit; }
textarea { min-height: 72px; resize: vertical; }
button { width: 100%; padding: 14px; border: 0; border-radius: 10px; background: var(--card); color: var(--text); font: inherit; font-weight: 600; }
button.main { background: var(--accent); color: #052e16; font-size: 18px; }
.row { display: grid; grid-template-columns: 1fr 1fr; gap: 10px; }
#msg { min-height: 1.4em; color: var(--muted); }
#msg.bad { color: var(--bad); }
.recent button { text-align: left; font-weight: 400; margin-top: 8px; }
.recent small { color: var(--muted); display: block; }
</style></head><body><main>
<h1>⚽ Football</h1>
<p>1. Find the match on <a href="https://search-ace.stream/" target="_blank" rel="noopener">search-ace.stream</a> and copy its ID or acestream:// link.</p>
<textarea id="text" placeholder="Paste the ID or link here"></textarea>
<input id="label" placeholder="Name (optional), e.g. Barcelona – Real">
<div class="row"><button class="main" onclick="send('/api/play', 'Waking the TV and starting Kodi…')">▶ Play in Kodi</button>
<button class="main" onclick="send('/api/cast', 'Casting to the TV…')">📺 Chromecast to TV</button></div>
<button onclick="openHls()">Open / cast (HLS)</button>
<div id="msg"></div>
<div class="recent" id="recent"></div>
</main><script>
let lastHls = null;
const msg = (text, bad) => { const m = document.getElementById("msg"); m.textContent = text; m.className = bad ? "bad" : ""; };
async function call(path, body) {
  const r = await fetch(path, {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify(body || {})});
  return r.json();
}
async function send(path, waiting) {
  const text = document.getElementById("text").value, label = document.getElementById("label").value;
  msg(waiting);
  const res = await call(path, {text, label});
  if (res.urls) lastHls = res.urls.hls;
  msg(res.message, !res.ok);
  if (res.recent) show(res.recent);
}
async function openHls() {
  const text = document.getElementById("text").value;
  if (text) { const res = await call("/api/urls", {text}); if (!res.ok) return msg(res.message, true); lastHls = res.urls.hls; }
  if (!lastHls) return msg("Paste an ID first.", true);
  window.open(lastHls, "_blank");
}
function show(recent) {
  const box = document.getElementById("recent");
  box.replaceChildren();
  if (recent.length) { const h = document.createElement("p"); h.textContent = "Recent"; box.append(h); }
  for (const r of recent) {
    const b = document.createElement("button");
    b.textContent = r.label || r.id.slice(0, 12) + "…";
    const s = document.createElement("small"); s.textContent = new Date(r.at * 1000).toLocaleString(); b.append(s);
    // fill the form; then pick Kodi or Chromecast
    b.onclick = () => { document.getElementById("text").value = r.id; document.getElementById("label").value = r.label; msg("Now pick Kodi or Chromecast."); };
    box.append(b);
  }
}
fetch("/api/recent").then(r => r.json()).then(show);
</script></body></html>
"""


def make_handler(cfg):
    class Handler(BaseHTTPRequestHandler):
        def send(self, code, payload, ctype="application/json"):
            data = payload.encode() if isinstance(payload, str) else json.dumps(payload).encode()
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def body(self):
            try:
                return json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
            except ValueError:
                return {}

        def do_GET(self):
            if self.path == "/":
                self.send(200, PAGE, "text/html; charset=utf-8")
            elif self.path == "/api/recent":
                self.send(200, load_recent(cfg["state_file"]))
            else:
                self.send(404, {"ok": False, "message": "not found"})

        def do_POST(self):
            if self.path not in ("/api/play", "/api/cast", "/api/urls"):
                return self.send(404, {"ok": False, "message": "not found"})
            req = self.body()
            content_id = parse_id(req.get("text"))
            if content_id is None:
                return self.send(400, {"ok": False, "message": "That isn't an Ace Stream ID or link."})
            if self.path == "/api/urls":
                return self.send(200, {"ok": True, "urls": stream_urls(content_id, cfg["public_engine_url"])})
            result = (play if self.path == "/api/play" else chromecast)(cfg, content_id)
            recent = add_recent(load_recent(cfg["state_file"]), content_id,
                                str(req.get("label") or "")[:80], time.time())
            save_recent(cfg["state_file"], recent)
            self.send(200, {**result, "recent": recent})

        def log_message(self, fmt, *args):
            pass  # requests aren't interesting; play() and chromecast() log what matters

    return Handler


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True)
    cfg = json.load(open(parser.parse_args().config))
    host, port = cfg["listen"].rsplit(":", 1)
    log(f"football page on {cfg['listen']}")
    ThreadingHTTPServer((host, int(port)), make_handler(cfg)).serve_forever()


if __name__ == "__main__":
    main()
