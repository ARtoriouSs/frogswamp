set shell := ["bash", "-euo", "pipefail", "-c"]

host := env_var_or_default("FROGSWAMP_HOST", "192.168.101.10")

# List commands
default:
    @just --list

# Run all checks (flake checks + HA config check once HA exists)
check:
    nix flake check -L
