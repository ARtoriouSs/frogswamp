set shell := ["bash", "-euo", "pipefail", "-c"]

host := env_var_or_default("FROGSWAMP_HOST", "192.168.101.10")

# List commands
default:
    @just --list

# Run all checks (flake checks + HA config check once HA exists)
check:
    nix flake check -L

# Edit encrypted secrets
secrets:
    sops secrets/frogswamp.yaml

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
