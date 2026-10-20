"""Create-if-missing setup for the media apps.

Every ensure_* function only adds what is missing (matched by name or path).
It never changes or deletes something that already exists, so settings changed
in the apps' UIs survive a re-run (every deploy and boot runs this script).
"""

import argparse
import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

PROFILE = "frogswamp"
CLIENT = "qBittorrent"
LANGUAGE_FORMAT = "{} audio"


def log(msg):
    print(msg, flush=True)


def read(path):
    try:
        with open(path) as f:
            return f.read().strip()
    except OSError:
        return ""


class HTTPStatus(RuntimeError):
    def __init__(self, code, msg):
        super().__init__(msg)
        self.code = code


def request(method, url, headers=None, body=None, form=None):
    data = None
    headers = dict(headers or {})
    if body is not None:
        data = json.dumps(body).encode()
        headers["Content-Type"] = "application/json"
    elif form is not None:
        data = urllib.parse.urlencode(form, doseq=True).encode()
        headers["Content-Type"] = "application/x-www-form-urlencoded"
    req = urllib.request.Request(url, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            raw = resp.read()
    except urllib.error.HTTPError as e:  # keep the app's validation message in the log
        raise HTTPStatus(e.code, f"{method} {url.split('?')[0]}: HTTP {e.code}: "
                                 f"{e.read()[:500].decode(errors='replace')}") from None
    return json.loads(raw) if raw.strip() else None


class Arr:
    """Radarr, Sonarr or Prowlarr API client."""

    def __init__(self, name, url, key_file):
        self.name, self.url, self.key = name, url.rstrip("/"), read(key_file)

    def call(self, method, path, body=None):
        return request(method, self.url + path, {"X-Api-Key": self.key}, body)

    # short: this runs during deploys, and Restart= tries again when an app is still starting
    def wait(self, timeout=120):
        deadline = time.time() + timeout
        while True:
            try:
                return self.call("GET", "/system/status")
            except (urllib.error.URLError, OSError):
                if time.time() > deadline:
                    raise
                time.sleep(5)


def set_field(fields, name, value):
    for f in fields:
        if f["name"] == name:
            f["value"] = value
            return
    fields.append({"name": name, "value": value})


def ensure_root_folder(app, path):
    if path in [r["path"].rstrip("/") for r in app.call("GET", "/rootfolder")]:
        log(f"{app.name}: root folder {path} exists")
        return
    app.call("POST", "/rootfolder", {"path": path})
    log(f"{app.name}: created root folder {path}")


def ensure_download_client(app, qbit, category, category_field):
    if any(c["name"] == CLIENT for c in app.call("GET", "/downloadclient")):
        log(f"{app.name}: download client {CLIENT} exists")
        return
    schema = next(s for s in app.call("GET", "/downloadclient/schema") if s["implementation"] == "QBittorrent")
    schema.update({"name": CLIENT, "enable": True, "removeCompletedDownloads": False, "removeFailedDownloads": True})
    for name, value in {
        "host": qbit["host"],
        "port": qbit["port"],
        "username": qbit["username"],
        "password": read(qbit["password_file"]),
        category_field: category,
        "sequentialOrder": True,
        "firstAndLast": True,
    }.items():
        set_field(schema["fields"], name, value)
    app.call("POST", "/downloadclient?forceSave=true", schema)
    log(f"{app.name}: created download client {CLIENT} (category {category})")


def ensure_media_management(app, min_free_mb):
    mm = app.call("GET", "/config/mediamanagement")
    changed = []
    if mm.get("minimumFreeSpaceWhenImporting") == 100:  # app default
        mm["minimumFreeSpaceWhenImporting"] = min_free_mb
        changed.append(f"minimum free space {min_free_mb} MB")
    if not mm.get("copyUsingHardlinks"):
        log(f"{app.name}: hardlinks are off (changed in the UI?), leaving as is")
    if changed:
        app.call("PUT", f"/config/mediamanagement/{mm['id']}", mm)
        log(f"{app.name}: set {', '.join(changed)}")
    else:
        log(f"{app.name}: media management unchanged")


def ensure_language_formats(app, scores):
    existing = {cf["name"]: cf["id"] for cf in app.call("GET", "/customformat")}
    languages = {l["name"]: l["id"] for l in app.call("GET", "/language")}
    ids = {}
    for language in scores:
        name = LANGUAGE_FORMAT.format(language)
        if name in existing:
            ids[name] = existing[name]
            continue
        cf = app.call("POST", "/customformat", {
            "name": name,
            "includeCustomFormatWhenRenaming": False,
            "specifications": [{
                "name": language,
                "implementation": "LanguageSpecification",
                "negate": False,
                "required": True,
                "fields": [{"name": "value", "value": languages[language]}],
            }],
        })
        ids[name] = cf["id"]
        log(f"{app.name}: created custom format {name}")
    return ids


def ensure_quality_profile(app, allowed, scores):
    if any(p["name"] == PROFILE for p in app.call("GET", "/qualityprofile")):
        log(f"{app.name}: quality profile {PROFILE} exists")
        return
    format_ids = ensure_language_formats(app, scores)
    profile = app.call("GET", "/qualityprofile/schema")
    cutoff = None
    for item in profile["items"]:  # listed worst to best, so the last allowed item ranks highest
        children = item.get("items") or []
        if children:
            for child in children:
                child["allowed"] = child["quality"]["name"] in allowed
            item["allowed"] = any(c["allowed"] for c in children)
            item_id = item.get("id")
        else:
            item["allowed"] = item["quality"]["name"] in allowed
            item_id = item["quality"]["id"]
        if item["allowed"]:
            cutoff = item_id
    by_id = {format_ids[LANGUAGE_FORMAT.format(l)]: s for l, s in scores.items()}
    for fi in profile.get("formatItems", []):
        fi["score"] = by_id.get(fi["format"], 0)
    profile.update({"name": PROFILE, "upgradeAllowed": False, "cutoff": cutoff, "minFormatScore": 0})
    if "language" in profile:  # Radarr only. The default "Original" rejects Russian releases of English films
        profile["language"] = next(l for l in app.call("GET", "/language") if l["name"] == "Any")
    app.call("POST", "/qualityprofile", profile)
    log(f"{app.name}: created quality profile {PROFILE}")


def ensure_ui_login(app, username, password_file, allowed_hosts):
    host = app.call("GET", "/config/host")
    if host.get("username"):
        log(f"{app.name}: UI login exists")
        return
    password = read(password_file)
    host.update({"username": username, "password": password, "passwordConfirmation": password,
                 "authenticationMethod": "forms", "authenticationRequired": "disabledForLocalAddresses"})
    # Newer Radarr/Prowlarr require Allowed Hosts when login is skipped for local addresses
    if "allowedHosts" in host and not host.get("allowedHosts"):
        host["allowedHosts"] = ",".join(allowed_hosts)
    app.call("PUT", f"/config/host/{host['id']}", host)
    log(f"{app.name}: created UI login {username}")


def ensure_prowlarr_app(prowlarr, name, url, key):
    if any(a["name"] == name for a in prowlarr.call("GET", "/applications")):
        log(f"prowlarr: application {name} exists")
        return
    schema = next(s for s in prowlarr.call("GET", "/applications/schema") if s["implementation"] == name)
    schema.update({"name": name, "syncLevel": "fullSync"})
    for field, value in {"prowlarrUrl": "http://127.0.0.1:9696", "baseUrl": url, "apiKey": key}.items():
        set_field(schema["fields"], field, value)
    prowlarr.call("POST", "/applications?forceSave=true", schema)
    log(f"prowlarr: created application {name}")


def ensure_bazarr(cfg, radarr_key, sonarr_key):
    url, key = cfg["url"].rstrip("/"), read(cfg["key_file"])
    headers = {"X-API-KEY": key}
    settings = request("GET", url + "/system/settings", headers)
    form = {}
    if not settings["general"].get("use_radarr"):
        form.update({"settings-general-use_radarr": "true", "settings-radarr-ip": "127.0.0.1",
                     "settings-radarr-port": "7878", "settings-radarr-apikey": radarr_key})
    if not settings["general"].get("use_sonarr"):
        form.update({"settings-general-use_sonarr": "true", "settings-sonarr-ip": "127.0.0.1",
                     "settings-sonarr-port": "8989", "settings-sonarr-apikey": sonarr_key})
    if not settings["general"].get("enabled_providers"):
        form["settings-general-enabled_providers"] = cfg["providers"]
    if not request("GET", url + "/system/languages/profiles", headers):
        items = [{"id": i + 1, "language": l, "audio_exclude": "False", "hi": "False", "forced": "False"}
                 for i, l in enumerate(cfg["languages"])]
        form.update({
            "languages-enabled": cfg["languages"],
            "languages-profiles": json.dumps([{"profileId": 1, "name": PROFILE, "items": items, "cutoff": None,
                                               "mustContain": [], "mustNotContain": [], "originalFormat": False}]),
            "settings-general-movie_default_enabled": "true", "settings-general-movie_default_profile": "1",
            "settings-general-serie_default_enabled": "true", "settings-general-serie_default_profile": "1",
        })
    if not settings.get("auth", {}).get("type"):
        # Bazarr has no "no login for local addresses" option, so it always asks
        form.update({"settings-auth-type": "form", "settings-auth-username": cfg["username"],
                     "settings-auth-password": read(cfg["password_file"])})
    if form:
        request("POST", url + "/system/settings", headers, form=form)
        log(f"bazarr: set {', '.join(sorted(form))}")
    else:
        log("bazarr: settings exist")


def ensure_jellyfin(cfg):
    key = read(cfg["key_file"])
    if not key:
        log("jellyfin: no API key yet, skipping (create one in Jellyfin: Dashboard → API Keys)")
        return
    url, headers = cfg["url"].rstrip("/"), {"X-Emby-Token": key}
    try:
        folders = request("GET", url + "/Library/VirtualFolders", headers)
    except HTTPStatus as e:
        if e.code != 401:
            raise
        # fresh install before the restore: this Jellyfin doesn't know the key yet
        log("jellyfin: Jellyfin key not accepted (fresh install, or the key isn't in Jellyfin yet), skipping")
        return
    existing = {f["Name"]: f.get("Locations") or [] for f in folders}
    for lib in cfg["libraries"]:
        if lib["name"] not in existing:
            query = urllib.parse.urlencode({"name": lib["name"], "collectionType": lib["type"],
                                            "paths": lib["paths"], "refreshLibrary": "true"}, doseq=True)
            request("POST", f"{url}/Library/VirtualFolders?{query}", headers,
                    {"LibraryOptions": {"EnableRealtimeMonitor": True, "PathInfos": [{"Path": p} for p in lib["paths"]]}})
            log(f"jellyfin: created library {lib['name']} → {', '.join(lib['paths'])}")
            continue
        missing = [p for p in lib["paths"] if p not in existing[lib["name"]]]
        for path in missing:
            request("POST", url + "/Library/VirtualFolders/Paths?refreshLibrary=true", headers,
                    {"Name": lib["name"], "PathInfo": {"Path": path}})
            log(f"jellyfin: added {path} to library {lib['name']}")
        if not missing:
            log(f"jellyfin: library {lib['name']} exists")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True)
    cfg = json.load(open(parser.parse_args().config))
    failed = []

    def step(name, fn, *args):
        try:
            fn(*args)
        except Exception as e:  # keep going so one broken app doesn't block the others
            failed.append(name)
            log(f"FAILED {name}: {e}")

    apps = {n: Arr(n, cfg[n]["url"], cfg[n]["key_file"]) for n in ("radarr", "sonarr", "prowlarr")}
    for app in apps.values():
        step(f"{app.name} up", app.wait)
    for n, category_field in (("radarr", "movieCategory"), ("sonarr", "tvCategory")):
        app, c = apps[n], cfg[n]
        step(f"{n} login", ensure_ui_login, app, cfg["ui"]["username"], cfg["ui"]["password_file"], cfg["ui"]["allowed_hosts"])
        step(f"{n} root folder", ensure_root_folder, app, c["root"])
        step(f"{n} download client", ensure_download_client, app, cfg["qbittorrent"], c["category"], category_field)
        step(f"{n} media management", ensure_media_management, app, cfg["min_free_mb"])
        step(f"{n} quality profile", ensure_quality_profile, app, c["qualities"], cfg["language_scores"])
    step("prowlarr login", ensure_ui_login, apps["prowlarr"], cfg["ui"]["username"], cfg["ui"]["password_file"],
         cfg["ui"]["allowed_hosts"])
    for n, url in (("Radarr", "http://127.0.0.1:7878"), ("Sonarr", "http://127.0.0.1:8989")):
        step(f"prowlarr {n}", ensure_prowlarr_app, apps["prowlarr"], n, url, apps[n.lower()].key)
    step("bazarr", ensure_bazarr, cfg["bazarr"], apps["radarr"].key, apps["sonarr"].key)
    step("jellyfin", ensure_jellyfin, cfg["jellyfin"])

    if failed:
        log(f"failed steps: {', '.join(failed)}")
        sys.exit(1)


if __name__ == "__main__":
    main()
