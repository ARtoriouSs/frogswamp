{ pkgs, ... }:
pkgs.testers.runNixOSTest {
  name = "mqtt";
  nodes.server =
    { pkgs, ... }:
    {
      imports = [ ../modules ];
      environment.systemPackages = [ pkgs.mosquitto ];
      frogswamp = {
        adminUser = "pupsich";
        mqtt = {
          enable = true;
          haPasswordFile = toString (pkgs.writeText "ha" "ha-secret");
          z2mPasswordFile = toString (pkgs.writeText "z2m" "z2m-secret");
          zigbee2mqtt.enable = false; # no dongle in the VM
        };
      };
    };
  testScript = ''
    server.wait_for_unit("mosquitto.service")
    server.wait_for_open_port(1883)
    pub = "mosquitto_pub -h 127.0.0.1 -t test/topic -m hi"
    server.succeed(f"{pub} -u ha -P ha-secret")
    server.succeed(f"{pub} -u z2m -P z2m-secret")
    server.fail(f"{pub}")                        # anonymous rejected
    server.fail(f"{pub} -u ha -P wrong")         # bad password rejected
    # listens only on localhost
    server.fail("ss -ltn | grep ':1883' | grep -v '127.0.0.1'")
  '';
}
