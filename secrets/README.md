# secrets

Everything here is encrypted with [sops](https://github.com/getsops/sops) + age and safe to publish.

| File | Content |
|---|---|
| `frogswamp.yaml` | All service secrets (passwords, tokens, keys) |
| `ssh_host_ed25519_key` | The server's SSH host key = its sops identity |

**Who can decrypt** (see `/.sops.yaml`): the admin age key (`~/.config/sops/age/keys.txt` on the management PC, backed up in a password manager) and the server (via its host key).

## Common tasks

- Edit secrets: `just secrets` (opens your `$EDITOR`, re-encrypts on save).
- Read one secret: `just secret <path>`, e.g. `just secret mosquitto/ha_password` (the MQTT password for Home Assistant). Same as `sops -d --extract '["mosquitto"]["ha_password"]' secrets/frogswamp.yaml`, plus a trailing newline.
- Add a secret: add the key in `just secrets`, declare it in `hosts/frogswamp/secrets.nix`, deploy.
- Rotate a password: change the value in `just secrets`, then `just deploy` (services restart where configured).
- New admin PC: copy the age key from your password manager to `~/.config/sops/age/keys.txt` (mode 600).

## Recovery

- **Lost the admin age key, server alive:** create a new key, decrypt on the server (`sops` with the host key), and replace the admin recipient in `.sops.yaml`. Then run `sops updatekeys secrets/*`.
- **Lost the admin key and the server:** secrets are unrecoverable. Create new ones and re-run `just gen-host-key` after deleting the old key files.
- Never commit decrypted files. The pre-commit `sops-guard` blocks plaintext files in this directory.
