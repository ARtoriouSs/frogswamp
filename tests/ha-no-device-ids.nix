{ pkgs, ... }:
pkgs.runCommand "test-ha-no-device-ids" { } ''
  if grep -rnE '(^|[[:space:]])device_id:' ${../modules/services/home-assistant/config}; then
    echo "Repo HA config uses device_id (random per install). Use entity_id instead, see the HA README." >&2
    exit 1
  fi
  touch $out
''
