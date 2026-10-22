# scripts

| Script | Run where | What |
|---|---|---|
| `bootstrap-local.sh` | Management PC (Mint/Ubuntu), once | Runs [`bin/bootstrap_frogswamp.sh` from the dotfiles repo](https://github.com/ARtoriouSs/dotfiles/blob/master/bin/bootstrap_frogswamp.sh) (downloaded from GitHub, or the local `~/dotfiles` copy if GitHub is unreachable): installs Nix (sudo), enables flakes, direnv, creates the admin age key and SSH key, adds an `~/.ssh/config` entry, installs Tailscale (off until `ts-up`; `ts-down` turns it off again). The shell helpers come from the dotfiles repo's `frogswamp/helpers.sh`. Safe to re-run. |
| `check-secrets.sh` | Automatically, by the git pre-commit hook | Blocks committing unencrypted files in `secrets/` |
