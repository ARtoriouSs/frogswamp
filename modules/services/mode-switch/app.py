"""Mode switch: flip a Home Assistant mode from a Homepage tile, then go back to Homepage.

GET /toggle/<name> toggles the mode's input_boolean through the HA API and redirects
to Homepage, which then shows the new state. Only the modes in the config can be
switched; anything else is a 404 and never reaches HA. A toggle must come from
Homepage (or a bookmark): any other web page gets a 403, so it can't flip a mode
with a hidden <img src=".../toggle/vacation">.
"""

import argparse
import html
import json
import re
import urllib.parse
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


def from_trusted_page(headers, trusted_hosts):
    """Whether the browser came from our own pages, not from some other site.

    Sec-Fetch-Site is sent by every current browser: same-site covers Homepage on
    another port of this server, none a bookmark or a typed URL. A cross-site
    request is fine only from a trusted host name (Homepage opened as frogswamp.local
    links to the IP). Old clients without the header (curl) are checked by Referer.
    """
    site = headers.get("Sec-Fetch-Site")
    if site in ("same-origin", "same-site", "none"):
        return True
    referer = headers.get("Referer")
    if referer is None:
        return site is None
    return urllib.parse.urlsplit(referer).hostname in trusted_hosts


def make_handler(cfg):
    back = cfg["back_url"]
    trusted_hosts = set(cfg["trusted_hosts"])

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
            if not from_trusted_page(self.headers, trusted_hosts):
                print(f"refused {entity_id}: request from another site", flush=True)
                return self.page(403, "Open the modes from the dashboard.")
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
