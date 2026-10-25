set shell := ["bash", "-euo", "pipefail", "-c"]

host := env_var_or_default("FROGSWAMP_HOST", "192.168.101.10")

# List commands
default:
    @just --list

# Run all checks (flake checks + HA config check once HA exists)
check:
    nix flake check -L
    just ha-check

# Edit encrypted secrets (in nvim unless SOPS_EDITOR says otherwise)
secrets:
    #!/usr/bin/env bash
    set -euo pipefail
    if [ -z "${SOPS_EDITOR:-}" ] && command -v nvim >/dev/null; then export SOPS_EDITOR=nvim; fi
    sops secrets/frogswamp.yaml

# Print one secret, e.g. `just secret mosquitto/ha_password`
secret path:
    #!/usr/bin/env bash
    set -euo pipefail
    key="$(awk -F/ '{for (i = 1; i <= NF; i++) printf "[\"%s\"]", $i}' <<<'{{path}}')"
    sops -d --extract "$key" secrets/frogswamp.yaml
    echo

# One-time: create the server's SSH host key (its sops identity) and .sops.yaml
gen-host-key:
    #!/usr/bin/env bash
    set -euo pipefail
    if [ -e secrets/ssh_host_ed25519_key ]; then echo "secrets/ssh_host_ed25519_key already exists; refusing to overwrite." >&2; exit 1; fi
    tmp="$(mktemp -d)"; trap 'rm -rf "$tmp"' EXIT
    ssh-keygen -q -t ed25519 -N "" -C "root@frogswamp" -f "$tmp/key"
    admin_age="$(age-keygen -y "$HOME/.config/sops/age/keys.txt")"
    host_age="$(ssh-to-age < "$tmp/key.pub")"
    cat > .sops.yaml <<EOF
    keys:
      - &admin $admin_age
      - &frogswamp $host_age
    creation_rules:
      - path_regex: ^secrets/[^/]+$
        key_groups:
          - age:
              - *admin
              - *frogswamp
    EOF
    cp "$tmp/key.pub" keys/ssh_host_ed25519_key.pub
    sops --encrypt --input-type binary --output-type binary \
      --filename-override secrets/ssh_host_ed25519_key "$tmp/key" > secrets/ssh_host_ed25519_key
    git add --intent-to-add .sops.yaml keys/ssh_host_ed25519_key.pub secrets/ssh_host_ed25519_key
    echo "Host key created. Public age recipient: $host_age"

# Build the system locally
build:
    nix build .#nixosConfigurations.frogswamp.config.system.build.toplevel -L

# Show what would change on the server
diff: build
    nix copy --to ssh://root@{{host}} ./result
    ssh root@{{host}} nix store diff-closures /run/current-system "$(readlink -f result)"

# Apply the config to the server
deploy: diff
    nixos-rebuild switch --flake .#frogswamp --target-host root@{{host}}

# Apply without making it the boot default (a reboot undoes it)
try: diff
    nixos-rebuild test --flake .#frogswamp --target-host root@{{host}}

# Switch the server back to the previous generation
rollback:
    ssh root@{{host}} 'nix-env -p /nix/var/nix/profiles/system --rollback && /nix/var/nix/profiles/system/bin/switch-to-configuration switch'

# Update all flake inputs (OS + packages), then show the diff
update:
    nix flake update
    just diff

