# scripts

| Script | Run where | What |
|---|---|---|
| `bootstrap-local.sh` | Management PC (Mint/Ubuntu), once | Installs Nix (sudo), enables flakes, direnv, creates the admin age key and SSH key, adds an `~/.ssh/config` entry. Safe to re-run. |
| `check-secrets.sh` | Automatically, by the git pre-commit hook | Blocks committing unencrypted files in `secrets/` |
