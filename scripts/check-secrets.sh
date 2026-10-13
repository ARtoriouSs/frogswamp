#!/usr/bin/env bash
# Pre-commit guard: every file under secrets/ (except README.md) must be sops-encrypted.
set -euo pipefail

status=0
for file in "$@"; do
  case "$(basename "$file")" in
    README.md) continue ;;
  esac
  if ! grep -qE '^sops:|"sops":[[:space:]]*\{' "$file"; then
    echo "check-secrets: $file is not sops-encrypted. Encrypt it with 'sops' before committing." >&2
    status=1
  fi
done
exit "$status"
