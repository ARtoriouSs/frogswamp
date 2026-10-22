#!/usr/bin/env bash
# Prepares this PC to manage the frogswamp server. The real script lives in the dotfiles repo
# (bin/bootstrap_frogswamp.sh), together with the shell helpers it relies on (ts-up, ts-down).
# It's downloaded from GitHub so a fresh PC gets the current version; if GitHub can't be
# reached, the local ~/dotfiles copy is used. Safe to re-run.
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
url="https://raw.githubusercontent.com/ARtoriouSs/dotfiles/master/bin/bootstrap_frogswamp.sh"
local_copy="$HOME/dotfiles/bin/bootstrap_frogswamp.sh"

script="$(mktemp)"
trap 'rm -f "$script"' EXIT
if curl -fsSL --max-time 30 "$url" -o "$script"; then
  echo "Using $url"
elif [ -f "$local_copy" ]; then
  echo "GitHub unreachable, using $local_copy" >&2
  cp "$local_copy" "$script"
else
  echo "Couldn't download $url and there is no $local_copy." >&2
  echo "Clone the dotfiles repo to ~/dotfiles (see its README), then run this again." >&2
  exit 1
fi
bash "$script" "$repo_root"
