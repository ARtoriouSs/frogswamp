{ config, lib, ... }:
let
  cfg = config.frogswamp.homepage;
  addr = config.frogswamp.lan.publicAddress;
  link = port: "http://${addr}:${toString port}";
  local = port: "http://127.0.0.1:${toString port}";
  modes = config.frogswamp.modeSwitch;
  modeTitles = {
    vacation = "Vacation";
    day_off = "Day off";
    guests = "Guests";
  };
  modeIcons = {
    vacation = "mdi-airplane";
    day_off = "mdi-beach";
    guests = "mdi-account-multiple";
  };
  # one tile per mode: it shows the state; a click flips it and comes back here
  modeTiles = lib.mapAttrsToList (name: entity: {
    ${modeTitles.${name} or name} = {
      href = "${link modes.port}/toggle/${name}";
      target = "_self";
      icon = modeIcons.${name} or "mdi-toggle-switch";
      description = "Click to switch";
      widget = {
        type = "homeassistant";
        url = local 8123;
        key = "{{HOMEPAGE_VAR_HA_TOKEN}}";
        custom = [
          {
            state = entity;
            label = "now";
          }
        ];
      };
    };
  }) modes.modes;
  # newest backup time as "20.10 03:41", with a warning sign when older than 36 h
  lastBackup = repo: ''
    {% set t = as_timestamp(states('sensor.backup_last_${repo}'), none) %}
    {%- if t %}{{ t | timestamp_custom('%d.%m %H:%M') }}{{ ' ⚠' if now().timestamp() - t > 36 * 3600 }}
    {%- else %}{{ states('sensor.backup_last_${repo}') }}{% endif %}'';
in
{
  options.frogswamp.homepage = {
    enable = lib.mkEnableOption "Homepage dashboard";
    environmentFile = lib.mkOption {
      type = lib.types.str;
      description = "Runtime env file with HOMEPAGE_VAR_{HA_TOKEN,JELLYFIN_KEY,RADARR_KEY,SONARR_KEY,PROWLARR_KEY,BAZARR_KEY,QBIT_PASSWORD}";
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
        # a list keeps this order (an attribute set would come out sorted by name)
        layout = [
          {
            Home = {
              style = "row";
              columns = 2;
              Modes = {
                style = "row";
                columns = 3;
              };
            };
          }
          {
            Media = {
              style = "row";
              columns = 5;
            };
          }
          {
            Infrastructure = {
              style = "row";
              columns = 5;
            };
          }
        ];
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
                      state = "sensor.stale_downloads";
                      label = "stale downloads";
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
          ]
          ++ lib.optional modes.enable { Modes = modeTiles; };
        }
        {
          # first row: downloads (5 columns), second row: watching
          Media = [
            {
              qBittorrent = {
                href = link 8090;
                icon = "qbittorrent.png";
                description = "Downloads (stops seeding when done)";
                siteMonitor = local 8090;
                widget = {
                  type = "qbittorrent";
                  url = local 8090;
                  username = config.frogswamp.adminUser;
                  password = "{{HOMEPAGE_VAR_QBIT_PASSWORD}}";
                };
              };
            }
            {
              Radarr = {
                href = link 7878;
                icon = "radarr.png";
                description = "Movies";
                siteMonitor = local 7878;
                widget = {
                  type = "radarr";
                  url = local 7878;
                  key = "{{HOMEPAGE_VAR_RADARR_KEY}}";
                };
              };
            }
            {
              Sonarr = {
                href = link 8989;
                icon = "sonarr.png";
                description = "Shows (one season at a time)";
                siteMonitor = local 8989;
                widget = {
                  type = "sonarr";
                  url = local 8989;
                  key = "{{HOMEPAGE_VAR_SONARR_KEY}}";
                };
              };
            }
            {
              Prowlarr = {
                href = link 9696;
                icon = "prowlarr.png";
                description = "Indexers";
                siteMonitor = local 9696;
                widget = {
                  type = "prowlarr";
                  url = local 9696;
                  key = "{{HOMEPAGE_VAR_PROWLARR_KEY}}";
                };
              };
            }
            {
              Bazarr = {
                href = link 6767;
                icon = "bazarr.png";
                description = "Subtitles (RU, EN, PL)";
                siteMonitor = local 6767;
                widget = {
                  type = "bazarr";
                  url = local 6767;
                  key = "{{HOMEPAGE_VAR_BAZARR_KEY}}";
                };
              };
            }
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
              Football = {
                href = link 8093;
                icon = "mdi-soccer";
                description = "Ace Stream matches → TV (Chromecast or Kodi)";
                siteMonitor = local 8093;
              };
            }
            {
              "Media share" = {
                icon = "mdi-folder-network"; # no Samba icon in the dashboard-icons set
                description = "smb://frogswamp.local/media (user pupsich)";
              };
            }
          ];
        }
        {
          Infrastructure = [
            {
              # the backup-status job publishes these HA sensors (modules/backup.nix)
              Backups = {
                icon = "mdi-backup-restore";
                description = "A400 disk + Backblaze B2, nightly";
                widget = {
                  type = "homeassistant";
                  url = local 8123;
                  key = "{{HOMEPAGE_VAR_HA_TOKEN}}";
                  custom = [
                    {
                      template = "{% set d = states('sensor.backup_disk') %}{{ d }}{% if d == 'mounted' %} · {{ state_attr('sensor.backup_disk', 'used_percent') }}% used{% endif %}";
                      label = "A400 disk";
                    }
                    {
                      template = lastBackup "local";
                      label = "last on A400";
                    }
                    {
                      template = lastBackup "b2";
                      label = "last on B2";
                    }
                  ];
                };
              };
            }
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
              "TRaSH Guides" = [
                {
                  abbr = "TG";
                  href = "https://trash-guides.info/";
                }
              ];
            }
            {
              "Servarr wiki" = [
                {
                  abbr = "SV";
                  href = "https://wiki.servarr.com/";
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
