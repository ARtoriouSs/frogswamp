#!/usr/bin/env python3
"""One-time Google Health login for nutrition-sync, run on the laptop, not on the server.

Google sends the browser back to a short-lived server on 127.0.0.1, so nothing has to be reachable
from the internet. Prints only the refresh token to stdout, which goes to sops (nutrition/google_refresh_token).
With --test-write it also creates one 1 kcal entry and deletes it, to prove the write access works.
"""
import argparse
import base64
import datetime as dt
import getpass
import hashlib
import http.server
import json
import secrets
import sys
import urllib.error
import urllib.parse
import urllib.request
import webbrowser

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
API_URL = "https://health.googleapis.com/v4/users/me/dataTypes/nutrition-log/dataPoints"
SCOPES = [
    "https://www.googleapis.com/auth/googlehealth.nutrition.writeonly",
    "https://www.googleapis.com/auth/googlehealth.nutrition.readonly",
    "https://www.googleapis.com/auth/googlehealth.activity_and_fitness.readonly",
    "https://www.googleapis.com/auth/googlehealth.sleep.readonly",
    "https://www.googleapis.com/auth/googlehealth.health_metrics_and_measurements.readonly",
]


def wait_for_code(state):
    """Starts the local server, returns (redirect_uri, function that blocks until Google calls back)"""
    result = {}

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            query = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            if query.get("state", [""])[0] != state:
                self.send_error(400, "Wrong state")
                return
            result.update({key: values[0] for key, values in query.items()})
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.end_headers()
            self.wfile.write("Done, you can close this tab".encode())

        def log_message(self, *args):
            pass

    server = http.server.HTTPServer(("127.0.0.1", 0), Handler)

    def wait():
        while not result:
            server.handle_request()
        server.server_close()
        if "error" in result:
            sys.exit(f"Google answered: {result['error']}")
        return result["code"]

    return f"http://127.0.0.1:{server.server_port}", wait


def post(url, data=None, body=None, token=None):
    headers = {}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if body is not None:
        data = json.dumps(body).encode()
        headers["Content-Type"] = "application/json"
    elif data is not None:
        data = urllib.parse.urlencode(data).encode()
    request = urllib.request.Request(url, data=data, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.load(response)
    except urllib.error.HTTPError as error:
        sys.exit(f"{url} answered {error.code}: {error.read().decode(errors='replace')}")


def login(client_id, client_secret):
    verifier = secrets.token_urlsafe(64)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    state = secrets.token_urlsafe(16)
    redirect_uri, wait = wait_for_code(state)
    url = AUTH_URL + "?" + urllib.parse.urlencode(
        {
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "scope": " ".join(SCOPES),
            "access_type": "offline",
            # always show the account picker, so the right account can be chosen
            "prompt": "consent select_account",
            "state": state,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
        }
    )
    print(f"Opening the browser. If it doesn't open, go to:\n{url}", file=sys.stderr)
    webbrowser.open(url)
    code = wait()
    return post(
        TOKEN_URL,
        data={
            "client_id": client_id,
            "client_secret": client_secret,
            "code": code,
            "code_verifier": verifier,
            "grant_type": "authorization_code",
            "redirect_uri": redirect_uri,
        },
    )


def test_write(access_token):
    now = dt.datetime.now(dt.timezone.utc).replace(microsecond=0)
    # Google wants the end strictly after the start
    end = now + dt.timedelta(minutes=1)
    body = {
        "nutritionLog": {
            "interval": {
                "startTime": now.isoformat().replace("+00:00", "Z"),
                "endTime": end.isoformat().replace("+00:00", "Z"),
            },
            "foodDisplayName": "Spike test",
            "mealType": "SNACK",
            "energy": {"kcal": 1},
            "totalCarbohydrate": {"grams": 0},
            "totalFat": {"grams": 0},
            "nutrients": [{"nutrient": "PROTEIN", "quantity": {"grams": 0}}],
        }
    }
    created = post(API_URL, body=body, token=access_token)
    print(f"Create answered:\n{json.dumps(created, indent=2)}", file=sys.stderr)
    name = created.get("response", {}).get("name") or created.get("name")
    if not name:
        sys.exit("No data point name in the answer, see above")
    ask("Check the food log in the Fitbit / Google Health app, then press Enter to delete it...")
    post(API_URL + ":batchDelete", body={"names": [name]}, token=access_token)
    print(f"Deleted {name}", file=sys.stderr)


def ask(prompt):
    # input() prints the prompt to stdout, and stdout must hold only the token
    print(prompt, end="", file=sys.stderr, flush=True)
    return sys.stdin.readline()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--test-write", action="store_true")
    args = parser.parse_args()
    client_id = ask("OAuth client id: ").strip()
    client_secret = getpass.getpass("OAuth client secret: ").strip()
    tokens = login(client_id, client_secret)
    if "refresh_token" not in tokens:
        sys.exit("Google gave no refresh token. Remove the app's access at myaccount.google.com/permissions and retry")
    if args.test_write:
        test_write(tokens["access_token"])
    print(tokens["refresh_token"])


if __name__ == "__main__":
    main()
