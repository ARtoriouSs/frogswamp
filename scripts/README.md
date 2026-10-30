# scripts

| Script | Run where | What |
|---|---|---|
| `bootstrap-local.sh` | Management PC (Mint/Ubuntu), once | Installs Nix (sudo), enables flakes, direnv, creates the admin age key and SSH key, adds an `~/.ssh/config` entry, installs Tailscale (off until `ts-up`; `ts-down` turns it off again). The shell helpers come from the [dotfiles repo's `frogswamp/helpers.sh`](https://github.com/ARtoriouSs/dotfiles/blob/master/frogswamp/helpers.sh), which `bin/bootstrap_frogswamp.sh` there also runs this script from GitHub. Safe to re-run. |
| `check-secrets.sh` | Automatically, by the git pre-commit hook | Blocks committing unencrypted files in `secrets/` |
