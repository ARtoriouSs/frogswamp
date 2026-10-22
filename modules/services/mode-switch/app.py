"""Mode switch: flip a Home Assistant mode from a Homepage tile, then go back to Homepage.

GET /toggle/<name> toggles the mode's input_boolean through the HA API and redirects
to Homepage, which then shows the new state. Only the modes in the config can be
switched; anything else is a 404 and never reaches HA.
"""

import argparse
import html
import json
import re
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PATH_RE = re.compile(r"^/toggle/([a-z_]+)$")


def toggle(cfg, entity_id):
    with open(cfg["ha"]["token_file"]) as f:
        token = f.read().strip()
    req = urllib.request.Request(
        cfg["ha"]["url"] + "/api/services/input_boolean/toggle",
        data=json.dumps({"entity_id": entity_id}).encode(),
        method="POST",
        headers={"Authorization": "Bearer " + token, "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=10) as resp:
        resp.read()


def make_handler(cfg):
    back = cfg["back_url"]

    class Handler(BaseHTTPRequestHandler):
        def redirect(self):
            self.send_response(303)
            self.send_header("Location", back)
            self.send_header("Content-Length", "0")
            self.end_headers()

        def page(self, code, text):
            body = (
                "<!doctype html><meta charset=utf-8><meta name=viewport content='width=device-width'>"
                f"<p>{html.escape(text)}</p><p><a href='{html.escape(back)}'>Back to the dashboard</a></p>"
            ).encode()
            self.send_response(code)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            if self.path in ("/", ""):
                return self.redirect()
            m = PATH_RE.match(self.path)
            name = m.group(1) if m else None
            entity_id = cfg["modes"].get(name)
            if entity_id is None:
                return self.page(404, "There is no such mode.")
            try:
                toggle(cfg, entity_id)
            except Exception as e:  # noqa: BLE001 - any failure is shown to the person who clicked
                print(f"toggle {entity_id} failed: {e}", flush=True)
                title = name.replace("_", " ").capitalize()
                return self.page(502, f"Couldn't switch {title}: Home Assistant didn't accept it ({e}).")
            print(f"toggled {entity_id}", flush=True)
            self.redirect()

        def log_message(self, *args):
            pass

    return Handler


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True)
    cfg = json.load(open(parser.parse_args().config))
    host, port = cfg["listen"].rsplit(":", 1)
    ThreadingHTTPServer((host, int(port)), make_handler(cfg)).serve_forever()


if __name__ == "__main__":
    main()
