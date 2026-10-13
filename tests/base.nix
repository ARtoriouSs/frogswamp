{ pkgs, ... }:
let
  sshKeys = import "${pkgs.path}/nixos/tests/ssh-keys.nix" pkgs;
in
pkgs.testers.runNixOSTest {
  name = "base";
  nodes = {
    server =
      { lib, ... }:
      {
        imports = [ ../modules ];
        virtualisation.vlans = [ 1 ];
        networking.interfaces.eth1.ipv4.addresses = lib.mkForce [ ];
        frogswamp = {
          adminUser = "pupsich";
          adminSshKeys = [ sshKeys.snakeOilPublicKey ];
          lan = {
            address = "192.168.1.10";
            prefixLength = 24;
            gateway = "192.168.1.254";
            subnet = "192.168.1.0/24";
            matchConfig = {
              Name = "eth1";
            };
          };
        };
      };
    client =
      { pkgs, ... }:
      {
        virtualisation.vlans = [ 1 ];
        environment.systemPackages = [ pkgs.netcat ];
      };
  };
  testScript = ''
    start_all()
    server.wait_for_unit("multi-user.target")
    server.wait_for_unit("sshd.service")
    server.succeed("ip -4 addr show eth1 | grep -q 'inet 192.168.1.10/24'")
    server.succeed("ip route | grep -q 'default via 192.168.1.254'")

    client.wait_for_unit("multi-user.target")
    client.succeed("mkdir -p -m 700 /root/.ssh")
    client.succeed("cp ${sshKeys.snakeOilPrivateKey} /root/.ssh/id_key && chmod 600 /root/.ssh/id_key")
    ssh = "ssh -i /root/.ssh/id_key -o StrictHostKeyChecking=no -o BatchMode=yes"
    client.succeed(f"{ssh} pupsich@192.168.1.10 true")
    client.succeed(f"{ssh} root@192.168.1.10 true")
    # password logins are disabled
    client.fail("ssh -o StrictHostKeyChecking=no -o BatchMode=yes -o PreferredAuthentications=password pupsich@192.168.1.10 true")
    # no service ports open by default
    client.fail("nc -z -w 2 192.168.1.10 8123")
  '';
}