# Build the custom installer ISO (result/iso/*.iso)
iso:
    nix build .#nixosConfigurations.installer.config.system.build.isoImage -L
    @ls -1 result/iso/*.iso

# Boot the full config in a local VM (ssh -p 2222 pupsich@localhost)
vm:
    nix build .#nixosConfigurations.frogswamp.config.system.build.vm -L
    NIX_DISK_IMAGE=/tmp/frogswamp-vm.qcow2 ./result/bin/run-frogswamp-vm

# SSH into the server as the admin user (optionally run a command: `just ssh uptime`)
ssh *args:
    ssh pupsich@{{host}} {{args}}

# WIPE the target's disk and install frogswamp (target = installer IP or frogswamp-installer.local)
install target:
    #!/usr/bin/env bash
    set -euo pipefail
    disk="$(nix eval --raw .#nixosConfigurations.frogswamp.config.frogswamp.disk)"
    ssh_opts=(-o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null)
    ssh -n "${ssh_opts[@]}" root@{{target}} "test -b $disk" || { echo "Target has no $disk. Set frogswamp.disk in hosts/frogswamp/default.nix." >&2; exit 1; }
    ssh -n "${ssh_opts[@]}" root@{{target}} lsblk -o NAME,SIZE,MODEL "$disk"
    read -r -p "This ERASES $disk on {{target}}. Type 'wipe' to continue: " answer
    [ "$answer" = "wipe" ] || { echo "Aborted."; exit 1; }
    # Serial numbers are dropped: the repo is public, and they don't change the hardware config
    ssh -n "${ssh_opts[@]}" root@{{target}} nixos-facter | jq 'walk(if type == "object" then del(.serial) else . end)' > hosts/frogswamp/facter.json
    git add --intent-to-add hosts/frogswamp/facter.json
    tmp="$(mktemp -d)"; trap 'rm -rf "$tmp"' EXIT
    install -d -m 755 "$tmp/etc/ssh"
    sops -d --input-type binary --output-type binary secrets/ssh_host_ed25519_key > "$tmp/etc/ssh/ssh_host_ed25519_key"
    chmod 600 "$tmp/etc/ssh/ssh_host_ed25519_key"
    cp keys/ssh_host_ed25519_key.pub "$tmp/etc/ssh/ssh_host_ed25519_key.pub"
    nixos-anywhere --flake .#frogswamp --extra-files "$tmp" root@{{target}}
    ssh-keygen -R {{host}} >/dev/null 2>&1 || true
    echo "Installed. The server is rebooting into the new system on {{host}}."
    echo ">>> PULL OUT THE USB STICK NOW. Don't press the power button: USB errors on the NUC's screen are normal."
    echo "Then check 'git diff hosts/frogswamp/facter.json'. On the same hardware, keep the old file: git checkout hosts/frogswamp/facter.json"

# Pin the Home Assistant image (default: current stable) by version + digest
ha-bump version="stable":
    #!/usr/bin/env bash
    set -euo pipefail
    ref="docker://ghcr.io/home-assistant/home-assistant:{{version}}"
    info="$(skopeo inspect --override-os linux --override-arch amd64 "$ref")"
    digest="$(jq -r .Digest <<<"$info")"
    ver="$(jq -r '.Labels["org.opencontainers.image.version"] // empty' <<<"$info")"
    [ -n "$ver" ] || ver="{{version}}"
    printf '{\n  # Updated by `just ha-bump`\n  image = "ghcr.io/home-assistant/home-assistant:%s@%s";\n}\n' "$ver" "$digest" > modules/services/home-assistant/image.nix
    git add --intent-to-add modules/services/home-assistant/image.nix
    echo "Pinned Home Assistant $ver ($digest)"

# Pin the Yeelight Cube Matrix integration to a commit of its repo (default: latest main)
cubematrix-bump ref="main":
    #!/usr/bin/env bash
    set -euo pipefail
    info="$(nix flake prefetch --json "github:ARtoriouSs/yeelight-cubematrix-ha/{{ref}}")"
    rev="$(jq -r .locked.rev <<<"$info")"
    hash="$(jq -r .hash <<<"$info")"
    file=modules/services/home-assistant/cubematrix.nix
    sed -i -e "s|rev = \".*\";|rev = \"$rev\";|" -e "s|hash = \".*\";|hash = \"$hash\";|" "$file"
    echo "Pinned yeelight-cubematrix-ha $rev"

# Copy UI-made automations/scripts/scenes from the server into git (they stay editable in the UI)
ha-pull:
    #!/usr/bin/env bash
    set -euo pipefail
    dir=modules/services/home-assistant/ui-snapshot
    for f in automations scripts scenes; do
      scp -q root@{{host}}:/var/lib/home-assistant/$f.yaml "$dir/$f.yaml"
    done
    just ha-check
    git --no-pager diff --stat -- "$dir"
    echo "Review with 'git diff $dir', then commit"

# Validate HA YAML with the pinned image (needs podman or docker locally)
ha-check:
    #!/usr/bin/env bash
    set -euo pipefail
    runtime="$(command -v podman || command -v docker || true)"
    if [ -z "$runtime" ]; then echo "ha-check: no podman/docker found, skipping" >&2; exit 0; fi
    image="$(nix eval --raw --file modules/services/home-assistant/image.nix image)"
    tmp="$(mktemp -d)"; trap 'rm -rf "$tmp"' EXIT
    cp -r modules/services/home-assistant/config/. "$tmp/"
    cp tests/fixtures/ha-secrets.yaml "$tmp/secrets.yaml"
    cp modules/services/home-assistant/ui-snapshot/{automations,scripts,scenes}.yaml "$tmp/"
    # check_config exits 0 even when an integration's config is invalid, so also inspect its output
    out="$("$runtime" run --rm -v "$tmp:/config" --entrypoint python3 "$image" -m homeassistant --script check_config --config /config 2>&1)" || { echo "$out"; exit 1; }
    echo "$out"
    if grep -qE 'Invalid config|Failed config|Fatal error' <<<"$out"; then echo "ha-check: invalid Home Assistant config" >&2; exit 1; fi
    echo "ha-check: configuration valid"

# One-time: format the external backup disk on the server (ERASES it)
init-backup-disk device:
    #!/usr/bin/env bash
    set -euo pipefail
    system="$(nix eval --raw .#nixosConfigurations.frogswamp.config.frogswamp.disk)"
    # refuse the system disk and its partitions, also when named by a /dev/disk/by-* link
    system="$(ssh -n root@{{host}} readlink -f "$system")"
    real="$(ssh -n root@{{host}} readlink -f {{device}})"
    parent="/dev/$(ssh -n root@{{host}} lsblk -no PKNAME "$real" 2>/dev/null | head -1)"
    if [ "$real" = "$system" ] || [ "$parent" = "$system" ]; then
      echo "{{device}} is the system disk ($system). Refusing." >&2; exit 1
    fi
    ssh -n root@{{host}} lsblk -o NAME,SIZE,MODEL,LABEL {{device}}
    read -r -p "ERASE {{device}} on the server and label it 'frogbackup'? Type 'wipe': " a; [ "$a" = "wipe" ]
    ssh -n root@{{host}} 'wipefs -a {{device}} && mkfs.ext4 -F -L frogbackup {{device}} && udevadm settle && systemctl start mnt-backup.mount && findmnt /mnt/backup'

# Run all enabled backup jobs now
backup-now:
    ssh root@{{host}} 'for u in restic-backups-local restic-backups-b2; do systemctl cat $u.service >/dev/null 2>&1 && systemctl start $u.service; done; restic-local snapshots | tail -3'

# Copy the server's backup repository (A400) to this PC as an extra copy outside the NUC
backup-pull dest="~/frogswamp-backup":
    #!/usr/bin/env bash
    set -euo pipefail
    dest="{{dest}}"; dest="${dest/#\~/$HOME}"
    mkdir -p "$dest/restic"
    rsync -a --delete --info=stats1 root@{{host}}:/mnt/backup/restic/ "$dest/restic/"
    export RESTIC_PASSWORD_COMMAND="sops -d --extract '[\"restic\"][\"password\"]' secrets/frogswamp.yaml"
    restic -r "$dest/restic" snapshots --compact | tail -3
    echo "Backup copy in $dest/restic (readable with: just secret restic/password)"

# Restore state on the server (repo: local or b2). Pass "--force" to overwrite existing state
restore repo="local" *flags="":
    ssh root@{{host}} frogswamp-restore --repo {{repo}} {{flags}}

# Reinstall from the installer USB, then restore state (local, falling back to b2)
rebuild-from-scratch target:
    #!/usr/bin/env bash
    set -euo pipefail
    just install {{target}}
    echo "Waiting for the server at {{host}}..."
    up=0
    for _ in $(seq 1 120); do
      if ssh -n -o ConnectTimeout=5 -o StrictHostKeyChecking=accept-new root@{{host}} true 2>/dev/null; then up=1; break; fi
      sleep 5
    done
    if [ "$up" -ne 1 ]; then
      echo "The server didn't come back on {{host}} within 10 minutes. Nothing was restored yet." >&2
      echo "Look at the NUC's screen (USB stick still in?). Once 'just ssh uptime' works, run: just restore local --force" >&2
      exit 1
    fi
    ssh -n root@{{host}} systemctl is-system-running --wait || true
    set +e
    # --force: the system was just installed, so its fresh (empty) app state may be overwritten
    ssh -n root@{{host}} frogswamp-restore --repo local --force
    rc=$?
    set -e
    if [ "$rc" -eq 4 ]; then
      echo "Local backup unavailable, trying B2..."
      ssh -n root@{{host}} frogswamp-restore --repo b2 --force
    elif [ "$rc" -ne 0 ]; then
      exit "$rc"
    fi
    echo "Rebuild complete. Check HA, Z2M, Jellyfin and Homepage in the browser."
