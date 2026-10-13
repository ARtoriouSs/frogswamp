{
  description = "frogswamp: homelab as code";

  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-26.05";
    nixpkgs-unstable.url = "github:NixOS/nixpkgs/nixos-unstable";
    disko = {
      url = "github:nix-community/disko";
      inputs.nixpkgs.follows = "nixpkgs";
    };
    sops-nix = {
      url = "github:Mic92/sops-nix";
      inputs.nixpkgs.follows = "nixpkgs";
    };
    git-hooks = {
      url = "github:cachix/git-hooks.nix";
      inputs.nixpkgs.follows = "nixpkgs";
    };
  };

  outputs =
    {
      self,
      nixpkgs,
      nixpkgs-unstable,
      git-hooks,
      ...
    }@inputs:
    let
      system = "x86_64-linux";
      pkgs = nixpkgs.legacyPackages.${system};
      pkgs-unstable = import nixpkgs-unstable { inherit system; };
      specialArgs = { inherit inputs pkgs-unstable; };
      hooks = git-hooks.lib.${system}.run {
        src = ./.;
        hooks = {
          nixfmt.enable = true;
          statix.enable = true;
          deadnix.enable = true;
          shellcheck.enable = true;
          detect-private-keys.enable = true;
          sops-guard = {
            enable = true;
            name = "sops-guard";
            entry = "${pkgs.bash}/bin/bash ${./scripts/check-secrets.sh}";
            files = "^secrets/";
            language = "system";
            pass_filenames = true;
          };
        };
      };
    in
    {
      nixosConfigurations = {
        frogswamp = nixpkgs.lib.nixosSystem {
          inherit specialArgs;
          modules = [ ./hosts/frogswamp ];
        };
      };

      checks.${system} = {
        pre-commit = hooks;
        frogswamp-toplevel = self.nixosConfigurations.frogswamp.config.system.build.toplevel;
      }
      // import ./tests { inherit pkgs self inputs; };

      devShells.${system}.default = pkgs.mkShell {
        packages = with pkgs; [
          just
          sops
          age
          ssh-to-age
          nixos-anywhere
          nixos-rebuild
          nvd
          mkpasswd
          skopeo
          jq
          openssl
          shellcheck
          nixfmt
          statix
          deadnix
        ];
        inherit (hooks) shellHook;
      };

      formatter.${system} = pkgs.nixfmt;
    };
}
