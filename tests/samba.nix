{ pkgs, ... }:
pkgs.testers.runNixOSTest {
  name = "samba";
  nodes = {
    server =
      { lib, pkgs, ... }:
      {
        imports = [ ../modules ];
        virtualisation.vlans = [ 1 ];
        virtualisation.diskSize = 4096;
        # Our catch-all 10-lan unit would otherwise replace the test framework's IP on eth1
        networking.interfaces.eth1.ipv4.addresses = lib.mkForce [ ];
        frogswamp = {
          adminUser = "pupsich";
          lan = {
            address = "192.168.1.2"; # the address the test framework maps to "server"
            gateway = "192.168.1.254";
            matchConfig.Name = "eth1";
          };
          jellyfin.enable = true;
          samba = {
            enable = true;
            passwordFile = toString (pkgs.writeText "smbpw" "s3cret-pw");
          };
        };
      };
    client =
      { pkgs, ... }:
      {
        virtualisation.vlans = [ 1 ];
        environment.systemPackages = [ pkgs.samba ];
      };
  };
  testScript = ''
    start_all()
    server.wait_for_unit("samba-smbd.service")
    server.wait_for_unit("samba-set-password.service")
    server.wait_for_open_port(445)
    client.wait_for_unit("multi-user.target")
    client.succeed("echo hello > /tmp/hello.txt")
    client.succeed("smbclient //server/media -U 'pupsich%s3cret-pw' -c 'put /tmp/hello.txt hello.txt'")
    server.succeed("test \"$(stat -c '%G' /srv/media/hello.txt)\" = media")
    client.fail("smbclient //server/media -U 'pupsich%wrong' -c 'ls'")
    client.fail("smbclient //server/media -N -c 'ls'")
  '';
}
