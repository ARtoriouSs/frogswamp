{ pkgs, ... }:
pkgs.runCommand "test-check-secrets"
  {
    nativeBuildInputs = [ pkgs.bash ];
  }
  ''
    set -u
    script=${../scripts/check-secrets.sh}
    if bash "$script" ${./fixtures/secrets-plain/plain.yaml}; then
      echo "FAIL: plaintext file was accepted"; exit 1
    fi
    bash "$script" ${./fixtures/secrets-enc/enc.yaml} || { echo "FAIL: encrypted file rejected"; exit 1; }
    # store paths carry a hash prefix; recreate the real repo-relative name
    mkdir -p secrets && cp ${../secrets/README.md} secrets/README.md
    bash "$script" secrets/README.md || { echo "FAIL: README rejected"; exit 1; }
    touch $out
  ''
