{ pkgs, ... }:
pkgs.testers.runNixOSTest {
  name = "backup";
  nodes.server =
    { pkgs, ... }:
    {
      imports = [ ../modules ];
      # /dev/vdb: the backup disk, unformatted = "disk absent"; /dev/vdc: some other USB disk
      virtualisation.emptyDiskImages = [
        512
        64
      ];
      # Test VMs replace fileSystems with their own, so declare the backup mount the VM way
      virtualisation.fileSystems."/mnt/backup" = {
        device = "/dev/disk/by-label/frogbackup";
        fsType = "ext4";
        options = [
          "nofail"
          "noatime"
          "x-systemd.device-timeout=5s"
        ];
      };
      environment.systemPackages = [ pkgs.e2fsprogs ];
      # stand-in for Home Assistant: records the states the backup status job posts
      systemd.services = {
        fake-ha = {
          wantedBy = [ "multi-user.target" ];
          serviceConfig.ExecStart = "${pkgs.python3}/bin/python3 ${pkgs.writeText "fake-ha.py" ''
            import http.server
            class H(http.server.BaseHTTPRequestHandler):
                def do_POST(self):
                    body = self.rfile.read(int(self.headers["Content-Length"])).decode()
                    with open("/tmp/ha-posts", "a") as f:
                        f.write(self.path + " " + self.headers["Authorization"] + " " + body + "\n")
                    self.send_response(200)
                    self.end_headers()
            http.server.HTTPServer(("127.0.0.1", 8123), H).serve_forever()
          ''}";
        };
        # stand-ins: an app the restore must restart, and a helper it must stop meanwhile
        jellyfin = {
          wantedBy = [ "multi-user.target" ];
          serviceConfig.ExecStart = "${pkgs.coreutils}/bin/sleep infinity";
        };
        media-provision = {
          wantedBy = [ "multi-user.target" ];
          serviceConfig.ExecStart = "${pkgs.coreutils}/bin/sleep infinity";
        };
      };
      users.users.jellyfin = {
        isSystemUser = true;
        group = "jellyfin";
      };
      users.groups.jellyfin = { };
      frogswamp = {
        adminUser = "pupsich";
        backup = {
          passwordFile = toString (pkgs.writeText "restic-pw" "test-restic-pw");
          local.enable = true;
          haTokenFile = toString (pkgs.writeText "ha-token" "test-token");
        };
      };
    };
  testScript = ''
    server.wait_for_unit("multi-user.target")

    # Review Focus 1: no disk -> job fails, nothing written to the root fs
    server.fail("systemctl start restic-backups-local.service")
    server.fail("test -e /mnt/backup/restic/config")

    # another disk (a USB stick, a second drive) is plugged in: nothing is mounted
    server.succeed("mkfs.ext4 -q -L usbstick /dev/vdc && udevadm settle")
    server.succeed("udevadm trigger --action=add --settle /dev/vdc")
    server.sleep(3)
    server.fail("mountpoint -q /mnt/backup")
    server.fail("findmnt -n -S /dev/vdc")

    # the backup disk is plugged in after boot: it is mounted by itself
    server.succeed("mkfs.ext4 -q -L frogbackup /dev/vdb && udevadm settle")
    server.succeed("udevadm trigger --action=add --settle /dev/vdb")
    server.wait_until_succeeds("mountpoint -q /mnt/backup", timeout=30)
    server.succeed("findmnt -n -S /dev/vdb -T /mnt/backup")

    # seed state: HA storage, sqlite DB, jellyfin file owned by a stale uid
    server.succeed("mkdir -p /var/lib/home-assistant/.storage /var/lib/jellyfin/data")
    server.succeed("echo entries > /var/lib/home-assistant/.storage/core.config_entries")
    server.succeed("sqlite3 /var/lib/home-assistant/home-assistant_v2.db 'create table t(x); insert into t values (42);'")
    server.succeed("echo lib > /var/lib/jellyfin/data/marker && chown -R 4242:4242 /var/lib/jellyfin")

    server.succeed("systemctl start restic-backups-local.service")
    server.succeed("restic-local snapshots --json | grep -q '\"paths\"'")
    # the job saved its own DB copy, then removed it
    server.succeed("restic-local ls latest | grep 'home-assistant_v2.db.snapshot-local$' >/dev/null")
    server.fail("restic-local ls latest | grep 'home-assistant_v2.db$' >/dev/null")
    server.fail("test -e /var/lib/home-assistant/home-assistant_v2.db.snapshot-local")

    # wipe state and restore (fresh-install situation)
    server.succeed("rm -rf /var/lib/home-assistant /var/lib/jellyfin")
    # a fresh app left its own journal files next to the DB
    server.succeed("mkdir -p /var/lib/home-assistant && touch /var/lib/home-assistant/home-assistant_v2.db-wal /var/lib/home-assistant/home-assistant_v2.db-shm")
    app_pid = server.succeed("systemctl show -p MainPID --value jellyfin.service").strip()
    server.succeed("frogswamp-restore --repo local")
    server.succeed("grep -q entries /var/lib/home-assistant/.storage/core.config_entries")
    server.succeed("test \"$(sqlite3 /var/lib/home-assistant/home-assistant_v2.db 'select x from t')\" = 42")
    server.fail("test -e /var/lib/home-assistant/home-assistant_v2.db-wal")
    server.fail("test -e /var/lib/home-assistant/home-assistant_v2.db.snapshot-local")
    # apps are restarted (not left running on replaced files), helpers were stopped and are back
    assert server.succeed("systemctl show -p MainPID --value jellyfin.service").strip() != app_pid
    server.succeed("journalctl -u media-provision.service | grep -q Stopped")
    server.succeed("systemctl is-active media-provision.service")
    # Review Focus 2: ownership fixed by name, not stale uid
    server.succeed("test \"$(stat -c '%U' /var/lib/jellyfin/data/marker)\" = jellyfin")

    # Review Focus 3: refuses to overwrite live state without --force
    rc = server.execute("frogswamp-restore --repo local")[0]
    assert rc == 3, f"expected exit 3, got {rc}"
    server.succeed("frogswamp-restore --repo local --force")

    # dashboard status: disk mounted, latest local backup time
    server.wait_for_unit("fake-ha.service")
    server.succeed("rm -f /tmp/ha-posts && systemctl start backup-status.service")
    server.succeed("grep 'sensor.backup_disk Bearer test-token' /tmp/ha-posts | grep -q '\"state\":\"mounted\"'")
    server.succeed("grep 'sensor.backup_last_local' /tmp/ha-posts | grep -qE '\"state\":\"20[0-9]{2}-'")

    # backup disk missing: exit 4, so rebuild-from-scratch tries B2
    server.succeed("systemctl stop mnt-backup.mount")
    rc = server.execute("frogswamp-restore --repo local --force")[0]
    assert rc == 4, f"expected exit 4, got {rc}"
    server.succeed("rm -f /tmp/ha-posts && systemctl start backup-status.service")
    server.succeed("grep 'sensor.backup_disk' /tmp/ha-posts | grep -q '\"state\":\"missing\"'")

    # unknown repo
    rc = server.execute("frogswamp-restore --repo nope")[0]
    assert rc == 2, f"expected exit 2, got {rc}"
  '';
}
