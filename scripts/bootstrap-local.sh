#!/usr/bin/env bash
# Prepares this PC (Linux Mint / Ubuntu / Debian) to manage the frogswamp server.
# Safe to re-run: every step is skipped when already done.
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ssh_key="$HOME/.ssh/frogswamp_ed25519"
age_key="$HOME/.config/sops/age/keys.txt"
nix_conf="$HOME/.config/nix/nix.conf"
direnvrc="$HOME/.config/direnv/direnvrc"

say()  { printf '\n\033[1;32m==>\033[0m %s\n' "$*"; }
skip() { printf '    \033[2mok: %s\033[0m\n' "$*"; }
warn() { printf '\033[1;33m!!\033[0m %s\n' "$*"; }

if [ "$(id -u)" -eq 0 ]; then
  echo "Run as your normal user, not root (sudo is requested when needed)." >&2
  exit 1
fi
if ! grep -qiE '^(ID|ID_LIKE)=.*(debian|ubuntu)' /etc/os-release; then
  echo "Only Debian/Ubuntu-based systems (e.g. Linux Mint) are supported." >&2
  exit 1
fi

say "1/6 Nix"
if command -v nix >/dev/null 2>&1 || [ -x /nix/var/nix/profiles/default/bin/nix ]; then
  skip "Nix is installed"
else
  installer="$(mktemp)"
  curl --proto '=https' --tlsv1.2 -sSfL https://nixos.org/nix/install -o "$installer"
  sh "$installer" --daemon --yes
  rm -f "$installer"
fi
if ! command -v nix >/dev/null 2>&1; then
  # shellcheck disable=SC1091
  . /nix/var/nix/profiles/default/etc/profile.d/nix-daemon.sh
fi

say "2/6 Flakes"
if grep -qs '^experimental-features.*flakes' "$nix_conf"; then
  skip "flakes enabled in $nix_conf"
else
  mkdir -p "$(dirname "$nix_conf")"
  echo 'experimental-features = nix-command flakes' >>"$nix_conf"
  skip "enabled flakes in $nix_conf"
fi

say "3/6 direnv + nix-direnv"
if nix profile list 2>/dev/null | grep -q 'nix-direnv'; then
  skip "direnv and nix-direnv installed"
else
  nix profile install nixpkgs#direnv nixpkgs#nix-direnv
fi
mkdir -p "$(dirname "$direnvrc")"
if grep -qs 'nix-direnv/direnvrc' "$direnvrc"; then
  skip "nix-direnv sourced in $direnvrc"
else
  # shellcheck disable=SC2016 # literal $HOME belongs in direnvrc
  echo 'source "$HOME/.nix-profile/share/nix-direnv/direnvrc"' >>"$direnvrc"
fi
for shell in zsh bash; do
  rc="$HOME/.${shell}rc"
  [ -e "$rc" ] || continue
  target="$(readlink -f "$rc")" # edit the real file; sed -i would replace a symlink
  if grep -qs '# frogswamp-shell-begin' "$target"; then
    skip "frogswamp shell block present in $rc"
    continue
  fi
  # Migrate the single-line hook written by an earlier version of this script.
  if grep -qs '# frogswamp: load per-directory dev environments' "$target"; then
    sed -i '/# frogswamp: load per-directory dev environments/{N;d;}' "$target"
  fi
  if grep -qs 'direnv hook' "$target"; then
    skip "user-managed direnv hook present in $rc"
    continue
  fi
  if [ "$target" != "$rc" ]; then
    warn "$rc is a symlink; the frogswamp block is added to $target."
  fi
  # Nix's profile script skips itself when __ETC_PROFILE_NIX_SOURCED is inherited from a
  # parent process even though PATH was rebuilt without Nix, so clear the flag first.
  cat >>"$target" <<EOF

# frogswamp-shell-begin (managed by frogswamp/scripts/bootstrap-local.sh)
if [ -e /nix/var/nix/profiles/default/etc/profile.d/nix-daemon.sh ]; then
  unset __ETC_PROFILE_NIX_SOURCED
  . /nix/var/nix/profiles/default/etc/profile.d/nix-daemon.sh
fi
command -v direnv >/dev/null 2>&1 && eval "\$(direnv hook ${shell})"
# frogswamp-shell-end
EOF
  skip "added frogswamp shell block to $target"
done

say "4/6 Admin age key (decrypts repo secrets)"
if [ -s "$age_key" ]; then
  skip "age key exists at $age_key"
else
  mkdir -p "$(dirname "$age_key")"
  nix shell nixpkgs#age --command age-keygen -o "$age_key"
  chmod 600 "$age_key"
  warn "NEW age key created at $age_key"
  warn "STORE ITS CONTENT IN YOUR PASSWORD MANAGER NOW. Without it (and the server) all secrets are lost."
fi

say "5/6 Admin SSH key"
if [ -s "$ssh_key" ]; then
  skip "SSH key exists at $ssh_key"
else
  mkdir -p "$HOME/.ssh"
  ssh-keygen -q -t ed25519 -N "" -C "frogswamp-admin" -f "$ssh_key"
fi
mkdir -p "$repo_root/keys"
if cmp -s "$ssh_key.pub" "$repo_root/keys/admin.pub"; then
  skip "keys/admin.pub up to date"
else
  cp "$ssh_key.pub" "$repo_root/keys/admin.pub"
  skip "copied public key to keys/admin.pub"
fi
if grep -qs '# frogswamp-begin' "$HOME/.ssh/config"; then
  skip "frogswamp block present in $HOME/.ssh/config"
else
  cat >>"$HOME/.ssh/config" <<EOF

# frogswamp-begin
Host frogswamp 192.168.101.10 frogswamp.local frogswamp-installer.local
  IdentityFile $ssh_key
  IdentitiesOnly yes
# frogswamp-end
EOF
  chmod 600 "$HOME/.ssh/config"
  skip "added frogswamp block to ~/.ssh/config"
fi

say "6/6 Done. Next steps:"
cat <<'EOF'
  1. Open a new terminal (loads Nix + direnv).
  2. cd into the repo and run:  direnv allow   (once the repo contains .envrc)
  3. Run:  just     (lists all commands)
EOF
