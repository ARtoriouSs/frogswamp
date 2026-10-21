{ pkgs, ... }:
let
  id = "dd1e67078381739d14beca697356ab76d49d1a2b";
  # one stand-in for the engine (6878), Kodi (8080) and HA (8123): records each request
  fake = pkgs.writeText "fake.py" ''
    import http.server, json, sys
    port = int(sys.argv[1])
    class H(http.server.BaseHTTPRequestHandler):
        def answer(self, body):
            data = json.dumps(body).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
        def do_GET(self):
            with open(f"/tmp/calls-{port}", "a") as f:
                f.write("GET " + self.path + "\n")
            self.answer({"result": {"version": "3.2"}})
        def do_POST(self):
            body = self.rfile.read(int(self.headers["Content-Length"])).decode()
            with open(f"/tmp/calls-{port}", "a") as f:
                f.write("POST " + self.path + " " + body + "\n")
            if port == 8080:
                method = json.loads(body)["method"]
                self.answer({"jsonrpc": "2.0", "id": 1, "result": [] if method == "Player.GetActivePlayers" else "OK"})
            else:
                self.answer([])
    http.server.HTTPServer(("127.0.0.1", port), H).serve_forever()
  '';
in
pkgs.testers.runNixOSTest {
  name = "football";
  nodes.server = {
    imports = [ ../modules ];
    systemd.services = pkgs.lib.genAttrs [ "fake-6878" "fake-8080" "fake-8123" ] (name: {
      wantedBy = [ "multi-user.target" ];
      serviceConfig.ExecStart = "${pkgs.python3}/bin/python3 ${fake} ${pkgs.lib.removePrefix "fake-" name}";
    });
    frogswamp = {
      adminUser = "pupsich";
      football = {
        enable = true;
        kodi = {
          host = "127.0.0.1";
          passwordFile = toString (pkgs.writeText "kodi-pw" "kodipw");
        };
        haTokenFile = toString (pkgs.writeText "ha-token" "token");
        tvRemoteEntity = "remote.tv";
      };
    };
  };
  testScript = ''
    import json
    server.wait_for_unit("football.service")
    for p in (6878, 8080, 8123):
        server.wait_for_open_port(p)
    server.wait_for_open_port(8093)
    server.succeed("curl -sf localhost:8093/ | grep -q 'Play on TV'")

    # a link from the phone: the TV is woken, Kodi gets the engine's stream URL
    out = json.loads(server.succeed("""curl -sf -X POST -d '{"text": "acestream://${id}", "label": "derby"}' localhost:8093/api/play"""))
    assert out["ok"], out
    server.succeed("grep -q 'remote/turn_on.*org.xbmc.kodi' /tmp/calls-8123")
    server.succeed("grep 'Player.Open' /tmp/calls-8080 | grep -q ':6878/ace/getstream?id=${id}'")

    # junk is refused and never reaches Kodi
    before = server.succeed("wc -l < /tmp/calls-8080").strip()
    server.succeed("""test "$(curl -s -o /dev/null -w '%{http_code}' -X POST -d '{"text": "hello"}' localhost:8093/api/play)" = 400""")
    assert server.succeed("wc -l < /tmp/calls-8080").strip() == before

    # the recent list survives a restart
    server.succeed("systemctl restart football.service")
    server.wait_for_open_port(8093)
    recent = json.loads(server.succeed("curl -sf localhost:8093/api/recent"))
    assert recent[0]["id"] == "${id}" and recent[0]["label"] == "derby", recent
  '';
}
