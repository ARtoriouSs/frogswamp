{ pkgs, ... }:
let
  # stands in for Home Assistant: records each call; answers 500 while /tmp/ha-down exists
  fakeHa = pkgs.writeText "fake-ha.py" ''
    import http.server, os
    class H(http.server.BaseHTTPRequestHandler):
        def do_POST(self):
            body = self.rfile.read(int(self.headers["Content-Length"])).decode()
            with open("/tmp/ha-calls", "a") as f:
                f.write(f"{self.path} {self.headers.get('Authorization')} {body}\n")
            self.send_response(500 if os.path.exists("/tmp/ha-down") else 200)
            self.end_headers()
            self.wfile.write(b"[]")
    http.server.HTTPServer(("127.0.0.1", 8123), H).serve_forever()
  '';
in
pkgs.testers.runNixOSTest {
  name = "mode-switch";
  nodes.server = {
    imports = [ ../modules ];
    systemd.services.fake-ha = {
      wantedBy = [ "multi-user.target" ];
      serviceConfig.ExecStart = "${pkgs.python3}/bin/python3 ${fakeHa}";
    };
    frogswamp = {
      adminUser = "pupsich";
      modeSwitch = {
        enable = true;
        haTokenFile = toString (pkgs.writeText "ha-token" "token");
        modes = {
          vacation = "input_boolean.vacation";
          day_off = "input_boolean.day_off";
        };
      };
    };
  };
  testScript = ''
    server.wait_for_unit("mode-switch.service")
    server.wait_for_unit("fake-ha.service")
    server.wait_for_open_port(8123)
    server.wait_for_open_port(8094)
    homepage = "http://localhost:8082"  # the VM has no LAN address

    # a click toggles the mode in HA and sends the browser back to Homepage
    out = server.succeed("curl -s -o /dev/null -w '%{http_code} %{redirect_url}' localhost:8094/toggle/vacation")
    assert out == f"303 {homepage}/", out
    calls = server.succeed("cat /tmp/ha-calls")
    assert '/api/services/input_boolean/toggle Bearer token {"entity_id": "input_boolean.vacation"}' in calls, calls

    # only configured modes can be switched, and nothing reaches HA for the rest
    server.succeed("rm /tmp/ha-calls")
    assert server.succeed("curl -s -o /dev/null -w '%{http_code}' localhost:8094/toggle/tuya_mode") == "404"
    assert server.succeed("curl -s -o /dev/null -w '%{http_code}' localhost:8094/toggle/../../api") == "404"
    server.fail("test -e /tmp/ha-calls")

    # HA failing: a page that says so, with a way back, instead of a silent redirect
    server.succeed("touch /tmp/ha-down")
    page = server.succeed("curl -s -w ' %{http_code}' localhost:8094/toggle/day_off")
    assert page.endswith(" 502") and "Day off" in page and homepage in page, page

    # the root goes to Homepage too
    assert server.succeed("curl -s -o /dev/null -w '%{http_code}' localhost:8094/") == "303"
    server.succeed("iptables -S | grep -q 'dport 8094'")
  '';
}
