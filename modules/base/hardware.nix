{ pkgs, ... }:
{
  hardware.bluetooth.enable = true;
  hardware.graphics = {
    enable = true;
    extraPackages = with pkgs; [
      intel-media-driver
      vpl-gpu-rt
    ];
  };
  environment.systemPackages = [ pkgs.libva-utils ];
}
