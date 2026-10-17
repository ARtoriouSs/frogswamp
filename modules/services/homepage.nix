{ config, lib, ... }:
let
  cfg = config.frogswamp.homepage;
  addr = config.frogswamp.lan.publicAddress;
  link = port: "http://${addr}:${toString port}";
  local = port: "http://127.0.0.1:${toString port}";
in
{
  options.frogswamp.homepage = {
    enable = lib.mkEnableOption "Homepage dashboard";
    environmentFile = lib.mkOption {
      type = lib.types.str;
      description = "Runtime env file with HOMEPAGE_VAR_HA_TOKEN and HOMEPAGE_VAR_JELLYFIN_KEY";
    };
  };

  config = lib.mkIf cfg.enable {
    services.homepage-dashboard = {
      enable = true;
      listenPort = 8082;
      openFirewall = true;
      inherit (cfg) environmentFile;
      allowedHosts = lib.concatStringsSep "," [
        "${addr}:8082"
        "frogswamp:8082"
        "frogswamp.local:8082"
        "localhost:8082"
      ];

      settings = {
        title = "frogswamp";
        description = "Home server";
        theme = "dark";
        color = "neutral";
        headerStyle = "boxed";
        statusStyle = "dot";
        hideVersion = true;
        useEqualHeights = true;
        target = "_blank";
        layout = {
          Home = {
            style = "row";
            columns = 2;
          };
          Media = {
            style = "row";
            columns = 2;
          };
          Infrastructure = {
            style = "row";
            columns = 4;
          };
        };
        quicklaunch = {
          searchDescriptions = true;
          provider = "duckduckgo";
        };
      };

      widgets = [
        {
          greeting = {
            text_size = "xl";
            text = "🐸 frogswamp";
          };
        }
        {
          datetime = {
            text_size = "l";
            format = {
              dateStyle = "long";
              timeStyle = "short";
              hour12 = false;
            };
          };
        }
        {
          openmeteo = {
            label = "Home";
            # City-level only (about ±5 km): top-bar widgets don't get HOMEPAGE_VAR_* substitution
            latitude = 52.2;
            longitude = 21.1;
            timezone = config.time.timeZone;
            units = "metric";
            cache = 15;
          };
        }
        {
          search = {
            provider = "duckduckgo";
            target = "_blank";
          };
        }
        {
          resources = {
            label = "System";
            cpu = true;
            memory = true;
            cputemp = true;
            uptime = true;
            units = "metric";
          };
        }
        {
          resources = {
            label = "System disk";
            disk = "/";
            expanded = true;
          };
        }
        {
          resources = {
            label = "Media";
            disk = "/srv/media";
            expanded = true;
          };
        }
        {
          resources = {
            label = "Backups (A400)";
            disk = "/mnt/backup";
            expanded = true;
          };
        }
      ];

      services = [
        {
          Home = [
            {
              "Home Assistant" = {
                href = link 8123;
                icon = "home-assistant.png";
                description = "Automations, devices, energy";
                siteMonitor = local 8123;
                widget = {
                  type = "homeassistant";
                  url = local 8123;
                  key = "{{HOMEPAGE_VAR_HA_TOKEN}}";
                  custom = [
                    {
                      template = "{{ states.light | selectattr('state', 'equalto', 'on') | list | length }}";
                      label = "lights on";
                    }
                    {
                      template = "{{ states.switch | selectattr('state', 'equalto', 'on') | list | length }}";
                      label = "switches on";
                    }
                    {
                      template = "{{ states.automation | selectattr('state', 'equalto', 'on') | list | length }}";
                      label = "automations";
                    }
                    {
                      state = "sun.sun";
                      label = "sun";
                    }
                  ];
                };
              };
            }
            {
              Zigbee2MQTT = {
                href = link 8080;
                icon = "zigbee2mqtt.png";
                description = "Zigbee devices and pairing";
                siteMonitor = local 8080;
              };
            }
          ];
        }
        {
          Media = [
            {
              Jellyfin = {
                href = link 8096;
                icon = "jellyfin.png";
                description = "Movies and shows";
                siteMonitor = local 8096;
                widget = {
                  type = "jellyfin";
                  url = local 8096;
                  key = "{{HOMEPAGE_VAR_JELLYFIN_KEY}}";
                  enableBlocks = true;
                  enableNowPlaying = true;
                  showEpisodeNumber = true;
                };
              };
            }
            {
              "Media share" = {
                icon = "samba.png";
                description = "smb://frogswamp.local/media (user pupsich)";
              };
            }
          ];
        }
        {
          Infrastructure = [
            {
              Router = {
                href = "http://192.168.101.1";
                icon = "router.png";
                description = "LAN, DHCP pool .100–.254";
                siteMonitor = "http://192.168.101.1";
              };
            }
            {
              Tailscale = {
                href = "https://login.tailscale.com/admin/machines";
                icon = "tailscale.png";
                description = "Remote access, subnet route";
              };
            }
            {
              "Backblaze B2" = {
                href = "https://secure.backblaze.com/b2_buckets.htm";
                icon = "backblaze.png";
                description = "Off-site backups (frogswamp-backup)";
              };
            }
            {
              "GitHub repo" = {
                href = "https://github.com/ARtoriouSs/frogswamp";
                icon = "github.png";
                description = "Infrastructure as code";
              };
            }
          ];
        }
      ];

      bookmarks = [
        {
          Documentation = [
            {
              "Home Assistant docs" = [
                {
                  abbr = "HA";
                  href = "https://www.home-assistant.io/docs/";
                }
              ];
            }
            {
              "Zigbee2MQTT devices" = [
                {
                  abbr = "Z2";
                  href = "https://www.zigbee2mqtt.io/supported-devices/";
                }
              ];
            }
            {
              "Jellyfin docs" = [
                {
                  abbr = "JF";
                  href = "https://jellyfin.org/docs/";
                }
              ];
            }
            {
              "NixOS options" = [
                {
                  abbr = "NX";
                  href = "https://search.nixos.org/options";
                }
              ];
            }
            {
              "Homepage docs" = [
                {
                  abbr = "HP";
                  href = "https://gethomepage.dev/";
                }
              ];
            }
          ];
        }
        {
          Admin = [
            {
              "Tailscale admin" = [
                {
                  abbr = "TS";
                  href = "https://login.tailscale.com/admin";
                }
              ];
            }
            {
              "Backblaze" = [
                {
                  abbr = "B2";
                  href = "https://secure.backblaze.com/b2_buckets.htm";
                }
              ];
            }
            {
              "GitHub" = [
                {
                  abbr = "GH";
                  href = "https://github.com/ARtoriouSs/frogswamp";
                }
              ];
            }
          ];
        }
      ];
    };
  };
}
