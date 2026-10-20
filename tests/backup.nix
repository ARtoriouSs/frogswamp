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
    server.fail("test -e /var/lib/home-assistant/home-assistant_v2.db.bak")  # cleanup ran

    # wipe state and restore (fresh-install situation)
    server.succeed("rm -rf /var/lib/home-assistant /var/lib/jellyfin")
    server.succeed("frogswamp-restore --repo local")
    server.succeed("grep -q entries /var/lib/home-assistant/.storage/core.config_entries")
    server.succeed("test \"$(sqlite3 /var/lib/home-assistant/home-assistant_v2.db 'select x from t')\" = 42")
    # Review Focus 2: ownership fixed by name, not stale uid
    server.succeed("test \"$(stat -c '%U' /var/lib/jellyfin/data/marker)\" = jellyfin")

    # Review Focus 3: refuses to overwrite live state without --force
    rc = server.execute("frogswamp-restore --repo local")[0]
    assert rc == 3, f"expected exit 3, got {rc}"
    server.succeed("frogswamp-restore --repo local --force")

    # unknown repo
    rc = server.execute("frogswamp-restore --repo nope")[0]
    assert rc == 2, f"expected exit 2, got {rc}"
  '';
}
