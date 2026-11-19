{ pkgs, ... }:
pkgs.runCommand "test-wellness-charts"
  {
    nativeBuildInputs = [ pkgs.nodejs ];
  }
  ''
    cp ${../modules/services/home-assistant/config/www/daily-chart.js} daily-chart.js
    cp ${../modules/services/home-assistant/config/www/daily-chart.test.mjs} daily-chart.test.mjs
    node --test daily-chart.test.mjs
    touch $out
  ''
