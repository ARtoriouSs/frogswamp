{ pkgs, ... }:
let
  # Plays Notion and Google: two meals today, one full day, and every request is logged to /srv/requests
  fake = pkgs.writeText "fake-apis.py" ''
    import datetime, http.server, json
    today = datetime.date.today().isoformat()

    def text(kind, value):
        return {"type": kind, kind: [{"plain_text": value}]}

    def meal(page_id, product, kcal):
        return {"id": page_id, "properties": {
            "Product": text("title", product),
            "Eaten": {"date": {"start": today + "T12:00:00+01:00"}},
            "Meal": {"select": {"name": "Обед"}},
            "Kcal": {"number": kcal}, "Protein": {"number": 10}, "Fat": {"number": 5}, "Carbs": {"number": 20}}}

    created = []

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_POST(self):
            raw = self.rfile.read(int(self.headers.get("Content-Length") or 0)).decode()
            with open("/srv/requests", "a") as log:
                log.write(json.dumps({"path": self.path, "auth": self.headers.get("Authorization"), "body": raw}) + "\n")
            if self.path == "/v1/data_sources/meals-id/query":
                body = {"results": [meal("m1", "Secret soup", 300), meal("m2", "Secret bread", 150)], "has_more": False}
            elif self.path == "/v1/data_sources/days-id/query":
                body = {"results": [{"id": "d", "properties": {"Date": text("title", today), "Full": {"checkbox": True}}}], "has_more": False}
            elif self.path == "/token":
                body = {"access_token": "fake-access", "expires_in": 3599}
            elif self.path.endswith("/dataPoints"):
                created.append(1)
                body = {"done": True, "response": {"name": f"users/1/dataTypes/nutrition-log/dataPoints/{len(created)}"}}
            else:
                self.send_error(404)
                return
            data = json.dumps(body).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

    http.server.HTTPServer(("127.0.0.1", 9100), Handler).serve_forever()
  '';
in
pkgs.testers.runNixOSTest {
  name = "nutrition-service";
  nodes.server = _: {
    imports = [ ../modules ];
    environment.etc = {
      "nt-token".text = "secret-notion-token\n";
      "nt-meals".text = "meals-id\n";
      "nt-days".text = "days-id\n";
      "gc-id".text = "client-id\n";
      "gc-secret".text = "secret-client\n";
      "gc-refresh".text = "secret-refresh\n";
    };
    frogswamp = {
      adminUser = "pupsich";
      nutrition = {
        enable = true;
        interval = 5;
        notionTokenFile = "/etc/nt-token";
        notionMealsSourceFile = "/etc/nt-meals";
        notionDaysSourceFile = "/etc/nt-days";
        googleClientIdFile = "/etc/gc-id";
        googleClientSecretFile = "/etc/gc-secret";
        googleRefreshTokenFile = "/etc/gc-refresh";
        notionUrl = "http://127.0.0.1:9100";
        googleApiUrl = "http://127.0.0.1:9100/v4/users/me/dataTypes/nutrition-log/dataPoints";
        googleTokenUrl = "http://127.0.0.1:9100/token";
      };
    };
  };
  testScript = ''
    import json

    server.succeed("mkdir -p /srv && systemd-run --unit=fake-apis ${pkgs.python3}/bin/python3 ${fake}")
    server.wait_for_open_port(9100)
    server.systemctl("restart nutrition-sync.service")
    server.wait_for_unit("nutrition-sync.service")
    server.wait_for_open_port(8131)
    server.wait_until_succeeds("curl -fs http://127.0.0.1:8131/status | grep -q '\"pending\": 0'", timeout=60)

    status = json.loads(server.succeed("curl -fs http://127.0.0.1:8131/status"))
    assert status["today"]["kcal"] == 450, status
    assert status["google"]["ok"], status

    csv = server.succeed("cat /var/lib/nutrition-sync/www/daily.csv")
    assert csv.splitlines()[0] == "date,calories,fats,proteins,carbohydrates,full?", csv
    assert csv.splitlines()[1].endswith(",450,10,20,40,TRUE"), csv

    # Two creates, and not again on the next reads
    server.sleep(12)
    requests = [json.loads(line) for line in server.succeed("cat /srv/requests").splitlines()]
    creates = [r for r in requests if r["path"].endswith("/dataPoints")]
    assert len(creates) == 2, creates
    assert all(r["auth"] == "Bearer fake-access" for r in creates)
    notion = [r for r in requests if r["path"].startswith("/v1/")]
    assert all(r["auth"] == "Bearer secret-notion-token" for r in notion)

    # Only on localhost, not as root, and no secrets or meal names in the log
    server.fail("ss -ltn | grep ':8131' | grep -v '127.0.0.1'")
    server.succeed("test \"$(systemctl show -p MainPID --value nutrition-sync | xargs ps -o user= -p)\" = nutrition-sync")
    for leak in ["secret-notion-token", "secret-refresh", "secret-client", "fake-access", "Secret soup"]:
        server.fail(f"journalctl -u nutrition-sync | grep -q '{leak}'")
  '';
}
